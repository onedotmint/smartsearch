"""Provider command boundaries and provider transport wrappers."""

import json
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
from .providers.anysearch import AnySearchProvider
from .providers.context7 import Context7Provider
from .providers.exa import ExaSearchProvider
from .providers.jina import JinaReaderProvider
from .providers.base import ProviderResult
from .providers.zhipu import ZhipuWebSearchProvider
from .providers.zhipu_mcp import ZhipuMCPProvider
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
    _normalize_domain_filter,
)

async def call_tavily_extract(url: str) -> str | None:
    availability = _provider_availability("tavily", "web_fetch")
    if not availability.get("eligible"):
        return None
    api_key = config.tavily_api_key
    endpoint = f"{config.tavily_api_url.rstrip('/')}/extract"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"urls": [url], "format": "markdown"}
    try:
        ctx = current_context()
        async with request_client(ctx, timeout=60.0) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=body,
                **request_timeout_kwargs(60.0, ctx),
            )
            response.raise_for_status()
            data = response.json()
            if data.get("results") and len(data["results"]) > 0:
                content = data["results"][0].get("raw_content", "")
                return content if content and content.strip() else None
            return None
    except Exception:
        return None

async def call_tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    availability = _provider_availability("tavily", "web_search")
    if not availability.get("eligible"):
        return None
    api_key = config.tavily_api_key
    endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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
            data = response.json()
            results = data.get("results", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                }
                for r in results
            ] if results else None
    except Exception:
        return None

async def call_firecrawl_search(query: str, limit: int = 14) -> list[dict] | None:
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"query": query, "limit": limit}
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
            data = response.json()
            results = data.get("data", {}).get("web", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", ""),
                }
                for r in results
            ] if results else None
    except Exception:
        return None

async def call_firecrawl_scrape(url: str, ctx=None) -> str | None:
    ctx = ctx or current_context()
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/scrape"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(config.retry_max_attempts):
        if attempt > 0:
            if not add_retry():
                return None
        body = {
            "url": url,
            "formats": ["markdown"],
            "timeout": 60000,
            "waitFor": (attempt + 1) * 1500,
        }
        try:
            async with request_client(ctx, timeout=90.0) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json=body,
                    **request_timeout_kwargs(90.0, ctx),
                )
                response.raise_for_status()
                data = response.json()
                markdown = data.get("data", {}).get("markdown", "")
                if markdown and markdown.strip():
                    return markdown
                await log_info(ctx, f"Firecrawl: markdown为空, 重试 {attempt + 1}/{config.retry_max_attempts}", config.debug_enabled)
        except Exception as e:
            await log_info(ctx, f"Firecrawl error: {e}", config.debug_enabled)
            return None
    return None

async def call_jina_reader(url: str) -> dict[str, Any]:
    raw = await JinaReaderProvider(
        config.jina_reader_api_url,
        config.jina_api_key,
        config.jina_respond_with,
        config.jina_timeout,
    ).fetch(url)
    return await _decode_provider_json(raw, provider="jina", capability="web_fetch")

async def call_tavily_map(
    url: str,
    instructions: str = "",
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    timeout: int = 150,
) -> dict[str, Any]:
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
    api_key = config.tavily_api_key

    endpoint = f"{config.tavily_api_url.rstrip('/')}/map"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
    if instructions:
        body["instructions"] = instructions
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
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
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error_type": "network_error", "error": f"HTTP错误: {e.response.status_code} - {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error_type": "network_error", "error": f"映射错误: {str(e)}"}

@observe_command
async def fetch(url: str) -> dict[str, Any]:
    from .search_service import _run_web_fetch_fallback

    start = time.time()
    # ================================================================================
    # 步骤1：校验 fetch 命令能力
    # ================================================================================
    # 目标：fetch 只依赖 web_fetch，不执行无关 minimum profile 预检。
    # 数据源：provider registry 和当前 profile 诊断结果。
    # 操作：
    # 1) 缺少 web_fetch 时立即返回 config_error。
    # 2) 保留 minimum profile 和 capability status 观测字段。
    # 3) provider 网络失败继续沿用同能力 fallback。
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
    result = {
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
    return result

async def map_site(
    url: str,
    instructions: str = "",
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    timeout: int = 150,
) -> dict[str, Any]:
    start = time.time()
    # ================================================================================
    # 步骤2：校验 map 命令能力
    # ================================================================================
    # 目标：site map 只依赖 site_map，缺失时给出聚焦配置错误。
    # 数据源：provider registry 和当前 profile 诊断结果。
    # 操作：
    # 1) 不执行 main_search、docs_search 或 web_fetch 的全局预检。
    # 2) 返回稳定 required/missing capability 字段。
    # 3) 通过后调用既有 Tavily site-map 适配器。
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
    start = time.time()
    preflight = _command_capability_preflight("exa-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    api_key = config.exa_api_key
    if not api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "EXA_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set EXA_API_KEY <key>`。",
        }

    provider = ExaSearchProvider(config.exa_base_url, api_key, config.exa_timeout)
    include_domain_list = _normalize_domain_filter(include_domains)
    exclude_domain_list = _normalize_domain_filter(exclude_domains)

    raw = await provider.search(
        query=query,
        num_results=num_results,
        search_type=search_type,
        include_text=include_text,
        include_highlights=include_highlights,
        start_published_date=start_published_date or None,
        include_domains=include_domain_list,
        exclude_domains=exclude_domain_list,
        category=category or None,
    )
    result = await _decode_provider_json(raw, provider="exa", capability="docs_search")
    result.update(preflight.get("metadata") or {})
    return result

def _anysearch_provider() -> AnySearchProvider:
    return AnySearchProvider(config.anysearch_api_url, config.anysearch_api_key, config.anysearch_timeout)

async def _decode_provider_json(
    raw: Any,
    provider: str = "anysearch",
    capability: str = "vertical_search",
) -> dict[str, Any]:
    """
    /*
     * ================================================================================
     * 步骤4：转换 provider 边界结果
     * ================================================================================
     * 目标：service 只消费统一结果，保留旧 provider JSON 的兼容入口。
     * 数据源：ProviderResult、结构化 dict 和旧 JSON 字符串。
     * 操作：
     * 1) 统一结果直接读取结构化 payload。
     * 2) legacy 字符串只在兼容边界解析一次。
     * 3) 解析失败返回 parse_error，不把错误折叠为 empty。
     * ================================================================================
     */
    """
    if isinstance(raw, ProviderResult):
        return raw.to_dict()
    if isinstance(raw, dict):
        return dict(raw)
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "ok": False,
            "provider": provider,
            "capability": capability,
            "error_type": "parse_error",
            "error": str(exc) or str(raw),
            "retryable": False,
        }
    if not isinstance(decoded, dict):
        return {
            "ok": False,
            "provider": provider,
            "capability": capability,
            "error_type": "protocol_error",
            "error": "provider response must be a JSON object",
            "retryable": False,
        }
    data = dict(decoded)
    data.setdefault("provider", provider)
    if not data.get("ok", False):
        data.setdefault("error_type", "network_error")
    return data

async def anysearch_domains(domain: str = "") -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("anysearch-domains")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"domain": domain})
    result = await _decode_provider_json(
        await _anysearch_provider().list_domains(domain),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def anysearch_search(query: str, domain: str = "", sub_domain: str = "", max_results: int = 5) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("anysearch-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    result = await _decode_provider_json(
        await _anysearch_provider().vertical_search(
            query=query,
            domain=domain,
            sub_domain=sub_domain,
            max_results=max_results,
        ),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def anysearch_extract(url: str, max_length: int = 20000) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("anysearch-extract")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"url": url})
    result = await _decode_provider_json(
        await _anysearch_provider().extract(url, max_length=max_length),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def anysearch_batch(queries: list[str], max_results: int = 3) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("anysearch-batch")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"queries": queries})
    result = await _decode_provider_json(
        await _anysearch_provider().batch_search(queries, max_results=max_results),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result

def _zhipu_mcp_search_provider() -> ZhipuMCPProvider:
    return ZhipuMCPProvider(
        config.zhipu_mcp_search_api_url,
        config.zhipu_mcp_api_key or "",
        config.zhipu_mcp_timeout,
        provider_id="zhipu-mcp",
    )

def _zhipu_mcp_reader_provider() -> ZhipuMCPProvider:
    return ZhipuMCPProvider(
        config.zhipu_mcp_reader_api_url,
        config.zhipu_mcp_api_key or "",
        config.zhipu_mcp_timeout,
        provider_id="zhipu-mcp-reader",
    )

def _zhipu_mcp_zread_provider() -> ZhipuMCPProvider:
    return ZhipuMCPProvider(
        config.zhipu_mcp_zread_api_url,
        config.zhipu_mcp_api_key or "",
        config.zhipu_mcp_timeout,
        provider_id="zhipu-mcp-zread",
    )

async def jina_fetch(url: str) -> dict[str, Any]:
    return await call_jina_reader(url)

async def zhipu_mcp_search(query: str, count: int = 5) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    result = await _decode_provider_json(
        await _zhipu_mcp_search_provider().web_search(query, count=count),
        provider="zhipu-mcp",
        capability="web_search",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def zhipu_mcp_reader(url: str) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-reader")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"url": url})
    result = await _decode_provider_json(
        await _zhipu_mcp_reader_provider().web_reader(url),
        provider="zhipu-mcp-reader",
        capability="web_fetch",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def zhipu_mcp_search_doc(repo: str, query: str, max_results: int = 5) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-search-doc")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"repo": repo, "query": query})
    result = await _decode_provider_json(
        await _zhipu_mcp_zread_provider().search_doc(repo, query, max_results=max_results),
        provider="zhipu-mcp-zread",
        capability="zread",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def zhipu_mcp_repo_structure(repo: str, ref: str = "") -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-repo-structure")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"repo": repo, "ref": ref})
    result = await _decode_provider_json(
        await _zhipu_mcp_zread_provider().get_repo_structure(repo, ref=ref),
        provider="zhipu-mcp-zread",
        capability="zread",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def zhipu_mcp_read_file(repo: str, path: str, ref: str = "") -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-read-file")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"repo": repo, "path": path, "ref": ref})
    result = await _decode_provider_json(
        await _zhipu_mcp_zread_provider().read_file(repo, path, ref=ref),
        provider="zhipu-mcp-zread",
        capability="zread",
    )
    result.update(preflight.get("metadata") or {})
    return result

async def exa_find_similar(url: str, num_results: int = 5) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("exa-similar")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"url": url})
    api_key = config.exa_api_key
    if not api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "EXA_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set EXA_API_KEY <key>`。",
        }

    provider = ExaSearchProvider(config.exa_base_url, api_key, config.exa_timeout)
    raw = await provider.find_similar(url=url, num_results=num_results)
    result = await _decode_provider_json(raw, provider="exa", capability="docs_search")
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
    start = time.time()
    preflight = _command_capability_preflight("zhipu-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    api_key = config.zhipu_api_key
    if not api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "ZHIPU_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set ZHIPU_API_KEY <key>`。",
        }
    provider = ZhipuWebSearchProvider(
        config.zhipu_api_url,
        api_key,
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
    result = await _decode_provider_json(raw, provider="zhipu", capability="web_search")
    result.update(preflight.get("metadata") or {})
    return result

async def context7_library(name: str, query: str = "") -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("context7-library")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"name": name, "query": query})
    api_key = config.context7_api_key
    if not api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "CONTEXT7_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set CONTEXT7_API_KEY <key>`。",
        }
    provider = Context7Provider(config.context7_base_url, api_key, config.context7_timeout)
    raw = await provider.library(name, query)
    result = await _decode_provider_json(raw, provider="context7", capability="docs_search")
    result.update(preflight.get("metadata") or {})
    return result

async def context7_docs(library_id: str, query: str) -> dict[str, Any]:
    start = time.time()
    preflight = _command_capability_preflight("context7-docs")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"library_id": library_id, "query": query})
    api_key = config.context7_api_key
    if not api_key:
        return {
            "ok": False,
            "error_type": "config_error",
            "error": "CONTEXT7_API_KEY 未配置。请运行 `smart-search setup`，或使用 `smart-search config set CONTEXT7_API_KEY <key>`。",
        }
    provider = Context7Provider(config.context7_base_url, api_key, config.context7_timeout)
    raw = await provider.docs(library_id, query)
    result = await _decode_provider_json(raw, provider="context7", capability="docs_search")
    result.update(preflight.get("metadata") or {})
    return result

__all__ = [name for name in globals() if not name.startswith("__")]
