"""Search orchestration and same-capability fallback workflows."""

import asyncio
import time
from typing import Any

import httpx

from .capability_service import (
    _command_capability_metadata,
    _main_search_provider_configs,
    _main_search_providers,
    _provider_configured,
    validate_command_capabilities,
    validate_minimum_profile,
)
from .config import config
from .evidence import EvidenceBundle
from .intent_router import IntentRouter
from .logger import logger
from .provider_fetch_commands import (
    call_firecrawl_scrape,
    call_tavily_extract,
    jina_fetch,
)
from .provider_mcp_commands import (
    zhipu_mcp_reader,
    zhipu_mcp_search,
)
from .provider_search_commands import (
    call_firecrawl_search,
    call_tavily_search,
    context7_library,
    exa_search,
    zhipu_search,
)
from .providers.base import classify_provider_exception, coerce_provider_result
from .providers.openai_compatible import OpenAICompatibleSearchProvider
from .providers.xai_responses import XAIResponsesSearchProvider
from .runtime_cache import (
    add_request,
    current_context,
    mark_budget_exhausted,
    observe_command,
    observe_stage,
)
from .security import redact_url_credentials, sanitize_text
from .service_support import (
    MINIMUM_PROFILE_ERROR,
    PROFILE_NAMES,
    SOURCE_PROVENANCE_WARNING,
    _AVAILABLE_MODELS_CACHE,
    _AVAILABLE_MODELS_LOCK,
    _append_openai_transport_attempts,
    _attempt,
    _attempt_timeout_seconds,
    _cache_attempt_extra,
    _cached_source_provider,
    _capability_plan,
    _capability_plan_from_result,
    _combined_degraded_reason,
    _elapsed_ms,
    _evidence_bundle_fields,
    _extract_urls,
    _fallback_used,
    _normalize_source_results,
    _openai_model_breaker_state,
    _openai_model_candidates,
    _provider_names_from_attempts,
    _record_openai_model_failure,
    _record_openai_model_success,
    _remaining_budget_seconds,
)
from .sources import merge_sources, new_session_id, split_answer_and_sources
from .utils import PromptConfigurationError


def _main_search_failure_allows_route_fallback(error_type: str) -> bool:
    """
    /*
     * ==============================================================================
     * 步骤1：判断模型路由是否继续
     * ==============================================================================
     * 目标：只把上游服务失败交给下一条模型路由，避免备用配置掩盖本地错误。
     * 数据源：ProviderResult 或异常分类后的 error_type。
     * 操作：配置、参数和预算错误立即停止；超时、网络、限流、空结果及其他
     * provider 协议错误继续尝试下一条路由。
     * ==============================================================================
     */
    """
    logger.info("步骤1开始：判断模型路由 fallback，error_type=%s", error_type)
    allowed = str(error_type or "").strip().lower() not in {
        "config_error",
        "parameter_error",
        "budget_exhausted",
    }
    logger.info("步骤1结束：模型路由 fallback 判断完成，allowed=%s", allowed)
    return allowed


def _empty_search_result(
    start: float,
    session_id: str,
    query: str,
    error_type: str,
    error: str,
    primary_api_mode: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "error": sanitize_text(error),
        "session_id": session_id,
        "query": query,
        "primary_api_mode": primary_api_mode,
        "content": "",
        "sources": [],
        "sources_count": 0,
        "primary_sources": [],
        "primary_sources_count": 0,
        "extra_sources": [],
        "extra_sources_count": 0,
        "evidence_bundle": EvidenceBundle().to_dict(),
        "discovery_candidates": [],
        "fetched_evidence": [],
        "evidence_items": [],
        "citations": [],
        "gaps": [],
        "source_warning": "",
        "routing_decision": {},
        "providers_used": [],
        "provider_attempts": [],
        "fallback_used": False,
        "model_route_id": "",
        "model_route_fallback_used": False,
        "validation_level": "",
        "required_capabilities": [],
        "required_capability_groups": [],
        "missing_capabilities": [],
        "required_providers": [],
        "missing_providers": [],
        "optional_missing": [],
        "optional_missing_capabilities": [],
        "degraded": False,
        "degraded_reason": "",
        "elapsed_ms": _elapsed_ms(start),
    }
    if extra:
        data.update(extra)
        data["error"] = sanitize_text(str(data.get("error") or ""))
    return data

async def fetch_available_models(api_url: str, api_key: str) -> list[str]:
    models_url = f"{api_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            models_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

    models: list[str] = []
    for item in (data or {}).get("data", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models

async def get_available_models_cached(api_url: str, api_key: str) -> list[str]:
    key = (api_url, api_key)
    async with _AVAILABLE_MODELS_LOCK:
        if key in _AVAILABLE_MODELS_CACHE:
            return _AVAILABLE_MODELS_CACHE[key]

    try:
        models = await fetch_available_models(api_url, api_key)
    except Exception:
        models = []

    async with _AVAILABLE_MODELS_LOCK:
        _AVAILABLE_MODELS_CACHE[key] = models
    return models

def extra_results_to_sources(
    tavily_results: list[dict] | None,
    firecrawl_results: list[dict] | None,
) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()

    if firecrawl_results:
        for r in firecrawl_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "firecrawl"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            desc = (r.get("description") or "").strip()
            if desc:
                item["description"] = desc
            sources.append(item)

    if tavily_results:
        for r in tavily_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item = {"url": url, "provider": "tavily"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            content = (r.get("content") or "").strip()
            if content:
                item["description"] = content
            sources.append(item)

    return sources

# Neutral capability runners live in operation_runtime; re-export for v1 callers
# and existing monkeypatch points (search_service._run_*).
from .operation_runtime import (  # noqa: E402
    _run_docs_search_fallback,
    _run_vertical_search_fallback,
    _run_web_fetch_fallback,
    _run_web_search_fallback,
)


def _resolve_search_profile(
    profile: str,
    validation: str,
    extra_sources: int,
) -> tuple[str, str, int]:
    """
    =================================================================================
    步骤2：解析搜索 profile
    =================================================================================
    目标：用 fast、balanced、deep 控制搜索深度，不把普通 search 升级成 research。
    数据源：CLI profile、旧 validation 参数和 extra_sources 参数。
    操作：
    1) 校验 profile 名称。
    2) 仅在旧参数未显式传入时补默认 validation。
    3) 为 balanced/deep 提供合理来源预算。
    """
    normalized = (profile or "").strip().lower()
    if normalized and normalized not in PROFILE_NAMES:
        raise ValueError(f"Invalid search profile: {normalized}")
    effective_validation = (validation or "").strip().lower()
    effective_extra = max(0, int(extra_sources or 0))
    if normalized == "fast":
        effective_validation = effective_validation or "fast"
        effective_extra = min(effective_extra, 2)
    elif normalized == "balanced":
        effective_validation = effective_validation or "balanced"
        effective_extra = max(effective_extra, 3)
    elif normalized == "deep":
        effective_validation = effective_validation or "strict"
        effective_extra = max(effective_extra, 5)
    return normalized, effective_validation, effective_extra

async def _search_without_synthesis(
    query: str,
    *,
    count: int,
    providers: str,
    fallback: str,
    validation_level: str,
    profile: str,
    response_mode: str,
    start: float,
    session_id: str,
    capability_metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    =================================================================================
    步骤3：使用来源搜索降级
    =================================================================================
    目标：仅配置 Tavily-compatible 或其他 source provider 时仍能完成 Search。
    数据源：web_search/docs_search fallback 链。
    操作：
    1) 优先获取 web_search 来源。
    2) 没有结果时尝试已配置 docs_search 来源。
    3) 返回证据候选，不伪造综合答案。
    """
    logger.info("开始执行 source-only 搜索: query=%s", query)
    sources, attempts = await _run_web_search_fallback(query, count=count, providers=providers, fallback=fallback)
    if not sources:
        docs_sources, docs_attempts = await _run_docs_search_fallback(query, providers=providers, fallback=fallback)
        sources.extend(docs_sources)
        attempts.extend(docs_attempts)
    evidence_bundle = EvidenceBundle()
    evidence_bundle.add_discovery_candidates(sources)
    evidence_bundle.add_provider_attempts(attempts)
    evidence_bundle.mark_degraded("main_search 未配置；source-only 结果不能直接作为最终结论")
    evidence_fields = _evidence_bundle_fields(evidence_bundle)
    execution_plan = _capability_plan(
        "search",
        required_capabilities=tuple(capability_metadata.get("required_capabilities") or ("web_search", "docs_search")),
        optional_capabilities=tuple(capability_metadata.get("optional_missing_capabilities") or ()),
        budget=profile,
        allow_synthesis=False,
        source_only=True,
        response_mode=response_mode,
    )
    ok = bool(sources)
    source_capabilities = list(
        dict.fromkeys(
            attempt.get("capability")
            for attempt in attempts
            if attempt.get("capability") in {"web_search", "docs_search"}
        )
    )
    result = {
        "ok": ok,
        "error_type": "" if ok else "network_error",
        "error": "" if ok else "搜索 provider 未返回来源",
        "session_id": session_id,
        "query": query,
        "profile": profile,
        "response_mode": response_mode,
        "primary_api_mode": "source-only",
        "content": "",
        "sources": evidence_fields["evidence_bundle"]["sources"],
        "results": evidence_fields["evidence_bundle"]["sources"],
        "sources_count": len(evidence_fields["evidence_bundle"]["sources"]),
        "primary_sources": evidence_fields["evidence_bundle"]["sources"],
        "primary_sources_count": len(evidence_fields["evidence_bundle"]["sources"]),
        "extra_sources": [],
        "extra_sources_count": 0,
        "source_warning": "未配置 main_search；当前结果仅包含来源候选，请先 fetch 后再形成最终结论。",
        "routing_decision": {
            "mode": "source-only",
            "reason": "No main_search provider configured; returned same-capability source discovery.",
            "required_capabilities": source_capabilities or ["web_search", "docs_search"],
        },
        "providers_used": _provider_names_from_attempts(attempts),
        "provider_attempts": attempts,
        "fallback_used": _fallback_used(attempts),
        "validation_level": validation_level,
        "capability_execution_plan": execution_plan.to_dict(),
        "elapsed_ms": _elapsed_ms(start),
    }
    result.update(capability_metadata)
    result.update(evidence_fields)
    result["degraded"] = bool(result.get("degraded")) or evidence_bundle.degraded
    result["degraded_reason"] = _combined_degraded_reason(evidence_bundle, capability_metadata)
    result["minimum_profile_ok"] = bool(capability_metadata.get("minimum_profile_ok", False))
    logger.info("source-only 搜索完成: ok=%s sources=%s", ok, len(sources))
    return result

@observe_command
async def search(
    query: str,
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    validation: str = "",
    fallback: str = "",
    providers: str = "auto",
    stream: bool | None = None,
    timeout_seconds: float | None = None,
    profile: str = "",
    response_mode: str = "concise",
) -> dict[str, Any]:
    start = time.time()
    context = current_context()
    session_id = context.session_id if context is not None else new_session_id()
    try:
        profile_name, profile_validation, profile_extra_sources = _resolve_search_profile(profile, validation, extra_sources)
        validation_level = (profile_validation or config.validation_level).strip().lower()
        fallback_mode = (fallback or config.fallback_mode).strip().lower()
        response_mode = (response_mode or "concise").strip().lower()
        if validation_level not in config._ALLOWED_VALIDATION_LEVELS:
            raise ValueError(f"Invalid validation level: {validation_level}")
        if fallback_mode not in config._ALLOWED_FALLBACK_MODES:
            raise ValueError(f"Invalid fallback mode: {fallback_mode}")
        if response_mode not in {"evidence", "concise", "synthesized"}:
            raise ValueError(f"Invalid response mode: {response_mode}")
    except ValueError as e:
        return _empty_search_result(start, session_id, query, "parameter_error", str(e))

    extra_sources = profile_extra_sources

    # ================================================================================
    # 步骤4：执行搜索命令能力校验
    # ================================================================================
    # 目标：只校验 search 当前需要的能力，保留 minimum profile 作为诊断信息。
    # 数据源：当前 capability status、minimum profile 和 response mode。
    # 操作：
    # 1) concise/synthesized 要求 main_search。
    # 2) lite/off 的 evidence 模式允许 web_search/docs_search source-only。
    # 3) 缺少命令必需能力时返回聚焦 config_error。
    try:
        if config.model_routes_configured:
            config.model_routes
    except ValueError as exc:
        return _empty_search_result(
            start,
            session_id,
            query,
            "config_error",
            str(exc),
            extra={"validation_level": validation_level},
        )
    minimum = validate_minimum_profile()
    if minimum.get("error_type") == "parameter_error":
        return _empty_search_result(
            start,
            session_id,
            query,
            "parameter_error",
            minimum.get("error", "Invalid minimum profile"),
            extra={"validation_level": validation_level},
        )
    command_capabilities = validate_command_capabilities(
        "search",
        minimum_profile=minimum.get("profile", ""),
        response_mode=response_mode,
        capability_status=minimum.get("capability_status", {}),
    )
    capability_metadata = _command_capability_metadata(command_capabilities, minimum)
    if not command_capabilities.get("ok"):
        return _empty_search_result(
            start,
            session_id,
            query,
            command_capabilities.get("error_type", "config_error"),
            command_capabilities.get("error", MINIMUM_PROFILE_ERROR),
            extra={
                "validation_level": validation_level,
                **capability_metadata,
            },
        )

    try:
        main_provider_configs = _main_search_provider_configs(model_override=model, providers=providers)
    except ValueError as e:
        return _empty_search_result(start, session_id, query, "parameter_error", str(e), extra={"validation_level": validation_level})

    if not main_provider_configs:
        source_capabilities = minimum.get("capability_status", {})
        has_source_search = bool(
            source_capabilities.get("web_search", {}).get("configured")
            or source_capabilities.get("docs_search", {}).get("configured")
        )
        if has_source_search and command_capabilities.get("source_only"):
            return await _search_without_synthesis(
                query,
                count=max(3, min(6, extra_sources or 3)),
                providers=providers,
                fallback=fallback_mode,
                validation_level=validation_level,
                profile=profile_name,
                response_mode=response_mode,
                start=start,
                session_id=session_id,
                capability_metadata=capability_metadata,
            )
        return _empty_search_result(
            start,
            session_id,
            query,
            "config_error",
            "No configured main_search provider matches --providers.",
            extra={
                "validation_level": validation_level,
                **capability_metadata,
                "required_capabilities": ["main_search"],
                "missing_capabilities": ["main_search"],
                "degraded": False,
                "degraded_reason": "",
            },
        )

    primary_api_mode = main_provider_configs[0]["mode"]
    provider_platform = platform
    if response_mode == "evidence":
        provider_platform = f"{platform}\nReturn compact evidence and source metadata only; do not write a long final answer."
    elif response_mode == "concise":
        provider_platform = f"{platform}\nReturn a concise conclusion with source metadata."
    if stream is not None:
        for provider_config in main_provider_configs:
            if provider_config["provider"] == "openai-compatible":
                provider_config["stream"] = stream

    has_tavily = _provider_configured("tavily")
    has_firecrawl = _provider_configured("firecrawl")
    tavily_count = 0
    firecrawl_count = 0
    if extra_sources > 0:
        if has_tavily and has_firecrawl:
            tavily_count = max(1, round(extra_sources * 0.6))
            firecrawl_count = extra_sources - tavily_count
        elif has_tavily:
            tavily_count = extra_sources
        elif has_firecrawl:
            firecrawl_count = extra_sources

    selected_main_provider_configs = main_provider_configs if fallback_mode != "off" else main_provider_configs[:1]
    try:
        with observe_stage("search.route"):
            route_result = await IntentRouter(config).route(query, validation_level=validation_level, allow_remote=True)
    except ValueError as e:
        return _empty_search_result(start, session_id, query, "parameter_error", str(e), extra={"validation_level": validation_level})
    fetch_urls = _extract_urls(query)
    supplemental_paths = route_result.required_capabilities
    openai_candidate_models = next(
        (
            [candidate["model"] for candidate in _openai_model_candidates(item, fallback_mode=fallback_mode, model_override=model)]
            for item in selected_main_provider_configs
            if item["provider"] == "openai-compatible"
        ),
        [],
    )
    # 3.1 路由元数据保留端点标识，但不得携带 URL 内嵌凭据。
    routing_decision = {
        **route_result.to_dict(),
        "validation_level": validation_level,
        "fallback_mode": fallback_mode,
        "providers": providers,
        "main_search_chain": [item["provider"] for item in selected_main_provider_configs],
        "main_search_route_chain": [
            item.get("route_id") or item["provider"] for item in selected_main_provider_configs
        ],
        "main_search_routes": [
            {
                "id": item.get("route_id", ""),
                "provider": item["provider"],
                "model": item.get("model", ""),
                "api_url": redact_url_credentials(str(item.get("api_url", ""))),
            }
            for item in selected_main_provider_configs
        ],
        "openai_compatible_stream": next((bool(item.get("stream")) for item in selected_main_provider_configs if item["provider"] == "openai-compatible"), False),
        "openai_compatible_models": openai_candidate_models,
        "openai_compatible_model_fallback_enabled": len(openai_candidate_models) > 1,
    }

    provider_attempts: list[dict] = []
    fetched_evidence: list[dict[str, Any]] = []
    primary_start = time.time()
    primary_result = None
    successful_main_config: dict[str, Any] | None = None
    last_primary_error: dict[str, Any] | None = None
    model_fallback_used = False
    model_route_fallback_used = False
    transport_fallback_used = False
    last_route_id = ""
    last_main_route_id = ""
    stop_main_search = False
    total_main_candidates = sum(
        len(_openai_model_candidates(item, fallback_mode=fallback_mode, model_override=model))
        if item["provider"] == "openai-compatible"
        else 1
        for item in selected_main_provider_configs
    )
    completed_main_candidates = 0
    for provider_config in selected_main_provider_configs:
        provider_candidates = (
            _openai_model_candidates(provider_config, fallback_mode=fallback_mode, model_override=model)
            if provider_config["provider"] == "openai-compatible"
            else [provider_config]
        )
        for candidate_config in provider_candidates:
            completed_main_candidates += 1
            primary_start = time.time()
            search_provider = _main_search_providers([candidate_config], fallback="auto")[0]
            attempt_extra: dict[str, Any] = {}
            route_id = str(candidate_config.get("route_id") or "")
            if route_id:
                attempt_extra["route_id"] = route_id
                if last_route_id and route_id != last_route_id:
                    attempt_extra["fallback_from_route"] = last_route_id
                    model_route_fallback_used = True
                last_route_id = route_id
            last_main_route_id = route_id
            if candidate_config["provider"] == "openai-compatible":
                attempt_extra["model"] = candidate_config["model"]
                attempt_extra["model_role"] = candidate_config.get("model_role", "primary")
                if candidate_config.get("fallback_from_model"):
                    attempt_extra["fallback_from_model"] = candidate_config["fallback_from_model"]
                    model_fallback_used = True
                breaker_state = _openai_model_breaker_state(candidate_config["api_url"], candidate_config["model"])
                if breaker_state.get("state") == "open":
                    attempt_extra["breaker_state"] = breaker_state
                    provider_attempts.append(
                        _attempt(
                            "main_search",
                            "OpenAI-compatible",
                            "skipped",
                            primary_start,
                            error_type="network_error",
                            error="model breaker open",
                            extra=attempt_extra,
                        )
                    )
                    continue
            attempt_timeout = _attempt_timeout_seconds(
                start,
                timeout_seconds,
                total_main_candidates - completed_main_candidates + 1,
            )
            if timeout_seconds is not None and _remaining_budget_seconds(start, timeout_seconds) <= 0:
                mark_budget_exhausted()
            try:
                if not add_request():
                    provider_attempts.append(
                        _attempt(
                            "main_search",
                            search_provider.get_provider_name(),
                            "skipped",
                            primary_start,
                            error_type="budget_exhausted",
                            error="request budget exhausted",
                            retryable=False,
                            extra={**attempt_extra, "budget_exhausted": True},
                        )
                    )
                    stop_main_search = True
                    break
                with observe_stage("search.primary"):
                    if attempt_timeout is not None:
                        candidate_result = await asyncio.wait_for(search_provider.search(query, provider_platform), timeout=attempt_timeout)
                    else:
                        candidate_result = await search_provider.search(query, provider_platform)
                if timeout_seconds is not None and _remaining_budget_seconds(start, timeout_seconds) <= 0:
                    mark_budget_exhausted()
                transport_attempts = getattr(search_provider, "last_transport_attempts", [])
                candidate_provider_result = coerce_provider_result(
                    candidate_result,
                    provider=candidate_config["provider"],
                    capability="main_search",
                    wire_format="content",
                )
                if _append_openai_transport_attempts(provider_attempts, search_provider, candidate_config, attempt_extra):
                    transport_fallback_used = transport_fallback_used or any(
                        attempt.get("fallback_from_transport") for attempt in transport_attempts
                    )
                if candidate_provider_result.ok and candidate_provider_result.content.strip():
                    primary_result = candidate_provider_result.content
                    successful_main_config = candidate_config
                    if candidate_config["provider"] != "openai-compatible" or not transport_attempts:
                        provider_attempts.append(
                            _attempt(
                                "main_search",
                                search_provider.get_provider_name(),
                                "ok",
                                primary_start,
                                result_count=1,
                                extra=attempt_extra,
                            )
                        )
                    if candidate_config["provider"] == "openai-compatible":
                        _record_openai_model_success(candidate_config["api_url"], candidate_config["model"])
                    if route_id:
                        last_route_id = route_id
                    break
                if candidate_config["provider"] == "openai-compatible":
                    attempt_extra["breaker_state"] = _record_openai_model_failure(candidate_config["api_url"], candidate_config["model"])
                error_type = candidate_provider_result.error_type or "empty"
                top_level_error_type = "network_error" if error_type == "empty" else error_type
                last_primary_error = _primary_search_error_result(
                    start,
                    session_id,
                    query,
                    candidate_config["mode"],
                    top_level_error_type,
                    candidate_provider_result.error or f"{search_provider.get_provider_name()} 返回空结果",
                )
                if candidate_config["provider"] != "openai-compatible" or not transport_attempts:
                    provider_attempts.append(
                        _attempt(
                            "main_search",
                            search_provider.get_provider_name(),
                            "empty" if error_type == "empty" else "error",
                            primary_start,
                            error_type=error_type,
                            error=candidate_provider_result.error,
                            retryable=candidate_provider_result.retryable,
                            extra=attempt_extra,
                        )
                    )
                if not _main_search_failure_allows_route_fallback(error_type):
                    stop_main_search = True
                    break
                if route_id:
                    last_route_id = route_id
            except Exception as exc:
                """
                /*
                 * ==============================================================================
                 * 步骤1：统一主搜索异常分类
                 * ==============================================================================
                 * 目标：把 provider 边界抛出的原始异常转换为统一错误协议。
                 * 数据源：Prompt 配置异常和 provider 原始异常。
                 * 操作：
                 * 1) 保留 Prompt 配置错误的 config_error 语义。
                 * 2) 其他异常交给统一 provider 分类器处理。
                 * ==============================================================================
                 */
                """
                logger.info("步骤1开始：统一主搜索异常分类，provider=%s", search_provider.get_provider_name())
                if isinstance(exc, PromptConfigurationError):
                    error_type, error, retryable = "config_error", str(exc), False
                else:
                    error_type, error, retryable = classify_provider_exception(exc)
                last_primary_error = _primary_search_error_result(
                    start,
                    session_id,
                    query,
                    candidate_config["mode"],
                    error_type,
                    error,
                )
                transport_attempts = getattr(search_provider, "last_transport_attempts", [])
                if _append_openai_transport_attempts(provider_attempts, search_provider, candidate_config, attempt_extra):
                    transport_fallback_used = transport_fallback_used or any(
                        attempt.get("fallback_from_transport") for attempt in transport_attempts
                    )
                if candidate_config["provider"] == "openai-compatible":
                    attempt_extra["breaker_state"] = _record_openai_model_failure(candidate_config["api_url"], candidate_config["model"])
                if candidate_config["provider"] != "openai-compatible" or not transport_attempts:
                    provider_attempts.append(
                        _attempt(
                            "main_search",
                            search_provider.get_provider_name(),
                            "error",
                            primary_start,
                            error_type=error_type,
                            error=error,
                            retryable=retryable,
                            extra=attempt_extra,
                        )
                    )
                if not _main_search_failure_allows_route_fallback(error_type):
                    stop_main_search = True
                    break
                if route_id:
                    last_route_id = route_id
                logger.info("步骤1结束：统一主搜索异常分类，error_type=%s", error_type)
        if primary_result is not None:
            break
        if stop_main_search:
            break
    if primary_result is None:
        result = last_primary_error or _primary_search_error_result(start, session_id, query, primary_api_mode, "network_error", "搜索失败或无结果")
        if any(attempt.get("error_type") == "budget_exhausted" for attempt in provider_attempts):
            result["error_type"] = "budget_exhausted"
            result["error"] = "request budget exhausted"
        if context is not None and context.budget.exhausted:
            result["error_type"] = "budget_exhausted"
            result["error"] = "request budget exhausted"
        result["provider_attempts"] = provider_attempts
        result["providers_used"] = _provider_names_from_attempts(provider_attempts)
        result["fallback_used"] = _fallback_used(provider_attempts) or model_route_fallback_used
        result["transport_fallback_used"] = transport_fallback_used
        result["model_fallback_used"] = model_fallback_used
        result["model_route_id"] = last_main_route_id
        result["model_route_fallback_used"] = model_route_fallback_used
        routing_decision["last_attempted_route_id"] = last_main_route_id
        routing_decision["model_route_fallback_used"] = model_route_fallback_used
        result["routing_decision"] = routing_decision
        result["validation_level"] = validation_level
        result.update(capability_metadata)
        evidence_bundle = EvidenceBundle()
        evidence_bundle.add_provider_attempts(provider_attempts)
        evidence_bundle.add_gap({"subquestion_id": "", "reason": result.get("error") or "搜索失败或无结果"})
        result.update(_evidence_bundle_fields(evidence_bundle))
        result["degraded"] = bool(result.get("degraded")) or evidence_bundle.degraded
        result["degraded_reason"] = _combined_degraded_reason(evidence_bundle, capability_metadata)
        result["capability_execution_plan"] = _capability_plan_from_result(
            "search",
            command_capabilities,
            budget=profile_name,
            allow_synthesis=response_mode == "synthesized",
            response_mode=response_mode,
        ).to_dict()
        return result

    successful_main_config = successful_main_config or selected_main_provider_configs[0]
    primary_api_mode = successful_main_config["mode"]
    effective_model = successful_main_config["model"]
    successful_route_id = str(successful_main_config.get("route_id") or "")
    routing_decision["selected_route_id"] = successful_route_id
    routing_decision["model_route_fallback_used"] = model_route_fallback_used

    """
    /*
     * ================================================================================
     * 步骤4：并行获取 extra_sources
     * ================================================================================
     * 目标：保留每个已调度 provider 的成功、空结果、异常、缓存和预算状态。
     * 数据源：Tavily/Firecrawl source factory、runtime cache 和 RequestBudget。
     * 操作：
     * 1) 每个 provider 在网络调用前预留 request budget。
     * 2) 并行等待缓存执行，异常不在 gather 边界丢失。
     * 3) 将每个执行结果转换为 provider_attempts，供 JSON 和 evidence bundle 使用。
     * ================================================================================
     */
    """
    logger.info("步骤4开始：并行获取 extra_sources")
    coros: list[Any] = []
    extra_provider_names: list[str] = []
    extra_provider_starts: dict[str, float] = {}
    extra_provider_outcomes: dict[str, dict[str, Any]] = {}
    if tavily_count:
        tavily_outcome: dict[str, Any] = {}
        extra_provider_outcomes["tavily"] = tavily_outcome
        extra_provider_starts["tavily"] = time.time()

        async def tavily_source_factory() -> list[dict]:
            if not add_request():
                tavily_outcome.update(
                    {
                        "error_type": "budget_exhausted",
                        "error": "request budget exhausted",
                        "retryable": False,
                    }
                )
                return []
            return _normalize_source_results(await call_tavily_search(query, tavily_count), "tavily")

        extra_provider_names.append("tavily")
        coros.append(
            _cached_source_provider(
                "web_search",
                "tavily",
                query,
                {"count": tavily_count},
                tavily_source_factory,
            )
        )
    if firecrawl_count:
        firecrawl_outcome: dict[str, Any] = {}
        extra_provider_outcomes["firecrawl"] = firecrawl_outcome
        extra_provider_starts["firecrawl"] = time.time()

        async def firecrawl_source_factory() -> list[dict]:
            if not add_request():
                firecrawl_outcome.update(
                    {
                        "error_type": "budget_exhausted",
                        "error": "request budget exhausted",
                        "retryable": False,
                    }
                )
                return []
            return _normalize_source_results(await call_firecrawl_search(query, firecrawl_count), "firecrawl")

        extra_provider_names.append("firecrawl")
        coros.append(
            _cached_source_provider(
                "web_search",
                "firecrawl",
                query,
                {"count": firecrawl_count},
                firecrawl_source_factory,
            )
        )

    with observe_stage("search.extra_sources"):
        gathered = await asyncio.gather(*coros, return_exceptions=True)
    primary_result = primary_result or ""
    extra_provider_sources: dict[str, list[dict]] = {"tavily": [], "firecrawl": []}
    for provider_name, execution in zip(extra_provider_names, gathered):
        provider_start = extra_provider_starts.get(provider_name, start)
        outcome = extra_provider_outcomes.get(provider_name, {})
        if isinstance(execution, BaseException):
            error_type, error, retryable = classify_provider_exception(execution)
            provider_attempts.append(
                _attempt(
                    "web_search",
                    provider_name,
                    "error",
                    provider_start,
                    error_type=error_type,
                    error=error,
                    retryable=retryable,
                )
            )
            continue
        if isinstance(execution.value, list):
            extra_provider_sources[provider_name] = execution.value
        else:
            outcome.update(
                {
                    "error_type": "protocol_error",
                    "error": "extra source provider result must be a list",
                    "retryable": False,
                }
            )

        error_type = str(outcome.get("error_type") or "")
        error = str(outcome.get("error") or "")
        retryable = outcome.get("retryable") if isinstance(outcome.get("retryable"), bool) else None
        result_count = len(extra_provider_sources[provider_name])
        if error_type == "budget_exhausted":
            status = "skipped"
        elif error_type:
            status = "empty" if error_type == "empty" else "error"
        elif result_count:
            status = "ok"
        else:
            error_type = "empty"
            error = "provider returned no usable result"
            retryable = False
            status = "empty"
        provider_attempts.append(
            _attempt(
                "web_search",
                provider_name,
                status,
                provider_start,
                result_count=result_count if status == "ok" else 0,
                error_type=error_type,
                error=error,
                retryable=retryable,
                extra=_cache_attempt_extra(execution),
            )
        )

    answer, primary_sources = split_answer_and_sources(primary_result)
    extra_source_items = merge_sources(
        extra_provider_sources["firecrawl"],
        extra_provider_sources["tavily"],
    )
    logger.info("步骤4结束：extra_sources provider attempts=%s", len(extra_provider_names))

    supplemental_sources: list[dict] = []
    if validation_level in {"balanced", "strict"}:
        if "docs_search" in supplemental_paths:
            with observe_stage("search.supplemental_docs"):
                docs_sources, docs_attempts = await _run_docs_search_fallback(query, providers=providers, fallback=fallback_mode)
            provider_attempts.extend(docs_attempts)
            supplemental_sources.extend(docs_sources)
        if "web_search" in supplemental_paths:
            with observe_stage("search.supplemental_web"):
                web_sources, web_attempts = await _run_web_search_fallback(query, count=max(1, extra_sources or 3), providers=providers, fallback=fallback_mode)
            provider_attempts.extend(web_attempts)
            supplemental_sources.extend(web_sources)
        if "web_fetch" in supplemental_paths:
            fetch_url = fetch_urls[0] if fetch_urls else query.strip()
            with observe_stage("search.supplemental_fetch"):
                fetch_result, fetch_attempts = await _run_web_fetch_fallback(fetch_url, fallback=fallback_mode)
            provider_attempts.extend(fetch_attempts)
            if fetch_result:
                fetched_evidence.append(
                    {
                        "url": fetch_result["url"],
                        "provider": fetch_result["provider"],
                        "title": fetch_result.get("title") or fetch_result["url"],
                        "content": fetch_result.get("content") or "",
                        "source_type": "fetched_page",
                    }
                )
                supplemental_sources.append({"url": fetch_result["url"], "provider": fetch_result["provider"], "description": fetch_result["content"][:300]})
        if "vertical_search" in supplemental_paths:
            with observe_stage("search.supplemental_vertical"):
                vertical_sources, vertical_attempts = await _run_vertical_search_fallback(query, providers=providers, fallback=fallback_mode)
            provider_attempts.extend(vertical_attempts)
            supplemental_sources.extend(vertical_sources)

    extra_source_items = merge_sources(extra_source_items, supplemental_sources)
    sources = merge_sources(primary_sources, extra_source_items)
    ok = bool(answer or sources)
    if validation_level == "strict" and not sources:
        ok = False
    evidence_bundle = EvidenceBundle()
    evidence_bundle.add_discovery_candidates(merge_sources(primary_sources, extra_source_items))
    evidence_bundle.add_fetched_evidence(fetched_evidence)
    evidence_bundle.add_provider_attempts(provider_attempts)
    if validation_level == "strict" and not sources:
        evidence_bundle.add_gap({"subquestion_id": "", "reason": "strict 模式证据不足"})
    evidence_fields = _evidence_bundle_fields(evidence_bundle)
    execution_plan = _capability_plan_from_result(
        "search",
        command_capabilities,
        budget=profile_name,
        allow_synthesis=response_mode == "synthesized",
        response_mode=response_mode,
    )
    return {
        "ok": ok,
        "error_type": "" if ok else ("evidence_error" if validation_level == "strict" else "network_error"),
        "error": "" if ok else ("strict 模式证据不足" if validation_level == "strict" else "搜索失败或无结果"),
        "session_id": session_id,
        "query": query,
        "platform": platform,
        "model": effective_model,
        "profile": profile_name,
        "response_mode": response_mode,
        "primary_api_mode": primary_api_mode,
        "content": answer,
        "sources": sources,
        "results": sources,
        "sources_count": len(sources),
        "primary_sources": primary_sources,
        "primary_sources_count": len(primary_sources),
        "extra_sources": extra_source_items,
        "extra_sources_count": len(extra_source_items),
        "source_warning": SOURCE_PROVENANCE_WARNING if extra_source_items else "",
        "routing_decision": routing_decision,
        "providers_used": _provider_names_from_attempts(provider_attempts),
        "provider_attempts": provider_attempts,
        "fallback_used": _fallback_used(provider_attempts) or model_route_fallback_used,
        "transport_fallback_used": transport_fallback_used,
        "model_fallback_used": model_fallback_used,
        "model_route_id": successful_route_id,
        "model_route_fallback_used": model_route_fallback_used,
        "validation_level": validation_level,
        **capability_metadata,
        **evidence_fields,
        "capability_execution_plan": execution_plan.to_dict(),
        "degraded": bool(capability_metadata.get("degraded")) or evidence_bundle.degraded,
        "degraded_reason": _combined_degraded_reason(evidence_bundle, capability_metadata),
        "elapsed_ms": _elapsed_ms(start),
    }

def _primary_search_error_result(
    start: float,
    session_id: str,
    query: str,
    primary_api_mode: str,
    error_type: str,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": error_type,
        "error": error,
        "session_id": session_id,
        "query": query,
        "primary_api_mode": primary_api_mode,
        "content": "",
        "sources": [],
        "sources_count": 0,
        "primary_sources": [],
        "primary_sources_count": 0,
        "extra_sources": [],
        "extra_sources_count": 0,
        "evidence_bundle": EvidenceBundle().to_dict(),
        "discovery_candidates": [],
        "fetched_evidence": [],
        "evidence_items": [],
        "citations": [],
        "gaps": [],
        "source_warning": "",
        "elapsed_ms": _elapsed_ms(start),
    }

__all__ = [name for name in globals() if not name.startswith("__")]
