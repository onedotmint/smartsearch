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
from .logger import log_info, logger
from .provider_command_support import decode_provider_json
from .providers.base import ProviderError, classify_provider_exception
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
    """
    /*
     * ================================================================================
     * 步骤1：调用 Tavily 页面提取
     * ================================================================================
     * 目标：保留 web_fetch 的传输异常，让 capability fallback 区分空内容和 provider 错误。
     * 数据源：Tavily 配置、目标 URL 和当前 RequestContext。
     * 操作：
     * 1) 检查 provider eligibility。
     * 2) 发起一次 uncached provider request。
     * 3) 只返回非空 markdown 正文。
     * ================================================================================
     */
    """
    logger.info("步骤1开始：调用 Tavily 页面提取")
    try:
        # 1.1 检查 provider eligibility，未配置时保持空结果语义。
        availability = _provider_availability("tavily", "web_fetch")
        if not availability.get("eligible"):
            logger.info("步骤1结束：Tavily 页面提取未进入调用链")
            return None

        # 1.2 发起请求并校验结果结构。
        endpoint = f"{config.tavily_api_url.rstrip('/')}/extract"
        headers = {"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"}
        ctx = current_context()
        async with request_client(ctx, timeout=60.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json={"urls": [url], "format": "markdown"},
                **request_timeout_kwargs(60.0, ctx),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results", []), list):
            raise ValueError("Tavily extract response results must be a list")
        results = payload.get("results", [])
        content = results[0].get("raw_content", "") if results and isinstance(results[0], dict) else ""
        normalized = content if isinstance(content, str) and content.strip() else None
        logger.info("步骤1结束：Tavily 页面提取完成，has_content=%s", bool(normalized))
        return normalized
    except ProviderError:
        logger.info("步骤1结束：Tavily 页面提取返回已分类异常")
        raise
    except Exception as exc:
        error_type, error, retryable = classify_provider_exception(exc)
        logger.info("步骤1结束：Tavily 页面提取异常，error_type=%s", error_type)
        raise ProviderError(
            error_type,
            error,
            provider="tavily",
            capability="web_fetch",
            retryable=retryable,
        ) from exc


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
    logger.info("步骤2开始：调用 Firecrawl 页面抓取")
    ctx = ctx or current_context()
    try:
        # 2.1 读取配置并确定本次抓取的尝试上限。
        if not config.firecrawl_api_key:
            logger.info("步骤2结束：Firecrawl 页面抓取未配置")
            return None
        endpoint = f"{config.firecrawl_api_url.rstrip('/')}/scrape"
        headers = {"Authorization": f"Bearer {config.firecrawl_api_key}", "Content-Type": "application/json"}
        attempt_limit = max(1, config.retry_max_attempts)

        # 2.2 对空内容和可重试传输/服务端异常继续尝试。
        for attempt in range(attempt_limit):
            if attempt > 0 and not add_retry():
                logger.info("步骤2结束：Firecrawl 页面抓取达到 retry budget")
                raise ProviderError(
                    "budget_exhausted",
                    "retry budget exhausted",
                    provider="firecrawl",
                    capability="web_fetch",
                    retryable=False,
                )
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
                    payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                    raise ValueError("Firecrawl scrape response data must be an object")
                markdown = payload["data"].get("markdown", "")
                if markdown and isinstance(markdown, str) and markdown.strip():
                    logger.info("步骤2结束：Firecrawl 页面抓取完成，attempt=%s", attempt + 1)
                    return markdown
                await log_info(
                    ctx,
                    f"Firecrawl: markdown为空, 重试 {attempt + 1}/{attempt_limit}",
                    config.debug_enabled,
                )
            except Exception as exc:
                error_type, error, retryable = classify_provider_exception(exc)
                if not retryable or attempt + 1 >= attempt_limit:
                    logger.info("步骤2结束：Firecrawl 页面抓取失败，error_type=%s", error_type)
                    raise ProviderError(
                        error_type,
                        error,
                        provider="firecrawl",
                        capability="web_fetch",
                        retryable=retryable,
                    ) from exc
                await log_info(
                    ctx,
                    f"Firecrawl error ({error_type}), 重试 {attempt + 1}/{attempt_limit}: {error}",
                    config.debug_enabled,
                )
        logger.info("步骤2结束：Firecrawl 页面抓取返回空内容")
        return None
    except ProviderError:
        raise
    except Exception as exc:
        error_type, error, retryable = classify_provider_exception(exc)
        logger.info("步骤2结束：Firecrawl 页面抓取异常，error_type=%s", error_type)
        raise ProviderError(
            error_type,
            error,
            provider="firecrawl",
            capability="web_fetch",
            retryable=retryable,
        ) from exc


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
    """
    /*
     * ================================================================================
     * 步骤3：调用 Tavily site map
     * ================================================================================
     * 目标：区分成功、空结果、超时、HTTP 和协议错误，保持 map 命令不缓存。
     * 数据源：Tavily 配置、站点 URL、深度/广度限制和超时参数。
     * 操作：
     * 1) 校验 provider eligibility 并构造请求体。
     * 2) 解析响应并对空结果返回稳定 empty 结构。
     * 3) 将异常按共享 provider 错误协议分类。
     * ================================================================================
     */
    """
    logger.info("步骤3开始：调用 Tavily site map")
    try:
        # 3.1 校验 provider eligibility 并构造请求体。
        availability = _provider_availability("tavily", "site_map")
        if not availability.get("eligible"):
            reason = str(availability.get("reason") or "provider_not_eligible")
            logger.info("步骤3结束：Tavily site map 未配置")
            return {
                "ok": False,
                "error_type": "config_error",
                "error": (
                    "Tavily provider unavailable: "
                    f"{reason}. 请运行 `smart-search setup`，或使用 `smart-search config set TAVILY_API_KEY <key>`。"
                ),
                "retryable": False,
            }
        body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
        if instructions:
            body["instructions"] = instructions

        # 3.2 发起请求并校验结果结构。
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(
                f"{config.tavily_api_url.rstrip('/')}/map",
                headers={"Authorization": f"Bearer {config.tavily_api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
            raise ValueError("Tavily map response results must be a list")
        results = data.get("results", [])
        base = {
            "base_url": data.get("base_url", ""),
            "results": results,
            "response_time": data.get("response_time", 0),
        }
        if not results:
            logger.info("步骤3结束：Tavily site map 返回空结果")
            return {
                "ok": False,
                **base,
                "error_type": "empty",
                "error": "Tavily map returned no results",
                "retryable": False,
            }
        logger.info("步骤3结束：Tavily site map 完成，结果数=%s", len(results))
        return {"ok": True, **base}
    except Exception as exc:
        error_type, error, retryable = classify_provider_exception(exc)
        logger.info("步骤3结束：Tavily site map 异常，error_type=%s", error_type)
        return {
            "ok": False,
            "error_type": error_type,
            "error": error,
            "retryable": retryable,
        }


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
    from .operation_runtime import _run_web_fetch_fallback

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
