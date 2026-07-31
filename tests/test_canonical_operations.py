from __future__ import annotations

import asyncio
from typing import Any

import pytest

from smart_search import (
    api_v2,
    canonical_operations,
    capability_executor,
    operation_runtime,
    search_service,
)
from smart_search.canonical_operations import (
    ContentFetchRequest,
    DocsDiscoveryRequest,
    SiteDiscoveryRequest,
    SourceDiscoveryRequest,
)
from smart_search.v2_contract import V2Status, serialize_result, validate_envelope_dict


@pytest.fixture
def no_network(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("network should not be called")

    for name in (
        "_default_call_tavily_search",
        "_default_call_firecrawl_search",
        "_default_zhipu_search",
        "_default_zhipu_mcp_search",
        "_default_exa_search",
        "_default_context7_library",
        "_default_call_tavily_extract",
        "_default_call_firecrawl_scrape",
        "_default_jina_fetch",
        "_default_zhipu_mcp_reader",
        "_default_call_tavily_map",
        "_default_anysearch_search",
    ):
        monkeypatch.setattr(f"smart_search.operation_runtime.{name}", boom)


@pytest.mark.asyncio
async def test_source_discovery_config_error_with_only_synthesis_providers(monkeypatch, no_network):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)

    calls = {"search": 0, "main": 0}

    async def spy_search(*args, **kwargs):
        calls["search"] += 1
        raise AssertionError("legacy search must not run")

    monkeypatch.setattr(search_service, "search", spy_search)

    result = await canonical_operations.source_discovery(SourceDiscoveryRequest("latest AI news"))
    assert result.status is V2Status.FAILED
    assert result.error is not None
    assert result.error.code == "CONFIGURATION_ERROR" or result.error.code.value == "CONFIGURATION_ERROR"
    assert result.attempts == ()
    assert calls["search"] == 0
    payload = serialize_result(result)
    validate_envelope_dict(payload)
    assert "answer" not in payload["result"]
    assert payload["operation"] == "source_discovery"


@pytest.mark.asyncio
async def test_source_discovery_success_and_empty(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        if "empty" in query:
            return [], [{"capability": "web_search", "provider": "tavily", "status": "empty", "elapsed_ms": 1, "result_count": 0}]
        return (
            [{"url": "https://example.com/a", "title": "A", "description": "snippet", "provider": "tavily"}],
            [{"capability": "web_search", "provider": "tavily", "status": "ok", "elapsed_ms": 3, "result_count": 1}],
        )

    monkeypatch.setattr(canonical_operations, "_run_web_search_fallback", fake_web)
    monkeypatch.setattr(
        canonical_operations,
        "_qualified_providers",
        lambda operation: ["tavily"] if operation == "source_discovery" else [],
    )

    success = await canonical_operations.source_discovery(SourceDiscoveryRequest("hello world"))
    assert success.status is V2Status.COMPLETE
    assert success.result["total"] == 1
    assert len(success.evidence.candidates) == 1
    assert success.evidence.citations == ()

    empty = await canonical_operations.source_discovery(SourceDiscoveryRequest("empty query"))
    assert empty.status is V2Status.COMPLETE
    assert empty.result["total"] == 0


@pytest.mark.asyncio
async def test_source_discovery_degraded_fallback(monkeypatch):
    monkeypatch.setattr(
        canonical_operations,
        "_qualified_providers",
        lambda operation: ["tavily", "firecrawl"],
    )

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        return (
            [{"url": "https://example.com/b", "title": "B", "description": "ok", "provider": "firecrawl"}],
            [
                {
                    "capability": "web_search",
                    "provider": "tavily",
                    "status": "error",
                    "error_type": "network_error",
                    "elapsed_ms": 2,
                    "result_count": 0,
                },
                {
                    "capability": "web_search",
                    "provider": "firecrawl",
                    "status": "ok",
                    "elapsed_ms": 4,
                    "result_count": 1,
                },
            ],
        )

    monkeypatch.setattr(canonical_operations, "_run_web_search_fallback", fake_web)
    result = await canonical_operations.source_discovery(SourceDiscoveryRequest("fallback"))
    assert result.status is V2Status.DEGRADED
    assert result.degradation
    assert result.result["total"] == 1


@pytest.mark.asyncio
async def test_content_fetch_evidence_only(monkeypatch):
    monkeypatch.setattr(canonical_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_fetch(url, fallback="auto", preferred_order=None, providers=None):
        assert providers == ["tavily"]
        return (
            {
                "ok": True,
                "url": url,
                "provider": "tavily",
                "title": "Page",
                "content": "Fetched body text",
            },
            [{"capability": "web_fetch", "provider": "tavily", "status": "ok", "elapsed_ms": 5, "result_count": 1}],
        )

    monkeypatch.setattr(canonical_operations, "_run_web_fetch_fallback", fake_fetch)
    result = await canonical_operations.content_fetch(ContentFetchRequest("https://example.com/page"))
    assert result.status is V2Status.COMPLETE
    assert len(result.evidence.items) == 1
    assert result.evidence.items[0].content == "Fetched body text"
    assert result.evidence.candidates == ()
    assert result.evidence.citations == ()


@pytest.mark.asyncio
async def test_composite_search_reuses_one_request_context(monkeypatch):
    from smart_search.runtime_cache import current_context

    seen_contexts: list[int] = []
    monkeypatch.setattr(
        canonical_operations,
        "_qualified_providers",
        lambda operation: ["tavily"] if operation == "source_discovery" else ["context7"],
    )

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        context = current_context()
        assert context is not None
        seen_contexts.append(id(context))
        return (
            [{"url": "https://example.com", "title": "Source", "provider": "tavily"}],
            [{"capability": "web_search", "provider": "tavily", "status": "ok", "result_count": 1}],
        )

    async def fake_docs(query, count=5, providers="auto", fallback="auto"):
        context = current_context()
        assert context is not None
        seen_contexts.append(id(context))
        return (
            [{"url": "context7:/docs", "title": "Docs", "provider": "context7"}],
            [{"capability": "docs_search", "provider": "context7", "status": "ok", "result_count": 1}],
        )

    monkeypatch.setattr(canonical_operations, "_run_web_search_fallback", fake_web)
    monkeypatch.setattr(canonical_operations, "_run_docs_search_fallback", fake_docs)

    result = await canonical_operations.composite_search("Python API docs")
    assert result.status is V2Status.COMPLETE
    assert len(seen_contexts) == 2
    assert len(set(seen_contexts)) == 1


@pytest.mark.asyncio
async def test_fetch_quality_failure_continues_same_capability_fallback(monkeypatch):
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [
            {"provider": "tavily", "configured": True, "eligible": True},
            {"provider": "firecrawl", "configured": True, "eligible": True},
        ],
    )

    async def challenge(url):
        return "Title: Just a moment... Checking if the site connection is secure"

    async def valid(url):
        return "Verified fetched content"

    monkeypatch.setattr(search_service, "call_tavily_extract", challenge)
    monkeypatch.setattr(search_service, "call_firecrawl_scrape", valid)

    value, attempts = await operation_runtime._run_web_fetch_fallback(
        "https://example.com",
        providers=["tavily", "firecrawl"],
        preferred_order=["tavily", "firecrawl"],
    )
    assert value is not None
    assert value["provider"] == "firecrawl"
    assert [item["provider"] for item in attempts] == ["tavily", "firecrawl"]
    assert attempts[0]["status"] == "error"
    assert attempts[0]["error_type"] == "quality_error"
    assert attempts[1]["status"] == "ok"


@pytest.mark.asyncio
async def test_site_map_uses_executor_request_budget(monkeypatch):
    calls = 0

    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [{"provider": "tavily", "configured": True, "eligible": True}],
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: False)

    async def should_not_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"ok": True, "results": ["https://example.com/page"]}

    monkeypatch.setattr(operation_runtime, "_default_call_tavily_map", should_not_run)
    value, attempts = await operation_runtime._run_site_map("https://example.com")
    assert calls == 0
    assert value is not None
    assert value["ok"] is False
    assert attempts[0]["error_type"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_v1_context7_runner_does_not_apply_v2_result_limit(monkeypatch):
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [{"provider": "context7", "configured": True, "eligible": True}],
    )

    async def seven_results(name, query):
        return {
            "ok": True,
            "results": [
                {"id": f"/library/{index}", "title": f"Library {index}"}
                for index in range(7)
            ],
        }

    monkeypatch.setattr(search_service, "context7_library", seven_results)
    values, attempts = await operation_runtime._run_docs_search_fallback(
        "library docs",
        providers="context7",
    )
    assert len(values) == 7
    assert attempts[-1]["result_count"] == 7


@pytest.mark.asyncio
async def test_same_capability_only_no_main_search_spy(monkeypatch):
    monkeypatch.setattr(canonical_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        assert "openai" not in providers
        assert "xai" not in providers
        return [], [{"capability": "web_search", "provider": "tavily", "status": "empty", "elapsed_ms": 1, "result_count": 0}]

    called = {"search": 0}

    async def spy_search(*args, **kwargs):
        called["search"] += 1
        return {"ok": True, "answer": "should not run"}

    monkeypatch.setattr(canonical_operations, "_run_web_search_fallback", fake_web)
    monkeypatch.setattr(search_service, "search", spy_search)
    result = await canonical_operations.source_discovery(SourceDiscoveryRequest("q"))
    assert called["search"] == 0
    assert "answer" not in result.result


def test_capability_status_local_only(monkeypatch, no_network):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    result = canonical_operations.capability_status(request_id="cap-1")
    assert result.operation == "capability_status"
    assert result.command == "capabilities"
    assert result.status is V2Status.COMPLETE
    assert result.attempts == ()
    assert result.routing.requested_capabilities == ()
    assert result.evidence.candidates == ()
    payload = serialize_result(result)
    assert "core_availability" in payload["result"]["capabilities"]


def test_capability_status_configuration_failure_is_classified_and_redacted(monkeypatch):
    from smart_search.config import ModelRoutesConfigurationError

    def fail_status():
        raise ModelRoutesConfigurationError("Bearer private-config-token")

    monkeypatch.setattr(canonical_operations, "get_capability_status", fail_status)
    payload = serialize_result(canonical_operations.capability_status(request_id="cap-config"))
    assert payload["operation"] == "capability_status"
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert "private-config-token" not in str(payload)


def test_capability_status_reports_runtime_availability_separately(monkeypatch):
    runtime = {
        "source_discovery": ["tavily"],
        "docs_discovery": [],
        "content_fetch": ["jina"],
        "site_discovery": [],
        "answer_synthesis": ["openai-compatible"],
    }
    monkeypatch.setattr(
        canonical_operations,
        "_qualified_providers",
        lambda operation: list(runtime[operation]),
    )
    monkeypatch.setattr(
        canonical_operations,
        "get_capability_status",
        lambda: {
            "web_search": {"configured": ["tavily"], "ok": True},
            "web_fetch": {"configured": ["jina"], "ok": True},
        },
    )

    payload = serialize_result(canonical_operations.capability_status(request_id="cap-runtime"))
    capabilities = payload["result"]["capabilities"]
    assert capabilities["core_availability"] == {
        "source_discovery": ["tavily"],
        "docs_discovery": [],
        "content_fetch": ["jina"],
    }
    assert capabilities["availability_by_tier"]["optional_extension"] == {
        "answer_synthesis": ["openai-compatible"]
    }
    assert capabilities["legacy_status"]["web_search"]["eligible"] == ["tavily"]
    assert "qualification_by_tier" in capabilities


def test_capability_status_internal_failure_does_not_expose_exception_text(monkeypatch):
    def fail_status():
        raise RuntimeError("Bearer private-token")

    monkeypatch.setattr(canonical_operations, "get_capability_status", fail_status)
    payload = serialize_result(canonical_operations.capability_status(request_id="cap-failure"))
    assert payload["operation"] == "capability_status"
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "private-token" not in str(payload)


@pytest.mark.asyncio
async def test_api_v2_facade_matches_canonical(monkeypatch):
    monkeypatch.setattr(canonical_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        return (
            [{"url": "https://example.com/a", "title": "A", "description": "s", "provider": "tavily"}],
            [{"capability": "web_search", "provider": "tavily", "status": "ok", "elapsed_ms": 1, "result_count": 1}],
        )

    monkeypatch.setattr(canonical_operations, "_run_web_search_fallback", fake_web)
    req = SourceDiscoveryRequest("parity")
    left = await canonical_operations.source_discovery(req)
    right = await api_v2.source_discovery(req)
    # request_id/duration differ; compare stable fields
    assert left.operation == right.operation
    assert left.status == right.status
    assert left.result["total"] == right.result["total"]
    assert len(left.evidence.candidates) == len(right.evidence.candidates)
    assert set(api_v2.__all__) == {
        "ContentFetchRequest",
        "DocsDiscoveryRequest",
        "SiteDiscoveryRequest",
        "SourceDiscoveryRequest",
        "V2Envelope",
        "capability_status",
        "content_fetch",
        "docs_discovery",
        "site_discovery",
        "source_discovery",
    }
    from smart_search import service
    for name in api_v2.__all__:
        assert name not in service.__all__


@pytest.mark.asyncio
async def test_composite_search_matrix(monkeypatch):
    monkeypatch.setattr(
        canonical_operations,
        "_qualified_providers",
        lambda operation: ["tavily"] if operation == "source_discovery" else ["context7"],
    )

    async def fake_source(request):
        from smart_search.v2_contract import (
            V2Attempt,
            V2Candidate,
            V2Envelope,
            V2Evidence,
            V2Meta,
            V2Routing,
            V2Status,
            validate_result,
        )
        q = request.query
        if "source-fail" in q:
            from smart_search.v2_contract import V2Error, V2ErrorCode
            return validate_result(V2Envelope(
                V2Status.FAILED, "search", "source_discovery", {}, V2Evidence(),
                V2Routing(("source_discovery",), (), "v2", ()), (), (),
                V2Error(V2ErrorCode.CONFIGURATION_ERROR, "source config", False, {}),
                V2Meta("s", 1),
            ))
        if "source-empty" in q:
            return validate_result(V2Envelope(
                V2Status.COMPLETE, "search", "source_discovery", {"total": 0, "items": []},
                V2Evidence(),
                V2Routing(("source_discovery",), ("source_discovery",), "v2", ()),
                (V2Attempt("source_discovery", "tavily", "empty", None, 1, 0),),
                (), None, V2Meta("s", 1),
            ))
        cand = V2Candidate("c-src", "https://example.com/src", "tavily", "Src", "s")
        return validate_result(V2Envelope(
            V2Status.COMPLETE, "search", "source_discovery", {"total": 1, "items": [{"id": "c-src"}]},
            V2Evidence(candidates=(cand,)),
            V2Routing(("source_discovery",), ("source_discovery",), "v2", ()),
            (V2Attempt("source_discovery", "tavily", "ok", None, 1, 1),),
            (), None, V2Meta("s", 1),
        ))

    async def fake_docs(request):
        from smart_search.v2_contract import (
            V2Attempt,
            V2Candidate,
            V2Envelope,
            V2Error,
            V2ErrorCode,
            V2Evidence,
            V2Meta,
            V2Routing,
            V2Status,
            validate_result,
        )
        q = request.query
        if "docs-fail" in q:
            return validate_result(V2Envelope(
                V2Status.FAILED, "search", "docs_discovery", {}, V2Evidence(),
                V2Routing(("docs_discovery",), (), "v2", ()), (), (),
                V2Error(V2ErrorCode.CONFIGURATION_ERROR, "docs config", False, {}),
                V2Meta("d", 1),
            ))
        if "docs-empty" in q:
            return validate_result(V2Envelope(
                V2Status.COMPLETE, "search", "docs_discovery", {"total": 0, "items": []},
                V2Evidence(),
                V2Routing(("docs_discovery",), ("docs_discovery",), "v2", ()),
                (V2Attempt("docs_discovery", "context7", "empty", None, 1, 0),),
                (), None, V2Meta("d", 1),
            ))
        cand = V2Candidate("c-docs", "context7:/lib", "context7", "Docs", "api")
        return validate_result(V2Envelope(
            V2Status.COMPLETE, "search", "docs_discovery", {"total": 1, "items": [{"id": "c-docs"}]},
            V2Evidence(candidates=(cand,)),
            V2Routing(("docs_discovery",), ("docs_discovery",), "v2", ()),
            (V2Attempt("docs_discovery", "context7", "ok", None, 1, 1),),
            (), None, V2Meta("d", 1),
        ))

    monkeypatch.setattr(canonical_operations, "source_discovery", fake_source)
    monkeypatch.setattr(canonical_operations, "docs_discovery", fake_docs)

    # docs intent via "API docs"
    both_empty = await canonical_operations.composite_search("source-empty docs-empty API docs")
    assert both_empty.status is V2Status.COMPLETE
    assert both_empty.result["total"] == 0
    assert both_empty.operation == "source_discovery"

    source_empty_docs_ok = await canonical_operations.composite_search("source-empty API docs")
    assert source_empty_docs_ok.status is V2Status.COMPLETE
    assert source_empty_docs_ok.result["total"] == 1

    source_fail_docs_ok = await canonical_operations.composite_search("source-fail API docs")
    assert source_fail_docs_ok.status is V2Status.DEGRADED
    assert source_fail_docs_ok.result["total"] == 1

    source_ok_docs_fail = await canonical_operations.composite_search("hello API docs-fail")
    assert source_ok_docs_fail.status is V2Status.DEGRADED
    assert source_ok_docs_fail.result["total"] == 1

    both_fail = await canonical_operations.composite_search("source-fail docs-fail API docs")
    assert both_fail.status is V2Status.FAILED
    assert both_fail.error is not None


@pytest.mark.asyncio
async def test_docs_request_honors_max_results_and_site_returns_candidates(monkeypatch):
    monkeypatch.setattr(
        canonical_operations,
        "_qualified_providers",
        lambda operation: ["context7"] if operation == "docs_discovery" else ["tavily"],
    )

    async def fake_docs(query, count=5, providers="auto", fallback="auto"):
        assert query == "Python API docs"
        assert count == 2
        assert providers == "context7"
        return (
            [
                {"url": "context7:/python", "title": "Python", "provider": "context7"},
                {"url": "context7:/typing", "title": "typing", "provider": "context7"},
            ],
            [{"capability": "docs_search", "provider": "context7", "status": "ok", "elapsed_ms": 1, "result_count": 2}],
        )

    async def fake_map(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        assert url == "https://docs.example.com"
        return (
            {"results": ["https://docs.example.com/api"]},
            [{"capability": "site_map", "provider": "tavily", "status": "ok", "elapsed_ms": 1, "result_count": 1}],
        )

    monkeypatch.setattr(canonical_operations, "_run_docs_search_fallback", fake_docs)
    monkeypatch.setattr(canonical_operations, "_run_site_map", fake_map)

    docs = await canonical_operations.docs_discovery(DocsDiscoveryRequest("Python API docs", max_results=2))
    site = await canonical_operations.site_discovery(SiteDiscoveryRequest("https://docs.example.com"))
    assert docs.status is V2Status.COMPLETE
    assert docs.result["total"] == 2
    assert site.status is V2Status.COMPLETE
    assert site.evidence.items == ()
    assert len(site.evidence.candidates) == 1
