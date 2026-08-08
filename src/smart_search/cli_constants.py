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

PUBLIC_COMMANDS = ("search", "fetch", "capabilities")

# Canonical replacement for every removed ``--schema-version`` spelling. The
# command domain (V2 evidence, V3 control plane, or Research Workflow) decides
# the contract; no selector remains as input surface.
SELECTOR_REPLACEMENT = "omit selector; route by canonical command domain"

# Reserved legacy spellings -> (error family, canonical replacement). This is
# parser metadata for deterministic removal errors only; it never dispatches.
# Spelling keys are the leading 1-2 command tokens of an invocation.
RESERVED_LEGACY_SPELLINGS: dict[tuple[str, ...], tuple[str, str]] = {
    # Top-level aliases
    ("s",): ("v2", "search"),
    ("f",): ("v2", "fetch"),
    ("m",): ("v2", "map"),
    ("rt",): ("v3", "dev route-explain"),
    ("dr",): ("workflow", "research plan"),
    ("route-cal",): ("v3", "dev route-calibrate"),
    ("rcal",): ("v3", "dev route-calibrate"),
    ("sm",): ("v3", "dev smoke"),
    ("d",): ("v3", "doctor probe"),
    ("diag",): ("v3", "dev diagnose openai-compatible"),
    ("mdl",): ("v3", "provider routes"),
    ("init",): ("v3", "config set"),
    ("skill",): ("v3", "dev skills"),
    ("cfg",): ("v3", "config"),
    ("reg",): ("v3", "dev regression"),
    ("rs",): ("workflow", "research run"),
    # Legacy control commands
    ("config",): ("v3", "config path|list|set|unset"),
    ("diagnose",): ("v3", "dev diagnose openai-compatible"),
    ("doctor",): ("v3", "doctor probe"),
    ("model",): ("v3", "provider routes"),
    ("regression",): ("v3", "dev regression"),
    ("route",): ("v3", "dev route-explain"),
    ("route-calibrate",): ("v3", "dev route-calibrate"),
    ("setup",): ("v3", "config set"),
    ("skills",): ("v3", "dev skills"),
    ("smoke",): ("v3", "dev smoke"),
    # Legacy workflow commands
    ("deep",): ("workflow", "research plan"),
    ("research",): ("workflow", "research run"),
    # Nested legacy commands
    ("model", "add"): ("v3", "provider.routes.add"),
    ("model", "current"): ("v3", "provider.routes.current"),
    ("model", "list"): ("v3", "provider.routes.list"),
    ("model", "remove"): ("v3", "provider.routes.remove"),
    ("skills", "status"): ("v3", "dev.skills.status"),
    ("skills", "update"): ("v3", "dev.skills.update"),
    # Nested aliases
    ("config", "p"): ("v3", "config.path"),
    ("config", "ls"): ("v3", "config.list"),
    ("config", "l"): ("v3", "config.list"),
    ("config", "s"): ("v3", "config.set"),
    ("config", "rm"): ("v3", "config.unset"),
    ("config", "u"): ("v3", "config.unset"),
    ("model", "a"): ("v3", "provider.routes.add"),
    ("model", "c"): ("v3", "provider.routes.current"),
    ("model", "cur"): ("v3", "provider.routes.current"),
    ("model", "l"): ("v3", "provider.routes.list"),
    ("model", "ls"): ("v3", "provider.routes.list"),
    ("model", "r"): ("v3", "provider.routes.remove"),
    ("model", "rm"): ("v3", "provider.routes.remove"),
    ("skills", "st"): ("v3", "dev.skills.status"),
    ("skills", "up"): ("v3", "dev.skills.update"),
}

# Canonical namespace prefixes that may carry canonical V3 leaves.
_V3_NAMESPACE_PREFIXES = frozenset({"config", "provider", "doctor", "dev"})

_V2_COMMAND_OPERATION = {
    "search": "source_discovery",
    "fetch": "content_fetch",
    "map": "site_discovery",
    "capabilities": "capability_status",
}

V2_SUPPORTED_COMMANDS = frozenset(_V2_COMMAND_OPERATION)

# Namespace paths are intentionally separate from legacy aliases. The parser
# uses these descriptors for complete help while dispatch normalizes leaves to
# their established v1 command ids.
NAMESPACE_COMMANDS: tuple[dict[str, str], ...] = (
    {"path": "research plan QUERY", "command": "research", "tier": "core", "network": "offline", "legacy": "deep, dr"},
    {"path": "research run QUERY", "command": "research", "tier": "advanced", "network": "live", "legacy": ""},
    {"path": "doctor probe", "command": "doctor", "tier": "advanced", "network": "live", "legacy": "doctor, d"},
    {"path": "doctor status", "command": "doctor", "tier": "advanced", "network": "local", "legacy": ""},
    {"path": "provider list", "command": "provider-list", "tier": "advanced", "network": "local", "legacy": ""},
    {"path": "provider status", "command": "provider-status", "tier": "advanced", "network": "local", "legacy": ""},
    {"path": "provider probe PROVIDER", "command": "provider-probe", "tier": "advanced", "network": "live", "legacy": ""},
    {"path": "provider routes current", "command": "provider", "tier": "advanced", "network": "local", "legacy": "model current, mdl cur/c"},
    {"path": "provider routes list", "command": "provider", "tier": "advanced", "network": "local", "legacy": "model list, mdl ls/l"},
    {"path": "provider routes add", "command": "provider", "tier": "advanced", "network": "local", "legacy": "model add, mdl a"},
    {"path": "provider routes remove", "command": "provider", "tier": "advanced", "network": "local", "legacy": "model remove, mdl rm/r"},
    {"path": "dev route-explain QUERY", "command": "dev", "tier": "developer", "network": "local_or_router", "legacy": "route, rt"},
    {"path": "dev route-calibrate", "command": "dev", "tier": "developer", "network": "configured_router", "legacy": "route-calibrate, route-cal, rcal"},
    {"path": "dev diagnose openai-compatible", "command": "dev", "tier": "developer", "network": "live", "legacy": "diagnose, diag"},
    {"path": "dev smoke", "command": "dev", "tier": "developer", "network": "mock_or_live", "legacy": "smoke, sm"},
    {"path": "dev regression", "command": "dev", "tier": "developer", "network": "local", "legacy": "regression, reg"},
    {"path": "dev skills status", "command": "dev", "tier": "developer", "network": "filesystem", "legacy": "skills status, skill st"},
    {"path": "dev skills update", "command": "dev", "tier": "developer", "network": "filesystem", "legacy": "skills update, skill up"},
)


def _namespace_operation_id(path: str) -> str:
    return path.removesuffix(" QUERY").replace(" ", "-")


NAMESPACE_COMMANDS = tuple(
    {**item, "operation": _namespace_operation_id(item["path"])}
    for item in NAMESPACE_COMMANDS
)


def _normalize_namespace_invocation(
    argv: list[str] | None,
) -> tuple[list[str], str | None, dict[str, object]]:
    """Translate collision-safe namespace paths and retain their operation id."""
    args = list(argv or [])
    attrs: dict[str, object] = {}
    if not args:
        return args, None, attrs
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        index += 1
    if index >= len(args):
        return args, None, attrs
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
                return normalized, "doctor-probe", attrs
            if token == "status":
                normalized = args[:index] + ["doctor"] + args[index + 1 : probe_index] + args[probe_index + 1 :]
                return normalized, "doctor-status", attrs
            if not token.startswith("-"):
                break
        return args, None, attrs
    if args[index] != "research":
        return args, None, attrs

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
    synthesize_indexes: list[int] = []
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
        if not after_delimiter and token == "--synthesize":
            synthesize_indexes.append(token_index)
            continue
        if not after_delimiter and token in options_with_values:
            skip_value = True
            continue
        if not after_delimiter and token.startswith("-"):
            continue
        positionals.append((token_index, token))

    # A legacy ``research plan`` / ``research run`` is still a valid one-word
    # query. Namespace dispatch needs the literal selector plus its own query,
    # except that selector help intentionally selects the namespace path.
    if positionals and positionals[0][1] == "plan" and (len(positionals) > 1 or saw_help):
        plan_index = positionals[0][0]
        normalized = args[:index] + ["research"] + args[index + 1 : plan_index] + args[plan_index + 1 :]
        return normalized, "research-plan", attrs
    if positionals and positionals[0][1] == "run" and (len(positionals) > 1 or saw_help):
        run_index = positionals[0][0]
        drop_indexes = {run_index, *synthesize_indexes}
        body = [token for token_index, token in enumerate(args[index + 1 :], start=index + 1) if token_index not in drop_indexes]
        normalized = args[:index] + ["research"] + body
        attrs["synthesize"] = bool(synthesize_indexes)
        return normalized, "research-run", attrs
    return args, None, attrs


def normalize_namespace_argv(argv: list[str] | None) -> list[str]:
    """Translate only unambiguous colliding namespace paths to legacy leaves."""
    return _normalize_namespace_invocation(argv)[0]


def namespace_operation_for_argv(argv: list[str] | None) -> str | None:
    """Return the unique namespace operation selected by a colliding path."""
    return _normalize_namespace_invocation(argv)[1]


def classify_namespace_argv(
    argv: list[str] | None,
) -> tuple[list[str], str | None, dict[str, object]]:
    """Return normalized argv, collision-safe operation, and namespace attrs."""
    return _normalize_namespace_invocation(argv)


# ---------------------------------------------------------------------------
# Canonical command-domain classifier
# ---------------------------------------------------------------------------
# Final routing: evidence commands always use V2, retained control-plane
# leaves always use V3, and ``research plan`` / ``research run`` always use the
# Research Workflow family. Removed selectors, aliases, and legacy spellings
# fail with the replacement family's strict INVALID_ARGUMENT envelope before
# any owner/config/provider import.


def _starts_with_flag(token: str) -> bool:
    return token.startswith("-") and token != "--"


def _is_help_only_invocation(argv: list[str]) -> bool:
    """True when a canonical namespace prefix is followed only by help flags.

    ``config --help``, ``doctor --help``, and ``research --help`` must show
    the namespace's own argparse help (SystemExit 0) instead of being
    rejected as removed bare spellings.
    """
    return len(argv) >= 2 and all(token in {"--help", "-h"} for token in argv[1:])


def _leading_command_tokens(argv: list[str]) -> tuple[str, ...]:
    """Return the first one or two non-flag command tokens."""
    tokens: list[str] = []
    for token in argv:
        if _starts_with_flag(token):
            if tokens:
                break
            continue
        tokens.append(token)
        if len(tokens) == 2:
            break
    return tuple(tokens)


def _removed_selector_spelling(argv: list[str]) -> str | None:
    """Return the exact removed schema-selector spelling if one is present."""
    for index, token in enumerate(argv):
        if token in {"--schema-version", "-schema-version"}:
            if index + 1 < len(argv) and not _starts_with_flag(argv[index + 1]):
                return f"{token} {argv[index + 1]}"
            return token
        if token.startswith("--schema-version=") or token.startswith("-schema-version="):
            return token
    return None


def _strip_selector(argv: list[str]) -> list[str]:
    """Return argv without the removed schema-selector flag and its value."""
    out: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--schema-version", "-schema-version"}:
            index += 2
            continue
        if token.startswith("--schema-version=") or token.startswith("-schema-version="):
            index += 1
            continue
        out.append(token)
        index += 1
    return out


def classify_command_domain(argv: list[str] | None) -> dict[str, object]:
    """Classify raw argv into a canonical command domain.

    Returns a dict with keys:
      family: "v2" | "v3" | "workflow" | "removed" | "unknown"
      command: canonical command name for parser errors
      operation: canonical operation id for parser errors
      error_family: replacement family ("v2"|"v3"|"workflow") for removed
      legacy_spelling: the removed spelling text for removed
      replacement: the canonical replacement text for removed
    """
    args = list(argv) if argv is not None else []

    # 1. Removed selector syntax anywhere in argv.
    selector = _removed_selector_spelling(args)
    if selector is not None:
        stripped = _strip_selector(args)
        inner = _classify_canonical(stripped)
        if inner["family"] in ("v2", "v3", "workflow"):
            return {
                "family": "removed",
                "command": inner.get("command"),
                "operation": inner.get("operation"),
                "error_family": inner["family"],
                "legacy_spelling": selector,
                "replacement": SELECTOR_REPLACEMENT,
            }
        if inner["family"] == "removed":
            return inner
        # selector-only or unidentifiable input -> V2 root parser-error sentinel
        return {
            "family": "removed",
            "command": None,
            "operation": None,
            "error_family": "v2",
            "legacy_spelling": selector,
            "replacement": SELECTOR_REPLACEMENT,
        }

    return _classify_canonical(args)


def _classify_canonical(argv: list[str]) -> dict[str, object]:
    """Classify selector-free argv into the canonical command domain."""
    if not argv:
        return {"family": "unknown", "command": None, "operation": None}
    # help/version-only invocations stay with argparse SystemExit(0).
    if argv[0] in {"--help", "-h", "--version", "--v", "-v"}:
        return {"family": "argparse", "command": None, "operation": None}

    tokens = _leading_command_tokens(argv)
    if not tokens:
        return {"family": "unknown", "command": None, "operation": None}
    first = tokens[0]

    # Evidence Core always uses V2.
    if first in V2_SUPPORTED_COMMANDS:
        return {
            "family": "v2",
            "command": first,
            "operation": _V2_COMMAND_OPERATION[first],
        }

    # Research plan/run are canonical workflow commands; bare research is a
    # removed legacy workflow spelling. ``research --help`` stays with
    # argparse help.
    if first == "research":
        if _is_help_only_invocation(argv):
            return {"family": "argparse", "command": None, "operation": None}
        _normalized, operation, _attrs = _normalize_namespace_invocation(argv)
        if operation in ("research-run", "research-plan"):
            return {"family": "workflow", "command": "research", "operation": "research.run"}
        # A leading ``plan``/``run`` selector without a query is a
        # missing-query invocation of the canonical workflow command, not a
        # bare ``research`` spelling: the diagnostic names the missing query.
        tokens = _leading_command_tokens(argv)
        if len(tokens) >= 2 and tokens[1] in ("plan", "run"):
            return {
                "family": "workflow",
                "command": "research",
                "operation": "research.run",
                "missing_query": f"research {tokens[1]}",
            }
        return {
            "family": "removed",
            "command": "research",
            "operation": None,
            "error_family": "workflow",
            "legacy_spelling": "research",
            "replacement": "research run",
        }

    # Canonical V3 control-plane leaves win over reserved spellings. A
    # canonical namespace prefix followed only by help flags shows the
    # namespace's argparse help instead of a removed-spelling error.
    if first in _V3_NAMESPACE_PREFIXES:
        if _is_help_only_invocation(argv):
            return {"family": "argparse", "command": None, "operation": None}
        from .control_plane_contract import operation_for_argv

        descriptor = operation_for_argv(argv)
        if descriptor is not None:
            return {
                "family": "v3",
                "command": descriptor.command,
                "operation": descriptor.operation,
            }
        # A canonical namespace with an unknown/missing leaf is a v3 parse
        # error, except when the exact spelling is itself a removed legacy
        # command (for example bare ``config`` or ``doctor``).
        hit = _match_reserved_spelling(tokens)
        if hit is not None:
            family, replacement = hit
            return {
                "family": "removed",
                "command": first,
                "operation": None,
                "error_family": family,
                "legacy_spelling": " ".join(tokens),
                "replacement": replacement,
            }
        return {"family": "v3", "command": first, "operation": None}

    # Removed aliases and legacy commands.
    hit = _match_reserved_spelling(tokens)
    if hit is not None:
        family, replacement = hit
        return {
            "family": "removed",
            "command": first,
            "operation": None,
            "error_family": family,
            "legacy_spelling": " ".join(tokens),
            "replacement": replacement,
        }

    return {"family": "unknown", "command": first, "operation": None}


def _match_reserved_spelling(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """Return (family, replacement) for the longest reserved spelling match."""
    if len(tokens) >= 2 and tokens[:2] in RESERVED_LEGACY_SPELLINGS:
        return RESERVED_LEGACY_SPELLINGS[tokens[:2]]
    if tokens[:1] in RESERVED_LEGACY_SPELLINGS:
        return RESERVED_LEGACY_SPELLINGS[tokens[:1]]
    return None


def removed_spelling_message(legacy_spelling: str, replacement: str) -> str:
    """Deterministic user-facing message for a removed spelling."""
    if replacement == SELECTOR_REPLACEMENT:
        return f"removed selector {legacy_spelling!r}; omit the selector and route by canonical command domain"
    return f"removed spelling {legacy_spelling!r}; use {replacement}"



def help_all_text() -> str:
    lines = ["Smart Search command reference", "", "Evidence Core (V2):"]
    lines.extend(f"  {command}" for command in ("search", "fetch", "map", "capabilities"))
    lines.extend(("", "Research workflow:"))
    lines.append("  research plan QUERY  [core; offline]")
    lines.append("  research run QUERY  [advanced; live]")
    lines.extend(("", "Control plane (V3):"))
    lines.append("  config path|list|set|unset  [advanced; local]")
    for item in NAMESPACE_COMMANDS:
        if item["command"] in {"doctor", "provider-list", "provider-status", "provider-probe", "provider", "dev"}:
            lines.append(f"  {item['path']}  [{item['tier']}; {item['network']}]")
    return "\n".join(lines) + "\n"


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


__all__ = [
    "CLIParseError",
    "DEFAULT_SKILL_TARGET_IDS",
    "EXIT_CONFIG_ERROR",
    "EXIT_NETWORK_ERROR",
    "EXIT_OK",
    "EXIT_PARAMETER_ERROR",
    "EXIT_RUNTIME_ERROR",
    "FIRECRAWL_DEFAULT_API_URL",
    "NAMESPACE_COMMANDS",
    "PUBLIC_COMMANDS",
    "SmartSearchArgumentParser",
    "TAVILY_DEFAULT_API_URL",
    "V2_SUPPORTED_COMMANDS",
    "ZHIPU_DEFAULT_API_URL",
    "ZHIPU_SEARCH_ENGINE_CHOICES",
    "_get_version",
    "RESERVED_LEGACY_SPELLINGS",
    "SELECTOR_REPLACEMENT",
    "classify_command_domain",
    "classify_namespace_argv",
    "help_all_text",
    "namespace_operation_for_argv",
    "normalize_namespace_argv",
    "removed_spelling_message",
]
