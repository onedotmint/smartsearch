"""Stable CLI facade for parsing, dispatch, rendering, and exit codes.

Parser construction and domain classification are stdlib-only. Routing is
canonical command-domain based: evidence commands use V2, retained
control-plane leaves use V3, and ``research plan`` / ``research run`` use the
Research Workflow family. Removed selectors, aliases, and legacy spellings
fail with the replacement family's strict INVALID_ARGUMENT envelope before
any owner/config/provider import. Legacy service/setup/render/dispatch
modules remain importable for the later cleanup task but are no longer
reachable from dispatch.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

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

_CLI_FORCE_OUTPUT = False


class _LazyService:
    """Compatibility proxy so tests can monkeypatch ``cli.service`` attributes."""

    _module = None

    def _load(self):
        if self._module is None:
            from . import service as _service

            self._module = _service
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_module":
            object.__setattr__(self, name, value)
            return
        setattr(self._load(), name, value)


service = _LazyService()  # type: ignore[assignment]


def _exit_code(data: dict[str, Any]) -> int:
    if data.get("ok", False):
        return EXIT_OK
    error_type = data.get("error_type")
    if error_type == "config_error":
        return EXIT_CONFIG_ERROR
    if error_type == "parameter_error":
        return EXIT_PARAMETER_ERROR
    if error_type == "network_error":
        return EXIT_NETWORK_ERROR
    if error_type == "provider_error":
        return EXIT_NETWORK_ERROR
    if error_type == "evidence_error":
        return EXIT_NETWORK_ERROR
    if error_type == "output_exists":
        return EXIT_PARAMETER_ERROR
    if error_type == "output_error":
        return EXIT_RUNTIME_ERROR
    return EXIT_RUNTIME_ERROR


def _print_result(
    command: str,
    data: dict[str, Any],
    fmt: str,
    output: str = "",
    *,
    force: bool | None = None,
) -> int:
    # Lazy import v1 render/contract/service only when printing v1 results.
    from .cli_contract import build_json_result
    from .cli_render import _json_stdout_safe, _render
    from .cli_support import _write_stdout
    from .logger import logger

    logger.info("开始渲染命令结果: command=%s format=%s", command, fmt)
    force_flag = _CLI_FORCE_OUTPUT if force is None else force
    result_data = data
    if fmt == "json":
        result_data = build_json_result(command, data)
        rendered = _json_stdout_safe(result_data)
    else:
        rendered = _render(command, data, fmt)

    if output:
        try:
            if force_flag:
                service.write_output(output, rendered, force=True)
            else:
                service.write_output(output, rendered)
        except FileExistsError as exc:
            result_data = {
                "ok": False,
                "error_type": "output_exists",
                "error_code": "OUTPUT_EXISTS",
                "error": str(exc),
                "output": output,
            }
            rendered = (
                _json_stdout_safe(build_json_result(command, result_data))
                if fmt == "json"
                else _render(command, result_data, fmt)
            )
        except OSError as exc:
            result_data = {
                "ok": False,
                "error_type": "output_error",
                "error_code": "OUTPUT_WRITE_FAILED",
                "error": str(exc),
                "output": output,
            }
            rendered = (
                _json_stdout_safe(build_json_result(command, result_data))
                if fmt == "json"
                else _render(command, result_data, fmt)
            )
    _write_stdout(rendered)
    if rendered and not rendered.endswith("\n"):
        _write_stdout("\n")
    logger.info("命令结果渲染完成: command=%s ok=%s", command, result_data.get("ok", False))
    return _exit_code(result_data)


def main(argv: list[str] | None = None) -> int:
    global _CLI_FORCE_OUTPUT
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

    parser = build_parser(raise_on_error=True)
    try:
        args = parser.parse_args(raw_argv)
    except CLIParseError as exc:
        return _emit_family_parse_error(classification, exc.message)
    # argparse help/version actions raise SystemExit(0) from parse_args; do
    # not swallow them here.

    _CLI_FORCE_OUTPUT = bool(getattr(args, "force", False))

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


_LAZY_SUPPORT_NAMES = frozenset({
    "Path", "subprocess", "argparse", "json",
    "build_json_result", "configure_cli_logging", "logger",
    "PromptConfigurationError", "_json_stdout_safe", "_render",
    "_write_stdout", "COMMAND_ALIASES",
})
_LAZY_SETUP_NAMES = frozenset({
    "_normalize_tavily_api_url",
    "_normalize_tavily_flag_api_url",
    "_prompt_tavily_api_url",
    "_prompt_zhipu_api_url",
    "_prompt_zhipu_search_engine",
    "_write_setup_banner",
})


def __getattr__(name: str) -> Any:
    """Lazy re-exports for historical ``smart_search.cli`` attribute access.

    Keeps module import free of service/config/httpx while preserving test and
    compatibility access to setup/render/dispatch helpers.
    """
    if name in _LAZY_SUPPORT_NAMES:
        from . import cli_support

        value = getattr(cli_support, name)
        globals()[name] = value
        return value
    if name in {"build_parser", "SmartSearchArgumentParser", "PUBLIC_COMMANDS"}:
        from . import cli_parser

        value = getattr(cli_parser, name)
        globals()[name] = value
        return value
    if name.startswith("_run_") or name in _LAZY_SETUP_NAMES:
        from . import cli_dispatch
        from . import cli_setup

        for module in (cli_setup, cli_dispatch):
            if hasattr(module, name):
                value = getattr(module, name)
                globals()[name] = value
                return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [name for name in globals() if not name.startswith("__")]
