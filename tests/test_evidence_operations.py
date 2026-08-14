"""Focused tests for the schema-neutral typed Evidence operation owners.

Covers typed model invariants, the exact operation state matrices, raw
admission rejection, provenance, deterministic ids, local capability status,
typed composition, one RequestContext reuse, and forbidden dependency
directions.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from smart_search import evidence_operations
from smart_search.evidence_operations import (
    ContentFetchRequest,
    DocsDiscoveryRequest,
    EvidenceDegradation,
    EvidenceOperationOutcome,
    EvidenceOperationStatus,
    EvidenceRouting,
    SiteDiscoveryRequest,
    SourceDiscoveryRequest,
)
from smart_search.execution_primitives import (
    ExecutionCandidate,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionMetadata,
    ExecutionOutcome,
    budget_exhausted_attempt,
    empty_attempt,
    error_attempt,
    success_attempt,
)
from smart_search.runtime_cache import current_context

OWNER_MODULE_PATH = Path("src/smart_search/evidence_operations.py")


def _candidate(identifier: str = "c-1", resource: str = "https://example.com/a") -> ExecutionCandidate:
    return ExecutionCandidate(identifier, resource, "tavily", "Title", "snippet")


def _evidence(identifier: str = "ev-1", resource: str = "https://example.com/a") -> ExecutionEvidenceItem:
    return ExecutionEvidenceItem(identifier, resource, "tavily", "Page", "Fetched body text")


def _complete_outcome(
    *,
    operation: str = "source_discovery",
    candidates: tuple[ExecutionCandidate, ...] = (),
    evidence_items: tuple[ExecutionEvidenceItem, ...] = (),
    attempts: tuple[Any, ...] = (),
    request_id: str = "req-1",
) -> EvidenceOperationOutcome:
    executed = (operation,) if attempts else ()
    return EvidenceOperationOutcome(
        operation=operation,
        status=EvidenceOperationStatus.COMPLETE,
        candidates=candidates,
        evidence_items=evidence_items,
        attempts=attempts,
        routing=EvidenceRouting((operation,), executed, "v2", (operation,)),
        metadata=ExecutionMetadata(request_id, 1),
    )


# ---------------------------------------------------------------------------
# Typed model invariants
# ---------------------------------------------------------------------------


def test_outcome_truth_table_complete_degraded_failed():
    ok_attempt = success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1)
    failed_attempt = error_attempt(
        "web_search", "tavily", error_type="network_error", message="fail", elapsed_ms=1.0
    )
    cand = _candidate()

    complete = _complete_outcome(candidates=(cand,), attempts=(ok_attempt,))
    assert complete.status is EvidenceOperationStatus.COMPLETE

    degraded = EvidenceOperationOutcome(
        operation="source_discovery",
        status=EvidenceOperationStatus.DEGRADED,
        candidates=(cand,),
        attempts=(failed_attempt, ok_attempt),
        degradation=(
            EvidenceDegradation(
                "provider_partial_failure", "source_discovery", "One or more providers failed"
            ),
        ),
        routing=EvidenceRouting(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
        metadata=ExecutionMetadata("req-1", 1),
    )
    assert degraded.status is EvidenceOperationStatus.DEGRADED

    failed = EvidenceOperationOutcome(
        operation="source_discovery",
        status=EvidenceOperationStatus.FAILED,
        candidates=(),
        attempts=(failed_attempt,),
        error=ExecutionError("network_error", "source_discovery failed", retryable=None),
        routing=EvidenceRouting(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
        metadata=ExecutionMetadata("req-1", 1),
    )
    assert failed.status is EvidenceOperationStatus.FAILED

    # invalid combinations are rejected at construction
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            error=ExecutionError("network_error", "boom", retryable=False),
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.DEGRADED,
            error=ExecutionError("network_error", "boom", retryable=False),
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.DEGRADED,
            degradation=(),
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.FAILED,
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError("network_error", "boom", retryable=False),
            degradation=(
                EvidenceDegradation("provider_partial_failure", "source_discovery", "bad"),
            ),
        )
    # complete cannot contain error/skipped attempts
    with pytest.raises(ValueError):
        _complete_outcome(attempts=(failed_attempt,))


def test_outcome_rejects_unknown_operation_and_status():
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="web_search",
            status=EvidenceOperationStatus.COMPLETE,
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status="succeeded",
        )


def test_outcome_operation_specific_shapes():
    cand = _candidate()
    item = _evidence()
    # discovery can only carry candidates
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(cand,),
            evidence_items=(item,),
        )
    # content_fetch can only carry evidence items
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(cand,),
            evidence_items=(item,),
        )
    # capability_status is local-only
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(cand,),
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.COMPLETE,
            routing=EvidenceRouting(("source_discovery",), (), "v2", ()),
        )


def test_outcome_immutable_collections_and_defensive_copies():
    from collections.abc import Mapping

    outcome = EvidenceOperationOutcome(
        operation="capability_status",
        status=EvidenceOperationStatus.COMPLETE,
        routing=EvidenceRouting((), (), "v2-capability-status-1", ("local_inspection",)),
        metadata=ExecutionMetadata("cap-1", 1),
        local_data={"capabilities": {"core_availability": {"source_discovery": ["tavily"]}}},
    )
    assert isinstance(outcome.local_data, Mapping)
    assert isinstance(outcome.local_data, dict) is False
    # attempts are tuples and immutable
    outcome = _complete_outcome(
        candidates=(_candidate(),),
        attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1),),
    )
    assert isinstance(outcome.attempts, tuple)
    assert isinstance(outcome.candidates, tuple)
    with pytest.raises(AttributeError):
        outcome.attempts = ()  # type: ignore[misc]


def test_outcome_id_uniqueness_and_citation_references():
    item = _evidence()
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(item, _evidence()),
        )
    # dangling citation reference is rejected
    from smart_search.execution_primitives import ExecutionCitation

    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(item,),
            citations=(ExecutionCitation("cit-1", "missing-evidence", "label"),),
        )
    # valid citation passes
    outcome = EvidenceOperationOutcome(
        operation="content_fetch",
        status=EvidenceOperationStatus.COMPLETE,
        evidence_items=(item,),
        citations=(ExecutionCitation("cit-1", "ev-1", "label"),),
        routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("content_fetch",)),
        metadata=ExecutionMetadata("req-1", 1),
    )
    assert outcome.citations[0].evidence_id == "ev-1"


def test_outcome_routing_subset_and_unknown_operations():
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError("config_error", "no providers", retryable=False),
            routing=EvidenceRouting(
                ("source_discovery",),
                ("docs_discovery",),
                "v2",
                ("source_discovery",),
            ),
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError("config_error", "no providers", retryable=False),
            routing=EvidenceRouting(
                ("source_discovery", "web_search"),
                (),
                "v2",
                (),
            ),
        )


def test_outcome_json_safety():
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.COMPLETE,
            local_data={"bad": float("nan")},
        )
    with pytest.raises(ValueError):
        EvidenceOperationOutcome(
            operation="capability_status",
            status=EvidenceOperationStatus.COMPLETE,
            local_data={1: "non-string-key"},
        )


def test_owner_module_forbidden_imports():
    """The owner must not import V2/V3/Workflow contracts, CLI, service, V1
    serializer, legacy projection, or Provider adapter modules directly."""
    source = OWNER_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
    forbidden_prefixes = (
        "smart_search.v2_contract",
        "smart_search.control_plane_contract",
        "smart_search.cli",
        "smart_search.cli_contract",
        "smart_search.cli_dispatch",
        "smart_search.cli_render",
        "smart_search.cli_parser",
        "smart_search.cli_v2",
        "smart_search.cli_v3",
        "smart_search.service",
        "smart_search.search_service",
        "smart_search.research_service",
        "smart_search.operations_service",
        "smart_search.service_support",
        "smart_search.v1_contract",
        "smart_search.providers",
    )
    for module in imported:
        assert not any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes
        ), f"forbidden import: {module}"
    # only the typed _execute_* runtime helpers are consumed
    runtime_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.endswith("operation_runtime")
        for alias in node.names
    }
    allowed_runtime = {
        "_execute_web_search",
        "_execute_docs_search",
        "_execute_web_fetch",
        "_execute_site_map",
    }
    assert runtime_imports <= allowed_runtime
    for name in (
        "_run_web_search_fallback",
        "_run_docs_search_fallback",
        "_run_web_fetch_fallback",
        "_run_site_map",
        "project_attempts_dict",
        "project_attempt_dict",
    ):
        assert name not in imported_names, f"forbidden legacy boundary: {name}"


# ---------------------------------------------------------------------------
# Individual owner operations
# ---------------------------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("network should not be called")

    for name in (
        "_execute_web_search",
        "_execute_docs_search",
        "_execute_web_fetch",
        "_execute_site_map",
    ):
        monkeypatch.setattr(evidence_operations, name, boom)


@pytest.mark.asyncio
async def test_source_discovery_no_qualified_provider_zero_call(monkeypatch, no_network):
    monkeypatch.setattr(
        evidence_operations,
        "_qualified_providers",
        lambda operation: [],
    )
    outcome = await evidence_operations.source_discovery(SourceDiscoveryRequest("q"))
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.type == "config_error"
    assert outcome.attempts == ()
    assert outcome.candidates == ()
    assert outcome.routing.requested_operations == ("source_discovery",)
    assert outcome.routing.executed_operations == ()
    assert outcome.routing.reason_codes == ("configuration_error",)


@pytest.mark.asyncio
async def test_source_discovery_success_and_normal_empty(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        assert providers == "tavily"
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
    success = await evidence_operations.source_discovery(SourceDiscoveryRequest("hello"))
    assert success.status is EvidenceOperationStatus.COMPLETE
    assert len(success.candidates) == 1
    assert success.candidates[0].resource == "https://example.com/a"
    assert success.candidates[0].provider == "tavily"
    assert success.candidates[0].id.startswith("source_discovery-")

    empty = await evidence_operations.source_discovery(SourceDiscoveryRequest("empty query"))
    assert empty.status is EvidenceOperationStatus.COMPLETE
    assert empty.candidates == ()
    assert empty.attempts[0].status.value == "empty"


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
    outcome = await evidence_operations.source_discovery(SourceDiscoveryRequest("fallback"))
    assert outcome.status is EvidenceOperationStatus.DEGRADED
    assert len(outcome.degradation) == 1
    assert outcome.degradation[0].code == "provider_partial_failure"
    assert len(outcome.candidates) == 1
    assert [a.provider for a in outcome.attempts] == ["tavily", "firecrawl"]


@pytest.mark.asyncio
async def test_source_discovery_all_failed_terminal_error(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        return ExecutionOutcome(
            value=[],
            attempts=(
                error_attempt("web_search", "tavily", error_type="timeout", message="timed out", elapsed_ms=2.0),
            ),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
    outcome = await evidence_operations.source_discovery(SourceDiscoveryRequest("all fail"))
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.type == "timeout"
    assert outcome.error.message == "source_discovery failed"
    assert outcome.candidates == ()


@pytest.mark.asyncio
async def test_attempt_filtering_keeps_qualified_providers_only(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        return ExecutionOutcome(
            value=[{"url": "https://example.com/c", "title": "C", "provider": "tavily"}],
            attempts=(
                error_attempt("web_search", "unqualified-provider", error_type="config_error", message="skipped", elapsed_ms=1.0),
                success_attempt("web_search", "tavily", elapsed_ms=2.0, result_count=1),
            ),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
    outcome = await evidence_operations.source_discovery(SourceDiscoveryRequest("filter"))
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    assert [a.provider for a in outcome.attempts] == ["tavily"]
    assert len(outcome.candidates) == 1


@pytest.mark.asyncio
async def test_docs_discovery_resource_ids_and_max_results_boundary(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["context7"])

    async def fake_docs(query, count=5, providers="auto", fallback="auto"):
        assert query == "Python API docs"
        assert count == 2
        assert providers == "context7"
        return ExecutionOutcome(
            value=[
                {"url": "context7:/python", "title": "Python", "provider": "context7"},
                {"url": "context7:/typing", "title": "typing", "provider": "context7"},
                {"url": "context7:/requests", "title": "requests", "provider": "context7"},
            ],
            attempts=(success_attempt("docs_search", "context7", elapsed_ms=1.0, result_count=3),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_docs_search", fake_docs)
    outcome = await evidence_operations.docs_discovery(
        DocsDiscoveryRequest("Python API docs", max_results=2)
    )
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    assert len(outcome.candidates) == 2
    assert outcome.candidates[0].resource == "context7:/python"
    assert outcome.candidates[0].id.startswith("docs_discovery-")
    assert outcome.attempts[0].result_count == 3


@pytest.mark.asyncio
async def test_content_fetch_admission_and_fallback(monkeypatch):
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
    outcome = await evidence_operations.content_fetch(ContentFetchRequest("https://example.com/page"))
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    assert len(outcome.evidence_items) == 1
    assert outcome.evidence_items[0].content == "Fetched body text"
    assert outcome.evidence_items[0].id.startswith("evidence-")
    assert outcome.candidates == ()
    assert outcome.citations == ()


@pytest.mark.asyncio
async def test_content_fetch_bounds_default_limit_with_explicit_metadata(monkeypatch):
    from smart_search.evidence_operations import DEFAULT_FETCH_CONTENT_LIMIT

    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])
    long_body = "x" * (DEFAULT_FETCH_CONTENT_LIMIT + 500)

    async def fake_fetch(url, fallback="auto", preferred_order=None, providers=None):
        body = "short body" if url.endswith("/short") else long_body
        return ExecutionOutcome(
            value={
                "ok": True,
                "url": url,
                "provider": "tavily",
                "content": body,
            },
            attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=5.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_fetch", fake_fetch)
    outcome = await evidence_operations.content_fetch(ContentFetchRequest("https://example.com/page"))
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    item = outcome.evidence_items[0]
    assert len(item.content) == DEFAULT_FETCH_CONTENT_LIMIT
    assert item.truncated is True
    assert item.original_length == len(long_body)
    assert item.returned_length == len(item.content)

    short_outcome = await evidence_operations.content_fetch(
        ContentFetchRequest("https://example.com/short")
    )
    short = short_outcome.evidence_items[0]
    assert short.truncated is False
    assert short.original_length == short.returned_length == len(short.content)


@pytest.mark.asyncio
async def test_content_fetch_full_bypasses_default_cap(monkeypatch):
    from smart_search.evidence_operations import DEFAULT_FETCH_CONTENT_LIMIT

    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])
    long_body = "y" * (DEFAULT_FETCH_CONTENT_LIMIT + 500)

    async def fake_fetch(url, fallback="auto", preferred_order=None, providers=None):
        return ExecutionOutcome(
            value={"ok": True, "url": url, "provider": "tavily", "content": long_body},
            attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=5.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_fetch", fake_fetch)
    outcome = await evidence_operations.content_fetch(
        ContentFetchRequest("https://example.com/page", full=True)
    )
    item = outcome.evidence_items[0]
    assert item.truncated is False
    assert item.original_length == item.returned_length == len(long_body)
    assert len(item.content) == len(long_body)

    custom = await evidence_operations.content_fetch(
        ContentFetchRequest("https://example.com/page", content_limit=100)
    )
    assert custom.evidence_items[0].truncated is True
    assert custom.evidence_items[0].returned_length == 100


@pytest.mark.asyncio
async def test_content_fetch_request_rejects_invalid_budget_inputs():
    with pytest.raises(evidence_operations.CanonicalOperationError):
        ContentFetchRequest("https://example.com", content_limit=0)
    with pytest.raises(evidence_operations.CanonicalOperationError):
        ContentFetchRequest("https://example.com", content_limit="8000")
    with pytest.raises(evidence_operations.CanonicalOperationError):
        ContentFetchRequest("https://example.com", full="yes")


@pytest.mark.asyncio
async def test_content_fetch_rejects_challenge_and_missing_provenance(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def challenge(url, fallback="auto", preferred_order=None, providers=None):
        return ExecutionOutcome(
            value={
                "ok": True,
                "url": url,
                "provider": "tavily",
                "content": "Title: Just a moment... Checking if the site connection is secure",
            },
            attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=5.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_fetch", challenge)
    outcome = await evidence_operations.content_fetch(ContentFetchRequest("https://example.com/challenge"))
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.evidence_items == ()
    assert outcome.error is not None
    assert outcome.error.message == "content_fetch failed"

    async def no_provenance(url, fallback="auto", preferred_order=None, providers=None):
        return ExecutionOutcome(
            value={"ok": True, "url": url, "content": "body without provider"},
            attempts=(success_attempt("web_fetch", "tavily", elapsed_ms=5.0, result_count=1),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_fetch", no_provenance)
    outcome = await evidence_operations.content_fetch(ContentFetchRequest("https://example.com/noprov"))
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.evidence_items == ()


@pytest.mark.asyncio
async def test_site_discovery_normalization_and_budget_refusal(monkeypatch):
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])

    async def fake_map(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        assert url == "https://docs.example.com"
        return ExecutionOutcome(
            value={
                "ok": True,
                "results": [
                    "https://docs.example.com/api",
                    {"url": "https://docs.example.com/guide", "title": "Guide"},
                ],
            },
            attempts=(success_attempt("site_map", "tavily", elapsed_ms=1.0, result_count=2),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_site_map", fake_map)
    outcome = await evidence_operations.site_discovery(SiteDiscoveryRequest("https://docs.example.com"))
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    assert len(outcome.candidates) == 2
    assert outcome.candidates[0].resource == "https://docs.example.com/api"
    assert outcome.candidates[0].provider == "tavily"
    assert outcome.evidence_items == ()

    # budget refusal surfaces a typed budget attempt and no provider call
    calls = {"map": 0}

    async def refused_map(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        calls["map"] += 1
        return ExecutionOutcome(
            value={"ok": False, "results": [], "provider": "request-budget"},
            attempts=(
                budget_exhausted_attempt("site_map", elapsed_ms=1.0),
            ),
        )

    monkeypatch.setattr(evidence_operations, "_execute_site_map", refused_map)
    outcome = await evidence_operations.site_discovery(SiteDiscoveryRequest("https://docs.example.com"))
    assert calls["map"] == 1
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.type == "budget_exhausted"
    assert outcome.error.message == "site_discovery failed"


def test_capability_status_local_only(monkeypatch, no_network):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    outcome = evidence_operations.capability_status(request_id="cap-1")
    assert outcome.operation == "capability_status"
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    assert outcome.attempts == ()
    assert outcome.routing.requested_operations == ()
    assert outcome.routing.executed_operations == ()
    assert outcome.candidates == ()
    assert outcome.evidence_items == ()
    assert "capabilities" in outcome.local_data
    assert "core_availability" in outcome.local_data["capabilities"]


def test_capability_status_configuration_failure_is_classified_and_redacted(monkeypatch):
    from smart_search.config import ModelRoutesConfigurationError

    def fail_status():
        raise ModelRoutesConfigurationError("Bearer private-config-token")

    monkeypatch.setattr(evidence_operations, "get_capability_status", fail_status)
    outcome = evidence_operations.capability_status(request_id="cap-config")
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.type == "config_error"
    assert "private-config-token" not in outcome.error.message


def test_capability_status_internal_failure_is_fixed_and_non_leaking(monkeypatch):
    def fail_status():
        raise RuntimeError("Bearer private-token")

    monkeypatch.setattr(evidence_operations, "get_capability_status", fail_status)
    outcome = evidence_operations.capability_status(request_id="cap-failure")
    assert outcome.status is EvidenceOperationStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.type == "internal_error"
    assert "private-token" not in outcome.error.message


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
    outcome = evidence_operations.capability_status(request_id="cap-runtime")
    capabilities = outcome.local_data["capabilities"]
    assert capabilities["core_availability"] == {
        "source_discovery": ("tavily",),
        "docs_discovery": (),
        "content_fetch": ("jina",),
    }
    assert capabilities["availability_by_tier"]["optional_extension"] == {
        "answer_synthesis": ("openai-compatible",)
    }
    assert capabilities["legacy_status"]["web_search"]["eligible"] == ("tavily",)
    assert "qualification_by_tier" in capabilities


def test_capability_status_disabled_provider_is_configured_but_not_eligible(monkeypatch):
    monkeypatch.setattr(
        evidence_operations,
        "get_capability_status",
        lambda: {
            "source_discovery": {
                "configured": ["tavily", "jina"],
                "ok": True,
                "provider_status": [
                    {
                        "provider": "tavily",
                        "configured": True,
                        "enabled": True,
                        "eligible": True,
                    },
                    {
                        "provider": "jina",
                        "configured": True,
                        "enabled": False,
                        "eligible": False,
                    },
                ],
            }
        },
    )
    outcome = evidence_operations.capability_status(request_id="cap-disabled")
    legacy_status = outcome.local_data["capabilities"]["legacy_status"]["source_discovery"]
    assert legacy_status["configured"] == ("tavily", "jina")
    assert legacy_status["eligible"] == ("tavily",)
    assert legacy_status["ok"] is True


# ---------------------------------------------------------------------------
# Typed composition
# ---------------------------------------------------------------------------


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
        return _complete_outcome(
            attempts=(empty_attempt("web_search", "tavily", elapsed_ms=1.0),),
            request_id="s",
        )
    return _complete_outcome(
        candidates=(ExecutionCandidate("c-src", "https://example.com/src", "tavily", "Src", "s"),),
        attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1),),
        request_id="s",
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
        return _complete_outcome(
            operation="docs_discovery",
            attempts=(empty_attempt("docs_search", "context7", elapsed_ms=1.0),),
            request_id="d",
        )
    return _complete_outcome(
        operation="docs_discovery",
        candidates=(ExecutionCandidate("c-docs", "context7:/lib", "context7", "Docs", "api"),),
        attempts=(success_attempt("docs_search", "context7", elapsed_ms=1.0, result_count=1),),
        request_id="d",
    )


@pytest.mark.asyncio
async def test_composite_matrix(monkeypatch):
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

    both_empty = await evidence_operations.composite_search("source-empty docs-empty API docs")
    assert both_empty.status is EvidenceOperationStatus.COMPLETE
    assert both_empty.candidates == ()
    assert both_empty.operation == "source_discovery"
    assert both_empty.routing.requested_operations == ("source_discovery", "docs_discovery")

    source_empty_docs_ok = await evidence_operations.composite_search("source-empty API docs")
    assert source_empty_docs_ok.status is EvidenceOperationStatus.COMPLETE
    assert len(source_empty_docs_ok.candidates) == 1

    source_fail_docs_ok = await evidence_operations.composite_search("source-fail API docs")
    assert source_fail_docs_ok.status is EvidenceOperationStatus.DEGRADED
    assert len(source_fail_docs_ok.candidates) == 1
    assert source_fail_docs_ok.degradation[0].code == "capability_unavailable"

    source_ok_docs_fail = await evidence_operations.composite_search("hello API docs-fail")
    assert source_ok_docs_fail.status is EvidenceOperationStatus.DEGRADED
    assert len(source_ok_docs_fail.candidates) == 1

    both_fail = await evidence_operations.composite_search("source-fail docs-fail API docs")
    assert both_fail.status is EvidenceOperationStatus.FAILED
    assert both_fail.error is not None
    assert both_fail.error.type == "config_error"

    docs_disabled = await evidence_operations.composite_search("hello world")
    assert docs_disabled.status is EvidenceOperationStatus.COMPLETE
    assert docs_disabled.routing.requested_operations == ("source_discovery",)
    assert len(docs_disabled.candidates) == 1


@pytest.mark.asyncio
async def test_composite_dedupe_and_order(monkeypatch):
    async def source(request):
        return _complete_outcome(
            candidates=(
                ExecutionCandidate("c-1", "https://example.com/shared", "tavily", "Source", "s"),
                ExecutionCandidate("c-2", "https://example.com/only-source", "tavily", "Only", "s"),
            ),
            attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=2),),
        )

    async def docs(request):
        return _complete_outcome(
            operation="docs_discovery",
            candidates=(
                ExecutionCandidate("c-3", "https://example.com/shared", "context7", "Shared", "api"),
                ExecutionCandidate("c-4", "context7:/lib", "context7", "Docs", "api"),
            ),
            attempts=(success_attempt("docs_search", "context7", elapsed_ms=1.0, result_count=2),),
        )

    monkeypatch.setattr(evidence_operations, "source_discovery", source)
    monkeypatch.setattr(evidence_operations, "docs_discovery", docs)
    monkeypatch.setattr(
        evidence_operations,
        "project_evidence_routing",
        lambda query: {"include_docs_discovery": True},
    )
    outcome = await evidence_operations.composite_search("API docs")
    # source-then-docs order; first occurrence wins for the shared resource
    assert [c.resource for c in outcome.candidates] == [
        "https://example.com/shared",
        "https://example.com/only-source",
        "context7:/lib",
    ]
    assert [c.provider for c in outcome.candidates] == ["tavily", "tavily", "context7"]
    assert [a.provider for a in outcome.attempts] == ["tavily", "context7"]


@pytest.mark.asyncio
async def test_composite_reuses_one_request_context(monkeypatch):
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

    outcome = await evidence_operations.composite_search("Python API docs")
    assert outcome.status is EvidenceOperationStatus.COMPLETE
    assert len(seen_contexts) == 2
    assert len(set(seen_contexts)) == 1