"""Internal, additive v2 result envelope contract.

This module is deliberately data-only. Production CLI dispatch remains on the
v1 contract until canonical v2 operation handlers are available.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from .security import sanitize_data

V2_SCHEMA_VERSION = "2"
V2_TOP_LEVEL_FIELDS = (
    "schema_version", "ok", "status", "command", "operation", "result",
    "evidence", "routing", "attempts", "degradation", "error", "meta",
)
# Capability operations mirror the Phase 1 taxonomy (exactly five ids).
V2_CAPABILITY_OPERATION_IDS = (
    "source_discovery",
    "docs_discovery",
    "content_fetch",
    "site_discovery",
    "answer_synthesis",
)
# Envelope-only meta operation for identified v2 capabilities inspection.
# Not a Provider capability and never valid in Research Plan / routing /
# attempt / degradation / gap / trace capability-bearing fields.
V2_META_OPERATION_IDS = (
    "capability_status",
)
V2_ENVELOPE_OPERATION_IDS = V2_CAPABILITY_OPERATION_IDS + V2_META_OPERATION_IDS
# Back-compat alias: capability operation domain used by taxonomy comparisons.
V2_OPERATION_IDS = V2_CAPABILITY_OPERATION_IDS
V2_META_OPERATION_CAPABILITY_STATUS = "capability_status"

EXIT_SUCCESS = 0
EXIT_INVALID_ARGUMENT = 2
EXIT_CONFIGURATION = 3
EXIT_UPSTREAM = 4
EXIT_INTERNAL = 5
EXIT_DEGRADED = 6
V2_EXIT_SUCCESS = EXIT_SUCCESS
V2_EXIT_INVALID_ARGUMENT = EXIT_INVALID_ARGUMENT
V2_EXIT_CONFIGURATION = EXIT_CONFIGURATION
V2_EXIT_UPSTREAM = EXIT_UPSTREAM
V2_EXIT_INTERNAL = EXIT_INTERNAL
V2_EXIT_DEGRADED = EXIT_DEGRADED


class V2ContractError(ValueError):
    """Raised when a typed or raw v2 envelope violates the contract."""


class V2Status(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


class V2AttemptStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    SKIPPED = "skipped"


class V2ErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FETCH_FAILED = "FETCH_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_RETRYABILITY: Mapping[V2ErrorCode, bool] = MappingProxyType({
    V2ErrorCode.INVALID_ARGUMENT: False,
    V2ErrorCode.CONFIGURATION_ERROR: False,
    V2ErrorCode.AUTHENTICATION_FAILED: False,
    V2ErrorCode.RATE_LIMITED: True,
    V2ErrorCode.UPSTREAM_TIMEOUT: True,
    V2ErrorCode.PROVIDER_UNAVAILABLE: True,
    V2ErrorCode.FETCH_FAILED: False,
    V2ErrorCode.PARSE_FAILED: False,
    V2ErrorCode.PROTOCOL_ERROR: False,
    V2ErrorCode.INSUFFICIENT_EVIDENCE: False,
    V2ErrorCode.BUDGET_EXHAUSTED: False,
    V2ErrorCode.INTERNAL_ERROR: False,
})
ERROR_EXIT_CODES: Mapping[V2ErrorCode, int] = MappingProxyType({
    V2ErrorCode.INVALID_ARGUMENT: EXIT_INVALID_ARGUMENT,
    V2ErrorCode.CONFIGURATION_ERROR: EXIT_CONFIGURATION,
    V2ErrorCode.AUTHENTICATION_FAILED: EXIT_UPSTREAM,
    V2ErrorCode.RATE_LIMITED: EXIT_UPSTREAM,
    V2ErrorCode.UPSTREAM_TIMEOUT: EXIT_UPSTREAM,
    V2ErrorCode.PROVIDER_UNAVAILABLE: EXIT_UPSTREAM,
    V2ErrorCode.FETCH_FAILED: EXIT_UPSTREAM,
    V2ErrorCode.PARSE_FAILED: EXIT_UPSTREAM,
    V2ErrorCode.PROTOCOL_ERROR: EXIT_UPSTREAM,
    V2ErrorCode.INSUFFICIENT_EVIDENCE: EXIT_UPSTREAM,
    V2ErrorCode.BUDGET_EXHAUSTED: EXIT_UPSTREAM,
    V2ErrorCode.INTERNAL_ERROR: EXIT_INTERNAL,
})
V2_ERROR_REGISTRY: Mapping[str, Mapping[str, Any]] = MappingProxyType({
    code.value: MappingProxyType({
        "retryable": ERROR_RETRYABILITY[code],
        "exit_code": ERROR_EXIT_CODES[code],
    })
    for code in V2ErrorCode
})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _tuple_values(value: Iterable[Any], name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise V2ContractError(f"{name} must be a collection, not a scalar string")
    try:
        return tuple(value)
    except TypeError as exc:
        raise V2ContractError(f"{name} must be a collection") from exc


def _secret_values(secrets: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(secrets, str):
        return (secrets,) if secrets else ()
    return tuple(str(secret) for secret in secrets if secret)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _value(value: Enum | str | None) -> str | None:
    return value.value if isinstance(value, Enum) else value


@dataclass(frozen=True)
class V2Error:
    code: V2ErrorCode | str
    message: str
    retryable: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _freeze(self.details))


@dataclass(frozen=True)
class V2Candidate:
    id: str
    resource: str
    provider: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class V2EvidenceItem:
    id: str
    resource: str
    provider: str
    title: str
    content: str


@dataclass(frozen=True)
class V2Citation:
    id: str
    evidence_id: str
    label: str


@dataclass(frozen=True)
class V2Gap:
    code: str
    message: str
    capability: str = ""
    resource: str = ""


@dataclass(frozen=True)
class V2Evidence:
    candidates: tuple[V2Candidate, ...] = ()
    items: tuple[V2EvidenceItem, ...] = ()
    citations: tuple[V2Citation, ...] = ()
    gaps: tuple[V2Gap, ...] = ()

    def __post_init__(self) -> None:
        for name in ("candidates", "items", "citations", "gaps"):
            object.__setattr__(self, name, _tuple_values(getattr(self, name), f"evidence.{name}"))


@dataclass(frozen=True)
class V2Routing:
    requested_capabilities: tuple[str, ...] = ()
    executed_capabilities: tuple[str, ...] = ()
    policy_version: str = "v2"
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("requested_capabilities", "executed_capabilities", "reason_codes"):
            object.__setattr__(self, name, _tuple_values(getattr(self, name), f"routing.{name}"))


@dataclass(frozen=True)
class V2Attempt:
    capability: str
    provider: str
    status: V2AttemptStatus | str
    error_code: V2ErrorCode | str | None
    elapsed_ms: int
    result_count: int


@dataclass(frozen=True)
class V2Degradation:
    code: str
    capability: str
    message: str


@dataclass(frozen=True)
class V2Meta:
    request_id: str
    duration_ms: int
    warnings: tuple[str, ...] = ()
    deprecations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "warnings", _tuple_values(self.warnings, "meta.warnings"))
        object.__setattr__(self, "deprecations", _tuple_values(self.deprecations, "meta.deprecations"))


@dataclass(frozen=True)
class V2TraceEvent:
    operation: str = ""
    capability: str = ""
    provider: str = ""
    status: str = ""
    error_code: str = ""
    evidence_id: str = ""
    reason_codes: tuple[str, ...] = ()
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", _tuple_values(self.reason_codes, "trace.reason_codes"))


@dataclass(frozen=True)
class V2Envelope:
    status: V2Status | str
    command: str
    operation: str | None
    result: Mapping[str, Any]
    evidence: V2Evidence
    routing: V2Routing
    attempts: tuple[V2Attempt, ...]
    degradation: tuple[V2Degradation, ...]
    error: V2Error | None
    meta: V2Meta

    def __post_init__(self) -> None:
        object.__setattr__(self, "result", _freeze(self.result))
        object.__setattr__(self, "attempts", _tuple_values(self.attempts, "attempts"))
        object.__setattr__(self, "degradation", _tuple_values(self.degradation, "degradation"))

    @property
    def ok(self) -> bool:
        return _value(self.status) in (V2Status.COMPLETE.value, V2Status.DEGRADED.value)


_NONBLANK = {"type": "string", "pattern": r"\S"}
_CAPABILITY = {"enum": list(V2_CAPABILITY_OPERATION_IDS)}
_ENVELOPE_OPERATION = {"enum": list(V2_ENVELOPE_OPERATION_IDS)}
_ERROR_CODES = [code.value for code in V2ErrorCode]


def _strict_object(required: Sequence[str], properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object", "required": list(required),
        "additionalProperties": False, "properties": dict(properties),
    }


def _error_schema() -> dict[str, Any]:
    schema = _strict_object(("code", "message", "retryable", "details"), {
        "code": {"enum": _ERROR_CODES}, "message": _NONBLANK,
        "retryable": {"type": "boolean"}, "details": {"type": "object"},
    })
    schema["oneOf"] = [{
        "properties": {"code": {"const": code.value}, "retryable": {"const": retryable}},
        "required": ["code", "retryable"],
    } for code, retryable in ERROR_RETRYABILITY.items()]
    return schema


_EMPTY_EVIDENCE = {
    "allOf": [
        {"$ref": "#/$defs/evidence"},
        {"properties": {
            "candidates": {"maxItems": 0},
            "items": {"maxItems": 0},
            "citations": {"maxItems": 0},
            "gaps": {"maxItems": 0},
        }},
    ],
}
_EMPTY_ROUTING = {
    "allOf": [
        {"$ref": "#/$defs/routing"},
        {"properties": {
            "requested_capabilities": {"maxItems": 0},
            "executed_capabilities": {"maxItems": 0},
        }},
    ],
}
_CAPABILITY_STATUS_SHAPE = {
    "operation": {"const": V2_META_OPERATION_CAPABILITY_STATUS},
    "evidence": _EMPTY_EVIDENCE,
    "routing": _EMPTY_ROUTING,
    "attempts": {"maxItems": 0},
    "degradation": {"maxItems": 0},
}

V2_ENVELOPE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://smart-search.local/schema/v2/envelope.json",
    "x-smart-search-semantic-validator": "smart_search.v2_contract.validate_envelope_dict",
    **_strict_object(V2_TOP_LEVEL_FIELDS, {
        "schema_version": {"const": V2_SCHEMA_VERSION},
        "ok": {"type": "boolean"},
        "status": {"enum": [status.value for status in V2Status]},
        "command": _NONBLANK,
        "operation": {"oneOf": [_ENVELOPE_OPERATION, {"type": "null"}]},
        "result": {"type": "object"},
        "evidence": {"$ref": "#/$defs/evidence"},
        "routing": {"$ref": "#/$defs/routing"},
        "attempts": {"type": "array", "items": {"$ref": "#/$defs/attempt"}},
        "degradation": {"type": "array", "items": {"$ref": "#/$defs/degradation"}},
        "error": {"oneOf": [{"$ref": "#/$defs/error"}, {"type": "null"}]},
        "meta": {"$ref": "#/$defs/meta"},
    }),
    "$defs": {},
    "oneOf": [
        {"properties": {
            "status": {"const": "complete"}, "ok": {"const": True},
            "operation": _CAPABILITY, "error": {"type": "null"},
            "attempts": {
                "type": "array",
                "items": {"properties": {"status": {"enum": ["ok", "empty"]}}},
            },
            "degradation": {"maxItems": 0},
        }},
        {"properties": {
            "status": {"const": "complete"}, "ok": {"const": True},
            "error": {"type": "null"},
            **_CAPABILITY_STATUS_SHAPE,
        }},
        {"properties": {
            "status": {"const": "degraded"}, "ok": {"const": True},
            "operation": _CAPABILITY, "error": {"type": "null"},
            "degradation": {"minItems": 1},
        }},
        {
            "properties": {
                "status": {"const": "failed"}, "ok": {"const": False},
                "error": {"$ref": "#/$defs/error"}, "degradation": {"maxItems": 0},
            },
            "oneOf": [
                {"properties": {"operation": _CAPABILITY}},
                {"properties": dict(_CAPABILITY_STATUS_SHAPE)},
                {"properties": {
                    "operation": {"type": "null"},
                    "error": {"allOf": [
                        {"$ref": "#/$defs/error"},
                        {"properties": {"code": {"const": "INVALID_ARGUMENT"}}},
                    ]},
                    "routing": {"allOf": [
                        {"$ref": "#/$defs/routing"},
                        {"properties": {
                            "requested_capabilities": {"maxItems": 0},
                            "executed_capabilities": {"maxItems": 0},
                        }},
                    ]},
                    "attempts": {"maxItems": 0},
                }},
            ],
        },
    ],
}

_defs = V2_ENVELOPE_JSON_SCHEMA["$defs"]
_defs["error"] = _error_schema()
_defs["candidate"] = {
    **_strict_object(("id", "resource", "provider", "title", "snippet"), {
        "id": _NONBLANK, "resource": _NONBLANK, "provider": _NONBLANK,
        "title": {"type": "string"}, "snippet": {"type": "string"},
    }),
    "anyOf": [
        {"properties": {"title": _NONBLANK}},
        {"properties": {"snippet": _NONBLANK}},
    ],
}
_defs["evidence_item"] = _strict_object(("id", "resource", "provider", "title", "content"), {
    "id": _NONBLANK, "resource": _NONBLANK, "provider": _NONBLANK,
    "title": {"type": "string"}, "content": _NONBLANK,
})
_defs["citation"] = _strict_object(("id", "evidence_id", "label"), {
    "id": _NONBLANK, "evidence_id": _NONBLANK, "label": _NONBLANK,
})
_defs["gap"] = _strict_object(("code", "message", "capability", "resource"), {
    "code": _NONBLANK, "message": _NONBLANK,
    "capability": {"oneOf": [_CAPABILITY, {"const": ""}]},
    "resource": {"type": "string"},
})
_defs["evidence"] = _strict_object(("candidates", "items", "citations", "gaps"), {
    "candidates": {"type": "array", "items": {"$ref": "#/$defs/candidate"}},
    "items": {"type": "array", "items": {"$ref": "#/$defs/evidence_item"}},
    "citations": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
    "gaps": {"type": "array", "items": {"$ref": "#/$defs/gap"}},
})
_defs["routing"] = _strict_object(
    ("requested_capabilities", "executed_capabilities", "policy_version", "reason_codes"),
    {
        "requested_capabilities": {"type": "array", "items": _CAPABILITY, "uniqueItems": True},
        "executed_capabilities": {"type": "array", "items": _CAPABILITY, "uniqueItems": True},
        "policy_version": _NONBLANK,
        "reason_codes": {"type": "array", "items": _NONBLANK, "uniqueItems": True},
    },
)
_defs["attempt"] = {
    **_strict_object(("capability", "provider", "status", "error_code", "elapsed_ms", "result_count"), {
        "capability": _CAPABILITY, "provider": _NONBLANK,
        "status": {"enum": [status.value for status in V2AttemptStatus]},
        "error_code": {"enum": [None, *_ERROR_CODES]},
        "elapsed_ms": {"type": "integer", "minimum": 0},
        "result_count": {"type": "integer", "minimum": 0},
    }),
    "oneOf": [
        {"properties": {
            "status": {"enum": ["ok", "empty"]}, "error_code": {"type": "null"},
        }},
        {"properties": {
            "status": {"enum": ["error", "skipped"]}, "error_code": {"enum": _ERROR_CODES},
        }},
    ],
}
_defs["degradation"] = _strict_object(("code", "capability", "message"), {
    "code": _NONBLANK, "capability": {"oneOf": [_CAPABILITY, {"const": ""}]},
    "message": _NONBLANK,
})
_TRACE_FIELDS = (
    "operation", "capability", "provider", "status", "error_code",
    "evidence_id", "reason_codes", "elapsed_ms",
)
_defs["trace_event"] = _strict_object(_TRACE_FIELDS, {
    "operation": {"oneOf": [_CAPABILITY, {"const": ""}]},
    "capability": {"oneOf": [_CAPABILITY, {"const": ""}]},
    "provider": {"type": "string"}, "status": {"type": "string"},
    "error_code": {"type": "string"}, "evidence_id": {"type": "string"},
    "reason_codes": {"type": "array", "items": {"type": "string"}},
    "elapsed_ms": {"type": "integer", "minimum": 0},
})
_defs["trace"] = _strict_object(("events",), {
    "events": {"type": "array", "items": {"$ref": "#/$defs/trace_event"}},
})
_defs["meta"] = _strict_object(("request_id", "duration_ms", "warnings", "deprecations"), {
    "request_id": _NONBLANK, "duration_ms": {"type": "integer", "minimum": 0},
    "warnings": {"type": "array", "items": {"type": "string"}},
    "deprecations": {"type": "array", "items": {"type": "string"}},
    "trace": {"$ref": "#/$defs/trace"},
})


def _nonblank(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise V2ContractError(f"{name} must be a non-blank string")


def _capability(value: Any, name: str, *, allow_empty: bool = False) -> None:
    """Validate a capability-bearing field (routing/attempt/gap/degradation/trace)."""
    if allow_empty and value == "":
        return
    if value not in V2_CAPABILITY_OPERATION_IDS:
        raise V2ContractError(f"{name} is not a stable v2 capability: {value!r}")


def _envelope_operation(value: Any, name: str = "operation") -> None:
    """Validate top-level envelope.operation (capability or meta)."""
    if value not in V2_ENVELOPE_OPERATION_IDS:
        raise V2ContractError(f"{name} is not a stable v2 envelope operation: {value!r}")


def _is_capability_status(operation: Any) -> bool:
    return operation == V2_META_OPERATION_CAPABILITY_STATUS


def _require_capability_status_empty_shape(result: V2Envelope) -> None:
    """capability_status envelopes must keep evidence/routing/attempts empty."""
    if result.evidence.candidates or result.evidence.items or result.evidence.citations or result.evidence.gaps:
        raise V2ContractError("capability_status requires empty evidence arrays")
    if result.routing.requested_capabilities or result.routing.executed_capabilities:
        raise V2ContractError("capability_status requires empty routing capability arrays")
    if result.attempts:
        raise V2ContractError("capability_status requires empty attempts")
    if result.degradation:
        raise V2ContractError("capability_status requires empty degradation")


def _exact_int(value: Any, name: str) -> None:
    is_integral_number = (
        type(value) is int
        or (type(value) is float and value.is_integer())
    )
    if not is_integral_number or value < 0:
        raise V2ContractError(f"{name} must be a non-negative integer")


def _unique(values: Sequence[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise V2ContractError(f"{name} values must be unique")


def _validate_json_value(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise V2ContractError(f"{name} object keys must be strings")
            _validate_json_value(item, name)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item, name)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise V2ContractError(f"{name} must contain only JSON-compatible values")


def _validate_error(error: V2Error) -> None:
    try:
        code = V2ErrorCode(_value(error.code))
    except (TypeError, ValueError) as exc:
        raise V2ContractError(f"unknown v2 error code: {error.code!r}") from exc
    _nonblank(error.message, "error.message")
    if type(error.retryable) is not bool:
        raise V2ContractError("error.retryable must be boolean")
    if error.retryable is not ERROR_RETRYABILITY[code]:
        raise V2ContractError(f"retryable does not match registry for {code.value}")
    if not isinstance(error.details, Mapping):
        raise V2ContractError("error.details must be an object")


def _validate_evidence(evidence: V2Evidence) -> None:
    candidate_ids = []
    for item in evidence.candidates:
        if not isinstance(item, V2Candidate):
            raise V2ContractError("evidence candidates must be V2Candidate values")
        for name in ("id", "resource", "provider"):
            _nonblank(getattr(item, name), f"candidate.{name}")
        if not isinstance(item.title, str) or not isinstance(item.snippet, str):
            raise V2ContractError("candidate title and snippet must be strings")
        if not item.title.strip() and not item.snippet.strip():
            raise V2ContractError("candidate requires a title or snippet")
        candidate_ids.append(item.id)
    _unique(candidate_ids, "candidate id")

    item_ids = []
    for item in evidence.items:
        if not isinstance(item, V2EvidenceItem):
            raise V2ContractError("evidence items must be V2EvidenceItem values")
        for name in ("id", "resource", "provider", "content"):
            _nonblank(getattr(item, name), f"evidence item.{name}")
        if not isinstance(item.title, str):
            raise V2ContractError("evidence item.title must be a string")
        item_ids.append(item.id)
    _unique(item_ids, "evidence item id")
    if set(candidate_ids) & set(item_ids):
        raise V2ContractError("candidate and evidence item ids must be disjoint")

    citation_ids = []
    for item in evidence.citations:
        if not isinstance(item, V2Citation):
            raise V2ContractError("citations must be V2Citation values")
        for name in ("id", "evidence_id", "label"):
            _nonblank(getattr(item, name), f"citation.{name}")
        if item.evidence_id not in item_ids:
            raise V2ContractError(f"citation references unknown evidence item: {item.evidence_id}")
        citation_ids.append(item.id)
    _unique(citation_ids, "citation id")

    for item in evidence.gaps:
        if not isinstance(item, V2Gap):
            raise V2ContractError("gaps must be V2Gap values")
        _nonblank(item.code, "gap.code")
        _nonblank(item.message, "gap.message")
        _capability(item.capability, "gap.capability", allow_empty=True)
        if not isinstance(item.resource, str):
            raise V2ContractError("gap.resource must be a string")


def _validate_routing(routing: V2Routing) -> None:
    _nonblank(routing.policy_version, "routing.policy_version")
    for capability in routing.requested_capabilities:
        _capability(capability, "routing.requested_capabilities")
    for capability in routing.executed_capabilities:
        _capability(capability, "routing.executed_capabilities")
    _unique(routing.requested_capabilities, "requested capability")
    _unique(routing.executed_capabilities, "executed capability")
    if not set(routing.executed_capabilities).issubset(routing.requested_capabilities):
        raise V2ContractError("executed capabilities must be requested")
    for reason in routing.reason_codes:
        _nonblank(reason, "routing.reason_code")
    _unique(routing.reason_codes, "routing reason code")


def _validate_attempt(attempt: V2Attempt) -> None:
    _capability(attempt.capability, "attempt.capability")
    _nonblank(attempt.provider, "attempt.provider")
    try:
        status = V2AttemptStatus(_value(attempt.status))
    except (TypeError, ValueError) as exc:
        raise V2ContractError(f"unknown attempt status: {attempt.status!r}") from exc
    _exact_int(attempt.elapsed_ms, "attempt.elapsed_ms")
    _exact_int(attempt.result_count, "attempt.result_count")
    error_code = _value(attempt.error_code)
    if status in (V2AttemptStatus.OK, V2AttemptStatus.EMPTY):
        if error_code is not None:
            raise V2ContractError(f"{status.value} attempt cannot have error_code")
    else:
        try:
            V2ErrorCode(error_code)
        except (TypeError, ValueError) as exc:
            raise V2ContractError(f"{status.value} attempt requires a known error_code") from exc


def validate_result(result: V2Envelope) -> V2Envelope:
    """Validate a typed envelope and return the same immutable value."""
    if not isinstance(result, V2Envelope):
        raise V2ContractError("result must be a V2Envelope")
    try:
        status = V2Status(_value(result.status))
    except (TypeError, ValueError) as exc:
        raise V2ContractError(f"unknown v2 status: {result.status!r}") from exc
    _nonblank(result.command, "command")
    if not isinstance(result.result, Mapping):
        raise V2ContractError("result must be an object")
    if not isinstance(result.evidence, V2Evidence) or not isinstance(result.routing, V2Routing):
        raise V2ContractError("evidence and routing must use v2 models")
    if not isinstance(result.meta, V2Meta):
        raise V2ContractError("meta must be V2Meta")
    _nonblank(result.meta.request_id, "meta.request_id")
    _exact_int(result.meta.duration_ms, "meta.duration_ms")
    if not all(isinstance(item, str) for item in (*result.meta.warnings, *result.meta.deprecations)):
        raise V2ContractError("meta warnings and deprecations must be strings")
    _validate_evidence(result.evidence)
    _validate_routing(result.routing)
    for attempt in result.attempts:
        if not isinstance(attempt, V2Attempt):
            raise V2ContractError("attempts must be V2Attempt values")
        _validate_attempt(attempt)
    for item in result.degradation:
        if not isinstance(item, V2Degradation):
            raise V2ContractError("degradation must use V2Degradation values")
        _nonblank(item.code, "degradation.code")
        _nonblank(item.message, "degradation.message")
        _capability(item.capability, "degradation.capability", allow_empty=True)

    if status is V2Status.COMPLETE:
        if result.error is not None or result.degradation:
            raise V2ContractError("complete requires error=null and empty degradation")
        if any(
            V2AttemptStatus(_value(attempt.status))
            in (V2AttemptStatus.ERROR, V2AttemptStatus.SKIPPED)
            for attempt in result.attempts
        ):
            raise V2ContractError("complete cannot contain failed or skipped attempts")
    if status is V2Status.DEGRADED and (result.error is not None or not result.degradation):
        raise V2ContractError("degraded requires error=null and non-empty degradation")
    if status is V2Status.FAILED:
        if result.error is None or result.degradation:
            raise V2ContractError("failed requires an error and empty degradation")
        _validate_error(result.error)

    if result.operation is None:
        if not (
            status is V2Status.FAILED
            and result.error is not None
            and _value(result.error.code) == V2ErrorCode.INVALID_ARGUMENT.value
            and not result.routing.requested_capabilities
            and not result.routing.executed_capabilities
            and not result.attempts
        ):
            raise V2ContractError("operation=null is restricted to pre-dispatch INVALID_ARGUMENT")
    else:
        _envelope_operation(result.operation, "operation")
        if _is_capability_status(result.operation):
            if status is V2Status.DEGRADED:
                raise V2ContractError("capability_status cannot be degraded")
            if result.command != "capabilities":
                raise V2ContractError(
                    "capability_status is only valid for the capabilities command"
                )
            _require_capability_status_empty_shape(result)
        elif status is V2Status.DEGRADED and result.operation not in V2_CAPABILITY_OPERATION_IDS:
            raise V2ContractError("degraded operation must be a capability operation")

    _validate_json_value(result.result, "result")
    if result.error:
        _validate_json_value(result.error.details, "error.details")
    try:
        json.dumps(_thaw(result.result), allow_nan=False)
        if result.error:
            json.dumps(_thaw(result.error.details), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise V2ContractError("result and error.details must be JSON-compatible") from exc
    return result


def _project(value: Any, names: Sequence[str]) -> dict[str, Any]:
    return {name: _thaw(getattr(value, name)) for name in names}


_TRACE_DEFAULTS = MappingProxyType({
    "operation": "", "capability": "", "provider": "", "status": "",
    "error_code": "", "evidence_id": "", "reason_codes": (), "elapsed_ms": 0,
})


def _trace_events(trace: Mapping[str, Any] | Iterable[Any]) -> Iterable[Any]:
    return trace.get("events", ()) if isinstance(trace, Mapping) else trace


def _whitelist_trace(trace: Mapping[str, Any] | Iterable[Any]) -> dict[str, Any]:
    events = []
    for raw in _trace_events(trace):
        source = _project(raw, _TRACE_FIELDS) if isinstance(raw, V2TraceEvent) else raw
        if not isinstance(source, Mapping):
            raise V2ContractError("trace events must be mappings or V2TraceEvent values")
        event = {name: _thaw(source.get(name, _TRACE_DEFAULTS[name])) for name in _TRACE_FIELDS}
        if not isinstance(event["reason_codes"], list):
            raise V2ContractError("trace reason_codes must be a sequence")
        events.append(event)
    return {"events": events}


def safe_trace(
    trace: Mapping[str, Any] | Iterable[Any], *, secrets: Iterable[str] | str = (),
) -> dict[str, Any]:
    """Whitelist, validate, and recursively redact trace fields."""
    sanitized = sanitize_data(_whitelist_trace(trace), _secret_values(secrets))
    _validate_trace_dict(sanitized)
    return sanitized


def serialize_result(
    result: V2Envelope, *, trace: Mapping[str, Any] | Iterable[Any] | None = None,
    secrets: Iterable[str] | str = (),
) -> dict[str, Any]:
    """Return a fresh, deterministic, recursively redacted v2 JSON object."""
    validate_result(result)
    evidence = {
        "candidates": [_project(item, ("id", "resource", "provider", "title", "snippet")) for item in result.evidence.candidates],
        "items": [_project(item, ("id", "resource", "provider", "title", "content")) for item in result.evidence.items],
        "citations": [_project(item, ("id", "evidence_id", "label")) for item in result.evidence.citations],
        "gaps": [_project(item, ("code", "message", "capability", "resource")) for item in result.evidence.gaps],
    }
    meta = _project(result.meta, ("request_id", "duration_ms", "warnings", "deprecations"))
    if trace is not None:
        meta["trace"] = _whitelist_trace(trace)
    error = None if result.error is None else {
        "code": _value(result.error.code), "message": result.error.message,
        "retryable": result.error.retryable, "details": _thaw(result.error.details),
    }
    output = {
        "schema_version": V2_SCHEMA_VERSION,
        "ok": result.ok,
        "status": _value(result.status),
        "command": result.command,
        "operation": result.operation,
        "result": _thaw(result.result),
        "evidence": evidence,
        "routing": _project(result.routing, ("requested_capabilities", "executed_capabilities", "policy_version", "reason_codes")),
        "attempts": [_project(item, ("capability", "provider", "status", "error_code", "elapsed_ms", "result_count")) for item in result.attempts],
        "degradation": [_project(item, ("code", "capability", "message")) for item in result.degradation],
        "error": error,
        "meta": meta,
    }
    sanitized = sanitize_data(output, _secret_values(secrets))
    validate_envelope_dict(sanitized)
    return sanitized


def _exact_keys(value: Any, required: Sequence[str], name: str, optional: Sequence[str] = ()) -> None:
    if not isinstance(value, dict):
        raise V2ContractError(f"{name} must be an object")
    required_set, allowed = set(required), set(required) | set(optional)
    if not required_set.issubset(value) or not set(value).issubset(allowed):
        raise V2ContractError(
            f"{name} has invalid fields; missing={sorted(required_set - set(value))} "
            f"extra={sorted(set(value) - allowed)}"
        )


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise V2ContractError(f"{name} must be an array")
    return value


def _validate_trace_dict(trace: Any) -> None:
    _exact_keys(trace, ("events",), "meta.trace")
    for event in _array(trace["events"], "trace.events"):
        _exact_keys(event, _TRACE_FIELDS, "trace event")
        for name in _TRACE_FIELDS[:-2]:
            if not isinstance(event[name], str):
                raise V2ContractError(f"trace.{name} must be a string")
        if not isinstance(event["reason_codes"], list) or not all(
            isinstance(item, str) for item in event["reason_codes"]
        ):
            raise V2ContractError("trace.reason_codes must be a string array")
        _exact_int(event["elapsed_ms"], "trace.elapsed_ms")
        _capability(event["operation"], "trace.operation", allow_empty=True)
        _capability(event["capability"], "trace.capability", allow_empty=True)


def _from_raw(raw: dict[str, Any]) -> V2Envelope:
    _exact_keys(raw, V2_TOP_LEVEL_FIELDS, "envelope")
    if raw["schema_version"] != V2_SCHEMA_VERSION or type(raw["ok"]) is not bool:
        raise V2ContractError("invalid schema_version or ok")
    ev = raw["evidence"]
    _exact_keys(ev, ("candidates", "items", "citations", "gaps"), "evidence")
    candidates, items, citations, gaps = [], [], [], []
    for item in _array(ev["candidates"], "evidence.candidates"):
        fields = ("id", "resource", "provider", "title", "snippet")
        _exact_keys(item, fields, "candidate")
        candidates.append(V2Candidate(**item))
    for item in _array(ev["items"], "evidence.items"):
        fields = ("id", "resource", "provider", "title", "content")
        _exact_keys(item, fields, "evidence item")
        items.append(V2EvidenceItem(**item))
    for item in _array(ev["citations"], "evidence.citations"):
        fields = ("id", "evidence_id", "label")
        _exact_keys(item, fields, "citation")
        citations.append(V2Citation(**item))
    for item in _array(ev["gaps"], "evidence.gaps"):
        fields = ("code", "message", "capability", "resource")
        _exact_keys(item, fields, "gap")
        gaps.append(V2Gap(**item))

    route = raw["routing"]
    route_fields = ("requested_capabilities", "executed_capabilities", "policy_version", "reason_codes")
    _exact_keys(route, route_fields, "routing")
    routing = V2Routing(
        tuple(_array(route["requested_capabilities"], "requested_capabilities")),
        tuple(_array(route["executed_capabilities"], "executed_capabilities")),
        route["policy_version"], tuple(_array(route["reason_codes"], "reason_codes")),
    )
    attempts = []
    attempt_fields = ("capability", "provider", "status", "error_code", "elapsed_ms", "result_count")
    for item in _array(raw["attempts"], "attempts"):
        _exact_keys(item, attempt_fields, "attempt")
        attempts.append(V2Attempt(**item))
    degradation = []
    for item in _array(raw["degradation"], "degradation"):
        fields = ("code", "capability", "message")
        _exact_keys(item, fields, "degradation item")
        degradation.append(V2Degradation(**item))
    error = None
    if raw["error"] is not None:
        fields = ("code", "message", "retryable", "details")
        _exact_keys(raw["error"], fields, "error")
        error = V2Error(**raw["error"])
    meta_raw = raw["meta"]
    meta_fields = ("request_id", "duration_ms", "warnings", "deprecations")
    _exact_keys(meta_raw, meta_fields, "meta", ("trace",))
    meta = V2Meta(
        meta_raw["request_id"], meta_raw["duration_ms"],
        tuple(_array(meta_raw["warnings"], "meta.warnings")),
        tuple(_array(meta_raw["deprecations"], "meta.deprecations")),
    )
    if "trace" in meta_raw:
        _validate_trace_dict(meta_raw["trace"])
    if not isinstance(raw["result"], dict):
        raise V2ContractError("result must be an object")
    envelope = V2Envelope(
        raw["status"], raw["command"], raw["operation"], raw["result"],
        V2Evidence(tuple(candidates), tuple(items), tuple(citations), tuple(gaps)),
        routing, tuple(attempts), tuple(degradation), error, meta,
    )
    validate_result(envelope)
    if raw["ok"] is not envelope.ok:
        raise V2ContractError("ok must be derived from status")
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise V2ContractError("envelope must be JSON-compatible") from exc
    return envelope


def validate_envelope_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate an untrusted raw v2 JSON object without runtime dependencies."""
    _from_raw(raw)
    return raw


def exit_code_for(
    result: V2Envelope | Mapping[str, Any] | str, *, fail_on_degraded: bool = False,
) -> int:
    """Return v2 exit policy, failing closed for unknown raw error codes."""
    if isinstance(result, V2Envelope):
        status = _value(result.status)
        error_code = _value(result.error.code) if result.error else None
    elif isinstance(result, Mapping):
        status, raw_error = result.get("status"), result.get("error")
        error_code = raw_error.get("code") if isinstance(raw_error, Mapping) else None
    else:
        status, error_code = V2Status.FAILED.value, result
    if status == V2Status.COMPLETE.value:
        return EXIT_SUCCESS
    if status == V2Status.DEGRADED.value:
        return EXIT_DEGRADED if fail_on_degraded else EXIT_SUCCESS
    if status != V2Status.FAILED.value:
        return EXIT_INTERNAL
    try:
        return ERROR_EXIT_CODES[V2ErrorCode(error_code)]
    except (KeyError, TypeError, ValueError):
        return EXIT_INTERNAL


def parser_error_result(
    command: str, operation: str | None, message: str,
    details: Mapping[str, Any] | None = None, *, request_id: str = "parser-error",
) -> V2Envelope:
    """Build a pure pre-dispatch INVALID_ARGUMENT result.

    When ``operation`` is a capability id it may appear in routing.requested.
    Meta operations (capability_status) and null keep routing capability arrays
    empty because they are not Provider capabilities.
    """
    if operation in V2_CAPABILITY_OPERATION_IDS:
        requested: tuple[str, ...] = (operation,)
    else:
        requested = ()
    result = V2Envelope(
        V2Status.FAILED, command, operation, {}, V2Evidence(),
        V2Routing(requested, (), "v2-parser-1", ("invalid_argument",)),
        (), (),
        V2Error(V2ErrorCode.INVALID_ARGUMENT, message, False, details or {}),
        V2Meta(request_id, 0),
    )
    return validate_result(result)


def capability_status_result(
    *,
    status: V2Status | str = V2Status.COMPLETE,
    result: Mapping[str, Any] | None = None,
    error: V2Error | None = None,
    request_id: str = "capability-status",
    duration_ms: int = 0,
    reason_codes: Sequence[str] = (),
) -> V2Envelope:
    """Build a pure local capability_status envelope (no Provider work)."""
    status_value = V2Status(_value(status) or V2Status.COMPLETE.value)
    if status_value is V2Status.DEGRADED:
        raise V2ContractError("capability_status cannot be degraded")
    if status_value is V2Status.FAILED and error is None:
        raise V2ContractError("failed capability_status requires an error")
    if status_value is V2Status.COMPLETE and error is not None:
        raise V2ContractError("complete capability_status requires error=null")
    envelope = V2Envelope(
        status=status_value,
        command="capabilities",
        operation=V2_META_OPERATION_CAPABILITY_STATUS,
        result=dict(result or {}),
        evidence=V2Evidence(),
        routing=V2Routing(
            (), (), "v2-capability-status-1", tuple(reason_codes),
        ),
        attempts=(),
        degradation=(),
        error=error,
        meta=V2Meta(request_id, duration_ms),
    )
    return validate_result(envelope)


__all__ = [
    "ERROR_EXIT_CODES", "ERROR_RETRYABILITY",
    "EXIT_CONFIGURATION", "EXIT_DEGRADED", "EXIT_INTERNAL",
    "EXIT_INVALID_ARGUMENT", "EXIT_SUCCESS", "EXIT_UPSTREAM",
    "V2Attempt", "V2AttemptStatus", "V2Candidate", "V2Citation",
    "V2ContractError", "V2Degradation", "V2Envelope", "V2Error",
    "V2ErrorCode", "V2Evidence", "V2EvidenceItem", "V2Gap", "V2Meta",
    "V2Routing", "V2Status", "V2TraceEvent", "V2_CAPABILITY_OPERATION_IDS",
    "V2_ENVELOPE_JSON_SCHEMA", "V2_ENVELOPE_OPERATION_IDS",
    "V2_ERROR_REGISTRY", "V2_EXIT_CONFIGURATION", "V2_EXIT_DEGRADED",
    "V2_EXIT_INTERNAL", "V2_EXIT_INVALID_ARGUMENT", "V2_EXIT_SUCCESS",
    "V2_EXIT_UPSTREAM", "V2_META_OPERATION_CAPABILITY_STATUS",
    "V2_META_OPERATION_IDS", "V2_OPERATION_IDS", "V2_SCHEMA_VERSION",
    "V2_TOP_LEVEL_FIELDS", "capability_status_result", "exit_code_for",
    "parser_error_result", "safe_trace", "serialize_result",
    "validate_envelope_dict", "validate_result",
]
