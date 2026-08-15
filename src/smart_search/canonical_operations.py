"""Canonical v2 operation projection boundary.

Strict one-way projection from schema-neutral typed Evidence operation
outcomes (``evidence_operations``) into the public V2 envelope. This module
does not own admission, qualification, fallback, cache, budget, routing
policy, or operation state; it only maps typed domain facts into V2 models and
validates the resulting envelope exactly once.

Projection helpers must never call Provider/runtime helpers, qualification,
config, cache/budget, intent routing, or legacy service functions, and must
never inspect raw Provider mappings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import evidence_operations as _evidence_operations
from .evidence_operations import (
    CanonicalOperationError,
    ContentFetchRequest,
    DocsDiscoveryRequest,
    EvidenceDegradation,
    EvidenceOperationOutcome,
    SiteDiscoveryRequest,
    SourceDiscoveryRequest,
)
from .execution_primitives import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCandidate,
    ExecutionCitation,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionGap,
)
from .v2_contract import (
    ERROR_RETRYABILITY,
    V2Attempt,
    V2AttemptStatus,
    V2Candidate,
    V2Citation,
    V2Degradation,
    V2Envelope,
    V2Error,
    V2ErrorCode,
    V2Evidence,
    V2EvidenceItem,
    V2Gap,
    V2Meta,
    V2Routing,
    V2Status,
    capability_status_result,
    validate_result,
)

# Evidence operation -> public v2 command.
_OPERATION_TO_COMMAND: Mapping[str, str] = {
    "source_discovery": "search",
    "docs_discovery": "search",
    "content_fetch": "fetch",
    "site_discovery": "map",
    "capability_status": "capabilities",
}

# Single internal execution error vocabulary -> V2 error code mapping.
_LEGACY_ERROR_TO_V2: Mapping[str, V2ErrorCode] = {
    "config_error": V2ErrorCode.CONFIGURATION_ERROR,
    "parameter_error": V2ErrorCode.INVALID_ARGUMENT,
    "auth_error": V2ErrorCode.AUTHENTICATION_FAILED,
    "authentication": V2ErrorCode.AUTHENTICATION_FAILED,
    "rate_limit": V2ErrorCode.RATE_LIMITED,
    "rate_limited": V2ErrorCode.RATE_LIMITED,
    "timeout": V2ErrorCode.UPSTREAM_TIMEOUT,
    "network_error": V2ErrorCode.PROVIDER_UNAVAILABLE,
    "provider_error": V2ErrorCode.PROVIDER_UNAVAILABLE,
    "provider_unavailable": V2ErrorCode.PROVIDER_UNAVAILABLE,
    "fetch_error": V2ErrorCode.FETCH_FAILED,
    "empty": V2ErrorCode.FETCH_FAILED,
    "quality_error": V2ErrorCode.FETCH_FAILED,
    "challenge": V2ErrorCode.FETCH_FAILED,
    "too_large": V2ErrorCode.FETCH_FAILED,
    "parse_error": V2ErrorCode.PARSE_FAILED,
    "protocol_error": V2ErrorCode.PROTOCOL_ERROR,
    "budget_exhausted": V2ErrorCode.BUDGET_EXHAUSTED,
    "internal_error": V2ErrorCode.INTERNAL_ERROR,
}


def _map_error_code(error_type: str | None) -> V2ErrorCode:
    key = str(error_type or "").strip().lower()
    return _LEGACY_ERROR_TO_V2.get(key, V2ErrorCode.PROVIDER_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Pure typed domain -> V2 model projection helpers
# ---------------------------------------------------------------------------


def _typed_attempt_to_v2(attempt: ExecutionAttempt, v2_operation: str) -> V2Attempt:
    """Project a typed ``ExecutionAttempt`` into the strict V2 attempt shape.

    Only the caller-selected V2 operation, typed provider, classified status,
    error type, elapsed milliseconds and result count are mapped. No legacy
    dict parsing, no mapping ``.get()``, no elapsed fallback, and no repair of
    arbitrary input. ``ok`` and ``empty`` map to a null error code; ``skipped``
    and ``error`` map through the single ``_LEGACY_ERROR_TO_V2`` vocabulary.
    """
    if attempt.status is ExecutionAttemptStatus.OK:
        status = V2AttemptStatus.OK
        error_code = None
    elif attempt.status is ExecutionAttemptStatus.EMPTY:
        status = V2AttemptStatus.EMPTY
        error_code = None
    elif attempt.status is ExecutionAttemptStatus.SKIPPED:
        status = V2AttemptStatus.SKIPPED
        error_code = _map_error_code(attempt.error.type).value if attempt.error is not None else None
    else:
        status = V2AttemptStatus.ERROR
        error_code = _map_error_code(attempt.error.type).value if attempt.error is not None else None
    return V2Attempt(
        capability=v2_operation,
        provider=attempt.provider,
        status=status,
        error_code=error_code,
        elapsed_ms=max(0, int(attempt.elapsed_ms)),
        result_count=attempt.result_count,
    )


def _candidate_to_v2(candidate: ExecutionCandidate) -> V2Candidate:
    return V2Candidate(
        candidate.id,
        candidate.resource,
        candidate.provider,
        candidate.title,
        candidate.snippet,
    )


def _item_to_v2(item: ExecutionEvidenceItem) -> V2EvidenceItem:
    return V2EvidenceItem(
        item.id,
        item.resource,
        item.provider,
        item.title,
        item.content,
        item.truncated,
        item.original_length,
        item.returned_length,
    )


def _citation_to_v2(citation: ExecutionCitation) -> V2Citation:
    return V2Citation(citation.id, citation.evidence_id, citation.label)


def _gap_to_v2(gap: ExecutionGap) -> V2Gap:
    return V2Gap(gap.code, gap.message, gap.capability, gap.resource)


def _degradation_to_v2(degradation: EvidenceDegradation) -> V2Degradation:
    return V2Degradation(degradation.code, degradation.operation, degradation.message)


def _error_to_v2(error: ExecutionError | None) -> V2Error | None:
    if error is None:
        return None
    code = _map_error_code(error.type)
    return V2Error(code, error.message, ERROR_RETRYABILITY[code], dict(error.details))


def _project_evidence_outcome(outcome: EvidenceOperationOutcome) -> V2Envelope:
    """Map one typed Evidence outcome into the strict V2 envelope.

    The projection derives only mechanical facts from the typed outcome:
    status by exact value, command by operation, arrays by typed values, and
    result summaries from typed arrays (or frozen local data for capability
    status). It never re-derives usability, fallback, terminal attempts,
    branch precedence, retryability, or admission.
    """
    if outcome.operation == "capability_status":
        return capability_status_result(
            status=outcome.status,
            result=outcome.local_data or None,
            error=_error_to_v2(outcome.error),
            request_id=outcome.metadata.request_id,
            duration_ms=int(outcome.metadata.duration_ms),
            reason_codes=outcome.routing.reason_codes,
        )
    command = _OPERATION_TO_COMMAND[outcome.operation]
    candidates = tuple(_candidate_to_v2(item) for item in outcome.candidates)
    items = tuple(_item_to_v2(item) for item in outcome.evidence_items)
    citations = tuple(_citation_to_v2(item) for item in outcome.citations)
    gaps = tuple(_gap_to_v2(item) for item in outcome.gaps)
    attempts = tuple(_typed_attempt_to_v2(item, outcome.operation) for item in outcome.attempts)
    degradation = tuple(_degradation_to_v2(item) for item in outcome.degradation)
    # Pre-execution configuration failures own an empty result object; every
    # executed path derives the result summary mechanically from typed arrays.
    config_failed = (
        not outcome.attempts
        and outcome.error is not None
        and outcome.error.type == "config_error"
        and outcome.routing.reason_codes == ("configuration_error",)
    )
    if config_failed:
        result: Mapping[str, Any] = {}
    else:
        result = {
            "total": len(candidates) + len(items),
            "items": [{"id": item.id} for item in candidates]
            + [{"id": item.id} for item in items],
        }
    return validate_result(
        V2Envelope(
            status=V2Status(outcome.status.value),
            command=command,
            operation=outcome.operation,
            result=result,
            evidence=V2Evidence(
                candidates=candidates,
                items=items,
                citations=citations,
                gaps=gaps,
            ),
            routing=V2Routing(
                outcome.routing.requested_operations,
                outcome.routing.executed_operations,
                outcome.routing.policy_version,
                outcome.routing.reason_codes,
            ),
            attempts=attempts,
            degradation=degradation,
            error=_error_to_v2(outcome.error),
            meta=V2Meta(
                outcome.metadata.request_id,
                int(outcome.metadata.duration_ms),
                warnings=outcome.metadata.warnings,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Stable public wrappers: each calls its typed owner exactly once.
# ---------------------------------------------------------------------------


async def source_discovery(request: SourceDiscoveryRequest) -> V2Envelope:
    return _project_evidence_outcome(await _evidence_operations.source_discovery(request))


async def docs_discovery(request: DocsDiscoveryRequest) -> V2Envelope:
    return _project_evidence_outcome(await _evidence_operations.docs_discovery(request))


async def content_fetch(request: ContentFetchRequest) -> V2Envelope:
    return _project_evidence_outcome(await _evidence_operations.content_fetch(request))


async def site_discovery(request: SiteDiscoveryRequest) -> V2Envelope:
    return _project_evidence_outcome(await _evidence_operations.site_discovery(request))


def capability_status(*, request_id: str | None = None) -> V2Envelope:
    return _project_evidence_outcome(_evidence_operations.capability_status(request_id=request_id))


async def composite_search(query: str, *, max_results: int = 5) -> V2Envelope:
    return _project_evidence_outcome(
        await _evidence_operations.composite_search(query, max_results=max_results)
    )


__all__ = [
    "CanonicalOperationError",
    "ContentFetchRequest",
    "DocsDiscoveryRequest",
    "SiteDiscoveryRequest",
    "SourceDiscoveryRequest",
    "capability_status",
    "composite_search",
    "content_fetch",
    "docs_discovery",
    "site_discovery",
    "source_discovery",
]