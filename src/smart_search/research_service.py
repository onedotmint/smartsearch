"""Offline Deep Research planning and live evidence workflow."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .capability_service import (
    _apply_research_overrides,
    _command_capability_metadata,
    _configured_for_capability,
    _safe_provider_overrides,
    validate_command_capabilities,
    validate_minimum_profile,
)
from .config import config
from .evidence_operations import DocsDiscoveryRequest, docs_discovery
from .execution_primitives import ExecutionCandidate, project_attempts_dict
from .evidence import EvidenceBundle
from .intent_router import IntentRouteResult, IntentRouter, build_rules_route
from .logger import logger
from .runtime_cache import allow_synthesis, attach_metrics, normalize_url, observe_command, observe_stage
from .research_plan import ResearchPlan, ResearchPlanOperation, build_research_plan
from .research_plan_render import (
    build_projection_context,
    projection_entry,
    render_v1_steps,
)
from .operation_runtime import _run_web_fetch_fallback, _run_web_search_fallback
from .security import sanitize_text
from .service_support import (
    DEEP_ALLOWED_TOOLS,
    DEEP_CHINA_KEYWORDS,
    DEEP_CURRENT_KEYWORDS,
    DEEP_EXA_DISCOVERY_KEYWORDS,
    DEEP_HIGH_COMPLEXITY_KEYWORDS,
    DEEP_RECENT_KEYWORDS,
    MINIMUM_PROFILE_ERROR,
    RESEARCH_JS_HEAVY_KEYWORDS,
    RESEARCH_PDF_KEYWORDS,
    RESEARCH_ROUTE_POLICY_VERSION,
    _capability_plan,
    _capability_plan_from_result,
    _combined_degraded_reason,
    _contains_any,
    _elapsed_ms,
    _evidence_bundle_fields,
    _extract_urls,
    _fallback_used,
    _is_docs_intent,
    _is_zh_current_intent,
    _provider_names_from_attempts,
)

RESEARCH_FETCH_CONCURRENCY = 4

def _research_fetch_order(query: str, url: str = "", capability_status: dict[str, Any] | None = None) -> list[str]:
    providers = _configured_for_capability("web_fetch", capability_status)
    target = f"{query} {url}".lower()
    if _contains_any(target, RESEARCH_JS_HEAVY_KEYWORDS):
        preferred = ["firecrawl", "tavily", "jina", "zhipu-mcp-reader"]
    elif _contains_any(target, RESEARCH_PDF_KEYWORDS) or url.lower().endswith(".pdf"):
        preferred = ["jina", "tavily", "zhipu-mcp-reader", "firecrawl"]
    elif url or _extract_urls(query):
        preferred = ["jina", "tavily", "zhipu-mcp-reader", "firecrawl"]
    else:
        preferred = providers
    ordered = [provider for provider in preferred if provider in providers]
    ordered.extend(provider for provider in providers if provider not in ordered)
    return _apply_research_overrides("web_fetch", ordered)

def _research_route_signals(question: str, plan: dict[str, Any]) -> dict[str, Any]:
    intent = plan.get("intent_signals") or {}
    rules_route = build_rules_route(question, plan_intent_signals=intent, mode="rules")
    text = question.lower()
    return {
        "docs_api_intent": rules_route.docs_intent,
        "official_low_noise_intent": _contains_any(question, DEEP_EXA_DISCOVERY_KEYWORDS),
        "current_or_locale_intent": rules_route.web_current_intent,
        "known_url": rules_route.fetch_intent,
        "pdf_or_arxiv_intent": _contains_any(question, RESEARCH_PDF_KEYWORDS),
        "js_heavy_intent": _contains_any(question, RESEARCH_JS_HEAVY_KEYWORDS),
        "claim_risk": intent.get("claim_risk", "medium"),
        "cross_validation_need": intent.get("cross_validation_need", "normal"),
        "raw_query": text,
    }

def _research_capability_routes(
    question: str,
    plan: dict[str, Any],
    fallback: str,
    capability_status: dict[str, Any] | None = None,
    route_result: IntentRouteResult | None = None,
) -> dict[str, Any]:
    signals = _research_route_signals(question, plan)
    if route_result is not None:
        signals["docs_api_intent"] = route_result.docs_intent
        signals["current_or_locale_intent"] = route_result.web_current_intent
        signals["known_url"] = route_result.fetch_intent
    _, _, invalid_overrides = _safe_provider_overrides()
    routes: dict[str, Any] = {
        "signals": signals,
        "fallback_mode": fallback,
        "route_policy_version": RESEARCH_ROUTE_POLICY_VERSION,
        "invalid_provider_overrides": invalid_overrides,
        "capabilities": {},
    }
    if route_result is not None:
        route_data = route_result.to_dict()
        for key in (
            "intent_router_mode",
            "required_capabilities",
            "intent_signals",
            "confidence",
            "router_engines_used",
            "degraded",
            "degraded_reason",
            "reasons",
        ):
            routes[key] = route_data.get(key)

    web_search = _configured_for_capability("web_search", capability_status)
    if signals["current_or_locale_intent"]:
        ordered = [provider for provider in ["zhipu", "zhipu-mcp", "tavily", "firecrawl"] if provider in web_search]
    else:
        ordered = [provider for provider in ["tavily", "firecrawl", "zhipu", "zhipu-mcp"] if provider in web_search]
    routes["capabilities"]["web_search"] = {
        "providers": _apply_research_overrides("web_search", ordered),
        "reason": "current/locale evidence" if signals["current_or_locale_intent"] else "broad source discovery",
    }

    docs = _configured_for_capability("docs_search", capability_status)
    docs_order = [provider for provider in ["context7", "exa"] if provider in docs]
    if signals["official_low_noise_intent"] and not signals["docs_api_intent"]:
        docs_order = [provider for provider in ["exa", "context7"] if provider in docs]
    routes["capabilities"]["docs_search"] = {
        "providers": _apply_research_overrides("docs_search", docs_order),
        "reason": "docs/API evidence" if signals["docs_api_intent"] else "official low-noise discovery",
    }

    fetch_order = _research_fetch_order(question, capability_status=capability_status)
    routes["capabilities"]["web_fetch"] = {
        "providers": fetch_order,
        "reason": "JS-heavy fetch" if signals["js_heavy_intent"] else ("known URL/PDF extraction" if signals["known_url"] or signals["pdf_or_arxiv_intent"] else "evidence extraction"),
    }

    return routes

def _research_evidence_item(
    *,
    url: str,
    provider: str,
    title: str = "",
    content: str = "",
    source_type: str = "fetched_page",
    subquestion_id: str = "",
) -> dict[str, Any]:
    digest = hashlib.sha1(f"{url}\n{provider}\n{title}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"e{digest}",
        "url": url,
        "title": title or url,
        "provider": provider,
        "source_type": source_type,
        "subquestion_id": subquestion_id,
        "content": content,
        "content_len": len(content or ""),
        "verified": bool(content and content.strip()),
    }

def _citation_items(evidence_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence_bundle = EvidenceBundle()
    evidence_bundle.add_fetched_evidence(evidence_items)
    return evidence_bundle.to_dict()["citations"]


def _typed_candidates_to_sources(candidates) -> list[dict[str, Any]]:
    """Project typed Evidence candidates into v1 discovery source dicts.

    ``resource`` is the candidate's stable identity (HTTP URL or resource id,
    e.g. ``context7:<id>``). Non-HTTP resource ids stay discovery-only: the
    candidate fetch stage skips them, so only URL-backed candidates can become
    fetched evidence under the generic Evidence policy.
    """
    sources: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, ExecutionCandidate):
            continue
        resource = (candidate.resource or "").strip()
        if not resource:
            continue
        item: dict[str, Any] = {
            "url": resource,
            "provider": candidate.provider,
        }
        title = (candidate.title or "").strip()
        if title:
            item["title"] = title
        snippet = (candidate.snippet or "").strip()
        if snippet:
            item["description"] = snippet
        sources.append(item)
    return sources


async def _run_research_docs_discovery(
    question: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run docs discovery through the generic typed Evidence owner.

    Replaces the removed Context7/Exa provider-specific research callbacks:
    the typed ``docs_discovery`` owner selects qualified docs providers and
    returns candidates only. URL-backed candidates later enter the generic
    fetch stage; resource-id candidates (``context7:...``) remain discovery
    candidates and never become fetched evidence.
    """
    outcome = await docs_discovery(DocsDiscoveryRequest(query=question, max_results=5))
    return _typed_candidates_to_sources(outcome.candidates), project_attempts_dict(outcome.attempts)

def _evidence_only_synthesis(
    question: str,
    evidence: EvidenceBundle | list[dict[str, Any]],
    gaps: list[dict[str, Any]] | None = None,
) -> str:
    """
    /*
     * ==============================================================================
     * 步骤8：只基于 EvidenceBundle 生成 synthesis
     * ==============================================================================
     * 目标：禁止 synthesis 重新调用 search/fetch provider，保证输入只有已读正文。
     * 数据源：EvidenceBundle.fetched_evidence 和 gap check 结果。
     * 操作：兼容旧的 list 调用，但立即转换为 EvidenceBundle 后再读取内容。
     * ==============================================================================
     */
    """
    logger.info("开始执行 evidence-only synthesis: question=%s", question)
    if isinstance(evidence, EvidenceBundle):
        evidence_bundle = evidence
    else:
        evidence_bundle = EvidenceBundle()
        evidence_bundle.add_fetched_evidence(evidence)
        for gap in gaps or []:
            evidence_bundle.add_gap(gap)
    evidence_items = evidence_bundle.fetched_evidence
    resolved_gaps = evidence_bundle.gaps if gaps is None else gaps
    if not evidence_items:
        result = (
            f"未能为 `{question}` 获取可引用的页面正文证据。"
            "本次 research 已停止在降级状态，未对缺证据的结论做断言。"
        )
        logger.info("evidence-only synthesis 完成: 无 fetched evidence")
        return result
    lines = [f"Research result for: {question}", ""]
    lines.append("Evidence-backed findings:")
    for index, item in enumerate(evidence_items, 1):
        content = re.sub(r"\s+", " ", (item.get("content") or "").strip())
        excerpt = content[:360]
        lines.append(f"{index}. {item.get('title') or item.get('url')} ({item.get('provider')})")
        if excerpt:
            lines.append(f"   Evidence excerpt: {excerpt}")
        lines.append(f"   Source: {item.get('url')}")
    if resolved_gaps:
        lines.extend(["", "Unverified gaps:"])
        for gap in resolved_gaps:
            lines.append(f"- {gap.get('subquestion_id', '')}: {gap.get('reason', '')}")
    result = "\n".join(lines).strip()
    logger.info("evidence-only synthesis 完成: evidence=%s", len(evidence_items))
    return result

def _research_fetch_key(url: str) -> str:
    """
    /*
     * ================================================================================
     * 步骤1：生成 research URL 去重键
     * ================================================================================
     * 目标：让等价公开 URL 在同一次 research 中只进入一个 fetch 批次。
     * 数据源：已知 URL 或 discovery candidate 的原始 URL。
     * 操作：
     * 1) 复用 runtime_cache.normalize_url() 的公开 URL 规则。
     * 2) 敏感或无法规范化的 URL 保留原始字符串，只做精确去重。
     * ================================================================================
     */
    """
    # 1.1 不把敏感 URL 写入新的规范化键，沿用原始字符串边界。
    normalized = normalize_url(url)
    return normalized or url


def _prepare_research_fetch_entries(
    entries: list[dict[str, Any]],
    *,
    seen_keys: set[str],
) -> list[dict[str, Any]]:
    """
    /*
     * ================================================================================
     * 步骤2：按 URL 去重构建 research fetch 批次
     * ================================================================================
     * 目标：保留第一个原始 URL，同时阻止等价变体重复消耗 fetch 预算。
     * 数据源：带计划索引、URL 和 provider 顺序的 fetch 条目。
     * 操作：
     * 1) 用规范化键判断是否已经由更早条目拥有。
     * 2) 复制首个条目并保留其原始索引，供后续稳定归并。
     * ================================================================================
     */
    """
    logger.info("开始构建 research fetch 批次: input=%s", len(entries))
    prepared: list[dict[str, Any]] = []
    for entry in entries:
        # 2.1 读取并清理展示/请求使用的原始 URL。
        url = str(entry.get("url") or "").strip()
        if not url:
            continue

        # 2.2 首个规范化键拥有本次 fetch；失败后也不允许变体重试。
        dedupe_key = _research_fetch_key(url)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        prepared.append({**entry, "url": url, "dedupe_key": dedupe_key})

    logger.info("research fetch 批次构建完成: scheduled=%s", len(prepared))
    return prepared


async def _run_research_fetch_batch(
    entries: list[dict[str, Any]],
    *,
    fallback: str,
    stage: str,
) -> list[dict[str, Any]]:
    """
    /*
     * ================================================================================
     * 步骤3：受控并发执行 research fetch 批次
     * ================================================================================
     * 目标：并发读取独立 URL，同时保持单个 URL 内的 capability fallback 串行。
     * 数据源：已去重的 fetch 条目、fallback 模式和当前 research stage。
     * 操作：
     * 1) 用固定 semaphore 限制同时进入 web_fetch 的 URL 数量。
     * 2) 用 gather(return_exceptions=True) 隔离单条异常或取消。
     * 3) 按输入条目顺序返回结果，供调用方稳定合并 evidence 和 artifact。
     * ================================================================================
     */
    """
    logger.info("开始并发抓取 research URL: stage=%s count=%s", stage, len(entries))
    if not entries:
        logger.info("research URL 并发抓取完成: stage=%s success=0", stage)
        return []

    semaphore = asyncio.Semaphore(RESEARCH_FETCH_CONCURRENCY)

    async def fetch_entry(entry: dict[str, Any]) -> dict[str, Any]:
        # 3.1 每个 URL 独占一个限流槽位，内部 fallback 仍由共享 executor 串行处理。
        async with semaphore:
            with observe_stage(stage):
                fetch_result, attempts = await _run_web_fetch_fallback(
                    entry["url"],
                    fallback=fallback,
                    preferred_order=entry["preferred_order"],
                )
        error_type = (
            "budget_exhausted"
            if any(attempt.get("error_type") == "budget_exhausted" for attempt in attempts)
            else ""
        )
        return {
            "entry": entry,
            "fetch_result": fetch_result,
            "attempts": attempts,
            "error_type": error_type,
        }

    # 3.2 gather 保留输入顺序；异常结果在归并时转成该 URL 的失败状态。
    outcomes = await asyncio.gather(*(fetch_entry(entry) for entry in entries), return_exceptions=True)
    results: list[dict[str, Any]] = []
    for entry, outcome in zip(entries, outcomes):
        if isinstance(outcome, BaseException):
            error_type = "cancelled" if isinstance(outcome, asyncio.CancelledError) else type(outcome).__name__.lower()
            results.append(
                {
                    "entry": entry,
                    "fetch_result": None,
                    "attempts": [],
                    "error_type": error_type,
                }
            )
        else:
            results.append(outcome)

    success_count = sum(1 for result in results if result["fetch_result"])
    logger.info("research URL 并发抓取完成: stage=%s success=%s", stage, success_count)
    return results


def _select_candidate_urls(sources: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        url = (source.get("url") or "").strip()
        if not url or url.startswith("context7:"):
            continue
        dedupe_key = _research_fetch_key(url)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        selected.append(source)
        if len(selected) >= limit:
            break
    return selected

def _artifact_path(evidence_root: str, name: str) -> Path:
    return Path(evidence_root) / name

def _write_research_artifact(evidence_root: str, name: str, data: Any) -> None:
    root = Path(evidence_root)
    root.mkdir(parents=True, exist_ok=True)
    path = _artifact_path(evidence_root, name)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _research_artifacts_enabled(evidence_dir: str) -> bool:
    """
    /*
     * ==============================================================================
     * 步骤9：决定 research artifact 持久化
     * ==============================================================================
     * 目标：默认只在内存中传递证据，避免无显式意图时写入临时目录。
     * 数据源：显式 evidence_dir 和 SMART_SEARCH_PERSIST_EVIDENCE 开关。
     * 操作：显式路径优先；持久化开关开启时使用生成的默认目录。
     * ==============================================================================
     */
    """
    logger.info("开始判断 research artifact 持久化: evidence_dir=%s", bool(evidence_dir.strip()))
    persist_flag = os.getenv("SMART_SEARCH_PERSIST_EVIDENCE", "").strip().lower()
    enabled = bool(evidence_dir.strip()) or persist_flag in {"1", "true", "yes", "on"}
    logger.info("research artifact 持久化判断完成: enabled=%s", enabled)
    return enabled

def _slugify_query(query: str) -> str:
    slug = re.sub(r"https?://", "", query.lower())
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", slug, flags=re.IGNORECASE)
    slug = slug.strip("-")
    return slug[:48] or "deep-research"

def _default_evidence_dir(query: str) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M")
    return str(Path(tempfile.gettempdir()) / "smart-search-evidence" / f"{timestamp}-{_slugify_query(query)}")

def _deep_capability(capability: str, tools: list[str], reason: str) -> dict[str, Any]:
    return {"capability": capability, "tools": tools, "reason": reason}

def _deep_subquestion(sub_id: str, question: str, reason: str, required_capabilities: list[str]) -> dict[str, Any]:
    return {
        "id": sub_id,
        "question": question,
        "reason": reason,
        "required_capabilities": required_capabilities,
    }

def _deep_budget(value: str) -> str:
    budget = (value or "standard").strip().lower()
    return budget if budget in {"quick", "standard", "deep"} else "standard"

def _is_deep_complex(query: str, budget: str) -> bool:
    q = re.sub(r"https?://[^\s<>\]\)\"']+", "", query)
    object_separators = len(re.findall(r"[/、,，]| 和 | 与 | vs | VS | versus ", q))
    return budget == "deep" or _contains_any(query, DEEP_HIGH_COMPLEXITY_KEYWORDS) or object_separators >= 2

def _build_deep_research_plan_impl(
    query: str, budget: str = "standard", evidence_dir: str = ""
) -> tuple[dict[str, Any], ResearchPlan]:
    """
    Offline Deep Research planner core.

    Builds one structured ResearchPlan plus a non-serialized v1 projection
    context, then derives frozen steps[]/command/output_path from the renderer.
    Heuristics and all other v1 plan fields remain unchanged. Returns the v1
    projection dict together with the schema-neutral typed plan so the strict
    Research Workflow owner can reuse the same plan without the v1 surface.
    """
    start = time.time()
    question = query.strip()
    budget = _deep_budget(budget)
    evidence_root = evidence_dir.strip() or _default_evidence_dir(question)
    urls = _extract_urls(question)
    known_url = bool(urls)
    docs_intent = _is_docs_intent(question)
    zh_current_intent = _is_zh_current_intent(question)
    recency_requirement = "none"
    if _contains_any(question, DEEP_CURRENT_KEYWORDS) or zh_current_intent:
        recency_requirement = "current"
    elif _contains_any(question, {"行情", "价格", "走势", "币圈", "股票", "市场"}) and _contains_any(question, DEEP_RECENT_KEYWORDS):
        recency_requirement = "current"
    elif _contains_any(question, DEEP_RECENT_KEYWORDS):
        recency_requirement = "recent"
    locale_domain_scope = "china" if _contains_any(question, DEEP_CHINA_KEYWORDS) else "global"
    if known_url:
        locale_domain_scope = "known_domains"
    claim_risk = "high" if recency_requirement in {"recent", "current"} or _contains_any(question, {"核验", "验证", "真假", "价格", "行情", "财经", "医疗", "政策", "监管", "risk"}) else "medium"
    cross_validation_need = "high" if claim_risk == "high" or _contains_any(question, {"对比", "选型", "核验", "验证", "compare", "versus"}) else "normal"
    authority_need = "high" if docs_intent or claim_risk == "high" or _contains_any(question, {"官方", "文档", "论文", "标准", "政策", "监管", "official"}) else "normal"
    complex_query = _is_deep_complex(question, budget)
    difficulty = "high" if complex_query else "standard"

    intent_signals = {
        "recency_requirement": recency_requirement,
        "docs_api_intent": docs_intent,
        "locale_domain_scope": locale_domain_scope,
        "known_url": known_url,
        "source_authority_need": authority_need,
        "claim_risk": claim_risk,
        "cross_validation_need": cross_validation_need,
        "breadth_depth_budget": budget,
    }

    decomposition: list[dict[str, Any]] = []
    capability_plan: list[dict[str, Any]] = []
    structured_ops: list[ResearchPlanOperation] = []
    projection_entries: list[Any] = []
    pending_tools: list[str] = []

    def next_op_id(prefix: str) -> str:
        return f"{prefix}-{len(structured_ops) + 1}"

    def next_filename(suffix: str) -> str:
        return f"{len(structured_ops) + 1:02d}-{suffix}"

    def add_structured(
        *,
        operation: str,
        renderer_kind: str,
        purpose: str,
        subquestion_id: str,
        input_data: dict[str, Any],
        constraints: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
        args: dict[str, Any] | None = None,
        output_suffix: str,
        tool_for_filter: str | None = None,
    ) -> None:
        tool_name = tool_for_filter
        if tool_name is None:
            from .research_plan_render import RENDERER_KIND_TO_TOOL
            tool_name = RENDERER_KIND_TO_TOOL[renderer_kind]
        if tool_name not in DEEP_ALLOWED_TOOLS:
            return
        op = ResearchPlanOperation(
            id=next_op_id(operation.replace("_", "-")),
            operation=operation,
            input=input_data,
            constraints=constraints or {},
            depends_on=tuple(depends_on or ()),
        )
        entry = projection_entry(
            op,
            renderer_kind=renderer_kind,
            purpose=purpose,
            subquestion_id=subquestion_id,
            args=args or dict(input_data),
            output_suffix=output_suffix,
        )
        structured_ops.append(op)
        projection_entries.append(entry)
        pending_tools.append(tool_name)

    def has_capability(name: str) -> bool:
        return any(item.get("capability") == name for item in capability_plan)

    def has_tool(tool: str) -> bool:
        return tool in pending_tools

    def has_purpose(prefix: str) -> bool:
        return any(entry.purpose.startswith(prefix) for entry in projection_entries)

    if known_url:
        url = urls[0]
        parsed = urlparse(url)
        host = parsed.netloc or "provided URL"
        decomposition.append(
            _deep_subquestion(
                "sq1",
                f"这个已知来源页面本身说了什么？{url}",
                "用户已经给出 URL，Deep Research 必须先抓正文再扩展。",
                ["page_evidence"],
            )
        )
        decomposition.append(
            _deep_subquestion(
                "sq2",
                f"围绕 {host} 还需要哪些相邻来源或交叉来源？",
                "已知好 URL 适合用相似页面和广泛发现扩展证据。",
                ["adjacent_source_discovery", "broad_discovery"],
            )
        )
        capability_plan.extend(
            [
                _deep_capability("page_evidence", ["fetch"], "Fetch the user-provided URL before making claims."),
                _deep_capability("adjacent_source_discovery", ["search"], "Find pages adjacent to the known source."),
                _deep_capability("broad_discovery", ["search"], "Broaden the context if the fetched page leaves gaps."),
            ]
        )
        add_structured(
            operation="content_fetch",
            renderer_kind="fetch",
            purpose="fetch user supplied URL first",
            subquestion_id="sq1",
            input_data={"resource": url},
            args={"url": url},
            output_suffix="01-fetch.md",
        )
        fetch_id = structured_ops[-1].id if structured_ops else ""
        add_structured(
            operation="source_discovery",
            renderer_kind="search",
            purpose="find adjacent sources from the provided URL",
            subquestion_id="sq2",
            input_data={"resource": url, "mode": "similar"},
            args={"url": url, "extra_sources": 2},
            depends_on=[fetch_id] if fetch_id else [],
            output_suffix="02-similar.json",
        )
        add_structured(
            operation="source_discovery",
            renderer_kind="search",
            purpose="broad discovery for missing context",
            subquestion_id="sq2",
            input_data={"query": question},
            constraints={"max_results": 1},
            args={"query": question, "extra_sources": 1},
            depends_on=[fetch_id] if fetch_id else [],
            output_suffix="03-search.json",
        )
    else:
        decomposition.append(
            _deep_subquestion(
                "sq1",
                f"{question} 的整体问题轮廓和候选来源是什么？",
                "先做 broad discovery，避免一开始把问题拆错。",
                ["broad_discovery"],
            )
        )
        capability_plan.append(_deep_capability("broad_discovery", ["search"], "Find the initial answer shape and candidate sources."))
        extra = 1 if budget == "quick" else 3
        add_structured(
            operation="source_discovery",
            renderer_kind="search",
            purpose="broad discovery and routing metadata",
            subquestion_id="sq1",
            input_data={"query": question},
            constraints={"max_results": extra},
            args={"query": question, "extra_sources": extra},
            output_suffix="01-search.json",
        )
        primary_id = structured_ops[-1].id if structured_ops else ""

        if docs_intent:
            decomposition.append(
                _deep_subquestion(
                    "sq2",
                    f"{question} 的官方文档、API 或 SDK 证据在哪里？",
                    "docs/API intent should resolve library and official documentation first.",
                    ["docs_source_discovery", "page_evidence"],
                )
            )
            capability_plan.append(
                _deep_capability(
                    "docs_source_discovery",
                    ["search"],
                    "Resolve official library/API documentation first; use generic search for official-domain or supplemental discovery.",
                )
            )
            library_hint = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", question)[:2]) or "<library-name>"
            add_structured(
                operation="docs_discovery",
                renderer_kind="search",
                purpose="resolve library id for docs/API intent",
                subquestion_id="sq2",
                input_data={"query": question, "library_hint": library_hint, "mode": "library"},
                args={"query": question, "extra_sources": 2},
                depends_on=[primary_id] if primary_id else [],
                output_suffix=next_filename("docs.json"),
            )
            lib_id = structured_ops[-1].id if structured_ops else primary_id
            add_structured(
                operation="docs_discovery",
                renderer_kind="search",
                purpose="retrieve docs after selecting the best library_id",
                subquestion_id="sq2",
                input_data={"query": question, "mode": "docs"},
                args={"query": question, "extra_sources": 2},
                depends_on=[lib_id] if lib_id else [],
                output_suffix=next_filename("docs.json"),
            )
            if _contains_any(question, DEEP_EXA_DISCOVERY_KEYWORDS):
                capability_plan.append(
                    _deep_capability(
                        "official_domain_discovery",
                        ["search"],
                        "Use generic search for official-domain or low-noise supplemental docs discovery.",
                    )
                )
                add_structured(
                    operation="docs_discovery",
                    renderer_kind="search",
                    purpose="official-domain docs source discovery",
                    subquestion_id="sq2",
                    input_data={"query": f"{question} official docs"},
                    args={"query": f"{question} official docs", "extra_sources": 2},
                    depends_on=[lib_id] if lib_id else [],
                    output_suffix=next_filename("docs.json"),
                )

        if recency_requirement != "none" or locale_domain_scope == "china":
            sub_id = f"sq{len(decomposition) + 1}"
            decomposition.append(
                _deep_subquestion(
                    sub_id,
                    f"{question} 的最新或中文/国内来源如何交叉验证？",
                    "Current or China-scoped prompts benefit from supplemental web-search reinforcement.",
                    ["current_or_locale_source_discovery"],
                )
            )
            capability_plan.append(
                _deep_capability("current_or_locale_source_discovery", ["search"], "Reinforce Chinese, domestic, or current web evidence.")
            )
            add_structured(
                operation="source_discovery",
                renderer_kind="search",
                purpose="current or locale-specific source discovery",
                subquestion_id=sub_id,
                input_data={"query": question, "locale": "zh"},
                args={"query": question, "extra_sources": 2},
                depends_on=[primary_id] if primary_id else [],
                output_suffix=f"{len(structured_ops) + 1:02d}-search.json",
            )

        if complex_query:
            while len(decomposition) < (2 if budget != "deep" else 4):
                sub_id = f"sq{len(decomposition) + 1}"
                if len(decomposition) == 1:
                    sub_question = f"{question} 里有哪些主要选项、说法或路线需要分别验证？"
                    reason = "Complex prompts need explicit comparison targets before final synthesis."
                    caps = ["cross_validation"]
                elif len(decomposition) == 2:
                    sub_question = f"{question} 的成本、风险、限制和适用边界是什么？"
                    reason = "High-difficulty research needs downside and boundary checks."
                    caps = ["low_noise_source_discovery", "page_evidence"]
                else:
                    sub_question = f"基于已抓取证据，{question} 应该如何形成可执行结论？"
                    reason = "A deep budget should reserve one synthesis-oriented gap check subquestion."
                    caps = ["gap_check"]
                decomposition.append(_deep_subquestion(sub_id, sub_question, reason, caps))
            if not has_capability("cross_validation"):
                capability_plan.append(
                    _deep_capability("cross_validation", ["search"], "Compare independent sources before final claims; supplemental tools depend on intent.")
                )
            if budget == "deep" and _contains_any(question, DEEP_EXA_DISCOVERY_KEYWORDS):
                add_structured(
                    operation="docs_discovery",
                    renderer_kind="search",
                    purpose="low-noise evidence for tradeoffs and risks",
                    subquestion_id="sq3",
                    input_data={"query": f"{question} risks limitations comparison"},
                    args={"query": f"{question} risks limitations comparison", "extra_sources": 2},
                    depends_on=[primary_id] if primary_id else [],
                    output_suffix=next_filename("docs.json"),
                )

        if cross_validation_need == "high":
            if not has_capability("cross_validation"):
                capability_plan.append(
                    _deep_capability("cross_validation", ["search"], "Compare independent sources before final claims; supplemental tools depend on intent.")
                )
            target_subquestion = decomposition[-1]["id"] if decomposition else "sq1"
            cross_validation_tools = next((item["tools"] for item in capability_plan if item.get("capability") == "cross_validation"), [])
            if recency_requirement != "none" or locale_domain_scope == "china" or zh_current_intent:
                if "search" not in cross_validation_tools:
                    cross_validation_tools.append("search")
                if not has_purpose("current or locale-specific"):
                    add_structured(
                        operation="source_discovery",
                        renderer_kind="search",
                        purpose="current or locale-specific cross-source discovery",
                        subquestion_id=target_subquestion,
                        input_data={"query": question, "locale": "zh"},
                        args={"query": question, "extra_sources": 2},
                        depends_on=[primary_id] if primary_id else [],
                        output_suffix=next_filename("search.json"),
                    )
            elif docs_intent:
                if "search" not in cross_validation_tools:
                    cross_validation_tools.append("search")
            elif _contains_any(question, DEEP_EXA_DISCOVERY_KEYWORDS):
                if "search" not in cross_validation_tools:
                    cross_validation_tools.append("search")
                if not has_purpose("official-domain"):
                    add_structured(
                        operation="docs_discovery",
                        renderer_kind="search",
                        purpose="official-domain or low-noise cross-source discovery",
                        subquestion_id=target_subquestion,
                        input_data={"query": question},
                        args={"query": question, "extra_sources": 2},
                        depends_on=[primary_id] if primary_id else [],
                        output_suffix=next_filename("search.json"),
                    )

        capability_plan.append(_deep_capability("page_evidence", ["fetch"], "Fetch key URLs before claim-level conclusions."))
        fetch_sub = "sq1" if len(decomposition) == 1 else decomposition[-1]["id"]
        discovery_ids = [op.id for op in structured_ops if op.operation in {"source_discovery", "docs_discovery"}]
        add_structured(
            operation="content_fetch",
            renderer_kind="fetch",
            purpose="fetch key URLs before final claims",
            subquestion_id=fetch_sub,
            input_data={"candidate_refs": discovery_ids[:1]} if discovery_ids else {"resource": "<key-url>"},
            constraints={"max_items": 3} if discovery_ids else {},
            args={"url": "<key-url>"},
            depends_on=discovery_ids[:1],
            output_suffix=next_filename("fetch.md"),
        )

    for item in capability_plan:
        item["tools"] = [tool for tool in item["tools"] if tool in DEEP_ALLOWED_TOOLS]

    if budget == "quick" and len(decomposition) > 2:
        decomposition = decomposition[:2]

    # Apply quick-budget step limit before building the immutable plan.
    if budget == "quick" and len(structured_ops) > 4:
        kept_ops = list(structured_ops[:4])
        kept_entries = list(projection_entries[:4])
        kept_tools = list(pending_tools[:4])
        if "fetch" not in kept_tools:
            fetch_index = next((i for i, tool in enumerate(pending_tools) if tool == "fetch"), None)
            if fetch_index is not None:
                fetch_op = structured_ops[fetch_index]
                fetch_entry = projection_entries[fetch_index]
                # Rebuild fetch entry with the frozen quick-budget path/name.
                from .research_plan_render import LegacyPlanProjectionEntry, RENDERER_KIND_TO_TOOL
                fetch_path_name = "04-fetch.md"
                rebuilt_op = ResearchPlanOperation(
                    id=fetch_op.id,
                    operation="content_fetch",
                    input={"resource": "<key-url>"},
                    constraints={},
                    depends_on=(),
                )
                rebuilt_entry = LegacyPlanProjectionEntry(
                    operation_id=rebuilt_op.id,
                    renderer_kind="fetch",
                    tool=RENDERER_KIND_TO_TOOL["fetch"],
                    purpose=fetch_entry.purpose,
                    subquestion_id=(
                        fetch_entry.subquestion_id
                        if fetch_entry.subquestion_id in {item["id"] for item in decomposition}
                        else (decomposition[-1]["id"] if decomposition else "sq1")
                    ),
                    args={"url": "<key-url>"},
                    output_suffix=fetch_path_name,
                )
                kept_ops = list(structured_ops[:3]) + [rebuilt_op]
                kept_entries = list(projection_entries[:3]) + [rebuilt_entry]
                kept_tools = list(pending_tools[:3]) + ["fetch"]
        structured_ops = kept_ops[:4]
        projection_entries = kept_entries[:4]
        pending_tools = kept_tools[:4]

    if budget == "quick":
        valid_subquestion_ids = {item["id"] for item in decomposition}
        fallback_subquestion_id = decomposition[-1]["id"] if decomposition else "sq1"
        from .research_plan_render import LegacyPlanProjectionEntry
        fixed_entries = []
        for entry in projection_entries:
            sub_id = entry.subquestion_id if entry.subquestion_id in valid_subquestion_ids else fallback_subquestion_id
            if sub_id != entry.subquestion_id:
                fixed_entries.append(
                    LegacyPlanProjectionEntry(
                        operation_id=entry.operation_id,
                        renderer_kind=entry.renderer_kind,
                        tool=entry.tool,
                        purpose=entry.purpose,
                        subquestion_id=sub_id,
                        args=dict(entry.args),
                        output_suffix=entry.output_suffix,
                    )
                )
            else:
                fixed_entries.append(entry)
        projection_entries = fixed_entries

    research_plan = build_research_plan(structured_ops)
    projection = build_projection_context(evidence_root, projection_entries)
    steps = render_v1_steps(research_plan, projection)

    execution_plan = _capability_plan(
        "deep",
        optional_capabilities=("main_search", "docs_search", "web_search", "web_fetch"),
        budget=budget,
        allow_synthesis=False,
        response_mode="plan",
    )
    plan_dict = {
        "ok": True,
        "mode": "deep_research",
        "query_mode": "deep",
        "question": question,
        "trigger_source": "explicit_cli",
        "difficulty": difficulty,
        "intent_signals": intent_signals,
        "decomposition": decomposition,
        "capability_plan": capability_plan,
        "capability_execution_plan": execution_plan.to_dict(),
        "evidence_policy": "fetch_before_claim",
        "preflight": {
            "tool": "doctor",
            "command": "smart-search doctor --format json",
            "when": "configuration or provider availability is uncertain",
            "executed_by_deep_command": False,
        },
        "steps": steps,
        "gap_check": {
            "required": True,
            "rule": "fetch missing evidence for key claims or downgrade unsupported claims to unverified candidates",
            "unsupported_claim_action": "downgrade_to_unverified_candidate",
        },
        "final_answer_policy": "cite fetched evidence, list unverified candidates, and include key commands",
        "usage_boundary": {
            "search": "smart-search search runs live fast/broad search immediately.",
            "deep": "smart-search deep is an offline planner; it does not execute provider calls or fetch pages.",
            "execution": "An AI agent or user executes the listed steps with existing CLI commands, then performs gap_check.",
        },
        "allowed_tools": sorted(DEEP_ALLOWED_TOOLS),
        "evidence_dir": evidence_root,
        "elapsed_ms": _elapsed_ms(start),
    }
    return plan_dict, research_plan


def build_deep_research_plan(query: str, budget: str = "standard", evidence_dir: str = "") -> dict[str, Any]:
    """Offline Deep Research planner: returns the v1 projection dict only."""
    plan_dict, _ = _build_deep_research_plan_impl(query, budget, evidence_dir)
    return plan_dict


def build_research_workflow_plan(query: str, budget: str = "deep", evidence_dir: str = "") -> ResearchPlan:
    """Return the schema-neutral typed Research Plan for the strict workflow owner.

    Reuses the offline planner's operation generation so ``research run`` keeps
    the same staged plan as the offline planner; the v1 projection (steps,
    shell commands, output paths) is never part of the typed plan or the
    workflow contract.
    """
    _, research_plan = _build_deep_research_plan_impl(query, budget, evidence_dir)
    return research_plan


@observe_command
async def research(
    query: str,
    budget: str = "deep",
    evidence_dir: str = "",
    fallback: str = "auto",
    *,
    synthesize: bool | None = None,
) -> dict[str, Any]:
    start = time.time()
    question = query.strip()
    fallback_mode = (fallback or "auto").strip().lower()
    # None preserves the bare legacy research path; explicit False/True is research-run.
    synthesis_enabled = True if synthesize is None else bool(synthesize)
    response_mode = "synthesized" if synthesis_enabled else "evidence"
    if fallback_mode not in {"auto", "off"}:
        return {
            "ok": False,
            "error_type": "parameter_error",
            "error": f"Invalid fallback mode: {fallback_mode}",
            "question": question,
            "mode": "deep_research_execution",
            "route_policy_version": RESEARCH_ROUTE_POLICY_VERSION,
            "elapsed_ms": _elapsed_ms(start),
        }

    # ================================================================================
    # 步骤1：执行 research 命令能力校验
    # ================================================================================
    # 目标：research 只要求 web_fetch，docs/web discovery 作为按意图选择的可选能力。
    # 数据源：当前 capability status、minimum profile 和 research 命令矩阵。
    # 操作：
    # 1) 缺少 web_fetch 时返回 config_error，不伪装成 evidence_error。
    # 2) 保留 minimum_profile_ok，供诊断和兼容调用方读取。
    # 3) 将缺少的可选 discovery 能力记录为 degraded。
    minimum = validate_minimum_profile()
    if minimum.get("error_type") == "parameter_error":
        return {
            "ok": False,
            "error_type": "parameter_error",
            "error": minimum.get("error", "Invalid minimum profile"),
            "question": question,
            "mode": "deep_research_execution",
            "route_policy_version": RESEARCH_ROUTE_POLICY_VERSION,
            "elapsed_ms": _elapsed_ms(start),
        }
    command_capabilities = validate_command_capabilities(
        "research",
        minimum_profile=minimum.get("profile", ""),
        capability_status=minimum.get("capability_status", {}),
    )
    capability_metadata = _command_capability_metadata(command_capabilities, minimum)
    execution_plan = _capability_plan_from_result(
        "research",
        command_capabilities,
        budget=_deep_budget(budget or "deep"),
        allow_synthesis=synthesis_enabled,
        response_mode=response_mode,
    )
    if not command_capabilities.get("ok"):
        evidence_bundle = EvidenceBundle()
        evidence_bundle.add_gap({"subquestion_id": "", "reason": "minimum profile is missing required capabilities"})
        return {
            "ok": False,
            "error_type": command_capabilities.get("error_type", "config_error"),
            "error": command_capabilities.get("error", MINIMUM_PROFILE_ERROR),
            "question": question,
            "mode": "deep_research_execution",
            "final_answer": "",
            "citations": [],
            "evidence_items": [],
            "gap_check": {
                "status": "failed",
                "gaps": [{"subquestion_id": "", "reason": "minimum profile is missing required capabilities"}],
            },
            "provider_attempts": [],
            "fallback_used": False,
            "route_policy_version": RESEARCH_ROUTE_POLICY_VERSION,
            "evidence_dir": evidence_dir,
            "capability_execution_plan": execution_plan.to_dict(),
            **capability_metadata,
            **_evidence_bundle_fields(evidence_bundle),
            "degraded": bool(capability_metadata.get("degraded")) or evidence_bundle.degraded,
            "degraded_reason": _combined_degraded_reason(evidence_bundle, capability_metadata),
            "elapsed_ms": _elapsed_ms(start),
        }

    plan = build_deep_research_plan(question, budget=_deep_budget(budget or "deep"), evidence_dir=evidence_dir)
    evidence_root = plan.get("evidence_dir") or _default_evidence_dir(question)
    persist_artifacts = _research_artifacts_enabled(evidence_dir)

    def persist_artifact(name: str, data: Any) -> None:
        if persist_artifacts:
            _write_research_artifact(evidence_root, name, data)

    try:
        with observe_stage("research.route"):
            route_result = await IntentRouter(config).route(
                question,
                validation_level="balanced",
                allow_remote=True,
                plan_intent_signals=plan.get("intent_signals") or {},
            )
    except ValueError as e:
        return {
            "ok": False,
            "error_type": "parameter_error",
            "error": str(e),
            "question": question,
            "mode": "deep_research_execution",
            "route_policy_version": RESEARCH_ROUTE_POLICY_VERSION,
            "elapsed_ms": _elapsed_ms(start),
        }
    routes = _research_capability_routes(question, plan, fallback_mode, route_result=route_result)
    provider_attempts: list[dict[str, Any]] = []
    discovery_sources: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []
    stage_results: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    persist_artifact("00-plan.json", plan)

    """
    /*
     * ================================================================================
     * 步骤2：并发抓取已知 URL
     * ================================================================================
     * 目标：缩短多个用户指定 URL 的总读取时间，保持首个 URL 的展示与 artifact 索引。
     * 数据源：query 中抽取的 URL、web_fetch provider 顺序和 research fallback 模式。
     * 操作：
     * 1) 先规范化去重，首次成功或失败都拥有该 URL 键。
     * 2) 受控并发执行，并按原始 URL 索引归并结果、attempt 和 gap。
     * ================================================================================
     */
    """
    urls = _extract_urls(question)
    fetch_order = routes["capabilities"]["web_fetch"]["providers"]
    seen_fetch_keys: set[str] = set()
    logger.info("开始并发抓取已知 URL: input=%s", len(urls))
    known_entries = _prepare_research_fetch_entries(
        [
            {"index": index, "url": url, "preferred_order": fetch_order}
            for index, url in enumerate(urls, 1)
        ],
        seen_keys=seen_fetch_keys,
    )
    known_results = await _run_research_fetch_batch(
        known_entries,
        fallback=fallback_mode,
        stage="research.known_url_fetch",
    )
    for batch_result in known_results:
        # 2.1 按原始计划顺序写回 result，避免网络完成顺序改变公开输出。
        entry = batch_result["entry"]
        url = entry["url"]
        fetch_result = batch_result["fetch_result"]
        attempts = batch_result["attempts"]
        stage_result = {
            "stage": "known_url_fetch",
            "url": url,
            "ok": bool(fetch_result),
            "provider_attempts": attempts,
        }
        if batch_result["error_type"]:
            stage_result["error_type"] = batch_result["error_type"]
        provider_attempts.extend(attempts)
        stage_results.append(stage_result)
        if fetch_result:
            item = _research_evidence_item(
                url=fetch_result["url"],
                provider=fetch_result["provider"],
                title=fetch_result["url"],
                content=fetch_result["content"],
                subquestion_id="sq1",
            )
            evidence_items.append(item)
            persist_artifact(f"{entry['index']:02d}-fetch-{fetch_result['provider']}.md", fetch_result["content"])
        else:
            gaps.append({"subquestion_id": "sq1", "reason": f"failed to fetch known URL: {url}", "url": url})
    logger.info("已知 URL 并发抓取完成: scheduled=%s", len(known_entries))

    signals = routes["signals"]
    if signals["docs_api_intent"] or (signals["official_low_noise_intent"] and fallback_mode != "off"):
        docs_providers = routes["capabilities"]["docs_search"]["providers"]
        if not docs_providers:
            gap_reason = (
                "no configured docs_search provider for docs/API evidence"
                if signals["docs_api_intent"]
                else "no configured docs_search provider for official-domain discovery"
            )
            gaps.append({"subquestion_id": "sq2", "reason": gap_reason})
        else:
            sources, attempts = await _run_research_docs_discovery(question)
            provider_attempts.extend(attempts)
            if sources:
                discovery_sources.extend(sources)
                stage_results.append({"stage": "docs_discovery", "ok": True, "result_count": len(sources)})

    should_run_web_discovery = (
        signals["current_or_locale_intent"]
        or signals["cross_validation_need"] == "high"
        or (not evidence_items and not discovery_sources)
    ) and not (urls and fallback_mode == "off")
    if should_run_web_discovery:
        web_provider_order = routes["capabilities"]["web_search"]["providers"]
        if web_provider_order:
            with observe_stage("research.web_discovery"):
                web_sources, attempts = await _run_web_search_fallback(
                    question,
                    count=5,
                    providers=",".join(web_provider_order),
                    fallback=fallback_mode,
                )
            provider_attempts.extend(attempts)
            discovery_sources.extend(web_sources)
            stage_results.append({"stage": "web_discovery", "ok": bool(web_sources), "result_count": len(web_sources), "provider_attempts": attempts})
        else:
            gaps.append({"subquestion_id": "", "reason": "no configured web_search provider for discovery"})

    """
    /*
     * ================================================================================
     * 步骤5：并发抓取 discovery candidate URL
     * ================================================================================
     * 目标：并发验证独立候选页，避免重复读取已知 URL 或等价 candidate。
     * 数据源：discovery_sources、已有 evidence、research provider 路由和 fallback 模式。
     * 操作：
     * 1) 继承已知 URL 的去重键，再保留每个 candidate 的首个原始索引。
     * 2) 并发读取后按 candidate 顺序写入 evidence、citation 输入、gap 和 artifact。
     * ================================================================================
     */
    """
    candidates = _select_candidate_urls(discovery_sources, limit=6)
    for item in evidence_items:
        # 5.1 既有 evidence 同样阻止等价 candidate 再次进入 fetch。
        evidence_url = str(item.get("url") or "").strip()
        if evidence_url:
            seen_fetch_keys.add(_research_fetch_key(evidence_url))
    logger.info("开始并发抓取 candidate URL: input=%s", len(candidates))
    candidate_entries = _prepare_research_fetch_entries(
        [
            {
                "index": index,
                "url": candidate.get("url", ""),
                "candidate": candidate,
                "preferred_order": _research_fetch_order(question, candidate.get("url", "")),
            }
            for index, candidate in enumerate(candidates, 1)
        ],
        seen_keys=seen_fetch_keys,
    )
    candidate_results = await _run_research_fetch_batch(
        candidate_entries,
        fallback=fallback_mode,
        stage="research.candidate_fetch",
    )
    no_new_evidence = True
    for batch_result in candidate_results:
        # 5.2 使用原始 candidate 索引归并，防止异步完成顺序污染公开结果。
        entry = batch_result["entry"]
        candidate = entry["candidate"]
        url = entry["url"]
        fetch_result = batch_result["fetch_result"]
        attempts = batch_result["attempts"]
        stage_result = {
            "stage": "candidate_fetch",
            "url": url,
            "ok": bool(fetch_result),
            "provider_attempts": attempts,
        }
        if batch_result["error_type"]:
            stage_result["error_type"] = batch_result["error_type"]
        provider_attempts.extend(attempts)
        stage_results.append(stage_result)
        if fetch_result:
            no_new_evidence = False
            content = fetch_result.get("content", "")
            item = _research_evidence_item(
                url=fetch_result["url"],
                provider=fetch_result["provider"],
                title=candidate.get("title") or fetch_result["url"],
                content=content,
                subquestion_id=candidate.get("subquestion_id", ""),
            )
            evidence_items.append(item)
            persist_artifact(f"fetch-{entry['index']:02d}-{fetch_result['provider']}.md", content)
        elif batch_result["error_type"]:
            gaps.append({"subquestion_id": "", "reason": f"candidate fetch {batch_result['error_type']}: {url}", "url": url})
        elif fallback_mode == "off":
            gaps.append({"subquestion_id": "", "reason": f"fetch failed with fallback off: {url}", "url": url})
    logger.info("candidate URL 并发抓取完成: scheduled=%s", len(candidate_entries))

    if not evidence_items:
        gaps.append({"subquestion_id": "", "reason": "no fetched/read evidence items were produced"})
    elif no_new_evidence and not urls and candidates:
        gaps.append({"subquestion_id": "", "reason": "discovery produced candidates but no new fetch evidence converged"})

    evidence_bundle = EvidenceBundle()
    evidence_bundle.add_discovery_candidates(discovery_sources)
    evidence_bundle.add_fetched_evidence(evidence_items)
    evidence_bundle.add_provider_attempts(provider_attempts)
    for gap in gaps:
        evidence_bundle.add_gap(gap)
    evidence_items = evidence_bundle.evidence_items
    discovery_sources = evidence_bundle.discovery_candidates
    synthesis_error = ""
    if not synthesis_enabled:
        # Intentional evidence-only mode skips synthesis budget and synthesizer.
        final_answer = ""
    elif allow_synthesis():
        with observe_stage("research.synthesis"):
            try:
                final_answer = _evidence_only_synthesis(question, evidence_bundle)
                if evidence_items and not final_answer.strip():
                    raise RuntimeError("evidence-only synthesis returned empty content")
            except Exception as exc:
                synthesis_error = sanitize_text(str(exc)) or "evidence-only synthesis failed"
                evidence_bundle.add_gap({"subquestion_id": "", "reason": f"synthesis failed: {synthesis_error}"})
                final_answer = ""
    else:
        evidence_bundle.add_gap({"subquestion_id": "", "reason": "request budget exhausted before synthesis"})
        final_answer = ""
    bundle_fields = _evidence_bundle_fields(evidence_bundle)
    gaps = evidence_bundle.gaps
    citations = evidence_bundle.citations
    covered = bool(evidence_items)
    gap_status = "closed" if covered and not gaps else ("degraded" if evidence_items else "failed")
    result = {
        "ok": bool(evidence_items),
        "error_type": "" if evidence_items else "evidence_error",
        "error": "" if evidence_items else "research could not obtain fetched evidence",
        "mode": "deep_research_execution",
        "query_mode": "research",
        "question": question,
        "budget": _deep_budget(budget or "deep"),
        "research_plan": plan,
        "routing_decision": routes,
        "stage_results": stage_results,
        "discovery_sources": discovery_sources,
        "final_answer": final_answer,
        "content": final_answer,
        "citations": citations,
        "evidence_items": evidence_items,
        "fetched_evidence": evidence_items,
        "evidence_bundle": bundle_fields["evidence_bundle"],
        "discovery_candidates": discovery_sources,
        "gaps": gaps,
        "gap_check": {
            "status": gap_status,
            "gaps": gaps,
            "stop_reason": "evidence_converged" if gap_status == "closed" else ("degraded_with_gaps" if evidence_items else "provider_exhausted"),
        },
        "provider_attempts": provider_attempts,
        "providers_used": _provider_names_from_attempts(provider_attempts),
        "fallback_used": _fallback_used(provider_attempts),
        "route_policy_version": RESEARCH_ROUTE_POLICY_VERSION,
        "evidence_dir": evidence_root,
        "artifacts_persisted": persist_artifacts,
        "synthesis_error": synthesis_error,
        "response_mode": response_mode,
        "synthesis_enabled": synthesis_enabled,
        "capability_execution_plan": execution_plan.to_dict(),
        **capability_metadata,
        "degraded_reason": _combined_degraded_reason(evidence_bundle, capability_metadata),
        "degraded": bool(evidence_bundle.degraded) or bool(capability_metadata.get("degraded")),
        "elapsed_ms": _elapsed_ms(start),
    }
    attach_metrics(result)
    persist_artifact("summary.json", result)
    return result

__all__ = [name for name in globals() if not name.startswith("__")]
