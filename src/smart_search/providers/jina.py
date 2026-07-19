import json
import time
from typing import Any

import httpx

from .base import ProviderResult, classify_provider_exception
from ..runtime_cache import current_context, request_client, request_timeout_kwargs


CHALLENGE_MARKERS = (
    "title: just a moment",
    "checking if the site connection is secure",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
)


def _elapsed_ms(start: float) -> float:
    return round((time.time() - start) * 1000, 2)


def _error_payload(exc: Exception) -> dict[str, str]:
    error_type, error, _retryable = classify_provider_exception(exc)
    return {"error_type": error_type, "error": error}


def _mask_secret(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _quality_error(content: str) -> str:
    lower = content.strip().lower()
    if not lower:
        return "empty response"
    for marker in CHALLENGE_MARKERS:
        if marker in lower:
            return f"low-quality challenge page detected: {marker}"
    return ""


class JinaReaderProvider:
    provider_id = "jina"
    capability = "web_fetch"

    def __init__(
        self,
        reader_api_url: str,
        api_key: str | None = None,
        respond_with: str = "",
        timeout: float = 30.0,
    ):
        self.reader_api_url = reader_api_url.rstrip("/")
        self.api_key = api_key or ""
        self.respond_with = respond_with.strip()
        self.timeout = timeout

    async def fetch(self, url: str, ctx=None) -> ProviderResult:
        ctx = ctx or current_context()
        start = time.time()
        if self.respond_with and not self.api_key:
            return ProviderResult.from_error(
                provider=self.provider_id,
                capability=self.capability,
                error_type="config_error",
                error="JINA_RESPOND_WITH requires JINA_API_KEY.",
                elapsed_ms=_elapsed_ms(start),
                data={"url": url},
            )

        headers = {"X-Return-Format": "markdown", "Accept": "text/plain, text/markdown, */*"}
        if self.respond_with:
            headers["X-Respond-With"] = self.respond_with
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        endpoint = f"{self.reader_api_url}/{url}"
        try:
            timeout = httpx.Timeout(connect=6.0, read=self.timeout, write=10.0, pool=None)
            async with request_client(ctx, timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    endpoint,
                    headers=headers,
                    **request_timeout_kwargs(self.timeout, ctx),
                )
                response.raise_for_status()
            content = response.text.strip()
            quality_error = _quality_error(content)
            if quality_error:
                return ProviderResult.from_error(
                    provider=self.provider_id,
                    capability=self.capability,
                    error_type="quality_error",
                    error=quality_error,
                    elapsed_ms=_elapsed_ms(start),
                    data={"url": url, "content": content},
                )
            return ProviderResult.from_payload(
                {
                    "ok": True,
                    "provider": self.provider_id,
                    "capability": self.capability,
                    "url": url,
                    "content": content,
                    "elapsed_ms": _elapsed_ms(start),
                },
                provider=self.provider_id,
                capability=self.capability,
            )
        except Exception as e:
            error_type, error, retryable = classify_provider_exception(e)
            return ProviderResult.from_error(
                provider=self.provider_id,
                capability=self.capability,
                error_type=error_type,
                error=_mask_secret(error, self.api_key),
                retryable=retryable,
                elapsed_ms=_elapsed_ms(start),
                data={"url": url},
            )
