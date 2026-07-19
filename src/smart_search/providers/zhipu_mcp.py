import json
import re
import time
from typing import Any

import httpx

from .base import ProviderResult, classify_provider_exception
from ..runtime_cache import current_context, request_client, request_timeout_kwargs


def _elapsed_ms(start: float) -> float:
    return round((time.time() - start) * 1000, 2)


def _error_payload(exc: Exception) -> dict[str, str]:
    error_type, error, _retryable = classify_provider_exception(exc)
    return {"error_type": error_type, "error": error}


def _mask_secret(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


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


def _parse_sse_or_json(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    data_lines = []
    for line in response.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    for line in reversed(data_lines):
        if not line or line == "[DONE]":
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return response.json()


def _parse_markdown_results(text: str, provider: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        heading = re.match(r"^#{2,4}\s+(?:\d+[.)]\s*)?(.+?)\s*$", line.strip())
        if heading:
            if current:
                results.append(current)
            current = {"title": heading.group(1).strip(), "url": "", "description": "", "provider": provider}
            continue
        url_match = re.search(r"https?://[^\s)>\]]+", line)
        if url_match:
            if current is None:
                current = {"title": url_match.group(0), "url": "", "description": "", "provider": provider}
            current["url"] = current.get("url") or url_match.group(0).rstrip(".,")
            continue
        if current is not None and line.strip() and not line.startswith("#"):
            current["description"] = (current.get("description", "") + " " + line.strip()).strip()
    if current:
        results.append(current)
    if results:
        return results
    urls = re.findall(r"https?://[^\s)>\]]+", text)
    return [{"title": url, "url": url, "description": "", "provider": provider} for url in dict.fromkeys(urls)]


def _content_error(text: str) -> tuple[str, str] | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if stripped.lower().startswith("mcp error"):
        lowered = stripped.lower()
        error_type = "auth_error" if "-401" in stripped or "api key" in lowered else "provider_error"
        return error_type, stripped
    decoded: Any = stripped
    for _ in range(2):
        if not isinstance(decoded, str):
            break
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError:
            break
    if isinstance(decoded, dict) and decoded.get("error"):
        return "provider_error", str(decoded.get("error"))
    return None


class ZhipuMCPProvider:
    capability_by_provider = {
        "zhipu-mcp": "web_search",
        "zhipu-mcp-reader": "web_fetch",
        "zhipu-mcp-zread": "zread",
    }

    def __init__(self, api_url: str, api_key: str, timeout: float = 30.0, provider_id: str = "zhipu-mcp"):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.provider_id = provider_id

    @property
    def capability(self) -> str:
        return self.capability_by_provider.get(self.provider_id, "web_search")

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx=None) -> ProviderResult:
        ctx = ctx or current_context()
        start = time.time()
        if not self.api_key:
            return ProviderResult.from_error(
                provider=self.provider_id,
                capability=self.capability,
                error_type="config_error",
                error="ZHIPU_MCP_API_KEY is not configured.",
                elapsed_ms=_elapsed_ms(start),
                data={"tool": name},
            )

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

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
                data = _parse_sse_or_json(response)
            output = self._normalize_response(name, arguments, data, start)
            if output.get("error"):
                output["error"] = _mask_secret(str(output["error"]), self.api_key)
            return ProviderResult.from_payload(
                output,
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
                data={"tool": name},
            )

    def _normalize_response(self, name: str, arguments: dict[str, Any], data: dict[str, Any], start: float) -> dict[str, Any]:
        if "error" in data:
            error = data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            return {
                "ok": False,
                "provider": self.provider_id,
                "tool": name,
                "error_type": "provider_error",
                "error": message or "Zhipu MCP JSON-RPC error",
                "elapsed_ms": _elapsed_ms(start),
            }

        result = data.get("result") or {}
        text = _extract_text(result)
        content_error = _content_error(text)
        is_error = bool(result.get("isError")) or bool(content_error)
        output: dict[str, Any] = {
            "ok": not is_error,
            "provider": self.provider_id,
            "tool": name,
            "content": text,
            "raw_content": text,
            "elapsed_ms": _elapsed_ms(start),
        }
        for key in ("query", "url", "repo", "path", "ref"):
            if arguments.get(key):
                output[key] = arguments[key]
        if name == "webReader":
            output["url"] = arguments.get("url", "")
        else:
            results = [] if is_error else _parse_markdown_results(text, self.provider_id)
            output["results"] = results
            output["total"] = len(results)
        if is_error:
            output["error_type"] = content_error[0] if content_error else "provider_error"
            output["error"] = content_error[1] if content_error else (text or "Zhipu MCP tool returned isError=true")
        return output

    async def web_search(self, query: str, count: int = 5, ctx=None) -> ProviderResult:
        del count
        return await self.call_tool("web_search_prime", {"search_query": query}, ctx=ctx)

    async def web_reader(self, url: str, ctx=None) -> ProviderResult:
        return await self.call_tool("webReader", {"url": url}, ctx=ctx)

    async def search_doc(self, repo: str, query: str, max_results: int = 5, ctx=None) -> ProviderResult:
        del max_results
        return await self.call_tool("search_doc", {"repo_name": repo, "query": query}, ctx=ctx)

    async def get_repo_structure(self, repo: str, ref: str = "", ctx=None) -> ProviderResult:
        arguments = {"repo_name": repo}
        if ref:
            arguments["dir_path"] = ref
        return await self.call_tool("get_repo_structure", arguments, ctx=ctx)

    async def read_file(self, repo: str, path: str, ref: str = "", ctx=None) -> ProviderResult:
        del ref
        arguments = {"repo_name": repo, "file_path": path}
        return await self.call_tool("read_file", arguments, ctx=ctx)
