"""Fetch and site-map provider command ownership."""

import time
from typing import Any

import httpx

from .capability_service import (
    _command_capability_failure,
    _command_capability_metadata,
    _command_capability_preflight,
    _provider_availability,
    get_capability_status,
    validate_command_capabilities,
    validate_minimum_profile,
)
from .config import config
from .evidence import EvidenceBundle
from .logger import log_info
from .provider_command_support import decode_provider_json
from .providers.jina import JinaReaderProvider
from .runtime_cache import (
    add_retry,
    current_context,
    observe_command,
    observe_stage,
    request_client,
    request_timeout_kwargs,
)
from .service_support import (
    _capability_plan_from_result,
    _combined_degraded_reason,
    _elapsed_ms,
    _evidence_bundle_fields,
    _fallback_used,
)


async def call_tavily_extract(url: str) -> str | None:
    """Extract one URL through Tavily without owning fallback or caching."""
    availability = _provider_availability("tavily", "web_fetch")
    if not availability.get("eligible"):
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/extract"
    headers = {"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"}
    try:
        ctx = current_context()
        async with request_client(ctx, timeout=60.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json={"urls": [url], "format": "markdown"},
                **request_timeout_kwargs(60.0, ctx),
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if results:
                content = results[0].get("raw_content", "")
                return content if content and content.strip() else None
    except Exception:
        return None
    return None


async def call_firecrawl_scrape(url: str, ctx=None) -> str | None:
    """
    /*
     * ================================================================================
     * 步骤1：调用 Firecrawl 页面抓取
     * ================================================================================
     * 目标：保留动态页面抓取、重试和 request budget 行为。
     * 数据源：Firecrawl 配置、目标 URL 和当前 RequestContext。
     * 操作：
     * 1) 复用当前 command client 和 timeout。
     * 2) 每次重试先预留 retry budget。
     * 3) 只返回 markdown 正文，失败交给上层 capability fallback。
     * ================================================================================
     */
    """
    ctx = ctx or current_context()
    if not config.firecrawl_api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/scrape"
    headers = {"Authorization": f"Bearer {config.firecrawl_api_key}", "Content-Type": "application/json"}
    for attempt in range(config.retry_max_attempts):
        if attempt > 0 and not add_retry():
            return None
        try:
            async with request_client(ctx, timeout=90.0) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "url": url,
                        "formats": ["markdown"],
                        "timeout": 60000,
                        "waitFor": (attempt + 1) * 1500,
                    },
                    **request_timeout_kwargs(90.0, ctx),
                )
                response.raise_for_status()
                markdown = response.json().get("data", {}).get("markdown", "")
                if markdown and markdown.strip():
                    return markdown
                await log_info(
                    ctx,
                    f"Firecrawl: markdown为空, 重试 {attempt + 1}/{config.retry_max_attempts}",
                    config.debug_enabled,
                )
        except Exception as exc:
            await log_info(ctx, f"Firecrawl error: {exc}", config.debug_enabled)
            return None
    return None


async def call_jina_reader(url: str) -> dict[str, Any]:
    """Call Jina Reader and normalize its provider response."""
    raw = await JinaReaderProvider(
        config.jina_reader_api_url,
        config.jina_api_key,
        config.jina_respond_with,
        config.jina_timeout,
    ).fetch(url)
    return await decode_provider_json(raw, provider="jina", capability="web_fetch")


async def call_tavily_map(
    url: str,
    instructions: str = "",
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    timeout: int = 150,
) -> dict[str, Any]:
    """Call Tavily site-map directly; site-map commands remain uncached."""
    availability = _provider_availability("tavily", "site_map")
    if not availability.get("eligible"):
        reason = str(availability.get("reason") or "provider_not_eligible")
        return {
            "ok": False,
            "error_type": "config_error",
            "error": (
                "Tavily provider unavailable: "
                f"{reason}. 请运行 `smart-search setup`，或使用 `smart-search config set TAVILY_API_KEY <key>`。"
            ),
        }
    body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
    if instructions:
        body["instructions"] = instructions
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(
                f"{config.tavily_api_url.rstrip('/')}/map",
                headers={"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "ok": True,
                "base_url": data.get("base_url", ""),
                "results": data.get("results", []),
                "response_time": data.get("response_time", 0),
            }
    except httpx.TimeoutException:
        return {"ok": False, "error_type": "network_error", "error": f"映射超时: 请求超过{timeout}秒"}
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "error_type": "network_error",
            "error": f"HTTP错误: {exc.response.status_code} - {exc.response.text[:200]}",
        }
    except Exception as exc:
        return {"ok": False, "error_type": "network_error", "error": f"映射错误: {exc}"}


@observe_command
async def fetch(url: str) -> dict[str, Any]:
    """
    /*
     * ================================================================================
     * 步骤2：执行 fetch capability
     * ================================================================================
     * 目标：只校验 web_fetch，并把 provider fallback 交给 search workflow。
     * 数据源：capability registry、当前 minimum profile 和 fetch fallback。
     * 操作：
     * 1) 缺少 web_fetch 时返回稳定 config_error。
     * 2) 成功正文进入 EvidenceBundle，候选和 provider attempts 保持可见。
     * 3) 低层 provider command 不主动使用结果缓存。
     * ================================================================================
     */
    """
    from .search_service import _run_web_fetch_fallback

    start = time.time()
    minimum = validate_minimum_profile()
    if minimum.get("error_type") == "parameter_error":
        return {
            "ok": False,
            "url": url,
            "content": "",
            "error_type": "parameter_error",
            "error": minimum.get("error", "Invalid minimum profile"),
            "elapsed_ms": _elapsed_ms(start),
        }
    command_capabilities = validate_command_capabilities(
        "fetch",
        minimum_profile=minimum.get("profile", ""),
        capability_status=minimum.get("capability_status", {}),
    )
    capability_metadata = _command_capability_metadata(command_capabilities, minimum)
    execution_plan = _capability_plan_from_result("fetch", command_capabilities, response_mode="evidence")
    if not command_capabilities.get("ok"):
        evidence_bundle = EvidenceBundle()
        evidence_bundle.add_gap({"subquestion_id": "", "reason": "fetch 缺少 web_fetch 能力"})
        return {
            "ok": False,
            "url": url,
            "provider": "",
            "content": "",
            "error_type": command_capabilities.get("error_type", "config_error"),
            "error": command_capabilities.get("error", "fetch 缺少 web_fetch 能力"),
            "capability_execution_plan": execution_plan.to_dict(),
            **_evidence_bundle_fields(evidence_bundle),
            **capability_metadata,
            "elapsed_ms": _elapsed_ms(start),
        }

    with observe_stage("fetch.providers"):
        fetch_result, attempts = await _run_web_fetch_fallback(url)
    if fetch_result:
        evidence_bundle = EvidenceBundle()
        evidence_bundle.add_fetched_evidence(
            [
                {
                    "url": fetch_result.get("url") or url,
                    "provider": fetch_result.get("provider") or "",
                    "title": fetch_result.get("title") or fetch_result.get("url") or url,
                    "content": fetch_result.get("content") or "",
                    "source_type": "fetched_page",
                }
            ]
        )
        evidence_bundle.add_provider_attempts(attempts)
        evidence_fields = _evidence_bundle_fields(evidence_bundle)
        result = {
            **fetch_result,
            "provider_attempts": attempts,
            "fallback_used": _fallback_used(attempts),
            "sources": evidence_fields["evidence_bundle"]["sources"],
            "elapsed_ms": _elapsed_ms(start),
        }
        result.update(evidence_fields)
        result["capability_execution_plan"] = execution_plan.to_dict()
        result.update(capability_metadata)
        result["degraded"] = bool(result.get("degraded")) or evidence_bundle.degraded
        result["degraded_reason"] = _combined_degraded_reason(evidence_bundle, capability_metadata)
        return result

    fetch_capability = get_capability_status()["web_fetch"]
    if not fetch_capability.get("configured"):
        disabled_reasons = [
            str(item.get("reason"))
            for item in fetch_capability.get("provider_status", [])
            if item.get("configured") and not item.get("eligible")
        ]
        error = (
            "web_fetch provider unavailable: " + ", ".join(disabled_reasons)
            if disabled_reasons
            else "TAVILY_API_KEY、JINA_API_KEY、ZHIPU_MCP_API_KEY 和 FIRECRAWL_API_KEY 均未配置"
        )
        error_type = "config_error"
    else:
        error = "所有提取服务均未能获取内容"
        error_type = "network_error"
    if any(attempt.get("error_type") == "budget_exhausted" for attempt in attempts):
        error = "request budget exhausted"
        error_type = "budget_exhausted"
    evidence_bundle = EvidenceBundle()
    evidence_bundle.add_provider_attempts(attempts)
    evidence_bundle.add_gap({"subquestion_id": "", "reason": error})
    return {
        "ok": False,
        "url": url,
        "provider": "",
        "content": "",
        "error_type": error_type,
        "error": error,
        "provider_attempts": attempts,
        "fallback_used": _fallback_used(attempts),
        "capability_execution_plan": execution_plan.to_dict(),
        **capability_metadata,
        **_evidence_bundle_fields(evidence_bundle),
        "degraded": bool(capability_metadata.get("degraded")) or evidence_bundle.degraded,
        "degraded_reason": _combined_degraded_reason(evidence_bundle, capability_metadata),
        "elapsed_ms": _elapsed_ms(start),
    }


async def map_site(
    url: str,
    instructions: str = "",
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    timeout: int = 150,
) -> dict[str, Any]:
    """Validate site_map and delegate to the Tavily site-map transport."""
    start = time.time()
    minimum = validate_minimum_profile()
    if minimum.get("error_type") == "parameter_error":
        return {
            "ok": False,
            "url": url,
            "error_type": "parameter_error",
            "error": minimum.get("error", "Invalid minimum profile"),
            "elapsed_ms": _elapsed_ms(start),
        }
    command_capabilities = validate_command_capabilities(
        "map",
        minimum_profile=minimum.get("profile", ""),
        capability_status=minimum.get("capability_status", {}),
    )
    capability_metadata = _command_capability_metadata(command_capabilities, minimum)
    if not command_capabilities.get("ok"):
        return {
            "ok": False,
            "url": url,
            "error_type": command_capabilities.get("error_type", "config_error"),
            "error": command_capabilities.get("error", "map 缺少 site_map 能力"),
            **capability_metadata,
            "elapsed_ms": _elapsed_ms(start),
        }
    result = await call_tavily_map(url, instructions, max_depth, max_breadth, limit, timeout)
    result.setdefault("url", url)
    result.update(capability_metadata)
    result.setdefault("elapsed_ms", _elapsed_ms(start))
    return result


async def jina_fetch(url: str) -> dict[str, Any]:
    """Compatibility name for the Jina web_fetch command route."""
    return await call_jina_reader(url)


__all__ = [
    "call_firecrawl_scrape",
    "call_jina_reader",
    "call_tavily_extract",
    "call_tavily_map",
    "fetch",
    "jina_fetch",
    "map_site",
]
