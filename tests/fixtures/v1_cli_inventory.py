"""Frozen v1 CLI inventory derived from parser/dispatch facts.

This module is the Phase 0 audit baseline for top-level commands, aliases,
nested subcommands, and the root-help public set. Tests rebuild the inventory
from the live parser and compare against these fixtures so future additive or
breaking surface changes are explicit.
"""

from __future__ import annotations

from typing import Any

from smart_search.cli_parser import build_parser


# 30 canonical top-level commands registered by the parser.
CANONICAL_TOP_LEVEL_COMMANDS: tuple[str, ...] = (
    "anysearch-batch",
    "anysearch-domains",
    "anysearch-extract",
    "anysearch-search",
    "capabilities",
    "config",
    "context7-docs",
    "context7-library",
    "deep",
    "diagnose",
    "doctor",
    "exa-search",
    "exa-similar",
    "fetch",
    "map",
    "model",
    "regression",
    "research",
    "route",
    "route-calibrate",
    "search",
    "setup",
    "skills",
    "smoke",
    "zhipu-mcp-read-file",
    "zhipu-mcp-reader",
    "zhipu-mcp-repo-structure",
    "zhipu-mcp-search",
    "zhipu-mcp-search-doc",
    "zhipu-search",
)

# Alias name -> canonical command, rebuilt from COMMAND_ALIASES at freeze time.
ALIAS_TO_CANONICAL: dict[str, str] = {
    "as": "anysearch-search",
    "as-batch": "anysearch-batch",
    "as-domains": "anysearch-domains",
    "as-extract": "anysearch-extract",
    "as-search": "anysearch-search",
    "c7": "context7-library",
    "c7d": "context7-docs",
    "c7docs": "context7-docs",
    "cfg": "config",
    "ctx7": "context7-library",
    "ctx7-docs": "context7-docs",
    "d": "doctor",
    "diag": "diagnose",
    "dr": "deep",
    "exa": "exa-search",
    "f": "fetch",
    "init": "setup",
    "m": "map",
    "mdl": "model",
    "rcal": "route-calibrate",
    "reg": "regression",
    "route-cal": "route-calibrate",
    "rs": "research",
    "rt": "route",
    "s": "search",
    "skill": "skills",
    "sm": "smoke",
    "x": "exa-search",
    "xs": "exa-similar",
    "z": "zhipu-search",
    "zmcp-doc": "zhipu-mcp-search-doc",
    "zmcp-file": "zhipu-mcp-read-file",
    "zmcp-reader": "zhipu-mcp-reader",
    "zmcp-search": "zhipu-mcp-search",
    "zmcp-tree": "zhipu-mcp-repo-structure",
    "zp": "zhipu-search",
}

# Root help whitelist (display only; hidden commands remain parseable). Keep
# this literal rather than importing PUBLIC_COMMANDS so surface drift is caught.
ROOT_HELP_COMMANDS: tuple[str, ...] = (
    "search",
    "fetch",
    "capabilities",
    "setup",
)

NESTED_CANONICAL_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "config": ("list", "path", "set", "unset"),
    "model": ("add", "current", "list", "remove"),
    "skills": ("status", "update"),
}

NESTED_ALIAS_TO_CANONICAL: dict[str, dict[str, str]] = {
    "config": {
        "l": "list",
        "ls": "list",
        "p": "path",
        "rm": "unset",
        "s": "set",
        "u": "unset",
    },
    "model": {
        "a": "add",
        "c": "current",
        "cur": "current",
        "l": "list",
        "ls": "list",
        "r": "remove",
        "rm": "remove",
    },
    "skills": {
        "st": "status",
        "up": "update",
    },
}

# Stable Python service facade exports.
SERVICE_PUBLIC_EXPORTS: tuple[str, ...] = (
    "DEEP_ALLOWED_TOOLS",
    "RESEARCH_ROUTE_POLICY_VERSION",
    "anysearch_batch",
    "anysearch_domains",
    "anysearch_extract",
    "anysearch_search",
    "build_deep_research_plan",
    "call_firecrawl_scrape",
    "call_firecrawl_search",
    "call_jina_reader",
    "call_tavily_extract",
    "call_tavily_map",
    "call_tavily_search",
    "capabilities",
    "config",
    "config_list",
    "config_path",
    "config_set",
    "config_unset",
    "context7_docs",
    "context7_library",
    "current_model",
    "diagnose_openai_compatible",
    "doctor",
    "exa_find_similar",
    "exa_search",
    "extra_results_to_sources",
    "fetch",
    "fetch_available_models",
    "get_available_models_cached",
    "get_capability_status",
    "intent_router_status",
    "map_site",
    "model_add",
    "model_list",
    "model_remove",
    "provider_profiles",
    "research",
    "route",
    "route_calibrate",
    "search",
    "smoke",
    "validate_command_capabilities",
    "validate_minimum_profile",
    "write_output",
    "zhipu_mcp_read_file",
    "zhipu_mcp_reader",
    "zhipu_mcp_repo_structure",
    "zhipu_mcp_search",
    "zhipu_mcp_search_doc",
    "zhipu_search",
)

# v1 compatibility fields frozen for deep / research workflows.
DEEP_STEP_REQUIRED_FIELDS: tuple[str, ...] = ("command", "output_path")
RESEARCH_COMPAT_FIELDS: tuple[str, ...] = ("final_answer", "content")


def _command_subparsers(parser):
    return next(action for action in parser._actions if getattr(action, "dest", None) == "command")


def inventory_from_parser(parser=None) -> dict[str, Any]:
    """Build the live CLI inventory from parser registration facts."""
    parser = parser or build_parser()
    top = _command_subparsers(parser)
    canonical: set[str] = set()
    aliases: dict[str, str] = {}
    legacy_names = set(CANONICAL_TOP_LEVEL_COMMANDS).union(ALIAS_TO_CANONICAL)
    for name, subparser in top.choices.items():
        if name not in legacy_names:
            continue
        command = subparser.get_default("command")
        if name == command:
            canonical.add(name)
        else:
            aliases[name] = command

    nested: dict[str, dict[str, Any]] = {}
    for parent, dest in (
        ("config", "config_command"),
        ("model", "model_command"),
        ("skills", "skills_command"),
    ):
        parent_parser = top.choices[parent]
        nested_action = next(action for action in parent_parser._actions if action.dest == dest)
        nested_canonical: set[str] = set()
        nested_aliases: dict[str, str] = {}
        for name, subparser in nested_action.choices.items():
            command = subparser.get_default(dest)
            if name == command:
                nested_canonical.add(name)
            else:
                nested_aliases[name] = command
        nested[parent] = {
            "canonical": tuple(sorted(nested_canonical)),
            "aliases": dict(sorted(nested_aliases.items())),
        }

    root_help = tuple(
        action.dest
        for action in top._choices_actions
        if action.dest in canonical
    )

    return {
        "canonical_top_level": tuple(sorted(canonical)),
        "aliases": dict(sorted(aliases.items())),
        "root_help": root_help,
        "nested": nested,
    }
