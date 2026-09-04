"""Direct provider execution and deterministic retrieval fusion for v1."""
from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from .models import Candidate, RankedCandidate, RetrievalPolicy
from .normalizers import normalize_brave, normalize_exa, normalize_tavily
from .ranking import deduplicate_candidates, reciprocal_rank_fusion
from ..security import safe_provider_message

_KNOWN_ERROR_TYPES = frozenset({
    "config_error", "auth_error", "parameter_error", "timeout", "network_error",
    "rate_limited", "protocol_error", "parse_error", "quality_error", "empty",
    "provider_error", "budget_exhausted", "too_large",
})


@dataclass(frozen=True)
class RetrievalOutcome:
    ranked: tuple[RankedCandidate, ...] = ()
    attempts: tuple[ProviderAttempt, ...] = ()
    providers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        if self.ranked:
            return False
        if not self.providers:
            return True
        # Empty responses from every provider are a valid complete search;
        # a failed provider alongside them is not.
        return any(a.status in {"failed", "error"} for a in self.attempts)

    @property
    def degraded(self) -> bool:
        return bool(self.ranked) and any(a.status not in {"complete", "success"} for a in self.attempts)

def _classify(exc: BaseException) -> tuple[str, str, bool]:
    try:
        from ..providers.base import classify_provider_exception
    except ModuleNotFoundError:
        return "provider_error", safe_provider_message("provider_error"), False
    return classify_provider_exception(exc)


async def _invoke(provider: Any, query: str, limit: int) -> Any:
    method = provider.search
    try:
        value = method(query, limit)
    except TypeError:
        value = method(query, num_results=limit)
    return await value if inspect.isawaitable(value) else value


def _payload(value: Any) -> tuple[bool, Any, str, str, float]:
    if isinstance(value, Mapping):
        raw_ok = value.get("ok", True)
        if type(raw_ok) is not bool:
            raise ValueError("provider ok field must be boolean")
        raw_elapsed = value.get("elapsed_ms", 0)
        if isinstance(raw_elapsed, bool) or not isinstance(raw_elapsed, (int, float)) or not math.isfinite(float(raw_elapsed)) or raw_elapsed < 0:
            raise ValueError("provider elapsed_ms field is invalid")
        raw_error_type = value.get("error_type", "")
        if raw_error_type and (not isinstance(raw_error_type, str) or raw_error_type not in _KNOWN_ERROR_TYPES):
            raise ValueError("provider error_type field is invalid")
        raw_error = value.get("error", "")
        if raw_error and not isinstance(raw_error, str):
            raise ValueError("provider error field is invalid")
        return raw_ok, value, raw_error_type, raw_error, float(raw_elapsed)
    data = getattr(value, "data", None)
    if isinstance(data, Mapping):
        raw_ok = getattr(value, "ok", data.get("ok", True))
        if type(raw_ok) is not bool:
            raise ValueError("provider ok field must be boolean")
        raw_elapsed = getattr(value, "elapsed_ms", data.get("elapsed_ms", 0))
        if isinstance(raw_elapsed, bool) or not isinstance(raw_elapsed, (int, float)) or not math.isfinite(float(raw_elapsed)) or raw_elapsed < 0:
            raise ValueError("provider elapsed_ms field is invalid")
        raw_error_type = getattr(value, "error_type", data.get("error_type", ""))
        if raw_error_type and (not isinstance(raw_error_type, str) or raw_error_type not in _KNOWN_ERROR_TYPES):
            raise ValueError("provider error_type field is invalid")
        raw_error = getattr(value, "error", data.get("error", ""))
        if raw_error and not isinstance(raw_error, str):
            raise ValueError("provider error field is invalid")
        return raw_ok, data, raw_error_type, raw_error, float(raw_elapsed)
    if isinstance(value, (list, tuple)):
        return True, value, "", "", 0.0
    raise ValueError("provider search result must be a mapping or result list")


def _results(value: Any) -> list[Any]:
    _ok, payload, _error_type, _error, _elapsed = _payload(value)
    if not _ok:
        return []
    if isinstance(payload, Mapping):
        if "results" not in payload or not isinstance(payload["results"], (list, tuple)):
            raise ValueError("provider results field must be a list")
        return list(payload["results"])
    return list(payload)


def _generic_normalizer(provider_id: str):
    def normalize(value: Any) -> list[Candidate]:
        result: list[Candidate] = []
        for index, item in enumerate(value or []):
            if isinstance(item, Candidate):
                result.append(item)
                continue
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("url") or item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if url and title:
                score = item.get("score")
                metadata = (
                    {"native_score": score}
                    if isinstance(score, (int, float)) and not isinstance(score, bool)
                    else {}
                )
                result.append(
                    Candidate(
                        url,
                        title,
                        provider_id,
                        str(item.get("content") or item.get("description") or "").strip(),
                        str(item.get("publishedDate") or ""),
                        index,
                        metadata,
                    )
                )
        return result

    return normalize


def _normalizer(provider_id: str, provider: Any = None):
    if provider is not None:
        normalizer = getattr(provider, "normalizer", None) or getattr(provider, "normalize", None)
        if callable(normalizer):
            return normalizer
    builtins = {
        "brave": normalize_brave,
        "exa": normalize_exa,
        "tavily": normalize_tavily,
    }
    return builtins.get(provider_id) or _generic_normalizer(provider_id)


def normalize_provider_results(
    provider_id: str, raw_results: Sequence[Any], *, provider: Any | None = None
) -> list[Candidate]:
    """Normalize one provider's raw, ordered results into core candidates."""
    normalized = _normalizer(provider_id, provider)(raw_results)
    if not isinstance(normalized, (list, tuple)) or not all(isinstance(item, Candidate) for item in normalized):
        raise ValueError("provider normalizer must return candidates")
    return list(normalized)


async def search(
    query: str,
    policy: RetrievalPolicy | None = None,
    *,
    registry: Registry | None = None,
) -> RetrievalOutcome:
    """Search eligible direct roles in parallel, then fuse their candidates."""
    from ..providers.registry import ProviderAttempt, default_registry

    query = str(query or "").strip()
    if not query:
        return RetrievalOutcome(warnings=("query is required",))
    policy = policy or RetrievalPolicy.balanced()
    registry = registry or default_registry()
    providers = registry.search_providers(policy.providers)

    async def one(provider: Any) -> tuple[Any, Any]:
        try:
            return provider, await _invoke(provider, query, policy.max_results)
        except Exception as exc:
            return provider, exc

    outcomes = await asyncio.gather(*(one(provider) for provider in providers))
    candidates: list[Candidate] = []
    attempts: list[ProviderAttempt] = []
    for provider, value in outcomes:
        provider_id = str(getattr(provider, "provider_id", "provider"))
        if isinstance(value, BaseException):
            error_type, _error, _retryable = _classify(value)
            attempts.append(ProviderAttempt(provider_id, "search", "failed", error_type, safe_provider_message(error_type)))
            continue
        elapsed = 0.0
        try:
            ok, _payload_value, error_type, _error, elapsed = _payload(value)
            raw = _results(value)
            if not ok:
                attempts.append(ProviderAttempt(provider_id, "search", "failed", error_type or "provider_error", safe_provider_message(error_type), elapsed_ms=elapsed))
                continue
            normalized = normalize_provider_results(provider_id, raw, provider=provider)
            candidates.extend(normalized)
            attempts.append(ProviderAttempt(provider_id, "search", "complete", result_count=len(normalized), elapsed_ms=elapsed))
        except Exception as exc:
            classified_type, _message, _retryable = _classify(exc)
            attempts.append(ProviderAttempt(provider_id, "search", "failed", classified_type, safe_provider_message(classified_type), elapsed_ms=elapsed))

    ranked = reciprocal_rank_fusion(deduplicate_candidates(candidates))
    warnings: list[str] = []
    if policy.rerank and ranked:
        reranker = registry.reranker()
        if reranker is not None:
            documents = [f"{item.candidate.title} {item.candidate.snippet}".strip() or item.candidate.url for item in ranked]
            try:
                try:
                    raw = reranker.rerank(query, documents, policy.max_results)
                except TypeError:
                    raw = reranker.rerank(query, documents)
                raw = await raw if inspect.isawaitable(raw) else raw
                _ok, data, _error_type, _error, _elapsed = _payload(raw)
                entries = data.get("results") if isinstance(data, Mapping) else None
                scores = {int(item["index"]): float(item["relevance_score"]) for item in entries or []
                          if isinstance(item, Mapping) and isinstance(item.get("index"), int)
                          and not isinstance(item.get("index"), bool)
                          and isinstance(item.get("relevance_score"), (int, float))
                          and not isinstance(item.get("relevance_score"), bool)}
                if scores:
                    order = sorted(range(len(ranked)), key=lambda i: (-scores.get(i, float("-inf")), i))
                    ranked = [replace(ranked[i], rank=rank) for rank, i in enumerate(order)]
                else:
                    warnings.append("jina rerank unavailable; keeping RRF order")
            except Exception as exc:
                error_type, _error, _retryable = _classify(exc)
                warnings.append(f"jina rerank unavailable ({error_type}); keeping RRF order")
    return RetrievalOutcome(tuple(ranked[: policy.max_results]), tuple(attempts), tuple(p.provider_id for p in providers), tuple(warnings))


retrieve = search

__all__ = ["RetrievalOutcome", "normalize_provider_results", "search", "retrieve"]
