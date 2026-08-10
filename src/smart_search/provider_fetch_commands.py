"""Fetch and site-map provider command ownership."""

import time
from typing import Any

import httpx

from .capability_service import (
    _command_capability_failure,
    _command_capability_preflight,
    _provider_availability,
)
from .config import config
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
from .service_support import _elapsed_ms


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
                    f"{reason}. 请运行 `smart-search config set TAVILY_API_KEY <key>`。"
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


async def jina_fetch(url: str) -> dict[str, Any]:
    """Compatibility name for the Jina web_fetch command route."""
    return await call_jina_reader(url)


__all__ = [
    "call_firecrawl_scrape",
    "call_jina_reader",
    "call_tavily_extract",
    "call_tavily_map",
    "jina_fetch",
]
