"""Multi-source retrieval core for v0.3.0 (provider-agnostic gateway).

Boundaries:

- ``DiscoveryCandidate`` is the one canonical internal representation for
  search results. Provider-specific response objects never leak above each
  adapter's normalizer.
- ``canonicalize_url`` / ``deduplicate_candidates`` / ``reciprocal_rank_fusion``
  / ``resolve_retrieval_policy`` are pure, deterministic, network-free and
  unit-testable without any provider import.
- ``retrieve`` is the thin orchestration: parallel per-provider
  ``execute_capability`` fan-out (each with ``fallback="off"`` and a single
  provider), normalization through the small
  ``PROVIDER_CANDIDATE_NORMALIZERS`` registry (no ``if provider == ...``
  dispatch in this module's mapping logic), dedup, RRF, optional best-effort
  Jina rerank (never a single point of failure), and a final top-limit cut.

Provider-native scores live in ``DiscoveryCandidate.metadata`` for diagnostics
only and are never used as a shared cross-provider ranking signal. Provenance
and rank diagnostics stay internal; the V2 evidence contract consumes only the
normalized candidate dicts produced by ``_execute_retrieval_search``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .capability_executor import CapabilityOperation, execute_capability
from .config import config
from .execution_primitives import ExecutionAttempt, ExecutionOutcome, error_attempt
from .provider_search_commands import call_brave_search, call_tavily_search, exa_search
from .providers.base import classify_provider_exception
from .providers.jina_rerank import rerank as _jina_rerank


# ---------------------------------------------------------------------------
# Canonical candidate model (internal; never part of the public V2 contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveryCandidate:
    """One normalized provider search result, before fusion.

    ``provider_rank`` is the 0-based position within that provider's own
    result list (RRF input and diagnostics). ``metadata`` carries provider
    native diagnostics (native scores, author ids, page age, ...) and must
    never become a shared cross-provider ranking signal.
    """

    url: str
    title: str
    provider: str
    snippet: str = ""
    published_at: str = ""  # ISO-8601 string when the provider gives one
    provider_rank: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FusedCandidate:
    """A deduplicated candidate with retained cross-provider provenance."""

    url: str  # canonical URL (canonicalize_url output)
    display_url: str  # original first-seen URL
    title: str
    snippet: str
    providers: tuple[str, ...]  # provenance in first-seen (policy) order
    provider_ranks: Mapping[str, int]  # {"brave": 0, "exa": 2}
    metadata: Mapping[str, Any]  # merged diagnostics


@dataclass(frozen=True)
class RankedCandidate:
    """One fused candidate with its deterministic RRF score and final rank."""

    candidate: FusedCandidate
    rrf_score: float
    rank: int  # 0-based deterministic final rank


@dataclass(frozen=True)
class RetrievalOutcome:
    """Result of one gateway retrieval run (internal diagnostics)."""

    ranked: tuple[RankedCandidate, ...]
    attempts: tuple[ExecutionAttempt, ...]  # existing typed attempts, policy order
    policy: tuple[str, ...]  # providers actually executed
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# URL canonicalization (pure, deterministic, conservative)
# ---------------------------------------------------------------------------

# Known tracking parameters removed during canonicalization. Matching is
# case-insensitive on the parameter name.
_TRACKING_QUERY_PARAMS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src", "ref_url"}
)
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def canonicalize_url(url: str) -> str:
    """Deterministic conservative canonicalization for cross-provider dedup.

    High-confidence transformations only:

    - lowercase scheme and hostname;
    - drop the fragment;
    - drop default http(80)/https(443) ports;
    - root/empty-path trailing-slash equivalence (``http://x/`` == ``http://x``);
    - drop known tracking parameters (``utm_*`` prefix, ``fbclid``, ``gclid``,
      ``mc_cid``, ``mc_eid``, ``igshid``, ``ref_src``, ``ref_url``);
    - sort the remaining query parameters by (key, value) for determinism;
    - preserve every other meaningful query parameter.

    Explicitly NOT done in v0.3.0: www normalization (``www.example.com`` and
    ``example.com`` must NOT merge), network redirect resolution, semantic or
    content dedup. Non-hierarchical or unparseable inputs are returned
    unchanged so distinct pages are never merged by aggressive heuristics.
    """
    if not isinstance(url, str):
        return ""
    stripped = url.strip()
    try:
        parts = urlparse(stripped)
    except ValueError:
        return stripped
    scheme = parts.scheme.lower()
    hostname = parts.hostname
    if not scheme or not hostname:
        return stripped

    # Rebuild netloc: lowercase hostname, keep userinfo exactly as given
    # (pages that differ by credentials must never merge), drop default ports.
    port = parts.port
    if port is not None and _DEFAULT_PORTS.get(scheme) == str(port):
        port = None
    netloc = hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        # IPv6 literal: preserve brackets
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    if parts.username:
        userinfo = parts.username
        if parts.password is not None:
            userinfo = f"{userinfo}:{parts.password}"
        netloc = f"{userinfo}@{netloc}"

    # Root/empty-path equivalence only; never collapse other paths.
    path = parts.path or ""
    if path in ("", "/"):
        path = "/"

    # Query: drop tracking params, sort the rest deterministically.
    query = ""
    if parts.query:
        kept: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower().startswith("utm_") or key.lower() in _TRACKING_QUERY_PARAMS:
                continue
            kept.append((key, value))
        if kept:
            query = urlencode(sorted(kept))

    return urlunparse((scheme, netloc, path, parts.params, query, ""))


# ---------------------------------------------------------------------------
# Cross-provider deduplication
# ---------------------------------------------------------------------------


def deduplicate_candidates(candidates: Sequence[DiscoveryCandidate]) -> list[FusedCandidate]:
    """Merge candidates sharing one canonical URL.

    First-seen order is preserved for stable title/snippet/display_url and for
    the fused output order (deterministic given the same input order). Each
    merge retains full provenance: the provider tuple, the per-provider rank
    map, and merged diagnostic metadata. Deduplication never destroys
    cross-provider agreement information.
    """
    groups: dict[str, list[DiscoveryCandidate]] = {}
    order: list[str] = []
    for candidate in candidates:
        key = canonicalize_url(candidate.url)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candidate)

    fused: list[FusedCandidate] = []
    for key in order:
        group = groups[key]
        first = group[0]
        providers: list[str] = []
        ranks: dict[str, int] = {}
        metadata: dict[str, Any] = {}
        for candidate in group:
            if candidate.provider not in providers:
                providers.append(candidate.provider)
            ranks[candidate.provider] = candidate.provider_rank
            for meta_key, meta_value in candidate.metadata.items():
                metadata.setdefault(meta_key, meta_value)
        fused.append(
            FusedCandidate(
                url=key,
                display_url=first.url,
                title=first.title,
                snippet=first.snippet,
                providers=tuple(providers),
                provider_ranks=MappingProxyType(dict(ranks)),
                metadata=MappingProxyType(dict(metadata)),
            )
        )
    return fused


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (deterministic, pure)
# ---------------------------------------------------------------------------

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    candidates: Sequence[FusedCandidate],
    *,
    k: int = DEFAULT_RRF_K,
) -> list[RankedCandidate]:
    """Fuse deduplicated candidates with reciprocal rank fusion.

    score(candidate) = Σ over providers of 1 / (k + provider_rank + 1),
    where ``provider_rank`` is the provider's 0-based position. Provider
    native scores are never read. Ordering is deterministic: descending score,
    then canonical URL, then the provenance tuple. The final 0-based rank is
    assigned after sorting.
    """
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        score = 0.0
        for provider in candidate.providers:
            provider_rank = candidate.provider_ranks.get(provider, 0)
            score += 1.0 / (k + provider_rank + 1)
        ranked.append(RankedCandidate(candidate=candidate, rrf_score=score, rank=0))
    ranked.sort(key=lambda item: (-item.rrf_score, item.candidate.url, item.candidate.providers))
    return [
        RankedCandidate(candidate=item.candidate, rrf_score=item.rrf_score, rank=index)
        for index, item in enumerate(ranked)
    ]


# ---------------------------------------------------------------------------
# Retrieval policy (thin, deterministic)
# ---------------------------------------------------------------------------

RETRIEVAL_POLICIES: dict[str, tuple[str, ...]] = {
    "general": ("brave", "exa"),
    "fresh": ("brave",),
    "semantic": ("exa",),
    "technical": ("brave", "exa"),
    "research": ("brave", "exa", "tavily"),
}


def resolve_retrieval_policy(intent: str, available: Sequence[str]) -> list[str]:
    """Resolve which providers execute source discovery for ``intent``.

    The policy table answers only "which providers run"; it never re-routes
    capabilities. Unknown intents fall back to the general policy. The
    returned list preserves the policy-table order and contains only
    providers present in ``available``; an empty intersection returns ``[]``
    and the caller decides the fallback.
    """
    key = str(intent or "").strip().lower()
    policy = RETRIEVAL_POLICIES.get(key, RETRIEVAL_POLICIES["general"])
    available_set = {str(item).strip().lower() for item in available}
    return [provider for provider in policy if provider in available_set]


# ---------------------------------------------------------------------------
# Normalizer registry (mapping bodies live in each provider adapter)
# ---------------------------------------------------------------------------


def _load_brave_normalizer() -> Callable[[Any], list[DiscoveryCandidate]]:
    # Lazy import keeps the adapter import graph free of a retrieval cycle.
    from .providers.brave import to_discovery_candidates

    return to_discovery_candidates


def _load_exa_normalizer() -> Callable[[Any], list[DiscoveryCandidate]]:
    from .providers.exa import to_discovery_candidates

    return to_discovery_candidates


def _load_tavily_normalizer() -> Callable[[Any], list[DiscoveryCandidate]]:
    from .providers.tavily import to_discovery_candidates

    return to_discovery_candidates


PROVIDER_CANDIDATE_NORMALIZERS: dict[str, Callable[[Any], list[DiscoveryCandidate]]] = {
    "brave": _load_brave_normalizer(),
    "exa": _load_exa_normalizer(),
    "tavily": _load_tavily_normalizer(),
}


# ---------------------------------------------------------------------------
# Per-provider execution closures (small registry; no dispatch in the core)
# ---------------------------------------------------------------------------


def _brave_run(query: str, count: int) -> Callable[[str, dict[str, Any]], Awaitable[list[dict]]]:
    async def run(_provider: str, outcome: dict[str, Any]) -> list[dict]:
        data = await call_brave_search(query, max_results=count)
        if isinstance(data, dict):
            outcome.update(data)
            if data.get("ok"):
                return list(data.get("results") or [])
        return []

    return run


def _exa_run(query: str, count: int) -> Callable[[str, dict[str, Any]], Awaitable[list[dict]]]:
    async def run(_provider: str, outcome: dict[str, Any]) -> list[dict]:
        data = await exa_search(query, num_results=count, include_highlights=True)
        if isinstance(data, dict):
            outcome.update(data)
            if data.get("ok"):
                return list(data.get("results") or [])
        return []

    return run


def _tavily_run(query: str, count: int) -> Callable[[str, dict[str, Any]], Awaitable[list[dict]]]:
    async def run(_provider: str, outcome: dict[str, Any]) -> list[dict]:
        data = await call_tavily_search(query, max_results=count)
        if data is None:
            return []
        return list(data)

    return run


_RETRIEVAL_RUNNERS: dict[str, Callable[[str, int], Callable[[str, dict[str, Any]], Awaitable[list[dict]]]]] = {
    "brave": _brave_run,
    "exa": _exa_run,
    "tavily": _tavily_run,
}

# Registry capability of each policy provider (used for the per-provider
# CapabilityOperation so eligibility/budget/attempt semantics match the
# provider registry).
_RETRIEVAL_CAPABILITIES: dict[str, str] = {
    "brave": "web_search",
    "exa": "docs_search",
    "tavily": "web_search",
}


def _cache_options(provider: str, count: int) -> dict[str, Any]:
    if provider == "exa":
        return {"include_highlights": True, "num_results": count}
    if provider == "tavily":
        # Distinct lane marker keeps the gateway lane isolated from the legacy
        # ``_execute_web_search`` cache entries for the same provider/query.
        return {"count": count, "retrieval_lane": True}
    return {"max_results": count}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _rerank_ranked(
    query: str,
    ranked: Sequence[RankedCandidate],
    *,
    top_n: int,
    ctx,
) -> list[RankedCandidate] | None:
    """Best-effort Jina rerank over the RRF order.

    Returns the reordered candidates on success and ``None`` on any classified
    failure or unusable response; the caller keeps the RRF order then. The
    gateway never parses Jina-specific structures itself.
    """
    documents: list[str] = []
    for item in ranked:
        text = f"{item.candidate.title} {item.candidate.snippet}".strip()
        documents.append(text or item.candidate.url)
    result = await _jina_rerank(query, documents, top_n=top_n, ctx=ctx)
    if not result.ok:
        return None
    raw = result.data.get("results") if isinstance(result.data, Mapping) else None
    if not isinstance(raw, list) or not raw:
        return None
    scores: dict[int, float] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        index = entry.get("index")
        score = entry.get("relevance_score")
        if isinstance(index, int) and isinstance(score, (int, float)):
            scores[index] = float(score)
    if not scores:
        return None
    ordered = sorted(
        range(len(ranked)),
        key=lambda index: (-scores.get(index, float("-inf")), index),
    )
    return [ranked[index] for index in ordered]


async def retrieve(
    query: str,
    providers: Sequence[str],
    limit: int = 5,
    *,
    intent: str = "general",
    ctx=None,
) -> RetrievalOutcome:
    """Run the gateway pipeline for one query.

    ``providers`` is the eligible policy-provider list (already filtered by
    configuration and taxonomy qualification); the retrieval policy
    (``resolve_retrieval_policy``) selects which of them execute for
    ``intent``. Each provider runs through ``execute_capability`` with
    ``fallback="off"`` (single provider) so budget, cache, classification and
    typed attempts are reused unchanged. Fusion is deterministic; the Jina
    rerank step is optional and never a single point of failure.
    """
    selected = resolve_retrieval_policy(intent, providers)

    async def run_one(provider: str) -> ExecutionOutcome:
        builder = _RETRIEVAL_RUNNERS.get(provider)
        if builder is None:
            return ExecutionOutcome(value=[], attempts=())
        operation = CapabilityOperation(
            capability=_RETRIEVAL_CAPABILITIES.get(provider, "web_search"),
            input_value=query,
            cache_options=_cache_options(provider, limit),
            run=builder(query, limit),
            empty_value=lambda _provider: [],
            is_success=lambda value: isinstance(value, list) and bool(value),
            result_count=lambda value: len(value) if isinstance(value, list) else 0,
        )
        return await execute_capability(operation, providers=[provider], fallback="off")

    results = await asyncio.gather(*(run_one(provider) for provider in selected), return_exceptions=True)

    attempts: list[ExecutionAttempt] = []
    candidates: list[DiscoveryCandidate] = []
    for provider, result in zip(selected, results):
        capability = _RETRIEVAL_CAPABILITIES.get(provider, "web_search")
        if isinstance(result, BaseException):
            error_type, error, retryable = classify_provider_exception(result)
            attempts.append(
                error_attempt(
                    capability,
                    provider,
                    error_type=error_type,
                    message=error,
                    elapsed_ms=0.0,
                    retryable=retryable,
                )
            )
            continue
        attempts.extend(result.attempts)
        normalizer = PROVIDER_CANDIDATE_NORMALIZERS.get(provider)
        if normalizer is not None:
            candidates.extend(normalizer(result.value))

    fused = deduplicate_candidates(candidates)
    ranked = reciprocal_rank_fusion(fused)

    warnings: list[str] = []
    if config.jina_api_key and ranked:
        reranked: list[RankedCandidate] | None = None
        reason = ""
        try:
            reranked = await _rerank_ranked(query, ranked, top_n=limit, ctx=ctx)
        except Exception as exc:  # never a single point of failure
            error_type, _message, _retryable = classify_provider_exception(exc)
            reason = f" ({error_type})"
        if reranked is not None:
            ranked = reranked
        else:
            warnings.append(f"jina rerank unavailable{reason}; keeping RRF order")

    return RetrievalOutcome(
        ranked=tuple(ranked[:limit]),
        attempts=tuple(attempts),
        policy=tuple(selected),
        warnings=tuple(warnings),
    )


__all__ = [
    "DEFAULT_RRF_K",
    "DiscoveryCandidate",
    "FusedCandidate",
    "PROVIDER_CANDIDATE_NORMALIZERS",
    "RETRIEVAL_POLICIES",
    "RankedCandidate",
    "RetrievalOutcome",
    "canonicalize_url",
    "deduplicate_candidates",
    "reciprocal_rank_fusion",
    "resolve_retrieval_policy",
    "retrieve",
]
