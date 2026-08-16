"""Search and documentation provider command ownership."""

import time
from typing import Any

from .capability_service import (
    _capability_preflight,
    _command_capability_failure,
    _provider_availability,
)
from .config import config
from .logger import logger
from .provider_command_support import decode_provider_json
from .providers.base import ProviderError, classify_provider_exception
from .providers.brave import BraveSearchProvider
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
    logger.info("步骤1开始：调用 Tavily 搜索")
    try:
        # 1.1 检查 provider eligibility，未配置时保持空结果语义。
        availability = _provider_availability("tavily", "web_search")
        if not availability.get("eligible"):
            logger.info("步骤1结束：Tavily 搜索未进入调用链")
            return None

        # 1.2 发起请求并校验结果结构。
        endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
        headers = {"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"}
        body = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_raw_content": False,
            "include_answer": False,
        }
        ctx = current_context()
        async with request_client(ctx, timeout=90.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=body,
                **request_timeout_kwargs(90.0, ctx),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results", []), list):
            raise ValueError("Tavily search response results must be a list")
        results = payload.get("results", [])
        normalized = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0),
            }
            for item in results
        ]
        logger.info("步骤1结束：Tavily 搜索完成，结果数=%s", len(normalized))
        return normalized or None
    except ProviderError:
        logger.info("步骤1结束：Tavily 搜索返回已分类异常")
        raise
    except Exception as exc:
        error_type, error, retryable = classify_provider_exception(exc)
        logger.info("步骤1结束：Tavily 搜索异常，error_type=%s", error_type)
        raise ProviderError(
            error_type,
            error,
            provider="tavily",
            capability="web_search",
            retryable=retryable,
        ) from exc


async def call_firecrawl_search(query: str, limit: int = 14) -> list[dict] | None:
    """
    /*
     * ================================================================================
     * 步骤2：调用 Firecrawl 搜索
     * ================================================================================
     * 目标：保留 web_search 的传输异常，让上层 attempt 区分空结果和 provider 错误。
     * 数据源：Firecrawl 配置、查询文本和结果上限。
     * 操作：
     * 1) 发起一次 uncached provider request。
     * 2) 校验响应结构并返回标准 source 字段。
     * ================================================================================
     */
    """
    logger.info("步骤2开始：调用 Firecrawl 搜索")
    try:
        # 2.1 检查配置并构造请求。
        if not config.firecrawl_api_key:
            logger.info("步骤2结束：Firecrawl 搜索未配置")
            return None
        endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
        headers = {"Authorization": f"Bearer {config.firecrawl_api_key}", "Content-Type": "application/json"}
        ctx = current_context()
        async with request_client(ctx, timeout=90.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json={"query": query, "limit": limit},
                **request_timeout_kwargs(90.0, ctx),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError("Firecrawl search response data must be an object")
        results = payload["data"].get("web", [])
        if not isinstance(results, list):
            raise ValueError("Firecrawl search response data.web must be a list")
        normalized = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                for item in results
            ]
        logger.info("步骤2结束：Firecrawl 搜索完成，结果数=%s", len(normalized))
        return normalized or None
    except ProviderError:
        logger.info("步骤2结束：Firecrawl 搜索返回已分类异常")
        raise
    except Exception as exc:
        error_type, error, retryable = classify_provider_exception(exc)
        logger.info("步骤2结束：Firecrawl 搜索异常，error_type=%s", error_type)
        raise ProviderError(
            error_type,
            error,
            provider="firecrawl",
            capability="web_search",
            retryable=retryable,
        ) from exc


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
     * 目标：保留显式 docs_search 的 preflight、参数归一化和 uncached 语义。
     * 数据源：命令参数、EXA_API_KEY 和 Exa adapter。
     * 操作：
     * 1) 校验 named provider 与 docs_search capability。
     * 2) 归一化域名过滤器并调用 provider。
     * 3) 统一 provider payload 后补充 capability metadata。
     * ================================================================================
     */
    """
    start = time.time()
    preflight = _capability_preflight("docs_search", provider="exa")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    if not config.exa_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "EXA_API_KEY 未配置。请运行 `smart-search config set EXA_API_KEY <key>`。",
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


async def call_brave_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Explicit Brave web-search command wrapper (v0.3.0 retrieval gateway).

    Validates the named provider and the ``web_search`` capability before any
    network I/O, then returns the normalized ProviderResult payload
    (``{ok, query, results, total, elapsed_ms}``) with classified errors and
    capability metadata.
    """
    start = time.time()
    preflight = _capability_preflight("web_search", provider="brave")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    if not config.brave_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "BRAVE_API_KEY 未配置。请运行 `smart-search config set BRAVE_API_KEY <key>`。",
        }
    provider = BraveSearchProvider(config.brave_api_url, config.brave_api_key, config.brave_timeout)
    raw = await provider.search(query=query, num_results=max_results)
    result = await decode_provider_json(raw, provider="brave", capability="web_search")
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
    preflight = _capability_preflight("web_search", provider="zhipu")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    if not config.zhipu_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "ZHIPU_API_KEY 未配置。请运行 `smart-search config set ZHIPU_API_KEY <key>`。",
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
    preflight = _capability_preflight("docs_search", provider="context7")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"name": name, "query": query})
    if not config.context7_api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "CONTEXT7_API_KEY 未配置。请运行 `smart-search config set CONTEXT7_API_KEY <key>`。",
        }
    provider = Context7Provider(config.context7_base_url, config.context7_api_key, config.context7_timeout)
    result = await decode_provider_json(
        await provider.library(name, query),
        provider="context7",
        capability="docs_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


__all__ = [
    "call_brave_search",
    "call_firecrawl_search",
    "call_tavily_search",
    "context7_library",
    "exa_search",
    "zhipu_search",
]
