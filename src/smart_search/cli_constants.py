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
DEFAULT_SKILL_TARGET_IDS = ("codex", "claude", "cursor")

PUBLIC_COMMANDS = ("search", "fetch", "capabilities", "setup")

# Namespace paths are intentionally separate from legacy aliases. The parser
# uses these descriptors for complete help while dispatch normalizes leaves to
# their established v1 command ids.
NAMESPACE_COMMANDS: tuple[dict[str, str], ...] = (
    {"path": "research plan QUERY", "command": "deep", "tier": "core", "network": "offline", "legacy": "deep, dr"},
    {"path": "doctor probe", "command": "doctor", "tier": "advanced", "network": "live", "legacy": "doctor, d"},
    {"path": "provider list", "command": "provider-list", "tier": "advanced", "network": "local", "legacy": ""},
    {"path": "provider status", "command": "provider-status", "tier": "advanced", "network": "local", "legacy": ""},
    {"path": "provider routes current", "command": "model", "tier": "advanced", "network": "local", "legacy": "model current, mdl cur/c"},
    {"path": "provider routes list", "command": "model", "tier": "advanced", "network": "local", "legacy": "model list, mdl ls/l"},
    {"path": "provider routes add", "command": "model", "tier": "advanced", "network": "local", "legacy": "model add, mdl a"},
    {"path": "provider routes remove", "command": "model", "tier": "advanced", "network": "local", "legacy": "model remove, mdl rm/r"},
    {"path": "provider exa search", "command": "exa-search", "tier": "advanced", "network": "live", "legacy": "exa-search, exa, x"},
    {"path": "provider exa similar", "command": "exa-similar", "tier": "advanced", "network": "live", "legacy": "exa-similar, xs"},
    {"path": "provider context7 library", "command": "context7-library", "tier": "advanced", "network": "live", "legacy": "context7-library, c7, ctx7"},
    {"path": "provider context7 docs", "command": "context7-docs", "tier": "advanced", "network": "live", "legacy": "context7-docs, c7d, c7docs, ctx7-docs"},
    {"path": "provider zhipu search", "command": "zhipu-search", "tier": "advanced", "network": "live", "legacy": "zhipu-search, z, zp"},
    {"path": "provider zhipu-mcp search", "command": "zhipu-mcp-search", "tier": "advanced", "network": "live", "legacy": "zhipu-mcp-search, zmcp-search"},
    {"path": "provider zhipu-mcp reader", "command": "zhipu-mcp-reader", "tier": "advanced", "network": "live", "legacy": "zhipu-mcp-reader, zmcp-reader"},
    {"path": "dev route-explain QUERY", "command": "route", "tier": "developer", "network": "local_or_router", "legacy": "route, rt"},
    {"path": "dev route-calibrate", "command": "route-calibrate", "tier": "developer", "network": "configured_router", "legacy": "route-calibrate, route-cal, rcal"},
    {"path": "dev diagnose openai-compatible", "command": "diagnose", "tier": "developer", "network": "live", "legacy": "diagnose, diag"},
    {"path": "dev smoke", "command": "smoke", "tier": "developer", "network": "mock_or_live", "legacy": "smoke, sm"},
    {"path": "dev regression", "command": "regression", "tier": "developer", "network": "local", "legacy": "regression, reg"},
    {"path": "dev skills status", "command": "skills", "tier": "developer", "network": "filesystem", "legacy": "skills status, skill st"},
    {"path": "dev skills update", "command": "skills", "tier": "developer", "network": "filesystem", "legacy": "skills update, skill up"},
    {"path": "experimental anysearch domains", "command": "anysearch-domains", "tier": "experimental", "network": "live", "legacy": "anysearch-domains, as-domains"},
    {"path": "experimental anysearch search", "command": "anysearch-search", "tier": "experimental", "network": "live", "legacy": "anysearch-search, as-search, as"},
    {"path": "experimental anysearch extract", "command": "anysearch-extract", "tier": "experimental", "network": "live", "legacy": "anysearch-extract, as-extract"},
    {"path": "experimental anysearch batch", "command": "anysearch-batch", "tier": "experimental", "network": "live", "legacy": "anysearch-batch, as-batch"},
    {"path": "experimental zread search-doc", "command": "zhipu-mcp-search-doc", "tier": "experimental", "network": "live", "legacy": "zhipu-mcp-search-doc, zmcp-doc"},
    {"path": "experimental zread repo-structure", "command": "zhipu-mcp-repo-structure", "tier": "experimental", "network": "live", "legacy": "zhipu-mcp-repo-structure, zmcp-tree"},
    {"path": "experimental zread read-file", "command": "zhipu-mcp-read-file", "tier": "experimental", "network": "live", "legacy": "zhipu-mcp-read-file, zmcp-file"},
)


def _namespace_operation_id(path: str) -> str:
    return path.removesuffix(" QUERY").replace(" ", "-")


NAMESPACE_COMMANDS = tuple(
    {**item, "operation": _namespace_operation_id(item["path"])}
    for item in NAMESPACE_COMMANDS
)


def _normalize_namespace_invocation(argv: list[str] | None) -> tuple[list[str], str | None]:
    """Translate collision-safe namespace paths and retain their operation id."""
    args = list(argv or [])
    if not args:
        return args, None
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        index += 2 if args[index] in {"--schema-version", "-schema-version"} else 1
    if index >= len(args):
        return args, None
    if args[index] == "doctor":
        options_with_values = {
            "--format",
            "--output",
            "--prompt-dir",
            "--search-prompt-file",
            "--fetch-prompt-file",
            "--research-prompt-file",
        }
        skip_value = False
        for probe_index, token in enumerate(args[index + 1 :], start=index + 1):
            if skip_value:
                skip_value = False
                continue
            if token == "--":
                break
            if token in options_with_values:
                skip_value = True
                continue
            if token == "probe":
                normalized = args[:index] + ["doctor"] + args[index + 1 : probe_index] + args[probe_index + 1 :]
                return normalized, "doctor-probe"
            if not token.startswith("-"):
                break
        return args, None
    if args[index] != "research":
        return args, None

    options_with_values = {
        "--budget",
        "--profile",
        "--evidence-dir",
        "--fallback",
        "--format",
        "--output",
        "--prompt-dir",
        "--search-prompt-file",
        "--fetch-prompt-file",
        "--research-prompt-file",
    }
    positionals: list[tuple[int, str]] = []
    saw_help = False
    after_delimiter = False
    skip_value = False
    for token_index, token in enumerate(args[index + 1 :], start=index + 1):
        if skip_value:
            skip_value = False
            continue
        if token == "--":
            after_delimiter = True
            continue
        if not after_delimiter and token == "--help":
            saw_help = True
            continue
        if not after_delimiter and token in options_with_values:
            skip_value = True
            continue
        if not after_delimiter and token.startswith("-"):
            continue
        positionals.append((token_index, token))

    # A legacy ``research plan`` is still a valid one-word query. Namespace
    # dispatch needs the literal plan selector plus its own query, except that
    # plan help intentionally selects the namespace-compatible deep help.
    if positionals and positionals[0][1] == "plan" and (len(positionals) > 1 or saw_help):
        plan_index = positionals[0][0]
        normalized = args[:index] + ["deep"] + args[index + 1 : plan_index] + args[plan_index + 1 :]
        return normalized, "research-plan"
    return args, None


def normalize_namespace_argv(argv: list[str] | None) -> list[str]:
    """Translate only unambiguous colliding namespace paths to legacy leaves."""
    return _normalize_namespace_invocation(argv)[0]


def namespace_operation_for_argv(argv: list[str] | None) -> str | None:
    """Return the unique namespace operation selected by a colliding path."""
    return _normalize_namespace_invocation(argv)[1]


def classify_namespace_argv(argv: list[str] | None) -> tuple[list[str], str | None]:
    """Return normalized argv and its collision-safe namespace operation once."""
    return _normalize_namespace_invocation(argv)


def help_all_text() -> str:
    lines = ["Smart Search command reference", "", "Core:"]
    lines.extend(f"  {command}" for command in PUBLIC_COMMANDS)
    lines.extend(("", "Advanced namespaces:"))
    for item in NAMESPACE_COMMANDS:
        lines.append(f"  {item['path']}  [{item['tier']}; {item['network']}]")
    lines.extend(("", "Legacy commands and aliases:"))
    for command, aliases in COMMAND_ALIASES.items():
        suffix = f" ({', '.join(aliases)})" if aliases else ""
        lines.append(f"  {command}{suffix}")
    for command, aliases in (
        ("config path", CONFIG_COMMAND_ALIASES["path"]),
        ("config list", CONFIG_COMMAND_ALIASES["list"]),
        ("config set", CONFIG_COMMAND_ALIASES["set"]),
        ("config unset", CONFIG_COMMAND_ALIASES["unset"]),
        ("model current", MODEL_COMMAND_ALIASES["current"]),
        ("model list", MODEL_COMMAND_ALIASES["list"]),
        ("model add", MODEL_COMMAND_ALIASES["add"]),
        ("model remove", MODEL_COMMAND_ALIASES["remove"]),
        ("skills status", SKILLS_COMMAND_ALIASES["status"]),
        ("skills update", SKILLS_COMMAND_ALIASES["update"]),
    ):
        lines.append(f"  {command} ({', '.join(aliases)})")
    return "\n".join(lines) + "\n"


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
    "DEFAULT_SKILL_TARGET_IDS",
    "EXIT_CONFIG_ERROR",
    "EXIT_NETWORK_ERROR",
    "EXIT_OK",
    "EXIT_PARAMETER_ERROR",
    "EXIT_RUNTIME_ERROR",
    "FIRECRAWL_DEFAULT_API_URL",
    "MODEL_COMMAND_ALIASES",
    "NAMESPACE_COMMANDS",
    "PUBLIC_COMMANDS",
    "SKILLS_COMMAND_ALIASES",
    "SmartSearchArgumentParser",
    "TAVILY_DEFAULT_API_URL",
    "V2_SUPPORTED_COMMANDS",
    "ZHIPU_DEFAULT_API_URL",
    "ZHIPU_SEARCH_ENGINE_CHOICES",
    "_get_version",
    "classify_namespace_argv",
    "help_all_text",
    "namespace_operation_for_argv",
    "normalize_namespace_argv",
    "prescan_schema_version",
]
