import json
import re
import time
from typing import Any

import httpx

from .base import BaseSearchProvider, ProviderResult, classify_provider_exception
from ..runtime_cache import current_context, request_client, request_timeout_kwargs


def _error_payload(exc: Exception) -> dict[str, str]:
    error_type, error, _retryable = classify_provider_exception(exc)
    return {"error_type": error_type, "error": error}


def _extract_text(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _parse_markdown_results(text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        heading = re.match(r"^###\s+\d+\.\s+(.+?)\s*$", line)
        if heading:
            if current:
                results.append(current)
            current = {"title": heading.group(1).strip(), "url": "", "description": ""}
            continue
        if current is None:
            continue
        url_match = re.match(r"^-\s+\*\*URL\*\*:\s+(\S+)", line)
        if url_match:
            current["url"] = url_match.group(1).strip()
            continue
        if line.strip() and not line.startswith("#") and not line.startswith("- **URL**"):
            description = current.get("description", "")
            current["description"] = (description + " " + line.strip()).strip()
    if current:
        results.append(current)
    if results:
        return results
    urls = re.findall(r"https?://[^\s)>\]]+", text)
    return [{"title": url, "url": url, "description": ""} for url in dict.fromkeys(urls)]


def _split_domain(domain: str, sub_domain: str = "") -> tuple[str, str]:
    if sub_domain or "." not in domain:
        return domain, sub_domain
    parent, child = domain.split(".", 1)
    return parent, child


def _batch_query_object(query: str, max_results: int) -> dict[str, Any]:
    return {"query": query, "max_results": max_results}


class AnySearchProvider(BaseSearchProvider):
    provider_id = "anysearch"
    capability = "vertical_search"

    def __init__(self, api_url: str, api_key: str | None = None, timeout: float = 30.0):
        super().__init__(api_url.rstrip("/"), api_key or "")
        self.timeout = timeout

    def get_provider_name(self) -> str:
        return "AnySearch"

    async def search(self, query: str, max_results: int = 5, ctx=None) -> ProviderResult:
        return await self.call_tool("search", {"query": query, "max_results": max_results}, ctx=ctx)

    async def list_domains(self, domain: str = "", ctx=None) -> ProviderResult:
        arguments = {"domain": domain} if domain else {}
        return await self.call_tool("list_domains", arguments, ctx=ctx)

    async def vertical_search(
        self,
        query: str,
        domain: str = "",
        sub_domain: str = "",
        max_results: int = 5,
        ctx=None,
    ) -> ProviderResult:
        arguments: dict[str, Any] = {"query": query, "max_results": max_results}
        domain, sub_domain = _split_domain(domain, sub_domain)
        if domain:
            arguments["domain"] = domain
        if sub_domain:
            arguments["sub_domain"] = sub_domain
        return await self.call_tool("search", arguments, ctx=ctx)

    async def extract(self, url: str, max_length: int = 20000, ctx=None) -> ProviderResult:
        return await self.call_tool("extract", {"url": url, "max_length": max_length}, ctx=ctx)

    async def batch_search(self, queries: list[str], max_results: int = 3, ctx=None) -> ProviderResult:
        if len(queries) > 5:
            return self.error_result(
                "parameter_error",
                f"too many queries: {len(queries)} (max 5)",
                elapsed_ms=0,
                data={"tool": "batch_search"},
            )
        return await self.call_tool(
            "batch_search",
            {"queries": [_batch_query_object(query, max_results) for query in queries]},
            ctx=ctx,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx=None) -> ProviderResult:
        ctx = ctx or current_context()
        start = time.time()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            timeout = httpx.Timeout(connect=6.0, read=self.timeout, write=10.0, pool=None)
            async with request_client(ctx, timeout=timeout, follow_redirects=True) as client:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    **request_timeout_kwargs(self.timeout, ctx),
                )
                response.raise_for_status()
                data = response.json()
            output = self._normalize_response(name, arguments, data, start)
            return self.result(output)
        except Exception as e:
            error_type, error, retryable = classify_provider_exception(e)
            return self.error_result(
                error_type,
                error,
                retryable=retryable,
                elapsed_ms=round((time.time() - start) * 1000, 2),
                data={"tool": name},
            )

    def _normalize_response(self, name: str, arguments: dict[str, Any], data: dict[str, Any], start: float) -> dict[str, Any]:
        if "error" in data:
            error = data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            return {
                "ok": False,
                "provider": "anysearch",
                "tool": name,
                "error_type": "provider_error",
                "error": message or "AnySearch JSON-RPC error",
                "elapsed_ms": round((time.time() - start) * 1000, 2),
            }

        result = data.get("result") or {}
        text = _extract_text(result)
        is_error = bool(result.get("isError"))
        parsed_results = [] if is_error else _parse_markdown_results(text)
        if text and not is_error and not parsed_results:
            parsed_results = [
                {
                    "title": f"{name} structured evidence",
                    "url": "",
                    "description": text[:500],
                    "evidence_type": "structured",
                    "raw_content": text,
                }
            ]
        output: dict[str, Any] = {
            "ok": not is_error,
            "provider": "anysearch",
            "tool": name,
            "content": text,
            "raw_content": text,
            "results": parsed_results,
            "total": len(parsed_results),
            "elapsed_ms": round((time.time() - start) * 1000, 2),
        }
        for key in ("query", "domain", "sub_domain", "url"):
            if arguments.get(key):
                output[key] = arguments[key]
        if is_error:
            output["error_type"] = "provider_error"
            output["error"] = text or "AnySearch tool returned isError=true"
        return output
