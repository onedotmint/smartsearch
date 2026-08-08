import json
import io
import contextlib

import pytest

from smart_search import capability_service, cli, service
from smart_search.cli_contract import build_json_result
from smart_search.utils import PromptConfigurationError, get_prompt, prompt_overrides


def _run_main(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_json_contract_preserves_legacy_error_and_adds_structured_error():
    result = build_json_result(
        "fetch",
        {
            "ok": False,
            "error_type": "network_error",
            "error": "Bearer sk-test-secret upstream timeout",
            "OPENAI_COMPATIBLE_API_KEY": "sk-test-secret",
        },
        secrets=["sk-test-secret"],
    )

    assert result["schema_version"] == "1"
    assert result["command"] == "fetch"
    assert result["error"] == "Bearer [REDACTED] upstream timeout"
    assert result["error_code"] == "PROVIDER_UNAVAILABLE"
    assert result["error_detail"]["code"] == "PROVIDER_UNAVAILABLE"
    assert result["data"]["error"]["message"] == "Bearer [REDACTED] upstream timeout"
    assert "sk-test-secret" not in json.dumps(result)


def test_minimum_profile_modes_keep_standard_fail_closed():
    status = {
        "main_search": {"ok": False},
        "web_search": {"ok": True},
        "docs_search": {"ok": False},
        "web_fetch": {"ok": False},
        "site_map": {"ok": True},
    }

    standard = capability_service._minimum_profile_result("standard", status)
    lite = capability_service._minimum_profile_result("lite", status)
    full = capability_service._minimum_profile_result(
        "full",
        {
            **status,
            "main_search": {"ok": True},
            "docs_search": {"ok": True},
            "web_fetch": {"ok": True},
        },
    )
    lite_docs_only = capability_service._minimum_profile_result(
        "lite",
        {
            "main_search": {"ok": False},
            "web_search": {"ok": False},
            "docs_search": {"ok": True},
            "web_fetch": {"ok": False},
            "site_map": {"ok": False},
        },
    )

    assert standard["ok"] is False
    assert standard["missing_required"] == ["main_search", "docs_search", "web_fetch"]
    assert lite["ok"] is True
    assert lite["degraded"] is True
    assert lite_docs_only["ok"] is True
    assert full["ok"] is True


def test_prompt_override_loads_local_utf8_file_and_rejects_remote(tmp_path):
    prompt_file = tmp_path / "search.md"
    prompt_file.write_text("只返回来源候选。", encoding="utf-8")

    with prompt_overrides(search_prompt_file=str(prompt_file)):
        assert get_prompt("search") == "只返回来源候选。"

    with prompt_overrides(search_prompt_file="https://example.com/prompt.md"):
        with pytest.raises(PromptConfigurationError, match="local UTF-8"):
            get_prompt("search")


def test_research_profile_maps_to_executor_budget(monkeypatch, capsys):
    captured: list[str] = []

    def fake_plan(query, budget="deep", evidence_dir=""):
        captured.append(budget)
        from smart_search.research_plan import ResearchPlanOperation, build_research_plan

        return build_research_plan(
            [
                ResearchPlanOperation(
                    id="fetch-1", operation="content_fetch",
                    input={"resource": "https://example.com/page"},
                    constraints={}, depends_on=(),
                )
            ]
        )

    import smart_search.research_service
    import smart_search.evidence_operations as evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import (
        ExecutionAttempt,
        ExecutionAttemptStatus,
        ExecutionEvidenceItem,
        ExecutionMetadata,
    )

    async def fake_fetch(request):
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(
                ExecutionEvidenceItem(
                    id="evidence-1", resource=request.resource, provider="jina",
                    title="page", content="body",
                ),
            ),
            attempts=(
                ExecutionAttempt(
                    capability="content_fetch", provider="jina",
                    status=ExecutionAttemptStatus.OK, elapsed_ms=1.0, result_count=1,
                ),
            ),
            routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("test",)),
            metadata=ExecutionMetadata("req-test", 1),
        )

    async def fake_source(request):
        return EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(), attempts=(),
            routing=EvidenceRouting(("source_discovery",), (), "v2", ("test",)),
            metadata=ExecutionMetadata("req-test", 1),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "docs_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "site_discovery", fake_source)
    monkeypatch.setattr(smart_search.research_service, "build_research_workflow_plan", fake_plan)

    for profile, expected in (("fast", "quick"), ("balanced", "standard"), ("deep", "deep")):
        # ``research run`` is the canonical workflow command; profile maps to
        # the workflow plan budget exactly like the offline planner.
        code, out, err = _run_main(["research", "run", "query", "--profile", profile, "--format", "json"])
        assert code == cli.EXIT_OK, (code, out, err)
        json.loads(out)
        assert captured[-1] == expected


def test_output_file_does_not_overwrite_without_force(tmp_path):
    output = tmp_path / "result.json"
    output.write_text("old", encoding="utf-8")

    with pytest.raises(FileExistsError):
        service.write_output(output, "new")

    service.write_output(output, "new", force=True)
    assert output.read_text(encoding="utf-8") == "new"
