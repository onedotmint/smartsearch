"""Frozen v1 CLI inventory derived from parser/dispatch facts.

This module is the Phase 0 audit baseline for top-level commands, aliases,
nested subcommands, and the root-help public set. Tests rebuild the inventory
from the live parser and compare against these fixtures so future additive or
breaking surface changes are explicit.
"""

from __future__ import annotations

from typing import Any

from smart_search.cli_parser import build_parser


# 16 canonical top-level commands registered by the parser.
CANONICAL_TOP_LEVEL_COMMANDS: tuple[str, ...] = (
    "capabilities",
    "config",
    "deep",
    "diagnose",
    "doctor",
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
)

# Alias name -> canonical command, rebuilt from COMMAND_ALIASES at freeze time.
ALIAS_TO_CANONICAL: dict[str, str] = {
    "cfg": "config",
    "d": "doctor",
    "diag": "diagnose",
    "dr": "deep",
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
    "build_deep_research_plan",
    "capabilities",
    "config",
    "config_list",
    "config_path",
    "config_set",
    "config_unset",
    "current_model",
    "diagnose_openai_compatible",
    "doctor",
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
    # The final canonical parser registers only canonical commands and no
    # aliases: every registered top-level name is canonical.
    canonical: set[str] = set(top.choices)
    aliases: dict[str, str] = {}

    nested: dict[str, dict[str, Any]] = {}
    for parent in ("config",):
        parent_parser = top.choices.get(parent)
        if parent_parser is None:
            continue
        nested_action = next(
            (action for action in parent_parser._actions if action.dest == "config_command"),
            None,
        )
        if nested_action is None:
            continue
        nested_canonical: set[str] = set()
        nested_aliases: dict[str, str] = {}
        for name, subparser in nested_action.choices.items():
            command = subparser.get_default("config_command")
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
