"""Provider-owned connection checks used by doctor and provider probe."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .capability_service import PROVIDER_REGISTRY, _provider_availability, _provider_capabilities
from .config import config
from .provider_fetch_commands import call_firecrawl_scrape, jina_fetch
from .provider_mcp_commands import zhipu_mcp_reader, zhipu_mcp_repo_structure, zhipu_mcp_search
from .provider_search_commands import context7_library, zhipu_search
from .provider_vertical_commands import anysearch_domains
from .security import sanitize_text
from .service_support import _elapsed_ms


async def _test_exa_connection() -> dict[str, object]:
    """
    /*
     * ================================================================================
     * 步骤1：检查 Exa provider
     * ================================================================================
     * 目标：只负责 Exa connection probe，doctor 负责聚合结果。
     * 数据源：Exa 配置和一次最小 search 请求。
     * 操作：
     * 1) 未配置 key 时不发网络请求。
     * 2) 发送固定 test query。
     * 3) 只返回非敏感 status、message 和耗时。
     * ================================================================================
     */
    """
    if not config.exa_api_key:
        return {"status": "not_configured", "message": "EXA_API_KEY 未设置，Exa 搜索功能不可用"}
    start = time.time()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{config.exa_base_url.rstrip('/')}/search",
            headers={"x-api-key": config.exa_api_key, "content-type": "application/json"},
            json={"query": "test", "numResults": 1, "type": "keyword"},
        )
    response_time = _elapsed_ms(start)
    if response.status_code == 200:
        return {"status": "ok", "message": "Exa API 可用 (HTTP 200)", "response_time_ms": response_time}
    return {"status": "warning", "message": f"HTTP {response.status_code}", "response_time_ms": response_time}


async def _test_tavily_connection() -> dict[str, object]:
    """Probe Tavily while honoring its enabled/disabled capability state."""
    availability = _provider_availability("tavily")
    if not availability.get("configured"):
        return {"status": "not_configured", "message": "TAVILY_API_KEY 未设置，Tavily 功能不可用"}
    if not availability.get("enabled"):
        return {"status": "disabled", "message": str(availability.get("reason") or "TAVILY_ENABLED=false")}
    start = time.time()
    timeout = httpx.Timeout(connect=6.0, read=config.tavily_timeout, write=10.0, pool=None)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=config.ssl_verify_enabled) as client:
        response = await client.post(
            f"{config.tavily_api_url.rstrip('/')}/search",
            headers={"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"},
            json={"query": "test", "max_results": 1, "search_depth": "basic"},
        )
    response_time = _elapsed_ms(start)
    if response.status_code == 200:
        return {"status": "ok", "message": "Tavily API 可用 (HTTP 200)", "response_time_ms": response_time}
    return {"status": "warning", "message": f"HTTP {response.status_code}", "response_time_ms": response_time}


async def _test_jina_connection() -> dict[str, object]:
    """Probe Jina through the same web_fetch command boundary as fetch."""
    if config.jina_respond_with and not config.jina_api_key:
        return {"status": "config_error", "message": "JINA_RESPOND_WITH requires JINA_API_KEY"}
    if not config.jina_api_key:
        return {"status": "not_configured", "message": "JINA_API_KEY 未设置，Jina 不满足 standard web_fetch；匿名 Reader 只能作为显式实验使用"}
    start = time.time()
    data = await jina_fetch("https://example.com")
    response_time = _elapsed_ms(start)
    if data.get("ok"):
        return {"status": "ok", "message": "Jina Reader 可用", "response_time_ms": response_time}
    error_type = data.get("error_type", "")
    status = error_type if error_type in {"auth_error", "config_error", "parameter_error", "rate_limited", "timeout"} else "warning"
    return {"status": status, "message": data.get("error", "Jina Reader 不可用"), "response_time_ms": response_time}


async def _test_zhipu_connection() -> dict[str, object]:
    """Probe the Zhipu REST web_search owner."""
    if not config.zhipu_api_key:
        return {"status": "not_configured", "message": "ZHIPU_API_KEY 未设置，智谱搜索功能不可用"}
    result = await zhipu_search("test", count=1)
    if result.get("ok"):
        return {"status": "ok", "message": "智谱 Web Search 可用", "response_time_ms": result.get("elapsed_ms", 0)}
    return {"status": "warning", "message": result.get("error", "智谱 Web Search 不可用"), "response_time_ms": result.get("elapsed_ms", 0)}


async def _test_zhipu_mcp_connection() -> dict[str, object]:
    """Probe the separate Zhipu Coding Plan MCP search route."""
    if not config.zhipu_mcp_api_key:
        return {"status": "not_configured", "message": "ZHIPU_MCP_API_KEY 未设置，智谱 Coding Plan MCP 功能不可用"}
    result = await zhipu_mcp_search("test", count=1)
    if result.get("ok"):
        return {"status": "ok", "message": "智谱 Coding Plan MCP 可用", "response_time_ms": result.get("elapsed_ms", 0)}
    error_type = result.get("error_type", "")
    status = error_type if error_type in {"auth_error", "config_error", "provider_error", "rate_limited", "timeout"} else "warning"
    return {"status": status, "message": result.get("error", "智谱 Coding Plan MCP 不可用"), "response_time_ms": result.get("elapsed_ms", 0)}


async def _test_context7_connection() -> dict[str, object]:
    """Probe Context7 through the docs_search owner."""
    if not config.context7_api_key:
        return {"status": "not_configured", "message": "CONTEXT7_API_KEY 未设置，Context7 功能不可用"}
    result = await context7_library("react", "hooks")
    if result.get("ok"):
        return {"status": "ok", "message": "Context7 API 可用", "response_time_ms": result.get("elapsed_ms", 0)}
    return {"status": "warning", "message": result.get("error", "Context7 API 不可用"), "response_time_ms": result.get("elapsed_ms", 0)}


async def _test_zhipu_mcp_reader_connection() -> dict[str, object]:
    """Probe Zhipu MCP Reader independently from MCP search."""
    if not config.zhipu_mcp_api_key:
        return {"status": "not_configured", "message": "ZHIPU_MCP_API_KEY 未设置，智谱 MCP Reader 功能不可用"}
    result = await zhipu_mcp_reader("https://example.com")
    if result.get("ok"):
        return {"status": "ok", "message": "智谱 Coding Plan MCP Reader 可用", "response_time_ms": result.get("elapsed_ms", 0)}
    error_type = result.get("error_type", "")
    status = error_type if error_type in {"auth_error", "config_error", "provider_error", "rate_limited", "timeout"} else "warning"
    return {"status": status, "message": result.get("error", "智谱 Coding Plan MCP Reader 不可用"), "response_time_ms": result.get("elapsed_ms", 0)}


async def _test_firecrawl_connection() -> dict[str, object]:
    """Probe Firecrawl through its scrape command boundary."""
    if not config.firecrawl_api_key:
        return {"status": "not_configured", "message": "FIRECRAWL_API_KEY 未设置，Firecrawl 功能不可用"}
    start = time.time()
    try:
        content = await call_firecrawl_scrape("https://example.com")
    except Exception as exc:
        return {
            "status": "provider_error",
            "message": sanitize_text(str(exc)) or "Firecrawl scrape failed",
            "response_time_ms": _elapsed_ms(start),
        }
    response_time = _elapsed_ms(start)
    if content:
        return {"status": "ok", "message": "Firecrawl scrape 可用", "response_time_ms": response_time}
    return {"status": "warning", "message": "Firecrawl scrape 返回空内容", "response_time_ms": response_time}


async def _test_anysearch_connection() -> dict[str, object]:
    """Probe experimental AnySearch through its vertical domains boundary."""
    if not config.anysearch_api_key:
        return {"status": "not_configured", "message": "ANYSEARCH_API_KEY 未设置，AnySearch 功能不可用"}
    result = await anysearch_domains()
    if result.get("ok"):
        return {
            "status": "ok",
            "message": "AnySearch domains 可用",
            "response_time_ms": result.get("elapsed_ms", 0),
            "experimental": True,
        }
    error_type = result.get("error_type", "")
    status = error_type if error_type in {"auth_error", "config_error", "provider_error", "rate_limited", "timeout"} else "warning"
    return {
        "status": status,
        "message": result.get("error", "AnySearch 不可用"),
        "response_time_ms": result.get("elapsed_ms", 0),
        "experimental": True,
    }


async def _test_zhipu_mcp_zread_connection() -> dict[str, object]:
    """Probe experimental zread through a stable public repository structure call."""
    if not config.zhipu_mcp_api_key:
        return {"status": "not_configured", "message": "ZHIPU_MCP_API_KEY 未设置，智谱 zread 功能不可用"}
    result = await zhipu_mcp_repo_structure("octocat/Hello-World")
    if result.get("ok"):
        return {
            "status": "ok",
            "message": "智谱 Coding Plan MCP zread 可用",
            "response_time_ms": result.get("elapsed_ms", 0),
            "experimental": True,
        }
    error_type = result.get("error_type", "")
    status = error_type if error_type in {"auth_error", "config_error", "provider_error", "rate_limited", "timeout"} else "warning"
    return {
        "status": status,
        "message": result.get("error", "智谱 Coding Plan MCP zread 不可用"),
        "response_time_ms": result.get("elapsed_ms", 0),
        "experimental": True,
    }


PROVIDER_PROBE_REGISTRY: dict[str, dict[str, Any]] = {
    "xai-responses": {
        "family": "main_search",
        "probe_capability": "main_search",
        "probe_operation": "primary_responses",
        "adapter": None,
        "route_family": True,
    },
    "openai-compatible": {
        "family": "main_search",
        "probe_capability": "main_search",
        "probe_operation": "primary_connection",
        "adapter": None,
        "route_family": True,
    },
    "exa": {
        "family": "docs_search",
        "probe_capability": "docs_search",
        "probe_operation": "exa_search",
        "adapter": _test_exa_connection,
    },
    "context7": {
        "family": "docs_search",
        "probe_capability": "docs_search",
        "probe_operation": "context7_library",
        "adapter": _test_context7_connection,
    },
    "zhipu": {
        "family": "web_search",
        "probe_capability": "web_search",
        "probe_operation": "zhipu_search",
        "adapter": _test_zhipu_connection,
    },
    "zhipu-mcp": {
        "family": "web_search",
        "probe_capability": "web_search",
        "probe_operation": "zhipu_mcp_search",
        "adapter": _test_zhipu_mcp_connection,
    },
    "tavily": {
        "family": "web_search",
        "probe_capability": "web_search",
        "probe_operation": "tavily_search",
        "adapter": _test_tavily_connection,
    },
    "jina": {
        "family": "web_fetch",
        "probe_capability": "web_fetch",
        "probe_operation": "jina_fetch",
        "adapter": _test_jina_connection,
    },
    "zhipu-mcp-reader": {
        "family": "web_fetch",
        "probe_capability": "web_fetch",
        "probe_operation": "zhipu_mcp_reader",
        "adapter": _test_zhipu_mcp_reader_connection,
    },
    "firecrawl": {
        "family": "web_fetch",
        "probe_capability": "web_fetch",
        "probe_operation": "firecrawl_scrape",
        "adapter": _test_firecrawl_connection,
    },
    "anysearch": {
        "family": "vertical_search",
        "probe_capability": "vertical_search",
        "probe_operation": "anysearch_domains",
        "adapter": _test_anysearch_connection,
        "experimental": True,
    },
    "zhipu-mcp-zread": {
        "family": "zread",
        "probe_capability": "zread",
        "probe_operation": "zhipu_mcp_repo_structure",
        "adapter": _test_zhipu_mcp_zread_connection,
        "experimental": True,
    },
}


def known_probe_providers() -> list[str]:
    """Return real registry providers with explicit probe dispositions."""
    return sorted(
        provider
        for provider in PROVIDER_REGISTRY
        if provider != "main-search" and provider in PROVIDER_PROBE_REGISTRY
    )


def _normalize_probe_status(raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status") or "provider_error")
    allowed = {
        "ok",
        "not_configured",
        "disabled",
        "config_error",
        "auth_error",
        "rate_limited",
        "timeout",
        "network_error",
        "provider_error",
        "warning",
        "unsupported",
        "error",
    }
    if status == "error":
        status = "network_error"
    if status not in allowed:
        status = "provider_error"
    message = sanitize_text(str(raw.get("message") or ""))
    result = {
        "status": status,
        "message": message,
        "response_time_ms": raw.get("response_time_ms", 0),
    }
    if raw.get("route_id"):
        result["route_id"] = raw["route_id"]
    if raw.get("provider"):
        result["provider"] = raw["provider"]
    if raw.get("experimental"):
        result["experimental"] = True
    return result


def _error_type_for_status(status: str, *, network_attempted: bool) -> str:
    if status == "ok":
        return ""
    if status in {"not_configured", "disabled", "config_error", "unsupported"}:
        return "config_error"
    if network_attempted:
        return "network_error"
    return "config_error"


def provider_probe_base(provider: str) -> dict[str, Any]:
    """Local readiness metadata for a probe target before any network work."""
    provider_id = (provider or "").strip()
    if not provider_id or provider_id == "main-search" or provider_id not in PROVIDER_REGISTRY:
        return {
            "ok": False,
            "operation": "provider_probe",
            "provider": provider_id,
            "error_type": "parameter_error",
            "error": f"Unknown provider: {provider_id or '<empty>'}",
            "status": "provider_error",
            "network_behavior": "one_explicit_provider_probe",
            "network_attempted": False,
            "message": f"Unknown provider: {provider_id or '<empty>'}",
            "response_time_ms": 0,
        }

    disposition = PROVIDER_PROBE_REGISTRY.get(provider_id)
    if disposition is None:
        return {
            "ok": False,
            "operation": "provider_probe",
            "provider": provider_id,
            "capabilities": list(_provider_capabilities(provider_id)),
            "configured": False,
            "enabled": False,
            "eligible": False,
            "network_behavior": "one_explicit_provider_probe",
            "network_attempted": False,
            "status": "unsupported",
            "error_type": "config_error",
            "error": f"Provider {provider_id} has no safe low-cost probe",
            "message": f"Provider {provider_id} has no safe low-cost probe",
            "probe_capability": "",
            "probe_operation": "unsupported",
            "route_family": False,
            "response_time_ms": 0,
        }

    availability = _provider_availability(provider_id)
    base = {
        "operation": "provider_probe",
        "provider": provider_id,
        "capabilities": list(availability.get("capabilities") or _provider_capabilities(provider_id)),
        "configured": bool(availability.get("configured")),
        "enabled": bool(availability.get("enabled")),
        "eligible": bool(availability.get("eligible")),
        "network_behavior": "one_explicit_provider_probe",
        "probe_capability": disposition["probe_capability"],
        "probe_operation": disposition["probe_operation"],
        "experimental": bool(disposition.get("experimental") or PROVIDER_REGISTRY.get(provider_id, {}).get("experimental")),
        "route_family": bool(disposition.get("route_family")),
        "availability_reason": availability.get("reason") or "",
        "availability_error": availability.get("error") or "",
        "response_time_ms": 0,
    }
    if availability.get("route_ids") is not None:
        base["route_ids"] = list(availability.get("route_ids") or [])
    return base


async def run_probe_adapter(provider: str) -> dict[str, Any]:
    """Run the provider-owned adapter for a non-route-family provider."""
    disposition = PROVIDER_PROBE_REGISTRY.get(provider) or {}
    adapter = disposition.get("adapter")
    if adapter is None:
        return {
            "status": "unsupported",
            "message": f"Provider {provider} has no safe low-cost probe",
            "response_time_ms": 0,
        }
    try:
        return _normalize_probe_status(dict(await adapter()))
    except httpx.TimeoutException:
        return {"status": "timeout", "message": f"{provider} 请求超时", "response_time_ms": 0}
    except httpx.RequestError as exc:
        return {
            "status": "network_error",
            "message": sanitize_text(str(exc)) or f"{provider} 网络错误",
            "response_time_ms": 0,
        }
    except Exception as exc:
        return {
            "status": "provider_error",
            "message": sanitize_text(str(exc)) or f"{provider} 未知错误",
            "response_time_ms": 0,
        }


__all__ = [
    "PROVIDER_PROBE_REGISTRY",
    "_normalize_probe_status",
    "_error_type_for_status",
    "_test_context7_connection",
    "_test_exa_connection",
    "_test_jina_connection",
    "_test_tavily_connection",
    "_test_zhipu_connection",
    "_test_zhipu_mcp_connection",
    "known_probe_providers",
    "provider_probe_base",
    "run_probe_adapter",
]
