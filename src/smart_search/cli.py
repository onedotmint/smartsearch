"""Stable CLI facade for parsing, dispatch, rendering, and exit codes.

Parser, setup, and command handlers live in dedicated modules. This module
keeps the public entrypoint and output contract stable.
"""

from .cli_support import *
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
    """
    =================================================================================
    步骤1：渲染稳定 CLI 结果
    =================================================================================
    目标：让 JSON stdout 只包含最终协议对象，同时保留 Markdown/content 的既有行为。
    数据源：Service 扁平结果、命令名、格式和输出路径。
    操作：
    1) JSON 格式包裹 schema_version、data、meta 和结构化 error。
    2) 其他格式沿用现有人类可读渲染器。
    3) 输出文件失败时返回机器可读错误，而不是写入半成品。
    """
    logger.info("开始渲染命令结果: command=%s format=%s", command, fmt)
    force = _CLI_FORCE_OUTPUT if force is None else force
    result_data = data
    if fmt == "json":
        result_data = build_json_result(command, data)
        rendered = _json_stdout_safe(result_data)
    else:
        rendered = _render(command, data, fmt)

    if output:
        try:
            if force:
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

from .cli_parser import *
from .cli_setup import *
from .cli_dispatch import *

def main(argv: list[str] | None = None) -> int:
    """
    =================================================================================
    步骤3：启动公共 CLI 边界
    =================================================================================
    目标：统一日志出口、输出覆盖策略和异常 JSON 契约。
    数据源：解析后的命令参数和 Service 结果。
    操作：
    1) 在执行命令前把日志绑定到 stderr。
    2) 记录本次是否允许覆盖输出文件。
    3) 将配置和运行异常转换为稳定的非零结果。
    """
    global _CLI_FORCE_OUTPUT
    parser = build_parser()
    args = parser.parse_args(argv)
    _CLI_FORCE_OUTPUT = bool(getattr(args, "force", False))
    configure_cli_logging(json_mode=getattr(args, "format", "") == "json")
    try:
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


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [name for name in globals() if not name.startswith("__")]
