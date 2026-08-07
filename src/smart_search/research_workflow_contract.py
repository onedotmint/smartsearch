"""Strict Research Workflow contract serializer and validator.

This module is the projection boundary for the typed Research Workflow family.
It validates and serializes an already-complete ``WorkflowOutcome`` into the
exact workflow JSON shape and validates untrusted raw workflow JSON. It never
executes stages, selects Providers, performs fetches, writes artifacts,
reorders stages, or infers terminal status, and it imports only the shared
primitives, the schema-neutral research plan, and the workflow value models.

Exact top-level shape (distinct from the V2 Evidence and V3 control-plane
envelopes)::

    schema_version, ok, status, command, operation, plan, stages,
    evidence, citations, gaps, attempts, artifacts, error, meta

The workflow identity is ``command = "research"`` and
``operation = "research.run"``. All unknown keys, V1 ``data`` / flat aliases,
answer-synthesis fields (``content``, ``final_answer``, ``synthesis_error``,
``response_mode``, ``synthesis_enabled``), shell commands, output paths, raw
Provider payloads, V2 ``routing`` envelopes, unsafe artifact records,
credentialed URL leaks, and duplicate/dangling identities are rejected.
Recursive redaction of secrets and URL userinfo is validator-owned and applied
at serialization time.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any

from .research_plan import (
    RESEARCH_PLAN_SCHEMA_VERSION,
    ResearchPlan,
    ResearchPlanError,
    research_plan_from_dict,
    serialize_research_plan,
)
from .research_workflow import (
    EXIT_CONFIGURATION,
    EXIT_DEGRADED,
    EXIT_INTERNAL,
    EXIT_INVALID_ARGUMENT,
    EXIT_SUCCESS,
    EXIT_UPSTREAM,
    WORKFLOW_ERROR_EXIT_CODES,
    WORKFLOW_ERROR_REGISTRY,
    WORKFLOW_ERROR_RETRYABILITY,
    WORKFLOW_EXECUTABLE_OPERATIONS,
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
    validate_artifact_name,
)
from .security import sanitize_data

WORKFLOW_SCHEMA_VERSION = "research-workflow-1"
WORKFLOW_COMMAND = "research"
WORKFLOW_OPERATION = "research.run"
WORKFLOW_TOP_LEVEL_FIELDS = (
    "schema_version",
    "ok",
    "status",
    "command",
    "operation",
    "plan",
    "stages",
    "evidence",
    "citations",
    "gaps",
    "attempts",
    "artifacts",
    "error",
    "meta",
)
EXIT_OK = EXIT_SUCCESS

# Legacy/answer/shell fields that are never part of the stable workflow.
WORKFLOW_FORBIDDEN_TOP_LEVEL_FIELDS = frozenset(
    {
        "content",
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

_ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_ARTIFACT_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
# Type and subtype must start with an alphanumeric so path-traversal-like
# values ("../evil") can never be a media type.
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")

_ATTEMPT_STATUSES = ("ok", "empty", "error", "skipped")


class WorkflowContractError(ValueError):
    """Raised when a typed or raw workflow result violates the contract."""


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _value(value: Enum | str | None) -> str | None:
    return value.value if isinstance(value, Enum) else value


def _secret_values(secrets: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(secrets, str):
        return (secrets,) if secrets else ()
    return tuple(str(secret) for secret in secrets if secret)


def _nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowContractError(f"{name} must be a non-blank string")
    return value.strip()


def _exact_keys(value: Any, required: Sequence[str], name: str, optional: Sequence[str] = ()) -> None:
    if not isinstance(value, dict):
        raise WorkflowContractError(f"{name} must be an object")
    required_set, allowed = set(required), set(required) | set(optional)
    if not required_set.issubset(value) or not set(value).issubset(allowed):
        raise WorkflowContractError(
            f"{name} has invalid fields; missing={sorted(required_set - set(value))} "
            f"extra={sorted(set(value) - allowed)}"
        )


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise WorkflowContractError(f"{name} must be an array")
    return value


def _exact_int(value: Any, name: str) -> None:
    # Integral floats are accepted exactly like the parent V2 integer policy
    # (``v2_contract._exact_int``): ``3.0`` is a valid non-negative integer,
    # ``3.5`` is not. This is an intentional alignment, not a divergence.
    is_integral = type(value) is int or (type(value) is float and value.is_integer())
    if not is_integral or value < 0:
        raise WorkflowContractError(f"{name} must be a non-negative integer")


def _number(value: Any, name: str) -> None:
    if type(value) not in (int, float) or type(value) is bool:
        raise WorkflowContractError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise WorkflowContractError(f"{name} must be a non-negative finite number")


def _enum_value(value: Any, enum: type[Enum], name: str) -> Enum:
    try:
        return enum(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError(f"unknown {name}: {value!r}") from exc


def _capability(value: Any, name: str, *, allow_empty: bool = False) -> None:
    if allow_empty and value == "":
        return
    if value not in WORKFLOW_EXECUTABLE_OPERATIONS:
        raise WorkflowContractError(f"{name} is not a workflow executable operation: {value!r}")


def _unique(values: Sequence[Any], name: str) -> None:
    if len(values) != len(set(values)):
        raise WorkflowContractError(f"{name} values must be unique")


def _validate_error(error: WorkflowError) -> None:
    try:
        code = WorkflowErrorCode(_value(error.code))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError(f"unknown workflow error code: {error.code!r}") from exc
    _nonblank(error.message, "error.message")
    if type(error.retryable) is not bool:
        raise WorkflowContractError("error.retryable must be boolean")
    if error.retryable is not WORKFLOW_ERROR_RETRYABILITY[code]:
        raise WorkflowContractError(f"retryable does not match registry for {code.value}")
    if not isinstance(error.details, Mapping):
        raise WorkflowContractError("error.details must be an object")


# ---------------------------------------------------------------------------
# Typed outcome validation
# ---------------------------------------------------------------------------


def validate_workflow(outcome: WorkflowOutcome) -> WorkflowOutcome:
    """Validate a typed workflow outcome and return the same immutable value.

    Structural invariants are enforced by the typed models at construction;
    this authoritative pass re-validates the terminal state truth table, the
    error registry, and JSON compatibility.
    """
    if not isinstance(outcome, WorkflowOutcome):
        raise WorkflowContractError("workflow result must be a WorkflowOutcome")
    try:
        status = WorkflowStatus(_value(outcome.status))
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError(f"unknown workflow status: {outcome.status!r}") from exc
    if outcome.error is not None and not isinstance(outcome.error, WorkflowError):
        raise WorkflowContractError("error must be a WorkflowError or None")
    if status is WorkflowStatus.COMPLETE:
        if outcome.error is not None:
            raise WorkflowContractError("complete requires error=null")
        if any(stage.status is not WorkflowStageStatus.COMPLETE for stage in outcome.stages):
            raise WorkflowContractError("complete requires all stages complete")
        if any(artifact.status is not ArtifactStatus.WRITTEN for artifact in outcome.artifacts):
            raise WorkflowContractError("complete requires all artifacts written")
    elif status is WorkflowStatus.DEGRADED:
        if outcome.error is not None:
            raise WorkflowContractError("degraded requires error=null")
        if any(stage.status is WorkflowStageStatus.CANCELLED for stage in outcome.stages):
            raise WorkflowContractError("degraded cannot contain cancelled stages")
        if not any(
            stage.status in (WorkflowStageStatus.DEGRADED, WorkflowStageStatus.FAILED)
            for stage in outcome.stages
        ) and not any(
            artifact.status is not ArtifactStatus.WRITTEN for artifact in outcome.artifacts
        ):
            raise WorkflowContractError(
                "degraded requires a degraded/failed stage or a partial/failed artifact"
            )
    else:
        if outcome.error is None:
            raise WorkflowContractError("failed requires an error")
        _validate_error(outcome.error)
    return outcome


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _error_to_dict(error: WorkflowError) -> dict[str, Any]:
    return {
        "code": _value(error.code),
        "message": error.message,
        "retryable": error.retryable,
        "details": _thaw(error.details),
    }


def _stage_to_dict(stage: WorkflowStage) -> dict[str, Any]:
    return {
        "id": stage.id,
        "operation": stage.operation,
        "status": _value(stage.status),
        "order": stage.order,
        "depends_on": list(stage.depends_on),
        "input": _thaw(stage.input),
        "result_count": stage.result_count,
        "evidence_ids": list(stage.evidence_ids),
        "artifact_ids": list(stage.artifact_ids),
        "error": None if stage.error is None else _error_to_dict(stage.error),
    }


def _evidence_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "resource": item.resource,
        "provider": item.provider,
        "title": item.title,
        "content": item.content,
    }


def _citation_to_dict(item: Any) -> dict[str, Any]:
    return {"id": item.id, "evidence_id": item.evidence_id, "label": item.label}


def _gap_to_dict(item: Any) -> dict[str, Any]:
    return {
        "code": item.code,
        "message": item.message,
        "capability": item.capability,
        "resource": item.resource,
    }


def _attempt_to_dict(attempt: Any) -> dict[str, Any]:
    error = attempt.error
    if error is None:
        error_type = ""
        error_text = ""
        retryable = None
    else:
        error_type = error.type
        error_text = error.message
        retryable = bool(error.retryable) if error.retryable is not None else None
    return {
        "capability": attempt.capability,
        "provider": attempt.provider,
        "status": _value(attempt.status),
        "error_type": error_type,
        "error": error_text,
        "elapsed_ms": attempt.elapsed_ms,
        "result_count": attempt.result_count,
        "retryable": retryable,
    }


def _artifact_to_dict(artifact: WorkflowArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "stage_id": artifact.stage_id,
        "kind": artifact.kind,
        "status": _value(artifact.status),
        "name": artifact.name,
        "media_type": artifact.media_type,
        "byte_length": artifact.byte_length,
        "digest": artifact.digest,
    }


def _meta_to_dict(meta: WorkflowMeta) -> dict[str, Any]:
    return {
        "request_id": meta.request_id,
        "duration_ms": meta.duration_ms,
        "warnings": list(meta.warnings),
    }


def serialize_workflow(
    outcome: WorkflowOutcome, *, secrets: Iterable[str] | str = ()
) -> dict[str, Any]:
    """Return a fresh, deterministic, recursively redacted workflow JSON object.

    The serializer validates the already-complete outcome and projects only the
    stable workflow facts: identity/status, plan, stages, evidence, citations,
    gaps, attempts, artifacts, error, and meta. It never executes or mutates
    anything and never emits answer/synthesis, shell, output-path, or raw
    Provider payload fields.
    """
    validate_workflow(outcome)
    secret_values = _secret_values(secrets)
    payload = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "ok": _value(outcome.status) in (WorkflowStatus.COMPLETE.value, WorkflowStatus.DEGRADED.value),
        "status": _value(outcome.status),
        "command": WORKFLOW_COMMAND,
        "operation": WORKFLOW_OPERATION,
        "plan": serialize_research_plan(outcome.plan),
        "stages": [_stage_to_dict(stage) for stage in outcome.stages],
        "evidence": [_evidence_item_to_dict(item) for item in outcome.evidence],
        "citations": [_citation_to_dict(item) for item in outcome.citations],
        "gaps": [_gap_to_dict(item) for item in outcome.gaps],
        "attempts": [_attempt_to_dict(item) for item in outcome.attempts],
        "artifacts": [_artifact_to_dict(item) for item in outcome.artifacts],
        "error": None if outcome.error is None else _error_to_dict(outcome.error),
        "meta": _meta_to_dict(outcome.meta),
    }
    sanitized = sanitize_data(payload, secret_values)
    validate_workflow_dict(sanitized)
    try:
        json.dumps(sanitized, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("workflow result must be JSON-compatible") from exc
    return sanitized


# ---------------------------------------------------------------------------
# Raw dict validation
# ---------------------------------------------------------------------------


def _error_from_raw(raw: Any) -> WorkflowError:
    _exact_keys(raw, ("code", "message", "retryable", "details"), "error")
    try:
        code = WorkflowErrorCode(raw["code"])
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError(f"unknown workflow error code: {raw['code']!r}") from exc
    _nonblank(raw["message"], "error.message")
    if type(raw["retryable"]) is not bool:
        raise WorkflowContractError("error.retryable must be boolean")
    if raw["retryable"] is not WORKFLOW_ERROR_RETRYABILITY[code]:
        raise WorkflowContractError(f"retryable does not match registry for {code.value}")
    if not isinstance(raw["details"], dict):
        raise WorkflowContractError("error.details must be an object")
    return WorkflowError(code, raw["message"], raw["retryable"], dict(raw["details"]))


def _stage_from_raw(raw: Any) -> WorkflowStage:
    fields = (
        "id", "operation", "status", "order", "depends_on", "input",
        "result_count", "evidence_ids", "artifact_ids", "error",
    )
    _exact_keys(raw, fields, "stage")
    _nonblank(raw["id"], "stage.id")
    _capability(raw["operation"], "stage.operation")
    status = _enum_value(raw["status"], WorkflowStageStatus, "stage.status")
    _exact_int(raw["order"], "stage.order")
    if raw["order"] < 1:
        raise WorkflowContractError("stage.order must be a positive integer")
    if not isinstance(raw["input"], dict):
        raise WorkflowContractError("stage.input must be an object")
    if not isinstance(raw["depends_on"], list):
        raise WorkflowContractError("stage.depends_on must be an array")
    for item in raw["depends_on"]:
        _nonblank(item, "stage.depends_on")
    _unique(raw["depends_on"], "stage.depends_on")
    _exact_int(raw["result_count"], "stage.result_count")
    for name in ("evidence_ids", "artifact_ids"):
        if not isinstance(raw[name], list):
            raise WorkflowContractError(f"stage.{name} must be an array")
        for item in raw[name]:
            _nonblank(item, f"stage.{name}")
        _unique(raw[name], f"stage.{name}")
    error = None if raw["error"] is None else _error_from_raw(raw["error"])
    return WorkflowStage(
        id=raw["id"],
        operation=raw["operation"],
        status=status,
        order=raw["order"],
        input=dict(raw["input"]),
        depends_on=tuple(raw["depends_on"]),
        result_count=raw["result_count"],
        evidence_ids=tuple(raw["evidence_ids"]),
        artifact_ids=tuple(raw["artifact_ids"]),
        error=error,
    )


def _evidence_item_from_raw(raw: Any) -> Any:
    fields = ("id", "resource", "provider", "title", "content")
    _exact_keys(raw, fields, "evidence item")
    for name in ("id", "resource", "provider", "content"):
        _nonblank(raw[name], f"evidence item.{name}")
    if not isinstance(raw["title"], str):
        raise WorkflowContractError("evidence item.title must be a string")
    return {
        "id": raw["id"],
        "resource": raw["resource"],
        "provider": raw["provider"],
        "title": raw["title"],
        "content": raw["content"],
    }


def _citation_from_raw(raw: Any) -> dict[str, str]:
    fields = ("id", "evidence_id", "label")
    _exact_keys(raw, fields, "citation")
    for name in fields:
        _nonblank(raw[name], f"citation.{name}")
    return dict(raw)


def _gap_from_raw(raw: Any) -> dict[str, Any]:
    fields = ("code", "message", "capability", "resource")
    _exact_keys(raw, fields, "gap")
    _nonblank(raw["code"], "gap.code")
    _nonblank(raw["message"], "gap.message")
    _capability(raw["capability"], "gap.capability", allow_empty=True)
    if not isinstance(raw["resource"], str):
        raise WorkflowContractError("gap.resource must be a string")
    return dict(raw)


def _attempt_from_raw(raw: Any) -> dict[str, Any]:
    fields = (
        "capability", "provider", "status", "error_type", "error",
        "elapsed_ms", "result_count", "retryable",
    )
    _exact_keys(raw, fields, "attempt")
    _capability(raw["capability"], "attempt.capability")
    _nonblank(raw["provider"], "attempt.provider")
    if raw["status"] not in _ATTEMPT_STATUSES:
        raise WorkflowContractError(f"unknown attempt status: {raw['status']!r}")
    status = raw["status"]
    if not isinstance(raw["error_type"], str) or not isinstance(raw["error"], str):
        raise WorkflowContractError("attempt.error_type and attempt.error must be strings")
    _number(raw["elapsed_ms"], "attempt.elapsed_ms")
    _exact_int(raw["result_count"], "attempt.result_count")
    retryable = raw["retryable"]
    if retryable is not None and type(retryable) is not bool:
        raise WorkflowContractError("attempt.retryable must be boolean or null")
    if status == "ok":
        if raw["error_type"] or raw["error"] or retryable is not None:
            raise WorkflowContractError("ok attempt must have empty error fields")
    elif status == "empty":
        if raw["error_type"] != "empty" or not raw["error"].strip():
            raise WorkflowContractError("empty attempt must carry a classified empty error")
    else:
        if not raw["error_type"].strip() or not raw["error"].strip():
            raise WorkflowContractError("error/skipped attempt requires error_type and error")
    return {
        "capability": raw["capability"],
        "provider": raw["provider"],
        "status": raw["status"],
        "error_type": raw["error_type"],
        "error": raw["error"],
        "elapsed_ms": raw["elapsed_ms"],
        "result_count": raw["result_count"],
        "retryable": retryable,
    }


def _artifact_from_raw(raw: Any) -> WorkflowArtifact:
    fields = (
        "id", "stage_id", "kind", "status", "name",
        "media_type", "byte_length", "digest",
    )
    _exact_keys(raw, fields, "artifact")
    _nonblank(raw["id"], "artifact.id")
    _nonblank(raw["stage_id"], "artifact.stage_id")
    if not isinstance(raw["kind"], str) or not _ARTIFACT_KIND_PATTERN.fullmatch(raw["kind"]):
        raise WorkflowContractError(f"artifact kind must match {_ARTIFACT_KIND_PATTERN.pattern!r}")
    _enum_value(raw["status"], ArtifactStatus, "artifact.status")
    validate_artifact_name(raw["name"])
    if not isinstance(raw["media_type"], str) or not _MEDIA_TYPE_PATTERN.fullmatch(raw["media_type"]):
        raise WorkflowContractError(f"artifact media_type must match media type syntax: {raw['media_type']!r}")
    _exact_int(raw["byte_length"], "artifact.byte_length")
    if not isinstance(raw["digest"], str) or not _DIGEST_PATTERN.fullmatch(raw["digest"]):
        raise WorkflowContractError("artifact digest must be a sha256 hex digest")
    return WorkflowArtifact(
        id=raw["id"],
        stage_id=raw["stage_id"],
        kind=raw["kind"],
        status=raw["status"],
        name=raw["name"],
        media_type=raw["media_type"],
        byte_length=raw["byte_length"],
        digest=raw["digest"],
    )


def _meta_from_raw(raw: Any) -> WorkflowMeta:
    fields = ("request_id", "duration_ms", "warnings")
    _exact_keys(raw, fields, "meta")
    _nonblank(raw["request_id"], "meta.request_id")
    _number(raw["duration_ms"], "meta.duration_ms")
    if not isinstance(raw["warnings"], list):
        raise WorkflowContractError("meta.warnings must be an array")
    for item in raw["warnings"]:
        if not isinstance(item, str):
            raise WorkflowContractError("meta.warnings must contain only strings")
    return WorkflowMeta(raw["request_id"], raw["duration_ms"], tuple(raw["warnings"]))


def _from_raw(raw: Mapping[str, Any]) -> WorkflowOutcome:
    present_forbidden = sorted(set(raw) & WORKFLOW_FORBIDDEN_TOP_LEVEL_FIELDS)
    if present_forbidden:
        raise WorkflowContractError(
            f"forbidden workflow field(s): {', '.join(present_forbidden)}"
        )
    _exact_keys(raw, WORKFLOW_TOP_LEVEL_FIELDS, "workflow result")
    if raw["schema_version"] != WORKFLOW_SCHEMA_VERSION:
        raise WorkflowContractError(
            f"unsupported workflow schema_version: {raw['schema_version']!r}"
        )
    if type(raw["ok"]) is not bool:
        raise WorkflowContractError("ok must be boolean")
    if raw["command"] != WORKFLOW_COMMAND:
        raise WorkflowContractError(f"command must be {WORKFLOW_COMMAND!r}")
    if raw["operation"] != WORKFLOW_OPERATION:
        raise WorkflowContractError(f"operation must be {WORKFLOW_OPERATION!r}")
    status = _enum_value(raw["status"], WorkflowStatus, "status")
    if raw["ok"] is not (status in (WorkflowStatus.COMPLETE, WorkflowStatus.DEGRADED)):
        raise WorkflowContractError("ok must be derived from status")
    try:
        plan = research_plan_from_dict(raw["plan"])
    except ResearchPlanError as exc:
        raise WorkflowContractError(f"invalid embedded research plan: {exc}") from exc
    try:
        stages = [_stage_from_raw(item) for item in _array(raw["stages"], "stages")]
        evidence = [_evidence_item_from_raw(item) for item in _array(raw["evidence"], "evidence")]
        citations = [_citation_from_raw(item) for item in _array(raw["citations"], "citations")]
        gaps = [_gap_from_raw(item) for item in _array(raw["gaps"], "gaps")]
        attempts = [_attempt_from_raw(item) for item in _array(raw["attempts"], "attempts")]
        artifacts = [_artifact_from_raw(item) for item in _array(raw["artifacts"], "artifacts")]
        error = None if raw["error"] is None else _error_from_raw(raw["error"])
        meta = _meta_from_raw(raw["meta"])
        outcome = WorkflowOutcome(
            status=status,
            plan=plan,
            stages=tuple(stages),
            evidence=tuple(_evidence_primitive(item) for item in evidence),
            citations=tuple(_citation_primitive(item) for item in citations),
            gaps=tuple(_gap_primitive(item) for item in gaps),
            attempts=tuple(_attempt_primitive(item) for item in attempts),
            artifacts=tuple(artifacts),
            error=error,
            meta=meta,
        )
    except WorkflowDomainError as exc:
        raise WorkflowContractError(str(exc)) from exc
    validate_workflow(outcome)
    try:
        json.dumps(dict(raw), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowContractError("workflow result must be JSON-compatible") from exc
    return outcome


def _evidence_primitive(item: dict[str, Any]) -> Any:
    from .execution_primitives import ExecutionEvidenceItem

    return ExecutionEvidenceItem(
        id=item["id"],
        resource=item["resource"],
        provider=item["provider"],
        title=item["title"],
        content=item["content"],
    )


def _citation_primitive(item: dict[str, str]) -> Any:
    from .execution_primitives import ExecutionCitation

    return ExecutionCitation(
        id=item["id"],
        evidence_id=item["evidence_id"],
        label=item["label"],
    )


def _gap_primitive(item: dict[str, Any]) -> Any:
    from .execution_primitives import ExecutionGap

    return ExecutionGap(
        code=item["code"],
        message=item["message"],
        capability=item["capability"],
        resource=item["resource"],
    )


def _attempt_primitive(item: dict[str, Any]) -> Any:
    from .execution_primitives import ExecutionAttempt, ExecutionError, ExecutionAttemptStatus

    status = ExecutionAttemptStatus(item["status"])
    if item["error_type"]:
        error = ExecutionError(
            item["error_type"],
            item["error"],
            item["retryable"] if item["retryable"] is not None else False,
        )
    else:
        error = None
    return ExecutionAttempt(
        capability=item["capability"],
        provider=item["provider"],
        status=status,
        error=error,
        elapsed_ms=item["elapsed_ms"],
        result_count=item["result_count"],
    )


def validate_workflow_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an untrusted raw workflow JSON object without runtime
    dependencies (no Evidence owners, Providers, config, or caches)."""
    _from_raw(raw)
    return dict(raw)


# ---------------------------------------------------------------------------
# Exit mapping and pre-dispatch results
# ---------------------------------------------------------------------------


def exit_code_for(
    result: WorkflowOutcome | Mapping[str, Any], *, fail_on_degraded: bool = False
) -> int:
    """Return the workflow exit policy, failing closed for unknown codes."""
    if isinstance(result, WorkflowOutcome):
        status = _value(result.status)
        error_code = _value(result.error.code) if result.error is not None else None
    elif isinstance(result, Mapping):
        status = result.get("status")
        raw_error = result.get("error")
        error_code = raw_error.get("code") if isinstance(raw_error, Mapping) else None
    else:
        return EXIT_INTERNAL
    if status == WorkflowStatus.COMPLETE.value:
        return EXIT_SUCCESS
    if status == WorkflowStatus.DEGRADED.value:
        return EXIT_DEGRADED if fail_on_degraded else EXIT_SUCCESS
    if status != WorkflowStatus.FAILED.value:
        return EXIT_INTERNAL
    try:
        return WORKFLOW_ERROR_EXIT_CODES[WorkflowErrorCode(error_code)]
    except (KeyError, TypeError, ValueError):
        return EXIT_INTERNAL


def workflow_parser_error_result(
    message: str,
    details: Mapping[str, Any] | None = None,
    *,
    request_id: str = "parser-error",
) -> WorkflowOutcome:
    """Build a pure pre-dispatch INVALID_ARGUMENT workflow result.

    It performs no owner, Provider, or config work: the plan is an empty
    validated ResearchPlan and every workflow collection is empty.
    """
    plan = ResearchPlan(RESEARCH_PLAN_SCHEMA_VERSION, ())
    outcome = WorkflowOutcome(
        status=WorkflowStatus.FAILED,
        plan=plan,
        stages=(),
        evidence=(),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=WorkflowError(
            WorkflowErrorCode.INVALID_ARGUMENT,
            message,
            False,
            dict(details or {}),
        ),
        meta=WorkflowMeta(request_id, 0),
    )
    return validate_workflow(outcome)


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------


def _strict_object(required: Sequence[str], properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": False,
        "properties": dict(properties),
    }


_NONBLANK = {"type": "string", "pattern": r"\S"}
_OPERATION = {"enum": sorted(WORKFLOW_EXECUTABLE_OPERATIONS)}
_ERROR_CODES = [code.value for code in WorkflowErrorCode]


def _error_schema() -> dict[str, Any]:
    schema = _strict_object(
        ("code", "message", "retryable", "details"),
        {
            "code": {"enum": _ERROR_CODES},
            "message": _NONBLANK,
            "retryable": {"type": "boolean"},
            "details": {"type": "object"},
        },
    )
    schema["oneOf"] = [
        {
            "properties": {"code": {"const": code.value}, "retryable": {"const": retryable}},
            "required": ["code", "retryable"],
        }
        for code, retryable in WORKFLOW_ERROR_RETRYABILITY.items()
    ]
    return schema


WORKFLOW_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://smart-search.local/schema/research-workflow.json",
    "x-smart-search-semantic-validator": "smart_search.research_workflow_contract.validate_workflow_dict",
    **_strict_object(
        WORKFLOW_TOP_LEVEL_FIELDS,
        {
            "schema_version": {"const": WORKFLOW_SCHEMA_VERSION},
            "ok": {"type": "boolean"},
            "status": {"enum": [status.value for status in WorkflowStatus]},
            "command": {"const": WORKFLOW_COMMAND},
            "operation": {"const": WORKFLOW_OPERATION},
            "plan": {"$ref": "#/$defs/plan"},
            "stages": {"type": "array", "items": {"$ref": "#/$defs/stage"}},
            "evidence": {"type": "array", "items": {"$ref": "#/$defs/evidence_item"}},
            "citations": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
            "gaps": {"type": "array", "items": {"$ref": "#/$defs/gap"}},
            "attempts": {"type": "array", "items": {"$ref": "#/$defs/attempt"}},
            "artifacts": {"type": "array", "items": {"$ref": "#/$defs/artifact"}},
            "error": {"oneOf": [{"$ref": "#/$defs/error"}, {"type": "null"}]},
            "meta": {"$ref": "#/$defs/meta"},
        },
    ),
    "$defs": {},
}

_defs = WORKFLOW_JSON_SCHEMA["$defs"]
_defs["error"] = _error_schema()
_defs["plan"] = _strict_object(
    ("schema_version", "operations"),
    {
        "schema_version": {"const": RESEARCH_PLAN_SCHEMA_VERSION},
        "operations": {"type": "array"},
    },
)
_defs["stage"] = _strict_object(
    (
        "id", "operation", "status", "order", "depends_on", "input",
        "result_count", "evidence_ids", "artifact_ids", "error",
    ),
    {
        "id": _NONBLANK,
        "operation": _OPERATION,
        "status": {"enum": [status.value for status in WorkflowStageStatus]},
        "order": {"type": "integer", "minimum": 1},
        "depends_on": {"type": "array", "items": _NONBLANK, "uniqueItems": True},
        "input": {"type": "object"},
        "result_count": {"type": "integer", "minimum": 0},
        "evidence_ids": {"type": "array", "items": _NONBLANK},
        "artifact_ids": {"type": "array", "items": _NONBLANK},
        "error": {"oneOf": [{"$ref": "#/$defs/error"}, {"type": "null"}]},
    },
)
_defs["evidence_item"] = _strict_object(
    ("id", "resource", "provider", "title", "content"),
    {
        "id": _NONBLANK,
        "resource": _NONBLANK,
        "provider": _NONBLANK,
        "title": {"type": "string"},
        "content": _NONBLANK,
    },
)
_defs["citation"] = _strict_object(
    ("id", "evidence_id", "label"),
    {"id": _NONBLANK, "evidence_id": _NONBLANK, "label": _NONBLANK},
)
_defs["gap"] = _strict_object(
    ("code", "message", "capability", "resource"),
    {
        "code": _NONBLANK,
        "message": _NONBLANK,
        "capability": {"oneOf": [_OPERATION, {"const": ""}]},
        "resource": {"type": "string"},
    },
)
_defs["attempt"] = _strict_object(
    (
        "capability", "provider", "status", "error_type", "error",
        "elapsed_ms", "result_count", "retryable",
    ),
    {
        "capability": _OPERATION,
        "provider": _NONBLANK,
        "status": {"enum": list(_ATTEMPT_STATUSES)},
        "error_type": {"type": "string"},
        "error": {"type": "string"},
        "elapsed_ms": {"type": "number", "minimum": 0},
        "result_count": {"type": "integer", "minimum": 0},
        "retryable": {"oneOf": [{"type": "boolean"}, {"type": "null"}]},
    },
)
_defs["artifact"] = _strict_object(
    (
        "id", "stage_id", "kind", "status", "name",
        "media_type", "byte_length", "digest",
    ),
    {
        "id": _NONBLANK,
        "stage_id": _NONBLANK,
        "kind": {"type": "string", "pattern": _ARTIFACT_KIND_PATTERN.pattern},
        "status": {"enum": [status.value for status in ArtifactStatus]},
        "name": {"type": "string", "pattern": _ARTIFACT_NAME_PATTERN.pattern},
        "media_type": {"type": "string", "pattern": _MEDIA_TYPE_PATTERN.pattern},
        "byte_length": {"type": "integer", "minimum": 0},
        "digest": {"type": "string", "pattern": _DIGEST_PATTERN.pattern},
    },
)
_defs["meta"] = _strict_object(
    ("request_id", "duration_ms", "warnings"),
    {
        "request_id": _NONBLANK,
        "duration_ms": {"type": "number", "minimum": 0},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
)


__all__ = [
    "EXIT_CONFIGURATION",
    "EXIT_DEGRADED",
    "EXIT_INTERNAL",
    "EXIT_INVALID_ARGUMENT",
    "EXIT_OK",
    "EXIT_SUCCESS",
    "EXIT_UPSTREAM",
    "WORKFLOW_COMMAND",
    "WORKFLOW_JSON_SCHEMA",
    "WORKFLOW_OPERATION",
    "WORKFLOW_SCHEMA_VERSION",
    "WORKFLOW_TOP_LEVEL_FIELDS",
    "WorkflowContractError",
    "exit_code_for",
    "serialize_workflow",
    "validate_workflow",
    "validate_workflow_dict",
    "workflow_parser_error_result",
]