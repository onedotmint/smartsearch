"""Stable CLI facade for parsing, dispatch, rendering, and exit codes.

Parser construction is stdlib-only. Legacy service/setup/render/dispatch modules
are imported only after a successful parse selects the v1 branch. The v2 branch
imports only its own canonical dependencies.
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
    prescan_schema_version,
    help_all_text,
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
    prescan = prescan_schema_version(raw_argv)
    want_v2 = bool(prescan.get("v2"))

    parser = build_parser(raise_on_error=want_v2)
    try:
        args = parser.parse_args(raw_argv)
    except CLIParseError as exc:
        from .cli_v2 import emit_parser_error

        return emit_parser_error(
            command=prescan.get("command") if isinstance(prescan.get("command"), str) else None,
            operation=prescan.get("operation") if isinstance(prescan.get("operation"), str) else None,
            message=exc.message,
        )
    # v1 argparse errors, --help, and --version raise SystemExit from parse_args
    # exactly as before Phase 3; do not swallow them here.

    schema_version = str(getattr(args, "schema_version", "1") or "1")
    _CLI_FORCE_OUTPUT = bool(getattr(args, "force", False))

    if schema_version == "2":
        from .cli_v2 import dispatch

        return asyncio.run(dispatch(args, argv=raw_argv))

    # v1 path: lazy-load logging, setup, and dispatch.
    from .logger import configure_cli_logging, logger
    from .utils import PromptConfigurationError

    configure_cli_logging(json_mode=getattr(args, "format", "") == "json")

    # Reject v2-only flags under schema 1.
    if getattr(args, "fail_on_degraded", False) or getattr(args, "trace", False):
        data = {
            "ok": False,
            "error_type": "parameter_error",
            "error_code": "INVALID_ARGUMENT",
            "error": "--fail-on-degraded and --trace require --schema-version 2",
        }
        return _print_result(
            getattr(args, "command", "unknown"),
            data,
            getattr(args, "format", "json"),
            getattr(args, "output", ""),
        )

    try:
        from .cli_dispatch import (
            _run_async,
            _run_config,
            _run_model,
            _run_regression,
            _run_setup,
            _run_skills,
        )

        if args.command == "regression":
            return _run_regression()
        if args.command == "setup":
            return _run_setup(args)
        if args.command == "skills":
            return _run_skills(args)
        if args.command == "config":
            return _run_config(args)
        if args.command == "model":
            return _run_model(args)
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        return EXIT_RUNTIME_ERROR
    except PromptConfigurationError as exc:
        data = {
            "ok": False,
            "error_type": "config_error",
            "error_code": "CONFIGURATION_ERROR",
            "error": str(exc),
        }
        return _print_result(
            getattr(args, "command", "unknown"),
            data,
            getattr(args, "format", "json"),
            getattr(args, "output", ""),
        )
    except Exception as exc:
        logger.exception("CLI 命令执行失败: command=%s", getattr(args, "command", "unknown"))
        data = {
            "ok": False,
            "error_type": "runtime_error",
            "error_code": "INTERNAL_ERROR",
            "error": str(exc),
        }
        return _print_result(
            getattr(args, "command", "unknown"),
            data,
            getattr(args, "format", "json"),
            getattr(args, "output", ""),
        )


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
