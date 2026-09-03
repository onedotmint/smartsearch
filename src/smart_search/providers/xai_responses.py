import json
import logging
import time
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt

from .base import BaseSearchProvider, ProviderResult, classify_provider_exception
from .openai_compatible import _WaitWithRetryAfter, _is_retryable_exception, get_local_time_info
from ..utils import _get_version
from ..config import config
from ..logger import log_info
from ..runtime_cache import (
    RequestBudgetExceeded,
    add_retry,
    current_context,
    request_client,
    request_timeout_kwargs,
)
from ..utils import get_prompt


_logger = logging.getLogger(__name__)
_ssl_warning_emitted = False


class XAIResponsesSearchProvider(BaseSearchProvider):
    provider_id = "xai-responses"
    capability = "main_search"

    def __init__(self, api_url: str, api_key: str, model: str = "grok-4-fast", tools: list[str] | None = None):
        super().__init__(api_url.rstrip("/"), api_key)
        self.model = model
        self.tools = tools or []

    def get_provider_name(self) -> str:
        return "xAI Responses"

    def _build_api_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"smart-search/{_get_version()}",
        }

    def _get_ssl_verify(self) -> bool:
        global _ssl_warning_emitted
        verify = config.ssl_verify_enabled
        if not verify and not _ssl_warning_emitted:
            _ssl_warning_emitted = True
            _logger.warning("SSL_VERIFY=false: xAI Responses API 请求已禁用 SSL 证书验证，存在安全风险")
        return verify

    def _build_search_payload(self, query: str, platform: str = "") -> dict[str, Any]:
        platform_prompt = ""
        if platform:
            platform_prompt = "\n\nYou should search the web for the information you need, and focus on these platform: " + platform + "\n"
        time_context = get_local_time_info() + "\n"
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": get_prompt("search"),
            "input": [{"role": "user", "content": time_context + query + platform_prompt}],
            "stream": False,
            "tools": [{"type": tool} for tool in self.tools],
        }
        return payload

    async def search(self, query: str, platform: str = "", ctx=None) -> ProviderResult:
        ctx = ctx or current_context()
        start = time.time()
        payload = self._build_search_payload(query, platform)
        await log_info(ctx, f"platform_prompt: {query}", config.debug_enabled)
        try:
            content = await self._execute_response_with_retry(self._build_api_headers(), payload, ctx)
            return self.content_result(
                content,
                elapsed_ms=round((time.time() - start) * 1000, 2),
                data={"model": self.model},
            )
        except Exception as exc:
            error_type, error, retryable = classify_provider_exception(exc)
            return self.error_result(
                error_type,
                error,
                elapsed_ms=round((time.time() - start) * 1000, 2),
                retryable=retryable,
                data={"model": self.model},
            )

    async def _execute_response_with_retry(self, headers: dict, payload: dict, ctx=None) -> str:
        timeout = httpx.Timeout(connect=6.0, read=120.0, write=10.0, pool=None)
        ctx = ctx or current_context()
        async with request_client(ctx, timeout=timeout, follow_redirects=True, verify=self._get_ssl_verify()) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=_WaitWithRetryAfter(config.retry_multiplier, config.retry_max_wait, ctx),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                if attempt.retry_state.attempt_number > 1:
                    if not add_retry():
                        raise RequestBudgetExceeded()
                with attempt:
                    response = await client.post(
                        f"{self.api_url}/responses",
                        headers=headers,
                        json=payload,
                        **request_timeout_kwargs(120.0, ctx),
                    )
                    response.raise_for_status()
                    return await self._parse_response(response, ctx)
        return ""

    async def _parse_response(self, response: httpx.Response, ctx=None) -> str:
        data = response.json()
        text_parts: list[str] = []
        sources: list[dict[str, str]] = []
        seen: set[str] = set()

        for item in data.get("output", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []) or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") != "output_text":
                    continue
                text = content.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
                for annotation in content.get("annotations", []) or []:
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    url = annotation.get("url")
                    if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
                        continue
                    seen.add(url)
                    source: dict[str, str] = {"url": url}
                    title = annotation.get("title")
                    if isinstance(title, str) and title.strip():
                        source["title"] = title.strip()
                    sources.append(source)

        answer = "\n\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if sources:
            answer = f"{answer}\n\nsources({json.dumps(sources, ensure_ascii=False)})".strip()

        await log_info(ctx, f"content: {answer}", config.debug_enabled)
        return answer
