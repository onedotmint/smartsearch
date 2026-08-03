"""Dependency-light typed execution primitives shared by capability execution.

This module owns the schema-neutral value semantics for capability execution:
classified errors, attempts, candidates, evidence items, citations, gaps and
metadata, plus a generic execution outcome. It is the shared typed authority
that downstream domain owners (V2 Evidence, V3 Control, Research Workflow)
reuse, and it must never depend on CLI, V1/V2/V3/Workflow contracts, config,
runtime cache, service modules, provider adapters or the broad facade.

Only the Python standard library and ``smart_search.security`` are imported.
All nested JSON values are defensively frozen into immutable private storage
and thawed into fresh JSON-compatible trees on every projection. Redaction of
secrets, URL userinfo and sensitive query/fragment data happens only at
projection time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, Iterable, Mapping, TypeVar

from .security import sanitize_data

T = TypeVar("T")

# Stable attempt fields that may never be overridden by ``details`` during
# projection. Keeping stable field names out of ``details`` preserves the
# one-way typed -> legacy projection contract.
_STABLE_ATTEMPT_FIELDS = frozenset(
    {
        "capability",
        "provider",
        "status",
        "error_type",
        "error",
        "elapsed_ms",
        "result_count",
        "retryable",
    }
)


def _nonblank_str(value: Any, name: str) -> str:
    """Require a non-blank string and return its stripped form."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value.strip()


def _str_value(value: Any, name: str) -> str:
    """Require a string (whitespace-only is allowed and preserved) and return it."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


# ---------------------------------------------------------------------------
# JSON value support
# ---------------------------------------------------------------------------


def _is_finite_number(value: Any) -> bool:
    """True only for exact int/float (never bool) with a finite float value."""
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def _freeze_json(value: Any, path: str = "value") -> Any:
    """Validate and freeze a JSON tree into immutable tuple/read-only mapping storage."""
    if value is None or isinstance(value, str) or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    raise ValueError(f"{path} must be JSON-compatible")


def _thaw_json(value: Any) -> Any:
    """Return a fresh JSON-compatible dict/list tree from frozen storage."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# ExecutionAttemptStatus
# ---------------------------------------------------------------------------


class ExecutionAttemptStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# ExecutionError
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionError:
    """A classified execution error with stable retryability.

    ``type`` is a stable internal classification such as ``empty``,
    ``config_error``, ``timeout``, ``network_error``, ``quality_error`` or
    ``budget_exhausted``. ``message`` is a non-blank diagnostic string.
    ``details`` is a bounded immutable JSON mapping reserved for internal
    facts and never raw Provider payload.
    """

    type: str
    message: str
    retryable: bool | None = None
    details: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _nonblank_str(self.type, "ExecutionError.type"))
        object.__setattr__(self, "message", _nonblank_str(self.message, "ExecutionError.message"))
        if self.retryable is not None and type(self.retryable) is not bool:
            raise ValueError("ExecutionError.retryable must be boolean or None")
        object.__setattr__(self, "details", _freeze_json(self.details, "ExecutionError.details"))


# ---------------------------------------------------------------------------
# ExecutionAttempt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionAttempt:
    """One ordered provider attempt within a same-capability execution.

    Status/error invariants:

    - ``ok`` requires ``error=None``;
    - ``empty`` requires ``error.type == "empty"``;
    - ``error`` and ``skipped`` require an error.

    ``details`` is a bounded immutable mapping for compatibility-only facts
    such as ``cache_hit``, ``inflight_joined``, ``budget_exhausted``,
    ``configured``, ``enabled``, ``eligible`` and ``reason``. It may never
    override a stable field.
    """

    capability: str
    provider: str
    status: ExecutionAttemptStatus | str
    error: ExecutionError | None = None
    elapsed_ms: float = 0.0
    result_count: int = 0
    details: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability", _nonblank_str(self.capability, "ExecutionAttempt.capability"))
        object.__setattr__(self, "provider", _nonblank_str(self.provider, "ExecutionAttempt.provider"))
        if isinstance(self.status, ExecutionAttemptStatus):
            status = self.status
        else:
            try:
                status = ExecutionAttemptStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"ExecutionAttempt.status must be a valid status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if not _is_finite_number(self.elapsed_ms) or self.elapsed_ms < 0:
            raise ValueError("ExecutionAttempt.elapsed_ms must be a non-negative finite number")
        if type(self.result_count) is not int or self.result_count < 0:
            raise ValueError("ExecutionAttempt.result_count must be a non-negative integer")
        if status is ExecutionAttemptStatus.OK:
            if self.error is not None:
                raise ValueError("ok attempt cannot carry an error")
        elif status is ExecutionAttemptStatus.EMPTY:
            if self.error is None or self.error.type != "empty":
                raise ValueError("empty attempt must carry a classified empty error")
        else:
            if self.error is None:
                raise ValueError("error/skipped attempt must carry a classified error")
        frozen_details = _freeze_json(self.details, "ExecutionAttempt.details")
        for key in frozen_details:
            if key in _STABLE_ATTEMPT_FIELDS:
                raise ValueError(f"ExecutionAttempt.details collides with stable field: {key}")
        object.__setattr__(self, "details", frozen_details)


def success_attempt(
    capability: str,
    provider: str,
    *,
    elapsed_ms: float,
    result_count: int,
    details: Mapping[str, Any] | None = None,
) -> ExecutionAttempt:
    """Construct a successful ``ok`` attempt."""
    return ExecutionAttempt(
        capability=capability,
        provider=provider,
        status=ExecutionAttemptStatus.OK,
        error=None,
        elapsed_ms=elapsed_ms,
        result_count=result_count,
        details=details or {},
    )


def empty_attempt(
    capability: str,
    provider: str,
    *,
    elapsed_ms: float,
    message: str = "provider returned no usable result",
    retryable: bool | None = False,
    details: Mapping[str, Any] | None = None,
) -> ExecutionAttempt:
    """Construct an ``empty`` attempt carrying a classified ``empty`` error."""
    return ExecutionAttempt(
        capability=capability,
        provider=provider,
        status=ExecutionAttemptStatus.EMPTY,
        error=ExecutionError("empty", message, retryable),
        elapsed_ms=elapsed_ms,
        result_count=0,
        details=details or {},
    )


def error_attempt(
    capability: str,
    provider: str,
    *,
    error_type: str,
    message: str,
    elapsed_ms: float,
    retryable: bool | None = None,
    result_count: int = 0,
    details: Mapping[str, Any] | None = None,
) -> ExecutionAttempt:
    """Construct an ``error`` attempt with a classified error."""
    return ExecutionAttempt(
        capability=capability,
        provider=provider,
        status=ExecutionAttemptStatus.ERROR,
        error=ExecutionError(error_type, message, retryable),
        elapsed_ms=elapsed_ms,
        result_count=result_count,
        details=details or {},
    )


def skipped_attempt(
    capability: str,
    provider: str,
    *,
    error_type: str,
    message: str,
    elapsed_ms: float,
    retryable: bool | None = False,
    details: Mapping[str, Any] | None = None,
) -> ExecutionAttempt:
    """Construct a ``skipped`` attempt with a classified error."""
    return ExecutionAttempt(
        capability=capability,
        provider=provider,
        status=ExecutionAttemptStatus.SKIPPED,
        error=ExecutionError(error_type, message, retryable),
        elapsed_ms=elapsed_ms,
        result_count=0,
        details=details or {},
    )


def budget_exhausted_attempt(
    capability: str,
    *,
    provider: str = "request-budget",
    message: str = "request budget exhausted",
    elapsed_ms: float,
) -> ExecutionAttempt:
    """Construct a ``skipped`` budget-exhausted attempt."""
    return skipped_attempt(
        capability,
        provider,
        error_type="budget_exhausted",
        message=message,
        elapsed_ms=elapsed_ms,
        retryable=False,
        details={"budget_exhausted": True},
    )


# ---------------------------------------------------------------------------
# Candidate / Evidence / Citation / Gap / Metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionCandidate:
    """A schema-neutral structured discovery candidate.

    ``resource`` is the stable identity (URL or resource id). At least one of
    ``title`` or ``snippet`` must be present and non-blank so the candidate
    carries a human-readable label.
    """

    id: str
    resource: str
    provider: str
    title: str = ""
    snippet: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _nonblank_str(self.id, "ExecutionCandidate.id"))
        object.__setattr__(self, "resource", _nonblank_str(self.resource, "ExecutionCandidate.resource"))
        object.__setattr__(self, "provider", _nonblank_str(self.provider, "ExecutionCandidate.provider"))
        object.__setattr__(self, "title", _str_value(self.title, "ExecutionCandidate.title"))
        object.__setattr__(self, "snippet", _str_value(self.snippet, "ExecutionCandidate.snippet"))
        if not self.title.strip() and not self.snippet.strip():
            raise ValueError("ExecutionCandidate requires a non-blank title or snippet")


@dataclass(frozen=True)
class ExecutionEvidenceItem:
    """A schema-neutral fetched/read evidence item with provenance.

    ``content`` must be a non-blank fetched/read body so an empty body is never
    mistaken for admitted evidence.
    """

    id: str
    resource: str
    provider: str
    title: str = ""
    content: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _nonblank_str(self.id, "ExecutionEvidenceItem.id"))
        object.__setattr__(self, "resource", _nonblank_str(self.resource, "ExecutionEvidenceItem.resource"))
        object.__setattr__(self, "provider", _nonblank_str(self.provider, "ExecutionEvidenceItem.provider"))
        object.__setattr__(self, "title", _str_value(self.title, "ExecutionEvidenceItem.title"))
        object.__setattr__(self, "content", _nonblank_str(self.content, "ExecutionEvidenceItem.content"))


@dataclass(frozen=True)
class ExecutionCitation:
    """A citation reference to an evidence item id."""

    id: str
    evidence_id: str
    label: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _nonblank_str(self.id, "ExecutionCitation.id"))
        object.__setattr__(self, "evidence_id", _nonblank_str(self.evidence_id, "ExecutionCitation.evidence_id"))
        object.__setattr__(self, "label", _nonblank_str(self.label, "ExecutionCitation.label"))


@dataclass(frozen=True)
class ExecutionGap:
    """A schema-neutral workflow gap."""

    code: str
    message: str
    capability: str = ""
    resource: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _nonblank_str(self.code, "ExecutionGap.code"))
        object.__setattr__(self, "message", _nonblank_str(self.message, "ExecutionGap.message"))
        object.__setattr__(self, "capability", _str_value(self.capability, "ExecutionGap.capability"))
        object.__setattr__(self, "resource", _str_value(self.resource, "ExecutionGap.resource"))


@dataclass(frozen=True)
class ExecutionMetadata:
    """Request/duration metadata for a command execution."""

    request_id: str
    duration_ms: float = 0.0
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _nonblank_str(self.request_id, "ExecutionMetadata.request_id"))
        if not _is_finite_number(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("ExecutionMetadata.duration_ms must be a non-negative finite number")
        if not isinstance(self.warnings, tuple):
            object.__setattr__(self, "warnings", tuple(self.warnings))
        for warning in self.warnings:
            if not isinstance(warning, str):
                raise ValueError("ExecutionMetadata.warnings must contain only strings")


# ---------------------------------------------------------------------------
# ExecutionOutcome
# ---------------------------------------------------------------------------


class ExecutionOutcome(Generic[T]):
    """Generic typed outcome of one same-capability execution.

    ``value`` is the owner-normalized JSON-safe result, stored defensively and
    returned as a fresh thawed tree on every access. ``attempts`` is always a
    tuple of immutable ``ExecutionAttempt`` values. ``provider`` is the id of
    the successful provider or an empty string. The instance is immutable:
    once constructed, ``provider`` may not be reassigned and ``attempts`` may
    not be replaced.
    """

    __slots__ = ("_frozen", "_value", "_attempts", "_provider")

    def __init__(
        self,
        value: Any,
        attempts: tuple[ExecutionAttempt, ...] | list[ExecutionAttempt] = (),
        provider: str = "",
    ) -> None:
        object.__setattr__(self, "_frozen", False)
        object.__setattr__(self, "_value", _freeze_json(value, "outcome.value"))
        if not isinstance(attempts, tuple):
            attempts = tuple(attempts)
        for attempt in attempts:
            if not isinstance(attempt, ExecutionAttempt):
                raise ValueError("ExecutionOutcome.attempts must contain only ExecutionAttempt values")
        object.__setattr__(self, "_attempts", attempts)
        if not isinstance(provider, str):
            raise ValueError("ExecutionOutcome.provider must be a string")
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("ExecutionOutcome is immutable")
        object.__setattr__(self, name, value)

    @property
    def value(self) -> Any:
        """Return a fresh JSON-compatible copy of the stored value."""
        return _thaw_json(self._value)

    @property
    def attempts(self) -> tuple[ExecutionAttempt, ...]:
        """Return the ordered immutable attempts tuple."""
        return self._attempts

    @property
    def provider(self) -> str:
        """Return the successful provider id or an empty string."""
        return self._provider


# ---------------------------------------------------------------------------
# Legacy compatibility projection
# ---------------------------------------------------------------------------


def project_attempt_dict(attempt: ExecutionAttempt, secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Project one typed attempt into a fresh legacy v1-compatible dict.

    This is the single, auditable boundary that converts typed attempts back
    into the historical ``provider_attempts`` JSON shape. It preserves the
    stable base keys, optional ``retryable`` omission, cache/inflight/budget
    and eligibility detail keys, and float-compatible elapsed milliseconds.
    The result is recursively redacted using a snapshotted secret iterable.
    """

    secrets = tuple(secrets)
    error = attempt.error
    if error is not None:
        error_type = error.type
        error_text = error.message
        retryable = error.retryable
    else:
        error_type = ""
        error_text = ""
        retryable = None

    data: dict[str, Any] = {
        "capability": attempt.capability,
        "provider": attempt.provider,
        "status": attempt.status.value,
        "error_type": error_type,
        "error": error_text,
        "elapsed_ms": attempt.elapsed_ms,
        "result_count": attempt.result_count,
    }
    if retryable is not None:
        data["retryable"] = bool(retryable)
    data.update(attempt.details)
    return sanitize_data(data, secrets)


def project_attempts_dict(
    attempts: tuple[ExecutionAttempt, ...] | list[ExecutionAttempt],
    secrets: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Project a sequence of typed attempts into fresh legacy dicts.

    Only a sequence of ``ExecutionAttempt`` values is accepted. Mappings and
    arbitrary objects are rejected so that the legacy boundary never re-parses
    a typed attempt by iterating its keys.
    """
    if not isinstance(attempts, (tuple, list)):
        raise TypeError("project_attempts_dict expects a sequence of ExecutionAttempt values")
    secrets = tuple(secrets)
    result: list[dict[str, Any]] = []
    for attempt in attempts:
        if not isinstance(attempt, ExecutionAttempt):
            raise TypeError("project_attempts_dict expects only ExecutionAttempt values")
        result.append(project_attempt_dict(attempt, secrets))
    return result


__all__ = [
    "ExecutionAttempt",
    "ExecutionAttemptStatus",
    "ExecutionCandidate",
    "ExecutionCitation",
    "ExecutionError",
    "ExecutionEvidenceItem",
    "ExecutionGap",
    "ExecutionMetadata",
    "ExecutionOutcome",
    "budget_exhausted_attempt",
    "empty_attempt",
    "error_attempt",
    "project_attempt_dict",
    "project_attempts_dict",
    "skipped_attempt",
    "success_attempt",
]
