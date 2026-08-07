"""v1 Deep Research plan projection from structured ResearchPlan.

This module owns shell quoting, evidence path construction, and legacy step
rendering. It never mutates the structured plan and renders only retained
canonical generic commands (``search``, ``fetch``, ``map``). Removed exact
Provider/Experimental spellings are never rendered as tools or commands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .research_plan import (
    ResearchPlan,
    ResearchPlanError,
    ResearchPlanOperation,
    serialize_research_plan,
)

LEGACY_PLAN_PROJECTION_VERSION = "v1-plan-projection-1"

# Renderer kinds permitted only in the non-serialized projection context.
# All kinds render to retained canonical generic commands only.
LEGACY_RENDERER_KINDS = frozenset({"search", "fetch", "map"})

# Map renderer_kind -> frozen v1 tool name (subset of the retained surface).
RENDERER_KIND_TO_TOOL: Mapping[str, str] = MappingProxyType(
    {
        "search": "search",
        "fetch": "fetch",
        "map": "map",
    }
)


def quote_arg(value: str) -> str:
    """Legacy PowerShell-safe quoting used by frozen v1 deep plan commands."""
    escaped = value.replace("`", "``").replace("$", "`$").replace('"', '`"')
    return f'"{escaped}"'


def path_join(base: str, filename: str) -> str:
    return str(Path(base) / filename)


@dataclass(frozen=True)
class LegacyPlanProjectionEntry:
    operation_id: str
    renderer_kind: str
    tool: str
    purpose: str
    subquestion_id: str
    args: Mapping[str, Any] = field(default_factory=dict)
    output_suffix: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ResearchPlanError("projection operation_id must be non-blank")
        if self.renderer_kind not in LEGACY_RENDERER_KINDS:
            raise ResearchPlanError(f"unknown renderer_kind: {self.renderer_kind!r}")
        expected_tool = RENDERER_KIND_TO_TOOL[self.renderer_kind]
        if self.tool != expected_tool:
            raise ResearchPlanError(
                f"renderer_kind {self.renderer_kind!r} requires tool {expected_tool!r}"
            )
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ResearchPlanError("projection purpose must be non-blank")
        if not isinstance(self.subquestion_id, str) or not self.subquestion_id.strip():
            raise ResearchPlanError("projection subquestion_id must be non-blank")
        if not isinstance(self.output_suffix, str) or not self.output_suffix.strip():
            raise ResearchPlanError("projection output_suffix must be non-blank")
        if not isinstance(self.args, Mapping):
            raise ResearchPlanError("projection args must be a mapping")
        object.__setattr__(
            self,
            "args",
            MappingProxyType({str(key): value for key, value in self.args.items()}),
        )


@dataclass(frozen=True)
class LegacyPlanProjectionContext:
    version: str
    evidence_dir: str
    entries: tuple[LegacyPlanProjectionEntry, ...]

    def __post_init__(self) -> None:
        if self.version != LEGACY_PLAN_PROJECTION_VERSION:
            raise ResearchPlanError(
                f"unsupported projection version: {self.version!r}"
            )
        if not isinstance(self.evidence_dir, str) or not self.evidence_dir.strip():
            raise ResearchPlanError("projection evidence_dir must be non-blank")
        try:
            entries = tuple(self.entries)
        except TypeError as exc:
            raise ResearchPlanError("projection entries must be a collection") from exc
        object.__setattr__(self, "entries", entries)
        ids = [entry.operation_id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ResearchPlanError("projection entries must be unique by operation_id")


def build_projection_context(
    evidence_dir: str,
    entries: Sequence[LegacyPlanProjectionEntry],
) -> LegacyPlanProjectionContext:
    return LegacyPlanProjectionContext(
        LEGACY_PLAN_PROJECTION_VERSION,
        evidence_dir,
        tuple(entries),
    )


def _render_command(
    entry: LegacyPlanProjectionEntry,
    output_path: str,
) -> str:
    args = entry.args
    kind = entry.renderer_kind
    if kind == "search":
        query = str(args.get("query", ""))
        extra_sources = int(args.get("extra_sources", 2))
        return (
            f"smart-search search {quote_arg(query)} --validation balanced "
            f"--extra-sources {extra_sources} --format json --output {quote_arg(output_path)}"
        )
    if kind == "fetch":
        target = str(args.get("url") or args.get("resource") or "<key-url>")
        return (
            f"smart-search fetch {quote_arg(target)} --format markdown "
            f"--output {quote_arg(output_path)}"
        )
    if kind == "map":
        target = str(args.get("url") or args.get("resource") or "<key-url>")
        return (
            f"smart-search map {quote_arg(target)} --format json "
            f"--output {quote_arg(output_path)}"
        )
    raise ResearchPlanError(f"unhandled renderer_kind: {kind!r}")


def render_v1_steps(
    plan: ResearchPlan,
    projection: LegacyPlanProjectionContext,
) -> list[dict[str, str]]:
    """
    Project a structured plan + projection context into frozen v1 steps[].

    Requires a one-to-one mapping between structured operations and projection
    entries. No entry may be orphaned and no operation may lack a projection.
    """
    if not isinstance(plan, ResearchPlan):
        raise ResearchPlanError("plan must be a ResearchPlan")
    if not isinstance(projection, LegacyPlanProjectionContext):
        raise ResearchPlanError("projection must be a LegacyPlanProjectionContext")

    # Touch serializer so plan fixture drift cannot silently include shell fields.
    serialize_research_plan(plan)

    plan_ids = [operation.id for operation in plan.operations]
    projection_ids = [entry.operation_id for entry in projection.entries]
    if set(plan_ids) != set(projection_ids):
        missing = sorted(set(plan_ids) - set(projection_ids))
        orphaned = sorted(set(projection_ids) - set(plan_ids))
        raise ResearchPlanError(
            f"projection must be 1:1 with plan operations; "
            f"missing={missing} orphaned={orphaned}"
        )
    if plan_ids != projection_ids:
        raise ResearchPlanError(
            "projection entry order must match structured plan operation order"
        )

    by_id = {entry.operation_id: entry for entry in projection.entries}
    steps: list[dict[str, str]] = []
    for index, operation in enumerate(plan.operations, start=1):
        entry = by_id[operation.id]
        filename = entry.output_suffix
        # Preserve absolute suffix when the projection already encoded a numbered name.
        if "/" in filename or "\\" in filename:
            output_path = filename
        else:
            output_path = path_join(projection.evidence_dir, filename)
        command = _render_command(entry, output_path)
        steps.append(
            {
                "id": f"s{index}",
                "subquestion_id": entry.subquestion_id,
                "tool": entry.tool,
                "purpose": entry.purpose,
                "command": command,
                "output_path": output_path,
            }
        )
    return steps


def projection_entry(
    operation: ResearchPlanOperation,
    *,
    renderer_kind: str,
    purpose: str,
    subquestion_id: str,
    args: Mapping[str, Any] | None = None,
    output_suffix: str,
) -> LegacyPlanProjectionEntry:
    """Helper to build a projection entry bound to a structured operation id."""
    return LegacyPlanProjectionEntry(
        operation_id=operation.id,
        renderer_kind=renderer_kind,
        tool=RENDERER_KIND_TO_TOOL[renderer_kind],
        purpose=purpose,
        subquestion_id=subquestion_id,
        args=dict(args or {}),
        output_suffix=output_suffix,
    )


__all__ = [
    "LEGACY_PLAN_PROJECTION_VERSION",
    "LEGACY_RENDERER_KINDS",
    "RENDERER_KIND_TO_TOOL",
    "LegacyPlanProjectionContext",
    "LegacyPlanProjectionEntry",
    "build_projection_context",
    "path_join",
    "projection_entry",
    "quote_arg",
    "render_v1_steps",
]
