"""Search and documentation provider command ownership."""

import time
from typing import Any

from .capability_service import (
    _command_capability_failure,
    _command_capability_preflight,
    _provider_availability,
)
from .config import config
from .provider_command_support import decode_provider_json
from .providers.context7 import Context7Provider
from .providers.exa import ExaSearchProvider
from .providers.zhipu import ZhipuWebSearchProvider
from .runtime_cache import current_context, request_client, request_timeout_kwargs
from .service_support import _normalize_domain_filter


async def call_tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    """
    /*
     * ================================================================================
     * 步骤1：调用 Tavily 搜索
     * ================================================================================
     * 目标：只负责 web_search transport，不负责 capability fallback 或缓存。
     * 数据源：Tavily 配置、查询文本和结果上限。
     * 操作：
     * 1) 检查 provider eligibility。
     * 2) 发起一次 uncached provider request。
     * 3) 转成 search workflow 使用的 source 字段。
     * ================================================================================
     */
    """
    availability = _provider_availability("tavily", "web_search")
    if not availability.get("eligible"):
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"}
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }
    try:
        ctx = current_context()
        async with request_client(ctx, timeout=90.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=body,
                **request_timeout_kwargs(90.0, ctx),
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                    "score": item.get("score", 0),
                }
                for item in results
            ] if results else None
    except Exception:
        return None


async def call_firecrawl_search(query: str, limit: int = 14) -> list[dict] | None:
    """Call Firecrawl search once for the web_search capability."""
    if not config.firecrawl_api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {config.firecrawl_api_key}", "Content-Type": "application/json"}
    try:
        ctx = current_context()
        async with request_client(ctx, timeout=90.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json={"query": query, "limit": limit},
                **request_timeout_kwargs(90.0, ctx),
            )
            response.raise_for_status()
            results = response.json().get("data", {}).get("web", [])
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                for item in results
            ] if results else None
    except Exception:
        return None


async def exa_search(
    query: str,
    num_results: int = 5,
    search_type: str = "neural",
    include_text: bool = False,
    include_highlights: bool = False,
    start_published_date: str = "",
    include_domains: str | list[str] | tuple[str, ...] = "",
    exclude_domains: str | list[str] | tuple[str, ...] = "",
    category: str = "",
) -> dict[str, Any]:
    """
    /*
     * ================================================================================
     * 步骤2：执行 Exa 文档搜索命令
     * ================================================================================
     * 目标：保留显式 exa-search 的 preflight、参数归一化和 uncached 语义。
     * 数据源：命令参数、EXA_API_KEY 和 Exa adapter。
     * 操作：
     * 1) 校验 named provider 与 docs_search capability。
     * 2) 归一化域名过滤器并调用 provider。
     * 3) 统一 provider payload 后补充 capability metadata。
     * ================================================================================
     */
    """
    start = time.time()
    preflight = _command_capability_preflight("exa-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    if not config.exa_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "EXA_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set EXA_API_KEY <key>`。",
        }
    provider = ExaSearchProvider(config.exa_base_url, config.exa_api_key, config.exa_timeout)
    raw = await provider.search(
        query=query,
        num_results=num_results,
        search_type=search_type,
        include_text=include_text,
        include_highlights=include_highlights,
        start_published_date=start_published_date or None,
        include_domains=_normalize_domain_filter(include_domains),
        exclude_domains=_normalize_domain_filter(exclude_domains),
        category=category or None,
    )
    result = await decode_provider_json(raw, provider="exa", capability="docs_search")
    result.update(preflight.get("metadata") or {})
    return result


async def exa_find_similar(url: str, num_results: int = 5) -> dict[str, Any]:
    """Run the explicit Exa similar-documents command without result caching."""
    start = time.time()
    preflight = _command_capability_preflight("exa-similar")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"url": url})
    if not config.exa_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "EXA_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set EXA_API_KEY <key>`。",
        }
    provider = ExaSearchProvider(config.exa_base_url, config.exa_api_key, config.exa_timeout)
    result = await decode_provider_json(
        await provider.find_similar(url=url, num_results=num_results),
        provider="exa",
        capability="docs_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def zhipu_search(
    query: str,
    count: int = 10,
    search_engine: str = "",
    search_recency_filter: str = "noLimit",
    search_domain_filter: str = "",
    content_size: str = "medium",
) -> dict[str, Any]:
    """Run the explicit Zhipu REST web-search command."""
    start = time.time()
    preflight = _command_capability_preflight("zhipu-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    if not config.zhipu_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "ZHIPU_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set ZHIPU_API_KEY <key>`。",
        }
    provider = ZhipuWebSearchProvider(
        config.zhipu_api_url,
        config.zhipu_api_key,
        search_engine or config.zhipu_search_engine,
        config.zhipu_timeout,
    )
    raw = await provider.search(
        query=query,
        count=count,
        search_engine=search_engine or None,
        search_recency_filter=search_recency_filter,
        search_domain_filter=search_domain_filter,
        content_size=content_size,
    )
    result = await decode_provider_json(raw, provider="zhipu", capability="web_search")
    result.update(preflight.get("metadata") or {})
    return result


async def context7_library(name: str, query: str = "") -> dict[str, Any]:
    """Resolve a Context7 library for the docs_search capability."""
    start = time.time()
    preflight = _command_capability_preflight("context7-library")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"name": name, "query": query})
    if not config.context7_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "CONTEXT7_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set CONTEXT7_API_KEY <key>`。",
        }
    provider = Context7Provider(config.context7_base_url, config.context7_api_key, config.context7_timeout)
    result = await decode_provider_json(
        await provider.library(name, query),
        provider="context7",
        capability="docs_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def context7_docs(library_id: str, query: str) -> dict[str, Any]:
    """Read Context7 documentation for an already resolved library."""
    start = time.time()
    preflight = _command_capability_preflight("context7-docs")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"library_id": library_id, "query": query})
    if not config.context7_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "CONTEXT7_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set CONTEXT7_API_KEY <key>`。",
        }
    provider = Context7Provider(config.context7_base_url, config.context7_api_key, config.context7_timeout)
    result = await decode_provider_json(
        await provider.docs(library_id, query),
        provider="context7",
        capability="docs_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


__all__ = [
    "call_firecrawl_search",
    "call_tavily_search",
    "context7_docs",
    "context7_library",
    "exa_find_similar",
    "exa_search",
    "zhipu_search",
]
