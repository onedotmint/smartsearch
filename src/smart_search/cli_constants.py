"""Stdlib-only CLI constants and pure helpers used before service imports.

This module must not import service, config, providers, or httpx.
"""

from __future__ import annotations

import argparse
import json
from importlib import metadata
from pathlib import Path

EXIT_OK = 0
EXIT_PARAMETER_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_NETWORK_ERROR = 4
EXIT_RUNTIME_ERROR = 5

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

PUBLIC_COMMANDS = (
    "search",
    "route",
    "fetch",
    "map",
    "deep",
    "research",
    "doctor",
    "setup",
    "config",
    "skills",
)

V2_SUPPORTED_COMMANDS = frozenset({"search", "fetch", "map", "capabilities"})

# Reverse alias map: alias -> canonical command
_ALIAS_TO_COMMAND: dict[str, str] = {}
for _canonical, _aliases in COMMAND_ALIASES.items():
    _ALIAS_TO_COMMAND[_canonical] = _canonical
    for _alias in _aliases:
        _ALIAS_TO_COMMAND[_alias] = _canonical


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
        return "unknown"


class CLIParseError(Exception):
    """Raised by the v2-aware parser instead of printing/exiting."""

    def __init__(self, message: str, *, parser: argparse.ArgumentParser | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.parser = parser


class SmartSearchArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        self._raise_on_error = bool(kwargs.pop("raise_on_error", False))
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # type: ignore[override]
        if self._raise_on_error:
            raise CLIParseError(message, parser=self)
        super().error(message)


def prescan_schema_version(argv: list[str] | None) -> dict[str, object]:
    """
    Stdlib-only root-global schema pre-scan.

    Recognizes only `--schema-version 2` / `--schema-version=2` before the
    subcommand. Does not import service/config/providers/httpx.
    """
    args = list(argv) if argv is not None else []
    schema_version = "1"
    explicit = False
    command: str | None = None
    operation: str | None = None
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"--schema-version", "-schema-version"}:
            explicit = True
            if index + 1 < len(args):
                schema_version = str(args[index + 1])
                index += 2
                continue
            schema_version = ""
            index += 1
            continue
        if token.startswith("--schema-version="):
            explicit = True
            schema_version = token.split("=", 1)[1]
            index += 1
            continue
        if token.startswith("-") and token not in {"--"}:
            # skip other root flags and their optional values conservatively
            index += 1
            continue
        # first non-flag token is the command/alias
        command = _ALIAS_TO_COMMAND.get(token, token)
        break
    if command in V2_SUPPORTED_COMMANDS:
        operation = {
            "search": "source_discovery",
            "fetch": "content_fetch",
            "map": "site_discovery",
            "capabilities": "capability_status",
        }.get(command)
    elif command is not None:
        operation = None
    return {
        "schema_version": schema_version if schema_version else "1",
        "explicit": explicit,
        "command": command,
        "operation": operation,
        "v2": explicit and schema_version == "2",
    }


__all__ = [
    "CLIParseError",
    "COMMAND_ALIASES",
    "CONFIG_COMMAND_ALIASES",
    "EXIT_CONFIG_ERROR",
    "EXIT_NETWORK_ERROR",
    "EXIT_OK",
    "EXIT_PARAMETER_ERROR",
    "EXIT_RUNTIME_ERROR",
    "FIRECRAWL_DEFAULT_API_URL",
    "MODEL_COMMAND_ALIASES",
    "PUBLIC_COMMANDS",
    "SKILLS_COMMAND_ALIASES",
    "SmartSearchArgumentParser",
    "TAVILY_DEFAULT_API_URL",
    "V2_SUPPORTED_COMMANDS",
    "ZHIPU_DEFAULT_API_URL",
    "ZHIPU_SEARCH_ENGINE_CHOICES",
    "_get_version",
    "prescan_schema_version",
]
