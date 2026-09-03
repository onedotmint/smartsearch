"""Brave Search provider adapter + DiscoveryCandidate normalizer (v0.3.0).

Mirrors the Exa adapter lifecycle: httpx timeout, tenacity retry on retryable
status codes, shared request client, retry budget, and classified errors via
``classify_provider_exception``. Auth uses the ``X-Subscription-Token``
header. ``freshness``/``country``/``language`` are sent ONLY when explicitly
given — Brave language stays unrestricted by default and there is no language
detection in v0.3.0.
"""

import time
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from .base import BaseSearchProvider, ProviderResult, classify_provider_exception
from ..core.models import Candidate
from ..config import config
from ..logger import log_info
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


def _normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "description": item.get("description") or "",
        "provider": "brave",
    }
    if item.get("age"):
        out["age"] = item["age"]
    if item.get("language"):
        out["language"] = item["language"]
    if item.get("family_friendly") is not None:
        out["family_friendly"] = item["family_friendly"]
    if item.get("page_age"):
        out["page_age"] = item["page_age"]
    return out


def to_discovery_candidates(payload: Any) -> list[Candidate]:
    """Map a Brave result payload to ``DiscoveryCandidate`` values.

    Accepts the normalized ``call_brave_search`` payload (``{ok, results,
    ...}``) or a plain results list. ``provider_rank`` is the item index;
    native fields (``age``, ``language``, ``family_friendly``, ``page_age``)
    are kept in ``metadata`` for diagnostics only and never become a shared
    ranking signal. ``DiscoveryCandidate`` is imported lazily to avoid an
    import cycle with the retrieval core.
    """
    results = payload.get("results") if isinstance(payload, dict) else payload
    candidates: list[Candidate] = []
    for index, item in enumerate(results or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        metadata: dict[str, Any] = {}
        for key in ("age", "language", "family_friendly", "page_age"):
            if item.get(key) is not None:
                metadata[key] = item[key]
        candidates.append(
                Candidate(
                url=url,
                title=title,
                provider="brave",
                snippet=str(item.get("description") or "").strip(),
                provider_rank=index,
                metadata=metadata,
            )
        )
    return candidates


class BraveSearchProvider(BaseSearchProvider):
    provider_id = "brave"
    capability = "web_search"
    normalizer = staticmethod(to_discovery_candidates)

    def __init__(self, api_url: str, api_key: str, timeout: float = 30.0):
        super().__init__(api_url, api_key)
        self.timeout = timeout

    def get_provider_name(self) -> str:
        return "Brave"

    async def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        freshness: str | None = None,
        country: str | None = None,
        language: str | None = None,
        ctx=None,
    ) -> ProviderResult:
        ctx = ctx or current_context()
        endpoint = f"{self.api_url.rstrip('/')}/web/search"
        headers = {
            "accept": "application/json",
            "x-subscription-token": self.api_key,
        }
        params: dict[str, Any] = {
            "q": query,
            "count": num_results,
        }
        # Explicit parameters only. No defaults and no language detection:
        # when none of these is given, Brave keeps its unrestricted default
        # language behavior.
        if freshness is not None:
            params["freshness"] = str(freshness)
        if country is not None:
            params["country"] = str(country)
        if language is not None:
            params["language"] = str(language)

        await log_info(ctx, f"Brave search: {query}", config.debug_enabled)

        start_time = time.time()
        try:
            data = await self._request_with_retry(endpoint, headers, params, ctx)
            elapsed_ms = round((time.time() - start_time) * 1000, 2)

            web = data.get("web") if isinstance(data, dict) else None
            if not isinstance(web, dict) or not isinstance(web.get("results"), list):
                raise ValueError("Brave search response web.results must be a list")
            results = [_normalize_result(item) for item in web.get("results", [])]

            output = {
                "ok": True,
                "query": query,
                "results": results,
                "total": len(results),
                "elapsed_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            error_type, error_message, retryable = classify_provider_exception(e)
            output = {
                "ok": False,
                "query": query,
                "error_type": error_type,
                "error": error_message,
                "retryable": retryable,
                "elapsed_ms": elapsed_ms,
            }

        await log_info(ctx, "Brave search finished!", config.debug_enabled)
        return self.result(output)

    async def _request_with_retry(
        self, endpoint: str, headers: dict, params: dict, ctx=None
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
                    response = await client.get(
                        endpoint,
                        headers=headers,
                        params=params,
                        **request_timeout_kwargs(self.timeout, ctx),
                    )
                    response.raise_for_status()
                    return response.json()
