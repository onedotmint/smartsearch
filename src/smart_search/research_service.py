"""Offline Deep Research planning and live evidence workflow."""

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
from .capability_executor import CapabilityOperation, execute_capability
from .config import config
from .evidence import EvidenceBundle
from .intent_router import IntentRouteResult, IntentRouter, build_rules_route
from .logger import logger
from .provider_commands import (
    anysearch_search,
    context7_docs,
    context7_library,
    exa_search,
)
from .runtime_cache import allow_synthesis, attach_metrics, observe_command, observe_stage
from .search_service import _run_web_fetch_fallback, _run_web_search_fallback
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
    _normalize_source_results,
    _provider_names_from_attempts,
)

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
        "vertical_intent": bool(rules_route.intent_signals.get("vertical_intent")),
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
        signals["vertical_intent"] = bool(route_result.intent_signals.get("vertical_intent") or "vertical_search" in route_result.required_capabilities)
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

    vertical = _configured_for_capability("vertical_search", capability_status)
    routes["capabilities"]["vertical_search"] = {
        "providers": _apply_research_overrides("vertical_search", vertical) if signals["vertical_intent"] else [],
        "reason": "vertical intent matched" if signals["vertical_intent"] else "vertical intent absent",
        "experimental": True,
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


async def _run_research_context7_docs(
    question: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    /*
     * ================================================================================
     * 步骤1：执行 Context7 文档 evidence 阶段
     * ================================================================================
     * 目标：保留 library resolve -> docs read 的两段边界，只把 provider 生命周期交给 executor。
     * 数据源：Context7 library/docs adapter、当前 query 和 RequestContext。
     * 操作：
     * 1) 用 source cache resolve library id。
     * 2) 用 content cache 读取选定 library 的文档正文。
     * 3) 只有正文非空时生成 fetched evidence，候选 library 不直接作为证据。
     * ================================================================================
     */
    """
    logger.info("开始执行 research Context7 阶段: question=%s", question)

    async def resolve_library(provider: str, outcome: dict[str, Any]) -> list[dict]:
        # 1.1 解析 library id，不把候选直接当作 fetched evidence。
        data = await context7_library(question, question)
        outcome.update(data if isinstance(data, dict) else {})
        return [
            {
                "url": f"context7:{item.get('id')}",
                "title": item.get("title") or item.get("id") or "Context7",
                "description": item.get("description") or "",
                "provider": provider,
            }
            for item in (data.get("results", []) if isinstance(data, dict) else [])
            if isinstance(data, dict) and data.get("ok") and item.get("id")
        ]

    library_execution = await execute_capability(
        CapabilityOperation(
            capability="docs_search",
            input_value=question,
            cache_options={"name": question, "query": question},
            run=resolve_library,
            empty_value=lambda _provider: [],
            is_success=lambda value: isinstance(value, list) and bool(value),
            result_count=lambda value: len(value) if isinstance(value, list) else 0,
        ),
        providers=("context7",),
        fallback="off",
    )
    attempts = list(library_execution.attempts)
    libraries = library_execution.value if isinstance(library_execution.value, list) else []
    if not libraries:
        logger.info("research Context7 library 阶段结束: 无 library")
        return [], attempts

    library_id = str(libraries[0].get("url", "")).removeprefix("context7:")
    if not library_id:
        logger.info("research Context7 阶段结束: library id 为空")
        return [], attempts

    async def read_docs(provider: str, outcome: dict[str, Any]) -> dict[str, Any]:
        # 1.2 读取正文并保留 provider 错误元数据。
        data = await context7_docs(library_id, question)
        outcome.update(data if isinstance(data, dict) else {})
        return {
            "content": sanitize_text(data.get("content") or "") if isinstance(data, dict) and data.get("ok") else "",
            "library_id": library_id,
        }

    docs_execution = await execute_capability(
        CapabilityOperation(
            capability="docs_search",
            input_value=f"https://context7.local/{library_id}",
            cache_kind="content",
            cache_options={"library_id": library_id, "query": question},
            run=read_docs,
            empty_value=lambda _provider: {"content": "", "library_id": library_id},
            is_success=lambda value: isinstance(value, dict) and bool(str(value.get("content") or "").strip()),
            result_count=lambda _value: 1,
        ),
        providers=("context7",),
        fallback="off",
    )
    attempts.extend(docs_execution.attempts)
    docs_payload = docs_execution.value if isinstance(docs_execution.value, dict) else {}
    content = str(docs_payload.get("content") or "")
    if not content.strip():
        logger.info("research Context7 阶段结束: library=%s 无正文", library_id)
        return [], attempts

    item = _research_evidence_item(
        url=f"context7:{library_id}",
        provider="context7",
        title=library_id,
        content=content,
        source_type="docs",
        subquestion_id="sq2",
    )
    logger.info("research Context7 阶段完成: library=%s", library_id)
    return [item], attempts


async def _run_research_exa_docs(
    question: str,
    fallback: str = "auto",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the Exa docs discovery operation through the shared executor."""

    async def run_provider(provider: str, outcome: dict[str, Any]) -> list[dict]:
        # 2.1 Exa 只产出 discovery candidates，后续仍需 fetch。
        data = await exa_search(question, num_results=5, include_highlights=True)
        outcome.update(data if isinstance(data, dict) else {})
        return _normalize_source_results(data.get("results"), provider) if isinstance(data, dict) and data.get("ok") else []

    execution = await execute_capability(
        CapabilityOperation(
            capability="docs_search",
            input_value=question,
            cache_options={"include_highlights": True, "num_results": 5},
            run=run_provider,
            empty_value=lambda _provider: [],
            is_success=lambda value: isinstance(value, list) and bool(value),
            result_count=lambda value: len(value) if isinstance(value, list) else 0,
        ),
        providers=("exa",),
        fallback=fallback,
    )
    return execution.value if isinstance(execution.value, list) else [], execution.attempts


async def _run_research_vertical_search(
    question: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the experimental vertical search operation through the executor."""

    async def run_provider(provider: str, outcome: dict[str, Any]) -> list[dict]:
        # 3.1 保留 AnySearch 的 vertical capability，不加入通用 fallback。
        data = await anysearch_search(question, max_results=5)
        outcome.update(data if isinstance(data, dict) else {})
        return _normalize_source_results(data.get("results"), provider) if isinstance(data, dict) and data.get("ok") else []

    execution = await execute_capability(
        CapabilityOperation(
            capability="vertical_search",
            input_value=question,
            cache_options={"max_results": 5},
            run=run_provider,
            empty_value=lambda _provider: [],
            is_success=lambda value: isinstance(value, list) and bool(value),
            result_count=lambda value: len(value) if isinstance(value, list) else 0,
        ),
        providers=("anysearch",),
        fallback="off",
    )
    return execution.value if isinstance(execution.value, list) else [], execution.attempts

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

def _select_candidate_urls(sources: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        url = (source.get("url") or "").strip()
        if not url or url.startswith("context7:") or url in seen:
            continue
        seen.add(url)
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

def _quote_arg(value: str) -> str:
    escaped = value.replace("`", "``").replace("$", "`$").replace('"', '`"')
    return f'"{escaped}"'

def _path_join(base: str, filename: str) -> str:
    return str(Path(base) / filename)

def _deep_step(
    step_id: str,
    subquestion_id: str,
    tool: str,
    purpose: str,
    command: str,
    output_path: str,
) -> dict[str, str]:
    return {
        "id": step_id,
        "subquestion_id": subquestion_id,
        "tool": tool,
        "purpose": purpose,
        "command": command,
        "output_path": output_path,
    }

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

def build_deep_research_plan(query: str, budget: str = "standard", evidence_dir: str = "") -> dict[str, Any]:
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
    steps: list[dict[str, str]] = []

    def add_step(sub_id: str, tool: str, purpose: str, command: str, filename: str) -> None:
        step_id = f"s{len(steps) + 1}"
        steps.append(_deep_step(step_id, sub_id, tool, purpose, command, _path_join(evidence_root, filename)))

    def next_filename(suffix: str) -> str:
        return f"{len(steps) + 1:02d}-{suffix}"

    def command_search(q: str, extra_sources: int = 2) -> str:
        return f"smart-search search {_quote_arg(q)} --validation balanced --extra-sources {extra_sources} --format json --output {_quote_arg(_path_join(evidence_root, next_filename('search.json')))}"

    def command_exa(q: str) -> str:
        return f"smart-search exa-search {_quote_arg(q)} --num-results 5 --format json --output {_quote_arg(_path_join(evidence_root, next_filename('exa.json')))}"

    def command_zhipu(q: str) -> str:
        return f"smart-search zhipu-search {_quote_arg(q)} --count 5 --format json --output {_quote_arg(_path_join(evidence_root, next_filename('zhipu.json')))}"

    def command_fetch(target: str = "<key-url>") -> str:
        return f"smart-search fetch {_quote_arg(target)} --format markdown --output {_quote_arg(_path_join(evidence_root, next_filename('fetch.md')))}"

    def has_capability(name: str) -> bool:
        return any(item.get("capability") == name for item in capability_plan)

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
                _deep_capability("adjacent_source_discovery", ["exa-similar"], "Find pages adjacent to the known source."),
                _deep_capability("broad_discovery", ["search"], "Broaden the context if the fetched page leaves gaps."),
            ]
        )
        add_step("sq1", "fetch", "fetch user supplied URL first", f"smart-search fetch {_quote_arg(url)} --format markdown --output {_quote_arg(_path_join(evidence_root, '01-fetch.md'))}", "01-fetch.md")
        add_step("sq2", "exa-similar", "find adjacent sources from the provided URL", f"smart-search exa-similar {_quote_arg(url)} --num-results 5 --format json --output {_quote_arg(_path_join(evidence_root, '02-similar.json'))}", "02-similar.json")
        add_step("sq2", "search", "broad discovery for missing context", command_search(question, 1), "03-search.json")
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
        add_step("sq1", "search", "broad discovery and routing metadata", command_search(question, 1 if budget == "quick" else 3), "01-search.json")

        if docs_intent:
            decomposition.append(
                _deep_subquestion(
                    "sq2",
                    f"{question} 的官方文档、API 或 SDK 证据在哪里？",
                    "docs/API intent should resolve the library docs first, with Exa only as official-domain discovery.",
                    ["docs_source_discovery", "page_evidence"],
                )
            )
            capability_plan.append(
                _deep_capability(
                    "docs_source_discovery",
                    ["context7-library", "context7-docs"],
                    "Resolve official library/API documentation first; use Exa only for official-domain or supplemental discovery.",
                )
            )
            library_hint = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", question)[:2]) or "<library-name>"
            add_step(
                "sq2",
                "context7-library",
                "resolve library id for docs/API intent",
                f"smart-search context7-library {_quote_arg(library_hint)} {_quote_arg(question)} --format json --output {_quote_arg(_path_join(evidence_root, next_filename('context7-library.json')))}",
                next_filename("context7-library.json"),
            )
            add_step(
                "sq2",
                "context7-docs",
                "retrieve docs after selecting the best library_id",
                f"smart-search context7-docs {_quote_arg('<library_id>')} {_quote_arg(question)} --format json --output {_quote_arg(_path_join(evidence_root, next_filename('context7-docs.json')))}",
                next_filename("context7-docs.json"),
            )
            if _contains_any(question, DEEP_EXA_DISCOVERY_KEYWORDS):
                capability_plan.append(
                    _deep_capability(
                        "official_domain_discovery",
                        ["exa-search"],
                        "Use Exa for official-domain or low-noise supplemental docs discovery.",
                    )
                )
                add_step("sq2", "exa-search", "official-domain docs source discovery", command_exa(f"{question} official docs"), next_filename("exa.json"))

        if recency_requirement != "none" or locale_domain_scope == "china":
            sub_id = f"sq{len(decomposition) + 1}"
            decomposition.append(
                _deep_subquestion(
                    sub_id,
                    f"{question} 的最新或中文/国内来源如何交叉验证？",
                    "Current or China-scoped prompts benefit from Zhipu web-search reinforcement.",
                    ["current_or_locale_source_discovery"],
                )
            )
            capability_plan.append(
                _deep_capability("current_or_locale_source_discovery", ["zhipu-search"], "Reinforce Chinese, domestic, or current web evidence.")
            )
            add_step(sub_id, "zhipu-search", "current or locale-specific source discovery", command_zhipu(question), f"{len(steps) + 1:02d}-zhipu.json")

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
                add_step("sq3", "exa-search", "low-noise evidence for tradeoffs and risks", command_exa(f"{question} risks limitations comparison"), next_filename("exa.json"))

        if cross_validation_need == "high":
            if not has_capability("cross_validation"):
                capability_plan.append(
                    _deep_capability("cross_validation", ["search"], "Compare independent sources before final claims; supplemental tools depend on intent.")
                )
            target_subquestion = decomposition[-1]["id"] if decomposition else "sq1"
            cross_validation_tools = next((item["tools"] for item in capability_plan if item.get("capability") == "cross_validation"), [])
            if recency_requirement != "none" or locale_domain_scope == "china" or zh_current_intent:
                if "zhipu-search" not in cross_validation_tools:
                    cross_validation_tools.append("zhipu-search")
                if not any(step["tool"] == "zhipu-search" for step in steps):
                    add_step(target_subquestion, "zhipu-search", "current or locale-specific cross-source discovery", command_zhipu(question), next_filename("zhipu.json"))
            elif docs_intent:
                if "context7-library" not in cross_validation_tools:
                    cross_validation_tools.extend(["context7-library", "context7-docs"])
            elif _contains_any(question, DEEP_EXA_DISCOVERY_KEYWORDS):
                if "exa-search" not in cross_validation_tools:
                    cross_validation_tools.append("exa-search")
                if not any(step["tool"] == "exa-search" for step in steps):
                    add_step(target_subquestion, "exa-search", "official-domain or low-noise cross-source discovery", command_exa(question), next_filename("exa.json"))

        capability_plan.append(_deep_capability("page_evidence", ["fetch"], "Fetch key URLs before claim-level conclusions."))
        add_step("sq1" if len(decomposition) == 1 else decomposition[-1]["id"], "fetch", "fetch key URLs before final claims", command_fetch(), next_filename("fetch.md"))

    for item in capability_plan:
        item["tools"] = [tool for tool in item["tools"] if tool in DEEP_ALLOWED_TOOLS]
    steps = [step for step in steps if step["tool"] in DEEP_ALLOWED_TOOLS]
    if budget == "quick" and len(decomposition) > 2:
        decomposition = decomposition[:2]
    if budget == "quick" and len(steps) > 4:
        limited_steps = steps[:4]
        if not any(step["tool"] == "fetch" for step in limited_steps):
            first_fetch = next((step for step in steps if step["tool"] == "fetch"), None)
            if first_fetch:
                first_fetch = dict(first_fetch)
                fetch_path = _path_join(evidence_root, "04-fetch.md")
                first_fetch["command"] = f"smart-search fetch {_quote_arg('<key-url>')} --format markdown --output {_quote_arg(fetch_path)}"
                first_fetch["output_path"] = fetch_path
                limited_steps = steps[:3] + [first_fetch]
        steps = limited_steps[:4]
    if budget == "quick":
        valid_subquestion_ids = {item["id"] for item in decomposition}
        fallback_subquestion_id = decomposition[-1]["id"] if decomposition else "sq1"
        for index, step in enumerate(steps, start=1):
            step["id"] = f"s{index}"
            if step.get("subquestion_id") not in valid_subquestion_ids:
                step["subquestion_id"] = fallback_subquestion_id

    execution_plan = _capability_plan(
        "deep",
        optional_capabilities=("main_search", "docs_search", "web_search", "web_fetch"),
        budget=budget,
        allow_synthesis=False,
        response_mode="plan",
    )
    return {
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

@observe_command
async def research(
    query: str,
    budget: str = "deep",
    evidence_dir: str = "",
    fallback: str = "auto",
) -> dict[str, Any]:
    start = time.time()
    question = query.strip()
    fallback_mode = (fallback or "auto").strip().lower()
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
        allow_synthesis=True,
        response_mode="synthesized",
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

    urls = _extract_urls(question)
    fetch_order = routes["capabilities"]["web_fetch"]["providers"]
    if urls:
        for index, url in enumerate(urls, 1):
            with observe_stage("research.known_url_fetch"):
                fetch_result, attempts = await _run_web_fetch_fallback(url, fallback=fallback_mode, preferred_order=fetch_order)
            provider_attempts.extend(attempts)
            stage_results.append({"stage": "known_url_fetch", "url": url, "ok": bool(fetch_result), "provider_attempts": attempts})
            if fetch_result:
                item = _research_evidence_item(
                    url=fetch_result["url"],
                    provider=fetch_result["provider"],
                    title=fetch_result["url"],
                    content=fetch_result["content"],
                    subquestion_id="sq1",
                )
                evidence_items.append(item)
                persist_artifact(f"{index:02d}-fetch-{fetch_result['provider']}.md", fetch_result["content"])
            else:
                gaps.append({"subquestion_id": "sq1", "reason": f"failed to fetch known URL: {url}", "url": url})

    signals = routes["signals"]
    if signals["docs_api_intent"]:
        docs_providers = routes["capabilities"]["docs_search"]["providers"]
        selected_docs_providers = docs_providers[:1] if fallback_mode == "off" else docs_providers
        if not selected_docs_providers:
            gaps.append({"subquestion_id": "sq2", "reason": "no configured docs_search provider for docs/API evidence"})
        for provider in selected_docs_providers:
            if provider == "context7":
                context7_items, attempts = await _run_research_context7_docs(question)
                provider_attempts.extend(attempts)
                if context7_items:
                    evidence_items.extend(context7_items)
                    stage_results.append({"stage": "docs_discovery", "provider": "context7", "ok": True, "result_count": len(context7_items)})
                    persist_artifact("docs-context7.md", context7_items[0].get("content") or "")
                    break
            elif provider == "exa":
                sources, attempts = await _run_research_exa_docs(question, fallback="off")
                provider_attempts.extend(attempts)
                if sources:
                    discovery_sources.extend(sources)
                    stage_results.append({"stage": "docs_discovery", "provider": "exa", "ok": True, "result_count": len(sources)})
                    break

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

    exa_in_selected_docs_route = "exa" in routes["capabilities"]["docs_search"]["providers"]
    if (
        fallback_mode != "off"
        and signals["official_low_noise_intent"]
        and exa_in_selected_docs_route
        and not any(source.get("provider") == "exa" for source in discovery_sources)
    ):
        sources, attempts = await _run_research_exa_docs(question, fallback=fallback_mode)
        provider_attempts.extend(attempts)
        if sources:
            discovery_sources.extend(sources)

    if signals["vertical_intent"] and routes["capabilities"]["vertical_search"]["providers"]:
        sources, attempts = await _run_research_vertical_search(question)
        provider_attempts.extend(attempts)
        if sources:
            discovery_sources.extend(sources)
            stage_results.append({"stage": "vertical_discovery", "provider": "anysearch", "ok": True, "result_count": len(sources)})

    candidates = _select_candidate_urls(discovery_sources, limit=6)
    fetched_urls = {item.get("url") for item in evidence_items}
    no_new_evidence = True
    for index, candidate in enumerate(candidates, 1):
        url = candidate.get("url", "")
        if not url or url in fetched_urls:
            continue
        order = _research_fetch_order(question, url)
        with observe_stage("research.candidate_fetch"):
            fetch_result, attempts = await _run_web_fetch_fallback(url, fallback=fallback_mode, preferred_order=order)
        provider_attempts.extend(attempts)
        stage_results.append({"stage": "candidate_fetch", "url": url, "ok": bool(fetch_result), "provider_attempts": attempts})
        if fetch_result:
            no_new_evidence = False
            fetched_urls.add(url)
            content = fetch_result.get("content", "")
            item = _research_evidence_item(
                url=fetch_result["url"],
                provider=fetch_result["provider"],
                title=candidate.get("title") or fetch_result["url"],
                content=content,
                subquestion_id=candidate.get("subquestion_id", ""),
            )
            evidence_items.append(item)
            persist_artifact(f"fetch-{index:02d}-{fetch_result['provider']}.md", content)
        elif fallback_mode == "off":
            gaps.append({"subquestion_id": "", "reason": f"fetch failed with fallback off: {url}", "url": url})

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
    if allow_synthesis():
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
