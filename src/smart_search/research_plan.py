"""Structured Research Plan model, schema, and semantic validator.

The plan is the Phase 3 truth source for offline Deep Research planning and
is embedded in the strict Research Workflow contract. The plan schema is
schema-neutral: its version and vocabulary are independent from the V2
Evidence envelope and the V3 control-plane envelope. Shell commands and
output paths are never part of the stable plan schema; they live only in
the v1 compatibility renderer.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

# Schema-neutral Research Plan family identity. Deliberately distinct from the
# V2 Evidence envelope ("2") and the V3 control-plane envelope ("3") so the
# embedded plan can never be mistaken for a capability operation result.
RESEARCH_PLAN_SCHEMA_VERSION = "research-plan-1"
# Schema-neutral executable operation vocabulary (Evidence capability ids,
# not V2 envelope ids). The Phase 3 plan generator emits only these four.
PLAN_EXECUTABLE_OPERATION_IDS = (
    "source_discovery",
    "docs_discovery",
    "content_fetch",
    "site_discovery",
)
# answer_synthesis is recognized as a taxonomy id but is not generated or
# accepted as a Phase 3 plan operation; capability_status is an envelope-only
# inspection operation, never a plan step.
PLAN_FORBIDDEN_OPERATION_IDS = frozenset(
    {
        "capability_status",
        "answer_synthesis",
        "command",
        "output_path",
    }
)
PLAN_FORBIDDEN_SERIALIZED_FIELDS = frozenset({"command", "output_path", "shell", "provider"})


class ResearchPlanError(ValueError):
    """Raised when a structured research plan violates the contract."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchPlanError(f"{name} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ResearchPlanError(f"{name} object keys must be strings")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResearchPlanError(f"{name} must be JSON-compatible") from exc
    return _freeze(value)


def _reject_forbidden_fields(value: Any, name: str) -> None:
    """Keep shell, path, and provider metadata out of every plan level."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = key.lower().replace("-", "_")
            if key in PLAN_FORBIDDEN_SERIALIZED_FIELDS or "provider" in normalized_key:
                raise ResearchPlanError(f"{name} cannot contain forbidden field {key!r}")
            _reject_forbidden_fields(item, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_forbidden_fields(item, f"{name}[{index}]")


def _as_depends(value: Any) -> tuple[str, ...]:
    if value is None:
        raise ResearchPlanError("depends_on must be a collection")
    if isinstance(value, (str, bytes, bytearray)):
        raise ResearchPlanError("depends_on must be a collection, not a scalar string")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise ResearchPlanError("depends_on must be a collection") from exc
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ResearchPlanError("depends_on entries must be non-blank strings")
    if len(items) != len(set(items)):
        raise ResearchPlanError("depends_on values must be unique within an operation")
    return items


@dataclass(frozen=True)
class ResearchPlanOperation:
    id: str
    operation: str
    input: Mapping[str, Any] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ResearchPlanError("operation id must be a non-blank string")
        if self.operation not in PLAN_EXECUTABLE_OPERATION_IDS:
            raise ResearchPlanError(
                f"operation {self.operation!r} is not an executable Phase 3 plan operation"
            )
        if self.operation in PLAN_FORBIDDEN_OPERATION_IDS:
            raise ResearchPlanError(f"operation {self.operation!r} is forbidden in Research Plan")
        input_data = _as_mapping(self.input, "input")
        constraints = _as_mapping(self.constraints, "constraints")
        _reject_forbidden_fields(input_data, "input")
        _reject_forbidden_fields(constraints, "constraints")
        object.__setattr__(self, "input", input_data)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "depends_on", _as_depends(self.depends_on))
        if self.id in self.depends_on:
            raise ResearchPlanError(f"operation {self.id!r} cannot depend on itself")
        forbidden = set(self.input) | set(self.constraints)
        leaked = forbidden & PLAN_FORBIDDEN_SERIALIZED_FIELDS
        if leaked:
            raise ResearchPlanError(
                f"plan input/constraints cannot contain shell fields: {sorted(leaked)}"
            )


@dataclass(frozen=True)
class ResearchPlan:
    schema_version: str
    operations: tuple[ResearchPlanOperation, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RESEARCH_PLAN_SCHEMA_VERSION:
            raise ResearchPlanError(
                f"unsupported research plan schema_version: {self.schema_version!r}"
            )
        try:
            ops = tuple(self.operations)
        except TypeError as exc:
            raise ResearchPlanError("operations must be a collection") from exc
        object.__setattr__(self, "operations", ops)
        validate_research_plan(self)


_NONBLANK = {"type": "string", "pattern": r"\S"}
_OPERATION_ENUM = {"enum": list(PLAN_EXECUTABLE_OPERATION_IDS)}


def _strict_object(required: Sequence[str], properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": False,
        "properties": dict(properties),
    }


RESEARCH_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://smart-search.local/schema/research-plan.json",
    "x-smart-search-semantic-validator": "smart_search.research_plan.validate_research_plan_dict",
    **_strict_object(
        ("schema_version", "operations"),
        {
            "schema_version": {"const": RESEARCH_PLAN_SCHEMA_VERSION},
            "operations": {
                "type": "array",
                "minItems": 0,
                "items": {"$ref": "#/$defs/operation"},
            },
        },
    ),
    "$defs": {
        "operation": _strict_object(
            ("id", "operation", "input", "constraints", "depends_on"),
            {
                "id": _NONBLANK,
                "operation": _OPERATION_ENUM,
                "input": {"type": "object"},
                "constraints": {"type": "object"},
                "depends_on": {
                    "type": "array",
                    "items": _NONBLANK,
                    "uniqueItems": True,
                },
            },
        ),
    },
}


def validate_research_plan(plan: ResearchPlan) -> ResearchPlan:
    """Validate typed plan invariants and return the same immutable value."""
    if not isinstance(plan, ResearchPlan):
        raise ResearchPlanError("plan must be a ResearchPlan")
    if plan.schema_version != RESEARCH_PLAN_SCHEMA_VERSION:
        raise ResearchPlanError(
            f"unsupported research plan schema_version: {plan.schema_version!r}"
        )
    ids: list[str] = []
    seen: set[str] = set()
    for index, operation in enumerate(plan.operations):
        if not isinstance(operation, ResearchPlanOperation):
            raise ResearchPlanError("operations must be ResearchPlanOperation values")
        if operation.id in seen:
            raise ResearchPlanError(f"duplicate operation id: {operation.id!r}")
        seen.add(operation.id)
        ids.append(operation.id)
        if operation.operation not in PLAN_EXECUTABLE_OPERATION_IDS:
            raise ResearchPlanError(
                f"operation {operation.operation!r} is not executable in Phase 3 plans"
            )
        if operation.operation in PLAN_FORBIDDEN_OPERATION_IDS:
            raise ResearchPlanError(
                f"operation {operation.operation!r} is forbidden in Research Plan"
            )
        prior = set(ids[:-1])
        for dep in operation.depends_on:
            if dep == operation.id:
                raise ResearchPlanError(f"operation {operation.id!r} cannot depend on itself")
            if dep not in prior:
                raise ResearchPlanError(
                    f"operation {operation.id!r} depends on missing or later id {dep!r}"
                )
        # Structural guarantee: earlier-only deps are already a topological order.
        _ = index
    return plan


def serialize_research_plan(plan: ResearchPlan) -> dict[str, Any]:
    """Return a fresh JSON-compatible plan object without shell/path fields."""
    validate_research_plan(plan)
    payload = {
        "schema_version": plan.schema_version,
        "operations": [
            {
                "id": item.id,
                "operation": item.operation,
                "input": _thaw(item.input),
                "constraints": _thaw(item.constraints),
                "depends_on": list(item.depends_on),
            }
            for item in plan.operations
        ],
    }
    for key in PLAN_FORBIDDEN_SERIALIZED_FIELDS:
        if key in payload:
            raise ResearchPlanError(f"serialized plan cannot contain {key}")
    for item in payload["operations"]:
        leaked = set(item) & PLAN_FORBIDDEN_SERIALIZED_FIELDS
        if leaked:
            raise ResearchPlanError(f"serialized operation cannot contain {sorted(leaked)}")
        for container in (item["input"], item["constraints"]):
            leaked = set(container) & PLAN_FORBIDDEN_SERIALIZED_FIELDS
            if leaked:
                raise ResearchPlanError(
                    f"serialized operation fields cannot contain {sorted(leaked)}"
                )
    json.dumps(payload, allow_nan=False)
    return payload


def research_plan_from_dict(raw: Mapping[str, Any]) -> ResearchPlan:
    """Parse and validate an untrusted plan dict."""
    if not isinstance(raw, Mapping):
        raise ResearchPlanError("plan must be an object")
    if set(raw) != {"schema_version", "operations"}:
        raise ResearchPlanError(
            "plan must contain only schema_version and operations "
            f"(got {sorted(raw)})"
        )
    operations_raw = raw.get("operations")
    if not isinstance(operations_raw, list):
        raise ResearchPlanError("operations must be an array")
    operations: list[ResearchPlanOperation] = []
    for item in operations_raw:
        if not isinstance(item, Mapping):
            raise ResearchPlanError("each operation must be an object")
        required = {"id", "operation", "input", "constraints", "depends_on"}
        if set(item) != required:
            raise ResearchPlanError(
                "operation fields must be exactly "
                f"{sorted(required)} (got {sorted(item)})"
            )
        operations.append(
            ResearchPlanOperation(
                id=item["id"],
                operation=item["operation"],
                input=item["input"],
                constraints=item["constraints"],
                depends_on=item["depends_on"],
            )
        )
    return ResearchPlan(str(raw.get("schema_version", "")), tuple(operations))


def validate_research_plan_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw plan JSON and return a fresh serialized copy."""
    return serialize_research_plan(research_plan_from_dict(raw))


def build_research_plan(operations: Iterable[ResearchPlanOperation]) -> ResearchPlan:
    """Construct a validated schema-neutral Research Plan from operations."""
    return ResearchPlan(RESEARCH_PLAN_SCHEMA_VERSION, tuple(operations))


__all__ = [
    "PLAN_EXECUTABLE_OPERATION_IDS",
    "PLAN_FORBIDDEN_OPERATION_IDS",
    "PLAN_FORBIDDEN_SERIALIZED_FIELDS",
    "RESEARCH_PLAN_JSON_SCHEMA",
    "RESEARCH_PLAN_SCHEMA_VERSION",
    "ResearchPlan",
    "ResearchPlanError",
    "ResearchPlanOperation",
    "build_research_plan",
    "research_plan_from_dict",
    "serialize_research_plan",
    "validate_research_plan",
    "validate_research_plan_dict",
]
