"""Direct Exa reader role backed by Exa's contents endpoint."""
from __future__ import annotations

import time
from typing import Any

from .base import ProviderResult, classify_provider_exception
from .exa import ExaSearchProvider
from ..security import safe_provider_message, sanitize_data


class ExaReaderProvider:
    provider_id = "exa"
    capability = "web_fetch"

    def __init__(self, api_url: str, api_key: str, timeout: float = 30.0):
        self.transport = ExaSearchProvider(api_url, api_key, timeout)

    async def read(self, url: str, ctx=None) -> ProviderResult:
        start = time.time()
        try:
            payload = await self.transport._request_with_retry(
                f"{self.transport.api_url.rstrip('/')}/contents",
                {"accept": "application/json", "content-type": "application/json", "x-api-key": self.transport.api_key},
                {"ids": [url]},
                ctx,
            )
            rows = payload.get("results") if isinstance(payload, dict) else None
            row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
            content = row.get("text") or row.get("content") or ""
            if not isinstance(content, str) or not content.strip():
                return ProviderResult.from_error(
                    provider=self.provider_id,
                    capability=self.capability,
                    error_type="empty",
                    error="Exa returned no content",
                    elapsed_ms=round((time.time() - start) * 1000, 2),
                    data=sanitize_data({"url": url}, secrets=(self.transport.api_key,)),
                )
            return ProviderResult.from_content(
                content,
                provider=self.provider_id,
                capability=self.capability,
                elapsed_ms=round((time.time() - start) * 1000, 2),
                data=sanitize_data(
                    {"url": url, "title": str(row.get("title") or "")},
                    secrets=(self.transport.api_key,),
                ),
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
                data=sanitize_data({"url": url}, secrets=(self.transport.api_key,)),
            )


__all__ = ["ExaReaderProvider"]
