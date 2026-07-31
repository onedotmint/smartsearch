"""Canonical v2 operation handlers.

Converts qualified same-capability runner outcomes into typed v2 envelopes.
Does not call legacy main_search or search_service.search.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

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
from .intent_router import project_evidence_routing
from .operation_runtime import (
    _run_docs_search_fallback,
    _run_site_map,
    _run_web_fetch_fallback,
    _run_web_search_fallback,
)
from .runtime_cache import observe_command
from .v2_contract import (
    ERROR_RETRYABILITY,
    V2Attempt,
    V2AttemptStatus,
    V2Candidate,
    V2Degradation,
    V2Envelope,
    V2Error,
    V2ErrorCode,
    V2Evidence,
    V2EvidenceItem,
    V2Meta,
    V2Routing,
    V2Status,
    V2_META_OPERATION_CAPABILITY_STATUS,
    capability_status_result,
    validate_result,
)

# v2 operation -> legacy execution capability
_V2_TO_V1_CAPABILITY: Mapping[str, str] = {
    "source_discovery": "web_search",
    "docs_discovery": "docs_search",
    "content_fetch": "web_fetch",
    "site_discovery": "site_map",
    "answer_synthesis": "main_search",
}

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
    "parse_error": V2ErrorCode.PARSE_FAILED,
    "protocol_error": V2ErrorCode.PROTOCOL_ERROR,
    "budget_exhausted": V2ErrorCode.BUDGET_EXHAUSTED,
    "internal_error": V2ErrorCode.INTERNAL_ERROR,
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


def _request_id() -> str:
    return f"v2-{uuid.uuid4().hex[:12]}"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    safe_prefix = re.sub(r"[^a-z0-9_-]+", "-", prefix.lower()).strip("-") or "id"
    return f"{safe_prefix}-{digest}"


def _map_error_code(error_type: str | None) -> V2ErrorCode:
    key = str(error_type or "").strip().lower()
    return _LEGACY_ERROR_TO_V2.get(key, V2ErrorCode.PROVIDER_UNAVAILABLE)


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


def _legacy_attempt_to_v2(attempt: Mapping[str, Any], v2_operation: str) -> V2Attempt:
    status_raw = str(attempt.get("status") or "error").lower()
    error_type = str(attempt.get("error_type") or "")
    if status_raw in {"ok", "success"}:
        status = "ok"
        error_code = None
    elif status_raw in {"empty"}:
        status = "empty"
        error_code = None
    elif status_raw in {"skipped"}:
        status = "skipped"
        error_code = _map_error_code(error_type or "config_error").value
    else:
        status = "error"
        error_code = _map_error_code(error_type).value
    elapsed = attempt.get("elapsed_ms")
    if elapsed is None:
        elapsed_s = attempt.get("elapsed_s") or attempt.get("elapsed") or 0
        try:
            elapsed = int(float(elapsed_s) * 1000)
        except (TypeError, ValueError):
            elapsed = 0
    try:
        elapsed_ms = max(0, int(elapsed))
    except (TypeError, ValueError):
        elapsed_ms = 0
    try:
        result_count = max(0, int(attempt.get("result_count") or 0))
    except (TypeError, ValueError):
        result_count = 0
    return V2Attempt(
        capability=v2_operation,
        provider=str(attempt.get("provider") or "unknown"),
        status=status,
        error_code=error_code,
        elapsed_ms=elapsed_ms,
        result_count=result_count,
    )


def _source_to_candidate(item: Mapping[str, Any], *, operation: str, index: int) -> V2Candidate | None:
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
    return V2Candidate(
        id=_stable_id(operation, resource, provider, str(index)),
        resource=resource,
        provider=provider,
        title=title,
        snippet=snippet[:500],
    )


def _fetch_to_evidence(item: Mapping[str, Any], *, index: int) -> V2EvidenceItem | None:
    if not is_content_fetch_success(item):
        return None
    resource = str(item.get("url") or "").strip()
    provider = str(item.get("provider") or "").strip()
    content = str(item.get("content") or item.get("raw_content") or "").strip()
    title = str(item.get("title") or resource).strip()
    return V2EvidenceItem(
        id=_stable_id("evidence", resource, provider, str(index)),
        resource=resource,
        provider=provider,
        title=title,
        content=content,
    )


def _config_failed(
    *,
    command: str,
    operation: str,
    message: str,
    request_id: str,
    duration_ms: int,
    details: Mapping[str, Any] | None = None,
) -> V2Envelope:
    return validate_result(
        V2Envelope(
            status=V2Status.FAILED,
            command=command,
            operation=operation,
            result={},
            evidence=V2Evidence(),
            routing=V2Routing((operation,), (), "v2", ("configuration_error",)),
            attempts=(),
            degradation=(),
            error=V2Error(
                V2ErrorCode.CONFIGURATION_ERROR,
                message,
                ERROR_RETRYABILITY[V2ErrorCode.CONFIGURATION_ERROR],
                dict(details or {}),
            ),
            meta=V2Meta(request_id, duration_ms),
        )
    )


def _derive_discovery_envelope(
    *,
    command: str,
    operation: str,
    candidates: Sequence[V2Candidate],
    attempts: Sequence[V2Attempt],
    request_id: str,
    duration_ms: int,
    reason_codes: Sequence[str] = (),
) -> V2Envelope:
    executed = (operation,) if attempts else ()
    has_error = any(str(a.status) in {"error", "skipped", V2AttemptStatus.ERROR.value, V2AttemptStatus.SKIPPED.value} for a in attempts)
    has_success_or_empty = any(str(a.status) in {"ok", "empty", V2AttemptStatus.OK.value, V2AttemptStatus.EMPTY.value} for a in attempts)
    usable = bool(candidates)

    if not attempts:
        return _config_failed(
            command=command,
            operation=operation,
            message=f"No qualified providers for {operation}",
            request_id=request_id,
            duration_ms=duration_ms,
        )

    if usable and has_error:
        status = V2Status.DEGRADED
        degradation = (
            V2Degradation(
                "provider_partial_failure",
                operation,
                "One or more providers failed before a usable result",
            ),
        )
        error = None
    elif usable or (has_success_or_empty and not has_error):
        # complete including total=0 empty success
        status = V2Status.COMPLETE
        degradation = ()
        error = None
    elif has_error and not usable:
        # all failed
        last_error = next(
            (a for a in reversed(attempts) if str(a.status) in {"error", "skipped", V2AttemptStatus.ERROR.value, V2AttemptStatus.SKIPPED.value}),
            None,
        )
        code = V2ErrorCode(last_error.error_code) if last_error and last_error.error_code else V2ErrorCode.PROVIDER_UNAVAILABLE
        status = V2Status.FAILED
        degradation = ()
        error = V2Error(code, f"{operation} failed", ERROR_RETRYABILITY[code], {})
    else:
        status = V2Status.COMPLETE
        degradation = ()
        error = None

    return validate_result(
        V2Envelope(
            status=status,
            command=command,
            operation=operation,
            result={"total": len(candidates), "items": [{"id": c.id} for c in candidates]},
            evidence=V2Evidence(candidates=tuple(candidates)),
            routing=V2Routing(
                (operation,),
                executed,
                "v2",
                tuple(reason_codes) or (("requested",) if executed else ()),
            ),
            attempts=tuple(attempts),
            degradation=degradation,
            error=error,
            meta=V2Meta(request_id, duration_ms),
        )
    )


@observe_command
async def source_discovery(request: SourceDiscoveryRequest) -> V2Envelope:
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, SourceDiscoveryRequest):
        request = SourceDiscoveryRequest(query=getattr(request, "query", ""), max_results=getattr(request, "max_results", 5))
    providers = _qualified_providers("source_discovery")
    if not providers:
        return _config_failed(
            command="search",
            operation="source_discovery",
            message="No qualified source_discovery providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            details={"qualified_providers": []},
        )
    values, legacy_attempts = await _run_web_search_fallback(
        request.query,
        count=request.max_results,
        providers=",".join(providers),
        fallback="auto",
    )
    # Filter attempts/providers to only those we qualified (executor may still skip others).
    allowed = set(providers)
    attempts = [
        _legacy_attempt_to_v2(item, "source_discovery")
        for item in legacy_attempts
        if str(item.get("provider") or "") in allowed or item.get("status") in {"ok", "empty", "error"}
    ]
    # Keep only attempts for qualified providers; drop unqualified skips that shouldn't run.
    attempts = [a for a in attempts if a.provider in allowed]
    candidates: list[V2Candidate] = []
    for index, item in enumerate(values or []):
        if isinstance(item, Mapping):
            candidate = _source_to_candidate(item, operation="source_discovery", index=index)
            if candidate:
                candidates.append(candidate)
    return _derive_discovery_envelope(
        command="search",
        operation="source_discovery",
        candidates=candidates,
        attempts=attempts,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason_codes=("source_discovery",),
    )


@observe_command
async def docs_discovery(request: DocsDiscoveryRequest) -> V2Envelope:
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, DocsDiscoveryRequest):
        request = DocsDiscoveryRequest(query=getattr(request, "query", ""), max_results=getattr(request, "max_results", 5))
    providers = _qualified_providers("docs_discovery")
    if not providers:
        return _config_failed(
            command="search",
            operation="docs_discovery",
            message="No qualified docs_discovery providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            details={"qualified_providers": []},
        )
    values, legacy_attempts = await _run_docs_search_fallback(
        request.query,
        count=request.max_results,
        providers=",".join(providers),
        fallback="auto",
    )
    allowed = set(providers)
    attempts = [
        _legacy_attempt_to_v2(item, "docs_discovery")
        for item in legacy_attempts
        if str(item.get("provider") or "") in allowed
    ]
    candidates: list[V2Candidate] = []
    for index, item in enumerate((values or [])[: request.max_results]):
        if isinstance(item, Mapping):
            candidate = _source_to_candidate(item, operation="docs_discovery", index=index)
            if candidate:
                candidates.append(candidate)
    return _derive_discovery_envelope(
        command="search",
        operation="docs_discovery",
        candidates=candidates,
        attempts=attempts,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason_codes=("docs_discovery",),
    )


@observe_command
async def content_fetch(request: ContentFetchRequest) -> V2Envelope:
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, ContentFetchRequest):
        request = ContentFetchRequest(resource=getattr(request, "resource", ""))
    providers = _qualified_providers("content_fetch")
    if not providers:
        return _config_failed(
            command="fetch",
            operation="content_fetch",
            message="No qualified content_fetch providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    value, legacy_attempts = await _run_web_fetch_fallback(
        request.resource,
        fallback="auto",
        preferred_order=providers,
        providers=providers,
    )
    allowed = set(providers)
    attempts = [
        _legacy_attempt_to_v2(item, "content_fetch")
        for item in legacy_attempts
        if str(item.get("provider") or "") in allowed
    ]
    items: list[V2EvidenceItem] = []
    if isinstance(value, Mapping):
        evidence_item = _fetch_to_evidence(value, index=0)
        if evidence_item:
            items.append(evidence_item)

    has_error = any(str(a.status) in {"error", "skipped"} for a in attempts)
    if items and has_error:
        status = V2Status.DEGRADED
        degradation = (
            V2Degradation(
                "provider_partial_failure",
                "content_fetch",
                "Fetch succeeded after provider failures",
            ),
        )
        error = None
    elif items:
        status = V2Status.COMPLETE
        degradation = ()
        error = None
    else:
        last_error = next((a for a in reversed(attempts) if a.error_code), None)
        code = V2ErrorCode(last_error.error_code) if last_error and last_error.error_code else V2ErrorCode.FETCH_FAILED
        status = V2Status.FAILED
        degradation = ()
        error = V2Error(code, "content_fetch failed", ERROR_RETRYABILITY[code], {})

    return validate_result(
        V2Envelope(
            status=status,
            command="fetch",
            operation="content_fetch",
            result={"total": len(items), "items": [{"id": item.id} for item in items]},
            evidence=V2Evidence(items=tuple(items)),
            routing=V2Routing(
                ("content_fetch",),
                ("content_fetch",) if attempts else (),
                "v2",
                ("content_fetch",),
            ),
            attempts=tuple(attempts),
            degradation=degradation,
            error=error,
            meta=V2Meta(request_id, int((time.monotonic() - started) * 1000)),
        )
    )


@observe_command
async def site_discovery(request: SiteDiscoveryRequest) -> V2Envelope:
    started = time.monotonic()
    request_id = _request_id()
    if not isinstance(request, SiteDiscoveryRequest):
        request = SiteDiscoveryRequest(resource=getattr(request, "resource", ""))
    providers = _qualified_providers("site_discovery")
    if not providers:
        return _config_failed(
            command="map",
            operation="site_discovery",
            message="No qualified site_discovery providers configured",
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    value, legacy_attempts = await _run_site_map(
        request.resource,
        instructions=request.instructions,
        max_depth=request.max_depth,
        max_breadth=request.max_breadth,
        limit=request.limit,
    )
    attempts = [_legacy_attempt_to_v2(item, "site_discovery") for item in legacy_attempts]
    candidates: list[V2Candidate] = []
    results = []
    if isinstance(value, Mapping):
        raw_results = value.get("results") or []
        if isinstance(raw_results, list):
            results = raw_results
    for index, raw in enumerate(results):
        if isinstance(raw, str):
            item = {"url": raw, "title": raw, "provider": "tavily"}
        elif isinstance(raw, Mapping):
            item = {
                "url": raw.get("url") or raw.get("link") or "",
                "title": raw.get("title") or raw.get("url") or "",
                "description": raw.get("description") or "",
                "provider": raw.get("provider") or "tavily",
            }
        else:
            continue
        candidate = _source_to_candidate(item, operation="site_discovery", index=index)
        if candidate:
            candidates.append(candidate)
    return _derive_discovery_envelope(
        command="map",
        operation="site_discovery",
        candidates=candidates,
        attempts=attempts,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason_codes=("site_discovery",),
    )


def capability_status(*, request_id: str | None = None) -> V2Envelope:
    """Local zero-network capability status inspection."""
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
            availability_by_tier.setdefault(bucket, {})[capability] = available_providers.get(capability, [])
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
                    key: {
                        "configured": value.get("configured", []),
                        "eligible": value.get("configured", []),
                        "ok": value.get("ok"),
                    }
                    for key, value in legacy_status.items()
                    if isinstance(value, Mapping)
                },
            }
        }
        return capability_status_result(
            result=result,
            request_id=rid,
            duration_ms=int((time.monotonic() - started) * 1000),
            reason_codes=("local_inspection",),
        )
    except (ConfigStorageError, ModelRoutesConfigurationError):
        return capability_status_result(
            status=V2Status.FAILED,
            error=V2Error(
                V2ErrorCode.CONFIGURATION_ERROR,
                "capability_status configuration is invalid",
                ERROR_RETRYABILITY[V2ErrorCode.CONFIGURATION_ERROR],
                {},
            ),
            request_id=rid,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:  # pragma: no cover - defensive
        return capability_status_result(
            status=V2Status.FAILED,
            error=V2Error(
                V2ErrorCode.INTERNAL_ERROR,
                "capability_status failed unexpectedly",
                ERROR_RETRYABILITY[V2ErrorCode.INTERNAL_ERROR],
                {},
            ),
            request_id=rid,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _merge_candidates(*groups: Sequence[V2Candidate]) -> list[V2Candidate]:
    merged: list[V2Candidate] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.resource.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


@observe_command
async def composite_search(query: str, *, max_results: int = 5) -> V2Envelope:
    """
    Deterministic v2 search composition.

    Always runs source_discovery; adds docs_discovery for local docs/API signals.
    Primary envelope operation remains source_discovery.
    """
    started = time.monotonic()
    request_id = _request_id()
    source_req = SourceDiscoveryRequest(query=query, max_results=max_results)
    source = await source_discovery(source_req)
    include_docs = bool(project_evidence_routing(query).get("include_docs_discovery"))
    docs: V2Envelope | None = None
    if include_docs:
        docs = await docs_discovery(DocsDiscoveryRequest(query=query, max_results=max_results))

    requested = ("source_discovery", "docs_discovery") if include_docs else ("source_discovery",)
    executed: list[str] = []
    if source.attempts:
        executed.append("source_discovery")
    if docs is not None and docs.attempts:
        executed.append("docs_discovery")

    source_failed = source.status is V2Status.FAILED or (
        isinstance(source.status, str) and source.status == "failed"
    )
    source_candidates = list(source.evidence.candidates)
    docs_candidates = list(docs.evidence.candidates) if docs is not None else []
    docs_failed = bool(
        docs is not None
        and (
            docs.status is V2Status.FAILED
            or (isinstance(docs.status, str) and docs.status == "failed")
        )
    )
    docs_ok = bool(docs is not None and not docs_failed)
    # Treat config-failed (no attempts) as failed for composition.
    source_config_failed = source_failed and not source.attempts
    docs_config_failed = bool(docs is not None and docs_failed and not docs.attempts)

    candidates = _merge_candidates(source_candidates, docs_candidates)
    attempts = list(source.attempts) + (list(docs.attempts) if docs is not None else [])

    degradation: list[V2Degradation] = []
    error: V2Error | None = None
    status: V2Status

    usable = bool(candidates)
    if usable:
        if source_failed or docs_failed:
            status = V2Status.DEGRADED
            if source_failed:
                code = "capability_unavailable" if source_config_failed else "provider_partial_failure"
                degradation.append(V2Degradation(code, "source_discovery", "source_discovery branch failed"))
            if docs_failed:
                code = "capability_unavailable" if docs_config_failed else "provider_partial_failure"
                degradation.append(V2Degradation(code, "docs_discovery", "docs_discovery branch failed"))
        else:
            # complete even if one side empty
            status = V2Status.COMPLETE
            # preserve degraded from a single branch if it already degraded with usable results
            if source.status is V2Status.DEGRADED or (docs is not None and docs.status is V2Status.DEGRADED):
                status = V2Status.DEGRADED
                degradation.extend(source.degradation)
                if docs is not None:
                    degradation.extend(docs.degradation)
    else:
        # no usable candidates
        if source_failed and (docs is None or docs_failed or not docs_ok):
            status = V2Status.FAILED
            # primary-source error precedence when both fail
            if source.error is not None:
                error = source.error
            elif docs is not None and docs.error is not None:
                error = docs.error
            else:
                error = V2Error(
                    V2ErrorCode.PROVIDER_UNAVAILABLE,
                    "search failed",
                    ERROR_RETRYABILITY[V2ErrorCode.PROVIDER_UNAVAILABLE],
                    {},
                )
        elif not source_failed and docs is not None and docs_failed and not source_candidates:
            # source empty, docs failed
            status = V2Status.FAILED
            error = docs.error or V2Error(
                V2ErrorCode.PROVIDER_UNAVAILABLE,
                "search failed",
                True,
                {},
            )
        elif source_failed and docs is not None and not docs_failed and not docs_candidates:
            status = V2Status.FAILED
            error = source.error or V2Error(
                V2ErrorCode.PROVIDER_UNAVAILABLE,
                "search failed",
                True,
                {},
            )
        else:
            # both empty success
            status = V2Status.COMPLETE

    if status is V2Status.FAILED:
        degradation = []

    return validate_result(
        V2Envelope(
            status=status,
            command="search",
            operation="source_discovery",
            result={"total": len(candidates), "items": [{"id": c.id} for c in candidates]},
            evidence=V2Evidence(candidates=tuple(candidates)),
            routing=V2Routing(
                tuple(requested),
                tuple(executed),
                "v2",
                tuple(requested),
            ),
            attempts=tuple(attempts),
            degradation=tuple(degradation),
            error=error,
            meta=V2Meta(request_id, int((time.monotonic() - started) * 1000)),
        )
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
