"""Direct tests for the schema-neutral typed control-plane owners.

Covers the exact 20-operation inventory, the immutable/JSON-safe domain model
truth table, forbidden dependency boundaries, and deterministic owner behavior
for every operation group (config, catalog, routes, probe, doctor, route
explain/calibrate, diagnose, smoke, regression, skills) using fail-if-called
spies and pure fakes. The v3 CLI projection is covered by ``test_cli_v3.py``;
this file freezes the typed ownership semantics underneath it.
"""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from smart_search import control_operations as co
from smart_search.control_operations import (
    CONTROL_OPERATION_IDS,
    CONTROL_OPERATION_OWNERS,
    CONTROL_OPERATION_SET,
    ControlMutationFacts,
    ControlNetworkFacts,
    ControlOperationOutcome,
    ControlOperationStatus,
    ControlSideEffectFacts,
    _connection_checks,
)
from smart_search.config import ConfigStorageError, ModelRoutesConfigurationError
from smart_search.execution_primitives import ExecutionError, ExecutionMetadata
from smart_search.skill_installer import SkillInstallError

OWNER_MODULE_PATH = Path("src/smart_search/control_operations.py")
ADAPTER_MODULE_PATH = Path("src/smart_search/control_plane_adapters.py")

EXPECTED_OPERATION_IDS = (
    "config.path",
    "config.list",
    "config.set",
    "config.unset",
    "provider.catalog.list",
    "provider.catalog.status",
    "provider.probe",
    "provider.routes.current",
    "provider.routes.list",
    "provider.routes.add",
    "provider.routes.remove",
    "doctor.status",
    "doctor.probe",
    "dev.route.explain",
    "dev.route.calibrate",
    "dev.diagnose.openai-compatible",
    "dev.smoke",
    "dev.regression",
    "dev.skills.status",
    "dev.skills.update",
)


def _outcome(
    operation: str,
    status: ControlOperationStatus,
    result: dict,
    *,
    error: ExecutionError | None = None,
    warnings: tuple[str, ...] = (),
    network: ControlNetworkFacts | None = None,
    side_effects: ControlSideEffectFacts | None = None,
) -> ControlOperationOutcome:
    return ControlOperationOutcome(
        operation=operation,
        status=status,
        result=result,
        error=error,
        warnings=warnings,
        network=network or ControlNetworkFacts(),
        side_effects=side_effects or ControlSideEffectFacts(),
        metadata=ExecutionMetadata(operation, 1),
    )


# ---------------------------------------------------------------------------
# Fixed operation inventory
# ---------------------------------------------------------------------------


def test_control_operation_inventory_is_exact_20_ids():
    assert CONTROL_OPERATION_IDS == EXPECTED_OPERATION_IDS
    assert CONTROL_OPERATION_SET == frozenset(EXPECTED_OPERATION_IDS)
    assert set(CONTROL_OPERATION_OWNERS) == set(EXPECTED_OPERATION_IDS)
    assert len(CONTROL_OPERATION_OWNERS) == 20


def test_control_operation_ids_match_v3_contract_inventory():
    from smart_search.control_plane_contract import V3_OPERATION_IDS

    assert set(CONTROL_OPERATION_IDS) == set(V3_OPERATION_IDS)
    # The schema-neutral inventory is authoritative; the v3 contract is not
    # imported from the owner module itself (see forbidden-import test).


def test_owner_rejects_unknown_and_legacy_operations():
    for operation in (
        "model.list",
        "provider.exa.search",
        "experimental.anysearch.domains",
        "doctor",
        "dev.diagnose",
        "legacy.search",
        "provider.routes.migrate",
    ):
        with pytest.raises(ValueError):
            _outcome(operation, ControlOperationStatus.COMPLETE, {"ok": True})
    with pytest.raises(ValueError):
        ControlOperationOutcome(
            operation="",
            status=ControlOperationStatus.COMPLETE,
            result={},
        )


# ---------------------------------------------------------------------------
# Domain model truth table and immutability
# ---------------------------------------------------------------------------


def test_outcome_truth_table_complete_degraded_failed():
    complete = _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {}})
    assert complete.status is ControlOperationStatus.COMPLETE
    assert complete.error is None
    assert complete.warnings == ()

    degraded = _outcome(
        "provider.probe",
        ControlOperationStatus.DEGRADED,
        {"provider": "xai-responses"},
        warnings=("one or more provider routes failed their probe",),
    )
    assert degraded.error is None
    assert degraded.warnings == ("one or more provider routes failed their probe",)

    failed = _outcome(
        "config.set",
        ControlOperationStatus.FAILED,
        {"key": "XAI_API_KEY"},
        error=ExecutionError("parameter_error", "unsupported key", False),
    )
    assert failed.error.type == "parameter_error"
    assert failed.warnings == ()

    # complete cannot carry an error
    with pytest.raises(ValueError):
        _outcome(
            "config.set",
            ControlOperationStatus.COMPLETE,
            {"key": "XAI_API_KEY"},
            error=ExecutionError("config_error", "boom", False),
        )
    # degraded cannot carry an error
    with pytest.raises(ValueError):
        _outcome(
            "provider.probe",
            ControlOperationStatus.DEGRADED,
            {"provider": "x"},
            warnings=("partial",),
            error=ExecutionError("network_error", "boom", True),
        )
    # degraded requires a non-blank warning
    with pytest.raises(ValueError):
        _outcome("provider.probe", ControlOperationStatus.DEGRADED, {"provider": "x"}, warnings=("  ",))
    # failed requires an error
    with pytest.raises(ValueError):
        _outcome("config.set", ControlOperationStatus.FAILED, {"key": "XAI_API_KEY"})
    # failed cannot carry warnings
    with pytest.raises(ValueError):
        _outcome(
            "config.set",
            ControlOperationStatus.FAILED,
            {"key": "XAI_API_KEY"},
            error=ExecutionError("config_error", "boom", False),
            warnings=("partial",),
        )


def test_outcome_accepts_string_status_and_rejects_unknown():
    outcome = ControlOperationOutcome(
        operation="config.list",
        status="complete",
        result={"values": {}},
    )
    assert outcome.status is ControlOperationStatus.COMPLETE
    with pytest.raises(ValueError):
        ControlOperationOutcome(operation="config.list", status="succeeded", result={})
    with pytest.raises(ValueError):
        ControlOperationOutcome(operation="config.list", status=3, result={})  # type: ignore[arg-type]


def test_outcome_result_is_frozen_json_safe_and_fresh_thawed():
    outcome = _outcome(
        "config.list",
        ControlOperationStatus.COMPLETE,
        {"values": {"nested": {"a": [1, 2, 3]}}},
    )
    with pytest.raises(TypeError):
        outcome.result["values"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        outcome.result["values"]["nested"]["a"][0] = 9  # type: ignore[index]
    fresh = outcome.result_dict
    fresh["values"]["nested"]["a"][0] = 99
    assert outcome.result_dict["values"]["nested"]["a"][0] == 1
    assert json.dumps(outcome.result_dict) == json.dumps(
        {"values": {"nested": {"a": [1, 2, 3]}}}
    )


def test_outcome_rejects_non_json_and_nonfinite_values():
    with pytest.raises(ValueError):
        _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {"x": float("nan")}})
    with pytest.raises(ValueError):
        _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {"x": float("inf")}})
    with pytest.raises(ValueError):
        _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {"x": {1: "int-key"}}})
    with pytest.raises(ValueError):
        _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {"x": {"a", "set"}}})
    with pytest.raises(ValueError):
        _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {"x": object()}})
    with pytest.raises(ValueError):
        _outcome("config.list", ControlOperationStatus.COMPLETE, "not-a-mapping")  # type: ignore[arg-type]


def test_network_facts_require_unique_nonblank_targets():
    ok = ControlNetworkFacts(attempted=True, targets=("tavily", "exa"))
    assert ok.targets == ("tavily", "exa")
    with pytest.raises(ValueError):
        ControlNetworkFacts(attempted=True, targets=("tavily", "tavily"))
    with pytest.raises(ValueError):
        ControlNetworkFacts(attempted=True, targets=("tavily", ""))
    with pytest.raises(ValueError):
        ControlNetworkFacts(attempted=True, targets=("  ",))
    with pytest.raises(ValueError):
        ControlNetworkFacts(attempted="yes", targets=("tavily",))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ControlNetworkFacts(attempted=True, targets=(1,))  # type: ignore[arg-type]


def test_mutation_facts_committed_requires_attempted():
    assert ControlMutationFacts(read=True, write_attempted=True, write_committed=True)
    with pytest.raises(ValueError):
        ControlMutationFacts(write_attempted=False, write_committed=True)
    with pytest.raises(ValueError):
        ControlMutationFacts(write_attempted=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ControlMutationFacts(read="yes")  # type: ignore[arg-type]


def test_side_effect_facts_type_checks():
    assert ControlSideEffectFacts(
        config=ControlMutationFacts(read=True),
        filesystem=ControlMutationFacts(read=True, write_attempted=True, write_committed=True),
        subprocess_started=True,
    )
    with pytest.raises(ValueError):
        ControlSideEffectFacts(config=ControlMutationFacts(read=True), subprocess_started=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ControlSideEffectFacts(config={"read": True})  # type: ignore[arg-type]


def test_outcome_metadata_is_required_and_validated():
    outcome = _outcome("config.list", ControlOperationStatus.COMPLETE, {"values": {}})
    assert outcome.metadata.duration_ms >= 0
    assert outcome.metadata.request_id == "config.list"
    with pytest.raises(ValueError):
        ControlOperationOutcome(
            operation="config.list",
            status=ControlOperationStatus.COMPLETE,
            result={},
            metadata=ExecutionMetadata("config.list", -1),
        )


# ---------------------------------------------------------------------------
# Forbidden dependency boundaries (AST)
# ---------------------------------------------------------------------------


def test_control_operations_forbidden_imports():
    """The owner must not import CLI, V1/V2/V3/Workflow contracts, renderers,
    the broad service facade, service_support, provider adapters or the v3
    adapter. The only low-level dependency is the private executor module
    ``control_executors`` (see
    ``test_control_operations_calls_only_approved_executor_helpers``)."""
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
        "smart_search.control_plane_adapters",
        "smart_search.cli",
        "smart_search.cli_contract",
        "smart_search.cli_render",
        "smart_search.cli_parser",
        "smart_search.cli_constants",
        "smart_search.cli_dispatch",
        "smart_search.cli_setup",
        "smart_search.cli_support",
        "smart_search.cli_v2",
        "smart_search.cli_v3",
        "smart_search.service",
        "smart_search.service_support",
        "smart_search.search_service",
        "smart_search.research_service",
        "smart_search.operations_service",
        "smart_search.v1_contract",
        "smart_search.legacy_surface_inventory",
        "smart_search.api_v2",
        "smart_search.canonical_operations",
        "smart_search.evidence_operations",
        "smart_search.operation_runtime",
        "smart_search.capability_executor",
        "smart_search.research_workflow_contract",
        "smart_search.workflow",
        "smart_search.providers",
    )
    for module in imported:
        assert not any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes
        ), f"forbidden import in control_operations: {module}"
    # The v3 envelope type names must never appear in the owner.
    assert "V3Envelope" not in source
    assert "V3Status" not in source


APPROVED_EXECUTOR_HELPERS = frozenset(
    {
        "_model_routes_result",
        "_safe_test_main_provider_connection",
        "_execute_doctor_status",
        "_execute_doctor_probe",
        "_execute_diagnose_openai_compatible",
        "_execute_smoke",
    }
)


def test_control_operations_calls_only_approved_executor_helpers():
    """The typed owner calls exactly the six raw private executors through
    module attribute access on the private ``control_executors`` module, never
    through any public legacy wrapper or v1 compatibility projection."""
    source = OWNER_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    accessed: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "control_executors"
        ):
            accessed.add(node.attr)
    assert accessed == APPROVED_EXECUTOR_HELPERS
    # Names must never be imported directly from the module; executors are
    # reached through module attribute access only.
    direct = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.endswith("control_executors")
    ]
    assert not direct, f"direct control_executors name import: {direct}"
    # The v1 executor host module is gone.
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "operations_service"
        for node in ast.walk(tree)
    )


def test_control_plane_adapters_projection_only_imports():
    """The v3 adapter must not import or call low-level execution owners."""
    source = ADAPTER_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    forbidden_prefixes = (
        "smart_search.config",
        "smart_search.capability_service",
        "smart_search.provider_catalog",
        "smart_search.provider_diagnostics",
        "smart_search.operations_service",
        "smart_search.control_executors",
        "smart_search.skill_installer",
        "smart_search.cli_dispatch",
        "smart_search.service",
        "smart_search.service_support",
        "smart_search.providers",
    )
    for module in imported:
        assert not any(
            module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes
        ), f"forbidden execution import in adapter: {module}"
    # Legacy semantic inference keys must not be read for status/effect facts.
    for key in (
        "network_attempted",
        "degraded_reason",
        "installed_count",
        "failed_count",
        "subprocess_started",
        "error_type",
    ):
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == key
            for node in ast.walk(tree)
        ), f"adapter reads legacy semantic key: {key}"


# ---------------------------------------------------------------------------
# Config owners
# ---------------------------------------------------------------------------


def test_config_path_complete_is_read_only(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "config_path_info",
        lambda: {
            "ok": True,
            "config_file": "/tmp/c.json",
            "config_dir": "/tmp",
            "config_dir_source": "override",
        },
    )
    result = asyncio.run(co.run_config_path())
    assert result.operation == "config.path"
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is False
    assert result.side_effects.config.read is True
    assert result.side_effects.config.write_attempted is False
    assert result.result_dict["config_file"] == "/tmp/c.json"


def test_config_path_failure_classified_no_network(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "config_path_info",
        lambda: {"ok": False, "error": "SMART_SEARCH_CONFIG_DIR unavailable", "config_file": ""},
    )
    result = asyncio.run(co.run_config_path())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.network.attempted is False
    assert "SMART_SEARCH_CONFIG_DIR" in result.error.message


def test_config_list_complete_empty_values(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "config_path_info",
        lambda: {
            "ok": True,
            "config_file": "/tmp/c.json",
            "config_dir": "/tmp",
            "config_dir_source": "override",
        },
    )
    result = asyncio.run(co.run_config_list())
    assert result.operation == "config.list"
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.result_dict["values"] == {}
    assert result.network.attempted is False
    assert result.side_effects.config.read is True


def test_config_list_failed_on_invalid_saved_routes(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "config_path_info",
        lambda: {
            "ok": True,
            "config_file": "/tmp/c.json",
            "config_dir": "/tmp",
            "config_dir_source": "override",
        },
    )
    monkeypatch.setattr(
        co.config,
        "validate_saved_model_routes",
        lambda: (_ for _ in ()).throw(ModelRoutesConfigurationError("invalid saved routes")),
    )
    result = asyncio.run(co.run_config_list())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.result_dict["values"] == {}
    assert result.network.attempted is False


def test_config_set_success_reports_write_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(co.config, "_config_file", tmp_path / "config.json")
    result = asyncio.run(co.run_config_set("XAI_API_KEY", "raw-secret"))
    assert result.operation == "config.set"
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.config.read is True
    assert result.side_effects.config.write_attempted is True
    assert result.side_effects.config.write_committed is True
    assert result.network.attempted is False
    rendered = json.dumps(result.result_dict)
    assert "raw-secret" not in rendered
    assert (tmp_path / "config.json").exists()


def test_config_set_parameter_failure_no_write_attempt(monkeypatch):
    def boom(key, value):
        raise ValueError("Unsupported config key: BOGUS")

    monkeypatch.setattr(co.config, "set_config_value", boom)
    result = asyncio.run(co.run_config_set("BOGUS", "x"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.config.write_attempted is False
    assert result.side_effects.config.write_committed is False


def test_config_set_atomic_failure_attempts_without_commit(monkeypatch, tmp_path):
    def boom(key, value):
        raise ConfigStorageError("atomic replace failed")

    monkeypatch.setattr(co.config, "_config_file", tmp_path / "config.json")
    monkeypatch.setattr(co.config, "set_config_value", boom)
    result = asyncio.run(co.run_config_set("XAI_API_KEY", "raw-secret"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.side_effects.config.write_attempted is True
    assert result.side_effects.config.write_committed is False
    assert "raw-secret" not in json.dumps(result.result_dict)
    assert not (tmp_path / "config.json").exists()


def test_config_unset_success_and_parameter_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(co.config, "_config_file", tmp_path / "config.json")
    result = asyncio.run(co.run_config_unset("XAI_API_KEY"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.config.write_attempted is True
    assert result.side_effects.config.write_committed is True

    monkeypatch.setattr(
        co.config,
        "unset_config_value",
        lambda key: (_ for _ in ()).throw(ValueError("Unsupported config key: BOGUS")),
    )
    failed = asyncio.run(co.run_config_unset("BOGUS"))
    assert failed.status is ControlOperationStatus.FAILED
    assert failed.error.type == "parameter_error"
    assert failed.side_effects.config.write_attempted is False


def test_config_set_rejects_environment_owned_key(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_API_KEY", "environment-key")
    monkeypatch.setattr(co.config, "_config_file", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}\n", encoding="utf-8")

    result = asyncio.run(co.run_config_set("XAI_API_KEY", "dormant-file-key"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert "environment" in result.error.message
    assert result.side_effects.config.write_committed is False
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == "{}\n"


def test_config_unset_rejects_environment_owned_key(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_API_KEY", "environment-key")
    monkeypatch.setattr(co.config, "_config_file", tmp_path / "config.json")
    (tmp_path / "config.json").write_text('{"XAI_API_KEY": "file-key"}\n', encoding="utf-8")

    result = asyncio.run(co.run_config_unset("XAI_API_KEY"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.config.write_committed is False
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"XAI_API_KEY": "file-key"}\n'


def test_config_list_and_set_on_malformed_file(monkeypatch, tmp_path):
    monkeypatch.setattr(co.config, "_config_file", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{ not valid json", encoding="utf-8")

    listed = asyncio.run(co.run_config_list())
    assert listed.status is ControlOperationStatus.FAILED
    assert listed.error.type == "config_error"
    assert "malformed" in listed.error.message

    original = (tmp_path / "config.json").read_bytes()
    result = asyncio.run(co.run_config_set("XAI_MODEL", "replacement"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.side_effects.config.write_committed is False
    assert (tmp_path / "config.json").read_bytes() == original


# ---------------------------------------------------------------------------
# Provider catalog owners
# ---------------------------------------------------------------------------


def test_catalog_list_complete_no_network_empty(monkeypatch, no_network_spies):
    monkeypatch.setattr(co, "provider_catalog", lambda include_status=False: {"providers": []})
    result = asyncio.run(co.run_provider_catalog_list())
    assert result.operation == "provider.catalog.list"
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.result_dict["provider_count"] == 0
    assert result.network.attempted is False
    assert result.side_effects.config.read is True


def test_catalog_status_include_status_records(monkeypatch):
    monkeypatch.setattr(
        co,
        "provider_catalog",
        lambda include_status=False: {
            "providers": [
                {
                    "provider": "tavily",
                    "capabilities": ["web_search", "web_fetch", "site_map"],
                    "status": [
                        {
                            "capability": "web_search",
                            "configured": True,
                            "enabled": True,
                            "eligible": True,
                            "reason": "ok",
                        }
                    ],
                }
            ]
        },
    )
    result = asyncio.run(co.run_provider_catalog_status())
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.result_dict["provider_count"] == 1
    assert result.result_dict["providers"][0]["provider"] == "tavily"
    assert result.network.attempted is False


# ---------------------------------------------------------------------------
# Provider routes owners
# ---------------------------------------------------------------------------


def _routes_ok_data(action: str) -> dict:
    return {
        "ok": True,
        "action": action,
        "routes": [
            {"id": "primary", "provider": "openai-compatible", "api_key": "***", "model": "m-a"}
        ],
        "model_routes": [],
        "route_count": 1,
        "current_route_id": "primary",
        "current_route": {"id": "primary", "provider": "openai-compatible", "api_key": "***", "model": "m-a"},
        "current_model": "m-a",
        "config_file": "/tmp/c.json",
    }


def test_routes_current_and_list_read_only(monkeypatch):
    monkeypatch.setattr(co.control_executors, "_model_routes_result", _routes_ok_data)
    for operation, action in (
        ("provider.routes.current", "current"),
        ("provider.routes.list", "list"),
    ):
        owner = co.CONTROL_OPERATION_OWNERS[operation]
        result = asyncio.run(owner())
        assert result.operation == operation
        assert result.status is ControlOperationStatus.COMPLETE
        assert result.result_dict["route_count"] == 1
        assert result.network.attempted is False
        assert result.side_effects.config.write_attempted is False


def test_routes_read_failure_classified(monkeypatch):
    monkeypatch.setattr(
        co.control_executors,
        "_model_routes_result",
        lambda action: {
            "ok": False,
            "action": action,
            "error_type": "config_error",
            "error": "invalid saved routes",
            "routes": [],
            "route_count": 0,
        },
    )
    result = asyncio.run(co.run_provider_routes_list())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.side_effects.config.write_attempted is False


def test_routes_add_success_write_facts_and_masking(monkeypatch):
    monkeypatch.setattr(co.config, "add_model_route", lambda route: [route])
    monkeypatch.setattr(co.control_executors, "_model_routes_result", _routes_ok_data)
    result = asyncio.run(
        co.run_provider_routes_add(
            "primary",
            "openai-compatible",
            "https://u:p@example.com/v1?api_key=sec",
            "sec",
            "model-a",
            tools="",
            stream=False,
            fallback_models="",
        )
    )
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.config.write_attempted is True
    assert result.side_effects.config.write_committed is True
    assert result.network.attempted is False
    rendered = json.dumps(result.result_dict)
    assert "sec" not in rendered
    assert "u:p@" not in rendered


def test_routes_add_parameter_failure_no_write(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "add_model_route",
        lambda route: (_ for _ in ()).throw(ValueError("duplicate route id")),
    )
    result = asyncio.run(co.run_provider_routes_add("primary", "openai-compatible", "https://x", "k", "m"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.config.write_attempted is False
    assert result.side_effects.config.write_committed is False


def test_routes_add_saved_corruption_is_config_failure(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "add_model_route",
        lambda route: (_ for _ in ()).throw(ModelRoutesConfigurationError("saved routes invalid")),
    )
    result = asyncio.run(co.run_provider_routes_add("primary", "openai-compatible", "https://x", "k", "m"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.side_effects.config.write_attempted is False


def test_routes_add_atomic_failure_attempts_no_commit(monkeypatch):
    monkeypatch.setattr(
        co.config,
        "add_model_route",
        lambda route: (_ for _ in ()).throw(ConfigStorageError("atomic replace failed")),
    )
    result = asyncio.run(co.run_provider_routes_add("primary", "openai-compatible", "https://x", "k", "m"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.side_effects.config.write_attempted is True
    assert result.side_effects.config.write_committed is False


def test_routes_remove_success_and_parameter_failure(monkeypatch):
    monkeypatch.setattr(co.config, "remove_model_route", lambda route_id: [])
    monkeypatch.setattr(co.control_executors, "_model_routes_result", _routes_ok_data)
    result = asyncio.run(co.run_provider_routes_remove("primary"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.config.write_attempted is True
    assert result.side_effects.config.write_committed is True

    monkeypatch.setattr(
        co.config,
        "remove_model_route",
        lambda route_id: (_ for _ in ()).throw(ValueError("unknown route id")),
    )
    failed = asyncio.run(co.run_provider_routes_remove("ghost"))
    assert failed.status is ControlOperationStatus.FAILED
    assert failed.error.type == "parameter_error"
    assert failed.side_effects.config.write_attempted is False


# ---------------------------------------------------------------------------
# Provider probe owner
# ---------------------------------------------------------------------------

_PROBE_BASE_ELIGIBLE = {
    "operation": "provider_probe",
    "provider": "tavily",
    "configured": True,
    "enabled": True,
    "eligible": True,
    "route_family": False,
    "probe_capability": "web_search",
    "probe_operation": "search",
    "availability_reason": "ok",
    "availability_error": "",
    "response_time_ms": 0,
    "capabilities": ["web_search"],
}


def test_probe_unknown_provider_no_network(monkeypatch):
    monkeypatch.setattr(
        co,
        "provider_probe_base",
        lambda provider: {
            "ok": False,
            "provider": provider,
            "error_type": "parameter_error",
            "error": f"Unknown provider: {provider}",
            "status": "provider_error",
            "network_attempted": False,
        },
    )
    result = asyncio.run(co.run_provider_probe("nope"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.network.attempted is False
    assert result.network.targets == ("nope",)


def test_probe_unsupported_provider_no_network(monkeypatch):
    monkeypatch.setattr(
        co,
        "provider_probe_base",
        lambda provider: {
            "ok": False,
            "provider": provider,
            "configured": False,
            "enabled": False,
            "eligible": False,
            "status": "unsupported",
            "error_type": "config_error",
            "error": f"Provider {provider} has no safe low-cost probe",
        },
    )
    result = asyncio.run(co.run_provider_probe("anysearch"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.network.attempted is False


def test_probe_invalid_model_routes_no_network(monkeypatch):
    base = dict(_PROBE_BASE_ELIGIBLE)
    base["availability_reason"] = "invalid_model_routes"
    base["availability_error"] = "Invalid SMART_SEARCH_MODEL_ROUTES"
    monkeypatch.setattr(co, "provider_probe_base", lambda provider: base)
    result = asyncio.run(co.run_provider_probe("xai-responses"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.network.attempted is False
    assert result.result_dict["status"] == "config_error"


def test_probe_not_configured_and_disabled_no_network(monkeypatch):
    for field, value in (("configured", False), ("enabled", False)):
        base = dict(_PROBE_BASE_ELIGIBLE)
        base[field] = value
        base["availability_reason"] = "not_configured" if field == "configured" else "disabled"
        monkeypatch.setattr(co, "provider_probe_base", lambda provider, base=base: base)
        result = asyncio.run(co.run_provider_probe("tavily"))
        assert result.status is ControlOperationStatus.FAILED
        assert result.error.type == "config_error"
        assert result.network.attempted is False


def test_probe_single_provider_success_records_network(monkeypatch):
    monkeypatch.setattr(co, "provider_probe_base", lambda provider: _PROBE_BASE_ELIGIBLE)

    async def fake_probe(provider):
        return {"status": "ok", "message": "ok", "response_time_ms": 1}

    monkeypatch.setattr(co, "run_probe_adapter", fake_probe)
    result = asyncio.run(co.run_provider_probe("tavily"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is True
    assert result.network.targets == ("tavily",)
    assert result.result_dict["status"] == "ok"
    assert result.side_effects.config.read is True


def test_probe_single_provider_failure_classified_network(monkeypatch):
    monkeypatch.setattr(co, "provider_probe_base", lambda provider: _PROBE_BASE_ELIGIBLE)

    async def fake_probe(provider):
        return {"status": "timeout", "message": "request timeout", "response_time_ms": 5}

    monkeypatch.setattr(co, "run_probe_adapter", fake_probe)
    result = asyncio.run(co.run_provider_probe("tavily"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "network_error"
    assert result.error.retryable is True
    assert result.network.attempted is True
    assert result.network.targets == ("tavily",)


def test_probe_route_family_all_success_complete(monkeypatch):
    base = dict(_PROBE_BASE_ELIGIBLE)
    base["provider"] = "xai-responses"
    base["route_family"] = True
    base["probe_capability"] = "main_search"
    monkeypatch.setattr(co, "provider_probe_base", lambda provider: base)
    monkeypatch.setattr(
        co,
        "_main_search_provider_configs",
        lambda **_: [
            {"provider": "xai-responses", "route_id": "primary", "api_url": "https://a", "api_key": "k1", "model": "m1"},
            {"provider": "xai-responses", "route_id": "backup", "api_url": "https://b", "api_key": "k2", "model": "m2"},
        ],
    )

    async def fake_connection(route):
        return {"status": "ok", "message": "ok", "response_time_ms": 1}

    monkeypatch.setattr(co.control_executors, "_safe_test_main_provider_connection", fake_connection)
    result = asyncio.run(co.run_provider_probe("xai-responses"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is True
    assert result.network.targets == ("xai-responses",)
    assert len(result.result_dict["routes"]) == 2
    assert all(item["status"] == "ok" for item in result.result_dict["routes"])


def test_probe_route_family_partial_degraded(monkeypatch):
    base = dict(_PROBE_BASE_ELIGIBLE)
    base["provider"] = "xai-responses"
    base["route_family"] = True
    monkeypatch.setattr(co, "provider_probe_base", lambda provider: base)
    monkeypatch.setattr(
        co,
        "_main_search_provider_configs",
        lambda **_: [
            {"provider": "xai-responses", "route_id": "primary", "api_url": "https://a", "api_key": "k1", "model": "m1"},
            {"provider": "xai-responses", "route_id": "backup", "api_url": "https://b", "api_key": "k2", "model": "m2"},
        ],
    )
    results = iter(
        [
            {"status": "ok", "message": "ok", "response_time_ms": 1},
            {"status": "timeout", "message": "timeout", "response_time_ms": 5},
        ]
    )

    async def fake_connection(route):
        return next(results)

    monkeypatch.setattr(co.control_executors, "_safe_test_main_provider_connection", fake_connection)
    result = asyncio.run(co.run_provider_probe("xai-responses"))
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.error is None
    assert result.warnings == ("one or more provider routes failed their probe",)
    assert result.network.attempted is True
    assert result.network.targets == ("xai-responses",)


def test_probe_route_family_all_failed(monkeypatch):
    base = dict(_PROBE_BASE_ELIGIBLE)
    base["provider"] = "xai-responses"
    base["route_family"] = True
    monkeypatch.setattr(co, "provider_probe_base", lambda provider: base)
    monkeypatch.setattr(
        co,
        "_main_search_provider_configs",
        lambda **_: [
            {"provider": "xai-responses", "route_id": "primary", "api_url": "https://a", "api_key": "k1", "model": "m1"},
        ],
    )

    async def fake_connection(route):
        return {"status": "timeout", "message": "timeout", "response_time_ms": 5}

    monkeypatch.setattr(co.control_executors, "_safe_test_main_provider_connection", fake_connection)
    result = asyncio.run(co.run_provider_probe("xai-responses"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "network_error"
    assert result.network.attempted is True
    assert result.network.targets == ("xai-responses",)


# ---------------------------------------------------------------------------
# Doctor owners
# ---------------------------------------------------------------------------

_DOCTOR_STATUS_OK = {
    "ok": True,
    "local_only": True,
    "config_storage_ok": True,
    "minimum_profile": "off",
    "minimum_profile_ok": True,
    "core_evidence_ready": True,
    "core_evidence_path": {},
    "capability_status": {},
    "intent_router_status": {},
}


def test_doctor_status_local_only_no_network(monkeypatch, no_network_spies):
    monkeypatch.setattr(co.control_executors, "_execute_doctor_status", lambda: _DOCTOR_STATUS_OK)
    result = asyncio.run(co.run_doctor_status())
    assert result.operation == "doctor.status"
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is False
    assert result.network.targets == ()
    assert result.side_effects.config.read is True
    assert result.result_dict["local_only"] is True


def test_doctor_status_failure_classified_no_network(monkeypatch):
    monkeypatch.setattr(
        co.control_executors,
        "_execute_doctor_status",
        lambda: {"ok": False, "error_type": "config_error", "error": "config unavailable", "local_only": True},
    )
    result = asyncio.run(co.run_doctor_status())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.network.attempted is False


def test_doctor_probe_complete_records_targets(monkeypatch):
    async def fake_doctor():
        return {
            "ok": True,
            "minimum_profile": "standard",
            "minimum_profile_ok": True,
            "main_search_connection_tests": {
                "primary": {"route_id": "primary", "provider": "openai-compatible", "status": "ok", "message": "ok", "response_time_ms": 1},
            },
            "exa_connection_test": {"status": "not_configured", "message": "missing"},
        }

    monkeypatch.setattr(co.control_executors, "_execute_doctor_probe", fake_doctor)
    result = asyncio.run(co.run_doctor_probe())
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is True
    assert result.network.targets == ("openai-compatible",)
    assert _connection_checks(result.result_dict)[0]["status"] == "ok"


def test_doctor_probe_owner_degraded(monkeypatch):
    async def fake_doctor():
        return {
            "ok": True,
            "degraded": True,
            "degraded_reason": "optional capabilities unavailable",
            "minimum_profile": "standard",
            "minimum_profile_ok": True,
            "main_search_connection_tests": {
                "primary": {"route_id": "primary", "provider": "openai-compatible", "status": "ok", "message": "ok", "response_time_ms": 1},
            },
        }

    monkeypatch.setattr(co.control_executors, "_execute_doctor_probe", fake_doctor)
    result = asyncio.run(co.run_doctor_probe())
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.error is None
    assert result.warnings == ("optional capabilities unavailable",)
    assert result.network.attempted is True


def test_doctor_probe_partial_connectivity_degraded(monkeypatch):
    async def fake_doctor():
        return {
            "ok": False,
            "error_type": "network_error",
            "error": "one route failed",
            "minimum_profile": "standard",
            "minimum_profile_ok": True,
            "main_search_connection_tests": {
                "primary": {"route_id": "primary", "provider": "openai-compatible", "status": "ok", "message": "ok", "response_time_ms": 1},
                "backup": {"route_id": "backup", "provider": "xai-responses", "status": "timeout", "message": "timeout", "response_time_ms": 5},
            },
        }

    monkeypatch.setattr(co.control_executors, "_execute_doctor_probe", fake_doctor)
    result = asyncio.run(co.run_doctor_probe())
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.warnings == ("aggregate doctor completed with partial connectivity",)
    assert set(result.network.targets) == {"openai-compatible", "xai-responses"}


def test_doctor_probe_failed_no_usable_connectivity(monkeypatch):
    async def fake_doctor():
        return {
            "ok": False,
            "error_type": "network_error",
            "error": "all routes failed",
            "minimum_profile": "standard",
            "minimum_profile_ok": False,
            "main_search_connection_tests": {
                "primary": {"route_id": "primary", "provider": "openai-compatible", "status": "timeout", "message": "timeout", "response_time_ms": 5},
            },
        }

    monkeypatch.setattr(co.control_executors, "_execute_doctor_probe", fake_doctor)
    result = asyncio.run(co.run_doctor_probe())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "network_error"
    assert result.network.attempted is True


# ---------------------------------------------------------------------------
# Dev route explain / calibrate owners
# ---------------------------------------------------------------------------


def test_route_explain_rules_only_no_network(monkeypatch):
    async def fake_route(query, validation="", mode=""):
        return {
            "ok": True,
            "query": query,
            "router_engines_used": ["rules"],
            "degraded": False,
            "required_capabilities": ["docs_search"],
            "intent_signals": {},
            "confidence": 0.9,
        }

    monkeypatch.setattr(co.capability_service, "route", fake_route)
    result = asyncio.run(co.run_dev_route_explain("docs"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is False
    assert result.network.targets == ()


def test_route_explain_network_targets_recorded(monkeypatch):
    async def fake_route(query, validation="", mode=""):
        return {
            "ok": True,
            "query": query,
            "router_engines_used": ["rules", "embeddings"],
            "degraded": False,
            "required_capabilities": ["docs_search"],
            "intent_signals": {},
            "confidence": 0.8,
        }

    monkeypatch.setattr(co.capability_service, "route", fake_route)
    result = asyncio.run(co.run_dev_route_explain("docs", validation="balanced", mode="hybrid"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is True
    assert result.network.targets == ("embeddings",)


def test_route_explain_degraded_router_fallback(monkeypatch):
    async def fake_route(query, validation="", mode=""):
        return {
            "ok": True,
            "query": query,
            "router_engines_used": ["rules"],
            "degraded": True,
            "degraded_reason": "classifier unavailable",
            "required_capabilities": ["docs_search"],
            "intent_signals": {},
            "confidence": 0.6,
        }

    monkeypatch.setattr(co.capability_service, "route", fake_route)
    result = asyncio.run(co.run_dev_route_explain("docs"))
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.warnings == ("classifier unavailable",)
    assert result.network.attempted is True  # unavailable classifier marks attempted
    assert result.network.targets == ()


def test_route_explain_failure_parameter(monkeypatch):
    async def fake_route(query, validation="", mode=""):
        return {
            "ok": False,
            "query": query,
            "error_type": "parameter_error",
            "error": "Invalid validation level: bogus",
        }

    monkeypatch.setattr(co.capability_service, "route", fake_route)
    result = asyncio.run(co.run_dev_route_explain("docs", validation="bogus"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.network.attempted is False


def test_calibrate_complete_records_attempted_models(monkeypatch):
    async def fake_calibrate(models=""):
        return {
            "ok": True,
            "models": ["a"],
            "failed_models": [],
            "model_results": [{"model": "a", "ok": True, "semantic_macro_f1": 0.9}],
            "recommended_model": "a",
            "dataset_size": 1,
        }

    monkeypatch.setattr(co.capability_service, "route_calibrate", fake_calibrate)
    result = asyncio.run(co.run_dev_route_calibrate(models="a"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is True
    assert result.network.targets == ("a",)


def test_calibrate_partial_degraded(monkeypatch):
    async def fake_calibrate(models=""):
        return {
            "ok": True,
            "models": ["a", "b"],
            "failed_models": ["b"],
            "model_results": [
                {"model": "a", "ok": True, "semantic_macro_f1": 0.9},
                {"model": "b", "ok": False, "error_type": "provider_error", "error": "failed"},
            ],
            "recommended_model": "a",
            "dataset_size": 2,
        }

    monkeypatch.setattr(co.capability_service, "route_calibrate", fake_calibrate)
    result = asyncio.run(co.run_dev_route_calibrate(models="a,b"))
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.warnings == ("one or more calibration models failed",)
    assert result.network.attempted is True
    assert set(result.network.targets) == {"a", "b"}


def test_calibrate_all_failed_classified(monkeypatch):
    async def fake_calibrate(models=""):
        return {
            "ok": False,
            "models": ["b"],
            "failed_models": ["b"],
            "model_results": [{"model": "b", "ok": False, "error_type": "provider_error", "error": "failed"}],
            "recommended_model": "",
            "dataset_size": 1,
            "error": "no embedding model could be calibrated",
        }

    monkeypatch.setattr(co.capability_service, "route_calibrate", fake_calibrate)
    result = asyncio.run(co.run_dev_route_calibrate(models="b"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "provider_error"
    assert result.network.attempted is True
    assert result.network.targets == ("b",)


def test_calibrate_all_config_failed_no_network(monkeypatch):
    async def fake_calibrate(models=""):
        return {
            "ok": False,
            "models": ["b"],
            "failed_models": ["b"],
            "model_results": [{"model": "b", "ok": False, "error_type": "config_error", "error": "missing key"}],
            "recommended_model": "",
            "dataset_size": 0,
            "error": "missing embedding configuration",
        }

    monkeypatch.setattr(co.capability_service, "route_calibrate", fake_calibrate)
    result = asyncio.run(co.run_dev_route_calibrate(models="b"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.network.attempted is False


# ---------------------------------------------------------------------------
# Diagnose / smoke owners
# ---------------------------------------------------------------------------


def test_diagnose_local_config_missing_fails_before_network(monkeypatch):
    async def fake_diagnose(timeout_seconds=30.0):
        return {
            "ok": False,
            "provider": "openai-compatible",
            "checks": [],
            "missing": ["OPENAI_COMPATIBLE_API_KEY"],
            "error_type": "config_error",
            "error": "missing OPENAI_COMPATIBLE_API_KEY",
        }

    monkeypatch.setattr(co.control_executors, "_execute_diagnose_openai_compatible", fake_diagnose)
    result = asyncio.run(co.run_dev_diagnose_openai_compatible())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.network.attempted is False
    assert result.network.targets == ()


def test_diagnose_missing_config_recommendation_uses_canonical_commands():
    """The real missing-config branch of the OpenAI-compatible diagnose
    executor recommends only retained canonical commands, never the removed
    ``smart-search setup`` spelling. No network or config write occurs."""
    data = asyncio.run(co.control_executors._execute_diagnose_openai_compatible())
    assert data["error_type"] == "config_error"
    recommendation = data["recommendation"]
    assert "`smart-search config set`" in recommendation
    assert "smart-search setup" not in recommendation


def test_diagnose_request_success_records_target(monkeypatch):
    async def fake_diagnose(timeout_seconds=30.0):
        return {
            "ok": True,
            "provider": "openai-compatible",
            "checks": [{"name": "chat", "status": "ok"}],
            "error_type": "",
            "error": "",
        }

    monkeypatch.setattr(co.control_executors, "_execute_diagnose_openai_compatible", fake_diagnose)
    result = asyncio.run(co.run_dev_diagnose_openai_compatible(timeout_seconds=10))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is True
    assert result.network.targets == ("openai-compatible",)


def test_diagnose_request_failure_classified(monkeypatch):
    async def fake_diagnose(timeout_seconds=30.0):
        return {
            "ok": False,
            "provider": "openai-compatible",
            "checks": [{"name": "chat", "status": "error"}],
            "error_type": "network_error",
            "error": "request failed",
        }

    monkeypatch.setattr(co.control_executors, "_execute_diagnose_openai_compatible", fake_diagnose)
    result = asyncio.run(co.run_dev_diagnose_openai_compatible())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "network_error"
    assert result.network.attempted is True


def _serve_stream_error(status: int, body: bytes) -> ThreadingHTTPServer:
    """Start a process-local localhost server that fails every POST.

    The streamed probe reads the status line and closes the connection
    without draining the body; the handler tolerates that write failure.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            try:
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass
            finally:
                self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _serve_status_error(status: int, body: bytes) -> ThreadingHTTPServer:
    """Localhost server that fails every POST and GET with a fixed status
    and a body that echoes credentials (used to prove diagnostic
    containment)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _respond(self):
            try:
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass
            finally:
                self.close_connection = True

        def do_POST(self):
            self._respond()

        def do_GET(self):
            self._respond()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_primary_connection_diagnostics_never_embed_response_body(monkeypatch):
    """The doctor main-search connection diagnostics (chat + models probes)
    must carry status only: an upstream error body echoing credentials must
    never cross into the public V3 diagnostic JSON."""
    server = _serve_status_error(401, b'{"error": "echo api_key=sk-leaked-123 request fragment"}')
    try:
        port = server.server_address[1]
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-local-test-secret")
        monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "model-x")
        data = asyncio.run(co.control_executors._test_primary_connection(
            f"http://127.0.0.1:{port}", "sk-local-test-secret", "model-x"
        ))
    finally:
        server.shutdown()

    rendered = json.dumps(data)
    assert "sk-leaked-123" not in rendered
    assert "api_key=" not in rendered
    assert "sk-local-test-secret" not in rendered
    for check in (data.get("models_endpoint_test"), data.get("chat_completion_test")):
        if check is not None:
            assert check["message"].startswith("HTTP 401")


def test_primary_responses_diagnostic_never_embeds_response_body(monkeypatch):
    """The xAI Responses probe must carry status only when the upstream error
    body echoes credentials or request fragments."""
    server = _serve_status_error(429, b'{"error": "echo api_key=sk-leaked-123"}')
    try:
        port = server.server_address[1]
        monkeypatch.setenv("XAI_API_URL", f"http://127.0.0.1:{port}")
        data = asyncio.run(co.control_executors._test_primary_responses(
            f"http://127.0.0.1:{port}", "sk-local-test-secret", "model-x"
        ))
    finally:
        server.shutdown()

    rendered = json.dumps(data)
    assert "sk-leaked-123" not in rendered
    assert "api_key=" not in rendered
    assert data["message"] == "HTTP 429"


def test_diagnose_streamed_http_error_returns_typed_network_failure(monkeypatch):
    """A streamed 4xx/5xx response must be consumed safely and reported as a
    diagnostic check; ResponseNotRead/StreamClosed must never escape into a
    V3 INTERNAL_ERROR, and request secrets must stay out of the checks.
    Uses a real localhost socket because buffered mock transports do not
    expose unread response bodies."""
    server = _serve_stream_error(503, b'{"error": "upstream boom"}')
    try:
        port = server.server_address[1]
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-local-test-secret")
        monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "model-x")
        outcome = asyncio.run(co.run_dev_diagnose_openai_compatible(timeout_seconds=5))
    finally:
        server.shutdown()

    assert outcome.status is ControlOperationStatus.FAILED
    assert outcome.error is not None and outcome.error.type == "network_error"
    assert outcome.network.attempted is True
    assert outcome.network.targets == ("openai-compatible",)
    rendered = json.dumps(outcome.result_dict)
    assert "INTERNAL_ERROR" not in rendered
    assert "sk-local-test-secret" not in rendered
    assert "upstream boom" not in rendered
    assert "ResponseNotRead" not in rendered
    assert "StreamClosed" not in rendered
    stream_check = next(check for check in outcome.result_dict["checks"] if check.get("stream") is True)
    assert stream_check["status"] == "warning"
    assert stream_check["http_status"] == 503
    assert stream_check["message"] == "HTTP 503: 上游返回错误响应"
    assert "upstream boom" not in stream_check["message"]


def test_diagnose_streamed_http_4xx_and_5xx_share_fixed_redaction(monkeypatch):
    """Both 4xx and 5xx streamed failures produce deterministic checks that
    never echo the upstream raw payload."""
    for status in (429, 503):
        server = _serve_stream_error(status, b'{"error": "raw upstream secret"}')
        try:
            port = server.server_address[1]
            monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", f"http://127.0.0.1:{port}")
            monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-local-test-secret")
            monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "model-x")
            outcome = asyncio.run(co.run_dev_diagnose_openai_compatible(timeout_seconds=5))
        finally:
            server.shutdown()
        rendered = json.dumps(outcome.result_dict)
        assert outcome.status is ControlOperationStatus.FAILED
        assert outcome.error.type == "network_error"
        assert "sk-local-test-secret" not in rendered
        assert "raw upstream secret" not in rendered
        stream_check = next(check for check in outcome.result_dict["checks"] if check.get("stream") is True)
        assert stream_check["http_status"] == status
        assert "raw upstream secret" not in stream_check["message"]


def test_smoke_mock_is_network_free(monkeypatch, no_network_spies):
    async def fake_smoke(mode="mock"):
        return {
            "ok": True,
            "mode": mode,
            "cases": [{"name": "search", "provider_attempts": [{"provider": "tavily"}]}],
            "failed_cases": [],
            "degraded_cases": [],
            "providers_used": ["tavily", "openai-compatible"],
        }

    monkeypatch.setattr(co.control_executors, "_execute_smoke", fake_smoke)
    result = asyncio.run(co.run_dev_smoke(mode="mock"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.network.attempted is False
    assert result.network.targets == ()
    # Mock case data is result data, never a network fact: providers_used is
    # preserved in the canonical result while network metadata stays empty.
    assert result.result_dict["providers_used"] == ["tavily", "openai-compatible"]


def test_smoke_mock_case_table_is_all_green():
    """The real mock smoke case table (minimum-profile gates, fallback chains,
    deep-research plan matrix) stays green without network or configuration.
    ``dev smoke --mock`` executes exactly this table; every other pytest path
    fakes ``_execute_smoke``, so the real 24-case table needs this direct check."""
    from smart_search.control_executors import _smoke_mock

    result = asyncio.run(_smoke_mock(0.0))

    assert result["mode"] == "mock"
    assert result["ok"] is True
    assert result["failed_cases"] == []
    assert len(result["cases"]) >= 20
    assert all("name" in case and "ok" in case for case in result["cases"])


def test_smoke_invalid_mode_parameter_failure(monkeypatch):
    result = asyncio.run(co.run_dev_smoke(mode="bogus"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.network.attempted is False


def test_smoke_live_optional_degraded_records_targets(monkeypatch):
    async def fake_smoke(mode="mock"):
        return {
            "ok": True,
            "mode": mode,
            "cases": [{"name": "search", "status": "ok", "provider_attempts": [{"provider": "tavily"}]}],
            "failed_cases": [],
            "degraded_cases": [{"name": "fetch"}],
            "providers_used": ["tavily"],
        }

    monkeypatch.setattr(co.control_executors, "_execute_smoke", fake_smoke)
    result = asyncio.run(co.run_dev_smoke(mode="live"))
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.warnings == ("live smoke completed with optional degraded cases",)
    assert result.network.attempted is True
    assert result.network.targets == ("tavily",)


def test_smoke_live_failure_classified(monkeypatch):
    async def fake_smoke(mode="mock"):
        return {
            "ok": False,
            "mode": mode,
            "error": "critical check failed",
            "cases": [{"name": "search", "status": "error", "provider_attempts": [{"provider": "tavily"}]}],
            "failed_cases": ["search"],
            "degraded_cases": [],
            "providers_used": ["tavily"],
        }

    monkeypatch.setattr(co.control_executors, "_execute_smoke", fake_smoke)
    result = asyncio.run(co.run_dev_smoke(mode="live"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "network_error"
    assert result.error.retryable is True
    assert result.network.attempted is True


# ---------------------------------------------------------------------------
# Regression owner
# ---------------------------------------------------------------------------


def test_regression_process_success(monkeypatch):
    monkeypatch.setattr(
        co,
        "_execute_regression",
        lambda: {
            "ok": True,
            "exit_code": 0,
            "subprocess_started": True,
            "fallback": "",
            "test_files": ["tests/test_cli.py"],
        },
    )
    result = asyncio.run(co.run_dev_regression())
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.subprocess_started is True
    assert result.result_dict["exit_code"] == 0
    assert result.result_dict["test_files"] == ["tests/test_cli.py"]


def test_regression_process_failure_subprocess_error(monkeypatch):
    monkeypatch.setattr(
        co,
        "_execute_regression",
        lambda: {
            "ok": False,
            "exit_code": 1,
            "subprocess_started": True,
            "fallback": "",
            "test_files": ["tests/test_cli.py"],
        },
    )
    result = asyncio.run(co.run_dev_regression())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "subprocess_error"
    assert result.side_effects.subprocess_started is True


def test_regression_mock_fallback_success_no_process(monkeypatch):
    monkeypatch.setattr(
        co,
        "_execute_regression",
        lambda: {
            "ok": True,
            "exit_code": 0,
            "subprocess_started": False,
            "fallback": "mock_smoke",
            "failed_cases": [],
        },
    )
    result = asyncio.run(co.run_dev_regression())
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.subprocess_started is False
    assert result.result_dict["fallback"] == "mock_smoke"


def test_regression_mock_fallback_failure_config_error(monkeypatch):
    monkeypatch.setattr(
        co,
        "_execute_regression",
        lambda: {
            "ok": False,
            "exit_code": 3,
            "subprocess_started": False,
            "fallback": "mock_smoke",
            "failed_cases": ["x"],
        },
    )
    result = asyncio.run(co.run_dev_regression())
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert result.side_effects.subprocess_started is False


def test_regression_source_checkout_process_captures_child_output(monkeypatch):
    """The real source-checkout regression branch captures the pytest child
    stdout/stderr instead of inheriting the parent streams, so pytest text
    never reaches the V3 CLI stdout and never becomes a result field."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("stdout") is subprocess.PIPE, "child stdout must be captured"
        assert kwargs.get("stderr") is subprocess.PIPE, "child stderr must be captured"
        assert kwargs.get("errors") == "replace", "captured output must not change subprocess classification"
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout=".....F pytest progress text.....\n",
            stderr="warnings and teardown noise\n",
        )

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    data = co._execute_regression()
    assert calls
    assert calls[0]["cwd"] == str(Path(co.__file__).resolve().parents[2])
    assert data == {
        "ok": False,
        "exit_code": 1,
        "subprocess_started": True,
        "fallback": "",
        "test_files": list(co._REGRESSION_PATTERNS),
        "failed_cases": [],
    }
    assert "pytest" not in json.dumps(data)

    outcome = asyncio.run(co.run_dev_regression())
    assert outcome.status is ControlOperationStatus.FAILED
    assert outcome.error is not None and outcome.error.type == "subprocess_error"
    assert outcome.side_effects.subprocess_started is True
    assert outcome.result_dict["failed_cases"] == []
    assert "pytest" not in json.dumps(outcome.result_dict)

    def fake_run_ok(cmd, **kwargs):
        assert kwargs.get("stdout") is subprocess.PIPE
        assert kwargs.get("stderr") is subprocess.PIPE
        assert kwargs.get("errors") == "replace"
        return subprocess.CompletedProcess(cmd, 0, stdout="pytest dots\n", stderr="")

    monkeypatch.setattr(co.subprocess, "run", fake_run_ok)
    data = co._execute_regression()
    assert data["ok"] is True and data["exit_code"] == 0
    assert "failed_cases" not in data
    outcome = asyncio.run(co.run_dev_regression())
    assert outcome.status is ControlOperationStatus.COMPLETE
    assert "failed_cases" not in outcome.result_dict
    assert "pytest" not in json.dumps(outcome.result_dict)


def test_regression_source_checkout_failure_reports_failed_cases(monkeypatch):
    """A nonzero source-checkout run surfaces only deterministic failed pytest
    node ids; raw pytest output and reason text never become result fields."""

    def fake_run(cmd, **kwargs):
        assert kwargs.get("stdout") is subprocess.PIPE
        assert kwargs.get("stderr") is subprocess.PIPE
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout=(
                "tests/test_cli_v2.py::test_search F\n"
                "=========================== short test summary info ============================\n"
                "FAILED tests/test_cli_v2.py::test_search - AssertionError: boom\n"
                "FAILED tests/test_cli_v3.py::TestRegression::test_run[case-a] - AssertionError: x\n"
            ),
            stderr="teardown noise with secret-fixture-value\n",
        )

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    data = co._execute_regression()
    assert data["ok"] is False
    assert data["exit_code"] == 1
    assert data["subprocess_started"] is True
    assert data["failed_cases"] == [
        "tests/test_cli_v2.py::test_search",
        "tests/test_cli_v3.py::TestRegression::test_run[case-a]",
    ]
    assert "AssertionError" not in json.dumps(data)
    assert "secret-fixture-value" not in json.dumps(data)
    assert "pytest" not in json.dumps(data)

    outcome = asyncio.run(co.run_dev_regression())
    assert outcome.status is ControlOperationStatus.FAILED
    assert outcome.error is not None and outcome.error.type == "subprocess_error"
    assert outcome.result_dict["failed_cases"] == [
        "tests/test_cli_v2.py::test_search",
        "tests/test_cli_v3.py::TestRegression::test_run[case-a]",
    ]
    assert "AssertionError" not in json.dumps(outcome.result_dict)


def test_extract_failed_test_cases_recognizes_short_summary_records():
    output = (
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_cli_v2.py::test_search - AssertionError: boom\n"
        "FAILED tests/test_cli_v3.py::TestRegression::test_run[case-a] - AssertionError: x\n"
        "FAILED tests/test_cli_v3.py::TestRegression::test_run[case-a] - AssertionError: dup\n"
        "============================== 3 failed in 2.34s ===============================\n"
    )
    assert co._extract_failed_test_cases(output) == [
        "tests/test_cli_v2.py::test_search",
        "tests/test_cli_v3.py::TestRegression::test_run[case-a]",
    ]


def test_extract_failed_test_cases_is_conservative():
    output = (
        ".....F pytest progress text.....\n"
        "FAILED not-a-node-id\n"
        "FAILED tests/test_x.py - missing node separator\n"
        "FAILED tests/test_x.py::\n"
        "FAILED tests/test_x.py::test spaced param\n"
        "tests/test_x.py::test_y F\n"
        "WARNING tests/test_x.py::test_z - boom\n"
    )
    assert co._extract_failed_test_cases(output) == []


def test_extract_failed_test_cases_is_bounded():
    output = "\n".join(f"FAILED tests/test_f.py::test_{i} - boom" for i in range(600))
    cases = co._extract_failed_test_cases(output)
    assert len(cases) == co._FAILED_CASE_LIMIT
    assert cases[0] == "tests/test_f.py::test_0"
    assert cases[-1] == f"tests/test_f.py::test_{co._FAILED_CASE_LIMIT - 1}"


def test_regression_subprocess_timeout_deterministic_result(monkeypatch):
    """A hung source-checkout child is bounded by the config-free subprocess
    timeout. TimeoutExpired yields a deterministic structured result: stable
    nonzero exit, explicit ``subprocess_timeout`` flag, empty failed cases, and
    no leaked partial child output even when the exception carries captured
    bytes/text."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("stdout") is subprocess.PIPE
        assert kwargs.get("stderr") is subprocess.PIPE
        assert kwargs.get("timeout") == co._REGRESSION_SUBPROCESS_TIMEOUT_SECONDS
        # subprocess.run attaches partially captured output to TimeoutExpired;
        # the owner must ignore it under the containment rule.
        raise subprocess.TimeoutExpired(
            cmd,
            kwargs.get("timeout"),
            output=".....F partial pytest progress.....\n",
            stderr="partial teardown with secret-fixture-value\n",
        )

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    data = co._execute_regression()
    assert calls
    assert data == {
        "ok": False,
        "exit_code": co._REGRESSION_TIMEOUT_EXIT_CODE,
        "subprocess_started": True,
        "fallback": "",
        "test_files": list(co._REGRESSION_PATTERNS),
        "failed_cases": [],
        "subprocess_timeout": True,
    }
    assert "pytest" not in json.dumps(data)
    assert "secret-fixture-value" not in json.dumps(data)

    outcome = asyncio.run(co.run_dev_regression())
    assert outcome.status is ControlOperationStatus.FAILED
    assert outcome.error is not None and outcome.error.type == "subprocess_error"
    assert outcome.side_effects.subprocess_started is True
    assert outcome.result_dict["subprocess_timeout"] is True
    assert outcome.result_dict["exit_code"] == co._REGRESSION_TIMEOUT_EXIT_CODE
    assert outcome.result_dict["failed_cases"] == []
    assert "pytest" not in json.dumps(outcome.result_dict)
    assert "secret-fixture-value" not in json.dumps(outcome.result_dict)


def test_regression_timeout_drops_captured_bytes(monkeypatch):
    """Captured timeout bytes are private just like text-mode child output."""
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd,
            kwargs.get("timeout"),
            output=b"partial output api_key=leaked-bytes",
            stderr=b"secret-fixture-bytes",
        )

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    data = co._execute_regression()
    outcome = asyncio.run(co.run_dev_regression())

    for rendered in (json.dumps(data), json.dumps(outcome.result_dict), outcome.error.message):
        assert "leaked-bytes" not in rendered
        assert "secret-fixture-bytes" not in rendered
    assert data["subprocess_timeout"] is True
    assert outcome.error.type == "subprocess_error"


def test_regression_subprocess_timeout_real_hanging_child(monkeypatch):
    """A real child that does not finish within the bound is killed and reaped
    by subprocess.run; the owner returns the deterministic timeout result
    instead of blocking indefinitely. Uses the real source-checkout branch with
    a shrunken bound so the pytest child genuinely outlives it."""
    monkeypatch.setattr(co, "_REGRESSION_SUBPROCESS_TIMEOUT_SECONDS", 1.0)
    start = time.perf_counter()
    data = co._execute_regression()
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, "the timeout must bound the real hanging child"
    assert data["subprocess_timeout"] is True
    assert data["ok"] is False
    assert data["exit_code"] == co._REGRESSION_TIMEOUT_EXIT_CODE
    assert data["subprocess_started"] is True
    assert data["failed_cases"] == []
    assert "pytest" not in json.dumps(data)

    outcome = asyncio.run(co.run_dev_regression())
    assert outcome.status is ControlOperationStatus.FAILED
    assert outcome.error is not None and outcome.error.type == "subprocess_error"
    assert outcome.result_dict["subprocess_timeout"] is True
    assert outcome.result_dict["failed_cases"] == []



# ---------------------------------------------------------------------------
# Skills owners
# ---------------------------------------------------------------------------


def test_skills_status_read_only_complete(monkeypatch):
    monkeypatch.setattr(co, "parse_skill_targets", lambda raw: ["codex"])
    monkeypatch.setattr(
        co.skill_installer,
        "status_skill_targets",
        lambda ids, project_root=None: {
            "ok": True,
            "root": "/tmp",
            "selected": ids,
            "skill": "smart-search-cli",
            "bundled_files": 1,
            "bundled_hash": "h",
            "targets": [],
            "status_counts": {"missing": 1},
        },
    )
    result = asyncio.run(co.run_dev_skills_status(targets="codex"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.filesystem.read is True
    assert result.side_effects.filesystem.write_attempted is False
    assert result.side_effects.filesystem.write_committed is False
    assert result.network.attempted is False


def test_skills_status_invalid_target_no_write(monkeypatch):
    monkeypatch.setattr(
        co,
        "parse_skill_targets",
        lambda raw: (_ for _ in ()).throw(SkillInstallError("Unknown skill target(s): nope")),
    )
    result = asyncio.run(co.run_dev_skills_status(targets="nope"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.filesystem.write_attempted is False


def test_skills_status_execution_failure_classified(monkeypatch):
    monkeypatch.setattr(co, "parse_skill_targets", lambda raw: ["codex"])
    monkeypatch.setattr(
        co.skill_installer,
        "status_skill_targets",
        lambda ids, project_root=None: (_ for _ in ()).throw(OSError("permission denied")),
    )
    result = asyncio.run(co.run_dev_skills_status(targets="codex"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "filesystem_error"
    assert result.side_effects.filesystem.write_attempted is False


def test_skills_update_complete_write_committed(monkeypatch):
    monkeypatch.setattr(co, "parse_skill_targets", lambda raw: ["codex"])
    monkeypatch.setattr(
        co.skill_installer,
        "install_skill_targets",
        lambda ids, project_root=None: {
            "ok": True,
            "root": "/tmp",
            "installed": [{"target": "codex"}],
            "skipped": [],
            "failed": [],
            "selected": ids,
            "installed_count": 1,
            "skipped_count": 0,
            "failed_count": 0,
        },
    )
    result = asyncio.run(co.run_dev_skills_update(targets="codex"))
    assert result.status is ControlOperationStatus.COMPLETE
    assert result.side_effects.filesystem.write_attempted is True
    assert result.side_effects.filesystem.write_committed is True


def test_skills_update_partial_degraded(monkeypatch):
    monkeypatch.setattr(co, "parse_skill_targets", lambda raw: ["codex", "claude"])
    monkeypatch.setattr(
        co.skill_installer,
        "install_skill_targets",
        lambda ids, project_root=None: {
            "ok": False,
            "root": "/tmp",
            "installed": [{"target": "codex"}],
            "skipped": [],
            "failed": [{"target": "claude"}],
            "selected": ids,
            "installed_count": 1,
            "skipped_count": 0,
            "failed_count": 1,
        },
    )
    result = asyncio.run(co.run_dev_skills_update(targets="codex,claude"))
    assert result.status is ControlOperationStatus.DEGRADED
    assert result.error is None
    assert result.warnings == ("some skill targets were updated and some failed",)
    assert result.side_effects.filesystem.write_attempted is True
    assert result.side_effects.filesystem.write_committed is True


def test_skills_update_total_failure_attempt_no_commit(monkeypatch):
    monkeypatch.setattr(co, "parse_skill_targets", lambda raw: ["codex", "claude"])
    monkeypatch.setattr(
        co.skill_installer,
        "install_skill_targets",
        lambda ids, project_root=None: {
            "ok": False,
            "root": "/tmp",
            "installed": [],
            "skipped": [],
            "failed": [{"target": "codex"}, {"target": "claude"}],
            "selected": ids,
            "installed_count": 0,
            "skipped_count": 0,
            "failed_count": 2,
        },
    )
    result = asyncio.run(co.run_dev_skills_update(targets="codex,claude"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "filesystem_error"
    assert result.side_effects.filesystem.write_attempted is True
    assert result.side_effects.filesystem.write_committed is False


def test_skills_update_invalid_target_no_write(monkeypatch):
    monkeypatch.setattr(
        co,
        "parse_skill_targets",
        lambda raw: (_ for _ in ()).throw(SkillInstallError("Unknown skill target(s): bogus")),
    )
    result = asyncio.run(co.run_dev_skills_update(targets="bogus"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.filesystem.write_attempted is False


def test_skills_update_symlink_preflight_reports_no_write(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("skill")
    victim = tmp_path / "victim.txt"
    victim.write_text("victim")
    dest = tmp_path / ".agents" / "skills" / "smart-search-cli"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").symlink_to(victim)

    result = asyncio.run(
        co.run_dev_skills_update(
            targets="generic",
            project_root=tmp_path,
        )
    )
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "filesystem_error"
    assert result.side_effects.filesystem.write_attempted is False
    assert result.side_effects.filesystem.write_committed is False
    assert victim.read_text() == "victim"


def test_skills_update_mixed_standalone_selector_parameter_error():
    # Real parser: skip/none/all are standalone-only; a mix raises
    # SkillInstallError which the owner maps to parameter_error.
    result = asyncio.run(co.run_dev_skills_update(targets="skip,codex"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.filesystem.write_attempted is False
    assert result.side_effects.filesystem.write_committed is False
    result = asyncio.run(co.run_dev_skills_update(targets="all,codex"))
    assert result.status is ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert result.side_effects.filesystem.write_attempted is False


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def no_network_spies(monkeypatch):
    """Fail-if-called network/probe/client spies for local-only operations."""

    async def boom(*args, **kwargs):
        raise AssertionError("network should not be called for a local-only operation")

    monkeypatch.setattr(co, "run_probe_adapter", boom)
    monkeypatch.setattr(co.control_executors, "_safe_test_main_provider_connection", boom)
    monkeypatch.setattr(co.capability_service, "route", boom)
    monkeypatch.setattr(co.capability_service, "route_calibrate", boom)
    monkeypatch.setattr(co.control_executors, "_execute_diagnose_openai_compatible", boom)
    monkeypatch.setattr(co.control_executors, "_execute_smoke", boom)
    monkeypatch.setattr(co.control_executors, "_execute_doctor_probe", boom)