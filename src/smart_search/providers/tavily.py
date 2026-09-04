"""Tavily direct search adapter for the v1 retrieval core.

Mirrors the Brave/Exa adapter lifecycle: one authenticated POST through the
shared request client with the shared retry budget, timeout/cancellation path,
and classified errors via ``classify_provider_exception``. Auth uses the
``Authorization: Bearer`` header. The module re-exports the pure
``normalize_tavily()`` normalizer as ``to_discovery_candidates`` so captured
raw ``results`` lists stay replayable offline without network I/O.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from .base import BaseSearchProvider, ProviderResult, classify_provider_exception
from ..core.normalizers import normalize_tavily as to_discovery_candidates
from ..config import config
from ..logger import log_info
from ..security import safe_provider_message, sanitize_data
from ..runtime_cache import (
    RequestBudgetExceeded,
    add_retry,
    bounded_retry_delay,
    current_context,
    request_client,
    request_timeout_kwargs,
)

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


def _normalize_result(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the discovery fields ``normalize_tavily()`` consumes."""
    out: dict[str, Any] = {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "content": item.get("content") or item.get("description") or "",
    }
    score = item.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        out["score"] = score
    return out


class TavilySearchProvider(BaseSearchProvider):
    provider_id = "tavily"
    capability = "web_search"
    normalizer = staticmethod(to_discovery_candidates)

    def __init__(self, api_url: str, api_key: str, timeout: float = 30.0):
        super().__init__(api_url, api_key)
        self.timeout = timeout

    def get_provider_name(self) -> str:
        return "Tavily"

    async def search(self, query: str, num_results: int = 5, *, ctx=None) -> ProviderResult:
        ctx = ctx or current_context()
        endpoint = f"{self.api_url.rstrip('/')}/search"
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        # Discovery-only v1 payload. No answer generation or raw content.
        payload = {
            "query": query,
            "max_results": num_results,
            "search_depth": "advanced",
            "include_raw_content": False,
            "include_answer": False,
        }

        await log_info(ctx, f"Tavily search: {query}", config.debug_enabled)

        start_time = time.time()
        try:
            data = await self._request_with_retry(endpoint, headers, payload, ctx)
            elapsed_ms = round((time.time() - start_time) * 1000, 2)

            results = data.get("results") if isinstance(data, Mapping) else None
            if not isinstance(results, list):
                raise ValueError("Tavily search response results must be a list")
            results = [_normalize_result(item) for item in results if isinstance(item, Mapping)]

            output = {
                "ok": True,
                "query": query,
                "results": results,
                "total": len(results),
                "elapsed_ms": elapsed_ms,
            }
            output = sanitize_data(output, secrets=(self.api_key,))
        except Exception as e:
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            error_type, _error_message, retryable = classify_provider_exception(e)
            output = {
                "ok": False,
                "query": query,
                "error_type": error_type,
                "error": safe_provider_message(error_type),
                "retryable": retryable,
                "elapsed_ms": elapsed_ms,
            }
            output = sanitize_data(output, secrets=(self.api_key,))

        await log_info(ctx, "Tavily search finished!", config.debug_enabled)
        return self.result(output, allow_empty=True)

    async def _request_with_retry(
        self, endpoint: str, headers: dict, payload: dict, ctx=None
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(connect=6.0, read=self.timeout, write=10.0, pool=None)

        ctx = ctx or current_context()
        base_wait = wait_random_exponential(multiplier=config.retry_multiplier, max=config.retry_max_wait)
        async with request_client(ctx, timeout=timeout) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=lambda retry_state: bounded_retry_delay(base_wait(retry_state), ctx),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                if attempt.retry_state.attempt_number > 1:
                    if not add_retry():
                        raise RequestBudgetExceeded()
                with attempt:
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        **request_timeout_kwargs(self.timeout, ctx),
                    )
                    response.raise_for_status()
                    return response.json()


__all__ = ["TavilySearchProvider"]
