"""Schema-neutral typed Research Workflow domain and owner.

The Research Workflow is an independent strict workflow family: it is neither
a V2 Evidence capability operation nor a V3 control-plane operation. This
module owns the typed workflow value models (error, stage, artifact, meta,
outcome) and the workflow owner that composes the typed Evidence operation
owners (``evidence_operations``) through the shared execution primitives.

Domain rules enforced here:

- stages form a valid DAG in deterministic plan order; every stage carries a
  contiguous ``order`` and only earlier stage dependencies;
- only typed Evidence owners are invoked; discovery candidates never become
  evidence or citations; citations reference admitted fetched evidence only;
- fetch concurrency is bounded and normalized URLs are fetched once;
- artifact records carry only logical identity, safe relative name, media
  type, byte length, digest and status; raw bodies, absolute paths, ``..``
  traversal, credentialed URLs, arbitrary metadata and filesystem exception
  text never enter the contract;
- the stable outcome never contains a synthesized answer, ``content`` /
  ``final_answer`` aliases, synthesis flags, shell commands, output-path
  projections or raw Provider payloads.

Import surface: this module imports only the standard library, the shared
execution primitives, and the schema-neutral research plan. The typed Evidence
owners are composed lazily at execution time so that importing the value
models (or the workflow contract) never loads config, providers, or the
runtime cache, and so that the owner can never be reached from a parser-error
path.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .execution_primitives import (
    ExecutionAttempt,
    ExecutionCandidate,
    ExecutionCitation,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionGap,
)
from .research_plan import (
    ResearchPlan,
    ResearchPlanOperation,
    validate_research_plan,
)
from .security import is_sensitive_key

# Stable schema-neutral workflow executable vocabulary (Evidence capability
# ids, not V2 envelope ids).
WORKFLOW_EXECUTABLE_OPERATIONS = frozenset(
    {"source_discovery", "docs_discovery", "content_fetch", "site_discovery"}
)
_DISCOVERY_OPERATIONS = frozenset(
    {"source_discovery", "docs_discovery", "site_discovery"}
)
# Default bounded fetch concurrency for one workflow run.
WORKFLOW_FETCH_CONCURRENCY = 4
# Maximum admitted evidence items for one workflow run. Fetches beyond the
# remaining allowance are never begun and are recorded as explicit
# ``evidence_output_budget`` gaps so hosts know evidence collection was capped.
WORKFLOW_EVIDENCE_OUTPUT_LIMIT = 5

# Exit policy follows the parent migration policy shared by the V2/V3 families.
EXIT_SUCCESS = 0
EXIT_INVALID_ARGUMENT = 2
EXIT_CONFIGURATION = 3
EXIT_UPSTREAM = 4
EXIT_INTERNAL = 5
EXIT_DEGRADED = 6


class WorkflowDomainError(ValueError):
    """Raised when a typed workflow value or request violates the domain."""


class WorkflowStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


class WorkflowStageStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactStatus(str, Enum):
    WRITTEN = "written"
    PARTIAL = "partial"
    FAILED = "failed"


class WorkflowErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    FETCH_FAILED = "FETCH_FAILED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"
    FILE_SYSTEM_ERROR = "FILE_SYSTEM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


WORKFLOW_ERROR_RETRYABILITY: Mapping[WorkflowErrorCode, bool] = MappingProxyType(
    {code: False for code in WorkflowErrorCode}
)
WORKFLOW_ERROR_EXIT_CODES: Mapping[WorkflowErrorCode, int] = MappingProxyType(
    {
        WorkflowErrorCode.INVALID_ARGUMENT: EXIT_INVALID_ARGUMENT,
        WorkflowErrorCode.CONFIGURATION_ERROR: EXIT_CONFIGURATION,
        WorkflowErrorCode.FETCH_FAILED: EXIT_UPSTREAM,
        WorkflowErrorCode.INSUFFICIENT_EVIDENCE: EXIT_UPSTREAM,
        WorkflowErrorCode.BUDGET_EXHAUSTED: EXIT_UPSTREAM,
        WorkflowErrorCode.CANCELLED: EXIT_UPSTREAM,
        WorkflowErrorCode.FILE_SYSTEM_ERROR: EXIT_INTERNAL,
        WorkflowErrorCode.INTERNAL_ERROR: EXIT_INTERNAL,
    }
)
WORKFLOW_ERROR_REGISTRY: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        code.value: MappingProxyType(
            {
                "retryable": WORKFLOW_ERROR_RETRYABILITY[code],
                "exit_code": WORKFLOW_ERROR_EXIT_CODES[code],
            }
        )
        for code in WorkflowErrorCode
    }
)

# Mapping from typed ExecutionError classifications to workflow error codes.
# The vocabulary matches the parent V2 classification
# (``canonical_operations._LEGACY_ERROR_TO_V2``): an ``empty`` classification
# stays a classified FETCH_FAILED with the same upstream exit policy rather
# than an internal error, so the workflow and V2 never disagree on exit codes.
_STAGE_ERROR_CODE_MAP = {
    "config_error": WorkflowErrorCode.CONFIGURATION_ERROR,
    "budget_exhausted": WorkflowErrorCode.BUDGET_EXHAUSTED,
    "cancelled": WorkflowErrorCode.CANCELLED,
    "fetch_error": WorkflowErrorCode.FETCH_FAILED,
    "provider_error": WorkflowErrorCode.FETCH_FAILED,
    "empty": WorkflowErrorCode.FETCH_FAILED,
}

# URL query keys that may carry credentials are the single shared policy in
# ``security.SENSITIVE_QUERY_KEY_NAMES`` (consumed via ``is_sensitive_key``);
# such URLs deduplicate on their exact raw string and never enter a
# normalized cache key.

# Forbidden stage-input fields mirror the research plan serializer rules.
_WORKFLOW_FORBIDDEN_INPUT_FIELDS = frozenset({"command", "output_path", "shell"})

_ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_ARTIFACT_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
# Type and subtype must start with an alphanumeric so path-traversal-like
# values ("../evil") can never be a media type.
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")


# ---------------------------------------------------------------------------
# JSON value support
# ---------------------------------------------------------------------------


def _is_finite_number(value: Any) -> bool:
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def _freeze_json(value: Any, path: str = "value") -> Any:
    """Validate and freeze a JSON tree into immutable tuple/read-only storage."""
    if value is None or isinstance(value, str) or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise WorkflowDomainError(f"{path} must be a finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowDomainError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    raise WorkflowDomainError(f"{path} must be JSON-compatible")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _tuple_nonblank(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise WorkflowDomainError(f"{name} must be a collection, not a scalar string")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise WorkflowDomainError(f"{name} must be a collection") from exc
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise WorkflowDomainError(f"{name} entries must be non-blank strings")
    return items


def _nonblank_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDomainError(f"{name} must be a non-blank string")
    return value.strip()


def _reject_forbidden_input_fields(value: Any, path: str = "stage.input") -> None:
    """Keep shell, output-path, and provider metadata out of stage input."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if key in _WORKFLOW_FORBIDDEN_INPUT_FIELDS or "provider" in normalized_key:
                raise WorkflowDomainError(f"{path} cannot contain forbidden field {key!r}")
            _reject_forbidden_input_fields(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_forbidden_input_fields(item, f"{path}[{index}]")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    safe_prefix = re.sub(r"[^a-z0-9_-]+", "-", prefix.lower()).strip("-") or "id"
    return f"{safe_prefix}-{digest}"


def _safe_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return segment or "item"


# ---------------------------------------------------------------------------
# Error record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowError:
    """One classified workflow error with registry-matched retryability.

    ``details`` is a bounded immutable JSON mapping for internal facts only;
    it is recursively redacted at projection time and never carries raw
    Provider payloads or filesystem exception text.
    """

    code: WorkflowErrorCode | str
    message: str
    retryable: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _nonblank_str(self.message, "WorkflowError.message"))
        if isinstance(self.code, WorkflowErrorCode):
            code = self.code
        else:
            try:
                code = WorkflowErrorCode(self.code)
            except (TypeError, ValueError) as exc:
                raise WorkflowDomainError(f"unknown workflow error code: {self.code!r}") from exc
        object.__setattr__(self, "code", code)
        if type(self.retryable) is not bool:
            raise WorkflowDomainError("WorkflowError.retryable must be boolean")
        if self.retryable is not WORKFLOW_ERROR_RETRYABILITY[code]:
            raise WorkflowDomainError(
                f"WorkflowError.retryable does not match registry for {code.value}"
            )
        object.__setattr__(self, "details", _freeze_json(self.details, "WorkflowError.details"))


# ---------------------------------------------------------------------------
# Artifact record
# ---------------------------------------------------------------------------


def validate_artifact_name(name: Any) -> str:
    """Return the validated safe relative logical artifact name.

    Rejects absolute paths, drive prefixes, ``..`` traversal, backslashes,
    control/whitespace names, empty segments, and over-long names so an
    artifact record can never leak a filesystem path or credentialed URL.
    """
    if not isinstance(name, str) or not name.strip():
        raise WorkflowDomainError("artifact name must be a non-blank string")
    if len(name) > 255:
        raise WorkflowDomainError("artifact name must be at most 255 characters")
    if not _ARTIFACT_NAME_PATTERN.fullmatch(name):
        raise WorkflowDomainError(f"unsafe artifact name: {name!r}")
    if any(segment in {".", ".."} for segment in name.split("/")):
        raise WorkflowDomainError(f"artifact name contains unsafe path segment: {name!r}")
    return name


@dataclass(frozen=True)
class WorkflowArtifact:
    """A safe logical artifact record: identity, kind, status, and metadata.

    The record carries only ``id``, ``stage_id``, ``kind``, ``status``, a safe
    relative logical ``name``, ``media_type``, ``byte_length`` and ``digest``.
    Raw bodies, absolute paths, path traversal, credentialed URLs, arbitrary
    metadata and filesystem exception text are never part of the record.
    """

    id: str
    stage_id: str
    kind: str
    status: ArtifactStatus | str
    name: str
    media_type: str
    byte_length: int
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _nonblank_str(self.id, "artifact.id"))
        object.__setattr__(self, "stage_id", _nonblank_str(self.stage_id, "artifact.stage_id"))
        if not isinstance(self.kind, str) or not _ARTIFACT_KIND_PATTERN.fullmatch(self.kind):
            raise WorkflowDomainError(
                f"artifact kind must match {_ARTIFACT_KIND_PATTERN.pattern!r}: {self.kind!r}"
            )
        if isinstance(self.status, ArtifactStatus):
            status = self.status
        else:
            try:
                status = ArtifactStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise WorkflowDomainError(f"unknown artifact status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "name", validate_artifact_name(self.name))
        if not isinstance(self.media_type, str) or not _MEDIA_TYPE_PATTERN.fullmatch(self.media_type):
            raise WorkflowDomainError(f"artifact media_type must match media type syntax: {self.media_type!r}")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise WorkflowDomainError("artifact byte_length must be a non-negative integer")
        if not isinstance(self.digest, str) or not _DIGEST_PATTERN.fullmatch(self.digest):
            raise WorkflowDomainError("artifact digest must be a sha256 hex digest")


# ---------------------------------------------------------------------------
# Stage record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowStage:
    """One ordered workflow stage bound to a plan operation.

    ``order`` is the deterministic 1..N plan index; ``depends_on`` may only
    reference earlier stages. ``evidence_ids`` and ``artifact_ids`` reference
    top-level admitted evidence and artifact records. Complete/degraded stages
    carry no error; failed/cancelled stages require a classified error.
    """

    id: str
    operation: str
    status: WorkflowStageStatus | str
    order: int
    input: Mapping[str, Any]
    depends_on: tuple[str, ...] = ()
    result_count: int = 0
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    error: WorkflowError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _nonblank_str(self.id, "stage.id"))
        if self.operation not in WORKFLOW_EXECUTABLE_OPERATIONS:
            raise WorkflowDomainError(
                f"stage operation {self.operation!r} is not a workflow executable operation"
            )
        if isinstance(self.status, WorkflowStageStatus):
            status = self.status
        else:
            try:
                status = WorkflowStageStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise WorkflowDomainError(f"unknown stage status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if type(self.order) is not int or self.order < 1:
            raise WorkflowDomainError("stage order must be a positive integer")
        object.__setattr__(self, "input", _freeze_json(self.input, "stage.input"))
        _reject_forbidden_input_fields(self.input)
        object.__setattr__(self, "depends_on", _tuple_nonblank(self.depends_on, "stage.depends_on"))
        if len(self.depends_on) != len(set(self.depends_on)):
            raise WorkflowDomainError("stage depends_on values must be unique")
        if type(self.result_count) is not int or self.result_count < 0:
            raise WorkflowDomainError("stage result_count must be a non-negative integer")
        for name in ("evidence_ids", "artifact_ids"):
            object.__setattr__(self, name, _tuple_nonblank(getattr(self, name), f"stage.{name}"))
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise WorkflowDomainError("stage evidence_ids must be unique")
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise WorkflowDomainError("stage artifact_ids must be unique")
        if self.error is not None and not isinstance(self.error, WorkflowError):
            raise WorkflowDomainError("stage error must be a WorkflowError or None")
        if status in (WorkflowStageStatus.COMPLETE, WorkflowStageStatus.DEGRADED):
            if self.error is not None:
                raise WorkflowDomainError(f"{status.value} stage cannot carry an error")
        else:
            if self.error is None:
                raise WorkflowDomainError(f"{status.value} stage requires an error")


# ---------------------------------------------------------------------------
# Meta record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowMeta:
    request_id: str
    duration_ms: float = 0.0
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _nonblank_str(self.request_id, "WorkflowMeta.request_id"))
        if not _is_finite_number(self.duration_ms) or self.duration_ms < 0:
            raise WorkflowDomainError("WorkflowMeta.duration_ms must be a non-negative finite number")
        if not isinstance(self.warnings, tuple):
            object.__setattr__(self, "warnings", tuple(self.warnings))
        for warning in self.warnings:
            if not isinstance(warning, str):
                raise WorkflowDomainError("WorkflowMeta.warnings must contain only strings")


# ---------------------------------------------------------------------------
# Outcome record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowOutcome:
    """Immutable typed outcome of one research workflow run.

    Structural invariants (identity uniqueness, stage DAG/order, evidence and
    citation references, artifact references) are enforced at construction;
    the terminal state truth table (complete/degraded/failed with error and
    stage/artifact status) is enforced here for fail-fast construction and is
    authoritative in the workflow contract validator.
    """

    status: WorkflowStatus | str
    plan: ResearchPlan
    stages: tuple[WorkflowStage, ...]
    evidence: tuple[ExecutionEvidenceItem, ...]
    citations: tuple[ExecutionCitation, ...]
    gaps: tuple[ExecutionGap, ...]
    attempts: tuple[ExecutionAttempt, ...]
    artifacts: tuple[WorkflowArtifact, ...]
    error: WorkflowError | None = None
    meta: WorkflowMeta = field(default_factory=lambda: WorkflowMeta("workflow"))

    def __post_init__(self) -> None:
        if isinstance(self.status, WorkflowStatus):
            status = self.status
        else:
            try:
                status = WorkflowStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise WorkflowDomainError(f"unknown workflow status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if not isinstance(self.plan, ResearchPlan):
            raise WorkflowDomainError("WorkflowOutcome.plan must be a ResearchPlan")
        validate_research_plan(self.plan)
        for name, expected in (
            ("stages", WorkflowStage),
            ("evidence", ExecutionEvidenceItem),
            ("citations", ExecutionCitation),
            ("gaps", ExecutionGap),
            ("attempts", ExecutionAttempt),
            ("artifacts", WorkflowArtifact),
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                value = tuple(value)
            for item in value:
                if not isinstance(item, expected):
                    raise WorkflowDomainError(
                        f"WorkflowOutcome.{name} must contain only {expected.__name__} values"
                    )
            object.__setattr__(self, name, value)
        if self.error is not None and not isinstance(self.error, WorkflowError):
            raise WorkflowDomainError("WorkflowOutcome.error must be a WorkflowError or None")
        if not isinstance(self.meta, WorkflowMeta):
            raise WorkflowDomainError("WorkflowOutcome.meta must be a WorkflowMeta")

        stage_ids = [stage.id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise WorkflowDomainError("stage ids must be unique")
        if [stage.order for stage in self.stages] != list(range(1, len(self.stages) + 1)):
            raise WorkflowDomainError("stage order must be a contiguous 1..N sequence")
        known = set(stage_ids)
        order_of = {stage.id: stage.order for stage in self.stages}
        for stage in self.stages:
            for dep in stage.depends_on:
                if dep not in known:
                    raise WorkflowDomainError(f"stage {stage.id!r} depends on unknown stage {dep!r}")
                if order_of[dep] >= stage.order:
                    raise WorkflowDomainError(f"stage {stage.id!r} depends on a later stage")

        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise WorkflowDomainError("evidence ids must be unique")
        evidence_set = set(evidence_ids)
        citation_ids = [item.id for item in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise WorkflowDomainError("citation ids must be unique")
        for citation in self.citations:
            if citation.evidence_id not in evidence_set:
                raise WorkflowDomainError(
                    f"citation references unknown evidence id: {citation.evidence_id!r}"
                )

        artifact_ids = [item.id for item in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise WorkflowDomainError("artifact ids must be unique")
        artifact_set = set(artifact_ids)
        for artifact in self.artifacts:
            if artifact.stage_id not in known:
                raise WorkflowDomainError(
                    f"artifact {artifact.id!r} references unknown stage {artifact.stage_id!r}"
                )
        for stage in self.stages:
            for evidence_id in stage.evidence_ids:
                if evidence_id not in evidence_set:
                    raise WorkflowDomainError(
                        f"stage {stage.id!r} references unknown evidence id {evidence_id!r}"
                    )
            for artifact_id in stage.artifact_ids:
                if artifact_id not in artifact_set:
                    raise WorkflowDomainError(
                        f"stage {stage.id!r} references unknown artifact id {artifact_id!r}"
                    )

        if status is WorkflowStatus.COMPLETE:
            if self.error is not None:
                raise WorkflowDomainError("complete outcome cannot carry an error")
            if any(stage.status is not WorkflowStageStatus.COMPLETE for stage in self.stages):
                raise WorkflowDomainError("complete outcome requires all stages complete")
            if any(artifact.status is not ArtifactStatus.WRITTEN for artifact in self.artifacts):
                raise WorkflowDomainError("complete outcome requires all artifacts written")
        elif status is WorkflowStatus.DEGRADED:
            if self.error is not None:
                raise WorkflowDomainError("degraded outcome cannot carry an error")
            if any(stage.status is WorkflowStageStatus.CANCELLED for stage in self.stages):
                raise WorkflowDomainError("degraded outcome cannot contain cancelled stages")
            if not any(
                stage.status
                in (WorkflowStageStatus.DEGRADED, WorkflowStageStatus.FAILED)
                for stage in self.stages
            ) and not any(
                artifact.status is not ArtifactStatus.WRITTEN for artifact in self.artifacts
            ):
                raise WorkflowDomainError(
                    "degraded outcome requires a degraded/failed stage or a partial/failed artifact"
                )
        else:
            if self.error is None:
                raise WorkflowDomainError("failed outcome requires an error")


# ---------------------------------------------------------------------------
# Request and artifact sink
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactData:
    """Raw artifact payload handed to the persistence sink (never serialized)."""

    kind: str
    name: str
    media_type: str
    data: str


@dataclass(frozen=True)
class ArtifactWriteResult:
    """Typed persistence outcome: written, partial, or failed."""

    status: ArtifactStatus | str
    message: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, ArtifactStatus):
            status = self.status
        else:
            try:
                status = ArtifactStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise WorkflowDomainError(f"unknown artifact status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if not isinstance(self.message, str):
            raise WorkflowDomainError("ArtifactWriteResult.message must be a string")


ArtifactSink = Callable[[ArtifactData], ArtifactWriteResult]


def logical_artifact_sink(data: ArtifactData) -> ArtifactWriteResult:
    """Default sink: records the logical artifact without touching the filesystem."""
    return ArtifactWriteResult(ArtifactStatus.WRITTEN)


@dataclass(frozen=True)
class WorkflowRequest:
    """One validated workflow run request."""

    query: str
    plan: ResearchPlan
    max_fetch_concurrency: int = WORKFLOW_FETCH_CONCURRENCY
    artifact_sink: ArtifactSink = field(default_factory=lambda: logical_artifact_sink)
    request_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _nonblank_str(self.query, "WorkflowRequest.query"))
        if not isinstance(self.plan, ResearchPlan):
            raise WorkflowDomainError("WorkflowRequest.plan must be a ResearchPlan")
        validate_research_plan(self.plan)
        if type(self.max_fetch_concurrency) is not int or self.max_fetch_concurrency < 1:
            raise WorkflowDomainError("WorkflowRequest.max_fetch_concurrency must be a positive integer")
        if not callable(self.artifact_sink):
            raise WorkflowDomainError("WorkflowRequest.artifact_sink must be callable")
        if not isinstance(self.request_id, str):
            raise WorkflowDomainError("WorkflowRequest.request_id must be a string")


# ---------------------------------------------------------------------------
# URL dedupe
# ---------------------------------------------------------------------------


def workflow_url_dedupe_key(url: str) -> str:
    """Return a stable dedupe key for one fetch URL.

    Mirrors the neutral cache normalization: scheme/host lowercased, default
    ports and fragments dropped, query preserved. URLs with userinfo or
    sensitive query parameters fall back to their exact raw string so
    credentials never enter a normalized key.
    """
    raw = str(url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    # Sensitive URLs keep their exact raw string as the dedupe key; the
    # checks run before the schemeless fallback so ``relative?token=...``
    # inputs never canonicalize into a shared key.
    if parsed.username is not None or parsed.password is not None:
        return raw
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(is_sensitive_key(key) for key, _ in pairs):
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return raw
    try:
        port = parsed.port
    except ValueError:
        return raw
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if not port or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


# ---------------------------------------------------------------------------
# Owner helpers
# ---------------------------------------------------------------------------


def _input_str(op: ResearchPlanOperation, key: str, default: str = "") -> str:
    value = op.input.get(key)
    if value is None:
        return default
    return str(value).strip()


def _constraint_int(op: ResearchPlanOperation, key: str, default: int) -> int:
    value = op.constraints.get(key)
    if type(value) is int and value > 0:
        return value
    return default


def _stage_error_from(error: ExecutionError) -> WorkflowError:
    code = _STAGE_ERROR_CODE_MAP.get(str(error.type), WorkflowErrorCode.FETCH_FAILED)
    return WorkflowError(
        code=code,
        message=str(error.message) if str(error.message).strip() else f"{code.value.lower()}",
        retryable=False,
        details=dict(error.details or {}),
    )


def _result_count(outcome: Any) -> int:
    items = getattr(outcome, "evidence_items", None)
    if items:
        return len(items)
    candidates = getattr(outcome, "candidates", None)
    return len(candidates) if candidates else 0


def _project_attempts(
    attempts: Sequence[ExecutionAttempt], operation: str
) -> tuple[ExecutionAttempt, ...]:
    """Re-bind typed execution attempts to the workflow operation vocabulary.

    The typed Evidence owners record attempts under the shared execution
    capability taxonomy (``web_search``, ``docs_search``, ``web_fetch``,
    ``site_map``); the stable workflow contract accepts only workflow
    executable operations. Each attempt is therefore projected to the stage
    operation it belongs to, mirroring the V2 projection that re-binds
    attempts to the envelope operation. Attempts already carrying the stage
    operation are kept untouched.
    """
    return tuple(
        attempt if attempt.capability == operation else replace(attempt, capability=operation)
        for attempt in attempts
    )


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------


class _WorkflowRunner:
    """Internal scheduler owning stage order, bounded fetch, URL dedupe,
    evidence admission, gaps, and artifact bookkeeping for one run."""

    def __init__(self, request: WorkflowRequest, request_id: str) -> None:
        self.request = request
        self.request_id = request_id
        self.started = time.monotonic()
        self.semaphore = asyncio.Semaphore(request.max_fetch_concurrency)
        self.orders = {op.id: index for index, op in enumerate(request.plan.operations, 1)}
        self.completed: dict[str, WorkflowStage] = {}
        self.candidates: dict[str, list[ExecutionCandidate]] = {}
        self.evidence_by_stage: dict[str, list[ExecutionEvidenceItem]] = {}
        self.seen_evidence_ids: set[str] = set()
        self.evidence_output_remaining = WORKFLOW_EVIDENCE_OUTPUT_LIMIT
        self.attempts: list[ExecutionAttempt] = []
        self.gaps: list[ExecutionGap] = []
        self.artifacts: dict[str, WorkflowArtifact] = {}
        self.fetched_keys: set[str] = set()
        self.cancelled = False
        self.fatal_error: WorkflowError | None = None

    async def run(self) -> WorkflowOutcome:
        remaining = list(self.request.plan.operations)
        try:
            while remaining:
                ready = [op for op in remaining if set(op.depends_on).issubset(self.completed)]
                if not ready:
                    self.fatal_error = WorkflowError(
                        WorkflowErrorCode.INTERNAL_ERROR,
                        "plan dependency ordering is invalid",
                        False,
                    )
                    break
                for op in ready:
                    remaining.remove(op)
                batch = await asyncio.gather(
                    *(self._execute(op) for op in ready),
                    return_exceptions=True,
                )
                for op, result in zip(ready, batch):
                    if isinstance(result, asyncio.CancelledError):
                        self.cancelled = True
                        self.completed[op.id] = self._cancelled_stage(op)
                    elif isinstance(result, BaseException):
                        self.completed[op.id] = self._failed_stage(
                            op,
                            WorkflowErrorCode.INTERNAL_ERROR,
                            "stage failed unexpectedly",
                            gap=ExecutionGap(
                                "stage_failed",
                                "stage failed unexpectedly",
                                capability=op.operation,
                            ),
                        )
                    else:
                        self.completed[op.id] = result
        except asyncio.CancelledError:
            self.cancelled = True
        return self._finalize()

    async def _execute(self, op: ResearchPlanOperation) -> WorkflowStage:
        if op.operation in _DISCOVERY_OPERATIONS:
            return await self._run_discovery(op)
        if op.operation == "content_fetch":
            return await self._run_fetch(op)
        return self._failed_stage(
            op,
            WorkflowErrorCode.INTERNAL_ERROR,
            f"unsupported stage operation {op.operation!r}",
            gap=ExecutionGap(
                "stage_failed",
                f"unsupported stage operation {op.operation!r}",
                capability=op.operation,
            ),
        )

    async def _invoke_discovery(self, op: ResearchPlanOperation) -> Any:
        from .evidence_operations import (
            DocsDiscoveryRequest,
            SiteDiscoveryRequest,
            SourceDiscoveryRequest,
            docs_discovery,
            site_discovery,
            source_discovery,
        )

        if op.operation == "source_discovery":
            return await source_discovery(
                SourceDiscoveryRequest(
                    query=_input_str(op, "query") or _input_str(op, "resource") or self.request.query,
                    max_results=_constraint_int(op, "max_results", 5),
                )
            )
        if op.operation == "docs_discovery":
            return await docs_discovery(
                DocsDiscoveryRequest(
                    query=_input_str(op, "query") or _input_str(op, "resource") or self.request.query,
                    max_results=_constraint_int(op, "max_results", 5),
                )
            )
        if op.operation == "site_discovery":
            resource = _input_str(op, "resource") or _input_str(op, "query") or ""
            if not resource:
                raise WorkflowDomainError("site_discovery stage requires a resource")
            return await site_discovery(
                SiteDiscoveryRequest(
                    resource=resource,
                    instructions=_input_str(op, "instructions"),
                    max_depth=_constraint_int(op, "max_depth", 1),
                    max_breadth=_constraint_int(op, "max_breadth", 20),
                    limit=_constraint_int(op, "limit", 50),
                )
            )
        raise WorkflowDomainError(f"unsupported discovery operation {op.operation!r}")

    async def _run_discovery(self, op: ResearchPlanOperation) -> WorkflowStage:
        from .evidence_operations import EvidenceOperationStatus

        try:
            outcome = await self._invoke_discovery(op)
        except WorkflowDomainError as exc:
            return self._failed_stage(
                op,
                WorkflowErrorCode.INTERNAL_ERROR,
                str(exc),
                gap=ExecutionGap("stage_failed", str(exc), capability=op.operation),
            )
        self.attempts.extend(_project_attempts(outcome.attempts, op.operation))
        self.candidates[op.id] = list(outcome.candidates)
        if outcome.status is EvidenceOperationStatus.FAILED:
            self.gaps.append(
                ExecutionGap(
                    "stage_failed",
                    outcome.error.message if outcome.error is not None else f"{op.operation} failed",
                    capability=op.operation,
                )
            )
        return self._stage_from_outcome(op, outcome)

    def _resolve_fetch_resources(self, op: ResearchPlanOperation) -> list[str]:
        """Resolve the ordered, deduplicated URL list one fetch stage may fetch.

        Honors ``constraints.max_items`` exactly as the plan generated by
        ``research_service`` intends: a positive ``max_items`` caps how many
        candidate URLs are selected from the referenced discovery stages; a
        missing, zero, or non-integer value falls back to the default of one,
        matching the shared constraint helper semantics. A direct ``resource``
        wins and yields at most one URL. The first normalized key owns the
        fetch: a URL already fetched by an earlier stage is skipped and never
        refetched. Placeholder tokens (``<key-url>``) resolve to nothing so
        the stage becomes a no-op instead of fetching a literal placeholder.

        The five-item output allowance is reserved here, at selection time, so
        concurrent fetch stages never begin fetches beyond the remaining
        allowance. Every suppressed planned fetch keeps an explicit
        ``evidence_output_budget`` gap identifying the stage/resource.
        """
        resource = _input_str(op, "resource")
        if resource:
            if resource.startswith("<") and resource.endswith(">"):
                return []
            key = workflow_url_dedupe_key(resource)
            if key and key in self.fetched_keys:
                return []
            if self.evidence_output_remaining <= 0:
                self.gaps.append(
                    ExecutionGap(
                        "evidence_output_budget",
                        "evidence output budget reached; suppressed planned fetch",
                        capability="content_fetch",
                        resource=resource,
                    )
                )
                return []
            if key:
                self.fetched_keys.add(key)
            self.evidence_output_remaining -= 1
            return [resource]
        refs = op.input.get("candidate_refs") or ()
        if not isinstance(refs, (list, tuple)):
            return []
        max_items = _constraint_int(op, "max_items", 1)
        selected: list[str] = []
        suppressed: list[str] = []
        for ref in refs:
            for candidate in self.candidates.get(str(ref), []):
                if len(selected) >= max_items:
                    return selected
                url = str(candidate.resource or "").strip()
                if not url:
                    continue
                key = workflow_url_dedupe_key(url)
                if not key or key in self.fetched_keys:
                    continue
                if self.evidence_output_remaining <= 0:
                    suppressed.append(url)
                    continue
                self.fetched_keys.add(key)
                self.evidence_output_remaining -= 1
                selected.append(url)
        for url in suppressed:
            self.gaps.append(
                ExecutionGap(
                    "evidence_output_budget",
                    "evidence output budget reached; suppressed planned fetch",
                    capability="content_fetch",
                    resource=url,
                )
            )
        return selected

    async def _run_fetch(self, op: ResearchPlanOperation) -> WorkflowStage:
        from .evidence_operations import ContentFetchRequest, EvidenceOperationStatus, content_fetch

        resources = self._resolve_fetch_resources(op)
        if not resources:
            # No new fetch work (placeholder/missing resource, only
            # already-fetched URLs, or budget-suppressed) completes empty;
            # the gap list already records any ``evidence_output_budget`` cap.
            return self._empty_stage(op)

        async def fetch_one(resource: str) -> tuple[str, object]:
            async with self.semaphore:
                try:
                    return resource, await content_fetch(ContentFetchRequest(resource=resource))
                except Exception:  # noqa: BLE001 - classified below
                    return resource, WorkflowError(
                        WorkflowErrorCode.INTERNAL_ERROR,
                        "content_fetch stage failed unexpectedly",
                        False,
                    )

        fetched = await asyncio.gather(*(fetch_one(resource) for resource in resources))
        stage_evidence: list[ExecutionEvidenceItem] = []
        stage_artifacts: list[WorkflowArtifact] = []
        stage_status = WorkflowStageStatus.COMPLETE
        stage_error: WorkflowError | None = None
        stage_count = 0
        for resource, result in fetched:
            if isinstance(result, WorkflowError):
                self.gaps.append(
                    ExecutionGap(
                        "stage_failed",
                        result.message,
                        capability="content_fetch",
                        resource=resource,
                    )
                )
                stage_status = WorkflowStageStatus.FAILED
                if stage_error is None:
                    stage_error = result
                continue
            outcome = result
            self.attempts.extend(_project_attempts(outcome.attempts, op.operation))
            stage_count += _result_count(outcome)
            for item in outcome.evidence_items:
                if item.id in self.seen_evidence_ids:
                    continue
                self.seen_evidence_ids.add(item.id)
                stage_evidence.append(item)
                stage_artifacts.append(self._persist_evidence_artifact(op, item))
                key = workflow_url_dedupe_key(item.resource)
                if key:
                    self.fetched_keys.add(key)
            if outcome.status is EvidenceOperationStatus.FAILED:
                self.gaps.append(
                    ExecutionGap(
                        "stage_failed",
                        outcome.error.message if outcome.error is not None else "content_fetch failed",
                        capability="content_fetch",
                        resource=resource,
                    )
                )
                stage_status = WorkflowStageStatus.FAILED
                if stage_error is None and outcome.error is not None:
                    stage_error = _stage_error_from(outcome.error)
            elif (
                outcome.status is EvidenceOperationStatus.DEGRADED
                and stage_status is WorkflowStageStatus.COMPLETE
            ):
                stage_status = WorkflowStageStatus.DEGRADED
        if stage_evidence:
            self.evidence_by_stage.setdefault(op.id, []).extend(stage_evidence)
        return WorkflowStage(
            id=op.id,
            operation=op.operation,
            status=stage_status,
            order=self.orders[op.id],
            input=dict(op.input),
            depends_on=tuple(op.depends_on),
            result_count=stage_count,
            evidence_ids=tuple(item.id for item in stage_evidence),
            artifact_ids=tuple(artifact.id for artifact in stage_artifacts),
            error=stage_error,
        )

    def _empty_stage(self, op: ResearchPlanOperation) -> WorkflowStage:
        """A fetch stage with no new work completes empty (never fails)."""
        return WorkflowStage(
            id=op.id,
            operation=op.operation,
            status=WorkflowStageStatus.COMPLETE,
            order=self.orders[op.id],
            input=dict(op.input),
            depends_on=tuple(op.depends_on),
            result_count=0,
        )

    def _persist_evidence_artifact(
        self, op: ResearchPlanOperation, item: ExecutionEvidenceItem
    ) -> WorkflowArtifact:
        name = f"{_safe_segment(op.id)}-{_safe_segment(item.id)}.md"
        media_type = "text/markdown"
        payload = item.content
        encoded = payload.encode("utf-8")
        result = self.request.artifact_sink(
            ArtifactData(kind="evidence", name=name, media_type=media_type, data=payload)
        )
        artifact = WorkflowArtifact(
            id=_stable_id("artifact", op.id, item.id),
            stage_id=op.id,
            kind="evidence",
            status=result.status,
            name=name,
            media_type=media_type,
            byte_length=len(encoded),
            digest=hashlib.sha256(encoded).hexdigest(),
        )
        self.artifacts[artifact.id] = artifact
        if result.status is not ArtifactStatus.WRITTEN:
            self.gaps.append(
                ExecutionGap(
                    "artifact_write_failed",
                    result.message or f"artifact write {result.status.value}",
                    capability=op.operation,
                    resource=name,
                )
            )
        return artifact

    def _stage_from_outcome(
        self,
        op: ResearchPlanOperation,
        outcome: Any,
        *,
        evidence_ids: Sequence[str] = (),
        artifact_ids: Sequence[str] = (),
    ) -> WorkflowStage:
        status_value = outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status)
        if status_value == "complete":
            stage_status = WorkflowStageStatus.COMPLETE
        elif status_value == "degraded":
            stage_status = WorkflowStageStatus.DEGRADED
        else:
            stage_status = WorkflowStageStatus.FAILED
        error = None
        if stage_status is WorkflowStageStatus.FAILED and outcome.error is not None:
            error = _stage_error_from(outcome.error)
        return WorkflowStage(
            id=op.id,
            operation=op.operation,
            status=stage_status,
            order=self.orders[op.id],
            input=dict(op.input),
            depends_on=tuple(op.depends_on),
            result_count=_result_count(outcome),
            evidence_ids=tuple(evidence_ids),
            artifact_ids=tuple(artifact_ids),
            error=error,
        )

    def _cancelled_stage(self, op: ResearchPlanOperation) -> WorkflowStage:
        return WorkflowStage(
            id=op.id,
            operation=op.operation,
            status=WorkflowStageStatus.CANCELLED,
            order=self.orders[op.id],
            input=dict(op.input),
            depends_on=tuple(op.depends_on),
            error=WorkflowError(WorkflowErrorCode.CANCELLED, "stage cancelled", False),
        )

    def _failed_stage(
        self,
        op: ResearchPlanOperation,
        code: WorkflowErrorCode,
        message: str,
        *,
        gap: ExecutionGap | None = None,
    ) -> WorkflowStage:
        if gap is not None:
            self.gaps.append(gap)
        return WorkflowStage(
            id=op.id,
            operation=op.operation,
            status=WorkflowStageStatus.FAILED,
            order=self.orders[op.id],
            input=dict(op.input),
            depends_on=tuple(op.depends_on),
            error=WorkflowError(code, message, False),
        )

    def _terminal(self) -> tuple[WorkflowStatus, WorkflowError | None]:
        stages = [self.completed.get(op.id) for op in self.request.plan.operations]
        if self.cancelled or any(
            stage is not None and stage.status is WorkflowStageStatus.CANCELLED for stage in stages
        ):
            return (
                WorkflowStatus.FAILED,
                WorkflowError(WorkflowErrorCode.CANCELLED, "research workflow cancelled", False),
            )
        if self.fatal_error is not None:
            return WorkflowStatus.FAILED, self.fatal_error
        evidence_items = [item for items in self.evidence_by_stage.values() for item in items]
        stage_failed = any(
            stage is not None and stage.status is WorkflowStageStatus.FAILED for stage in stages
        )
        if not evidence_items:
            if stage_failed:
                # Preserve the classified stage error identity so a
                # configuration failure stays CONFIGURATION_ERROR (exit 3)
                # instead of being flattened to FETCH_FAILED (exit 4). The
                # terminal error is the first failed stage's classified error
                # (code, message, redaction details), never a synthetic code.
                failed_stage = next(
                    (
                        stage
                        for stage in stages
                        if stage is not None and stage.status is WorkflowStageStatus.FAILED
                    ),
                    None,
                )
                if failed_stage is not None and failed_stage.error is not None:
                    return WorkflowStatus.FAILED, failed_stage.error
            code = (
                WorkflowErrorCode.FETCH_FAILED
                if stage_failed
                else WorkflowErrorCode.INSUFFICIENT_EVIDENCE
            )
            return WorkflowStatus.FAILED, WorkflowError(
                code, "research produced no admitted evidence", False
            )
        if stage_failed or any(
            stage is not None and stage.status is WorkflowStageStatus.DEGRADED for stage in stages
        ):
            return WorkflowStatus.DEGRADED, None
        if any(artifact.status is not ArtifactStatus.WRITTEN for artifact in self.artifacts.values()):
            return WorkflowStatus.DEGRADED, None
        return WorkflowStatus.COMPLETE, None

    def _finalize(self) -> WorkflowOutcome:
        stages: list[WorkflowStage] = []
        for op in self.request.plan.operations:
            stage = self.completed.get(op.id)
            if stage is None:
                stage = self._failed_stage(
                    op,
                    WorkflowErrorCode.INTERNAL_ERROR,
                    "stage did not run",
                    gap=ExecutionGap("stage_failed", "stage did not run", capability=op.operation),
                )
            stages.append(stage)
        evidence_items: list[ExecutionEvidenceItem] = []
        for op in self.request.plan.operations:
            evidence_items.extend(self.evidence_by_stage.get(op.id, []))
        artifacts = sorted(
            self.artifacts.values(), key=lambda item: (self.orders.get(item.stage_id, 0), item.id)
        )
        citations = [
            ExecutionCitation(
                id=_stable_id("cite", item.id),
                evidence_id=item.id,
                label=item.title or item.resource,
            )
            for item in evidence_items
        ]
        status, error = self._terminal()
        return WorkflowOutcome(
            status=status,
            plan=self.request.plan,
            stages=tuple(stages),
            evidence=tuple(evidence_items),
            citations=tuple(citations),
            gaps=tuple(self.gaps),
            attempts=tuple(self.attempts),
            artifacts=tuple(artifacts),
            error=error,
            meta=WorkflowMeta(self.request_id, (time.monotonic() - self.started) * 1000),
        )


async def run_research_workflow(request: WorkflowRequest) -> WorkflowOutcome:
    """Run one strict research workflow over the typed Evidence owners.

    The owner schedules plan operations in dependency order, executes
    discovery stages and bounded concurrent fetch stages, deduplicates
    normalized URLs, admits only typed evidence, records workflow gaps and
    safe logical artifact records, and never synthesizes an answer or emits
    shell/output-path projections.
    """
    if not isinstance(request, WorkflowRequest):
        raise WorkflowDomainError("run_research_workflow requires a WorkflowRequest")
    rid = request.request_id.strip() or f"workflow-{uuid.uuid4().hex[:12]}"
    runner = _WorkflowRunner(request, rid)
    return await runner.run()


__all__ = [
    "ArtifactData",
    "ArtifactSink",
    "ArtifactStatus",
    "ArtifactWriteResult",
    "EXIT_CONFIGURATION",
    "EXIT_DEGRADED",
    "EXIT_INTERNAL",
    "EXIT_INVALID_ARGUMENT",
    "EXIT_SUCCESS",
    "EXIT_UPSTREAM",
    "WORKFLOW_ERROR_EXIT_CODES",
    "WORKFLOW_ERROR_REGISTRY",
    "WORKFLOW_ERROR_RETRYABILITY",
    "WORKFLOW_EXECUTABLE_OPERATIONS",
    "WORKFLOW_FETCH_CONCURRENCY",
    "WORKFLOW_EVIDENCE_OUTPUT_LIMIT",
    "WorkflowArtifact",
    "WorkflowDomainError",
    "WorkflowError",
    "WorkflowErrorCode",
    "WorkflowMeta",
    "WorkflowOutcome",
    "WorkflowRequest",
    "WorkflowStage",
    "WorkflowStageStatus",
    "WorkflowStatus",
    "logical_artifact_sink",
    "run_research_workflow",
    "validate_artifact_name",
    "workflow_url_dedupe_key",
]