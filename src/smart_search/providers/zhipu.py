import json
import time
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from .base import BaseSearchProvider, ProviderResult, classify_provider_exception
from ..config import config
from ..logger import log_info
from ..runtime_cache import add_retry


RETRYABLE_STATUS_CODES = {408, 500, 502, 503, 504}


def _is_retryable_exception(exc) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


def _normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title") or "",
        "url": item.get("link") or item.get("url") or "",
        "description": item.get("content") or "",
        "provider": "zhipu",
        "source": item.get("media") or "",
        "published_date": item.get("publish_date") or "",
        "icon": item.get("icon") or "",
        "refer": item.get("refer") or "",
    }


def _error_payload(exc: Exception) -> dict[str, Any]:
    error_type, error, _retryable = classify_provider_exception(exc)
    return {"error_type": error_type, "error": error}


class ZhipuWebSearchProvider(BaseSearchProvider):
    provider_id = "zhipu"
    capability = "web_search"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        search_engine: str = "search_std",
        timeout: float = 30.0,
    ):
        super().__init__(api_url.rstrip("/"), api_key)
        self.search_engine = search_engine
        self.timeout = timeout

    def get_provider_name(self) -> str:
        return "Zhipu Web Search"

    async def search(
        self,
        query: str,
        count: int = 10,
        search_engine: str | None = None,
        search_intent: bool = True,
        search_domain_filter: str = "",
        search_recency_filter: str = "noLimit",
        content_size: str = "medium",
        user_id: str = "",
        ctx=None,
    ) -> ProviderResult:
        endpoint = f"{self.api_url}/paas/v4/web_search"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload: dict[str, Any] = {
            "search_query": query[:70],
            "search_engine": search_engine or self.search_engine,
            "search_intent": search_intent,
            "count": count,
            "search_recency_filter": search_recency_filter,
            "content_size": content_size,
        }
        if search_domain_filter:
            payload["search_domain_filter"] = search_domain_filter
        if user_id:
            payload["user_id"] = user_id

        await log_info(ctx, f"Zhipu search: {query}", config.debug_enabled)
        start_time = time.time()
        try:
            data = await self._request_with_retry(endpoint, headers, payload)
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            results = [_normalize_result(item) for item in data.get("search_result", []) or []]
            output = {
                "ok": True,
                "query": query,
                "provider": "zhipu",
                "search_engine": payload["search_engine"],
                "results": results,
                "total": len(results),
                "search_intent": data.get("search_intent", []),
                "request_id": data.get("request_id", ""),
                "elapsed_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            error_type, error_message, retryable = classify_provider_exception(e)
            output = {
                "ok": False,
                "query": query,
                "provider": "zhipu",
                "error_type": error_type,
                "error": error_message,
                "retryable": retryable,
                "elapsed_ms": elapsed_ms,
            }
        return self.result(output)

    async def _request_with_retry(self, endpoint: str, headers: dict, payload: dict) -> dict[str, Any]:
        timeout = httpx.Timeout(connect=6.0, read=self.timeout, write=10.0, pool=None)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=wait_random_exponential(multiplier=config.retry_multiplier, max=config.retry_max_wait),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                if attempt.retry_state.attempt_number > 1:
                    add_retry()
                with attempt:
                    response = await client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    return response.json()
        return {}
