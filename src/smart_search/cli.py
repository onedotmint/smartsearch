"""Stable CLI facade for parsing, dispatch, rendering, and exit codes.

Parser construction and domain classification are stdlib-only. Routing is
canonical command-domain based: evidence commands use V2, retained
control-plane leaves use V3, and ``research plan`` / ``research run`` use the
Research Workflow family. Removed selectors, aliases, and legacy spellings
fail with the replacement family's strict INVALID_ARGUMENT envelope before
any owner/config/provider import. The broad ``smart_search.service`` facade,
the v1 lazy proxy, and the v1 render/dispatch/setup modules are removed; each
canonical family owns its serialization and presentation boundary.
"""

from __future__ import annotations

import asyncio
import sys

from .cli_constants import (
    CLIParseError,
    EXIT_CONFIG_ERROR,
    EXIT_NETWORK_ERROR,
    EXIT_OK,
    EXIT_PARAMETER_ERROR,
    EXIT_RUNTIME_ERROR,
    classify_command_domain,
    help_all_text,
    removed_spelling_message,
)
from .cli_parser import build_parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    if raw_argv == ["--help-all"]:
        sys.stdout.write(help_all_text())
        return EXIT_OK

    classification = classify_command_domain(raw_argv)

    # Removed selectors, aliases, and legacy spellings fail with the
    # replacement family's strict INVALID_ARGUMENT envelope before any
    # owner/config/provider import and before argparse is involved.
    if classification["family"] == "removed":
        return _emit_removed_family_error(classification)

    # Unidentifiable input uses the existing V2 root parser-error sentinel.
    if classification["family"] == "unknown":
        from .cli_v2 import emit_parser_error as v2_emit

        token = classification.get("command")
        message = f"unrecognized command {token!r}" if token else "unrecognized command"
        return v2_emit(command=None, operation=None, message=message)

    # A leading value-taking option (--format json ...) is leaf-only and must
    # fail with the target family's stable parameter error, never argparse's
    # misleading "invalid choice" for the option value.
    root_option = classification.get("root_leading_option")
    if root_option:
        return _emit_root_leading_option_error(classification, str(root_option))

    parser = build_parser(raise_on_error=True)
    try:
        args = parser.parse_args(raw_argv)
    except CLIParseError as exc:
        return _emit_family_parse_error(classification, exc.message)
    # argparse help/version actions raise SystemExit(0) from parse_args; do
    # not swallow them here.

    family = classification["family"]
    if family == "v3":
        from .cli_v3 import dispatch

        return asyncio.run(dispatch(args, argv=raw_argv))
    if family == "v2":
        from .cli_v2 import dispatch

        return asyncio.run(dispatch(args, argv=raw_argv))

    # Research Workflow family: canonical ``research plan`` / ``research run``.
    missing_query = classification.get("missing_query")
    if missing_query:
        from .cli_research import emit_parser_error as workflow_emit

        return workflow_emit(f"{missing_query} requires a non-blank query")
    from .cli_research import dispatch as research_dispatch

    return asyncio.run(research_dispatch(args, argv=raw_argv))


def _emit_root_leading_option_error(classification: dict[str, object], option: str) -> int:
    """Emit the target family's stable parameter error for a leading leaf option."""
    family = classification["family"]
    command = classification.get("command")
    operation = classification.get("operation")
    message = f"{option} is a leaf-level option and cannot precede the command"
    if family == "v3":
        from .cli_v3 import emit_parser_error

        return emit_parser_error(
            command=command if isinstance(command, str) else None,
            operation=operation if isinstance(operation, str) else None,
            message=message,
        )
    if family == "v2":
        from .cli_v2 import emit_parser_error

        return emit_parser_error(
            command=command if isinstance(command, str) else None,
            operation=operation if isinstance(operation, str) else None,
            message=message,
        )
    from .cli_research import emit_parser_error

    return emit_parser_error(message)


def _emit_removed_family_error(classification: dict[str, object]) -> int:
    """Emit the replacement family's strict INVALID_ARGUMENT envelope."""
    error_family = classification["error_family"]
    legacy_spelling = str(classification.get("legacy_spelling") or "")
    replacement = str(classification.get("replacement") or "")
    message = removed_spelling_message(legacy_spelling, replacement)
    details = {"legacy_spelling": legacy_spelling, "replacement": replacement}
    command = classification.get("command")
    operation = classification.get("operation")
    if error_family == "v2":
        from .cli_v2 import emit_parser_error

        return emit_parser_error(
            command=command if isinstance(command, str) else None,
            operation=operation if isinstance(operation, str) else None,
            message=message,
            details=details,
        )
    if error_family == "v3":
        from .cli_v3 import emit_parser_error

        return emit_parser_error(
            command=command if isinstance(command, str) else None,
            operation=operation if isinstance(operation, str) else None,
            message=message,
            details=details,
        )
    from .cli_research import emit_parser_error

    return emit_parser_error(message, details=details)


def _emit_family_parse_error(classification: dict[str, object], message: str) -> int:
    """Emit the canonical family's parser error for malformed canonical input."""
    family = classification["family"]
    command = classification.get("command")
    operation = classification.get("operation")
    if family == "v2":
        from .cli_v2 import emit_parser_error

        return emit_parser_error(
            command=command if isinstance(command, str) else None,
            operation=operation if isinstance(operation, str) else None,
            message=message,
        )
    if family == "v3":
        from .cli_v3 import emit_parser_error

        return emit_parser_error(
            command=command if isinstance(command, str) else None,
            operation=operation if isinstance(operation, str) else None,
            message=message,
        )
    from .cli_research import emit_parser_error

    return emit_parser_error(message)


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["main"]
