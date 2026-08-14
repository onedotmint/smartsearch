import json
import io
import contextlib

import pytest

from smart_search import capability_service, cli
from smart_search.utils import PromptConfigurationError, get_prompt, prompt_overrides


def _run_main(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_v1_json_contract_is_removed_without_replacement_shim():
    """The v1 envelope authority (build_json_result and its duplicate
    error_detail/error_code/error_message/data projections) is deleted; no
    compatibility shim may recreate it."""
    import pytest as _pytest

    with _pytest.raises(ImportError):
        import smart_search.cli_contract  # noqa: F401
    with _pytest.raises(ImportError):
        from smart_search.cli_contract import build_json_result  # noqa: F401

    # Canonical families emit strictly one envelope each: no v1 duplicate
    # error fields on the typed serializers.
    from smart_search.v2_contract import serialize_result as v2_serialize
    from smart_search.v2_contract import parser_error_result as v2_parser_error

    payload = v2_serialize(v2_parser_error("fetch", "content_fetch", "boom"))
    assert "error_detail" not in payload
    assert "error_code" not in payload
    assert "error_message" not in payload
    assert "data" not in payload
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


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
    # ``standard`` is the Core minimum: source discovery (web_search OR
    # docs_search) plus web_fetch. Legacy model routes are optional LLM
    # synthesis state, never a Core requirement.
    assert standard["missing_required"] == ["web_fetch"]
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


def test_v1_output_utility_is_removed(tmp_path):
    """The v1 write_output helper is deleted with the v1 renderer; no CLI path
    writes rendered output files anymore."""
    import smart_search.control_executors as control_executors

    assert not hasattr(control_executors, "write_output")
    assert not hasattr(control_executors, "OutputFileExistsError")
