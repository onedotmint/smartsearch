"""Leaf v2 CLI dispatch, allowlist, serialization, and exit policy.

Module import must stay free of service/config/provider/httpx so parser-error
paths can run in a fresh process without loading those modules.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .v2_contract import (
    ERROR_RETRYABILITY,
    V2_CAPABILITY_OPERATION_IDS,
    V2Envelope,
    V2Error,
    V2ErrorCode,
    V2Evidence,
    V2Meta,
    V2_META_OPERATION_CAPABILITY_STATUS,
    V2Routing,
    V2Status,
    exit_code_for,
    parser_error_result,
    serialize_result,
)

# Command -> allowed dest names after parse (besides schema/trace/fail flags).
_V2_DISALLOWED_NONEMPTY: dict[str, frozenset[str]] = {
    "search": frozenset({
        "platform", "model", "extra_sources", "profile", "response_mode", "validation",
        "fallback", "providers", "stream", "timeout", "output", "force",
        "prompt_dir", "search_prompt_file", "fetch_prompt_file", "research_prompt_file",
    }),
    "fetch": frozenset({
        "output", "force", "prompt_dir", "search_prompt_file", "fetch_prompt_file", "research_prompt_file",
    }),
    "map": frozenset({
        "timeout", "output", "force",
        "prompt_dir", "search_prompt_file", "fetch_prompt_file", "research_prompt_file",
    }),
    "capabilities": frozenset({
        "output", "force", "prompt_dir", "search_prompt_file", "fetch_prompt_file", "research_prompt_file",
    }),
}

_COMMAND_OPERATION = {
    "search": "source_discovery",
    "fetch": "content_fetch",
    "map": "site_discovery",
    "capabilities": V2_META_OPERATION_CAPABILITY_STATUS,
}

_V2_SUPPORTED = frozenset(_COMMAND_OPERATION)


def _json_stdout(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def emit_parser_error(
    *,
    command: str | None,
    operation: str | None,
    message: str,
    details: dict[str, str] | None = None,
) -> int:
    cmd = command or "unknown"
    envelope = parser_error_result(cmd, operation, message, details)
    payload = serialize_result(envelope)
    _json_stdout(payload)
    return exit_code_for(payload)


def _emit_internal_error(command: str | None, operation: str | None) -> int:
    """Render a non-leaking v2 failure when a known handler fails unexpectedly."""
    requested = (operation,) if operation in V2_CAPABILITY_OPERATION_IDS else ()
    envelope = V2Envelope(
        status=V2Status.FAILED,
        command=command or "unknown",
        operation=operation,
        result={},
        evidence=V2Evidence(),
        routing=V2Routing(requested, (), "v2", ("internal_error",)),
        attempts=(),
        degradation=(),
        error=V2Error(
            V2ErrorCode.INTERNAL_ERROR,
            "v2 command failed unexpectedly",
            ERROR_RETRYABILITY[V2ErrorCode.INTERNAL_ERROR],
            {},
        ),
        meta=V2Meta("v2-internal-error", 0),
    )
    payload = serialize_result(envelope)
    _json_stdout(payload)
    return exit_code_for(payload)


def _argv_option_names(argv: list[str] | None) -> set[str]:
    """Return explicitly supplied long-option names before an argv `--` marker."""
    names: set[str] = set()
    for token in argv or ():
        if token == "--":
            break
        if token.startswith("--"):
            names.add(token[2:].split("=", 1)[0])
    return names


def _reject_v1_only(args: Any, *, argv: list[str] | None = None) -> str | None:
    command = getattr(args, "command", None)
    if command not in _V2_SUPPORTED:
        return f"command {command!r} is not an evidence v2 command"
    fmt = getattr(args, "format", "json")
    if fmt not in ("json", "markdown", "content"):
        return f"v2 supports only --format json|markdown|content; got --format {fmt}"
    disallowed = _V2_DISALLOWED_NONEMPTY.get(command, frozenset())
    present_options = _argv_option_names(argv)
    for name in disallowed:
        option_names = {name.replace("_", "-")}
        if name == "stream":
            option_names.add("no-stream")
        supplied = sorted(option_names & present_options)
        if supplied:
            return f"v2 does not support --{supplied[0]}"

    # Preserve validation for programmatic dispatch calls that have no raw argv.
    default_ok = {
        "platform": "",
        "model": "",
        "extra_sources": 0,
        "profile": "",
        "response_mode": "concise",
        "validation": "",
        "fallback": "",
        "providers": "auto",
        "stream": None,
        "timeout": 90,
        "output": "",
        "force": False,
        "prompt_dir": "",
        "search_prompt_file": "",
        "fetch_prompt_file": "",
        "research_prompt_file": "",
    }
    if command == "map":
        default_ok.update({"timeout": 150})
    for name in disallowed:
        if not hasattr(args, name) or name == "response_mode":
            continue
        value = getattr(args, name)
        if value != default_ok[name]:
            return f"v2 does not support --{name.replace('_', '-')} (got {value!r})"
    return None


def _argv_has_response_mode(argv: list[str] | None) -> bool:
    if not argv:
        return False
    for token in argv:
        if token == "--":
            break
        if token == "--response-mode" or token.startswith("--response-mode="):
            return True
    return False


async def dispatch(args: Any, *, argv: list[str] | None = None) -> int:
    # Lazy import canonical facade only after allowlist checks pass conceptually;
    # still validate flags first without network.
    command = getattr(args, "command", None)
    if command == "search" and _argv_has_response_mode(argv):
        return emit_parser_error(
            command="search",
            operation="source_discovery",
            message="v2 search does not define response_mode; omit --response-mode",
        )
    rejected = _reject_v1_only(args, argv=argv)
    if rejected:
        return emit_parser_error(
            command=command,
            operation=_COMMAND_OPERATION.get(command),
            message=rejected,
        )

    try:
        from . import api_v2
        from .canonical_operations import CanonicalOperationError, ContentFetchRequest, SiteDiscoveryRequest
    except Exception:
        return _emit_internal_error(command, _COMMAND_OPERATION.get(command))

    try:
        if command == "search":
            envelope = await api_v2._composite_search(args.query)
        elif command == "fetch":
            envelope = await api_v2.content_fetch(ContentFetchRequest(resource=args.url))
        elif command == "map":
            envelope = await api_v2.site_discovery(
                SiteDiscoveryRequest(
                    resource=args.url,
                    instructions=getattr(args, "instructions", "") or "",
                    max_depth=getattr(args, "max_depth", 1),
                    max_breadth=getattr(args, "max_breadth", 20),
                    limit=getattr(args, "limit", 50),
                )
            )
        elif command == "capabilities":
            envelope = api_v2.capability_status()
        else:
            return emit_parser_error(
                command=command,
                operation=None,
                message=f"unsupported v2 command: {command}",
            )

        payload = serialize_result(envelope)
    except CanonicalOperationError as exc:
        return emit_parser_error(
            command=command,
            operation=_COMMAND_OPERATION.get(command),
            message=str(exc),
        )
    except Exception:
        return _emit_internal_error(command, _COMMAND_OPERATION.get(command))

    fmt = getattr(args, "format", "json")
    if fmt == "json":
        _json_stdout(payload)
    else:
        # Pure one-way human presentation over the validated redacted payload.
        # The exit code is always derived from the validated JSON authority.
        from .presentation import render_v2

        sys.stdout.write(render_v2(payload, fmt))
    return exit_code_for(payload, fail_on_degraded=bool(getattr(args, "fail_on_degraded", False)))


__all__ = ["dispatch", "emit_parser_error"]
