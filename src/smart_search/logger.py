import logging
import os
import sys
from datetime import datetime
from .config import config
from .security import sanitize_text

logger = logging.getLogger("smart_search")
logger.setLevel(getattr(logging, config.log_level))
logger.addHandler(logging.NullHandler())


class _SecretRedactionFilter(logging.Filter):
    """
    =================================================================================
    步骤1：过滤日志中的敏感内容
    =================================================================================
    目标：保证 stderr、文件日志和子模块日志不会输出凭据。
    数据源：logging record 的格式化文本。
    操作：
    1) 先格式化参数。
    2) 脱敏 Authorization、Token、Key 和敏感 URL 参数。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_text(record.getMessage())
        record.args = ()
        return True


_SECRET_REDACTION_FILTER = _SecretRedactionFilter()


def _file_logging_enabled() -> bool:
    return config.debug_enabled or config.log_to_file_enabled


def _configure_file_logging() -> None:
    if not _file_logging_enabled():
        return
    if any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        return

    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"smart_search_{datetime.now().strftime('%Y%m%d')}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(getattr(logging, config.log_level))

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_SECRET_REDACTION_FILTER)
    logger.addHandler(file_handler)


_configure_file_logging()


def configure_cli_logging(*, json_mode: bool = False) -> None:
    """
    =================================================================================
    步骤1：配置 CLI 日志边界
    =================================================================================
    目标：让诊断和进度信息进入 stderr，不污染机器可读 stdout。
    数据源：CLI 输出格式和当前日志配置。
    操作：
    1) 安装单一 stderr handler。
    2) 使用纯文本 formatter，禁止 ANSI 控制序列。
    """
    handler_name = "smart-search-cli-stderr"
    existing = next((handler for handler in logger.handlers if getattr(handler, "name", "") == handler_name), None)
    if existing is not None:
        logger.removeHandler(existing)
        existing.close()
    if not json_mode:
        return

    logger.info("开始配置 CLI 日志边界: json_mode=%s", json_mode)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.name = handler_name
    stream_handler.setLevel(getattr(logging, config.log_level, logging.INFO))
    stream_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    stream_handler.addFilter(_SECRET_REDACTION_FILTER)
    logger.addHandler(stream_handler)
    logger.setLevel(getattr(logging, config.log_level, logging.INFO))
    logger.info("CLI 日志边界配置完成: stderr=%s", True)

async def log_info(ctx, message: str, is_debug: bool = False):
    """
    =================================================================================
    步骤2：输出安全诊断信息
    =================================================================================
    目标：把调试信息送到日志或宿主上下文，同时避免泄露远程凭据。
    数据源：Provider、CLI 和宿主上下文传入的消息。
    操作：
    1) 统一清理敏感文本。
    2) 写入 logger 和可选宿主上下文。
    """
    message = sanitize_text(message)
    if is_debug:
        logger.info(message)

    if ctx:
        await ctx.info(message)
