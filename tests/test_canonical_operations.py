"""Canonical v2 projection boundary tests.

The canonical module is a strict one-way projection from schema-neutral typed
Evidence owner outcomes (``evidence_operations``) into the public V2 envelope.
These tests assert owner-once wrapping, exact envelope parity, prohibited
projection dependencies, unchanged public facade/CLI behavior, and unchanged
runtime/executor behavior.
"""

from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from typing import Any

import pytest

from smart_search import (
    api_v2,
    canonical_operations,
    capability_executor,
    evidence_operations,
    operation_runtime,
)
from smart_search.canonical_operations import (
    ContentFetchRequest,
    DocsDiscoveryRequest,
    SiteDiscoveryRequest,
    SourceDiscoveryRequest,
)
from smart_search.evidence_operations import (
    EvidenceOperationOutcome,
    EvidenceOperationStatus,
    EvidenceRouting,
)
from smart_search.execution_primitives import (
    ExecutionCandidate,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionMetadata,
    ExecutionOutcome,
    empty_attempt,
    error_attempt,
    success_attempt,
)
from smart_search.v2_contract import V2Status, serialize_result, validate_envelope_dict


CANONICAL_MODULE_PATH = Path("src/smart_search/canonical_operations.py")


def _candidate(identifier: str = "c-1") -> ExecutionCandidate:
    return ExecutionCandidate(identifier, "https://example.com/a", "tavily", "Title", "snippet")


def _evidence(identifier: str = "ev-1") -> ExecutionEvidenceItem:
    return ExecutionEvidenceItem(identifier, "https://example.com/a", "tavily", "Page", "Fetched body text")


def _source_success_outcome(request_id: str = "req-1") -> EvidenceOperationOutcome:
    return EvidenceOperationOutcome(
        operation="source_discovery",
        status=EvidenceOperationStatus.COMPLETE,
        candidates=(_candidate(),),
        attempts=(success_attempt("web_search", "tavily", elapsed_ms=3.0, result_count=1),),
        routing=EvidenceRouting(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
        metadata=ExecutionMetadata(request_id, 5),
    )


def _source_config_failed_outcome(request_id: str = "req-1") -> EvidenceOperationOutcome:
    return EvidenceOperationOutcome(
        operation="source_discovery",
        status=EvidenceOperationStatus.FAILED,
        error=ExecutionError(
            "config_error",
            "No qualified source_discovery providers configured",
            retryable=False,
            details={"qualified_providers": []},
        ),
        routing=EvidenceRouting(("source_discovery",), (), "v2", ("configuration_error",)),
        metadata=ExecutionMetadata(request_id, 5),
    )


# ---------------------------------------------------------------------------
# Projection dependency boundary
# ---------------------------------------------------------------------------


def test_canonical_projection_boundary_imports_and_no_mapping_parsing():
    """Canonical V2 must be a pure projection: no runtime/qualification/config/
    routing/cache/provider imports, no legacy wrappers, no mapping .get() in the
    typed attempt projection."""
    source = CANONICAL_MODULE_PATH.read_text(encoding="utf-8")
    assert "_legacy_attempt_to_v2" not in source
    assert "legacy_attempts" not in source
    tree = ast.parse(source)
    imported_modules: list[str] = []
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
    forbidden_prefixes = (
        "smart_search.capability_service",
        "smart_search.capability_taxonomy",
        "smart_search.config",
        "smart_search.intent_router",
        "smart_search.operation_runtime",
        "smart_search.runtime_cache",
        "smart_search.providers",
        "smart_search.search_service",
        "smart_search.research_service",
        "smart_search.service",
    )
    for module in imported_modules:
        assert not any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes
        ), f"forbidden import: {module}"
    for name in (
        "_execute_web_search",
        "_execute_docs_search",
        "_execute_web_fetch",
        "_execute_site_map",
        "_run_web_search_fallback",
        "_run_docs_search_fallback",
        "_run_web_fetch_fallback",
        "_run_site_map",
        "project_attempts_dict",
    ):
        assert name not in imported_names, f"forbidden runtime/legacy symbol: {name}"

    # typed projection exists and its first parameter is annotated ExecutionAttempt
    proj = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_typed_attempt_to_v2"),
        None,
    )
    assert proj is not None
    assert proj.args.args and "ExecutionAttempt" in ast.unparse(proj.args.args[0].annotation)
    get_calls = [n for n in ast.walk(proj) if isinstance(n, ast.Attribute) and n.attr == "get"]
    assert get_calls == []


def test_projection_wrappers_call_owner_exactly_once(monkeypatch):
    calls = {"source": 0, "docs": 0, "fetch": 0, "site": 0, "composite": 0, "status": 0}

    async def fake_source(request):
        calls["source"] += 1
        return _source_success_outcome()

    async def fake_docs(request):
        calls["docs"] += 1
        return _source_success_outcome()

    async def fake_fetch(request):
        calls["fetch"] += 1
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(_evidence(),),
            attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=1.0, result_count=1),),
            routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("content_fetch",)),
            metadata=ExecutionMetadata("req-1", 1),
        )

    async def fake_site(request):
        calls["site"] += 1
        return _source_success_outcome()

    async def fake_composite(query, max_results=5):
        calls["composite"] += 1
        return _source_success_outcome()

    def fake_status(*, request_id=None):
        calls["status"] += 1
        return EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.COMPLETE,
            routing=EvidenceRouting((), (), "v2-capability-status-1", ("local_inspection",)),
            metadata=ExecutionMetadata(request_id or "cap-1", 1),
            local_data={"capabilities": {}},
        )

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "docs_discovery", fake_docs)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
    monkeypatch.setattr(evidence_operations, "site_discovery", fake_site)
    monkeypatch.setattr(evidence_operations, "composite_search", fake_composite)
    monkeypatch.setattr(evidence_operations, "capability_status", fake_status)

    asyncio.run(canonical_operations.source_discovery(SourceDiscoveryRequest("q")))
    asyncio.run(canonical_operations.docs_discovery(DocsDiscoveryRequest("q")))
    asyncio.run(canonical_operations.content_fetch(ContentFetchRequest("https://example.com")))
    asyncio.run(canonical_operations.site_discovery(SiteDiscoveryRequest("https://example.com")))
    asyncio.run(canonical_operations.composite_search("q"))
    canonical_operations.capability_status(request_id="cap-1")
    assert calls == {"source": 1, "docs": 1, "fetch": 1, "site": 1, "composite": 1, "status": 1}


# ---------------------------------------------------------------------------
# Exact envelope parity
# ---------------------------------------------------------------------------


def test_projection_complete_envelope_parity():
    envelope = canonical_operations._project_evidence_outcome(_source_success_outcome())
    payload = serialize_result(envelope)
    validate_envelope_dict(payload)
    assert payload["ok"] is True
    assert payload["status"] == "complete"
    assert payload["command"] == "search"
    assert payload["operation"] == "source_discovery"
    assert payload["result"] == {"total": 1, "items": [{"id": "c-1"}]}
    assert payload["evidence"]["candidates"][0]["provider"] == "tavily"
    assert payload["routing"]["requested_capabilities"] == ["source_discovery"]
    assert payload["routing"]["executed_capabilities"] == ["source_discovery"]
    assert payload["routing"]["policy_version"] == "v2"
    assert payload["attempts"][0]["capability"] == "source_discovery"
    assert payload["attempts"][0]["status"] == "ok"
    assert payload["attempts"][0]["error_code"] is None
    assert payload["error"] is None
    assert payload["degradation"] == []


def test_projection_config_failed_envelope_parity():
    envelope = canonical_operations._project_evidence_outcome(_source_config_failed_outcome())
    payload = serialize_result(envelope)
    validate_envelope_dict(payload)
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["command"] == "search"
    assert payload["operation"] == "source_discovery"
    assert payload["result"] == {}
    assert payload["routing"]["requested_capabilities"] == ["source_discovery"]
    assert payload["routing"]["executed_capabilities"] == []
    assert payload["routing"]["reason_codes"] == ["configuration_error"]
    assert payload["attempts"] == []
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["details"] == {"qualified_providers": []}
    assert payload["degradation"] == []


def test_projection_degraded_and_failed_envelope_parity():
    degraded = EvidenceOperationOutcome(
        operation="source_discovery",
        status=EvidenceOperationStatus.DEGRADED,
        candidates=(_candidate(),),
        attempts=(
            error_attempt("web_search", "tavily", error_type="network_error", message="fail", elapsed_ms=2.0),
            success_attempt("web_search", "firecrawl", elapsed_ms=4.0, result_count=1),
        ),
        degradation=(
            evidence_operations.EvidenceDegradation(
                "provider_partial_failure",
                "source_discovery",
                "One or more providers failed before a usable result",
            ),
        ),
        routing=EvidenceRouting(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
        metadata=ExecutionMetadata("req-1", 6),
    )
    payload = serialize_result(canonical_operations._project_evidence_outcome(degraded))
    assert payload["status"] == "degraded"
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["degradation"][0]["code"] == "provider_partial_failure"
    assert payload["attempts"][0]["status"] == "error"
    assert payload["attempts"][0]["error_code"] == "PROVIDER_UNAVAILABLE"

    failed = EvidenceOperationOutcome(
        operation="docs_discovery",
        status=EvidenceOperationStatus.FAILED,
        attempts=(
            error_attempt("docs_search", "context7", error_type="timeout", message="timed out", elapsed_ms=2.0),
        ),
        error=ExecutionError("timeout", "docs_discovery failed", retryable=None),
        routing=EvidenceRouting(("docs_discovery",), ("docs_discovery",), "v2", ("docs_discovery",)),
        metadata=ExecutionMetadata("req-1", 3),
    )
    payload = serialize_result(canonical_operations._project_evidence_outcome(failed))
    assert payload["status"] == "failed"
    assert payload["ok"] is False
    assert payload["command"] == "search"
    assert payload["operation"] == "docs_discovery"
    assert payload["result"] == {"total": 0, "items": []}
    assert payload["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert payload["error"]["message"] == "docs_discovery failed"
    assert payload["error"]["retryable"] is True


def test_projection_fetch_and_site_envelope_parity():
    fetch = EvidenceOperationOutcome(
        operation="content_fetch",
        status=EvidenceOperationStatus.COMPLETE,
        evidence_items=(_evidence(),),
        attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=5.0, result_count=1),),
        routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("content_fetch",)),
        metadata=ExecutionMetadata("req-1", 6),
    )
    payload = serialize_result(canonical_operations._project_evidence_outcome(fetch))
    assert payload["command"] == "fetch"
    assert payload["operation"] == "content_fetch"
    assert payload["result"] == {"total": 1, "items": [{"id": "ev-1"}]}
    assert payload["evidence"]["items"][0]["content"] == "Fetched body text"
    assert payload["evidence"]["candidates"] == []

    site = EvidenceOperationOutcome(
        operation="site_discovery",
        status=EvidenceOperationStatus.COMPLETE,
        candidates=(_candidate("c-site"),),
        attempts=(success_attempt("site_map", "tavily", elapsed_ms=1.0, result_count=1),),
        routing=EvidenceRouting(("site_discovery",), ("site_discovery",), "v2", ("site_discovery",)),
        metadata=ExecutionMetadata("req-1", 2),
    )
    payload = serialize_result(canonical_operations._project_evidence_outcome(site))
    assert payload["command"] == "map"
    assert payload["operation"] == "site_discovery"
    assert payload["result"] == {"total": 1, "items": [{"id": "c-site"}]}
    assert payload["evidence"]["items"] == []


def test_projection_capability_status_envelope_parity():
    outcome = EvidenceOperationOutcome(
        operation="capability_status",
        status=EvidenceOperationStatus.COMPLETE,
        routing=EvidenceRouting((), (), "v2-capability-status-1", ("local_inspection",)),
        metadata=ExecutionMetadata("cap-1", 3),
        local_data={"capabilities": {"core_availability": {"source_discovery": ["tavily"]}}},
    )
    payload = serialize_result(canonical_operations._project_evidence_outcome(outcome))
    assert payload["operation"] == "capability_status"
    assert payload["command"] == "capabilities"
    assert payload["status"] == "complete"
    assert payload["attempts"] == []
    assert payload["routing"]["requested_capabilities"] == []
    assert payload["routing"]["executed_capabilities"] == []
    assert payload["routing"]["policy_version"] == "v2-capability-status-1"
    assert payload["evidence"]["candidates"] == []
    assert payload["result"]["capabilities"]["core_availability"] == {"source_discovery": ["tavily"]}


# ---------------------------------------------------------------------------
# Behavior parity through the public wrappers
# ---------------------------------------------------------------------------


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

    calls = {"search": 0}
    import smart_search.research_service as research_service

    # The v1 live search facade is removed entirely.
    assert not hasattr(research_service, "search")

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
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        if "empty" in query:
            return ExecutionOutcome(
                value=[],
                attempts=(empty_attempt("web_search", "tavily", elapsed_ms=1.0),),
            )
        return ExecutionOutcome(
            value=[{"url": "https://example.com/a", "title": "A", "description": "snippet", "provider": "tavily"}],
            attempts=(success_attempt("web_search", "tavily", elapsed_ms=3.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)

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
        evidence_operations,
        "_qualified_providers",
        lambda operation: ["tavily", "firecrawl"],
    )

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        return ExecutionOutcome(
            value=[{"url": "https://example.com/b", "title": "B", "description": "ok", "provider": "firecrawl"}],
            attempts=(
                error_attempt("web_search", "tavily", error_type="network_error", message="network fail", elapsed_ms=2.0),
                success_attempt("web_search", "firecrawl", elapsed_ms=4.0, result_count=1),
            ),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
    result = await canonical_operations.source_discovery(SourceDiscoveryRequest("fallback"))
    assert result.status is V2Status.DEGRADED
    assert result.degradation
    assert result.result["total"] == 1


@pytest.mark.asyncio
async def test_content_fetch_evidence_only(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_fetch(url, fallback="auto", preferred_order=None, providers=None):
        assert providers == ["tavily"]
        return ExecutionOutcome(
            value={
                "ok": True,
                "url": url,
                "provider": "tavily",
                "title": "Page",
                "content": "Fetched body text",
            },
            attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=5.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_fetch", fake_fetch)
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
        evidence_operations,
        "_qualified_providers",
        lambda operation: ["tavily"] if operation == "source_discovery" else ["context7"],
    )

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        context = current_context()
        assert context is not None
        seen_contexts.append(id(context))
        return ExecutionOutcome(
            value=[{"url": "https://example.com", "title": "Source", "provider": "tavily"}],
            attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1),),
        )

    async def fake_docs(query, count=5, providers="auto", fallback="auto"):
        context = current_context()
        assert context is not None
        seen_contexts.append(id(context))
        return ExecutionOutcome(
            value=[{"url": "context7:/docs", "title": "Docs", "provider": "context7"}],
            attempts=(success_attempt("docs_search", "context7", elapsed_ms=1.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
    monkeypatch.setattr(evidence_operations, "_execute_docs_search", fake_docs)

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

    monkeypatch.setattr(operation_runtime, "_default_call_tavily_extract", challenge)
    monkeypatch.setattr(operation_runtime, "_default_call_firecrawl_scrape", valid)

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

    monkeypatch.setattr(operation_runtime, "_default_context7_library", seven_results)
    values, attempts = await operation_runtime._run_docs_search_fallback(
        "library docs",
        providers="context7",
    )
    assert len(values) == 7
    assert attempts[-1]["result_count"] == 7


@pytest.mark.asyncio
async def test_same_capability_only_no_main_search_spy(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        assert "openai" not in providers
        assert "xai" not in providers
        return ExecutionOutcome(
            value=[],
            attempts=(empty_attempt("web_search", "tavily", elapsed_ms=1.0),),
        )

    called = {"search": 0}

    async def spy_search(*args, **kwargs):
        called["search"] += 1
        return {"ok": True, "answer": "should not run"}

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
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

    monkeypatch.setattr(evidence_operations, "get_capability_status", fail_status)
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
        evidence_operations,
        "_qualified_providers",
        lambda operation: list(runtime[operation]),
    )
    monkeypatch.setattr(
        evidence_operations,
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

    monkeypatch.setattr(evidence_operations, "get_capability_status", fail_status)
    payload = serialize_result(canonical_operations.capability_status(request_id="cap-failure"))
    assert payload["operation"] == "capability_status"
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "private-token" not in str(payload)


@pytest.mark.asyncio
async def test_api_v2_facade_matches_canonical(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        return ExecutionOutcome(
            value=[{"url": "https://example.com/a", "title": "A", "description": "s", "provider": "tavily"}],
            attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
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
    import pytest as _pytest

    # The broad v1 facade is removed; api_v2 is the narrow typed Python facade.
    with _pytest.raises(ImportError):
        import smart_search.service  # noqa: F401


@pytest.mark.asyncio
async def test_composite_search_matrix(monkeypatch):
    monkeypatch.setattr(
        evidence_operations,
        "_qualified_providers",
        lambda operation: ["tavily"] if operation == "source_discovery" else ["context7"],
    )

    def _source_outcome(query: str) -> EvidenceOperationOutcome:
        if "source-fail" in query:
            return EvidenceOperationOutcome(
                operation="source_discovery",
                status=EvidenceOperationStatus.FAILED,
                error=ExecutionError(
                    "config_error",
                    "No qualified source_discovery providers configured",
                    retryable=False,
                ),
                routing=EvidenceRouting(("source_discovery",), (), "v2", ("configuration_error",)),
                metadata=ExecutionMetadata("s", 1),
            )
        if "source-empty" in query:
            return EvidenceOperationOutcome(
                operation="source_discovery",
                status=EvidenceOperationStatus.COMPLETE,
                attempts=(empty_attempt("web_search", "tavily", elapsed_ms=1.0),),
                routing=EvidenceRouting(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
                metadata=ExecutionMetadata("s", 1),
            )
        return EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(ExecutionCandidate("c-src", "https://example.com/src", "tavily", "Src", "s"),),
            attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1),),
            routing=EvidenceRouting(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
            metadata=ExecutionMetadata("s", 1),
        )

    def _docs_outcome(query: str) -> EvidenceOperationOutcome:
        if "docs-fail" in query:
            return EvidenceOperationOutcome(
                operation="docs_discovery",
                status=EvidenceOperationStatus.FAILED,
                error=ExecutionError("config_error", "docs config", retryable=False),
                routing=EvidenceRouting(("docs_discovery",), (), "v2", ("configuration_error",)),
                metadata=ExecutionMetadata("d", 1),
            )
        if "docs-empty" in query:
            return EvidenceOperationOutcome(
                operation="docs_discovery",
                status=EvidenceOperationStatus.COMPLETE,
                attempts=(empty_attempt("docs_search", "context7", elapsed_ms=1.0),),
                routing=EvidenceRouting(("docs_discovery",), ("docs_discovery",), "v2", ("docs_discovery",)),
                metadata=ExecutionMetadata("d", 1),
            )
        return EvidenceOperationOutcome(
            operation="docs_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(ExecutionCandidate("c-docs", "context7:/lib", "context7", "Docs", "api"),),
            attempts=(success_attempt("docs_search", "context7", elapsed_ms=1.0, result_count=1),),
            routing=EvidenceRouting(("docs_discovery",), ("docs_discovery",), "v2", ("docs_discovery",)),
            metadata=ExecutionMetadata("d", 1),
        )

    async def fake_source(request):
        return _source_outcome(request.query)

    async def fake_docs(request):
        return _docs_outcome(request.query)

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "docs_discovery", fake_docs)
    monkeypatch.setattr(
        evidence_operations,
        "project_evidence_routing",
        lambda query: {"include_docs_discovery": "API docs" in query},
    )

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
        evidence_operations,
        "_qualified_providers",
        lambda operation: ["context7"] if operation == "docs_discovery" else ["tavily"],
    )

    async def fake_docs(query, count=5, providers="auto", fallback="auto"):
        assert query == "Python API docs"
        assert count == 2
        assert providers == "context7"
        return ExecutionOutcome(
            value=[
                {"url": "context7:/python", "title": "Python", "provider": "context7"},
                {"url": "context7:/typing", "title": "typing", "provider": "context7"},
            ],
            attempts=(success_attempt("docs_search", "context7", elapsed_ms=1.0, result_count=2),),
        )

    async def fake_map(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        assert url == "https://docs.example.com"
        return ExecutionOutcome(
            value={"results": ["https://docs.example.com/api"], "ok": True},
            attempts=(success_attempt("site_map", "tavily", elapsed_ms=1.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_docs_search", fake_docs)
    monkeypatch.setattr(evidence_operations, "_execute_site_map", fake_map)

    docs = await canonical_operations.docs_discovery(DocsDiscoveryRequest("Python API docs", max_results=2))
    site = await canonical_operations.site_discovery(SiteDiscoveryRequest("https://docs.example.com"))
    assert docs.status is V2Status.COMPLETE
    assert docs.result["total"] == 2
    assert site.status is V2Status.COMPLETE
    assert site.evidence.items == ()
    assert len(site.evidence.candidates) == 1

def test_v2_too_large_attempt_maps_to_fetch_failed():
    """A too_large provider attempt projects as FETCH_FAILED, not generic."""
    failed = EvidenceOperationOutcome(
        operation="content_fetch",
        status=EvidenceOperationStatus.FAILED,
        attempts=(
            error_attempt("web_fetch", "jina", error_type="too_large", message="response body exceeds the 5242880-byte transport limit", elapsed_ms=2.0),
        ),
        error=ExecutionError("too_large", "content_fetch failed", retryable=False),
        routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("content_fetch",)),
        metadata=ExecutionMetadata("req-1", 3),
    )
    payload = serialize_result(canonical_operations._project_evidence_outcome(failed))
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "FETCH_FAILED"
    assert payload["attempts"][0]["error_code"] == "FETCH_FAILED"
    assert payload["result"] == {"total": 0, "items": []}
