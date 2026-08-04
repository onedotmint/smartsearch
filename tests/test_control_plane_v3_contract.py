from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from smart_search.control_plane_contract import (
    ERROR_EXIT_CODES,
    ERROR_RETRYABILITY,
    V3_ENVELOPE_JSON_SCHEMA,
    V3Error,
    V3ErrorCode,
    V3Status,
    V3ContractError,
    V3Mutation,
    V3Network,
    V3SideEffects,
    V3Envelope,
    V3Meta,
    V3_OPERATION_IDS,
    V3_TOP_LEVEL_FIELDS,
    exit_code_for,
    operation_for_argv,
    parser_error_result,
    serialize_result,
    validate_envelope_dict,
)
from tests.fixtures.control_plane_v3 import (
    complete_config_list,
    complete_config_write,
    complete_empty_catalog,
    degraded_probe,
    failed_filesystem,
    failed_local_configuration,
    failed_subprocess,
)

ROOT = Path(__file__).parents[1]


def test_inventory_has_stable_control_plane_operation_ids_and_canonical_paths():
    assert V3_OPERATION_IDS == (
        "config.path", "config.list", "config.set", "config.unset",
        "provider.catalog.list", "provider.catalog.status", "provider.probe",
        "provider.routes.current", "provider.routes.list", "provider.routes.add",
        "provider.routes.remove", "doctor.status", "doctor.probe",
        "dev.route.explain", "dev.route.calibrate", "dev.diagnose.openai-compatible",
        "dev.smoke", "dev.regression", "dev.skills.status", "dev.skills.update",
    )
    assert operation_for_argv(["--schema-version", "3", "provider", "routes", "list"]).operation == "provider.routes.list"
    assert operation_for_argv(["--schema-version", "3", "cfg", "list"]) is None
    assert operation_for_argv(["--schema-version", "3", "experimental", "anysearch", "search"]) is None


def test_v3_schema_is_strict_and_fixtures_validate():
    Draft202012Validator.check_schema(V3_ENVELOPE_JSON_SCHEMA)
    assert V3_ENVELOPE_JSON_SCHEMA["required"] == list(V3_TOP_LEVEL_FIELDS)
    assert V3_ENVELOPE_JSON_SCHEMA["additionalProperties"] is False
    for fixture in (
        complete_config_list(), complete_empty_catalog(), complete_config_write(),
        degraded_probe(), failed_local_configuration(), failed_filesystem(), failed_subprocess(),
    ):
        raw = serialize_result(fixture)
        validate_envelope_dict(raw)
        Draft202012Validator(V3_ENVELOPE_JSON_SCHEMA).validate(raw)
        json.dumps(raw)


def test_v3_state_truth_table_and_exit_registry():
    complete = serialize_result(complete_config_list())
    degraded = serialize_result(degraded_probe())
    failed = serialize_result(failed_local_configuration())
    assert complete["ok"] is True and complete["status"] == "complete" and complete["error"] is None
    assert degraded["ok"] is True and degraded["status"] == "degraded" and degraded["error"] is None
    assert failed["ok"] is False and failed["status"] == "failed" and failed["error"]["code"] == "CONFIGURATION_ERROR"
    assert exit_code_for(complete) == 0
    assert exit_code_for(degraded) == 0
    assert exit_code_for(degraded, fail_on_degraded=True) == 6
    assert exit_code_for(failed) == 3
    assert ERROR_RETRYABILITY[V3ErrorCode.UPSTREAM_TIMEOUT] is True
    assert ERROR_EXIT_CODES[V3ErrorCode.INVALID_ARGUMENT] == 2


def test_v3_redacts_config_values_urls_and_error_details():
    code = V3ErrorCode.CONFIGURATION_ERROR
    result = V3Envelope(
        V3Status.FAILED,
        "config",
        "config.set",
        {"key": "XAI_API_KEY", "value": "super-secret", "api_url": "https://u:p@example.com/?api_key=super-secret"},
        V3Network("none", "none"),
        V3SideEffects(config=V3Mutation(read=True, write_attempted=True)),
        error=V3Error(code, "failed super-secret https://u:p@example.com/?token=super-secret", ERROR_RETRYABILITY[code], {"password": "super-secret"}),
    )
    rendered = json.dumps(serialize_result(result, secrets=("super-secret",)))
    assert "super-secret" not in rendered
    assert "u:p@" not in rendered
    assert "api_key=super-secret" not in rendered


@pytest.mark.parametrize("fixture", [complete_config_list, complete_empty_catalog, complete_config_write, degraded_probe, failed_local_configuration, failed_filesystem, failed_subprocess])
def test_fixture_top_level_field_order_is_exact(fixture):
    assert tuple(serialize_result(fixture())) == V3_TOP_LEVEL_FIELDS


def test_v3_semantic_validator_rejects_cross_status_and_effect_mismatches():
    raw = serialize_result(complete_config_list())
    raw["status"] = "degraded"
    raw["ok"] = True
    with pytest.raises(V3ContractError):
        validate_envelope_dict(raw)

    raw = serialize_result(complete_empty_catalog())
    raw["side_effects"]["config"]["write_attempted"] = True
    with pytest.raises(V3ContractError):
        validate_envelope_dict(raw)

    raw = serialize_result(complete_config_list())
    raw["network"]["attempted"] = True
    with pytest.raises(V3ContractError):
        validate_envelope_dict(raw)

    with pytest.raises(V3ContractError):
        V3Meta(duration_ms=1.0)  # type: ignore[arg-type]
    with pytest.raises(V3ContractError):
        V3Meta(duration_ms=True)  # type: ignore[arg-type]


def test_v3_parser_error_has_no_runtime_imports(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["SMART_SEARCH_CONFIG_DIR"] = str(tmp_path / "config")
    script = r'''
import sys
from smart_search.cli import main
code = main(["--schema-version", "3", "search"])
assert code == 2
for name in ("smart_search.service", "smart_search.config", "httpx", "smart_search.providers.openai_compatible"):
    assert name not in sys.modules, name
print("ok")
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout
    assert not (tmp_path / "config").exists()


def test_v3_parser_error_and_unsupported_alias_are_single_documents(capsys):
    from smart_search.cli import main

    assert main(["--schema-version", "3", "config"]) == 2
    first = json.loads(capsys.readouterr().out)
    assert tuple(first) == V3_TOP_LEVEL_FIELDS
    assert first["operation"] is None

    assert main(["--schema-version", "3", "cfg", "list"]) == 2
    second = json.loads(capsys.readouterr().out)
    assert second["error"]["code"] == "INVALID_ARGUMENT"
    assert second["operation"] is None
    assert "cfg list" in second["error"]["message"]


def test_v3_rejects_v1_output_before_owner(monkeypatch, capsys):
    from smart_search.cli import main
    from smart_search import operations_service

    monkeypatch.setattr(operations_service, "config_list", lambda **_: (_ for _ in ()).throw(AssertionError("owner called")))
    code = main(["--schema-version", "3", "config", "list", "--output", "result.json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["side_effects"]["config"]["read"] is False


def test_v3_fail_on_degraded_keeps_envelope(monkeypatch, capsys):
    from smart_search.cli import main
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlNetworkFacts,
        ControlOperationOutcome,
        ControlOperationStatus,
    )
    from smart_search.execution_primitives import ExecutionMetadata

    async def fake_probe(provider):
        return ControlOperationOutcome(
            operation="provider.probe",
            status=ControlOperationStatus.DEGRADED,
            result={
                "provider": provider, "status": "ok",
                "routes": [{"route_id": "a", "status": "ok"}, {"route_id": "b", "status": "error"}],
            },
            network=ControlNetworkFacts(attempted=True, targets=(provider,)),
            warnings=("one or more provider routes failed their probe",),
            metadata=ExecutionMetadata("provider.probe", 0),
        )

    monkeypatch.setattr(control_operations, "run_provider_probe", fake_probe)
    code = main(["--schema-version", "3", "--fail-on-degraded", "provider", "probe", "xai-responses"])
    assert code == 6
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "3"
    assert payload["status"] == "degraded"
    assert payload["error"] is None
    assert payload["network"]["attempted"] is True


def test_v1_and_v2_selection_remain_unchanged(monkeypatch, capsys):
    from smart_search.cli import main
    from smart_search import api_v2

    async def fake_composite(query, max_results=5):
        return await api_v2.source_discovery(api_v2.SourceDiscoveryRequest(query=query, max_results=max_results))

    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)
    code = main(["--schema-version", "2", "search", "q"])
    assert code in {0, 3, 4}
    output = capsys.readouterr().out
    if output:
        assert json.loads(output)["schema_version"] == "2"
