"""Offline Deep Research planning for the strict Research Workflow owner.

Builds the schema-neutral typed Research Plan only. The v1 deep-plan
projection (steps with shell commands and output paths) and the v1 live
research workflow (``research()``) are removed; ``research run`` executes the
typed plan through ``research_workflow``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .research_plan import ResearchPlan, ResearchPlanOperation, build_research_plan
from .service_support import (
    DEEP_ALLOWED_TOOLS,
    DEEP_CHINA_KEYWORDS,
    DEEP_CURRENT_KEYWORDS,
    DEEP_EXA_DISCOVERY_KEYWORDS,
    DEEP_HIGH_COMPLEXITY_KEYWORDS,
    DEEP_RECENT_KEYWORDS,
    _contains_any,
    _extract_urls,
    _is_docs_intent,
    _is_zh_current_intent,
)

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

def _build_typed_research_plan(query: str, budget: str = "standard") -> ResearchPlan:
    """Offline typed Research Plan core used by the strict workflow owner.

    Heuristics and operation generation are shared with the historical v1
    planner; the returned plan carries no shell commands, output paths, or v1
    answer projections.
    """
    question = query.strip()
    budget = _deep_budget(budget)
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
    purposes: list[str] = []
    pending_tools: list[str] = []

    def next_op_id(prefix: str) -> str:
        return f"{prefix}-{len(structured_ops) + 1}"

    def add_structured(
        *,
        operation: str,
        renderer_kind: str,
        purpose: str,
        input_data: dict[str, Any],
        constraints: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
    ) -> None:
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
        structured_ops.append(op)
        purposes.append(purpose)
        pending_tools.append(tool_name)

    def has_capability(name: str) -> bool:
        return any(item.get("capability") == name for item in capability_plan)

    def has_tool(tool: str) -> bool:
        return tool in pending_tools

    def has_purpose(prefix: str) -> bool:
        return any(purpose.startswith(prefix) for purpose in purposes)

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
            input_data={"resource": url},
        )
        fetch_id = structured_ops[-1].id if structured_ops else ""
        add_structured(
            operation="source_discovery",
            renderer_kind="search",
            purpose="find adjacent sources from the provided URL",
            input_data={"resource": url, "mode": "similar"},
            depends_on=[fetch_id] if fetch_id else [],
        )
        add_structured(
            operation="source_discovery",
            renderer_kind="search",
            purpose="broad discovery for missing context",
            input_data={"query": question},
            constraints={"max_results": 1},
            depends_on=[fetch_id] if fetch_id else [],
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
            input_data={"query": question},
            constraints={"max_results": extra},
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
                input_data={"query": question, "library_hint": library_hint, "mode": "library"},
                depends_on=[primary_id] if primary_id else [],
            )
            lib_id = structured_ops[-1].id if structured_ops else primary_id
            add_structured(
                operation="docs_discovery",
                renderer_kind="search",
                purpose="retrieve docs after selecting the best library_id",
                input_data={"query": question, "mode": "docs"},
                depends_on=[lib_id] if lib_id else [],
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
                    input_data={"query": f"{question} official docs"},
                    depends_on=[lib_id] if lib_id else [],
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
                input_data={"query": question, "locale": "zh"},
                depends_on=[primary_id] if primary_id else [],
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
                    input_data={"query": f"{question} risks limitations comparison"},
                    depends_on=[primary_id] if primary_id else [],
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
                        input_data={"query": question, "locale": "zh"},
                        depends_on=[primary_id] if primary_id else [],
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
                        input_data={"query": question},
                        depends_on=[primary_id] if primary_id else [],
                    )

        capability_plan.append(_deep_capability("page_evidence", ["fetch"], "Fetch key URLs before claim-level conclusions."))
        discovery_ids = [op.id for op in structured_ops if op.operation in {"source_discovery", "docs_discovery"}]
        add_structured(
            operation="content_fetch",
            renderer_kind="fetch",
            purpose="fetch key URLs before final claims",
            input_data={"candidate_refs": discovery_ids[:1]} if discovery_ids else {"resource": "<key-url>"},
            constraints={"max_items": 3} if discovery_ids else {},
            depends_on=discovery_ids[:1],
        )

    for item in capability_plan:
        item["tools"] = [tool for tool in item["tools"] if tool in DEEP_ALLOWED_TOOLS]

    if budget == "quick" and len(decomposition) > 2:
        decomposition = decomposition[:2]

    # Apply quick-budget step limit before building the immutable plan.
    if budget == "quick" and len(structured_ops) > 4:
        kept_ops = list(structured_ops[:4])
        kept_tools = list(pending_tools[:4])
        if "fetch" not in kept_tools:
            fetch_index = next((i for i, tool in enumerate(pending_tools) if tool == "fetch"), None)
            if fetch_index is not None:
                fetch_op = structured_ops[fetch_index]
                # Keep one stable fetch operation in quick budgets so the typed
                # plan never drops content_fetch.
                rebuilt_op = ResearchPlanOperation(
                    id=fetch_op.id,
                    operation="content_fetch",
                    input={"resource": "<key-url>"},
                    constraints={},
                    depends_on=(),
                )
                kept_ops = list(structured_ops[:3]) + [rebuilt_op]
                kept_tools = list(pending_tools[:3]) + ["fetch"]
        structured_ops = kept_ops[:4]
        pending_tools = kept_tools[:4]

    return build_research_plan(structured_ops)



def build_research_workflow_plan(query: str, budget: str = "deep", evidence_dir: str = "") -> ResearchPlan:
    """Return the schema-neutral typed Research Plan for the strict workflow owner.

    ``evidence_dir`` is accepted for call compatibility and never enters the
    typed plan; the plan carries no shell commands, output paths, or v1 answer
    projections.
    """
    return _build_typed_research_plan(query.strip(), budget=budget or "deep")


__all__ = ["build_research_workflow_plan"]
