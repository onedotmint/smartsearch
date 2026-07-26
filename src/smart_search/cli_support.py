"""Shared CLI imports, constants, streams, and pure helpers."""

import argparse
import asyncio
import contextlib
import getpass
import inspect
import json
from importlib import metadata
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import service
from .cli_render import (
    _json,
    _json_stdout_safe,
    _escape_unencodable_json_char,
    _format_seconds,
    _one_line,
    _md_cell,
    _markdown_table,
    _markdown_code_block,
    _status_label,
    _yes_no,
    _latency_text,
    _configured_text,
    _error_lines,
    _error_summary,
    _result_title,
    _result_target,
    _result_summary,
    _result_rows,
    _format_result_markdown,
    _format_doctor_markdown,
    _provider_detail_lines,
    _format_smoke_markdown,
    _format_diagnose_markdown,
    _format_route_markdown,
    _format_route_calibrate_markdown,
    _format_config_markdown,
    _format_model_markdown,
    _format_setup_markdown,
    _format_skills_markdown,
    _format_markdown,
    _plain_result_lines,
    _format_content,
    _render,
    _stdout_safe,
    _stream_safe,
)

from .embedding_presets import (
    QWEN3_EMBEDDING_8B_PRESET,
    embedding_preset_for_model,
)
from .skill_installer import (
    DEFAULT_SKILL_TARGET_IDS,
    SKILL_TARGETS,
    SkillInstallError,
    install_skill_targets,
    parse_skill_targets,
    status_skill_targets,
)
from .cli_contract import build_json_result
from .logger import configure_cli_logging, logger
from .utils import PromptConfigurationError, prompt_overrides


EXIT_OK = 0
EXIT_PARAMETER_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_NETWORK_ERROR = 4
EXIT_RUNTIME_ERROR = 5

_CLI_FORCE_OUTPUT = False

COMMAND_ALIASES = {
    "search": ["s"],
    "route": ["rt"],
    "fetch": ["f"],
    "map": ["m"],
    "exa-search": ["exa", "x"],
    "exa-similar": ["xs"],
    "zhipu-search": ["z", "zp"],
    "zhipu-mcp-search": ["zmcp-search"],
    "zhipu-mcp-reader": ["zmcp-reader"],
    "zhipu-mcp-search-doc": ["zmcp-doc"],
    "zhipu-mcp-repo-structure": ["zmcp-tree"],
    "zhipu-mcp-read-file": ["zmcp-file"],
    "anysearch-domains": ["as-domains"],
    "anysearch-search": ["as-search", "as"],
    "anysearch-extract": ["as-extract"],
    "anysearch-batch": ["as-batch"],
    "context7-library": ["c7", "ctx7"],
    "context7-docs": ["c7d", "c7docs", "ctx7-docs"],
    "deep": ["dr"],
    "research": ["rs"],
    "route-calibrate": ["route-cal", "rcal"],
    "smoke": ["sm"],
    "doctor": ["d"],
    "diagnose": ["diag"],
    "model": ["mdl"],
    "setup": ["init"],
    "skills": ["skill"],
    "config": ["cfg"],
    "regression": ["reg"],
}

CONFIG_COMMAND_ALIASES = {
    "path": ["p"],
    "list": ["ls", "l"],
    "set": ["s"],
    "unset": ["rm", "u"],
}

MODEL_COMMAND_ALIASES = {
    "current": ["cur", "c"],
    "list": ["ls", "l"],
    "add": ["a"],
    "remove": ["rm", "r"],
}

SKILLS_COMMAND_ALIASES = {
    "status": ["st"],
    "update": ["up"],
}

TAVILY_DEFAULT_API_URL = "https://api.tavily.com"
FIRECRAWL_DEFAULT_API_URL = "https://api.firecrawl.dev/v2"
ZHIPU_DEFAULT_API_URL = "https://open.bigmodel.cn/api"
ZHIPU_SEARCH_ENGINE_CHOICES = [
    "search_std",
    "search_pro",
    "search_pro_sogou",
    "search_pro_quark",
]

_STATIC_SMART_SEARCH_BANNER = r"""
 ____                       _     ____                      _
/ ___| _ __ ___   __ _ _ __| |_  / ___|  ___  __ _ _ __ ___| |__
\___ \| '_ ` _ \ / _` | '__| __| \___ \ / _ \/ _` | '__/ __| '_ \
 ___) | | | | | | (_| | |  | |_   ___) |  __/ (_| | | | (__| | | |
|____/|_| |_| |_|\__,_|_|   \__| |____/ \___|\__,_|_|  \___|_| |_|
""".strip("\n")

def _get_version() -> str:
    root = Path(__file__).resolve().parents[2]
    package_json = root / "package.json"
    try:
        version = json.loads(package_json.read_text(encoding="utf-8")).get("version", "")
        if version:
            return str(version)
    except (OSError, json.JSONDecodeError):
        pass

    pyproject = root / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.startswith("version = "):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass

    try:
        return metadata.version("smart-search")
    except metadata.PackageNotFoundError:
        pass

    return "unknown"

def _search_timeout_result(query: str, timeout: float, search_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    seconds = _format_seconds(timeout)
    search_kwargs = search_kwargs or {}
    stream = search_kwargs.get("stream")
    if stream is None:
        stream = service.config.openai_compatible_stream
    model = search_kwargs.get("model") or service.config.openai_compatible_model
    return {
        "ok": False,
        "error_type": "network_error",
        "error": f"Search timed out after {seconds} seconds",
        "query": query,
        "content": "",
        "sources": [],
        "sources_count": 0,
        "primary_sources": [],
        "primary_sources_count": 0,
        "extra_sources": [],
        "extra_sources_count": 0,
        "source_warning": "",
        "routing_decision": {},
        "providers_used": [],
        "provider_attempts": [],
        "fallback_used": False,
        "validation_level": "",
        "timeout_seconds": timeout,
        "provider": search_kwargs.get("providers", "auto"),
        "model": model,
        "stream": stream,
        "diagnose_command": "smart-search diagnose openai-compatible --format markdown",
        "recommendation": "Run `smart-search diagnose openai-compatible --format markdown` to check whether OpenAI-compatible stream/no-stream search requests are hanging upstream.",
    }

def _write_stdout(text: str) -> None:
    sys.stdout.write(_stdout_safe(text))

def _write_stderr(text: str) -> None:
    sys.stderr.write(_stream_safe(sys.stderr, text))

def _smart_search_banner_text() -> str:
    try:
        import pyfiglet

        banner = pyfiglet.figlet_format("Smart Search", font="slant")
        return banner.rstrip()
    except Exception:
        return _STATIC_SMART_SEARCH_BANNER

def _write_setup_banner(lang: str) -> None:
    banner = _smart_search_banner_text()
    tagline = _t(lang, "CLI-first multi-source search for AI agents", "CLI-first multi-source search for AI agents")
    _write_stderr(f"\n{banner}\n\n   Smart Search\n   {tagline}\n")

def _write_panel(text: str, lang: str) -> None:
    if not _is_interactive_setup_stream():
        _write_stderr(text)
        return
    try:
        from rich.console import Console
        from rich.panel import Panel
    except Exception:
        _write_stderr(text)
        return
    console = Console(file=sys.stderr, force_terminal=True)
    title = _t(lang, "Smart Search 配置", "Smart Search Setup")
    console.print(Panel(text.strip(), title=title, expand=False, safe_box=True))

def _prompt_override_context(args: argparse.Namespace):
    """
    =================================================================================
    步骤2：准备本地 Prompt 覆盖
    =================================================================================
    目标：把显式 CLI Prompt 配置限制在当前命令调用内。
    数据源：命令行参数；环境变量、用户配置和内置 Prompt 由 utils 继续处理。
    操作：
    1) 读取四个本地路径参数。
    2) 交给 ContextVar，避免污染同进程的后续调用。
    """
    return prompt_overrides(
        prompt_dir=getattr(args, "prompt_dir", ""),
        search_prompt_file=getattr(args, "search_prompt_file", ""),
        fetch_prompt_file=getattr(args, "fetch_prompt_file", ""),
        research_prompt_file=getattr(args, "research_prompt_file", ""),
    )

def _supports_argument(callable_obj: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return True
    return name in inspect.signature(callable_obj).parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )

def _is_secret_key(key: str) -> bool:
    upper_key = key.upper()
    return "KEY" in upper_key or "TOKEN" in upper_key or "SECRET" in upper_key

def _is_private_display_key(key: str) -> bool:
    return key.upper().endswith("_URL") or key.upper().endswith("_BASE_URL")

def _t(lang: str, zh: str, en: str) -> str:
    return zh if lang == "zh" else en

def _display_provider(provider: str, lang: str) -> str:
    names = {
        "xai-responses": "xAI Responses",
        "openai-compatible": "OpenAI-compatible",
        "zhipu": _t(lang, "智谱", "Zhipu"),
        "zhipu-mcp": _t(lang, "智谱 Coding Plan MCP", "Zhipu Coding Plan MCP"),
        "zhipu-mcp-reader": _t(lang, "智谱 MCP Reader", "Zhipu MCP Reader"),
        "exa": "Exa",
        "context7": "Context7",
        "jina": "Jina Reader",
        "tavily": "Tavily",
        "firecrawl": "Firecrawl",
        "anysearch": "AnySearch",
    }
    return names.get(provider, provider)

def _with_scheme(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    if "://" not in value:
        return f"https://{value}"
    return value

def _normalize_custom_base_url(url: str) -> str:
    value = _with_scheme(url).strip()
    return value.rstrip("/") if value else ""

def _normalize_tavily_api_url(url: str, *, hikari: bool = True) -> str:
    value = _normalize_custom_base_url(url)
    if not value:
        return ""
    parsed = urlsplit(value)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if host == "api.tavily.com":
        return urlunsplit((parsed.scheme, parsed.netloc, path or "", "", ""))
    if hikari and path in {"", "/mcp"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/api/tavily", "", ""))
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

def _normalize_tavily_flag_api_url(url: str, api_key: str = "") -> str:
    value = _normalize_custom_base_url(url)
    if not value:
        return ""
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if path == "/mcp" or _is_tavily_hikari_key(api_key):
        return _normalize_tavily_api_url(value)
    return _normalize_tavily_api_url(value, hikari=False)

def _normalize_firecrawl_api_url(url: str) -> str:
    return _normalize_custom_base_url(url)

def _normalize_zhipu_api_url(url: str) -> str:
    return _normalize_custom_base_url(url)

def _normalize_jina_reader_api_url(url: str) -> str:
    return _normalize_custom_base_url(url)

def _is_tavily_hikari_key(api_key: str) -> bool:
    return api_key.strip().lower().startswith("th-")

def _is_interactive_setup_stream() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stderr, "isatty", lambda: False)())

__all__ = [name for name in globals() if not name.startswith("__")]
