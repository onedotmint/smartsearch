"""Neutral capability runners shared by v1 workflows and v2 canonical operations.

Ownership: same-capability provider invocation, cache/budget/attempt lifecycle
via execute_capability. Provider adapters remain responsible for transport and
normalization. This module must not import CLI or the service facade.
"""

from __future__ import annotations

import sys
from typing import Any

from .capability_executor import CapabilityOperation, execute_capability
from .capability_taxonomy import is_content_fetch_success
from .provider_fetch_commands import (
    call_firecrawl_scrape as _default_call_firecrawl_scrape,
    call_tavily_extract as _default_call_tavily_extract,
    call_tavily_map as _default_call_tavily_map,
    jina_fetch as _default_jina_fetch,
)
from .provider_mcp_commands import (
    zhipu_mcp_reader as _default_zhipu_mcp_reader,
    zhipu_mcp_search as _default_zhipu_mcp_search,
)
from .provider_search_commands import (
    call_firecrawl_search as _default_call_firecrawl_search,
    call_tavily_search as _default_call_tavily_search,
    context7_library as _default_context7_library,
    exa_search as _default_exa_search,
    zhipu_search as _default_zhipu_search,
)
from .provider_vertical_commands import anysearch_search as _default_anysearch_search
from .runtime_cache import add_fetch
from .security import sanitize_text
from .service_support import _normalize_source_results


def _host_call(name: str, default):
    """Resolve provider callables through search_service when available.

    Existing v1 tests monkeypatch ``search_service.call_*`` / provider helpers.
    Prefer those symbols when the module is loaded so runner extraction does not
    break historical patch points. Fall back to direct provider-module defaults.
    """
    host = sys.modules.get("smart_search.search_service")
    if host is not None and hasattr(host, name):
        return getattr(host, name)
    return default


def _fetch_payload(
    *,
    content: Any,
    url: str,
    provider: str,
    error_type: Any = "",
    error: Any = "",
) -> dict[str, Any]:
    payload = {
        "content": sanitize_text(content or ""),
        "url": url,
        "provider": provider,
        "error_type": str(error_type or ""),
        "error": str(error or ""),
    }
    if payload["content"] and not payload["error_type"] and not is_content_fetch_success(payload):
        payload["error_type"] = "quality_error"
        payload["error"] = "fetch content failed the evidence quality gate"
    return payload


async def _run_web_fetch_fallback(
    url: str,
    fallback: str = "auto",
    preferred_order: list[str] | None = None,
    providers: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[dict]]:
    async def run_provider(provider: str, outcome: dict[str, Any]) -> dict[str, Any]:
        if provider == "tavily":
            content = await _host_call("call_tavily_extract", _default_call_tavily_extract)(url)
            return _fetch_payload(content=content, url=url, provider=provider)
        if provider == "jina":
            data = await _host_call("jina_fetch", _default_jina_fetch)(url)
            outcome.update(data if isinstance(data, dict) else {})
            return _fetch_payload(
                content=data.get("content") if isinstance(data, dict) and data.get("ok") else "",
                url=url,
                provider=provider,
                error_type=data.get("error_type", "") if isinstance(data, dict) else "protocol_error",
                error=data.get("error", "") if isinstance(data, dict) else "invalid Jina result",
            )
        if provider == "zhipu-mcp-reader":
            data = await _host_call("zhipu_mcp_reader", _default_zhipu_mcp_reader)(url)
            outcome.update(data if isinstance(data, dict) else {})
            return _fetch_payload(
                content=data.get("content") if isinstance(data, dict) and data.get("ok") else "",
                url=url,
                provider=provider,
                error_type=data.get("error_type", "") if isinstance(data, dict) else "protocol_error",
                error=data.get("error", "") if isinstance(data, dict) else "invalid MCP reader result",
            )
        content = await _host_call("call_firecrawl_scrape", _default_call_firecrawl_scrape)(url)
        return _fetch_payload(content=content, url=url, provider=provider)

    operation = CapabilityOperation(
        capability="web_fetch",
        input_value=url,
        cache_kind="fetch",
        cache_options={"format": "markdown"},
        run=run_provider,
        empty_value=lambda provider: {
            "content": "",
            "url": url,
            "provider": provider,
            "error_type": "budget_exhausted" if provider == "request-budget" else "",
            "error": "request budget exhausted" if provider == "request-budget" else "",
        },
        is_success=lambda value: isinstance(value, dict) and is_content_fetch_success(value),
        result_count=lambda _value: 1,
    )
    execution = await execute_capability(
        operation,
        providers=providers,
        fallback=fallback,
        preferred_order=preferred_order,
        reserve_fetch=add_fetch,
    )
    fetch_result = execution.value if isinstance(execution.value, dict) and execution.value.get("content") else None
    if fetch_result is not None:
        fetch_result = {"ok": True, **fetch_result}
    return fetch_result, execution.attempts

async def _run_web_search_fallback(
    query: str,
    count: int = 5,
    providers: str = "auto",
    fallback: str = "auto",
) -> tuple[list[dict], list[dict]]:
    async def run_provider(provider: str, outcome: dict[str, Any]) -> list[dict]:
        if provider == "zhipu":
            data = await _host_call("zhipu_search", _default_zhipu_search)(query, count=count)
            outcome.update(data if isinstance(data, dict) else {})
            return _normalize_source_results(data.get("results"), provider) if isinstance(data, dict) and data.get("ok") else []
        if provider == "zhipu-mcp":
            data = await _host_call("zhipu_mcp_search", _default_zhipu_mcp_search)(query, count=count)
            outcome.update(data if isinstance(data, dict) else {})
            return _normalize_source_results(data.get("results"), provider) if isinstance(data, dict) and data.get("ok") else []
        if provider == "tavily":
            return _normalize_source_results(await _host_call("call_tavily_search", _default_call_tavily_search)(query, count), provider)
        return _normalize_source_results(await _host_call("call_firecrawl_search", _default_call_firecrawl_search)(query, count), provider)

    operation = CapabilityOperation(
        capability="web_search",
        input_value=query,
        cache_options={"count": count},
        run=run_provider,
        empty_value=lambda _provider: [],
        is_success=lambda value: isinstance(value, list) and bool(value),
        result_count=lambda value: len(value) if isinstance(value, list) else 0,
    )
    execution = await execute_capability(
        operation,
        provider_filter=providers,
        fallback=fallback,
    )
    return execution.value if isinstance(execution.value, list) else [], execution.attempts

async def _run_docs_search_fallback(
    query: str,
    count: int = 5,
    providers: str = "auto",
    fallback: str = "auto",
) -> tuple[list[dict], list[dict]]:
    async def run_provider(provider: str, outcome: dict[str, Any]) -> list[dict]:
        if provider == "exa":
            data = await _host_call("exa_search", _default_exa_search)(query, num_results=count, include_highlights=True)
            outcome.update(data if isinstance(data, dict) else {})
            return _normalize_source_results(data.get("results"), provider) if isinstance(data, dict) and data.get("ok") else []
        data = await _host_call("context7_library", _default_context7_library)(query, query)
        outcome.update(data if isinstance(data, dict) else {})
        return [
            {
                "url": f"context7:{item.get('id')}",
                "title": item.get("title") or item.get("id") or "Context7",
                "description": item.get("description") or "",
                "provider": provider,
            }
            for item in data.get("results", [])
            if isinstance(data, dict) and data.get("ok") and item.get("id")
        ]

    operation = CapabilityOperation(
        capability="docs_search",
        input_value=query,
        cache_options={"include_highlights": True, "num_results": count},
        run=run_provider,
        empty_value=lambda _provider: [],
        is_success=lambda value: isinstance(value, list) and bool(value),
        result_count=lambda value: len(value) if isinstance(value, list) else 0,
    )
    execution = await execute_capability(
        operation,
        provider_filter=providers,
        fallback=fallback,
    )
    return execution.value if isinstance(execution.value, list) else [], execution.attempts

async def _run_vertical_search_fallback(
    query: str,
    providers: str = "auto",
    fallback: str = "auto",
) -> tuple[list[dict], list[dict]]:
    async def run_provider(provider: str, outcome: dict[str, Any]) -> list[dict]:
        data = await _host_call("anysearch_search", _default_anysearch_search)(query, max_results=5)
        outcome.update(data if isinstance(data, dict) else {})
        return _normalize_source_results(data.get("results"), provider) if isinstance(data, dict) and data.get("ok") else []

    operation = CapabilityOperation(
        capability="vertical_search",
        input_value=query,
        cache_options={"max_results": 5},
        run=run_provider,
        empty_value=lambda _provider: [],
        is_success=lambda value: isinstance(value, list) and bool(value),
        result_count=lambda value: len(value) if isinstance(value, list) else 0,
    )
    execution = await execute_capability(
        operation,
        provider_filter=providers,
        fallback=fallback,
    )
    return execution.value if isinstance(execution.value, list) else [], execution.attempts



async def _run_site_map(
    url: str,
    instructions: str = "",
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    timeout: int = 150,
) -> tuple[dict[str, Any] | None, list[dict]]:
    async def run_provider(provider: str, outcome: dict[str, Any]) -> dict[str, Any]:
        data = await _host_call("call_tavily_map", _default_call_tavily_map)(
            url,
            instructions,
            max_depth,
            max_breadth,
            limit,
            timeout,
        )
        if isinstance(data, dict):
            outcome.update(data)
            return data
        outcome.update({"error_type": "protocol_error", "error": "invalid site map result"})
        return {"ok": False, "results": [], "error_type": "protocol_error", "error": "invalid site map result"}

    operation = CapabilityOperation(
        capability="site_map",
        input_value=url,
        cache_kind="none",
        cache_options={
            "instructions": instructions,
            "max_depth": max_depth,
            "max_breadth": max_breadth,
            "limit": limit,
        },
        run=run_provider,
        empty_value=lambda provider: {
            "ok": False,
            "results": [],
            "error_type": "budget_exhausted" if provider == "request-budget" else "empty",
            "error": "request budget exhausted" if provider == "request-budget" else "site map returned no results",
        },
        is_success=lambda value: (
            isinstance(value, dict)
            and value.get("ok") is True
            and isinstance(value.get("results"), list)
            and bool(value["results"])
        ),
        result_count=lambda value: len(value.get("results") or []) if isinstance(value, dict) else 0,
    )
    execution = await execute_capability(
        operation,
        providers=["tavily"],
        fallback="off",
    )
    value = execution.value if isinstance(execution.value, dict) else None
    return value, execution.attempts


__all__ = [
    "_run_docs_search_fallback",
    "_run_site_map",
    "_run_vertical_search_fallback",
    "_run_web_fetch_fallback",
    "_run_web_search_fallback",
]
