"""Isolated v3 control-plane CLI validation, dispatch, and serialization.

Importing this module must not load config, service, providers, skill runtime, or
httpx. Runtime adapters are imported only after raw argv passes the v3
allowlist and option checks.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .control_plane_contract import (
    ERROR_RETRYABILITY,
    V3Envelope,
    V3Error,
    V3ErrorCode,
    V3Meta,
    V3Mutation,
    V3Network,
    V3OperationDescriptor,
    V3SideEffects,
    V3Status,
    exit_code_for,
    operation_for_argv,
    parser_error_result,
    serialize_result,
)


_COMMON_OPTIONS = frozenset({"schema-version", "fail-on-degraded", "format"})


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
) -> int:
    envelope = parser_error_result(command, operation, message)
    payload = serialize_result(envelope)
    _json_stdout(payload)
    return exit_code_for(payload)


def _argv_options(argv: list[str] | None) -> dict[str, str | None]:
    options: dict[str, str | None] = {}
    args = list(argv or ())
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        if token.startswith("--"):
            raw = token[2:]
            if "=" in raw:
                name, value = raw.split("=", 1)
                options[name] = value
            else:
                name = raw
                value = args[index + 1] if index + 1 < len(args) and not args[index + 1].startswith("-") else None
                options[name] = value
        index += 1
    return options


def _raw_command_label(argv: list[str] | None, args: Any) -> str | None:
    """Prefer the raw argv leaf so aliases are not reported as normalized targets."""
    tokens: list[str] = []
    index = 0
    raw = list(argv or ())
    while index < len(raw):
        token = raw[index]
        if token in {"--schema-version", "-schema-version"}:
            index += 2
            continue
        if token.startswith("--schema-version=") or token in {"--fail-on-degraded", "--trace"}:
            index += 1
            continue
        if token.startswith("-") and token not in {"--"}:
            # Skip other root/common options and their values when present.
            if "=" not in token and index + 1 < len(raw) and not raw[index + 1].startswith("-"):
                index += 2
            else:
                index += 1
            continue
        break
    while index < len(raw) and not raw[index].startswith("-"):
        tokens.append(raw[index])
        index += 1
        if len(tokens) >= 4:
            break
    if tokens:
        return " ".join(tokens)
    command = getattr(args, "command", None)
    return str(command) if command else None


def _reject(args: Any, argv: list[str] | None) -> tuple[V3OperationDescriptor | None, str | None]:
    descriptor = operation_for_argv(argv)
    if descriptor is None:
        command = _raw_command_label(argv, args)
        return None, f"command {command!r} is not supported under --schema-version 3"

    options = _argv_options(argv)
    supplied = set(options)
    if "trace" in supplied or bool(getattr(args, "trace", False)):
        return descriptor, "v3 does not define trace output; omit --trace"
    if "format" in supplied:
        value = options.get("format")
        if value != "json":
            return descriptor, f"v3 supports only JSON output; got --format {value or ''}".rstrip()
    unsupported = sorted(supplied - _COMMON_OPTIONS - descriptor.permitted_options)
    if unsupported:
        return descriptor, f"v3 does not support --{unsupported[0]} for {descriptor.operation}"
    return descriptor, None


def _internal_error(descriptor: V3OperationDescriptor | None) -> int:
    if descriptor is None:
        return emit_parser_error(
            command=None,
            operation=None,
            message="v3 command failed before operation selection",
        )
    # Unknown failures must not invent write or subprocess outcomes. Reads may
    # have already happened for the selected operation, so keep declared reads.
    envelope = V3Envelope(
        status=V3Status.FAILED,
        command=descriptor.command,
        operation=descriptor.operation,
        result={},
        network=V3Network(descriptor.network_policy, descriptor.network_scope, False, ()),
        side_effects=V3SideEffects(
            config=V3Mutation(read=descriptor.config_read),
            filesystem=V3Mutation(read=descriptor.filesystem_read),
            subprocess_started=False,
        ),
        error=V3Error(
            V3ErrorCode.INTERNAL_ERROR,
            "v3 control-plane command failed unexpectedly",
            ERROR_RETRYABILITY[V3ErrorCode.INTERNAL_ERROR],
            {},
        ),
        meta=V3Meta(),
    )
    payload = serialize_result(envelope)
    _json_stdout(payload)
    return exit_code_for(payload)


async def dispatch(args: Any, *, argv: list[str] | None = None) -> int:
    descriptor, rejection = _reject(args, argv)
    if rejection:
        return emit_parser_error(
            command=(
                descriptor.command
                if descriptor is not None
                else _raw_command_label(argv, args)
            ),
            operation=descriptor.operation if descriptor is not None else None,
            message=rejection,
        )

    try:
        from .control_plane_adapters import run_operation

        envelope = await run_operation(args, descriptor)
        payload = serialize_result(envelope)
    except Exception:
        return _internal_error(descriptor)

    _json_stdout(payload)
    return exit_code_for(
        payload,
        fail_on_degraded=bool(getattr(args, "fail_on_degraded", False)),
    )


__all__ = ["dispatch", "emit_parser_error"]
