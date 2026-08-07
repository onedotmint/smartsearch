"""Focused CLI boundary tests for the strict research workflow route.

``research run QUERY --format json`` is the only canonical path that enters
the strict typed Research Workflow owner and its contract serializer. These
tests prove the one-JSON-document guarantee, the exact 14-field strict shape
with no legacy answer/synthesis/shell/path/raw fields, the workflow exit
mapping, pre-owner rejection of invalid options/input, and that the legacy
bare ``research`` and offline ``research plan`` paths are untouched.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_search import cli
from smart_search import evidence_operations
from smart_search.evidence_operations import (
    EvidenceOperationOutcome,
    EvidenceOperationStatus,
    EvidenceRouting,
)
from smart_search.execution_primitives import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionMetadata,
)
from smart_search.research_workflow_contract import (
    EXIT_CONFIGURATION,
    EXIT_DEGRADED,
    EXIT_UPSTREAM,
    WORKFLOW_TOP_LEVEL_FIELDS,
    WORKFLOW_COMMAND,
    WORKFLOW_OPERATION,
    WORKFLOW_SCHEMA_VERSION,
)

ROOT = Path(__file__).parents[1]

# Keys that must never appear anywhere in the strict workflow JSON. Note that
# ``content`` is a legitimate evidence-item field, so it is not listed here;
# top-level exactness (the 14-field check) forbids it as an answer alias.
_FORBIDDEN_KEYS_EVERYWHERE = frozenset(
    {
        "final_answer",
        "synthesis_error",
        "response_mode",
        "synthesis_enabled",
        "synthesis",
        "data",
        "routing",
        "output_path",
        "command_line",
        "shell",
    }
)


def _ok_attempt(capability: str, provider: str) -> ExecutionAttempt:
    return ExecutionAttempt(
        capability=capability,
        provider=provider,
        status=ExecutionAttemptStatus.OK,
        elapsed_ms=1.0,
        result_count=1,
    )


def _fetch_outcome(
    *,
    items=(),
    status=EvidenceOperationStatus.COMPLETE,
    error=None,
) -> EvidenceOperationOutcome:
    attempts = ()
    if error is not None:
        attempts = (
            ExecutionAttempt(
                capability="content_fetch",
                provider="jina",
                status=ExecutionAttemptStatus.ERROR,
                error=error,
                elapsed_ms=1.0,
            ),
        )
    return EvidenceOperationOutcome(
        operation="content_fetch",
        status=status,
        evidence_items=tuple(items),
        attempts=attempts,
        error=error,
        routing=EvidenceRouting(
            ("content_fetch",),
            ("content_fetch",) if attempts else (),
            "v2",
            ("test",),
        ),
        metadata=ExecutionMetadata("req-test", 1),
    )


def _source_outcome() -> EvidenceOperationOutcome:
    return EvidenceOperationOutcome(
        operation="source_discovery",
        status=EvidenceOperationStatus.COMPLETE,
        candidates=(),
        attempts=(),
        routing=EvidenceRouting(("source_discovery",), (), "v2", ("test",)),
        metadata=ExecutionMetadata("req-test", 1),
    )


def _evidence(index: int, resource: str) -> ExecutionEvidenceItem:
    return ExecutionEvidenceItem(
        id=f"evidence-{index}",
        resource=resource,
        provider="jina",
        title=f"page {index}",
        content=f"body of {resource}",
    )


@pytest.fixture
def mock_evidence_owners(monkeypatch):
    """Patch every typed Evidence owner the workflow may invoke with fakes."""
    calls: list[str] = []

    async def fake_fetch(request):
        calls.append(f"fetch:{request.resource}")
        return _fetch_outcome(items=(_evidence(1, request.resource),))

    async def fake_source(request):
        calls.append(f"source:{request.query}")
        return _source_outcome()

    async def fake_docs(request):
        calls.append(f"docs:{request.query}")
        return _source_outcome()

    async def fake_site(request):
        calls.append(f"site:{request.resource}")
        return _source_outcome()

    async def boom(*args, **kwargs):
        raise AssertionError("workflow owner must not be invoked on invalid input")

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "docs_discovery", fake_docs)
    monkeypatch.setattr(evidence_operations, "site_discovery", fake_site)
    return calls


def _assert_no_forbidden_keys(node: object) -> None:
    stack: list[object] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                assert key not in _FORBIDDEN_KEYS_EVERYWHERE, f"forbidden key {key!r}"
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def _assert_strict_error(payload: dict, *, message: str | None = None) -> None:
    assert sorted(payload) == sorted(WORKFLOW_TOP_LEVEL_FIELDS)
    assert payload["schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert payload["command"] == WORKFLOW_COMMAND
    assert payload["operation"] == WORKFLOW_OPERATION
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    if message is not None:
        assert message in payload["error"]["message"]
    assert payload["stages"] == [] and payload["evidence"] == []
    _assert_no_forbidden_keys(payload)


# ---------------------------------------------------------------------------
# Successful workflow JSON
# ---------------------------------------------------------------------------


def test_research_run_emits_one_strict_workflow_document(mock_evidence_owners, capsys):
    code = cli.main(
        ["research", "run", "https://example.com/about", "--format", "json"]
    )
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    # exactly one JSON document on stdout
    payload = json.loads(out)
    assert sorted(payload) == sorted(WORKFLOW_TOP_LEVEL_FIELDS)
    assert payload["schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert payload["command"] == WORKFLOW_COMMAND
    assert payload["operation"] == WORKFLOW_OPERATION
    assert payload["ok"] is True
    assert payload["status"] == "complete"
    assert payload["error"] is None
    assert payload["meta"]["request_id"].startswith("workflow-")
    assert payload["plan"]["schema_version"] == "research-plan-1"
    resources = [item["resource"] for item in payload["evidence"]]
    assert "https://example.com/about" in resources
    assert all(citation["evidence_id"] in {item["id"] for item in payload["evidence"]} for citation in payload["citations"])
    assert all(stage["operation"] in {"source_discovery", "docs_discovery", "site_discovery", "content_fetch"} for stage in payload["stages"])
    assert all(artifact["status"] == "written" for artifact in payload["artifacts"])
    assert all(artifact["name"] == artifact["name"].strip("/") and ".." not in artifact["name"] for artifact in payload["artifacts"])
    _assert_no_forbidden_keys(payload)
    # the strict route never touches the legacy service
    assert mock_evidence_owners[0].startswith("fetch:")


def test_research_run_presentation_formats_are_one_stdout_document(mock_evidence_owners, capsys):
    """Markdown/content select one human stdout document after validation."""
    code = cli.main(
        ["research", "run", "https://example.com/about", "--format", "markdown"]
    )
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.count("# Research Run") == 1
    assert "Status: COMPLETE" in out
    assert "## Evidence" in out
    assert '"schema_version"' not in out
    assert out.count("research.run") >= 1

    code = cli.main(
        ["research", "run", "https://example.com/about", "--format", "content"]
    )
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("research.run COMPLETE:")
    assert "evidence items" in out
    assert out.count("\n") == 1


def test_research_run_accepts_budget_and_profile(mock_evidence_owners, monkeypatch, capsys):
    captured: list[tuple[str, str]] = []

    def fake_plan(query, budget="deep", evidence_dir=""):
        captured.append((query, budget))
        from smart_search.research_plan import ResearchPlanOperation, build_research_plan

        return build_research_plan(
            [
                ResearchPlanOperation(
                    id="fetch-1",
                    operation="content_fetch",
                    input={"resource": "https://example.com/page"},
                    constraints={},
                    depends_on=(),
                )
            ]
        )

    import smart_search.research_service

    monkeypatch.setattr(
        smart_search.research_service,
        "build_research_workflow_plan",
        fake_plan,
    )

    assert cli.main(["research", "run", "topic", "--budget", "quick"]) == cli.EXIT_OK
    json.loads(capsys.readouterr().out)
    assert captured == [("topic", "quick")]

    assert cli.main(["research", "run", "topic", "--profile", "fast"]) == cli.EXIT_OK
    json.loads(capsys.readouterr().out)
    assert captured[-1] == ("topic", "quick")


# ---------------------------------------------------------------------------
# Invalid options/input fail before any owner work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["research", "run", "topic", "--synthesize", "--format", "json"], "answer synthesis"),
        (["research", "run", "topic", "--output", "out.json"], "output path"),
        (["research", "run", "topic", "--force"], "output path"),
        (["research", "run", "topic", "--evidence-dir", "/tmp/evidence"], "logical artifacts only"),
        (["research", "run", "topic", "--fallback", "off"], "provider fallback"),
        (["research", "run", "topic", "--prompt-dir", "prompts"], "prompt files"),
        (["research", "run", "topic", "--search-prompt-file", "s.md"], "prompt files"),
        (["research", "run", "topic", "--fetch-prompt-file", "f.md"], "prompt files"),
        (["research", "run", "topic", "--research-prompt-file", "r.md"], "prompt files"),
        (["--trace", "research", "run", "topic"], "trace"),
    ],
)
def test_research_run_invalid_options_fail_before_owner(
    monkeypatch, capsys, argv, message
):
    for name in (
        "content_fetch",
        "source_discovery",
        "docs_discovery",
        "site_discovery",
    ):

        async def boom(*args, _name=name, **kwargs):
            raise AssertionError(f"{_name} must not run on invalid input")

        monkeypatch.setattr(evidence_operations, name, boom)

    code = cli.main(argv)
    assert code == cli.EXIT_PARAMETER_ERROR
    payload = json.loads(capsys.readouterr().out)
    _assert_strict_error(payload, message=message)
    expected_argument = next(token for token in argv if token.startswith("--"))
    assert payload["error"]["details"] == {"argument": expected_argument}


@pytest.mark.parametrize(
    "argv",
    [
        ["research", "run", "", "--format", "json"],
        ["research", "run", "   ", "--format", "json"],
    ],
)
def test_research_run_blank_query_rejected(monkeypatch, capsys, argv):
    for name in (
        "content_fetch",
        "source_discovery",
        "docs_discovery",
        "site_discovery",
    ):

        async def boom(*args, _name=name, **kwargs):
            raise AssertionError(f"{_name} must not run on invalid input")

        monkeypatch.setattr(evidence_operations, name, boom)

    code = cli.main(argv)
    assert code == cli.EXIT_PARAMETER_ERROR
    payload = json.loads(capsys.readouterr().out)
    _assert_strict_error(payload, message="non-blank query")


# ---------------------------------------------------------------------------
# Exit mapping
# ---------------------------------------------------------------------------


def test_research_run_cancelled_exit(monkeypatch, capsys):
    async def cancelled_fetch(request):
        raise asyncio.CancelledError()

    async def fake_source(request):
        return _source_outcome()

    monkeypatch.setattr(evidence_operations, "content_fetch", cancelled_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)

    code = cli.main(["research", "run", "https://example.com/cancel"])
    assert code == EXIT_UPSTREAM
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "CANCELLED"
    assert any(stage["status"] == "cancelled" for stage in payload["stages"])


def test_research_run_config_error_exit(monkeypatch, capsys):
    # a config_error fetch failure with no admitted evidence keeps its
    # classified identity: CONFIGURATION_ERROR and exit 3, not FETCH_FAILED/4
    async def config_failed_fetch(request):
        return _fetch_outcome(
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError(
                "config_error", "No qualified content_fetch providers configured", False
            ),
        )

    async def fake_source(request):
        return _source_outcome()

    monkeypatch.setattr(evidence_operations, "content_fetch", config_failed_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)

    code = cli.main(["research", "run", "https://example.com/config-error"])
    assert code == EXIT_CONFIGURATION
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert payload["evidence"] == []
    assert any(gap["code"] == "stage_failed" for gap in payload["gaps"])
    _assert_no_forbidden_keys(payload)


def test_research_run_failed_exit(monkeypatch, capsys):
    async def failed_fetch(request):
        return _fetch_outcome(
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError("fetch_error", "connection refused", False),
        )

    async def fake_source(request):
        return _source_outcome()

    monkeypatch.setattr(evidence_operations, "content_fetch", failed_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)

    code = cli.main(["research", "run", "https://example.com/fail"])
    assert code == EXIT_UPSTREAM
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "FETCH_FAILED"
    assert payload["evidence"] == []
    assert any(gap["code"] == "stage_failed" for gap in payload["gaps"])


def test_research_run_degraded_exit_and_fail_on_degraded(monkeypatch, capsys):
    async def degraded_fetch(request):
        return _fetch_outcome(
            items=(_evidence(1, request.resource),),
            status=EvidenceOperationStatus.FAILED,
            error=ExecutionError("fetch_error", "timeout", False),
        )

    async def fake_source(request):
        return _source_outcome()

    monkeypatch.setattr(evidence_operations, "content_fetch", degraded_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)

    assert cli.main(["research", "run", "https://example.com/degraded"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["evidence"]

    assert cli.main(["--fail-on-degraded", "research", "run", "https://example.com/degraded"]) == EXIT_DEGRADED
    capsys.readouterr()


# ---------------------------------------------------------------------------
# Legacy surfaces stay unchanged
# ---------------------------------------------------------------------------


def test_legacy_bare_research_unchanged(monkeypatch, capsys):
    calls: list[tuple[str, str]] = []

    async def fake_research(query, budget="deep", evidence_dir="", fallback="auto"):
        calls.append((query, budget))
        return {
            "ok": True,
            "query_mode": "research",
            "content": "Evidence answer",
            "final_answer": "Evidence answer",
        }

    from smart_search import cli_research

    async def boom(*args, **kwargs):
        raise AssertionError("bare research must not enter the workflow route")

    monkeypatch.setattr(cli_research, "dispatch", boom)
    monkeypatch.setattr(cli.service, "research", fake_research)

    assert cli.main(["research", "topic", "--format", "json"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["query_mode"] == "research"
    assert payload["content"] == "Evidence answer"
    assert calls == [("topic", "deep")]


def test_research_plan_offline_behavior_unchanged(monkeypatch, capsys):
    from smart_search import cli_research

    async def boom(*args, **kwargs):
        raise AssertionError("research plan must not enter the workflow route")

    calls: list[tuple[str, str]] = []

    def fake_plan(query, budget="standard", evidence_dir=""):
        calls.append((query, budget))
        return {"ok": True, "mode": "deep_research", "query": query}

    monkeypatch.setattr(cli_research, "dispatch", boom)
    monkeypatch.setattr(cli.service, "build_deep_research_plan", fake_plan)

    assert cli.main(["research", "plan", "topic", "--budget", "quick"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "deep_research"
    assert calls == [("topic", "quick")]


# ---------------------------------------------------------------------------
# Import isolation for parser-error paths
# ---------------------------------------------------------------------------


def test_parser_error_never_imports_owner_provider_or_config(tmp_path):
    config_dir = tmp_path / "config"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["SMART_SEARCH_CONFIG_DIR"] = str(config_dir)
    script = """
import json
import sys
from smart_search.cli import main

code = main(["research", "run", "topic", "--synthesize", "--format", "json"])
assert code == 2, code
for name in (
    "smart_search.research_service",
    "smart_search.evidence_operations",
    "smart_search.operation_runtime",
    "smart_search.runtime_cache",
    "smart_search.capability_service",
    "smart_search.config",
    "smart_search.providers",
    "smart_search.service",
    "httpx",
):
    assert name not in sys.modules, name
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    _assert_strict_error(payload, message="answer synthesis")
    assert not config_dir.exists()