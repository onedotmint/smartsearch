from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from smart_search.v2_contract import (
    ERROR_EXIT_CODES,
    ERROR_RETRYABILITY,
    EXIT_DEGRADED,
    EXIT_INTERNAL,
    V2Attempt,
    V2AttemptStatus,
    V2Candidate,
    V2Citation,
    V2ContractError,
    V2Degradation,
    V2Envelope,
    V2Error,
    V2ErrorCode,
    V2Evidence,
    V2EvidenceItem,
    V2Gap,
    V2Meta,
    V2Routing,
    V2Status,
    V2TraceEvent,
    V2_CAPABILITY_OPERATION_IDS,
    V2_ENVELOPE_JSON_SCHEMA,
    V2_ENVELOPE_OPERATION_IDS,
    V2_META_OPERATION_CAPABILITY_STATUS,
    V2_META_OPERATION_IDS,
    V2_OPERATION_IDS,
    V2_SCHEMA_VERSION,
    V2_TOP_LEVEL_FIELDS,
    capability_status_result,
    exit_code_for,
    parser_error_result,
    safe_trace,
    serialize_result,
    validate_envelope_dict,
    validate_result,
)

ROOT = Path(__file__).parents[1]
SCHEMA_VALIDATOR = Draft202012Validator(V2_ENVELOPE_JSON_SCHEMA)


def envelope(
    status: V2Status = V2Status.COMPLETE,
    *,
    operation: str | None = "source_discovery",
    result: dict | None = None,
    evidence: V2Evidence | None = None,
    routing: V2Routing | None = None,
    attempts: tuple[V2Attempt, ...] = (),
    degradation: tuple[V2Degradation, ...] = (),
    error: V2Error | None = None,
) -> V2Envelope:
    return V2Envelope(
        status=status,
        command="search",
        operation=operation,
        result={"total": 0} if result is None else result,
        evidence=evidence or V2Evidence(),
        routing=routing or V2Routing(
            requested_capabilities=(operation,) if operation else (),
            executed_capabilities=(operation,) if operation else (),
            policy_version="v2-test-1",
            reason_codes=("requested",) if operation else (),
        ),
        attempts=attempts,
        degradation=degradation,
        error=error,
        meta=V2Meta(request_id="req-1", duration_ms=7),
    )


def failed(code: V2ErrorCode, *, operation: str | None = "source_discovery") -> V2Envelope:
    routing = None
    if operation is None:
        routing = V2Routing((), (), "v2-parser-1", ("invalid_argument",))
    return envelope(
        V2Status.FAILED,
        operation=operation,
        routing=routing,
        degradation=(),
        error=V2Error(code, f"failure: {code.value}", ERROR_RETRYABILITY[code], {}),
    )


@pytest.fixture
def complete_empty() -> V2Envelope:
    return envelope(result={"total": 0, "items": []})


@pytest.fixture
def complete_results() -> V2Envelope:
    item = V2EvidenceItem("ev-1", "https://example.com/a", "tavily", "A", "Fetched body")
    return envelope(
        result={"total": 1, "items": [{"id": "result-1"}]},
        evidence=V2Evidence(
            items=(item,), citations=(V2Citation("cite-1", "ev-1", "Example A"),),
        ),
        attempts=(V2Attempt("source_discovery", "tavily", "ok", None, 4, 1),),
    )


@pytest.fixture
def degraded_partial() -> V2Envelope:
    return envelope(
        V2Status.DEGRADED,
        result={"total": 1},
        evidence=V2Evidence(gaps=(V2Gap("provider_partial", "One provider failed", "source_discovery"),)),
        attempts=(
            V2Attempt("source_discovery", "tavily", "error", "PROVIDER_UNAVAILABLE", 5, 0),
            V2Attempt("source_discovery", "firecrawl", "ok", None, 8, 1),
        ),
        degradation=(V2Degradation("provider_partial", "source_discovery", "Fallback result used"),),
    )


@pytest.fixture
def failed_argument() -> V2Envelope:
    return failed(V2ErrorCode.INVALID_ARGUMENT, operation=None)


@pytest.fixture
def failed_config() -> V2Envelope:
    return failed(V2ErrorCode.CONFIGURATION_ERROR)


@pytest.fixture
def failed_provider() -> V2Envelope:
    return envelope(
        V2Status.FAILED,
        attempts=(V2Attempt("source_discovery", "tavily", "error", "PROVIDER_UNAVAILABLE", 2, 0),),
        error=V2Error("PROVIDER_UNAVAILABLE", "Provider failed", True, {}),
    )


def test_schema_is_draft_2020_12_and_has_strict_shapes():
    Draft202012Validator.check_schema(V2_ENVELOPE_JSON_SCHEMA)
    assert V2_ENVELOPE_JSON_SCHEMA["$schema"].endswith("2020-12/schema")
    assert V2_ENVELOPE_JSON_SCHEMA["x-smart-search-semantic-validator"] == (
        "smart_search.v2_contract.validate_envelope_dict"
    )
    assert V2_ENVELOPE_JSON_SCHEMA["required"] == list(V2_TOP_LEVEL_FIELDS)
    assert V2_ENVELOPE_JSON_SCHEMA["additionalProperties"] is False
    for name in (
        "error", "candidate", "evidence_item", "citation", "gap", "evidence",
        "routing", "attempt", "degradation", "meta", "trace", "trace_event",
    ):
        assert V2_ENVELOPE_JSON_SCHEMA["$defs"][name]["additionalProperties"] is False


def test_v2_operation_ids_match_phase_1_taxonomy():
    from smart_search.capability_taxonomy import V2_CAPABILITY_IDS

    assert V2_OPERATION_IDS == V2_CAPABILITY_IDS
    assert V2_CAPABILITY_OPERATION_IDS == V2_CAPABILITY_IDS
    assert V2_META_OPERATION_IDS == (V2_META_OPERATION_CAPABILITY_STATUS,)
    assert V2_ENVELOPE_OPERATION_IDS == V2_CAPABILITY_OPERATION_IDS + V2_META_OPERATION_IDS
    assert V2_META_OPERATION_CAPABILITY_STATUS not in V2_CAPABILITY_OPERATION_IDS
    assert V2_META_OPERATION_CAPABILITY_STATUS not in V2_CAPABILITY_IDS


def test_importing_v2_contract_does_not_load_config_or_create_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["SMART_SEARCH_CONFIG_DIR"] = str(config_dir)
    script = """
import sys
import smart_search.v2_contract
for name in (
    'smart_search.capability_taxonomy',
    'smart_search.logger',
    'smart_search.config',
):
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, check=True)
    assert not config_dir.exists()


@pytest.mark.parametrize(
    "fixture_name",
    ["complete_empty", "complete_results", "degraded_partial", "failed_argument", "failed_config", "failed_provider"],
)
def test_required_fixtures_validate_locally_and_with_standard_schema(request, fixture_name):
    model = request.getfixturevalue(fixture_name)
    output = serialize_result(model)
    validate_envelope_dict(output)
    SCHEMA_VALIDATOR.validate(output)
    json.dumps(output)


def test_top_level_order_exact_shape_and_no_v1_aliases(complete_results):
    output = serialize_result(complete_results)
    assert tuple(output) == V2_TOP_LEVEL_FIELDS
    assert len(output) == 12
    assert not {"data", "error_detail", "error_type", "provider_attempts", "content"} & set(output)
    assert output["schema_version"] == "2"


@pytest.mark.parametrize(
    ("status", "error", "degradation"),
    [
        (V2Status.COMPLETE, V2Error("INTERNAL_ERROR", "bad", False), ()),
        (V2Status.COMPLETE, None, (V2Degradation("partial", "", "bad"),)),
        (V2Status.DEGRADED, None, ()),
        (V2Status.DEGRADED, V2Error("INTERNAL_ERROR", "bad", False), (V2Degradation("partial", "", "bad"),)),
        (V2Status.FAILED, None, ()),
        (V2Status.FAILED, V2Error("INTERNAL_ERROR", "bad", False), (V2Degradation("partial", "", "bad"),)),
    ],
)
def test_state_machine_rejects_every_cross_invalid_combination(status, error, degradation):
    with pytest.raises(V2ContractError):
        validate_result(envelope(status, error=error, degradation=degradation))


def test_raw_state_matrix_is_rejected_by_local_and_standard_schema(complete_empty):
    valid = serialize_result(complete_empty)
    mutations = []
    for status, ok, error, degradation in (
        ("complete", False, None, []),
        ("complete", True, {"code": "INTERNAL_ERROR", "message": "x", "retryable": False, "details": {}}, []),
        ("degraded", True, None, []),
        ("degraded", False, None, [{"code": "partial", "capability": "", "message": "x"}]),
        ("failed", False, None, []),
        ("failed", False, {"code": "INTERNAL_ERROR", "message": "x", "retryable": False, "details": {}}, [{"code": "partial", "capability": "", "message": "x"}]),
        ("unknown", False, None, []),
    ):
        raw = copy.deepcopy(valid)
        raw.update(status=status, ok=ok, error=error, degradation=degradation)
        mutations.append(raw)
    for raw in mutations:
        with pytest.raises(V2ContractError):
            validate_envelope_dict(raw)
        assert list(SCHEMA_VALIDATOR.iter_errors(raw))


def test_standard_schema_defers_cross_object_semantics_to_local_validator(
    complete_empty, complete_results,
):
    dangling = serialize_result(complete_results)
    dangling["evidence"]["citations"][0]["evidence_id"] = "missing"

    duplicate = serialize_result(complete_results)
    duplicate["evidence"]["items"].append(copy.deepcopy(duplicate["evidence"]["items"][0]))

    collision = serialize_result(complete_results)
    collision["evidence"]["candidates"].append({
        "id": "ev-1",
        "resource": "https://candidate.example",
        "provider": "tavily",
        "title": "Candidate",
        "snippet": "",
    })

    unrequested = serialize_result(complete_empty)
    unrequested["routing"]["executed_capabilities"] = ["content_fetch"]

    for raw in (dangling, duplicate, collision, unrequested):
        # Draft 2020-12 validates structure and the terminal-state truth table.
        # Cross-array references and set-subset relations require the mandatory
        # Smart Search semantic validator named by the Schema annotation.
        SCHEMA_VALIDATOR.validate(raw)
        with pytest.raises(V2ContractError):
            validate_envelope_dict(raw)


def test_complete_empty_is_not_automatically_degraded(complete_empty):
    output = serialize_result(complete_empty)
    assert output["status"] == "complete"
    assert output["ok"] is True
    assert output["result"]["total"] == 0
    assert output["degradation"] == []


@pytest.mark.parametrize("attempt_status", ["error", "skipped"])
def test_complete_rejects_failed_or_skipped_attempts(complete_empty, attempt_status):
    attempt = V2Attempt(
        "source_discovery", "tavily", attempt_status,
        "PROVIDER_UNAVAILABLE", 1, 0,
    )
    with pytest.raises(V2ContractError):
        validate_result(envelope(attempts=(attempt,)))

    raw = serialize_result(complete_empty)
    raw["attempts"] = [{
        "capability": "source_discovery",
        "provider": "tavily",
        "status": attempt_status,
        "error_code": "PROVIDER_UNAVAILABLE",
        "elapsed_ms": 1,
        "result_count": 0,
    }]
    with pytest.raises(V2ContractError):
        validate_envelope_dict(raw)
    assert list(SCHEMA_VALIDATOR.iter_errors(raw))


def test_integral_numbers_match_json_schema_integer_semantics(complete_empty):
    raw = serialize_result(complete_empty)
    raw["meta"]["duration_ms"] = 1.0
    validate_envelope_dict(raw)
    SCHEMA_VALIDATOR.validate(raw)


def test_ok_is_derived_and_model_is_recursively_immutable(complete_results):
    assert complete_results.ok is True
    with pytest.raises(FrozenInstanceError):
        complete_results.status = V2Status.FAILED
    with pytest.raises(TypeError):
        complete_results.result["total"] = 3
    nested = envelope(result={"nested": {"values": [1, 2]}})
    with pytest.raises(TypeError):
        nested.result["nested"]["other"] = 1
    assert nested.result["nested"]["values"] == (1, 2)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: V2Meta("req", 0, warnings="warning"),
        lambda: V2Routing(reason_codes="reason"),
        lambda: V2TraceEvent(reason_codes="reason"),
        lambda: V2Envelope(
            V2Status.COMPLETE, "search", "source_discovery", {},
            V2Evidence(), V2Routing(), attempts="attempt", degradation=(),
            error=None, meta=V2Meta("req", 0),
        ),
    ],
)
def test_scalar_strings_are_not_split_into_tuple_collections(factory):
    with pytest.raises(V2ContractError):
        factory()


def test_serializer_returns_defensive_fresh_copies(complete_results):
    first = serialize_result(complete_results)
    first["result"]["items"].append({"id": "mutated"})
    first["evidence"]["items"][0]["content"] = "mutated"
    second = serialize_result(complete_results)
    assert second["result"]["items"] == [{"id": "result-1"}]
    assert second["evidence"]["items"][0]["content"] == "Fetched body"


@pytest.mark.parametrize(
    "evidence",
    [
        V2Evidence(candidates=(V2Candidate("candidate-1", "https://a", "tavily", "A"),), citations=(V2Citation("c", "candidate-1", "A"),)),
        V2Evidence(items=(V2EvidenceItem("ev", "", "tavily", "A", "body"),)),
        V2Evidence(items=(V2EvidenceItem("ev", "https://a", "", "A", "body"),)),
        V2Evidence(items=(V2EvidenceItem("ev", "https://a", "tavily", "A", "   "),)),
        V2Evidence(items=(V2EvidenceItem("ev", "https://a", "tavily", "A", "body"),), citations=(V2Citation("c", "missing", "A"),)),
        V2Evidence(items=(V2EvidenceItem("ev", "https://a", "tavily", "A", "body"), V2EvidenceItem("ev", "https://b", "jina", "B", "body"))),
    ],
)
def test_evidence_provenance_and_citation_invariants(evidence):
    with pytest.raises(V2ContractError):
        validate_result(envelope(evidence=evidence))


def test_candidate_requires_identity_provenance_and_display_text():
    for candidate in (
        V2Candidate("", "resource", "provider", "title"),
        V2Candidate("id", "", "provider", "title"),
        V2Candidate("id", "resource", "", "title"),
        V2Candidate("id", "resource", "provider", "", ""),
    ):
        with pytest.raises(V2ContractError):
            validate_result(envelope(evidence=V2Evidence(candidates=(candidate,))))


def test_routing_and_attempt_whitelists_reject_unknown_fields(complete_empty):
    raw = serialize_result(complete_empty)
    raw["routing"]["score"] = 0.9
    with pytest.raises(V2ContractError):
        validate_envelope_dict(raw)
    assert list(SCHEMA_VALIDATOR.iter_errors(raw))

    raw = serialize_result(complete_empty)
    raw["attempts"].append({
        "capability": "source_discovery", "provider": "tavily", "status": "ok",
        "error_code": None, "elapsed_ms": 1, "result_count": 1,
        "extra": {"api_key": "leak"},
    })
    with pytest.raises(V2ContractError):
        validate_envelope_dict(raw)
    assert list(SCHEMA_VALIDATOR.iter_errors(raw))


@pytest.mark.parametrize(
    "attempt",
    [
        V2Attempt("source_discovery", "tavily", "ok", "FETCH_FAILED", 1, 1),
        V2Attempt("source_discovery", "tavily", "empty", "FETCH_FAILED", 1, 0),
        V2Attempt("source_discovery", "tavily", "error", None, 1, 0),
        V2Attempt("source_discovery", "tavily", "skipped", "UNKNOWN", 1, 0),
        V2Attempt("source_discovery", "tavily", "pending", None, 1, 0),
        V2Attempt("source_discovery", "tavily", "ok", None, -1, 1),
    ],
)
def test_attempt_state_invariants(attempt):
    with pytest.raises(V2ContractError):
        validate_result(envelope(attempts=(attempt,)))


def test_trace_is_opt_in_whitelisted_and_recursively_redacted(complete_empty):
    secret = "super-secret-value"
    trace = {"events": [{
        "operation": "source_discovery",
        "capability": "source_discovery",
        "provider": "Bearer token-123 Basic basic-456",
        "status": f"api_key={secret}",
        "error_code": "",
        "evidence_id": "ev-1",
        "reason_codes": [f"https://user:pass@example.com/a?api_key={secret}#token={secret}"],
        "elapsed_ms": 1,
        "extra": {"authorization": secret},
        "request_body": secret,
        "response_body": secret,
        "embedding": [0.1],
        "config": {"password": secret},
    }]}
    without_trace = serialize_result(complete_empty)
    assert "trace" not in without_trace["meta"]
    output = serialize_result(complete_empty, trace=trace, secrets=(secret,))
    event = output["meta"]["trace"]["events"][0]
    assert tuple(event) == (
        "operation", "capability", "provider", "status", "error_code",
        "evidence_id", "reason_codes", "elapsed_ms",
    )
    assert not {"extra", "request_body", "response_body", "embedding", "config"} & set(event)
    rendered = json.dumps(output)
    assert secret not in rendered
    assert "token-123" not in rendered
    assert "basic-456" not in rendered
    assert "user:pass" not in rendered
    assert "%5BREDACTED%5D" in rendered


def test_safe_trace_accepts_typed_events_and_returns_fresh_data():
    event = V2TraceEvent(operation="content_fetch", capability="content_fetch", provider="jina", status="ok", elapsed_ms=3)
    first = safe_trace((event,))
    first["events"][0]["provider"] = "changed"
    assert safe_trace((event,))["events"][0]["provider"] == "jina"


@pytest.mark.parametrize(
    "event",
    [
        {"operation": "legacy_capability"},
        {"operation": "source_discovery", "elapsed_ms": -1},
        {"operation": 3},
        {"operation": "source_discovery", "reason_codes": "not-an-array"},
    ],
)
def test_safe_trace_rejects_schema_invalid_whitelisted_values(event):
    with pytest.raises(V2ContractError):
        safe_trace({"events": [event]})


def test_explicit_secret_inputs_are_snapshotted_before_recursive_redaction(complete_empty):
    secret = "opaque-secret-value"
    model = replace(complete_empty, result={"note": secret})
    for secrets in ((item for item in (secret,)), secret, [secret]):
        assert serialize_result(model, secrets=secrets)["result"]["note"] == "[REDACTED]"
    for secrets in ((item for item in (secret,)), secret, [secret]):
        trace = safe_trace({"events": [{"status": secret}]}, secrets=secrets)
        assert trace["events"][0]["status"] == "[REDACTED]"


def test_final_boundary_redacts_result_error_details_and_meta():
    secret = "private-api-key"
    model = failed(V2ErrorCode.INTERNAL_ERROR)
    model = replace(
        model,
        result={"nested": {"api_key": secret, "message": f"Bearer {secret}"}},
        error=V2Error("INTERNAL_ERROR", f"Basic {secret}", False, {"client_secret": secret}),
        meta=V2Meta("req-1", 1, (f"https://u:p@example.test/a?token={secret}",)),
    )
    rendered = json.dumps(serialize_result(model, secrets=(secret,)))
    assert secret not in rendered
    assert "u:p" not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.parametrize("code", list(V2ErrorCode))
def test_complete_error_registry_retryability_schema_and_exit(code):
    model = failed(code)
    output = serialize_result(model)
    assert output["error"]["retryable"] is ERROR_RETRYABILITY[code]
    assert exit_code_for(model) == ERROR_EXIT_CODES[code]
    SCHEMA_VALIDATOR.validate(output)


@pytest.mark.parametrize("code", list(V2ErrorCode))
def test_retryability_cannot_be_overridden(code):
    model = failed(code)
    assert model.error is not None
    contradictory = replace(model, error=replace(model.error, retryable=not ERROR_RETRYABILITY[code]))
    with pytest.raises(V2ContractError):
        validate_result(contradictory)
    raw = serialize_result(model)
    raw["error"]["retryable"] = not ERROR_RETRYABILITY[code]
    with pytest.raises(V2ContractError):
        validate_envelope_dict(raw)
    assert list(SCHEMA_VALIDATOR.iter_errors(raw))


def test_exit_policy_degraded_switch_does_not_mutate_envelope(degraded_partial):
    before = serialize_result(degraded_partial)
    assert exit_code_for(degraded_partial) == 0
    assert exit_code_for(degraded_partial, fail_on_degraded=True) == EXIT_DEGRADED
    assert serialize_result(degraded_partial) == before
    assert exit_code_for("UNKNOWN_CODE") == EXIT_INTERNAL
    assert exit_code_for({"status": "failed", "error": {"code": "UNKNOWN_CODE"}}) == EXIT_INTERNAL
    assert exit_code_for({"status": "unknown"}) == EXIT_INTERNAL


def test_unknown_error_codes_are_rejected_not_coerced():
    model = failed(V2ErrorCode.INTERNAL_ERROR)
    assert model.error is not None
    with pytest.raises(V2ContractError):
        validate_result(replace(model, error=replace(model.error, code="UNKNOWN")))
    raw = serialize_result(model)
    raw["error"]["code"] = "UNKNOWN"
    with pytest.raises(V2ContractError):
        validate_envelope_dict(raw)
    assert list(SCHEMA_VALIDATOR.iter_errors(raw))


def test_parser_error_known_and_null_operation_are_pure(monkeypatch):
    imported = []
    original_import = __import__

    def spy(name, *args, **kwargs):
        if name.startswith(("smart_search.service", "smart_search.config", "smart_search.providers", "httpx")):
            imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", spy)
    known = parser_error_result("search", "source_discovery", "bad query", {"argument": "query"})
    unknown = parser_error_result("smart-search", None, "unknown command")
    assert imported == []
    for model in (known, unknown):
        assert model.status is V2Status.FAILED
        assert model.error.code is V2ErrorCode.INVALID_ARGUMENT
        assert exit_code_for(model) == 2
        SCHEMA_VALIDATOR.validate(serialize_result(model))
    assert known.routing.requested_capabilities == ("source_discovery",)
    assert unknown.routing.requested_capabilities == ()
    assert unknown.attempts == ()


@pytest.mark.parametrize(
    ("operation", "code", "requested", "attempts"),
    [
        (None, V2ErrorCode.CONFIGURATION_ERROR, (), ()),
        (None, V2ErrorCode.INVALID_ARGUMENT, ("source_discovery",), ()),
        (None, V2ErrorCode.INVALID_ARGUMENT, (), (V2Attempt("source_discovery", "tavily", "error", "INVALID_ARGUMENT", 0, 0),)),
        ("legacy_main_search", V2ErrorCode.INVALID_ARGUMENT, (), ()),
    ],
)
def test_parser_operation_null_sentinel_is_strict(operation, code, requested, attempts):
    model = envelope(
        V2Status.FAILED,
        operation=operation,
        routing=V2Routing(requested, (), "parser", ()),
        attempts=attempts,
        error=V2Error(code, "bad", ERROR_RETRYABILITY[code]),
    )
    with pytest.raises(V2ContractError):
        validate_result(model)


def test_non_json_result_and_details_are_rejected():
    for bad_result in ({"bad": object()}, {1: "non-string key"}, {"bad": float("nan")}):
        with pytest.raises(V2ContractError):
            validate_result(envelope(result=bad_result))
    model = failed(V2ErrorCode.INTERNAL_ERROR)
    assert model.error is not None
    for bad_details in ({"bad": object()}, {1: "non-string key"}):
        with pytest.raises(V2ContractError):
            validate_result(replace(model, error=replace(model.error, details=bad_details)))


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            imports.add(prefix + (node.module or ""))
    return imports


def test_v2_contract_import_isolation_is_bidirectional():
    # The v1 dispatch/contract/facade modules are removed; the stdlib-only
    # routing layer (cli, cli_parser) must not import v2_contract. The typed
    # v2 CLI boundary (cli_v2) is the only CLI module that may.
    for name in ("cli.py", "cli_parser.py"):
        imports = imported_modules(ROOT / "src" / "smart_search" / name)
        assert not any("v2_contract" in item for item in imports), name
    for name in (
        "cli_dispatch.py",
        "cli_contract.py",
        "cli_render.py",
        "cli_setup.py",
        "cli_support.py",
        "service.py",
        "search_service.py",
        "operations_service.py",
    ):
        assert not (ROOT / "src" / "smart_search" / name).exists(), name
    imports = imported_modules(ROOT / "src" / "smart_search" / "v2_contract.py")
    allowed_local = {".security"}
    assert {item for item in imports if item.startswith(".")} <= allowed_local
    assert not any(fragment in item for item in imports for fragment in ("cli", "service", "config", "provider"))


def test_v1_service_facade_is_removed_and_free_of_v2_exports():
    import pytest as _pytest

    from smart_search import cli_parser
    from smart_search.v2_contract import V2_SCHEMA_VERSION

    parser = cli_parser.build_parser()
    help_text = parser.format_help()
    # The schema selector is fully removed from the parser surface.
    assert "--schema-version" not in help_text
    assert not any(
        "--schema-version" in action.option_strings for action in parser._actions
    )
    assert V2_SCHEMA_VERSION == "2"
    # The v1 facade module is deleted; no v2 symbol may be re-exported from a
    # legacy facade module.
    with _pytest.raises(ImportError):
        import smart_search.service  # noqa: F401
    with _pytest.raises(ImportError):
        from smart_search import cli_contract  # noqa: F401


def test_raw_unknown_fields_and_types_are_rejected(complete_empty):
    base = serialize_result(complete_empty)
    cases = []
    raw = copy.deepcopy(base); raw["data"] = {}; cases.append(raw)
    raw = copy.deepcopy(base); raw["result"] = []; cases.append(raw)
    raw = copy.deepcopy(base); raw["meta"]["unknown"] = True; cases.append(raw)
    raw = copy.deepcopy(base); raw["routing"]["requested_capabilities"] = ["web_search"]; cases.append(raw)
    raw = copy.deepcopy(base); raw["meta"]["duration_ms"] = True; cases.append(raw)
    raw = copy.deepcopy(base); raw["evidence"]["items"] = [{
        "id": "ev", "resource": "resource", "provider": "tavily",
        "title": "Title", "content": "body", "verified": True,
    }]; cases.append(raw)
    for raw in cases:
        with pytest.raises(V2ContractError):
            validate_envelope_dict(raw)
        assert list(SCHEMA_VALIDATOR.iter_errors(raw))


def test_capability_status_complete_and_failed_are_valid():
    complete = capability_status_result(
        result={"capabilities": {"source_discovery": {"qualified_providers": ["tavily"]}}},
        reason_codes=("local_inspection",),
    )
    assert complete.operation == V2_META_OPERATION_CAPABILITY_STATUS
    assert complete.command == "capabilities"
    assert complete.status == V2Status.COMPLETE
    assert complete.attempts == ()
    assert complete.degradation == ()
    assert complete.evidence.candidates == ()
    assert complete.routing.requested_capabilities == ()
    output = serialize_result(complete)
    validate_envelope_dict(output)
    SCHEMA_VALIDATOR.validate(output)

    failed = capability_status_result(
        status=V2Status.FAILED,
        error=V2Error(V2ErrorCode.CONFIGURATION_ERROR, "no config", False, {}),
    )
    assert failed.operation == V2_META_OPERATION_CAPABILITY_STATUS
    failed_out = serialize_result(failed)
    validate_envelope_dict(failed_out)
    SCHEMA_VALIDATOR.validate(failed_out)

    arg_failed = capability_status_result(
        status=V2Status.FAILED,
        error=V2Error(V2ErrorCode.INVALID_ARGUMENT, "bad arg", False, {"flag": "x"}),
    )
    assert arg_failed.operation == V2_META_OPERATION_CAPABILITY_STATUS
    serialize_result(arg_failed)


def test_capability_status_rejects_degraded_and_nonempty_shape():
    with pytest.raises(V2ContractError):
        capability_status_result(status=V2Status.DEGRADED)

    model = capability_status_result()
    with pytest.raises(V2ContractError):
        validate_result(replace(
            model,
            evidence=V2Evidence(candidates=(V2Candidate("c1", "https://x", "tavily", "T", ""),)),
        ))
    with pytest.raises(V2ContractError):
        validate_result(replace(
            model,
            attempts=(V2Attempt("source_discovery", "tavily", "ok", None, 1, 0),),
        ))
    with pytest.raises(V2ContractError):
        validate_result(replace(
            model,
            routing=V2Routing(("source_discovery",), (), "v2", ()),
        ))
    with pytest.raises(V2ContractError):
        validate_result(replace(model, command="search"))


def test_capability_status_forbidden_in_capability_bearing_fields():
    with pytest.raises(V2ContractError):
        validate_result(envelope(
            routing=V2Routing(
                (V2_META_OPERATION_CAPABILITY_STATUS,),
                (V2_META_OPERATION_CAPABILITY_STATUS,),
                "v2",
                ("x",),
            ),
        ))
    with pytest.raises(V2ContractError):
        validate_result(envelope(
            attempts=(V2Attempt(V2_META_OPERATION_CAPABILITY_STATUS, "local", "ok", None, 1, 0),),
        ))
    with pytest.raises(V2ContractError):
        validate_result(envelope(
            V2Status.DEGRADED,
            degradation=(V2Degradation("partial", V2_META_OPERATION_CAPABILITY_STATUS, "bad"),),
        ))
    with pytest.raises(V2ContractError):
        validate_result(envelope(
            evidence=V2Evidence(gaps=(V2Gap("g", "msg", V2_META_OPERATION_CAPABILITY_STATUS),)),
        ))
    with pytest.raises(V2ContractError):
        safe_trace([{"operation": V2_META_OPERATION_CAPABILITY_STATUS, "capability": "", "provider": "",
                     "status": "", "error_code": "", "evidence_id": "", "reason_codes": [], "elapsed_ms": 0}])
    with pytest.raises(V2ContractError):
        safe_trace([{"operation": "", "capability": V2_META_OPERATION_CAPABILITY_STATUS, "provider": "",
                     "status": "", "error_code": "", "evidence_id": "", "reason_codes": [], "elapsed_ms": 0}])


def test_identified_capabilities_parser_error_keeps_capability_status_operation():
    model = parser_error_result(
        "capabilities",
        V2_META_OPERATION_CAPABILITY_STATUS,
        "unknown flag for capabilities",
    )
    assert model.operation == V2_META_OPERATION_CAPABILITY_STATUS
    assert model.routing.requested_capabilities == ()
    assert model.routing.executed_capabilities == ()
    assert model.attempts == ()
    output = serialize_result(model)
    SCHEMA_VALIDATOR.validate(output)


def test_unidentified_parser_error_uses_operation_null():
    model = parser_error_result("unknown", None, "unrecognized arguments")
    assert model.operation is None
    assert model.routing.requested_capabilities == ()
    output = serialize_result(model)
    SCHEMA_VALIDATOR.validate(output)
