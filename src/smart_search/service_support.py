"""Shared runtime primitives and registry data for service workflows."""

import asyncio
import hashlib
import json
import re
import tempfile
import time
import os
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from .config import ConfigStorageError, config
from .evidence import CapabilityPlan, EvidenceBundle
from .intent_router import (
    CAPABILITY_UTTERANCES,
    CURRENT_INTENT_KEYWORDS as ROUTER_CURRENT_INTENT_KEYWORDS,
    DEFAULT_ROUTE_CALIBRATION_MODELS,
    DEFAULT_SEMANTIC_CONFIDENCE_MARGIN,
    DEFAULT_SEMANTIC_CONFIDENCE_THRESHOLD,
    DOCS_INTENT_KEYWORDS as ROUTER_DOCS_INTENT_KEYWORDS,
    FETCH_INTENT_KEYWORDS as ROUTER_FETCH_INTENT_KEYWORDS,
    ROUTABLE_CAPABILITIES,
    ROUTE_CALIBRATION_QUERIES,
    VERTICAL_INTENT_KEYWORDS as ROUTER_VERTICAL_INTENT_KEYWORDS,
    IntentRouteResult,
    IntentRouter,
    build_rules_route,
    extract_urls as router_extract_urls,
    _classifier_can_add_capability,
    _cosine_similarity,
    _ordered_capabilities,
    _semantic_summary,
)
from .logger import log_info, logger
from .providers.anysearch import AnySearchProvider
from .providers.base import ProviderError, ProviderResult, coerce_provider_result
from .providers.context7 import Context7Provider
from .providers.exa import ExaSearchProvider
from .providers.jina import JinaReaderProvider
from .providers.openai_compatible import OpenAICompatibleSearchProvider, get_local_time_info
from .providers.xai_responses import XAIResponsesSearchProvider
from .providers.zhipu import ZhipuWebSearchProvider
from .providers.zhipu_mcp import ZhipuMCPProvider
from .sources import merge_sources, new_session_id, split_answer_and_sources
from .runtime_cache import (
    CacheExecution,
    RuntimeTTLCache,
    add_fetch,
    add_request,
    add_retry,
    attach_metrics,
    allow_synthesis,
    cache_input,
    current_context,
    mark_budget_exhausted,
    observe_command,
    observe_stage,
    request_client,
    request_timeout_kwargs,
)
from .security import sanitize_text
from .utils import PromptConfigurationError, get_prompt


_AVAILABLE_MODELS_CACHE: dict[tuple[str, str], list[str]] = {}
_AVAILABLE_MODELS_LOCK = asyncio.Lock()
_RUNTIME_SEARCH_CACHE = RuntimeTTLCache()
_RUNTIME_FETCH_CACHE = RuntimeTTLCache()
MODEL_BREAKER_FAILURE_THRESHOLD = 2
MODEL_BREAKER_COOLDOWN_SECONDS = 600.0
_OPENAI_COMPATIBLE_MODEL_BREAKERS: dict[tuple[str, str], dict[str, Any]] = {}
SOURCE_PROVENANCE_WARNING = (
    "extra_sources are retrieved in parallel and are not automatically used to verify generated content; "
    "use fetch on key URLs for claim-level evidence."
)
MINIMUM_PROFILE_ERROR = "当前能力档位缺少可用的搜索或取证能力。"
PROFILE_NAMES = ("fast", "balanced", "deep")
CAPABILITY_PROFILE_NAMES = ("lite", "standard", "full", "off")
COMMAND_CAPABILITY_MATRIX: dict[str, dict[str, tuple[str, ...]]] = {
    "search": {
        "required": ("main_search",),
        "optional": ("docs_search", "web_search", "web_fetch"),
    },
    "fetch": {"required": ("web_fetch",), "optional": ()},
    "map": {"required": ("site_map",), "optional": ()},
    "research": {
        "required": ("web_fetch",),
        "optional": ("docs_search", "web_search"),
    },
    "route": {"required": (), "optional": ()},
    "doctor": {"required": (), "optional": ()},
    "capabilities": {"required": (), "optional": ()},
}
OPENAI_COMPATIBLE_DIAGNOSE_COMMAND = "smart-search diagnose openai-compatible --format markdown"
DOCS_INTENT_KEYWORDS = ROUTER_DOCS_INTENT_KEYWORDS
ZH_CURRENT_KEYWORDS = ROUTER_CURRENT_INTENT_KEYWORDS
FETCH_INTENT_KEYWORDS = ROUTER_FETCH_INTENT_KEYWORDS
# Retained canonical generic tools that deep/research plans may advertise.
# Removed exact Provider/Experimental spellings are never planned tools.
DEEP_ALLOWED_TOOLS = {
    "search",
    "fetch",
    "map",
}
DEEP_TRIGGER_KEYWORDS = {
    "深度搜索",
    "深度调研",
    "深入搜索",
    "deep search",
    "deep research",
    "核验",
    "验证",
    "交叉验证",
    "选型",
    "对比",
    "评测",
}
DEEP_HIGH_COMPLEXITY_KEYWORDS = {
    "对比",
    "选型",
    "核验",
    "验证",
    "为什么",
    "架构",
    "方案",
    "趋势",
    "优缺点",
    "风险",
    "区别",
    "怎么选",
    "compare",
    "comparison",
    "evaluate",
    "architecture",
    "tradeoff",
    "trade-off",
    "risk",
}
DEEP_RECENT_KEYWORDS = {
    "最近",
    "最新",
    "当前",
    "现在",
    "今天",
    "实时",
    "刚刚",
    "本周",
    "本月",
    "recent",
    "latest",
    "current",
    "today",
}
DEEP_CURRENT_KEYWORDS = {"今天", "实时", "刚刚", "当前", "现在", "today", "current", "live", "realtime"}
DEEP_CHINA_KEYWORDS = {"中国", "国内", "中文", "政策", "监管", "公告", "A股", "港股"}
DEEP_EXA_DISCOVERY_KEYWORDS = {
    "官方",
    "官网",
    "论文",
    "paper",
    "papers",
    "research paper",
    "产品页",
    "product page",
    "可信站点",
    "trusted",
    "known domain",
    "known domains",
    "site:",
    "白皮书",
    "standard",
    "standards",
}
RESEARCH_VERTICAL_KEYWORDS = ROUTER_VERTICAL_INTENT_KEYWORDS
RESEARCH_JS_HEAVY_KEYWORDS = {
    "js-heavy",
    "javascript",
    "dynamic",
    "动态页面",
    "浏览器渲染",
    "登录页",
    "cloudflare",
    "screenshot",
    "ocr",
    "扫描",
}
RESEARCH_PDF_KEYWORDS = {"pdf", "arxiv", "论文", "paper", ".pdf"}
PROVIDER_PROFILES: dict[str, dict[str, Any]] = {
    "xai-responses": {
        "capability": "main_search",
        "config_attrs": ("xai_api_key",),
        "fallback_order": {"main_search": 0},
        "strengths": ["broad synthesis", "web_search", "x_search"],
        "exclusions": ["evidence proof without fetch"],
        "fallback_group": "main_search",
        "minimum_profile_role": "main_search",
        "quality_filters": ["source extraction required for high-risk claims"],
        "route_reasons": ["broad live answer", "primary synthesis"],
    },
    "openai-compatible": {
        "capability": "main_search",
        "config_attrs": ("openai_compatible_api_url", "openai_compatible_api_key"),
        "fallback_order": {"main_search": 1},
        "strengths": ["broad synthesis", "relay compatibility"],
        "exclusions": ["xAI server tools"],
        "fallback_group": "main_search",
        "minimum_profile_role": "main_search",
        "quality_filters": ["source extraction required for high-risk claims"],
        "route_reasons": ["relay-compatible primary synthesis"],
    },
    "context7": {
        "capability": "docs_search",
        "config_attrs": ("context7_api_key",),
        "fallback_order": {"docs_search": 0},
        "strengths": ["library docs", "API docs", "framework docs", "versioned snippets"],
        "exclusions": ["general news", "generic web facts"],
        "fallback_group": "docs_search",
        "minimum_profile_role": "docs_search",
        "quality_filters": ["library id required", "content required before citation"],
        "route_reasons": ["docs/API evidence", "framework reference"],
    },
    "exa": {
        "capability": "docs_search",
        "config_attrs": ("exa_api_key",),
        "fallback_order": {"docs_search": 1},
        "strengths": ["official domains", "papers", "product pages", "trusted low-noise discovery", "similar pages"],
        "exclusions": ["default second hop for every high-risk claim"],
        "fallback_group": "docs_search",
        "minimum_profile_role": "docs_search",
        "quality_filters": ["URL required", "fetch before proof citation"],
        "route_reasons": ["official low-noise discovery", "paper/product discovery"],
    },
    "zhipu": {
        "capability": "web_search",
        "config_attrs": ("zhipu_api_key",),
        "fallback_order": {"web_search": 0},
        "strengths": ["Chinese", "domestic China", "current", "policy", "announcements", "recency filters"],
        "exclusions": ["web_fetch", "chat model selection"],
        "fallback_group": "web_search",
        "minimum_profile_role": "",
        "quality_filters": ["URL required", "fetch before proof citation"],
        "route_reasons": ["Chinese/current/policy discovery"],
    },
    "zhipu-mcp": {
        "capability": "web_search",
        "config_attrs": ("zhipu_mcp_api_key",),
        "fallback_order": {"web_search": 1},
        "strengths": ["Coding Plan quota", "remote MCP web_search_prime"],
        "exclusions": ["Zhipu REST Web Search API"],
        "fallback_group": "web_search",
        "minimum_profile_role": "",
        "quality_filters": ["URL required", "fetch before proof citation"],
        "route_reasons": ["Coding Plan quota web discovery"],
    },
    "tavily": {
        "capability": "web_search",
        "capabilities": ["web_search", "web_fetch", "site_map"],
        "config_attrs": ("tavily_api_key",),
        "enabled_attr": "tavily_enabled",
        "enabled_key": "TAVILY_ENABLED",
        "fallback_order": {"web_search": 2, "web_fetch": 0, "site_map": 0},
        "strengths": ["broad source discovery", "site map", "URL extract"],
        "exclusions": ["docs semantic replacement"],
        "fallback_group": "web_search/web_fetch/site_map",
        "minimum_profile_role": "web_fetch",
        "quality_filters": ["non-empty normalized result", "non-empty extracted content"],
        "route_reasons": ["broad source discovery", "site map", "URL fetch"],
    },
    "jina": {
        "capability": "web_fetch",
        "config_attrs": ("jina_api_key",),
        "anonymous_capable": True,
        "anonymous_endpoint_attr": "jina_reader_api_url",
        "anonymous_key_required_attrs": ("jina_respond_with",),
        "fallback_order": {"web_fetch": 1},
        "strengths": ["known public URL", "PDF", "arXiv", "clean markdown", "ReaderLM-v2 with key", "anonymous default endpoint"],
        "exclusions": ["general search provider"],
        "fallback_group": "web_fetch",
        "minimum_profile_role": "web_fetch",
        "quality_filters": ["non-empty markdown", "challenge page rejection", "ReaderLM-v2 requires key"],
        "route_reasons": ["known URL extraction", "PDF/arXiv extraction"],
    },
    "zhipu-mcp-reader": {
        "capability": "web_fetch",
        "config_attrs": ("zhipu_mcp_api_key",),
        "fallback_order": {"web_fetch": 2},
        "strengths": ["Coding Plan quota", "remote MCP webReader"],
        "exclusions": ["Zhipu REST Web Search API"],
        "fallback_group": "web_fetch",
        "minimum_profile_role": "",
        "quality_filters": ["non-empty reader content"],
        "route_reasons": ["Coding Plan quota page read"],
    },
    "zhipu-mcp-zread": {
        "capability": "zread",
        "config_attrs": ("zhipu_mcp_api_key",),
        "fallback_order": {"zread": 0},
        "strengths": ["repository docs", "repository structure", "repository file reads"],
        "exclusions": ["general docs fallback", "standard minimum profile"],
        "fallback_group": "zread",
        "minimum_profile_role": "",
        "quality_filters": ["repository target required", "Coding Plan entitlement required"],
        "route_reasons": ["explicit repository/docs command"],
        "experimental": True,
    },
    "firecrawl": {
        "capability": "web_fetch",
        "capabilities": ["web_search", "web_fetch"],
        "config_attrs": ("firecrawl_api_key",),
        "fallback_order": {"web_search": 3, "web_fetch": 3},
        "strengths": ["robust scrape fallback", "JS-heavy pages", "dynamic pages", "OCR/PDF/structured extraction"],
        "exclusions": ["docs semantic replacement"],
        "fallback_group": "web_search/web_fetch",
        "minimum_profile_role": "web_fetch",
        "quality_filters": ["non-empty normalized result", "non-empty extracted content"],
        "route_reasons": ["JS-heavy fetch", "dynamic/browser-like extraction", "robust fetch fallback"],
    },
    "anysearch": {
        "capability": "vertical_search",
        "config_attrs": ("anysearch_api_key",),
        "fallback_order": {"vertical_search": 0},
        "strengths": ["CVE", "finance", "legal", "academic", "code/docs", "structured vertical domains"],
        "exclusions": ["generic default fallback", "standard minimum profile"],
        "fallback_group": "vertical_search",
        "minimum_profile_role": "",
        "quality_filters": ["vertical intent required", "URL required before evidence citation"],
        "route_reasons": ["vertical domain discovery"],
        "experimental": True,
    },
    "main-search": {
        "capability": "synthesis",
        "config_attrs": (),
        "fallback_order": {"synthesis": 0},
        "strengths": ["evidence-only final synthesis"],
        "exclusions": ["live source discovery during research synthesis"],
        "fallback_group": "synthesis",
        "minimum_profile_role": "",
        "quality_filters": ["fetched evidence only", "no provider calls during synthesis"],
        "route_reasons": ["evidence-only synthesis"],
    },
}


"""
================================================================================
步骤1：构建 provider 注册表
================================================================================
目标：让能力归属、配置来源和同能力 fallback 顺序只从 PROVIDER_PROFILES 读取。
数据源：各 provider 的 capability、config_attrs 和 fallback_order 元数据。
操作：
1) 按 provider profile 生成每条 capability 的稳定 fallback 链。
2) 保留旧的 PROVIDER_PROFILES 名称，同时提供 registry 别名供诊断和路由使用。
"""
PROVIDER_REGISTRY = PROVIDER_PROFILES

def _elapsed_ms(start: float) -> float:
    return round((time.time() - start) * 1000, 2)

def _capability_plan(
    command: str,
    *,
    required_capabilities: list[str] | tuple[str, ...] = (),
    optional_capabilities: list[str] | tuple[str, ...] = (),
    budget: str = "",
    allow_synthesis: bool = False,
    source_only: bool = False,
    response_mode: str = "",
) -> CapabilityPlan:
    """
    /*
     * ==============================================================================
     * 步骤1：装配命令能力计划
     * ==============================================================================
     * 目标：把命令能力矩阵和当前 RequestContext 预算绑定到 CapabilityPlan。
     * 数据源：命令参数、当前 command budget 和 research budget。
     * 操作：
     * 1) 优先读取当前 RequestContext 的 provider/fetch 上限。
     * 2) 没有运行时上下文时使用命令级默认上限，保证 planner 可离线构造。
     * ==============================================================================
     */
    """
    logger.info("开始装配命令能力计划: command=%s", command)
    context = current_context()
    if context is not None:
        max_provider_attempts = context.budget.max_provider_attempts
        max_fetches = context.budget.max_fetches
    else:
        default_limits = {
            "quick": (12, 4),
            "standard": (20, 8),
            "deep": (32, 12),
        }
        max_provider_attempts, max_fetches = default_limits.get(
            (budget or "").strip().lower(),
            (32, 8),
        )
    result = CapabilityPlan(
        command=command,
        required_capabilities=tuple(required_capabilities),
        optional_capabilities=tuple(optional_capabilities),
        max_provider_attempts=max_provider_attempts,
        max_fetches=max_fetches,
        budget=budget,
        allow_synthesis=allow_synthesis,
        source_only=source_only,
        response_mode=response_mode,
    )
    logger.info(
        "命令能力计划装配完成: command=%s required=%s provider_limit=%s fetch_limit=%s",
        command,
        result.required_capabilities,
        result.max_provider_attempts,
        result.max_fetches,
    )
    return result

def _capability_plan_from_result(
    command: str,
    command_result: dict[str, Any],
    *,
    budget: str = "",
    allow_synthesis: bool = False,
    response_mode: str = "",
) -> CapabilityPlan:
    """
    /*
     * ==============================================================================
     * 步骤2：从命令预检生成能力计划
     * ==============================================================================
     * 目标：复用 validate_command_capabilities 的结果，避免重复定义依赖。
     * 数据源：required_capability_groups、optional_capabilities 和 source_only。
     * 操作：展开能力组并保留 source-only、response_mode 和 synthesis 语义。
     * ==============================================================================
     */
    """
    logger.info("开始从命令预检生成能力计划: command=%s", command)
    groups = command_result.get("required_capability_groups") or []
    required: list[str] = []
    for group in groups:
        for capability in group:
            if capability and capability not in required:
                required.append(capability)
    if not required:
        required = list(command_result.get("required_capabilities") or [])
    plan = _capability_plan(
        command,
        required_capabilities=required,
        optional_capabilities=list(command_result.get("optional_capabilities") or []),
        budget=budget,
        allow_synthesis=allow_synthesis,
        source_only=bool(command_result.get("source_only")),
        response_mode=response_mode,
    )
    logger.info("命令预检能力计划生成完成: command=%s", command)
    return plan

def _evidence_bundle_fields(bundle: EvidenceBundle) -> dict[str, Any]:
    """
    /*
     * ==============================================================================
     * 步骤3：适配 evidence bundle 到 flat JSON
     * ==============================================================================
     * 目标：让 CLI 继续读取旧字段，同时让新 evidence 对象成为唯一数据源。
     * 数据源：EvidenceBundle.to_dict() 快照。
     * 操作：输出新增嵌套字段和 evidence_items/citations 等兼容字段。
     * ==============================================================================
     */
    """
    logger.info("开始适配 evidence bundle 到 flat JSON")
    snapshot = bundle.to_dict()
    fields = {
        "evidence_bundle": snapshot,
        "discovery_candidates": snapshot["discovery_candidates"],
        "fetched_evidence": snapshot["fetched_evidence"],
        "evidence_items": snapshot["fetched_evidence"],
        "citations": snapshot["citations"],
        "gaps": snapshot["gaps"],
    }
    logger.info(
        "evidence bundle flat JSON 适配完成: candidates=%s evidence=%s",
        len(fields["discovery_candidates"]),
        len(fields["evidence_items"]),
    )
    return fields

def _combined_degraded_reason(
    bundle: EvidenceBundle,
    capability_metadata: dict[str, Any] | None = None,
) -> str:
    """
    /*
     * ==============================================================================
     * 步骤4：合并 flat degraded 原因
     * ==============================================================================
     * 目标：让兼容 flat 字段同时保留 capability 和 evidence stage 的降级原因。
     * 数据源：Capability metadata 与 EvidenceBundle.degraded_reasons。
     * 操作：
     * 1) 保留命令预检已有原因。
     * 2) 追加 synthesis、fetch 和 gap check 产生的证据原因并去重。
     * ==============================================================================
     */
    """
    logger.info("开始合并 degraded 原因")
    reasons: list[str] = []
    metadata_reason = str((capability_metadata or {}).get("degraded_reason") or "").strip()
    if metadata_reason:
        reasons.append(metadata_reason)
    reasons.extend(reason for reason in bundle.degraded_reasons if reason)
    result = "; ".join(dict.fromkeys(reasons))
    logger.info("degraded 原因合并完成: count=%s", len(reasons))
    return result

def reset_runtime_cache() -> None:
    """
    ================================================================================
    步骤1：清理运行时缓存
    ================================================================================
    目标：让测试、配置刷新和显式维护操作隔离旧的 source/content 结果。
    数据源：进程内 search/fetch TTL/LRU cache。
    操作：
    1) 清理已完成的 TTL/LRU 条目。
    2) 使清理前的 in-flight 任务不再回填新缓存。
    """

    logger.info("开始清理运行时缓存")
    _RUNTIME_SEARCH_CACHE.clear()
    _RUNTIME_FETCH_CACHE.clear()
    logger.info("运行时缓存清理完成")

def _runtime_cache_settings(cache: RuntimeTTLCache, kind: str) -> tuple[bool, int]:
    """
    ================================================================================
    步骤2：读取缓存配置快照
    ================================================================================
    目标：每次 command 都使用当前配置，避免配置刷新后沿用旧行为。
    数据源：Config 的开关、TTL 和最大条目数。
    操作：
    1) 读取并校验当前配置快照。
    2) 同步 LRU 容量；显式关闭时清理旧结果。
    """

    try:
        enabled = config.cache_enabled
        ttl_seconds = config.search_cache_ttl_seconds if kind == "search" else config.fetch_cache_ttl_seconds
        max_size = config.cache_max_size
    except ValueError as exc:
        logger.warning("运行时缓存配置无效，按关闭处理: %s", exc)
        enabled = False
        ttl_seconds = 0
        max_size = 256
    cache.configure(max_size)
    if not enabled:
        cache.clear()
    return enabled, ttl_seconds

async def _cached_runtime_call(
    cache: RuntimeTTLCache,
    *,
    capability: str,
    provider: str,
    input_value: str,
    input_kind: str,
    options: dict[str, Any],
    factory: Callable[[], Awaitable[Any]],
    cacheable: Callable[[Any], bool] | None = None,
) -> CacheExecution:
    enabled, ttl_seconds = _runtime_cache_settings(cache, "fetch" if input_kind == "url" else "search")
    normalized = cache_input(input_value, kind=input_kind)
    if normalized is None:
        return CacheExecution(await factory())

    if not enabled or ttl_seconds <= 0:
        return CacheExecution(await factory())

    key = (
        capability,
        provider,
        normalized,
        config.runtime_cache_fingerprint(
            capability,
            provider,
            {
                **options,
                "_runtime_cache_enabled": enabled,
                "_runtime_cache_ttl_seconds": ttl_seconds,
                "_runtime_cache_max_size": cache.max_size,
            },
        ),
        config.credential_epoch,
    )
    return await cache.get_or_set(
        key,
        factory,
        ttl_seconds=ttl_seconds,
        enabled=True,
        cacheable=cacheable,
    )

def _clean_cached_sources(sources: list[dict] | None) -> list[dict]:
    """
    ================================================================================
    步骤3：清理可缓存 source
    ================================================================================
    目标：缓存只保存标准化字段，不保存 provider 原始响应或敏感 URL。
    数据源：_normalize_source_results 产生的 source 列表。
    操作：
    1) 丢弃带 userinfo 或敏感 query 参数的 URL。
    2) 只保留公共 source 字段并清理文本内容。
    """

    cleaned: list[dict] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not url or cache_input(url, kind="url") is None:
            continue
        item: dict[str, Any] = {
            "url": url,
            "provider": sanitize_text(source.get("provider") or ""),
        }
        for field_name in ("title", "description", "published_date", "source"):
            value = source.get(field_name)
            if value:
                item[field_name] = sanitize_text(value)
        cleaned.append(item)
    return cleaned

async def _cached_source_provider(
    capability: str,
    provider: str,
    query: str,
    options: dict[str, Any],
    factory: Callable[[], Awaitable[list[dict]]],
) -> CacheExecution:
    async def clean_factory() -> list[dict]:
        return _clean_cached_sources(await factory())

    return await _cached_runtime_call(
        _RUNTIME_SEARCH_CACHE,
        capability=capability,
        provider=provider,
        input_value=query,
        input_kind="query",
        options=options,
        factory=clean_factory,
        cacheable=lambda value: bool(value),
    )

async def _cached_fetch_provider(
    provider: str,
    url: str,
    options: dict[str, Any],
    factory: Callable[[], Awaitable[dict[str, Any]]],
) -> CacheExecution:
    return await _cached_runtime_call(
        _RUNTIME_FETCH_CACHE,
        capability="web_fetch",
        provider=provider,
        input_value=url,
        input_kind="url",
        options=options,
        factory=factory,
        cacheable=lambda value: bool(isinstance(value, dict) and value.get("content")),
    )

async def _cached_content_provider(
    capability: str,
    provider: str,
    input_value: str,
    options: dict[str, Any],
    factory: Callable[[], Awaitable[dict[str, Any]]],
) -> CacheExecution:
    return await _cached_runtime_call(
        _RUNTIME_FETCH_CACHE,
        capability=capability,
        provider=provider,
        input_value=input_value,
        input_kind="url",
        options=options,
        factory=factory,
        cacheable=lambda value: bool(isinstance(value, dict) and value.get("content")),
    )

def _cache_attempt_extra(execution: CacheExecution) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if execution.cache_hit:
        extra["cache_hit"] = True
    if execution.inflight_joined:
        extra["inflight_joined"] = True
    return extra

def _normalize_domain_filter(value: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    if not value:
        return None

    raw_parts = [value] if isinstance(value, str) else [str(item) for item in value if item]
    domains: list[str] = []
    for part in raw_parts:
        domains.extend(item.strip() for item in re.split(r"[\s,]+", part) if item.strip())
    return domains or None

def _attempt(
    capability: str,
    provider: str,
    status: str,
    start: float,
    result_count: int = 0,
    error_type: str = "",
    error: str = "",
    retryable: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "capability": capability,
        "provider": provider,
        "status": status,
        "error_type": error_type,
        "error": sanitize_text(error),
        "elapsed_ms": _elapsed_ms(start),
        "result_count": result_count,
    }
    if retryable is not None:
        data["retryable"] = bool(retryable)
    if extra:
        data.update(extra)
        data["error"] = sanitize_text(str(data.get("error") or ""))
    return data

def _budget_exhausted_attempt(capability: str, provider: str = "request-budget") -> dict[str, Any]:
    context = current_context()
    reason = "request budget exhausted"
    if context is not None and context.budget.exhausted_reason:
        reason = f"request budget exhausted: {context.budget.exhausted_reason}"
    return _attempt(
        capability,
        provider,
        "skipped",
        time.time(),
        error_type="budget_exhausted",
        error=reason,
        retryable=False,
        extra={"budget_exhausted": True},
    )

def _openai_model_breaker_key(api_url: str, model: str) -> tuple[str, str]:
    return (api_url.rstrip("/"), model)

def reset_runtime_breakers() -> None:
    _OPENAI_COMPATIBLE_MODEL_BREAKERS.clear()

def _openai_model_breaker_state(api_url: str, model: str) -> dict[str, Any]:
    key = _openai_model_breaker_key(api_url, model)
    state = _OPENAI_COMPATIBLE_MODEL_BREAKERS.get(key, {})
    opened_until = float(state.get("opened_until") or 0.0)
    now = time.monotonic()
    if opened_until and opened_until > now:
        return {
            "state": "open",
            "opened_until_seconds": round(opened_until - now, 3),
            "consecutive_failures": int(state.get("consecutive_failures") or 0),
        }
    if opened_until and opened_until <= now:
        _OPENAI_COMPATIBLE_MODEL_BREAKERS.pop(key, None)
        state = {}
    return {"state": "closed", "consecutive_failures": int(state.get("consecutive_failures") or 0)}

def _record_openai_model_success(api_url: str, model: str) -> None:
    _OPENAI_COMPATIBLE_MODEL_BREAKERS.pop(_openai_model_breaker_key(api_url, model), None)

def _record_openai_model_failure(api_url: str, model: str) -> dict[str, Any]:
    key = _openai_model_breaker_key(api_url, model)
    state = _OPENAI_COMPATIBLE_MODEL_BREAKERS.setdefault(key, {"consecutive_failures": 0, "opened_until": 0.0})
    state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
    if state["consecutive_failures"] >= MODEL_BREAKER_FAILURE_THRESHOLD:
        state["opened_until"] = time.monotonic() + MODEL_BREAKER_COOLDOWN_SECONDS
    return _openai_model_breaker_state(api_url, model)

def _openai_model_candidates(provider_config: dict[str, Any], *, fallback_mode: str, model_override: str) -> list[dict[str, Any]]:
    primary_model = provider_config["model"]
    candidates = [
        {
            **provider_config,
            "model": primary_model,
            "model_role": "primary",
            "fallback_from_model": "",
            "stream": provider_config.get("stream", False),
        }
    ]
    if fallback_mode == "off" or model_override:
        return candidates
    for fallback_model in provider_config.get("fallback_models") or []:
        candidates.append(
            {
                **provider_config,
                "model": fallback_model,
                "model_role": "fallback",
                "fallback_from_model": primary_model,
                "stream": False,
            }
        )
    return candidates

def _remaining_budget_seconds(start: float, timeout_seconds: float | None) -> float | None:
    context = current_context()
    if context is not None and context.deadline is not None:
        return context.remaining_seconds()
    if timeout_seconds is None:
        return None
    return max(0.0, float(timeout_seconds) - (time.time() - start))

def _attempt_timeout_seconds(
    search_start: float,
    timeout_seconds: float | None,
    remaining_candidates: int,
) -> float | None:
    remaining_budget = _remaining_budget_seconds(search_start, timeout_seconds)
    if remaining_budget is None:
        return None
    if remaining_budget <= 0:
        return 0.001
    if remaining_candidates <= 1:
        return max(0.001, remaining_budget)
    return max(0.001, min(30.0, remaining_budget / 2.0))

def _append_openai_transport_attempts(
    provider_attempts: list[dict],
    search_provider: Any,
    candidate_config: dict[str, Any],
    attempt_extra: dict[str, Any] | None = None,
) -> bool:
    transport_attempts = getattr(search_provider, "last_transport_attempts", [])
    if candidate_config.get("provider") != "openai-compatible" or not transport_attempts:
        return False
    for transport_attempt in transport_attempts:
        transport_extra = {
            key: value
            for key, value in transport_attempt.items()
            if key not in {"status", "error_type", "error", "elapsed_ms", "result_count"}
        }
        if candidate_config.get("route_id"):
            transport_extra["route_id"] = candidate_config["route_id"]
        if attempt_extra and attempt_extra.get("fallback_from_route"):
            transport_extra["fallback_from_route"] = attempt_extra["fallback_from_route"]
        if candidate_config.get("fallback_from_model"):
            transport_extra["fallback_from_model"] = candidate_config["fallback_from_model"]
        provider_attempts.append(
            {
                **_attempt(
                    "main_search",
                    search_provider.get_provider_name(),
                    transport_attempt.get("status", "error"),
                    time.time(),
                    result_count=int(transport_attempt.get("result_count") or 0),
                    error_type=transport_attempt.get("error_type", ""),
                    error=transport_attempt.get("error", ""),
                    retryable=transport_attempt.get("retryable"),
                ),
                "elapsed_ms": transport_attempt.get("elapsed_ms", 0),
                **transport_extra,
            }
        )
    return True

def _normalize_source_results(results: list[dict] | None, provider: str) -> list[dict]:
    normalized: list[dict] = []
    for item in results or []:
        url = (item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        out = {"url": url, "provider": item.get("provider") or provider}
        title = (item.get("title") or "").strip()
        if title:
            out["title"] = title
        desc = (item.get("description") or item.get("content") or item.get("snippet") or "").strip()
        if desc:
            out["description"] = desc
        published = item.get("published_date") or item.get("publishedDate") or item.get("publish_date")
        if published:
            out["published_date"] = published
        source = item.get("source") or item.get("media")
        if source:
            out["source"] = source
        normalized.append(out)
    return normalized

def _provider_names_from_attempts(attempts: list[dict]) -> list[str]:
    names: list[str] = []
    for attempt in attempts:
        provider = attempt.get("provider")
        if attempt.get("status") == "ok" and provider and provider not in names:
            names.append(provider)
    return names

def _fallback_used(attempts: list[dict]) -> bool:
    by_capability: dict[str, list[dict]] = {}
    for attempt in attempts:
        capability = attempt.get("capability", "")
        if attempt.get("status") in {"ok", "empty", "error", "skipped"}:
            by_capability.setdefault(capability, []).append(attempt)
    for capability_attempts in by_capability.values():
        previous_failed = False
        previous_identity = ""
        for attempt in capability_attempts:
            provider = attempt.get("provider", "")
            model = str(attempt.get("model") or "")
            identity = f"{provider}:{model}" if provider == "OpenAI-compatible" and model else provider
            status = attempt.get("status")
            if previous_identity and identity and identity != previous_identity:
                return True
            if previous_failed and identity != previous_identity:
                return True
            previous_failed = status in {"empty", "error", "skipped"}
            previous_identity = identity or previous_identity
    return False

def _is_docs_intent(query: str) -> bool:
    return build_rules_route(query, mode="rules").docs_intent

def _is_zh_current_intent(query: str) -> bool:
    return build_rules_route(query, mode="rules").zh_current_intent

def _is_web_current_intent(query: str) -> bool:
    return build_rules_route(query, mode="rules").web_current_intent

def _is_fetch_intent(query: str) -> bool:
    return build_rules_route(query, mode="rules").fetch_intent

def _contains_any(query: str, keywords: set[str]) -> bool:
    q = query.lower()
    return any(keyword.lower() in q for keyword in keywords)

def _extract_urls(query: str) -> list[str]:
    return router_extract_urls(query)

__all__ = [name for name in globals() if not name.startswith("__")]
