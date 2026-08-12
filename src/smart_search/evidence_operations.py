"""Schema-neutral typed Evidence operation owners.

This module is the single semantic authority for Evidence operations:
admission, provenance, operation status, classified error/degradation, local
capability status inspection, and deterministic source/docs composition. It
consumes typed ``ExecutionOutcome`` values from the neutral same-capability
runtime and returns immutable ``EvidenceOperationOutcome`` values that the V2
projection boundary (``canonical_operations``) maps one-way into the strict V2
envelope.

The module must stay schema-neutral: it must not import CLI, V1/V2/V3/Workflow
contracts, renderers, the broad ``service`` facade, legacy result projection,
or Provider adapter modules directly.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from .capability_service import (
    _provider_status_for_capability,
    get_capability_status,
)
from .capability_taxonomy import (
    is_content_fetch_success,
    is_docs_discovery_candidate,
    is_provider_qualified,
    is_structured_discovery_candidate,
    iter_v2_descriptors,
    v2_availability_by_tier,
)
from .config import ConfigStorageError, ModelRoutesConfigurationError
from .execution_primitives import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCandidate,
    ExecutionCitation,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionGap,
    ExecutionMetadata,
)
from .intent_router import project_evidence_routing
from .operation_runtime import (
    _execute_docs_search,
    _execute_site_map,
    _execute_web_fetch,
    _execute_web_search,
)
from .runtime_cache import observe_command

# Stable Evidence capability operation ids (schema-neutral domain vocabulary).
EVIDENCE_CAPABILITY_OPERATIONS = frozenset(
    {
        "source_discovery",
        "docs_discovery",
        "content_fetch",
        "site_discovery",
    }
)
# Envelope-only local capability-status operation id.
EVIDENCE_OPERATIONS = frozenset(EVIDENCE_CAPABILITY_OPERATIONS | {"capability_status"})

# v2 operation -> legacy execution capability.
_V2_TO_V1_CAPABILITY: Mapping[str, str] = {
    "source_discovery": "web_search",
    "docs_discovery": "docs_search",
    "content_fetch": "web_fetch",
    "site_discovery": "site_map",
    "answer_synthesis": "main_search",
}


class CanonicalOperationError(ValueError):
    """Raised for invalid canonical request construction."""


def _nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanonicalOperationError(f"{name} must be a non-blank string")
    return value.strip()


def _positive_int(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if type(value) is bool or type(value) is not int or value < 1:
        raise CanonicalOperationError(f"{name} must be a positive integer")
    return value


# ---------------------------------------------------------------------------
# Typed request models (domain requests, not V2 envelope models)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceDiscoveryRequest:
    query: str
    max_results: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _nonblank(self.query, "query"))
        object.__setattr__(self, "max_results", _positive_int(self.max_results, "max_results", 5))


@dataclass(frozen=True)
class DocsDiscoveryRequest:
    query: str
    max_results: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _nonblank(self.query, "query"))
        object.__setattr__(self, "max_results", _positive_int(self.max_results, "max_results", 5))


@dataclass(frozen=True)
class ContentFetchRequest:
    resource: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource", _nonblank(self.resource, "resource"))


@dataclass(frozen=True)
class SiteDiscoveryRequest:
    resource: str
    instructions: str = ""
    max_depth: int = 1
    max_breadth: int = 20
    limit: int = 50

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource", _nonblank(self.resource, "resource"))
        if not isinstance(self.instructions, str):
            raise CanonicalOperationError("instructions must be a string")
        object.__setattr__(self, "max_depth", _positive_int(self.max_depth, "max_depth", 1))
        object.__setattr__(self, "max_breadth", _positive_int(self.max_breadth, "max_breadth", 20))
        object.__setattr__(self, "limit", _positive_int(self.limit, "limit", 50))


# ---------------------------------------------------------------------------
# Typed domain models
# ---------------------------------------------------------------------------


class EvidenceOperationStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class EvidenceDegradation:
    """One stable degradation entry owned by the Evidence operation."""

    code: str
    operation: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _nonblank(self.code, "EvidenceDegradation.code"))
        object.__setattr__(self, "operation", _nonblank(self.operation, "EvidenceDegradation.operation"))
        object.__setattr__(self, "message", _nonblank(self.message, "EvidenceDegradation.message"))


@dataclass(frozen=True)
class EvidenceRouting:
    """Requested/executed operation routing facts for one Evidence operation."""

    requested_operations: tuple[str, ...] = ()
    executed_operations: tuple[str, ...] = ()
    policy_version: str = "v2"
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("requested_operations", "executed_operations", "reason_codes"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                value = tuple(value)
            for item in value:
                if not isinstance(item, str) or not item:
                    raise ValueError(f"EvidenceRouting.{name} must contain only non-blank strings")
            object.__setattr__(self, name, value)
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ValueError("EvidenceRouting.policy_version must be a non-blank string")


def _is_finite_number(value: Any) -> bool:
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


@dataclass(frozen=True)
class EvidenceOperationOutcome:
    """Immutable typed outcome of one Evidence operation.

    Invariants enforced at construction:

    - ``operation`` is one of the stable Evidence operation ids or the
      envelope-only ``capability_status``;
    - requested/executed routing contains only stable capability ids and
      executed is a unique subset of requested;
    - ``complete`` has no error/degradation and no error/skipped attempt;
    - ``degraded`` has no error and non-empty degradation;
    - ``failed`` has one classified error and empty degradation;
    - source/docs/site outcomes contain candidates only; ``content_fetch``
      contains evidence items only; citations reference only evidence ids;
      ids are unique and candidate/item sets are disjoint;
    - ``capability_status`` is local-only with empty evidence/routing
      capability arrays and its result lives in frozen ``local_data``;
    - all nested collections are tuples/read-only mappings and all JSON values
      are finite and JSON-safe.
    """

    operation: str
    status: EvidenceOperationStatus | str
    candidates: tuple[ExecutionCandidate, ...] = ()
    evidence_items: tuple[ExecutionEvidenceItem, ...] = ()
    citations: tuple[ExecutionCitation, ...] = ()
    gaps: tuple[ExecutionGap, ...] = ()
    attempts: tuple[ExecutionAttempt, ...] = ()
    degradation: tuple[EvidenceDegradation, ...] = ()
    error: ExecutionError | None = None
    routing: EvidenceRouting = field(default_factory=EvidenceRouting)
    metadata: ExecutionMetadata = field(default_factory=lambda: ExecutionMetadata("request"))
    local_data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.operation not in EVIDENCE_OPERATIONS:
            raise ValueError(f"unknown evidence operation: {self.operation!r}")
        if isinstance(self.status, EvidenceOperationStatus):
            status = self.status
        else:
            try:
                status = EvidenceOperationStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown evidence operation status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        for name, expected in (
            ("candidates", ExecutionCandidate),
            ("evidence_items", ExecutionEvidenceItem),
            ("citations", ExecutionCitation),
            ("gaps", ExecutionGap),
            ("attempts", ExecutionAttempt),
            ("degradation", EvidenceDegradation),
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                value = tuple(value)
            for item in value:
                if not isinstance(item, expected):
                    raise ValueError(
                        f"EvidenceOperationOutcome.{name} must contain only {expected.__name__} values"
                    )
            object.__setattr__(self, name, value)
        if self.error is not None and not isinstance(self.error, ExecutionError):
            raise ValueError("EvidenceOperationOutcome.error must be an ExecutionError or None")
        if not isinstance(self.routing, EvidenceRouting):
            raise ValueError("EvidenceOperationOutcome.routing must be an EvidenceRouting")
        if not isinstance(self.metadata, ExecutionMetadata):
            raise ValueError("EvidenceOperationOutcome.metadata must be an ExecutionMetadata")
        for name in ("requested_operations", "executed_operations"):
            for operation in getattr(self.routing, name):
                if operation not in EVIDENCE_CAPABILITY_OPERATIONS:
                    raise ValueError(
                        f"EvidenceOperationOutcome.routing.{name} contains unknown operation: {operation!r}"
                    )
        if not set(self.routing.executed_operations).issubset(set(self.routing.requested_operations)):
            raise ValueError(
                "EvidenceOperationOutcome.routing.executed_operations must be a subset of requested"
            )
        ids = [item.id for item in self.candidates] + [item.id for item in self.evidence_items]
        if len(ids) != len(set(ids)):
            raise ValueError("EvidenceOperationOutcome ids must be unique")
        evidence_ids = {item.id for item in self.evidence_items}
        for citation in self.citations:
            if citation.evidence_id not in evidence_ids:
                raise ValueError("EvidenceOperationOutcome citation references an unknown evidence id")
        if self.operation in ("source_discovery", "docs_discovery", "site_discovery"):
            if self.evidence_items:
                raise ValueError(f"{self.operation} must contain candidates only")
        elif self.operation == "content_fetch":
            if self.candidates:
                raise ValueError("content_fetch must contain evidence items only")
        elif self.operation == "capability_status":
            if self.candidates or self.evidence_items or self.citations or self.gaps or self.attempts or self.degradation:
                raise ValueError("capability_status must be local-only")
            if self.routing.requested_operations or self.routing.executed_operations:
                raise ValueError("capability_status routing must be empty")
        if status is EvidenceOperationStatus.COMPLETE:
            if self.error is not None or self.degradation:
                raise ValueError("complete outcome cannot carry error or degradation")
            if any(
                item.status in (ExecutionAttemptStatus.ERROR, ExecutionAttemptStatus.SKIPPED)
                for item in self.attempts
            ):
                raise ValueError("complete outcome cannot contain error/skipped attempts")
        elif status is EvidenceOperationStatus.DEGRADED:
            if self.error is not None:
                raise ValueError("degraded outcome cannot carry an error")
            if not self.degradation:
                raise ValueError("degraded outcome requires non-empty degradation")
        else:
            if self.error is None:
                raise ValueError("failed outcome requires an error")
            if self.degradation:
                raise ValueError("failed outcome cannot carry degradation")
        object.__setattr__(self, "local_data", _freeze_json(self.local_data, "outcome.local_data"))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _request_id() -> str:
    return f"v2-{uuid.uuid4().hex[:12]}"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    safe_prefix = re.sub(r"[^a-z0-9_-]+", "-", prefix.lower()).strip("-") or "id"
    return f"{safe_prefix}-{digest}"


def _qualified_providers(v2_operation: str) -> list[str]:
    """Providers that are both legacy-eligible and independently v2-qualified."""
    v1_capability = _V2_TO_V1_CAPABILITY[v2_operation]
    statuses = _provider_status_for_capability(v1_capability)
    providers: list[str] = []
    for item in statuses:
        provider = str(item.get("provider") or "")
        if not provider:
            continue
        if not item.get("eligible"):
            continue
        if not is_provider_qualified(provider, v2_operation):
            continue
        providers.append(provider)
    return providers


def _normalize_candidate(
    item: Mapping[str, Any], *, operation: str, index: int
) -> ExecutionCandidate | None:
    """Allowlisted one-time normalization of a raw runner mapping into a candidate.

    Applies the taxonomy admission predicate exactly once, keeps only the
    stable identity fields, bounds the snippet at the current 500-character
    projection, and constructs a deterministic stable id. Unknown raw keys and
    raw Provider payload fields never enter the typed outcome.
    """
    if operation == "docs_discovery":
        if not is_docs_discovery_candidate(item):
            return None
    elif not is_structured_discovery_candidate(item):
        return None
    resource = str(item.get("url") or item.get("id") or "").strip()
    provider = str(item.get("provider") or "").strip()
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("description") or item.get("snippet") or item.get("content") or "").strip()
    if not resource or not provider:
        return None
    if not title and not snippet:
        title = resource
    return ExecutionCandidate(
        id=_stable_id(operation, resource, provider, str(index)),
        resource=resource,
        provider=provider,
        title=title,
        snippet=snippet[:500],
    )


def _normalize_evidence(item: Mapping[str, Any], *, index: int) -> ExecutionEvidenceItem | None:
    """Allowlisted one-time normalization of a raw fetch mapping into evidence.

    Admitted only when resource, Provider provenance, and a non-blank fetched
    or read body pass the taxonomy predicate. Challenge pages, classified
    failures, missing provenance, and blank bodies never enter evidence.
    """
    if not is_content_fetch_success(item):
        return None
    resource = str(item.get("url") or "").strip()
    provider = str(item.get("provider") or "").strip()
    content = str(item.get("content") or item.get("raw_content") or "").strip()
    title = str(item.get("title") or resource).strip()
    return ExecutionEvidenceItem(
        id=_stable_id("evidence", resource, provider, str(index)),
        resource=resource,
        provider=provider,
        title=title,
        content=content,
    )


def _merge_candidates(*groups: Sequence[ExecutionCandidate]) -> list[ExecutionCandidate]:
    merged: list[ExecutionCandidate] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.resource.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _config_failed_outcome(
    *,
    operation: str,
    message: str,
    request_id: str,
    duration_ms: int,
    details: Mapping[str, Any] | None = None,
) -> EvidenceOperationOutcome:
    """Pre-execution configuration failure with zero attempts and zero network."""
    return EvidenceOperationOutcome(
        operation=operation,
        status=EvidenceOperationStatus.FAILED,
        error=ExecutionError("config_error", message, retryable=False, details=dict(details or {})),
        routing=EvidenceRouting((operation,), (), "v2", ("configuration_error",)),
        metadata=ExecutionMetadata(request_id, duration_ms),
    )


def _derive_discovery_outcome(
    *,
    operation: str,
    candidates: Sequence[ExecutionCandidate],
    attempts: Sequence[ExecutionAttempt],
    request_id: str,
    duration_ms: int,
    reason_codes: Sequence[str],
    config_message: str,
    config_details: Mapping[str, Any] | None = None,
) -> EvidenceOperationOutcome:
    """Exact discovery/site state machine from typed attempts and candidates.

    Normal empty with no failed attempt is complete; usable candidates plus a
    failed attempt is degraded; no usable output plus a failed attempt is
    failed with the last classified terminal error; no attempts after an
    eligible execution path remains the current configuration failure.
    """
    executed = (operation,) if attempts else ()
    has_error = any(
        item.status in (ExecutionAttemptStatus.ERROR, ExecutionAttemptStatus.SKIPPED)
        for item in attempts
    )
    has_success_or_empty = any(
        item.status in (ExecutionAttemptStatus.OK, ExecutionAttemptStatus.EMPTY)
        for item in attempts
    )
    usable = bool(candidates)

    if not attempts:
        return _config_failed_outcome(
            operation=operation,
            message=config_message,
            request_id=request_id,
            duration_ms=duration_ms,
            details=config_details,
        )
    if usable and has_error:
        return EvidenceOperationOutcome(
            operation=operation,
            status=EvidenceOperationStatus.DEGRADED,
            candidates=tuple(candidates),
            attempts=tuple(attempts),
            degradation=(
                EvidenceDegradation(
                    "provider_partial_failure",
                    operation,
                    "One or more providers failed before a usable result",
                ),
            ),
            routing=EvidenceRouting((operation,), executed, "v2", tuple(reason_codes)),
            metadata=ExecutionMetadata(request_id, duration_ms),
        )
    if usable or (has_success_or_empty and not has_error):
        return EvidenceOperationOutcome(
            operation=operation,
            status=EvidenceOperationStatus.COMPLETE,
            candidates=tuple(candidates),
            attempts=tuple(attempts),
            routing=EvidenceRouting((operation,), executed, "v2", tuple(reason_codes)),
            metadata=ExecutionMetadata(request_id, duration_ms),
        )
    last_error = next(
        (
            item
            for item in reversed(attempts)
            if item.status in (ExecutionAttemptStatus.ERROR, ExecutionAttemptStatus.SKIPPED)
        ),
        None,
    )
    error_type = "provider_error" if last_error is None or last_error.error is None else last_error.error.type
    return EvidenceOperationOutcome(
        operation=operation,
        status=EvidenceOperationStatus.FAILED,
        candidates=(),
        attempts=tuple(attempts),
        error=ExecutionError(error_type, f"{operation} failed", retryable=None),
        routing=EvidenceRouting((operation,), executed, "v2", tuple(reason_codes)),
        metadata=ExecutionMetadata(request_id, duration_ms),
    )


# ---------------------------------------------------------------------------
# Individual Evidence operation owners
# ---------------------------------------------------------------------------


@observe_command
async def source_discovery(request: SourceDiscoveryRequest) -> EvidenceOperationOutcome:
    """Structured source discovery: candidates only, never citations or evidence."""
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, SourceDiscoveryRequest):
        request = SourceDiscoveryRequest(
            query=getattr(request, "query", ""),
            max_results=getattr(request, "max_results", 5),
        )
    providers = _qualified_providers("source_discovery")
    if not providers:
        return _config_failed_outcome(
            operation="source_discovery",
            message="No qualified source_discovery providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            details={"qualified_providers": []},
        )
    execution = await _execute_web_search(
        request.query,
        count=request.max_results,
        providers=",".join(providers),
        fallback="auto",
    )
    allowed = set(providers)
    attempts = tuple(item for item in execution.attempts if item.provider in allowed)
    candidates: list[ExecutionCandidate] = []
    for index, item in enumerate(execution.value or []):
        if isinstance(item, Mapping):
            candidate = _normalize_candidate(item, operation="source_discovery", index=index)
            if candidate:
                candidates.append(candidate)
    return _derive_discovery_outcome(
        operation="source_discovery",
        candidates=candidates,
        attempts=attempts,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason_codes=("source_discovery",),
        config_message="No qualified source_discovery providers configured",
        config_details={"qualified_providers": []},
    )


@observe_command
async def docs_discovery(request: DocsDiscoveryRequest) -> EvidenceOperationOutcome:
    """Docs/API/library discovery: candidates only, docs max-results applied here."""
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, DocsDiscoveryRequest):
        request = DocsDiscoveryRequest(
            query=getattr(request, "query", ""),
            max_results=getattr(request, "max_results", 5),
        )
    providers = _qualified_providers("docs_discovery")
    if not providers:
        return _config_failed_outcome(
            operation="docs_discovery",
            message="No qualified docs_discovery providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            details={"qualified_providers": []},
        )
    execution = await _execute_docs_search(
        request.query,
        count=request.max_results,
        providers=",".join(providers),
        fallback="auto",
    )
    allowed = set(providers)
    attempts = tuple(item for item in execution.attempts if item.provider in allowed)
    candidates: list[ExecutionCandidate] = []
    for index, item in enumerate((execution.value or [])[: request.max_results]):
        if isinstance(item, Mapping):
            candidate = _normalize_candidate(item, operation="docs_discovery", index=index)
            if candidate:
                candidates.append(candidate)
    return _derive_discovery_outcome(
        operation="docs_discovery",
        candidates=candidates,
        attempts=attempts,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason_codes=("docs_discovery",),
        config_message="No qualified docs_discovery providers configured",
        config_details={"qualified_providers": []},
    )


@observe_command
async def content_fetch(request: ContentFetchRequest) -> EvidenceOperationOutcome:
    """Known-resource content fetch: evidence items only after admission."""
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, ContentFetchRequest):
        request = ContentFetchRequest(resource=getattr(request, "resource", ""))
    providers = _qualified_providers("content_fetch")
    if not providers:
        return _config_failed_outcome(
            operation="content_fetch",
            message="No qualified content_fetch providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    execution = await _execute_web_fetch(
        request.resource,
        fallback="auto",
        preferred_order=providers,
        providers=providers,
    )
    allowed = set(providers)
    attempts = tuple(item for item in execution.attempts if item.provider in allowed)
    items: list[ExecutionEvidenceItem] = []
    if isinstance(execution.value, Mapping):
        evidence_item = _normalize_evidence(execution.value, index=0)
        if evidence_item:
            items.append(evidence_item)

    executed = ("content_fetch",) if attempts else ()
    routing = EvidenceRouting(("content_fetch",), executed, "v2", ("content_fetch",))
    metadata = ExecutionMetadata(request_id, int((time.monotonic() - started) * 1000))
    has_error = any(
        item.status in (ExecutionAttemptStatus.ERROR, ExecutionAttemptStatus.SKIPPED)
        for item in attempts
    )
    if items and has_error:
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.DEGRADED,
            evidence_items=tuple(items),
            attempts=tuple(attempts),
            degradation=(
                EvidenceDegradation(
                    "provider_partial_failure",
                    "content_fetch",
                    "Fetch succeeded after provider failures",
                ),
            ),
            routing=routing,
            metadata=metadata,
        )
    if items:
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=tuple(items),
            attempts=tuple(attempts),
            routing=routing,
            metadata=metadata,
        )
    last_error = next(
        (
            item
            for item in reversed(attempts)
            if item.status in (ExecutionAttemptStatus.ERROR, ExecutionAttemptStatus.SKIPPED)
        ),
        None,
    )
    error_type = "fetch_error" if last_error is None or last_error.error is None else last_error.error.type
    return EvidenceOperationOutcome(
        operation="content_fetch",
        status=EvidenceOperationStatus.FAILED,
        evidence_items=(),
        attempts=tuple(attempts),
        error=ExecutionError(error_type, "content_fetch failed", retryable=None),
        routing=routing,
        metadata=metadata,
    )


@observe_command
async def site_discovery(request: SiteDiscoveryRequest) -> EvidenceOperationOutcome:
    """Advanced site-structure discovery: candidates only from site-map results."""
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, SiteDiscoveryRequest):
        request = SiteDiscoveryRequest(resource=getattr(request, "resource", ""))
    providers = _qualified_providers("site_discovery")
    if not providers:
        return _config_failed_outcome(
            operation="site_discovery",
            message="No qualified site_discovery providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    execution = await _execute_site_map(
        request.resource,
        instructions=request.instructions,
        max_depth=request.max_depth,
        max_breadth=request.max_breadth,
        limit=request.limit,
    )
    attempts = tuple(execution.attempts)
    candidates: list[ExecutionCandidate] = []
    results: list[Any] = []
    if isinstance(execution.value, Mapping):
        raw_results = execution.value.get("results") or []
        if isinstance(raw_results, list):
            results = raw_results
    for index, raw in enumerate(results):
        if isinstance(raw, str):
            item: dict[str, Any] = {"url": raw, "title": raw, "provider": "tavily"}
        elif isinstance(raw, Mapping):
            item = {
                "url": raw.get("url") or raw.get("link") or "",
                "title": raw.get("title") or raw.get("url") or "",
                "description": raw.get("description") or "",
                "provider": raw.get("provider") or "tavily",
            }
        else:
            continue
        candidate = _normalize_candidate(item, operation="site_discovery", index=index)
        if candidate:
            candidates.append(candidate)
    return _derive_discovery_outcome(
        operation="site_discovery",
        candidates=candidates,
        attempts=attempts,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason_codes=("site_discovery",),
        config_message="No qualified site_discovery providers configured",
    )


def _project_legacy_status(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project one legacy capability-status entry into configured/eligible/ok.

    Prefer per-provider status rows when present: ``configured`` lists every
    provider with configuration and ``eligible`` only the providers that are
    both configured and enabled. Entries without ``provider_status`` fall back
    to the legacy ``configured`` list for both fields.
    """
    provider_status = value.get("provider_status")
    if isinstance(provider_status, Sequence):
        configured = [
            str(item.get("provider"))
            for item in provider_status
            if isinstance(item, Mapping) and item.get("configured")
        ]
        eligible = [
            str(item.get("provider"))
            for item in provider_status
            if isinstance(item, Mapping) and item.get("eligible")
        ]
        return {
            "configured": configured,
            "eligible": eligible,
            "ok": value.get("ok"),
        }
    configured = value.get("configured", [])
    return {
        "configured": configured,
        "eligible": configured,
        "ok": value.get("ok"),
    }


def capability_status(*, request_id: str | None = None) -> EvidenceOperationOutcome:
    """Local zero-network capability status inspection.

    Reads one capability-status snapshot plus taxonomy descriptors and
    qualification metadata. Runtime availability and static qualification stay
    separate. Known config exceptions become a fixed ``config_error``;
    unexpected exceptions become a fixed ``internal_error``. No Provider
    client, probe, transport, or filesystem mutation is ever created.
    """
    started = time.monotonic()
    rid = request_id or _request_id()
    try:
        legacy_status = get_capability_status()
        descriptors = list(iter_v2_descriptors())
        available_providers = {
            str(item.get("id")): _qualified_providers(str(item.get("id")))
            for item in descriptors
        }
        availability_by_tier: dict[str, dict[str, list[str]]] = {
            "core": {},
            "advanced": {},
            "optional_extension": {},
        }
        for item in descriptors:
            capability = str(item.get("id") or "")
            stability = str(item.get("stability") or "")
            tier = str(item.get("tier") or "")
            bucket = "optional_extension" if stability == "optional_extension" else tier
            availability_by_tier.setdefault(bucket, {})[capability] = available_providers.get(
                capability, []
            )
        core = dict(availability_by_tier.get("core", {}))
        qualification_by_tier = v2_availability_by_tier()
        result = {
            "capabilities": {
                "core_availability": core,
                "availability_by_tier": availability_by_tier,
                "qualification_by_tier": qualification_by_tier,
                "descriptors": [
                    {
                        "id": item.get("id"),
                        "tier": item.get("tier"),
                        "stability": item.get("stability"),
                    }
                    for item in descriptors
                ],
                "legacy_status": {
                    key: _project_legacy_status(value)
                    for key, value in legacy_status.items()
                    if isinstance(value, Mapping)
                },
            }
        }
        return EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.COMPLETE,
            routing=EvidenceRouting((), (), "v2-capability-status-1", ("local_inspection",)),
            metadata=ExecutionMetadata(rid, int((time.monotonic() - started) * 1000)),
            local_data=result,
        )
    except (ConfigStorageError, ModelRoutesConfigurationError):
        return EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError(
                "config_error",
                "capability_status configuration is invalid",
                retryable=False,
            ),
            routing=EvidenceRouting((), (), "v2-capability-status-1", ()),
            metadata=ExecutionMetadata(rid, int((time.monotonic() - started) * 1000)),
        )
    except Exception:  # pragma: no cover - defensive
        return EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError(
                "internal_error",
                "capability_status failed unexpectedly",
                retryable=False,
            ),
            routing=EvidenceRouting((), (), "v2-capability-status-1", ()),
            metadata=ExecutionMetadata(rid, int((time.monotonic() - started) * 1000)),
        )


# ---------------------------------------------------------------------------
# Typed deterministic composition
# ---------------------------------------------------------------------------


@observe_command
async def composite_search(query: str, *, max_results: int = 5) -> EvidenceOperationOutcome:
    """
    Deterministic v2 search composition.

    Always runs source_discovery; adds docs_discovery for local docs/API
    signals. Primary envelope operation remains source_discovery. Combines
    typed child outcomes only, never V2 envelopes, and reuses one outer
    RequestContext for nested branch execution.
    """
    started = time.monotonic()
    request_id = _request_id()
    source_req = SourceDiscoveryRequest(query=query, max_results=max_results)
    source = await source_discovery(source_req)
    route = project_evidence_routing(query)
    include_docs = bool(route.get("include_docs_discovery"))
    docs: EvidenceOperationOutcome | None = None
    if include_docs:
        docs = await docs_discovery(DocsDiscoveryRequest(query=query, max_results=max_results))

    requested = ("source_discovery", "docs_discovery") if include_docs else ("source_discovery",)
    executed: list[str] = []
    if source.attempts:
        executed.append("source_discovery")
    if docs is not None and docs.attempts:
        executed.append("docs_discovery")

    source_failed = source.status is EvidenceOperationStatus.FAILED
    source_candidates = list(source.candidates)
    docs_candidates = list(docs.candidates) if docs is not None else []
    docs_failed = bool(docs is not None and docs.status is EvidenceOperationStatus.FAILED)
    docs_ok = bool(docs is not None and not docs_failed)
    source_config_failed = source_failed and not source.attempts
    docs_config_failed = bool(docs is not None and docs_failed and not docs.attempts)

    candidates = _merge_candidates(source_candidates, docs_candidates)
    attempts = tuple(source.attempts) + (tuple(docs.attempts) if docs is not None else ())

    degradation: list[EvidenceDegradation] = []
    error: ExecutionError | None = None
    status: EvidenceOperationStatus

    usable = bool(candidates)
    if usable:
        if source_failed or docs_failed:
            status = EvidenceOperationStatus.DEGRADED
            if source_failed:
                code = "capability_unavailable" if source_config_failed else "provider_partial_failure"
                degradation.append(
                    EvidenceDegradation(code, "source_discovery", "source_discovery branch failed")
                )
            if docs_failed:
                code = "capability_unavailable" if docs_config_failed else "provider_partial_failure"
                degradation.append(
                    EvidenceDegradation(code, "docs_discovery", "docs_discovery branch failed")
                )
        else:
            status = EvidenceOperationStatus.COMPLETE
            if source.status is EvidenceOperationStatus.DEGRADED or (
                docs is not None and docs.status is EvidenceOperationStatus.DEGRADED
            ):
                status = EvidenceOperationStatus.DEGRADED
                degradation.extend(source.degradation)
                if docs is not None:
                    degradation.extend(docs.degradation)
    else:
        if source_failed and (docs is None or docs_failed or not docs_ok):
            status = EvidenceOperationStatus.FAILED
            if source.error is not None:
                error = source.error
            elif docs is not None and docs.error is not None:
                error = docs.error
            else:
                error = ExecutionError("provider_error", "search failed", retryable=True)
        elif not source_failed and docs is not None and docs_failed and not source_candidates:
            status = EvidenceOperationStatus.FAILED
            error = docs.error or ExecutionError("provider_error", "search failed", retryable=True)
        elif source_failed and docs is not None and not docs_failed and not docs_candidates:
            status = EvidenceOperationStatus.FAILED
            error = source.error or ExecutionError("provider_error", "search failed", retryable=True)
        else:
            status = EvidenceOperationStatus.COMPLETE

    if status is EvidenceOperationStatus.FAILED:
        degradation = []

    return EvidenceOperationOutcome(
        operation="source_discovery",
        status=status,
        candidates=tuple(candidates),
        attempts=attempts,
        degradation=tuple(degradation),
        error=error,
        routing=EvidenceRouting(tuple(requested), tuple(executed), "v2", tuple(requested)),
        metadata=ExecutionMetadata(request_id, int((time.monotonic() - started) * 1000)),
    )


__all__ = [
    "CanonicalOperationError",
    "ContentFetchRequest",
    "DocsDiscoveryRequest",
    "EvidenceDegradation",
    "EvidenceOperationOutcome",
    "EvidenceOperationStatus",
    "EvidenceRouting",
    "SiteDiscoveryRequest",
    "SourceDiscoveryRequest",
    "capability_status",
    "composite_search",
    "content_fetch",
    "docs_discovery",
    "site_discovery",
    "source_discovery",
]