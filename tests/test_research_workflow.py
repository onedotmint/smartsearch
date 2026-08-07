"""Focused tests for the typed Research Workflow domain and owner.

The owner composes the typed Evidence operation owners (``evidence_operations``)
for every stage; tests monkeypatch those owners with deterministic fakes so no
Provider, config, cache, or network code ever runs.
"""

from __future__ import annotations

import asyncio
import ast
import hashlib
import re
from pathlib import Path

import pytest

from smart_search import evidence_operations
from smart_search.evidence_operations import (
    EvidenceOperationOutcome,
    EvidenceOperationStatus,
    EvidenceRouting,
)
from smart_search.execution_primitives import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCandidate,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionMetadata,
)
from smart_search.research_plan import (
    ResearchPlanError,
    ResearchPlanOperation,
    build_research_plan,
)
from smart_search.research_workflow import (
    ArtifactStatus,
    ArtifactWriteResult,
    WORKFLOW_ERROR_EXIT_CODES,
    WorkflowArtifact,
    WorkflowDomainError,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowMeta,
    WorkflowOutcome,
    WorkflowRequest,
    WorkflowStage,
    WorkflowStageStatus,
    WorkflowStatus,
    run_research_workflow,
    workflow_url_dedupe_key,
)

OWNER_MODULE_PATH = Path("src/smart_search/research_workflow.py")


def _plan(*ops: ResearchPlanOperation):
    return build_research_plan(ops)


def _op(op_id: str, operation: str = "source_discovery", **kwargs) -> ResearchPlanOperation:
    return ResearchPlanOperation(
        id=op_id,
        operation=operation,
        input=kwargs.get("input", {"query": "q"}),
        constraints=kwargs.get("constraints", {}),
        depends_on=kwargs.get("depends_on", ()),
    )


def _candidate(index: int, url: str, provider: str = "tavily") -> ExecutionCandidate:
    return ExecutionCandidate(
        id=f"cand-{index}",
        resource=url,
        provider=provider,
        title=f"source {index}",
        snippet="snippet",
    )


def _evidence(index: int, url: str, provider: str = "jina") -> ExecutionEvidenceItem:
    return ExecutionEvidenceItem(
        id=f"evidence-{index}",
        resource=url,
        provider=provider,
        title=f"page {index}",
        content=f"body of {url}",
    )


def _discovery_outcome(
    *,
    candidates=(),
    attempts=(),
    status=EvidenceOperationStatus.COMPLETE,
    op="source_discovery",
) -> EvidenceOperationOutcome:
    return EvidenceOperationOutcome(
        operation=op,
        status=status,
        candidates=tuple(candidates),
        attempts=tuple(attempts),
        routing=EvidenceRouting((op,), (op,) if attempts else (), "v2", ("test",)),
        metadata=ExecutionMetadata("req-test", 1),
    )


def _fetch_outcome(
    *,
    items=(),
    attempts=(),
    status=EvidenceOperationStatus.COMPLETE,
    error=None,
) -> EvidenceOperationOutcome:
    return EvidenceOperationOutcome(
        operation="content_fetch",
        status=status,
        evidence_items=tuple(items),
        attempts=tuple(attempts),
        error=error,
        routing=EvidenceRouting(("content_fetch",), ("content_fetch",) if attempts else (), "v2", ("test",)),
        metadata=ExecutionMetadata("req-test", 1),
    )


def _ok_attempt(capability: str, provider: str) -> ExecutionAttempt:
    return ExecutionAttempt(
        capability=capability,
        provider=provider,
        status=ExecutionAttemptStatus.OK,
        elapsed_ms=1.0,
        result_count=1,
    )


async def _run(request: WorkflowRequest) -> WorkflowOutcome:
    return await run_research_workflow(request)


# ---------------------------------------------------------------------------
# Typed model invariants
# ---------------------------------------------------------------------------


def test_typed_outcome_structural_invariants():
    plan = _plan(_op("discover", "source_discovery"), _op("fetch", "content_fetch", depends_on=("discover",)))
    evidence = _evidence(1, "https://example.com/a")
    stage_complete = WorkflowStage(
        id="fetch",
        operation="content_fetch",
        status=WorkflowStageStatus.COMPLETE,
        order=2,
        input={},
        depends_on=("discover",),
        evidence_ids=("evidence-1",),
    )
    stage_ok = WorkflowStage(
        id="discover",
        operation="source_discovery",
        status=WorkflowStageStatus.COMPLETE,
        order=1,
        input={"query": "q"},
    )
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=plan,
        stages=(stage_ok, stage_complete),
        evidence=(evidence,),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        meta=WorkflowMeta("req-1", 1),
    )
    assert outcome.status is WorkflowStatus.COMPLETE

    # duplicate evidence ids
    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=plan,
            stages=(stage_ok, stage_complete),
            evidence=(evidence, evidence),
            citations=(),
            gaps=(),
            attempts=(),
            artifacts=(),
        )
    # dangling citation
    from smart_search.execution_primitives import ExecutionCitation

    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=plan,
            stages=(stage_ok, stage_complete),
            evidence=(evidence,),
            citations=(ExecutionCitation("cite-1", "missing-evidence", "label"),),
            gaps=(),
            attempts=(),
            artifacts=(),
        )
    # dangling stage dependency (plan stays valid; stage deps are checked
    # against the outcome stage ids)
    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=_plan(_op("fetch", "content_fetch")),
            stages=(
                WorkflowStage(
                    id="fetch",
                    operation="content_fetch",
                    status=WorkflowStageStatus.COMPLETE,
                    order=1,
                    input={},
                    depends_on=("missing",),
                ),
            ),
            evidence=(),
            citations=(),
            gaps=(),
            attempts=(),
            artifacts=(),
        )
    # non-contiguous order
    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=_plan(_op("a")),
            stages=(WorkflowStage(id="a", operation="source_discovery", status=WorkflowStageStatus.COMPLETE, order=5, input={}),),
            evidence=(),
            citations=(),
            gaps=(),
            attempts=(),
            artifacts=(),
        )
    # complete with error is rejected
    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=_plan(_op("a")),
            stages=(WorkflowStage(id="a", operation="source_discovery", status=WorkflowStageStatus.COMPLETE, order=1, input={}),),
            evidence=(),
            citations=(),
            gaps=(),
            attempts=(),
            artifacts=(),
            error=WorkflowError(WorkflowErrorCode.INTERNAL_ERROR, "boom", False),
        )
    # failed without error is rejected
    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.FAILED,
            plan=_plan(),
            stages=(),
            evidence=(),
            citations=(),
            gaps=(),
            attempts=(),
            artifacts=(),
        )


def test_typed_models_reject_legacy_fields_and_unsafe_records():
    # stage input cannot carry shell/output-path/provider fields
    with pytest.raises(WorkflowDomainError):
        WorkflowStage(
            id="a",
            operation="content_fetch",
            status=WorkflowStageStatus.COMPLETE,
            order=1,
            input={"resource": "https://x", "command": "smart-search fetch"},
        )
    with pytest.raises(WorkflowDomainError):
        WorkflowStage(
            id="a",
            operation="content_fetch",
            status=WorkflowStageStatus.COMPLETE,
            order=1,
            input={"resource": "https://x", "provider_id": "tavily"},
        )
    # unsafe artifact names
    for bad in (
        "/etc/passwd",
        "C:/tmp/evidence.md",
        "../escape.md",
        "a/../b.md",
        "https://user:pass@host/x.md",
        "a\\b.md",
        "",
        "a b.md",
    ):
        with pytest.raises(WorkflowDomainError):
            WorkflowArtifact(
                id="a1",
                stage_id="stage-1",
                kind="evidence",
                status=ArtifactStatus.WRITTEN,
                name=bad,
                media_type="text/markdown",
                byte_length=1,
                digest="a" * 64,
            )
    # bad digest / media type / byte length
    with pytest.raises(WorkflowDomainError):
        WorkflowArtifact(
            id="a1", stage_id="s1", kind="evidence", status="written",
            name="01.md", media_type="text/markdown", byte_length=1, digest="not-hex",
        )
    with pytest.raises(WorkflowDomainError):
        WorkflowArtifact(
            id="a1", stage_id="s1", kind="evidence", status="written",
            name="01.md", media_type="../evil", byte_length=1, digest="a" * 64,
        )
    with pytest.raises(WorkflowDomainError):
        WorkflowArtifact(
            id="a1", stage_id="s1", kind="evidence", status="written",
            name="01.md", media_type="text/markdown", byte_length=-1, digest="a" * 64,
        )
    # plan-level shell fields are rejected by the research plan model
    with pytest.raises(ResearchPlanError):
        _op("a", "source_discovery", input={"query": "q", "output_path": "/tmp/x"})


def test_workflow_request_validation():
    plan = _plan(_op("a"))
    with pytest.raises(WorkflowDomainError):
        WorkflowRequest(query="  ", plan=plan)
    with pytest.raises(WorkflowDomainError):
        WorkflowRequest(query="q", plan=plan, max_fetch_concurrency=0)
    with pytest.raises(WorkflowDomainError):
        WorkflowRequest(query="q", plan=plan, artifact_sink="not-callable")  # type: ignore[arg-type]
    request = WorkflowRequest(query="q", plan=plan, request_id="req-1")
    assert request.query == "q"
    assert request.request_id == "req-1"


def test_url_dedupe_key_normalization_and_sensitivity():
    assert workflow_url_dedupe_key("https://Example.com/docs/page") == "https://example.com/docs/page"
    assert workflow_url_dedupe_key("https://example.com/docs/page/") == "https://example.com/docs/page"
    assert workflow_url_dedupe_key("https://example.com:443/a") == "https://example.com/a"
    assert workflow_url_dedupe_key("http://example.com:80/a") == "http://example.com/a"
    # fragments are dropped
    assert workflow_url_dedupe_key("https://example.com/a#frag") == "https://example.com/a"
    # sensitive URLs fall back to their exact raw string
    assert workflow_url_dedupe_key("https://user:pass@example.com/a") == "https://user:pass@example.com/a"
    assert workflow_url_dedupe_key("https://example.com/a?token=abc") == "https://example.com/a?token=abc"


# ---------------------------------------------------------------------------
# Owner behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_stage_order_dependencies_and_artifacts(monkeypatch):
    plan = _plan(
        _op("discover-primary", "source_discovery"),
        _op(
            "fetch-selected",
            "content_fetch",
            input={"candidate_refs": ["discover-primary"]},
            depends_on=("discover-primary",),
        ),
    )
    candidate = _candidate(1, "https://example.com/page")
    evidence_item = _evidence(1, "https://example.com/page")

    async def fake_source(request):
        return _discovery_outcome(candidates=(candidate,), attempts=(_ok_attempt("source_discovery", "tavily"),))

    async def fake_fetch(request):
        return _fetch_outcome(items=(evidence_item,), attempts=(_ok_attempt("content_fetch", "jina"),))

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan, request_id="req-1"))
    assert outcome.status is WorkflowStatus.COMPLETE
    assert [stage.id for stage in outcome.stages] == ["discover-primary", "fetch-selected"]
    assert [stage.order for stage in outcome.stages] == [1, 2]
    assert outcome.stages[1].depends_on == ("discover-primary",)
    assert outcome.stages[1].operation == "content_fetch"
    assert outcome.stages[0].result_count == 1
    assert outcome.stages[0].evidence_ids == ()
    assert outcome.stages[1].evidence_ids == ("evidence-1",)
    assert [item.resource for item in outcome.evidence] == ["https://example.com/page"]
    assert outcome.citations[0].evidence_id == "evidence-1"
    assert [stage.evidence_ids for stage in outcome.stages] == [(), ("evidence-1",)]
    # one artifact per admitted evidence item, metadata only
    assert len(outcome.artifacts) == 1
    artifact = outcome.artifacts[0]
    assert artifact.stage_id == "fetch-selected"
    assert artifact.kind == "evidence"
    assert artifact.status is ArtifactStatus.WRITTEN
    assert artifact.media_type == "text/markdown"
    assert artifact.byte_length == len(evidence_item.content.encode("utf-8"))
    assert re.fullmatch(r"[a-f0-9]{64}", artifact.digest)
    assert artifact.id in outcome.stages[1].artifact_ids
    # no legacy answer/shell surface on the typed outcome
    assert not hasattr(outcome, "final_answer")
    assert not hasattr(outcome, "content")
    assert outcome.meta.request_id == "req-1"
    assert outcome.meta.duration_ms >= 0


@pytest.mark.asyncio
async def test_owner_projects_execution_taxonomy_attempts_to_workflow_vocabulary(monkeypatch):
    """Real Evidence owners record attempts under the execution capability
    taxonomy (web_search/docs_search/web_fetch/site_map); the owner must
    re-bind them to the workflow operation vocabulary so the strict contract
    round-trip never rejects a real run."""
    from smart_search.research_workflow_contract import serialize_workflow

    plan = _plan(
        _op("discover-primary", "source_discovery"),
        _op("fetch-selected", "content_fetch", input={"resource": "https://example.com/page"}),
    )
    candidate = _candidate(1, "https://example.com/page")
    evidence_item = _evidence(1, "https://example.com/page")

    async def fake_source(request):
        return _discovery_outcome(
            candidates=(candidate,),
            attempts=(_ok_attempt("web_search", "tavily"),),
        )

    async def fake_fetch(request):
        return _fetch_outcome(
            items=(evidence_item,),
            attempts=(_ok_attempt("web_fetch", "jina"),),
        )

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan, request_id="req-1"))
    assert outcome.status is WorkflowStatus.COMPLETE
    assert [attempt.capability for attempt in outcome.attempts] == [
        "source_discovery",
        "content_fetch",
    ]
    # the strict serializer round-trip must accept the projected attempts
    payload = serialize_workflow(outcome)
    assert [attempt["capability"] for attempt in payload["attempts"]] == [
        "source_discovery",
        "content_fetch",
    ]


@pytest.mark.asyncio
async def test_owner_bounded_fetch_concurrency(monkeypatch):
    plan = _plan(
        *[
            _op(f"fetch-{index}", "content_fetch", input={"resource": f"https://example.com/page-{index}"})
            for index in range(5)
        ]
    )

    class Tracker:
        def __init__(self) -> None:
            self.current = 0
            self.max_concurrent = 0
            self.calls = 0

        async def __call__(self, request):
            self.calls += 1
            call_no = self.calls
            self.current += 1
            self.max_concurrent = max(self.max_concurrent, self.current)
            await asyncio.sleep(0.02)
            self.current -= 1
            return _fetch_outcome(
                items=(_evidence(f"c{call_no}", request.resource),),
                attempts=(_ok_attempt("content_fetch", "jina"),),
            )

    tracker = Tracker()
    monkeypatch.setattr(evidence_operations, "content_fetch", tracker)

    outcome = await _run(WorkflowRequest(query="q", plan=plan, max_fetch_concurrency=2))
    assert tracker.calls == 5
    assert tracker.max_concurrent <= 2
    assert outcome.status is WorkflowStatus.COMPLETE
    assert len(outcome.evidence) == 5


@pytest.mark.asyncio
async def test_owner_url_dedupe_fetches_once(monkeypatch):
    plan = _plan(
        _op("fetch-a", "content_fetch", input={"resource": "https://Example.com/docs/page"}),
        _op("fetch-b", "content_fetch", input={"resource": "https://example.com/docs/page/"}),
    )
    calls: list[str] = []

    async def fake_fetch(request):
        calls.append(request.resource)
        return _fetch_outcome(items=(_evidence(len(calls), request.resource),))

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    # the second normalized-identical URL is deduplicated
    assert len(calls) == 1
    assert outcome.status is WorkflowStatus.COMPLETE
    assert len(outcome.evidence) == 1
    # the duplicate stage completes empty rather than failing the workflow
    assert [stage.status for stage in outcome.stages] == [
        WorkflowStageStatus.COMPLETE,
        WorkflowStageStatus.COMPLETE,
    ]
    assert outcome.stages[1].result_count == 0


@pytest.mark.asyncio
async def test_owner_fetch_honors_max_items(monkeypatch):
    # a fetch stage with constraints.max_items > 1 fetches up to that many
    # candidate URLs from the referenced discovery stage, in candidate order
    plan = _plan(
        _op("discover", "source_discovery"),
        _op(
            "fetch-selected",
            "content_fetch",
            input={"candidate_refs": ["discover"]},
            constraints={"max_items": 3},
            depends_on=("discover",),
        ),
    )
    candidates = tuple(
        _candidate(index, f"https://example.com/page-{index}") for index in range(1, 6)
    )

    async def fake_source(request):
        return _discovery_outcome(
            candidates=candidates,
            attempts=(_ok_attempt("source_discovery", "tavily"),),
        )

    calls: list[str] = []

    async def fake_fetch(request):
        calls.append(request.resource)
        return _fetch_outcome(items=(_evidence(len(calls), request.resource),))

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    # only the first max_items candidates are selected and fetched
    assert calls == [f"https://example.com/page-{index}" for index in range(1, 4)]
    assert outcome.status is WorkflowStatus.COMPLETE
    assert len(outcome.evidence) == 3
    fetch_stage = outcome.stages[1]
    assert fetch_stage.status is WorkflowStageStatus.COMPLETE
    assert fetch_stage.result_count == 3
    assert len(fetch_stage.evidence_ids) == 3
    assert len(fetch_stage.artifact_ids) == 3


@pytest.mark.asyncio
async def test_owner_fetch_max_items_zero_or_invalid_falls_back_to_default(monkeypatch):
    # non-positive/non-int max_items follows the shared constraint helper
    # semantics: the default of one candidate is used, matching the legacy
    # single-fetch behavior instead of silently ignoring the constraint
    candidates = tuple(
        _candidate(index, f"https://example.com/page-{index}") for index in range(1, 4)
    )

    async def fake_source(request):
        return _discovery_outcome(
            candidates=candidates,
            attempts=(_ok_attempt("source_discovery", "tavily"),),
        )

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    for bad_constraints in ({"max_items": 0}, {"max_items": -2}, {"max_items": "three"}):
        plan = _plan(
            _op("discover", "source_discovery"),
            _op(
                "fetch-selected",
                "content_fetch",
                input={"candidate_refs": ["discover"]},
                constraints=bad_constraints,
                depends_on=("discover",),
            ),
        )
        calls: list[str] = []

        async def fake_fetch(request):
            calls.append(request.resource)
            return _fetch_outcome(items=(_evidence(len(calls), request.resource),))

        monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
        outcome = await _run(WorkflowRequest(query="q", plan=plan))
        assert calls == ["https://example.com/page-1"]
        assert len(outcome.evidence) == 1
        assert outcome.stages[1].result_count == 1


@pytest.mark.asyncio
async def test_owner_fetch_max_items_dedupes_normalized_urls(monkeypatch):
    # candidate URLs deduplicate on their normalized key across referenced
    # discovery stages before the max_items cap is applied, and the first
    # normalized key owns the fetch
    plan = _plan(
        _op("discover-a", "source_discovery", input={"query": "a"}),
        _op("discover-b", "source_discovery", input={"query": "b"}, depends_on=("discover-a",)),
        _op(
            "fetch-selected",
            "content_fetch",
            input={"candidate_refs": ["discover-a", "discover-b"]},
            constraints={"max_items": 3},
            depends_on=("discover-a", "discover-b"),
        ),
    )
    candidates_a = (
        _candidate(1, "https://Example.com/docs/page"),
        _candidate(2, "https://example.com/docs/page/"),  # same normalized URL
        _candidate(3, "https://example.com/other"),
    )
    candidates_b = (
        _candidate(4, "https://example.com/other"),  # cross-stage duplicate
        _candidate(5, "https://example.com/third"),
        _candidate(6, "https://example.com/fourth"),
    )

    async def fake_source(request):
        selected = candidates_a if request.query == "a" else candidates_b
        return _discovery_outcome(
            candidates=selected,
            attempts=(_ok_attempt("source_discovery", "tavily"),),
        )

    calls: list[str] = []

    async def fake_fetch(request):
        calls.append(request.resource)
        return _fetch_outcome(items=(_evidence(len(calls), request.resource),))

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    # normalized duplicates drop out; the raw first-seen resource string is fetched
    assert calls == [
        "https://Example.com/docs/page",
        "https://example.com/other",
        "https://example.com/third",
    ]
    assert outcome.status is WorkflowStatus.COMPLETE
    assert len(outcome.evidence) == 3


@pytest.mark.asyncio
async def test_owner_fetch_max_items_bounded_concurrency(monkeypatch):
    # a single fetch stage with max_items > 1 keeps fetch concurrency bounded
    # by the workflow semaphore, never above max_fetch_concurrency
    plan = _plan(
        _op("discover", "source_discovery"),
        _op(
            "fetch-selected",
            "content_fetch",
            input={"candidate_refs": ["discover"]},
            constraints={"max_items": 4},
            depends_on=("discover",),
        ),
    )
    candidates = tuple(
        _candidate(index, f"https://example.com/page-{index}") for index in range(1, 5)
    )

    async def fake_source(request):
        return _discovery_outcome(
            candidates=candidates,
            attempts=(_ok_attempt("source_discovery", "tavily"),),
        )

    class Tracker:
        def __init__(self) -> None:
            self.current = 0
            self.max_concurrent = 0
            self.calls = 0

        async def __call__(self, request):
            self.calls += 1
            call_no = self.calls
            self.current += 1
            self.max_concurrent = max(self.max_concurrent, self.current)
            await asyncio.sleep(0.02)
            self.current -= 1
            return _fetch_outcome(
                items=(_evidence(f"c{call_no}", request.resource),),
                attempts=(_ok_attempt("content_fetch", "jina"),),
            )

    tracker = Tracker()
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", tracker)

    outcome = await _run(WorkflowRequest(query="q", plan=plan, max_fetch_concurrency=2))
    assert tracker.calls == 4
    assert tracker.max_concurrent <= 2
    assert outcome.status is WorkflowStatus.COMPLETE
    assert len(outcome.evidence) == 4


@pytest.mark.asyncio
async def test_owner_fetch_max_items_mixed_failure_degrades(monkeypatch):
    # one failed fetch inside a multi-URL stage keeps the stage failed with
    # the classified error while admitted evidence from the other URLs keeps
    # the workflow degraded (never complete)
    plan = _plan(
        _op("discover", "source_discovery"),
        _op(
            "fetch-selected",
            "content_fetch",
            input={"candidate_refs": ["discover"]},
            constraints={"max_items": 3},
            depends_on=("discover",),
        ),
    )
    candidates = tuple(
        _candidate(index, f"https://example.com/page-{index}") for index in range(1, 4)
    )

    async def fake_source(request):
        return _discovery_outcome(
            candidates=candidates,
            attempts=(_ok_attempt("source_discovery", "tavily"),),
        )

    calls: list[str] = []

    async def mixed_fetch(request):
        calls.append(request.resource)
        if request.resource.endswith("/page-2"):
            return _fetch_outcome(
                status=EvidenceOperationStatus.FAILED,
                attempts=(
                    ExecutionAttempt(
                        capability="content_fetch",
                        provider="jina",
                        status=ExecutionAttemptStatus.ERROR,
                        error=ExecutionError("fetch_error", "timeout", False),
                        elapsed_ms=1.0,
                    ),
                ),
                error=ExecutionError("fetch_error", "timeout", False),
            )
        return _fetch_outcome(items=(_evidence(len(calls), request.resource),))

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", mixed_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    assert calls == [f"https://example.com/page-{index}" for index in range(1, 4)]
    assert outcome.status is WorkflowStatus.DEGRADED
    assert outcome.error is None
    assert len(outcome.evidence) == 2
    fetch_stage = outcome.stages[1]
    assert fetch_stage.status is WorkflowStageStatus.FAILED
    assert fetch_stage.error is not None
    assert fetch_stage.error.code is WorkflowErrorCode.FETCH_FAILED
    assert any(gap.code == "stage_failed" for gap in outcome.gaps)
    assert fetch_stage.result_count == 2


@pytest.mark.asyncio
async def test_owner_terminal_preserves_classified_stage_error(monkeypatch):
    # with no admitted evidence and a failed stage, the terminal error keeps
    # the classified stage error identity/exit mapping: config_error stays
    # CONFIGURATION_ERROR (exit 3) instead of being flattened to FETCH_FAILED
    plan = _plan(_op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}))

    async def config_failing_fetch(request):
        return _fetch_outcome(
            status=EvidenceOperationStatus.FAILED,
            attempts=(
                ExecutionAttempt(
                    capability="content_fetch",
                    provider="jina",
                    status=ExecutionAttemptStatus.ERROR,
                    error=ExecutionError("config_error", "no qualified provider configured", False),
                    elapsed_ms=1.0,
                ),
            ),
            error=ExecutionError("config_error", "no qualified provider configured", False),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", config_failing_fetch)
    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    assert outcome.status is WorkflowStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code is WorkflowErrorCode.CONFIGURATION_ERROR
    assert WORKFLOW_ERROR_EXIT_CODES[outcome.error.code] == 3
    assert outcome.error.message == "no qualified provider configured"

    # the existing fetch_error classification keeps its upstream exit mapping
    async def fetch_error_fetch(request):
        return _fetch_outcome(
            status=EvidenceOperationStatus.FAILED,
            attempts=(
                ExecutionAttempt(
                    capability="content_fetch",
                    provider="jina",
                    status=ExecutionAttemptStatus.ERROR,
                    error=ExecutionError("fetch_error", "connection refused", False),
                    elapsed_ms=1.0,
                ),
            ),
            error=ExecutionError("fetch_error", "connection refused", False),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", fetch_error_fetch)
    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    assert outcome.status is WorkflowStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code is WorkflowErrorCode.FETCH_FAILED
    assert WORKFLOW_ERROR_EXIT_CODES[outcome.error.code] == 4


@pytest.mark.asyncio
async def test_owner_cancellation_isolates_stage(monkeypatch):
    plan = _plan(
        _op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}),
        _op("fetch-b", "content_fetch", input={"resource": "https://example.com/b"}),
    )

    async def fake_fetch(request):
        if request.resource.endswith("/a"):
            raise asyncio.CancelledError()
        return _fetch_outcome(items=(_evidence(2, request.resource),))

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    assert outcome.status is WorkflowStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code is WorkflowErrorCode.CANCELLED
    by_id = {stage.id: stage for stage in outcome.stages}
    assert by_id["fetch-a"].status is WorkflowStageStatus.CANCELLED
    assert by_id["fetch-b"].status is WorkflowStageStatus.COMPLETE


@pytest.mark.asyncio
async def test_owner_candidates_never_become_citations_or_evidence(monkeypatch):
    plan = _plan(
        _op("discover", "source_discovery"),
        _op("fetch", "content_fetch", input={"candidate_refs": ["discover"]}, depends_on=("discover",)),
    )
    candidate = _candidate(1, "https://example.com/candidate-page")
    evidence_item = _evidence(1, "https://example.com/fetched-page")

    async def fake_source(request):
        return _discovery_outcome(candidates=(candidate,), attempts=(_ok_attempt("source_discovery", "tavily"),))

    async def fake_fetch(request):
        return _fetch_outcome(items=(evidence_item,), attempts=(_ok_attempt("content_fetch", "jina"),))

    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    assert [item.id for item in outcome.evidence] == ["evidence-1"]
    assert [item.resource for item in outcome.evidence] == ["https://example.com/fetched-page"]
    # candidates are discovery data only; citations reference admitted evidence
    assert [citation.evidence_id for citation in outcome.citations] == ["evidence-1"]
    assert "candidate-page" not in " ".join(citation.label for citation in outcome.citations)


@pytest.mark.asyncio
async def test_owner_artifact_partial_write_gap_and_degraded(monkeypatch):
    plan = _plan(_op("fetch", "content_fetch", input={"resource": "https://example.com/a"}))

    def partial_sink(data):
        return ArtifactWriteResult(ArtifactStatus.PARTIAL, "disk full")

    async def fake_fetch(request):
        return _fetch_outcome(items=(_evidence(1, request.resource),), attempts=(_ok_attempt("content_fetch", "jina"),))

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    outcome = await _run(WorkflowRequest(query="q", plan=plan, artifact_sink=partial_sink))
    assert outcome.status is WorkflowStatus.DEGRADED
    assert outcome.error is None
    assert outcome.artifacts[0].status is ArtifactStatus.PARTIAL
    gap_codes = [gap.code for gap in outcome.gaps]
    assert "artifact_write_failed" in gap_codes
    assert outcome.stages[0].status is WorkflowStageStatus.COMPLETE


@pytest.mark.asyncio
async def test_owner_terminal_status_matrix(monkeypatch):
    # all stages succeed -> complete
    plan_ok = _plan(_op("fetch", "content_fetch", input={"resource": "https://example.com/a"}))

    async def ok_fetch(request):
        return _fetch_outcome(items=(_evidence(1, request.resource),))

    monkeypatch.setattr(evidence_operations, "content_fetch", ok_fetch)
    outcome = await _run(WorkflowRequest(query="q", plan=plan_ok))
    assert outcome.status is WorkflowStatus.COMPLETE

    # fetch failure with no evidence -> failed FETCH_FAILED
    async def failing_fetch(request):
        return _fetch_outcome(
            status=EvidenceOperationStatus.FAILED,
            attempts=(ExecutionAttempt(
                capability="content_fetch", provider="jina",
                status=ExecutionAttemptStatus.ERROR,
                error=ExecutionError("fetch_error", "connection refused", False),
                elapsed_ms=1.0,
            ),),
            error=ExecutionError("fetch_error", "connection refused", False),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", failing_fetch)
    outcome = await _run(WorkflowRequest(query="q", plan=plan_ok))
    assert outcome.status is WorkflowStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code is WorkflowErrorCode.FETCH_FAILED
    assert any(gap.code == "stage_failed" for gap in outcome.gaps)

    # empty discovery with no fetch work -> failed INSUFFICIENT_EVIDENCE
    plan_discovery = _plan(_op("discover", "source_discovery"))

    async def empty_source(request):
        return _discovery_outcome(candidates=(), attempts=(_ok_attempt("source_discovery", "tavily"),))

    monkeypatch.setattr(evidence_operations, "source_discovery", empty_source)
    outcome = await _run(WorkflowRequest(query="q", plan=plan_discovery))
    assert outcome.status is WorkflowStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code is WorkflowErrorCode.INSUFFICIENT_EVIDENCE

    # one failed stage plus admitted evidence -> degraded
    plan_mixed = _plan(
        _op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}),
        _op("fetch-b", "content_fetch", input={"resource": "https://example.com/b"}),
    )

    async def mixed_fetch(request):
        if request.resource.endswith("/b"):
            return _fetch_outcome(
                status=EvidenceOperationStatus.FAILED,
                attempts=(ExecutionAttempt(
                    capability="content_fetch", provider="jina",
                    status=ExecutionAttemptStatus.ERROR,
                    error=ExecutionError("fetch_error", "timeout", False),
                    elapsed_ms=1.0,
                ),),
                error=ExecutionError("fetch_error", "timeout", False),
            )
        return _fetch_outcome(items=(_evidence(1, request.resource),))

    monkeypatch.setattr(evidence_operations, "content_fetch", mixed_fetch)
    outcome = await _run(WorkflowRequest(query="q", plan=plan_mixed))
    assert outcome.status is WorkflowStatus.DEGRADED
    assert outcome.error is None
    assert len(outcome.evidence) == 1


@pytest.mark.asyncio
async def test_owner_placeholder_resource_is_a_noop(monkeypatch):
    plan = _plan(_op("fetch", "content_fetch", input={"resource": "<key-url>"}))
    calls: list[str] = []

    async def fake_fetch(request):
        calls.append(request.resource)
        return _fetch_outcome(items=(_evidence(1, request.resource),))

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
    outcome = await _run(WorkflowRequest(query="q", plan=plan))
    assert calls == []
    assert outcome.status is WorkflowStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code is WorkflowErrorCode.INSUFFICIENT_EVIDENCE
    assert outcome.stages[0].status is WorkflowStageStatus.COMPLETE


def test_owner_forbidden_imports():
    """The workflow domain must not import V1/service/Provider-CLI/V2/V3
    contracts, the legacy research service, or the operation runtime."""
    source = OWNER_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    forbidden_prefixes = (
        "smart_search.v2_contract",
        "smart_search.control_plane_contract",
        "smart_search.research_workflow_contract",
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
        "smart_search.operation_runtime",
        "smart_search.runtime_cache",
    )
    for module in imported:
        assert not any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes
        ), f"forbidden import: {module}"
    # the owner composes the typed Evidence owners (lazily at run time)
    assert "evidence_operations" in imported
    assert "execution_primitives" in imported
    assert "research_plan" in imported