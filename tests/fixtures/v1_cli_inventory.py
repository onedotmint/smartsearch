"""Frozen v1 CLI inventory derived from the live argparse parser."""
from __future__ import annotations

from typing import Any

from smart_search.cli import build_parser


CANONICAL_TOP_LEVEL_COMMANDS: tuple[str, ...] = (
    "read",
    "research",
    "search",
    "setup",
)
ROOT_HELP_COMMANDS = CANONICAL_TOP_LEVEL_COMMANDS
ALIAS_TO_CANONICAL: dict[str, str] = {}
NESTED_CANONICAL_SUBCOMMANDS: dict[str, tuple[str, ...]] = {}
NESTED_ALIAS_TO_CANONICAL: dict[str, dict[str, str]] = {}
SERVICE_PUBLIC_EXPORTS: tuple[str, ...] = ()


def inventory_from_parser(parser: Any | None = None) -> dict[str, Any]:
    """Build the current top-level inventory without importing a retired parser."""
    parser = parser or build_parser()
    action = next(item for item in parser._actions if item.dest == "operation")
    commands = tuple(sorted(action.choices))
    return {
        "canonical_top_level": commands,
        "aliases": {},
        "root_help": commands,
        "nested": {},
    }


__all__ = [
    "ALIAS_TO_CANONICAL",
    "CANONICAL_TOP_LEVEL_COMMANDS",
    "NESTED_ALIAS_TO_CANONICAL",
    "NESTED_CANONICAL_SUBCOMMANDS",
    "ROOT_HELP_COMMANDS",
    "SERVICE_PUBLIC_EXPORTS",
    "inventory_from_parser",
]
