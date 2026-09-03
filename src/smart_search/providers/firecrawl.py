"""Direct Firecrawl reader adapter used by the v1 reader fallback."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import ProviderResult, classify_provider_exception, read_response_bounded
from ..evidence_budget import DEFAULT_FETCH_TRANSPORT_LIMIT
from ..runtime_cache import current_context, request_client, request_timeout_kwargs
from ..security import safe_provider_message, sanitize_data


class FirecrawlReaderProvider:
    provider_id = "firecrawl"
    capability = "web_fetch"

    def __init__(self, api_url: str, api_key: str, timeout: float = 90.0):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout

    async def read(self, url: str, ctx=None) -> ProviderResult:
        start = time.time()
        if not self.api_key:
            return ProviderResult.from_error(
                provider=self.provider_id,
                capability=self.capability,
                error_type="config_error",
                error="Firecrawl API key is not configured",
                elapsed_ms=0,
                data=sanitize_data({"url": url}, secrets=(self.api_key,)),
            )
        try:
            ctx = ctx or current_context()
            async with request_client(ctx, timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.api_url}/scrape",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"url": url, "formats": ["markdown"]},
                    **request_timeout_kwargs(self.timeout, ctx),
                ) as response:
                    response.raise_for_status()
                    body = await read_response_bounded(response, DEFAULT_FETCH_TRANSPORT_LIMIT)
            payload = json.loads(body)
            data = payload.get("data") if isinstance(payload, dict) else None
            content = data.get("markdown") if isinstance(data, dict) else ""
            if not isinstance(content, str) or not content.strip():
                return ProviderResult.from_error(
                    provider=self.provider_id,
                    capability=self.capability,
                    error_type="empty",
                    error="Firecrawl returned no markdown content",
                    elapsed_ms=round((time.time() - start) * 1000, 2),
                    data=sanitize_data({"url": url}, secrets=(self.api_key,)),
                )
            return ProviderResult.from_content(
                content,
                provider=self.provider_id,
                capability=self.capability,
                elapsed_ms=round((time.time() - start) * 1000, 2),
                data=sanitize_data({"url": url}, secrets=(self.api_key,)),
            )
        except Exception as exc:
            error_type, _error, retryable = classify_provider_exception(exc)
            return ProviderResult.from_error(
                provider=self.provider_id,
                capability=self.capability,
                error_type=error_type,
                error=safe_provider_message(error_type),
                retryable=retryable,
                elapsed_ms=round((time.time() - start) * 1000, 2),
                data=sanitize_data({"url": url}, secrets=(self.api_key,)),
            )


__all__ = ["FirecrawlReaderProvider"]
