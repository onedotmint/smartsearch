"""Focused tests for the strict Research Workflow contract family.

The workflow contract is independent from the V2 Evidence envelope and the V3
control-plane envelope: exact top-level shape, schema-neutral identity,
validator-owned state/error/exit truth table, recursive redaction, strict
stage/evidence/citation/artifact references, and rejection of every unknown,
V1, answer/synthesis, shell, output-path, and raw Provider field.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import httpx

from smart_search.execution_primitives import (
    DEFAULT_FETCH_CONTENT_LIMIT,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCitation,
    ExecutionEvidenceItem,
    ExecutionGap,
    error_attempt,
)
from smart_search.providers.base import classify_provider_exception
from smart_search.research_plan import (
    ResearchPlanOperation,
    build_research_plan,
    serialize_research_plan,
)
from smart_search.research_workflow import (
    ArtifactStatus,
    WorkflowArtifact,
    WorkflowDomainError,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowMeta,
    WorkflowOutcome,
    WorkflowStage,
    WorkflowStageStatus,
    WorkflowStatus,
)
from smart_search.research_workflow_contract import (
    EXIT_CONFIGURATION,
    EXIT_DEGRADED,
    EXIT_INTERNAL,
    EXIT_INVALID_ARGUMENT,
    EXIT_SUCCESS,
    EXIT_UPSTREAM,
    WORKFLOW_COMMAND,
    WORKFLOW_JSON_SCHEMA,
    WORKFLOW_OPERATION,
    WORKFLOW_SCHEMA_VERSION,
    WORKFLOW_TOP_LEVEL_FIELDS,
    WorkflowContractError,
    exit_code_for,
    serialize_workflow,
    validate_workflow,
    validate_workflow_dict,
    workflow_parser_error_result,
)

ROOT = Path(__file__).parents[1]
CONTRACT_MODULE_PATH = Path("src/smart_search/research_workflow_contract.py")

_DIGEST = "a" * 64


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


def _evidence(index: int = 1, url: str = "https://example.com/page") -> ExecutionEvidenceItem:
    return ExecutionEvidenceItem(
        id=f"evidence-{index}",
        resource=url,
        provider="jina",
        title=f"page {index}",
        content=f"body of {url}",
    )


def _attempt() -> ExecutionAttempt:
    return ExecutionAttempt(
        capability="content_fetch",
        provider="jina",
        status=ExecutionAttemptStatus.OK,
        elapsed_ms=1.0,
        result_count=1,
    )


def _artifact(stage_id: str = "fetch-1", kind: str = "evidence") -> WorkflowArtifact:
    return WorkflowArtifact(
        id="artifact-1",
        stage_id=stage_id,
        kind=kind,
        status=ArtifactStatus.WRITTEN,
        name="fetch-1-evidence-1.md",
        media_type="text/markdown",
        byte_length=8,
        digest=_DIGEST,
    )


def complete_outcome() -> WorkflowOutcome:
    plan = _plan(
        _op("discover-1", "source_discovery"),
        _op("fetch-1", "content_fetch", input={"candidate_refs": ["discover-1"]}, depends_on=("discover-1",)),
    )
    evidence = _evidence()
    artifact = _artifact()
    return WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=plan,
        stages=(
            WorkflowStage(
                id="discover-1", operation="source_discovery",
                status=WorkflowStageStatus.COMPLETE, order=1,
                input={"query": "q"}, depends_on=(), result_count=1,
            ),
            WorkflowStage(
                id="fetch-1", operation="content_fetch",
                status=WorkflowStageStatus.COMPLETE, order=2,
                input={"candidate_refs": ["discover-1"]}, depends_on=("discover-1",),
                result_count=1, evidence_ids=("evidence-1",), artifact_ids=("artifact-1",),
            ),
        ),
        evidence=(evidence,),
        citations=(ExecutionCitation("cite-1", "evidence-1", "page 1"),),
        gaps=(ExecutionGap("no_cross_validation", "no cross validation performed", "source_discovery", ""),),
        attempts=(_attempt(),),
        artifacts=(artifact,),
        meta=WorkflowMeta("req-complete", 12.5, ("a warning",)),
    )


def degraded_outcome() -> WorkflowOutcome:
    plan = _plan(
        _op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}),
        _op("fetch-b", "content_fetch", input={"resource": "https://example.com/b"}),
    )
    return WorkflowOutcome(
        status=WorkflowStatus.DEGRADED,
        plan=plan,
        stages=(
            WorkflowStage(
                id="fetch-a", operation="content_fetch",
                status=WorkflowStageStatus.COMPLETE, order=1,
                input={"resource": "https://example.com/a"}, depends_on=(),
                result_count=1, evidence_ids=("evidence-1",),
            ),
            WorkflowStage(
                id="fetch-b", operation="content_fetch",
                status=WorkflowStageStatus.FAILED, order=2,
                input={"resource": "https://example.com/b"}, depends_on=(),
                error=WorkflowError(WorkflowErrorCode.FETCH_FAILED, "fetch failed", False),
            ),
        ),
        evidence=(_evidence(),),
        citations=(ExecutionCitation("cite-1", "evidence-1", "page 1"),),
        gaps=(ExecutionGap("stage_failed", "fetch failed", "content_fetch", "https://example.com/b"),),
        attempts=(_attempt(),),
        artifacts=(),
        meta=WorkflowMeta("req-degraded", 30.0),
    )


def failed_outcome() -> WorkflowOutcome:
    plan = _plan(_op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}))
    return WorkflowOutcome(
        status=WorkflowStatus.FAILED,
        plan=plan,
        stages=(
            WorkflowStage(
                id="fetch-a", operation="content_fetch",
                status=WorkflowStageStatus.FAILED, order=1,
                input={"resource": "https://example.com/a"}, depends_on=(),
                error=WorkflowError(WorkflowErrorCode.FETCH_FAILED, "fetch failed", False),
            ),
        ),
        evidence=(),
        citations=(),
        gaps=(ExecutionGap("stage_failed", "fetch failed", "content_fetch", "https://example.com/a"),),
        attempts=(),
        artifacts=(),
        error=WorkflowError(WorkflowErrorCode.FETCH_FAILED, "research produced no admitted evidence", False),
        meta=WorkflowMeta("req-failed", 5.0),
    )


# ---------------------------------------------------------------------------
# Shape and schema
# ---------------------------------------------------------------------------


def test_exact_top_level_shape_and_identity():
    payload = serialize_workflow(complete_outcome())
    assert tuple(payload) == WORKFLOW_TOP_LEVEL_FIELDS
    assert len(payload) == 14
    assert payload["schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert payload["command"] == WORKFLOW_COMMAND
    assert payload["operation"] == WORKFLOW_OPERATION
    assert payload["status"] == "complete"
    assert payload["ok"] is True
    assert payload["error"] is None
    # legacy/answer/shell/alias fields are never present
    for forbidden in (
        "content", "final_answer", "synthesis_error", "response_mode",
        "synthesis_enabled", "synthesis", "data", "routing", "output_path",
        "provider_attempts", "evidence_items", "discovery_sources",
        "error_detail", "error_code", "error_type", "error_message",
    ):
        assert forbidden not in payload
    assert payload["plan"]["schema_version"] == "research-plan-1"
    assert "command" not in payload["plan"]
    assert "output_path" not in payload["plan"]


def test_schema_is_strict_and_fixtures_validate():
    Draft202012Validator.check_schema(WORKFLOW_JSON_SCHEMA)
    assert WORKFLOW_JSON_SCHEMA["required"] == list(WORKFLOW_TOP_LEVEL_FIELDS)
    assert WORKFLOW_JSON_SCHEMA["additionalProperties"] is False
    for fixture in (complete_outcome(), degraded_outcome(), failed_outcome()):
        raw = serialize_workflow(fixture)
        validate_workflow_dict(raw)
        Draft202012Validator(WORKFLOW_JSON_SCHEMA).validate(raw)
        json.dumps(raw)
    # parser-error result also validates
    parser = workflow_parser_error_result("bad input")
    parser_raw = serialize_workflow(parser)
    validate_workflow_dict(parser_raw)
    Draft202012Validator(WORKFLOW_JSON_SCHEMA).validate(parser_raw)


def test_evidence_item_content_budget_metadata_is_required_and_validated():
    """Workflow evidence items carry the additive content-budget metadata and
    reject contradictory values through both the typed validator and the JSON
    Schema."""
    from smart_search.research_workflow import (
        WorkflowOutcome,
        WorkflowStage,
        WorkflowStageStatus,
        WorkflowStatus,
    )
    from smart_search.execution_primitives import ExecutionCitation, ExecutionGap

    truncated = ExecutionEvidenceItem(
        id="evidence-trunc",
        resource="https://example.com/page",
        provider="jina",
        title="page",
        content="x" * 8000,
        truncated=True,
        original_length=9000,
        returned_length=8000,
    )
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=_plan(_op("fetch-1", "content_fetch", input={"resource": "https://example.com/page"})),
        stages=(
            WorkflowStage(
                id="fetch-1", operation="content_fetch",
                status=WorkflowStageStatus.COMPLETE, order=1,
                input={"resource": "https://example.com/page"}, depends_on=(),
                result_count=1, evidence_ids=("evidence-trunc",),
            ),
        ),
        evidence=(truncated,),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        meta=WorkflowMeta("req-budget", 1.0),
    )
    raw = serialize_workflow(outcome)
    item = raw["evidence"][0]
    assert item["truncated"] is True
    assert item["original_length"] == 9000
    assert item["returned_length"] == 8000
    validate_workflow_dict(raw)
    Draft202012Validator(WORKFLOW_JSON_SCHEMA).validate(raw)

    # Default untruncated fixtures derive equal lengths from the content.
    defaulted = serialize_workflow(complete_outcome())
    assert defaulted["evidence"][0]["truncated"] is False
    assert defaulted["evidence"][0]["original_length"] == defaulted["evidence"][0]["returned_length"]

    # Missing metadata fails the raw validator and the JSON Schema.
    broken = serialize_workflow(outcome)
    del broken["evidence"][0]["truncated"]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(broken)
    assert list(Draft202012Validator(WORKFLOW_JSON_SCHEMA).iter_errors(broken))

    # Contradictory metadata fails the raw validator.
    broken = serialize_workflow(outcome)
    broken["evidence"][0]["original_length"] = 5000
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(broken)

    broken = serialize_workflow(outcome)
    broken["evidence"][0]["returned_length"] = 7999
    with pytest.raises(WorkflowContractError, match="returned_length must equal the content length"):
        validate_workflow_dict(broken)
    broken = serialize_workflow(outcome)
    broken["evidence"][0]["content"] = "x" * (DEFAULT_FETCH_CONTENT_LIMIT + 1)
    broken["evidence"][0]["original_length"] = DEFAULT_FETCH_CONTENT_LIMIT + 2
    broken["evidence"][0]["returned_length"] = DEFAULT_FETCH_CONTENT_LIMIT + 1
    with pytest.raises(WorkflowContractError, match="must not exceed DEFAULT_FETCH_CONTENT_LIMIT"):
        validate_workflow_dict(broken)


def test_typed_and_raw_round_trip():
    raw = serialize_workflow(complete_outcome())
    validated = validate_workflow_dict(raw)
    assert validated == raw
    assert json.loads(json.dumps(raw)) == raw


# ---------------------------------------------------------------------------
# Unknown / legacy / answer / shell / output-path rejection
# ---------------------------------------------------------------------------


def _base_raw() -> dict:
    return serialize_workflow(complete_outcome())


@pytest.mark.parametrize(
    "extra",
    [
        {"content": "answer"},
        {"final_answer": "answer"},
        {"synthesis_error": "boom"},
        {"response_mode": "synthesized"},
        {"synthesis_enabled": True},
        {"data": {}},
        {"routing": {}},
        {"output_path": "/tmp/out.md"},
        {"shell": "smart-search search"},
        {"provider_attempts": []},
        {"evidence_items": []},
        {"error_detail": "x"},
        {"unknown_field": 1},
    ],
)
def test_unknown_and_legacy_top_level_fields_rejected(extra):
    raw = _base_raw()
    raw.update(extra)
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_nested_unknown_fields_rejected():
    raw = _base_raw()
    raw["stages"][0]["extra"] = 1
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["evidence"][0]["raw_content"] = "raw"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["artifacts"][0]["metadata"] = {"owner": "x"}
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["attempts"][0]["details"] = {"cache_hit": True}
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["meta"]["trace"] = []
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_stage_input_rejects_shell_output_path_and_provider_fields():
    raw = _base_raw()
    raw["stages"][0]["input"] = {"query": "q", "command": "smart-search search"}
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["stages"][0]["input"] = {"query": "q", "output_path": "/tmp/x.md"}
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["stages"][0]["input"] = {"query": "q", "provider_id": "tavily"}
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_embedded_plan_rejects_legacy_fields():
    raw = _base_raw()
    raw["plan"] = {
        "schema_version": "research-plan-1",
        "operations": [{
            "id": "a", "operation": "source_discovery",
            "input": {"query": "q", "command": "search"}, "constraints": {},
            "depends_on": [],
        }],
    }
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_identity_and_ok_derivation():
    raw = _base_raw()
    raw["command"] = "search"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["operation"] = "research.plan"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["schema_version"] = "2"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["ok"] = False
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


# ---------------------------------------------------------------------------
# State / error / exit truth table
# ---------------------------------------------------------------------------


def test_state_truth_table_and_exit_registry():
    complete = serialize_workflow(complete_outcome())
    degraded = serialize_workflow(degraded_outcome())
    failed = serialize_workflow(failed_outcome())
    assert complete["ok"] is True and complete["status"] == "complete" and complete["error"] is None
    assert degraded["ok"] is True and degraded["status"] == "degraded" and degraded["error"] is None
    assert failed["ok"] is False and failed["status"] == "failed"
    assert failed["error"]["code"] == "FETCH_FAILED"
    assert exit_code_for(complete) == EXIT_SUCCESS
    assert exit_code_for(degraded) == EXIT_SUCCESS
    assert exit_code_for(degraded, fail_on_degraded=True) == EXIT_DEGRADED
    assert exit_code_for(failed) == EXIT_UPSTREAM
    assert exit_code_for(WorkflowStatus.FAILED.value) == EXIT_INTERNAL
    assert exit_code_for({"status": "bogus"}) == EXIT_INTERNAL
    assert exit_code_for({"status": "failed", "error": {"code": "CONFIGURATION_ERROR"}}) == EXIT_CONFIGURATION
    assert exit_code_for({"status": "failed", "error": {"code": "INVALID_ARGUMENT"}}) == EXIT_INVALID_ARGUMENT
    assert exit_code_for({"status": "failed", "error": {"code": "INTERNAL_ERROR"}}) == EXIT_INTERNAL
    assert exit_code_for({"status": "failed", "error": {"code": "UNKNOWN"}}) == EXIT_INTERNAL


def test_error_retryability_registry():
    assert WorkflowError(
        WorkflowErrorCode.INVALID_ARGUMENT, "bad", False
    ).retryable is False
    with pytest.raises(WorkflowDomainError):
        WorkflowError(WorkflowErrorCode.FETCH_FAILED, "x", True)
    raw = _base_raw()
    raw["error"] = {
        "code": "FETCH_FAILED", "message": "boom", "retryable": True, "details": {},
    }
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_invalid_typed_states_rejected_by_validator():
    # complete with an error is rejected at typed construction (fail-fast) and
    # by the raw validator with the contract error type
    with pytest.raises(WorkflowDomainError):
        WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=_plan(_op("a")),
            stages=(
                WorkflowStage(
                    id="a", operation="source_discovery",
                    status=WorkflowStageStatus.COMPLETE, order=1, input={},
                ),
            ),
            evidence=(), citations=(), gaps=(), attempts=(), artifacts=(),
            error=WorkflowError(WorkflowErrorCode.INTERNAL_ERROR, "boom", False),
            meta=WorkflowMeta("r", 0),
        )
    # degraded stage but status complete
    raw = serialize_workflow(complete_outcome())
    raw["stages"][0]["status"] = "degraded"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    # complete with a failed stage
    raw = serialize_workflow(complete_outcome())
    raw["stages"][1]["status"] = "failed"
    raw["stages"][1]["error"] = {
        "code": "FETCH_FAILED", "message": "boom", "retryable": False, "details": {},
    }
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    # failed without error
    raw = serialize_workflow(complete_outcome())
    raw["status"] = "failed"
    raw["ok"] = False
    raw["error"] = None
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    # degraded with no degradation signal
    raw = serialize_workflow(complete_outcome())
    raw["status"] = "degraded"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


# ---------------------------------------------------------------------------
# References and DAG
# ---------------------------------------------------------------------------


def test_citation_and_reference_integrity():
    raw = _base_raw()
    raw["citations"][0]["evidence_id"] = "missing-evidence"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["stages"][1]["evidence_ids"] = ["missing-evidence"]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["stages"][1]["artifact_ids"] = ["missing-artifact"]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["artifacts"][0]["stage_id"] = "missing-stage"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["stages"][0]["depends_on"] = ["fetch-1"]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)  # forward dependency
    raw = _base_raw()
    raw["stages"][0]["depends_on"] = ["missing"]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["stages"][0]["order"] = 3
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)  # non-contiguous order
    raw = _base_raw()
    raw["stages"].append(dict(raw["stages"][0]))
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)  # duplicate stage id


def test_attempt_status_error_consistency():
    base = _base_raw()
    attempt = base["attempts"][0]
    ok_attempt = dict(attempt)
    ok_attempt["error_type"] = "network_error"
    raw = _base_raw()
    raw["attempts"] = [ok_attempt]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    error_attempt = dict(attempt)
    error_attempt.update({"status": "error", "error_type": "", "error": "", "retryable": False})
    raw = _base_raw()
    raw["attempts"] = [error_attempt]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    empty_attempt = dict(attempt)
    empty_attempt.update({"status": "empty", "error_type": "wrong", "error": "no result", "retryable": False})
    raw = _base_raw()
    raw["attempts"] = [empty_attempt]
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


# ---------------------------------------------------------------------------
# Artifact safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "/etc/passwd",
        "C:/tmp/evidence.md",
        "./relative.md",
        "../escape.md",
        "a/../b.md",
        "https://user:pass@host/x.md",
        "a\\b.md",
        "a//b.md",
        "",
        "a b.md",
        "a" * 256,
    ],
)
def test_raw_artifact_unsafe_names_rejected(bad_name):
    raw = _base_raw()
    raw["artifacts"][0]["name"] = bad_name
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_raw_artifact_bad_metadata_rejected():
    raw = _base_raw()
    raw["artifacts"][0]["digest"] = "not-a-digest"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["artifacts"][0]["byte_length"] = -1
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["artifacts"][0]["media_type"] = "../evil"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["artifacts"][0]["status"] = "written-extra"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)
    raw = _base_raw()
    raw["artifacts"][0]["kind"] = "Evidence"
    with pytest.raises(WorkflowContractError):
        validate_workflow_dict(raw)


def test_artifact_partial_status_serializes_and_degrades():
    raw = serialize_workflow(complete_outcome())
    raw["artifacts"][0]["status"] = "partial"
    raw["status"] = "degraded"
    raw["stages"][0]["status"] = "degraded"
    # adjust the degraded truth table: a degraded stage is present
    validate_workflow_dict(raw)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_serialize_redacts_secrets_urls_and_error_details():
    plan = _plan(_op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}))
    outcome = WorkflowOutcome(
        status=WorkflowStatus.FAILED,
        plan=plan,
        stages=(
            WorkflowStage(
                id="fetch-a", operation="content_fetch",
                status=WorkflowStageStatus.FAILED, order=1,
                input={"resource": "https://example.com/a"}, depends_on=(),
                error=WorkflowError(WorkflowErrorCode.FETCH_FAILED, "boom", False),
            ),
        ),
        evidence=(
            ExecutionEvidenceItem(
                id="evidence-1",
                resource="https://example.com/a",
                provider="jina",
                title="page",
                content="body mentions secret-value-123",
            ),
        ),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=WorkflowError(
            WorkflowErrorCode.FETCH_FAILED,
            "failed with secret-value-123",
            False,
            {"api_key": "secret-value-123"},
        ),
        meta=WorkflowMeta("req-redact", 1.0, ("token=abc123",)),
    )
    redacted = serialize_workflow(outcome, secrets=("secret-value-123",))
    text = json.dumps(redacted)
    assert "secret-value-123" not in text
    assert "abc123" not in text
    assert redacted["error"]["details"]["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in redacted["evidence"][0]["content"]
    validate_workflow_dict(redacted)


def test_serialize_redacts_signed_url_echoed_by_provider_payload():
    signed_url = (
        "https://cdn.example.com/file.zip?key=access-key-abc&sig=signature-xyz"
        "&signature=extra-sig&expires=20300101"
    )
    plan = _plan(_op("fetch-a", "content_fetch", input={"resource": signed_url}))
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=plan,
        stages=(
            WorkflowStage(
                id="fetch-a", operation="content_fetch",
                status=WorkflowStageStatus.COMPLETE, order=1,
                input={"resource": signed_url}, depends_on=(), result_count=1,
                evidence_ids=("evidence-1",),
            ),
        ),
        evidence=(
            ExecutionEvidenceItem(
                id="evidence-1",
                resource=signed_url,
                provider="jina",
                title="Signed artifact",
                content=f"provider echoed {signed_url} in the payload",
            ),
        ),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=None,
        meta=WorkflowMeta("req-signed", 1.0, (signed_url,)),
    )
    redacted = serialize_workflow(outcome)
    text = json.dumps(redacted)
    assert "signature-xyz" not in text
    assert "access-key-abc" not in text
    assert "extra-sig" not in text
    assert "sig=%5BREDACTED%5D" in text
    assert "key=%5BREDACTED%5D" in text
    assert "signature=%5BREDACTED%5D" in text
    assert "cdn.example.com" in text
    assert "expires=20300101" in text
    evidence = redacted["evidence"][0]
    assert evidence["returned_length"] == len(evidence["content"])
    assert evidence["original_length"] == evidence["returned_length"]
    validate_workflow_dict(redacted)


def test_serializer_preserves_truncated_evidence_budget_after_signed_url_redaction():
    signed_url = "https://cdn.example.com/file?sig=a"
    content = signed_url + " " + "x" * (8000 - len(signed_url) - 1)
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=_plan(_op("fetch-a", "content_fetch", input={"resource": signed_url})),
        stages=(
            WorkflowStage(
                id="fetch-a",
                operation="content_fetch",
                status=WorkflowStageStatus.COMPLETE,
                order=1,
                input={"resource": signed_url},
                depends_on=(),
                result_count=1,
                evidence_ids=("evidence-truncated",),
            ),
        ),
        evidence=(
            ExecutionEvidenceItem(
                id="evidence-truncated",
                resource=signed_url,
                provider="jina",
                title="Signed artifact",
                content=content,
                truncated=True,
                original_length=8001,
                returned_length=8000,
            ),
        ),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=None,
        meta=WorkflowMeta("req-signed-truncated", 1.0),
    )

    payload = serialize_workflow(outcome)
    evidence = payload["evidence"][0]
    assert "?sig=a" not in evidence["content"]
    assert "?sig=%5BREDACTED%5D" in evidence["content"]
    assert evidence["truncated"] is True
    assert evidence["original_length"] == 8001
    assert evidence["returned_length"] == len(evidence["content"]) == DEFAULT_FETCH_CONTENT_LIMIT
    assert len(evidence["content"]) <= DEFAULT_FETCH_CONTENT_LIMIT
    validate_workflow_dict(payload)
    Draft202012Validator(WORKFLOW_JSON_SCHEMA).validate(payload)


def test_serialize_redacts_url_userinfo_in_resources():
    plan = _plan(_op("fetch-a", "content_fetch", input={"resource": "https://example.com/a"}))
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=plan,
        stages=(
            WorkflowStage(
                id="fetch-a", operation="content_fetch",
                status=WorkflowStageStatus.COMPLETE, order=1,
                input={"resource": "https://example.com/a"}, depends_on=(),
                result_count=1, evidence_ids=("evidence-1",), artifact_ids=("artifact-1",),
            ),
        ),
        evidence=(
            ExecutionEvidenceItem(
                id="evidence-1",
                resource="https://user:hunter2@example.com/private",
                provider="jina",
                title="private",
                content="body",
            ),
        ),
        citations=(ExecutionCitation("cite-1", "evidence-1", "private"),),
        gaps=(),
        attempts=(),
        artifacts=(_artifact(stage_id="fetch-a"),),
        meta=WorkflowMeta("req-redact", 1.0),
    )
    payload = serialize_workflow(outcome)
    text = json.dumps(payload)
    assert "hunter2" not in text
    assert "user:hunter2@" not in text
    assert "[REDACTED]@example.com/private" in text
    validate_workflow_dict(payload)


def test_serialize_rejects_non_finite_numbers():
    plan = _plan(_op("a"))
    outcome = WorkflowOutcome(
        status=WorkflowStatus.COMPLETE,
        plan=plan,
        stages=(
            WorkflowStage(
                id="a", operation="source_discovery",
                status=WorkflowStageStatus.COMPLETE, order=1, input={},
            ),
        ),
        evidence=(),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        meta=WorkflowMeta("r", 0),
    )
    payload = serialize_workflow(outcome)
    assert payload["meta"]["duration_ms"] == 0
    # NaN can never enter the typed model
    with pytest.raises(WorkflowDomainError):
        WorkflowMeta("r", float("nan"))


# ---------------------------------------------------------------------------
# Parser error and import isolation
# ---------------------------------------------------------------------------


def test_parser_error_result_is_pure_and_maps_to_exit_2():
    result = workflow_parser_error_result("invalid research-run input", {"reason": "bad flag"})
    raw = serialize_workflow(result)
    assert raw["status"] == "failed"
    assert raw["ok"] is False
    assert raw["error"]["code"] == "INVALID_ARGUMENT"
    assert raw["error"]["retryable"] is False
    assert raw["stages"] == [] and raw["evidence"] == [] and raw["artifacts"] == []
    assert exit_code_for(raw) == EXIT_INVALID_ARGUMENT
    validate_workflow(result)


def test_importing_workflow_contract_is_light(tmp_path):
    config_dir = tmp_path / "config"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["SMART_SEARCH_CONFIG_DIR"] = str(config_dir)
    script = """
import sys
import smart_search.research_workflow_contract
for name in (
    'smart_search.evidence_operations',
    'smart_search.operation_runtime',
    'smart_search.runtime_cache',
    'smart_search.capability_service',
    'smart_search.config',
    'smart_search.v2_contract',
    'smart_search.control_plane_contract',
    'smart_search.service',
    'smart_search.research_service',
    'smart_search.providers',
):
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, check=True)
    assert not config_dir.exists()


def test_contract_module_forbidden_imports():
    """The serializer must not import Evidence owners, execution runtime,
    CLI, service, Provider modules, or other contract envelopes."""
    source = CONTRACT_MODULE_PATH.read_text(encoding="utf-8")
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
        "smart_search.evidence_operations",
        "smart_search.operation_runtime",
        "smart_search.runtime_cache",
        "smart_search.capability_service",
        "smart_search.config",
    )
    for module in imported:
        assert not any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes
        ), f"forbidden import: {module}"
    # the serializer reuses only the shared primitives, research plan, workflow
    # value models, and redaction helpers
    assert "research_plan" in imported
    assert "research_workflow" in imported
    assert "security" in imported

# ---------------------------------------------------------------------------
# Upstream response-body containment in Workflow JSON
# ---------------------------------------------------------------------------


def test_workflow_json_contains_no_provider_body_bytes():
    """An upstream error body echoing credentials must never cross the
    Workflow JSON boundary: the attempt error projected into Workflow JSON
    carries status only, and no body bytes survive serialization."""
    request = httpx.Request("POST", "https://provider.example.test/v1/search")
    response = httpx.Response(429, text='{"error": "echo api_key=sk-leaked-123 request fragment"}', request=request)
    error = httpx.HTTPStatusError("upstream", request=request, response=response)
    _, message, retryable = classify_provider_exception(error)
    assert message == "HTTP 429"

    outcome = complete_outcome()
    outcome = WorkflowOutcome(
        status=outcome.status,
        plan=outcome.plan,
        stages=outcome.stages,
        evidence=outcome.evidence,
        citations=outcome.citations,
        gaps=outcome.gaps,
        attempts=(
            error_attempt(
                "content_fetch",
                "jina",
                error_type="rate_limited",
                message=message,
                elapsed_ms=1.0,
                retryable=retryable,
            ),
        ),
        artifacts=outcome.artifacts,
        meta=outcome.meta,
    )

    payload = serialize_workflow(outcome)
    rendered = json.dumps(payload)
    assert "sk-leaked-123" not in rendered
    assert "api_key=" not in rendered
    assert payload["attempts"][0]["error"] == "HTTP 429"
    assert payload["attempts"][0]["error_type"] == "rate_limited"
