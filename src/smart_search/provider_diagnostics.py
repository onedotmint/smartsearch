"""Provider-owned connection checks used by the doctor orchestrator."""

import time

import httpx

from .capability_service import _provider_availability
from .config import config
from .provider_fetch_commands import jina_fetch
from .provider_mcp_commands import zhipu_mcp_search
from .provider_search_commands import context7_library, zhipu_search
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
    return {"status": "warning", "message": f"HTTP {response.status_code}: {response.text[:100]}", "response_time_ms": response_time}


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
    return {"status": "warning", "message": f"HTTP {response.status_code}: {response.text[:100]}", "response_time_ms": response_time}


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


__all__ = [
    "_test_context7_connection",
    "_test_exa_connection",
    "_test_jina_connection",
    "_test_tavily_connection",
    "_test_zhipu_connection",
    "_test_zhipu_mcp_connection",
]
