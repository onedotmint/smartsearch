"""Pure typed-family presentation tests.

Proves the presentation layer is a pure one-way transformation of validated
V2/V3/Research-Workflow payloads: readable complete/degraded/failed/empty
views, redaction preserved, Unicode/long content bounded, exactly one stdout
document per CLI invocation, strict ``--output``/``--force`` rejection, and no
business side effects (no provider/owner/config/cache/legacy-renderer use).
Markdown/content is explicitly non-machine-stable; JSON remains the direct
serializer result and is never produced by the presentation views.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_search import cli
from smart_search.presentation import (
    PRESENTATION_FORMATS,
    PresentationError,
    render_v2,
    render_v3,
    render_workflow,
)

ROOT = Path(__file__).parents[1]
PRESENTATION_MODULE_PATH = ROOT / "src" / "smart_search" / "presentation.py"


# ---------------------------------------------------------------------------
# Validated payload fixtures for all three contract families
# ---------------------------------------------------------------------------


def _v2_payload(
    status="complete",
    *,
    operation="source_discovery",
    result=None,
    candidates=(),
    items=(),
    citations=(),
    gaps=(),
    attempts=(),
    degradation=(),
    error=None,
    warnings=(),
):
    from smart_search.v2_contract import (
        V2Attempt,
        V2Candidate,
        V2Citation,
        V2Envelope,
        V2Evidence,
        V2EvidenceItem,
        V2Gap,
        V2Meta,
        V2Routing,
        V2Status,
        serialize_result,
    )

    envelope = V2Envelope(
        status=V2Status(status),
        command="search",
        operation=operation,
        result={"total": len(candidates) + len(items)} if result is None else result,
        evidence=V2Evidence(
            candidates=tuple(candidates),
            items=tuple(items),
            citations=tuple(citations),
            gaps=tuple(gaps),
        ),
        routing=V2Routing(
            (operation,) if operation else (),
            (operation,) if operation else (),
            "v2-test-1",
            ("test",),
        ),
        attempts=tuple(attempts),
        degradation=tuple(degradation),
        error=error,
        meta=V2Meta("presentation-test", 3, warnings=tuple(warnings)),
    )
    return serialize_result(envelope)


def _v2_complete_payload(unicode_content="", long_content=False):
    from smart_search.v2_contract import (
        V2Attempt,
        V2AttemptStatus,
        V2Candidate,
        V2Citation,
        V2EvidenceItem,
    )

    item = V2EvidenceItem(
        id="evidence-1",
        resource="https://example.com/page",
        provider="jina",
        title="Example page",
        content=unicode_content or "body with 内容 and 🚀",
    )
    result = None
    if long_content:
        result = {"total": 3, "items": [{"id": "evidence-1"}]}
    return _v2_payload(
        candidates=(
            V2Candidate("c1", "https://example.com/c1", "tavily", "First", "snippet one"),
            V2Candidate("c2", "https://example.com/c2", "tavily", "Second", "snippet two"),
        ),
        items=(item,),
        citations=(V2Citation("cit-1", "evidence-1", "Example page"),),
        attempts=(
            V2Attempt("source_discovery", "tavily", V2AttemptStatus.OK, None, 12, 2),
            V2Attempt("source_discovery", "jina", V2AttemptStatus.EMPTY, None, 5, 0),
        ),
        result=result,
        warnings=("first warning",),
    )


def _v2_degraded_payload():
    from smart_search.v2_contract import V2Attempt, V2AttemptStatus, V2Degradation

    return _v2_payload(
        status="degraded",
        candidates=(),
        attempts=(V2Attempt("source_discovery", "tavily", V2AttemptStatus.EMPTY, None, 4, 0),),
        degradation=(V2Degradation("empty", "source_discovery", "no usable results"),),
        warnings=("partial coverage",),
    )


def _v2_failed_payload():
    from smart_search.v2_contract import V2Error, V2ErrorCode

    return _v2_payload(
        status="failed",
        error=V2Error(
            V2ErrorCode.CONFIGURATION_ERROR,
            "missing provider configuration",
            False,
            {"provider": "tavily"},
        ),
    )


def _v2_empty_payload():
    return _v2_payload(result={"total": 0, "items": []})


def _v3_payload(
    status="complete",
    *,
    operation="config.list",
    result=None,
    warnings=(),
    error=None,
    network_attempted=False,
    targets=(),
    network_policy="none",
    network_scope="none",
):
    from smart_search.control_plane_contract import (
        V3Envelope,
        V3Error,
        V3Meta,
        V3Mutation,
        V3Network,
        V3SideEffects,
        V3Status,
        serialize_result,
    )

    envelope = V3Envelope(
        status=V3Status(status),
        command=operation.split(".")[0],
        operation=operation,
        result=result or {},
        network=V3Network(network_policy, network_scope, network_attempted, targets),
        side_effects=V3SideEffects(
            config=V3Mutation(read=True),
            filesystem=V3Mutation(),
            subprocess_started=False,
        ),
        error=error,
        meta=V3Meta(duration_ms=2, warnings=tuple(warnings)),
    )
    return serialize_result(envelope)


def _v3_complete_payload():
    return _v3_payload(
        operation="provider.catalog.list",
        result={
            "provider_count": 2,
            "providers": [
                {"provider": "tavily", "capabilities": ["source_discovery"], "tier": "search", "stability": "stable"},
                {"provider": "jina", "capabilities": ["content_fetch"], "tier": "fetch", "stability": "stable"},
            ],
        },
    )


def _v3_degraded_payload():
    return _v3_payload(
        status="degraded",
        operation="doctor.probe",
        result={
            "minimum_profile": "standard",
            "minimum_profile_ok": True,
            "checks": [
                {"name": "primary", "status": "ok", "response_time_ms": 10},
                {"name": "backup", "status": "timeout", "response_time_ms": 0, "message": "timed out"},
            ],
        },
        warnings=("backup route timed out",),
        network_attempted=True,
        targets=("openai-compatible", "xai-responses"),
        network_policy="explicit",
        network_scope="aggregate",
    )


def _v3_failed_payload():
    from smart_search.control_plane_contract import V3Error, V3ErrorCode

    return _v3_payload(
        status="failed",
        operation="provider.probe",
        result={"provider": "tavily", "configured": False, "eligible": False, "status": "not_configured"},
        error=V3Error(
            V3ErrorCode.CONFIGURATION_ERROR,
            "provider tavily is not configured",
            False,
            {},
        ),
        targets=("tavily",),
        network_policy="explicit",
        network_scope="single_provider",
    )


def _v3_empty_payload():
    return _v3_payload(operation="config.list", result={"config_file": "/tmp/none", "values": {}})


def _workflow_payload(
    status="complete",
    *,
    error=None,
    stage_status="complete",
    stage_error=None,
    artifact_status="written",
    warnings=(),
    unicode_content="",
):
    from smart_search.execution_primitives import (
        ExecutionAttempt,
        ExecutionAttemptStatus,
        ExecutionCitation,
        ExecutionEvidenceItem,
        ExecutionGap,
        ExecutionMetadata,
    )
    from smart_search.research_plan import (
        RESEARCH_PLAN_SCHEMA_VERSION,
        ResearchPlan,
        ResearchPlanOperation,
    )
    from smart_search.research_workflow import (
        ArtifactStatus,
        WorkflowArtifact,
        WorkflowError,
        WorkflowMeta,
        WorkflowOutcome,
        WorkflowStage,
        WorkflowStageStatus,
        WorkflowStatus,
    )
    from smart_search.research_workflow_contract import serialize_workflow

    plan = ResearchPlan(
        RESEARCH_PLAN_SCHEMA_VERSION,
        (
            ResearchPlanOperation(
                id="fetch-1",
                operation="content_fetch",
                input={"resource": "https://example.com/page"},
                constraints={},
            ),
        ),
    )
    item = ExecutionEvidenceItem(
        id="evidence-1",
        resource="https://example.com/page",
        provider="jina",
        title="Example page",
        content=unicode_content or "body of the page",
    )
    stages = (
        WorkflowStage(
            id="fetch-1",
            operation="content_fetch",
            status=WorkflowStageStatus(stage_status),
            order=1,
            input={"resource": "https://example.com/page"},
            result_count=1,
            evidence_ids=("evidence-1",),
            artifact_ids=("artifact-1",) if artifact_status else (),
            error=stage_error,
        ),
    )
    artifacts = ()
    if artifact_status:
        artifacts = (
            WorkflowArtifact(
                id="artifact-1",
                stage_id="fetch-1",
                kind="evidence",
                status=ArtifactStatus(artifact_status),
                name="evidence/example.com/page.txt",
                media_type="text/plain",
                byte_length=10,
                digest="a" * 64,
            ),
        )
    outcome = WorkflowOutcome(
        status=WorkflowStatus(status),
        plan=plan,
        stages=stages,
        evidence=(item,),
        citations=(ExecutionCitation("cit-1", "evidence-1", "Example page"),),
        gaps=(ExecutionGap("gap-1", "coverage gap", "content_fetch", "https://example.com/other"),),
        attempts=(
            ExecutionAttempt(
                capability="content_fetch",
                provider="jina",
                status=ExecutionAttemptStatus.OK,
                elapsed_ms=8.0,
                result_count=1,
            ),
        ),
        artifacts=artifacts,
        error=error,
        meta=WorkflowMeta("workflow-presentation", 4.0, warnings=tuple(warnings)),
    )
    return serialize_workflow(outcome)


def _workflow_complete_payload():
    return _workflow_payload()


def _workflow_degraded_payload():
    return _workflow_payload(
        status="degraded",
        stage_status="degraded",
        artifact_status="partial",
        warnings=("one stage degraded",),
    )


def _workflow_failed_payload():
    from smart_search.research_workflow import WorkflowError, WorkflowErrorCode

    return _workflow_payload(
        status="failed",
        stage_status="failed",
        stage_error=WorkflowError(
            WorkflowErrorCode.FETCH_FAILED, "fetch failed", False, {}
        ),
        artifact_status="",
        error=WorkflowError(WorkflowErrorCode.FETCH_FAILED, "fetch failed", False, {}),
    )


def _workflow_empty_payload():
    from smart_search.research_plan import (
        RESEARCH_PLAN_SCHEMA_VERSION,
        ResearchPlan,
    )
    from smart_search.research_workflow import WorkflowMeta, WorkflowOutcome, WorkflowStatus
    from smart_search.research_workflow_contract import serialize_workflow

    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=ResearchPlan(RESEARCH_PLAN_SCHEMA_VERSION, ()),
        stages=(),
        evidence=(),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=None,
        meta=WorkflowMeta("workflow-empty", 0.0),
    )
    return serialize_workflow(outcome)


# ---------------------------------------------------------------------------
# Readable complete / degraded / failed / empty views
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "family_render,payload",
    [
        (render_v2, _v2_complete_payload()),
        (render_v2, _v2_degraded_payload()),
        (render_v2, _v2_failed_payload()),
        (render_v2, _v2_empty_payload()),
        (render_v3, _v3_complete_payload()),
        (render_v3, _v3_degraded_payload()),
        (render_v3, _v3_failed_payload()),
        (render_v3, _v3_empty_payload()),
        (render_workflow, _workflow_complete_payload()),
        (render_workflow, _workflow_degraded_payload()),
        (render_workflow, _workflow_failed_payload()),
        (render_workflow, _workflow_empty_payload()),
    ],
)
def test_all_states_render_readable_markdown_and_content(family_render, payload):
    markdown = family_render(payload, "markdown")
    content = family_render(payload, "content")
    assert isinstance(markdown, str) and markdown.endswith("\n")
    assert isinstance(content, str) and content.endswith("\n")
    status = payload["status"]
    assert status.upper() in markdown
    # exactly one document: markdown has exactly one H1 title line
    assert sum(1 for line in markdown.splitlines() if line.startswith("# ")) == 1
    # markdown must not contain a raw JSON document
    assert '"schema_version"' not in markdown
    assert '"schema_version"' not in content


def test_v2_markdown_shows_evidence_citations_gaps_attempts_and_error():
    markdown = render_v2(_v2_complete_payload(), "markdown")
    assert "## Candidates" in markdown and "## Evidence" in markdown
    assert "## Citations" in markdown and "## Attempts" in markdown
    assert "## Warnings" in markdown
    assert "Example page" in markdown and "snippet one" in markdown

    failed = render_v2(_v2_failed_payload(), "markdown")
    assert "## Error" in failed
    assert "CONFIGURATION_ERROR" in failed
    assert "missing provider configuration" in failed

    degraded = render_v2(_v2_degraded_payload(), "markdown")
    assert "## Degradation" in degraded
    assert "## Warnings" in degraded


def test_v2_content_matches_evidence_body_and_failure_summary():
    complete = render_v2(_v2_complete_payload(), "content")
    assert "body with 内容 and 🚀" in complete
    assert complete.count("\n") >= 1

    failed = render_v2(_v2_failed_payload(), "content")
    assert failed.startswith("FAILED: search CONFIGURATION_ERROR")
    assert "missing provider configuration" in failed

    degraded = render_v2(_v2_degraded_payload(), "content")
    assert degraded.startswith("DEGRADED: search")
    assert "no usable results" in degraded


def test_v3_markdown_shows_operation_sections_network_and_side_effects():
    complete = render_v3(_v3_complete_payload(), "markdown")
    assert "# V3 Provider Catalog" in complete
    assert "## Network" in complete and "## Side Effects" in complete
    assert "tavily" in complete and "2" in complete

    degraded = render_v3(_v3_degraded_payload(), "markdown")
    assert "## Warnings" in degraded
    assert "backup route timed out" in degraded
    assert "xai-responses" in degraded

    failed = render_v3(_v3_failed_payload(), "markdown")
    assert "## Error" in failed
    assert "CONFIGURATION_ERROR" in failed
    assert "## Network" in failed


def test_v3_content_is_a_compact_summary():
    content = render_v3(_v3_complete_payload(), "content")
    assert content.startswith("provider.catalog.list COMPLETE:")
    assert "providers=2" in content
    assert "network_attempted=no" in content
    assert "config_read=yes" in content

    failed = render_v3(_v3_failed_payload(), "content")
    assert "provider.probe FAILED:" in failed
    assert "CONFIGURATION_ERROR" in failed


def test_workflow_markdown_shows_stages_evidence_artifacts_and_plan():
    complete = render_workflow(_workflow_complete_payload(), "markdown")
    assert "# Research Run" in complete
    assert "## Plan" in complete and "## Stages" in complete
    assert "## Evidence" in complete and "## Citations" in complete
    assert "## Gaps" in complete and "## Attempts" in complete
    assert "## Artifacts" in complete
    assert "evidence/example.com/page.txt" in complete

    degraded = render_workflow(_workflow_degraded_payload(), "markdown")
    assert "## Warnings" in degraded
    assert "one stage degraded" in degraded

    failed = render_workflow(_workflow_failed_payload(), "markdown")
    assert "## Error" in failed
    assert "FETCH_FAILED" in failed


def test_workflow_content_is_a_compact_summary():
    content = render_workflow(_workflow_complete_payload(), "content")
    assert content.startswith("research.run COMPLETE:")
    assert "1 stages" in content
    assert "1 evidence items" in content
    assert "1 citations" in content

    failed = render_workflow(_workflow_failed_payload(), "content")
    assert "research.run FAILED:" in failed
    assert "FETCH_FAILED" in failed


# ---------------------------------------------------------------------------
# Redaction, Unicode, long content, and format safety
# ---------------------------------------------------------------------------


def test_rendering_preserves_serializer_redaction():
    from smart_search.v2_contract import (
        V2Candidate,
        V2Evidence,
        V2Envelope,
        V2Meta,
        V2Routing,
        V2Status,
        serialize_result,
    )

    candidate = V2Candidate(
        "c1",
        "https://user:supersecret@example.com/x?api_key=abc123&ok=1",
        "tavily",
        "T",
        "look at https://alice:pass@example.net/y",
    )
    envelope = V2Envelope(
        V2Status.COMPLETE,
        "search",
        "source_discovery",
        {"total": 1, "items": [{"id": "c1"}]},
        V2Evidence(candidates=(candidate,)),
        V2Routing(("source_discovery",), ("source_discovery",), "v2", ("test",)),
        (),
        (),
        None,
        V2Meta("redact-test", 1),
    )
    payload = serialize_result(envelope)
    markdown = render_v2(payload, "markdown")
    content = render_v2(payload, "content")
    for rendered in (markdown, content):
        assert "supersecret" not in rendered
        assert "user:supersecret" not in rendered
        assert "alice:pass" not in rendered
        assert "abc123" not in rendered
        assert "example.com" in rendered or "example.net" in rendered
        # redaction markers survive into the human output
        assert "REDACTED" in rendered or "redacted" in rendered.lower()


def test_rendering_preserves_workflow_redaction():
    from smart_search.execution_primitives import (
        ExecutionEvidenceItem,
        ExecutionMetadata,
    )
    from smart_search.research_plan import (
        RESEARCH_PLAN_SCHEMA_VERSION,
        ResearchPlan,
        ResearchPlanOperation,
    )
    from smart_search.research_workflow import WorkflowMeta, WorkflowOutcome, WorkflowStatus
    from smart_search.research_workflow_contract import serialize_workflow

    item = ExecutionEvidenceItem(
        id="evidence-1",
        resource="https://user:hunter2@example.com/private",
        provider="jina",
        title="secret page",
        content="content with https://bob:pw@example.net/data",
    )
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=ResearchPlan(
            RESEARCH_PLAN_SCHEMA_VERSION,
            (
                ResearchPlanOperation(
                    id="fetch-1",
                    operation="content_fetch",
                    input={"resource": "https://user:hunter2@example.com/private"},
                    constraints={},
                ),
            ),
        ),
        stages=(),
        evidence=(item,),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=None,
        meta=WorkflowMeta("workflow-redact", 0.0),
    )
    payload = serialize_workflow(outcome)
    markdown = render_workflow(payload, "markdown")
    content = render_workflow(payload, "content")
    for rendered in (markdown, content):
        assert "hunter2" not in rendered
        assert "bob:pw" not in rendered
    assert "example.com" in markdown


def test_unicode_and_long_content_are_bounded_and_safe():
    long_body = ("第".join(["很长"] * 10000)) + "🚀" * 50
    long_body = long_body * 2  # ~100KB
    payload = _v2_complete_payload(unicode_content=long_body)
    markdown = render_v2(payload, "markdown")
    assert long_body in markdown
    content = render_v2(payload, "content")
    assert long_body in content

    workflow = _workflow_payload(unicode_content=long_body)
    workflow_markdown = render_workflow(workflow, "markdown")
    workflow_content = render_workflow(workflow, "content")
    # the workflow summary view lists evidence without embedding bodies; long
    # content must still render without throwing and stay bounded
    assert isinstance(workflow_markdown, str) and workflow_markdown.endswith("\n")
    assert isinstance(workflow_content, str) and workflow_content.endswith("\n")
    assert "Example page" in workflow_markdown


def test_narrow_terminal_encoding_fallback_is_safe(monkeypatch):
    """Unencodable characters are backslash-escaped, never raised."""
    captured: list[str] = []

    class _AsciiStdout:
        encoding = "ascii"
        errors = "strict"

        def write(self, text: str) -> int:
            captured.append(text)
            return len(text)

    monkeypatch.setattr(sys, "stdout", _AsciiStdout())
    payload = _v2_complete_payload(unicode_content="中文 🚀 emoji")
    rendered = render_v2(payload, "markdown")
    assert "中文" not in rendered and "\\u4e2d" in rendered or "\\u" in rendered
    rendered_content = render_v2(payload, "content")
    assert isinstance(rendered_content, str) and rendered_content.endswith("\n")
    render_v3(_v3_complete_payload(), "markdown")
    render_workflow(_workflow_complete_payload(), "content")


def test_json_format_is_not_a_presentation_format():
    for renderer, payload in (
        (render_v2, _v2_complete_payload()),
        (render_v3, _v3_complete_payload()),
        (render_workflow, _workflow_complete_payload()),
    ):
        with pytest.raises(PresentationError):
            renderer(payload, "json")
        with pytest.raises(PresentationError):
            renderer(payload, "xml")


def test_presentation_rejects_unvalidated_raw_dictionaries():
    # A raw Provider/service-shaped dict is never a valid typed envelope.
    with pytest.raises(PresentationError):
        render_v2({"ok": True, "results": [{"title": "raw"}]}, "markdown")
    with pytest.raises(PresentationError):
        render_v3({"ok": True, "status": "complete"}, "markdown")
    with pytest.raises(PresentationError):
        render_workflow({"ok": True, "final_answer": "raw"}, "markdown")


def test_presentation_payloads_are_never_mutated():
    payload = _v3_complete_payload()
    before = json.dumps(payload, sort_keys=True)
    render_v3(payload, "markdown")
    render_v3(payload, "content")
    assert json.dumps(payload, sort_keys=True) == before


# ---------------------------------------------------------------------------
# Prohibited side effects and dependency boundaries
# ---------------------------------------------------------------------------


def test_presentation_forbidden_imports_ast_gate():
    """The presentation module must never import providers, service, config,
    typed owners, the legacy renderer, or the typed CLI dispatchers."""
    source = PRESENTATION_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert imported, "expected imports to be recorded"
    forbidden_prefixes = (
        "smart_search.cli_render",
        "smart_search.cli",
        "smart_search.cli_v2",
        "smart_search.cli_v3",
        "smart_search.cli_research",
        "smart_search.cli_support",
        "smart_search.cli_dispatch",
        "smart_search.cli_parser",
        "smart_search.service",
        "smart_search.service_support",
        "smart_search.search_service",
        "smart_search.research_service",
        "smart_search.operations_service",
        "smart_search.api_v2",
        "smart_search.canonical_operations",
        "smart_search.evidence_operations",
        "smart_search.control_operations",
        "smart_search.control_plane_adapters",
        "smart_search.research_workflow",
        "smart_search.research_plan",
        "smart_search.config",
        "smart_search.runtime_cache",
        "smart_search.capability_service",
        "smart_search.capability_executor",
        "smart_search.skill_installer",
        "smart_search.providers",
        "httpx",
    )
    for name in imported:
        assert not name.startswith(forbidden_prefixes), f"forbidden import: {name}"
    # only the three pure contract validators are allowed as smart_search deps
    allowed = {
        "smart_search.v2_contract",
        "smart_search.control_plane_contract",
        "smart_search.research_workflow_contract",
    }
    assert all(name in allowed for name in imported if name.startswith("smart_search"))


def test_presentation_has_no_file_or_side_effect_calls():
    source = PRESENTATION_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
    forbidden_calls = {"open", "write", "remove", "unlink", "mkdir", "exec", "eval"}
    assert not (forbidden_calls & set(calls)), forbidden_calls & set(calls)


def test_presentation_import_isolation_fresh_process():
    """Importing presentation must not pull providers, service, config,
    owners, runtime cache, or the legacy renderer into the process."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    script = r"""
import sys
from smart_search.presentation import render_v2, render_v3, render_workflow
for name in (
    "smart_search.service",
    "smart_search.config",
    "smart_search.api_v2",
    "smart_search.evidence_operations",
    "smart_search.control_operations",
    "smart_search.research_service",
    "smart_search.operations_service",
    "smart_search.runtime_cache",
    "smart_search.capability_service",
    "smart_search.skill_installer",
    "smart_search.cli_render",
    "smart_search.cli_v2",
    "smart_search.cli_v3",
    "smart_search.cli_research",
    "httpx",
):
    assert name not in sys.modules, name
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_rendering_never_invokes_owners_or_business_code(monkeypatch):
    """Owner-once/prohibited-call proof: rendering is a pure string transform."""
    from smart_search import (
        api_v2,
        control_operations,
        control_executors,
        evidence_operations,
        research_service,
        runtime_cache,
    )

    def boom(*args, **kwargs):
        raise AssertionError("presentation must not call business code")

    for module in (
        api_v2,
        control_operations,
        control_executors,
        evidence_operations,
        research_service,
        runtime_cache,
    ):
        for name in dir(module):
            if name.startswith("_") or name in {"Path", "json", "os", "sys"}:
                continue
            monkeypatch.setattr(module, name, boom, raising=False)

    render_v2(_v2_complete_payload(), "markdown")
    render_v2(_v2_failed_payload(), "content")
    render_v3(_v3_complete_payload(), "markdown")
    render_v3(_v3_degraded_payload(), "content")
    render_workflow(_workflow_complete_payload(), "markdown")
    render_workflow(_workflow_failed_payload(), "content")


# ---------------------------------------------------------------------------
# CLI boundary: one stdout document, strict option rejection, JSON authority
# ---------------------------------------------------------------------------


def _run_main(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_v3_markdown_and_content_are_one_stdout_document(monkeypatch, tmp_path):
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlMutationFacts,
        ControlOperationOutcome,
        ControlOperationStatus,
        ControlSideEffectFacts,
    )
    from smart_search.execution_primitives import ExecutionMetadata

    async def fake_list():
        return ControlOperationOutcome(
            operation="config.list",
            status=ControlOperationStatus.COMPLETE,
            result={"config_file": str(tmp_path / "config.json"), "values": {"KEY": "value"}},
            side_effects=ControlSideEffectFacts(
                config=ControlMutationFacts(read=True)
            ),
            metadata=ExecutionMetadata("config.list", 0),
        )

    monkeypatch.setattr(control_operations, "run_config_list", fake_list)
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))

    code, out, err = _run_main(["config", "list", "--format", "markdown"])
    assert code == 0, err
    assert sum(1 for line in out.splitlines() if line.startswith("# V3 Config List")) == 1
    assert "Status: COMPLETE" in out
    assert out.count('"schema_version"') == 0
    assert out.count("Status:") == 1

    code, out, err = _run_main(["config", "list", "--format", "content"])
    assert code == 0, err
    assert out.startswith("config.list COMPLETE:")
    assert out.count("\n") == 1

    # JSON default stays the direct serializer document
    code, out, err = _run_main(["config", "list"])
    assert code == 0, err
    payload = json.loads(out)
    assert payload["operation"] == "config.list"
    # ``KEY`` is a sensitive name under the shared policy, so the serializer
    # emits the redacted value; the assertion still proves the JSON default
    # is the direct serializer document, not a presentation round trip.
    assert payload["result"]["values"] == {"KEY": "[REDACTED]"}
    assert out.count('"schema_version"') == 1


def test_v3_output_and_force_are_strictly_rejected(monkeypatch, tmp_path):
    from smart_search import control_operations

    async def boom(*args, **kwargs):
        raise AssertionError("owner must not run on invalid options")

    monkeypatch.setattr(control_operations, "run_config_list", boom)
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))

    for argv in (
        ["config", "list", "--output", "out.md"],
        ["config", "list", "--force"],
    ):
        code, out, err = _run_main(argv)
        assert code == 2, (argv, out, err)
        payload = json.loads(out)
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert payload["operation"] == "config.list"
        assert out.count('"schema_version"') == 1


def test_research_run_output_and_force_are_strictly_rejected(monkeypatch):
    from smart_search import evidence_operations

    async def boom(*args, **kwargs):
        raise AssertionError("owner must not run on invalid options")

    monkeypatch.setattr(evidence_operations, "content_fetch", boom)
    monkeypatch.setattr(evidence_operations, "source_discovery", boom)
    for argv in (
        ["research", "run", "topic", "--output", "out.json"],
        ["research", "run", "topic", "--force"],
    ):
        code, out, err = _run_main(argv)
        assert code == cli.EXIT_PARAMETER_ERROR
        payload = json.loads(out)
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert out.count('"schema_version": "research-workflow-1"') == 1


def test_v3_diagnose_default_stays_json_despite_parser_default(monkeypatch, capsys):
    """The typed family ignores any parser default and keeps JSON unless
    --format is explicit."""
    from smart_search import control_executors

    async def fake_diagnose(timeout_seconds=30):
        return {
            "ok": False, "provider": "openai-compatible", "checks": [],
            "missing": ["OPENAI_COMPATIBLE_API_KEY"], "error_type": "config_error",
            "error": "missing OPENAI_COMPATIBLE_API_KEY",
        }

    monkeypatch.setattr(control_executors, "_execute_diagnose_openai_compatible", fake_diagnose)
    code, out, err = _run_main(["dev", "diagnose", "openai-compatible"])
    assert code == 3
    payload = json.loads(out)
    assert payload["operation"] == "dev.diagnose.openai-compatible"
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"

    code, out, err = _run_main([
        "dev", "diagnose", "openai-compatible", "--format", "markdown",
    ])
    assert code == 3
    assert out.count("# V3 Diagnose OpenAI-Compatible") == 1
    assert "## Error" in out
    assert out.count('"schema_version"') == 0

    # Every V3 operation, including diagnose, accepts explicit content even
    # though the shared v1 parser historically listed only json|markdown.
    code, out, err = _run_main([
        "dev", "diagnose", "openai-compatible", "--format", "content",
    ])
    assert code == 3
    assert out.startswith("dev.diagnose.openai-compatible FAILED:")
    assert "CONFIGURATION_ERROR" in out
    assert '"schema_version"' not in out
    assert out.count("\n") == 1


def test_diagnose_parser_accepts_content_for_canonical_spelling():
    """The canonical ``dev diagnose openai-compatible`` leaf accepts
    json|markdown|content while the typed v3 route keeps JSON as its
    contract default. The legacy bare ``diagnose`` spelling is removed and
    fails with the v3 family error instead of parsing."""
    from smart_search.cli_parser import build_parser

    parser = build_parser(raise_on_error=False)
    args = parser.parse_args(["dev", "diagnose", "openai-compatible", "--format", "content"])
    assert args.format == "content"
    args = parser.parse_args(["dev", "diagnose", "openai-compatible"])
    assert args.format == "markdown"


def test_dev_regression_parser_accepts_typed_format_set():
    """dev regression must register the typed json|markdown|content format so
    the v3 route can select its presentation view. The shared default stays
    json and the top-level v1 regression parser stays format-free."""
    from smart_search.cli_parser import build_parser

    parser = build_parser(raise_on_error=False)
    for fmt in ("json", "markdown", "content"):
        args = parser.parse_args(["dev", "regression", "--format", fmt])
        assert args.format == fmt
    # Omitted format keeps the shared json default; the typed family reads
    # argv, so an omitted --format always yields the JSON contract document.
    args = parser.parse_args(["dev", "regression"])
    assert args.format == "json"
    # The top-level v1 regression command does not accept --format.
    with pytest.raises(SystemExit):
        parser.parse_args(["regression", "--format", "markdown"])


def test_v3_dev_regression_format_views_are_one_stdout_document(monkeypatch):
    """Explicit dev regression --format json|markdown|content selects exactly
    one stdout document after the validated envelope; markdown/content carry
    no JSON contract fields and the JSON form stays the direct serializer."""
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlOperationOutcome,
        ControlOperationStatus,
        ControlSideEffectFacts,
    )
    from smart_search.execution_primitives import ExecutionMetadata

    async def fake_regression():
        return ControlOperationOutcome(
            operation="dev.regression",
            status=ControlOperationStatus.COMPLETE,
            result={"exit_code": 0, "subprocess_started": True, "fallback": ""},
            side_effects=ControlSideEffectFacts(subprocess_started=True),
            metadata=ExecutionMetadata("dev.regression", 0),
        )

    monkeypatch.setattr(control_operations, "run_dev_regression", fake_regression)

    code, out, err = _run_main(["dev", "regression", "--format", "markdown"])
    assert code == 0, err
    assert out.count("# V3 Regression") == 1
    assert "Status: COMPLETE" in out
    assert out.count('"schema_version"') == 0

    code, out, err = _run_main(["dev", "regression", "--format", "content"])
    assert code == 0, err
    assert out.startswith("dev.regression COMPLETE:")
    assert out.count("\n") == 1
    assert '"schema_version"' not in out

    # Explicit json and omitted format both produce the serializer document.
    for argv in (
        ["dev", "regression"],
        ["dev", "regression", "--format", "json"],
    ):
        code, out, err = _run_main(argv)
        assert code == 0, err
        payload = json.loads(out)
        assert payload["operation"] == "dev.regression"
        assert payload["status"] == "complete"
        assert out.count('"schema_version"') == 1


def test_documentation_marks_typed_markdown_content_as_human_presentation():
    """READMEs, docs/commands.md, and both skill copies must no longer call
    typed Markdown/content invalid or JSON-only: JSON is the only stable
    machine contract and Markdown/content are non-stable human views."""
    files = [
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "docs" / "commands.md",
        ROOT / "skills" / "smart-search-cli" / "SKILL.md",
        ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli" / "SKILL.md",
        ROOT / "skills" / "smart-search-cli" / "references" / "cli-core.md",
        ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli" / "references" / "cli-core.md",
    ]
    texts = {path: path.read_text(encoding="utf-8") for path in files}
    stale_claims = (
        "JSON-only",
        "JSON only",
        "only JSON",
        "仅支持 JSON",
        "只接受根级全局入口和 JSON",
    )
    for path, text in texts.items():
        for claim in stale_claims:
            assert claim not in text, (
                f"{path.relative_to(ROOT)} still claims {claim!r}"
            )
    # JSON is documented as the only stable machine contract.
    assert "only stable machine contract" in texts[ROOT / "README.md"]
    assert "唯一稳定的机器契约" in texts[ROOT / "README.zh-CN.md"]
    assert "only stable machine contract" in texts[ROOT / "docs" / "commands.md"]
    assert "only stable machine contract" in texts[ROOT / "skills" / "smart-search-cli" / "SKILL.md"]
    # Markdown/content are documented as non-stable human presentation.
    assert "non-stable human" in texts[ROOT / "docs" / "commands.md"]
    assert "非稳定人类视图" in texts[ROOT / "README.zh-CN.md"]
    # Source and packaged skill copies stay byte-identical.
    assert texts[ROOT / "skills" / "smart-search-cli" / "SKILL.md"] == texts[
        ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli" / "SKILL.md"
    ]
    assert texts[ROOT / "skills" / "smart-search-cli" / "references" / "cli-core.md"] == texts[
        ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli" / "references" / "cli-core.md"
    ]


def test_v2_cli_json_output_is_exactly_the_serializer_document(monkeypatch):
    """The JSON document on stdout is byte-for-byte the family serializer
    result; the presentation views never touch the JSON path."""
    from smart_search import api_v2
    from smart_search.v2_contract import (
        V2Envelope,
        V2Evidence,
        V2Meta,
        V2Routing,
        V2Status,
        serialize_result,
    )

    envelope = V2Envelope(
        V2Status.COMPLETE,
        "search",
        "source_discovery",
        {"total": 0, "items": []},
        V2Evidence(),
        V2Routing(("source_discovery",), (), "v2", ("test",)),
        (),
        (),
        None,
        V2Meta("authority", 1),
    )

    async def fake_composite(query, max_results=5):
        return envelope

    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)
    code, out, err = _run_main(["search", "q", "--format", "json"])
    assert code == 0, err
    expected = json.dumps(serialize_result(envelope), ensure_ascii=False, indent=2) + "\n"
    assert out == expected
    # a human view of the same validated payload is not JSON
    assert out != render_v2(serialize_result(envelope), "markdown")
