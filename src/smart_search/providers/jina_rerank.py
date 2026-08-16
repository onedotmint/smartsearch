"""Small concrete Jina Reranker helper (v0.3.0 retrieval gateway).

Semantically separate from ``JinaReaderProvider``: the Reader remains the
fetch-only provider and gains no rerank responsibility, and this module never
fetches pages. The gateway consumes only the normalized success shape
``{ok, results: [{index, relevance_score}]}`` and never parses Jina-specific
structures itself.
"""

import time
from typing import Any

from .base import ProviderResult, classify_provider_exception
from ..config import config
from ..runtime_cache import current_context, request_client, request_timeout_kwargs


def _elapsed_ms(start: float) -> float:
    return round((time.time() - start) * 1000, 2)


async def rerank(
    query: str,
    documents: list[str],
    *,
    model: str | None = None,
    return_scores: bool = True,
    top_n: int | None = None,
    ctx=None,
) -> ProviderResult:
    """Rerank ``documents`` against ``query`` with the Jina Reranker API.

    Returns a ``ProviderResult`` with success shape
    ``{ok: true, results: [{index, relevance_score}]}`` where ``index`` refers
    to the input ``documents`` order. Missing ``JINA_API_KEY`` is a classified
    ``config_error`` before any network I/O; transport/HTTP/timeout/schema
    failures stay classified provider errors. Reranking is optional in the
    gateway: callers keep the RRF order on any failure.
    """
    start = time.time()
    if not config.jina_api_key:
        return ProviderResult.from_error(
            provider="jina",
            capability="rerank",
            error_type="config_error",
            error="JINA_API_KEY is required for reranking",
            retryable=False,
            elapsed_ms=_elapsed_ms(start),
        )

    ctx = ctx or current_context()
    endpoint = config.jina_rerank_api_url
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {config.jina_api_key}",
    }
    body: dict[str, Any] = {
        "model": model or config.jina_rerank_model,
        "query": query,
        "documents": documents,
    }
    if top_n is not None:
        body["top_n"] = int(top_n)
    if not return_scores:
        body["return_scores"] = False

    try:
        async with request_client(ctx, timeout=30.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=body,
                **request_timeout_kwargs(30.0, ctx),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Jina rerank response must be a JSON object")
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError("Jina rerank response results must be a list")
        normalized = [
            {
                "index": item.get("index"),
                "relevance_score": item.get("relevance_score"),
            }
            for item in results
            if isinstance(item, dict) and "index" in item
        ]
        return ProviderResult.from_payload(
            {
                "ok": True,
                "query": query,
                "results": normalized,
                "total": len(normalized),
                "elapsed_ms": _elapsed_ms(start),
            },
            provider="jina",
            capability="rerank",
        )
    except Exception as exc:
        error_type, error, retryable = classify_provider_exception(exc)
        return ProviderResult.from_error(
            provider="jina",
            capability="rerank",
            error_type=error_type,
            error=error.replace(config.jina_api_key, "***") if config.jina_api_key else error,
            retryable=retryable,
            elapsed_ms=_elapsed_ms(start),
        )
