import pytest

from smart_search import capability_service
from smart_search.intent_router import IntentRouter


@pytest.mark.asyncio
async def test_route_hybrid_degrades_to_rules_when_remote_router_unconfigured(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")

    result = await capability_service.route("React useEffect API docs")

    assert result["ok"] is True
    assert result["intent_router_mode"] == "hybrid"
    assert result["required_capabilities"] == ["docs_search"]
    assert result["docs_intent"] is True
    assert result["degraded"] is True
    assert "embeddings not configured" in result["degraded_reason"]
    assert "classifier not configured" in result["degraded_reason"]
    assert result["executed_search"] is False


@pytest.mark.asyncio
async def test_route_outputs_multiple_capabilities_for_url_verification(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "rules")

    result = await capability_service.route("请核验这个链接里的说法 https://example.com/source", validation="strict")

    assert result["ok"] is True
    assert set(result["required_capabilities"]) == {"web_search", "web_fetch"}
    assert result["fetch_intent"] is True
    assert result["web_current_intent"] is False
    assert result["intent_signals"]["strict_validation"] is True


@pytest.mark.asyncio
async def test_route_keeps_zh_current_field_narrow_for_english_current_queries(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "rules")

    result = await capability_service.route("today AI news")

    assert result["zh_current_intent"] is False
    assert result["web_current_intent"] is True
    assert result["required_capabilities"] == ["web_search"]


@pytest.mark.asyncio
async def test_route_docs_query_selects_docs_search_capability(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "rules")

    result = await capability_service.route("React useEffect API docs")

    assert result["ok"] is True
    routing = result["routing_decision"] if "routing_decision" in result else result
    assert routing["docs_intent"] is True
    assert routing["zh_current_intent"] is False
    assert routing["web_current_intent"] is False
    assert routing["required_capabilities"] == ["docs_search"]


@pytest.mark.asyncio
async def test_classifier_ignores_unknown_capabilities_and_provider_names(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_URL", "https://classifier.example.com/chat/completions")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_KEY", "classifier-secret")
    monkeypatch.setenv("INTENT_CLASSIFIER_MODEL", "intent-mini")

    async def fake_classifier_route(self, query, rules, semantic):
        return {
            "required_capabilities": ["web_search", "openai-compatible", "docs_search"],
            "intent_signals": {"provider": "zhipu", "classifier_signal": True},
            "confidence": 0.91,
            "reasons": ["needs current sources"],
            "provider": "zhipu",
        }

    monkeypatch.setattr(IntentRouter, "_classifier_route", fake_classifier_route)

    result = await capability_service.route("今天国内 AI 新闻")

    assert set(result["required_capabilities"]) == {"web_search", "docs_search"}
    assert "openai-compatible" not in result["required_capabilities"]
    assert result["intent_signals"]["classifier_signal"] is True
    assert "provider" not in result["intent_signals"]
    assert any("ignored unknown capability" in reason for reason in result["reasons"])
    assert any("provider choices were ignored" in reason for reason in result["reasons"])
    assert "classifier" in result["router_engines_used"]


@pytest.mark.asyncio
async def test_classifier_cannot_add_web_search_without_current_or_validation_signal(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_URL", "https://classifier.example.com/chat/completions")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_KEY", "classifier-secret")
    monkeypatch.setenv("INTENT_CLASSIFIER_MODEL", "intent-mini")

    async def fake_classifier_route(self, query, rules, semantic):
        return {
            "required_capabilities": ["docs_search", "web_search"],
            "confidence": 0.9,
            "reasons": ["tutorial-style explanation could use broad web search"],
        }

    monkeypatch.setattr(IntentRouter, "_classifier_route", fake_classifier_route)

    result = await capability_service.route("中文解释 Python 函数")

    assert result["required_capabilities"] == ["docs_search"]
    assert result["web_current_intent"] is False
    assert any("ignored unsupported capability" in reason for reason in result["reasons"])


@pytest.mark.asyncio
async def test_semantic_router_can_add_capability_without_classifier(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")
    monkeypatch.setenv("INTENT_EMBEDDING_API_URL", "https://embed.example.com/embeddings")
    monkeypatch.setenv("INTENT_EMBEDDING_API_KEY", "embed-secret")
    monkeypatch.setenv("INTENT_EMBEDDING_MODEL", "embed-model")

    async def fake_semantic_route(self, query):
        return {"scores": {"docs_search": 0.76, "web_search": 0.1}}

    monkeypatch.setattr(IntentRouter, "_semantic_route", fake_semantic_route)

    result = await capability_service.route("这个 SDK 怎么接入")

    assert "docs_search" in result["required_capabilities"]
    assert "embeddings" in result["router_engines_used"]
    assert result["intent_signals"]["semantic_docs_search_score"] == 0.76
    assert "classifier not configured" in result["degraded_reason"]


@pytest.mark.asyncio
async def test_semantic_router_marks_ambiguous_margin_without_adding_capability(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")
    monkeypatch.setenv("INTENT_EMBEDDING_API_URL", "https://embed.example.com/embeddings")
    monkeypatch.setenv("INTENT_EMBEDDING_API_KEY", "embed-secret")
    monkeypatch.setenv("INTENT_EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("INTENT_EMBEDDING_THRESHOLD", "0.74")
    monkeypatch.setenv("INTENT_EMBEDDING_MARGIN", "0.05")

    async def fake_semantic_route(self, query):
        return {"scores": {"web_search": 0.81, "docs_search": 0.79}}

    monkeypatch.setattr(IntentRouter, "_semantic_route", fake_semantic_route)

    result = await capability_service.route("普通问题")

    assert result["required_capabilities"] == []
    assert result["intent_signals"]["semantic_top_capability"] == "web_search"
    assert result["intent_signals"]["semantic_passed_threshold"] is True
    assert result["intent_signals"]["semantic_passed_margin"] is False
    assert any("embeddings ambiguous" in reason for reason in result["reasons"])


@pytest.mark.asyncio
async def test_classifier_can_add_capability_when_semantic_is_ambiguous(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")
    monkeypatch.setenv("INTENT_EMBEDDING_API_URL", "https://embed.example.com/embeddings")
    monkeypatch.setenv("INTENT_EMBEDDING_API_KEY", "embed-secret")
    monkeypatch.setenv("INTENT_EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_URL", "https://classifier.example.com/chat/completions")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_KEY", "classifier-secret")
    monkeypatch.setenv("INTENT_CLASSIFIER_MODEL", "intent-mini")
    monkeypatch.setenv("INTENT_EMBEDDING_THRESHOLD", "0.74")
    monkeypatch.setenv("INTENT_EMBEDDING_MARGIN", "0.05")

    async def fake_semantic_route(self, query):
        return {"scores": {"docs_search": 0.82, "web_search": 0.8}}

    async def fake_classifier_route(self, query, rules, semantic):
        return {"required_capabilities": ["docs_search"], "confidence": 0.9, "reasons": ["docs intent"]}

    monkeypatch.setattr(IntentRouter, "_semantic_route", fake_semantic_route)
    monkeypatch.setattr(IntentRouter, "_classifier_route", fake_classifier_route)

    result = await capability_service.route("普通问题")

    assert result["required_capabilities"] == ["docs_search"]
    assert any("embeddings ambiguous" in reason for reason in result["reasons"])
    assert any("classifier: docs intent" in reason for reason in result["reasons"])


def test_deep_research_plan_uses_offline_rules_even_when_remote_router_configured(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "hybrid")
    monkeypatch.setenv("INTENT_EMBEDDING_API_URL", "https://embed.example.com/embeddings")
    monkeypatch.setenv("INTENT_EMBEDDING_API_KEY", "embed-secret")
    monkeypatch.setenv("INTENT_EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_URL", "https://classifier.example.com/chat/completions")
    monkeypatch.setenv("INTENT_CLASSIFIER_API_KEY", "classifier-secret")
    monkeypatch.setenv("INTENT_CLASSIFIER_MODEL", "intent-mini")

    async def should_not_run_remote(*args, **kwargs):
        raise AssertionError("deep planner must not call remote intent router components")

    monkeypatch.setattr(IntentRouter, "_semantic_route", should_not_run_remote)
    monkeypatch.setattr(IntentRouter, "_classifier_route", should_not_run_remote)

    from smart_search.research_service import build_research_workflow_plan
    from smart_search.research_plan import serialize_research_plan

    plan = build_research_workflow_plan("React useEffect API docs")
    ops = serialize_research_plan(plan)["operations"]

    assert ops
    assert all(item["operation"] in {"source_discovery", "docs_discovery", "content_fetch"} for item in ops)
    assert any(item["operation"] == "docs_discovery" for item in ops)
