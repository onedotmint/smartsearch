from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from smart_search.cli import main
from smart_search import control_executors


def _payload(capsys):
    return json.loads(capsys.readouterr().out)


def test_v3_config_read_and_write_metadata(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))
    assert main(["config", "list"]) == 0
    listed = _payload(capsys)
    assert listed["operation"] == "config.list"
    assert listed["result"]["values"] == {}
    assert listed["side_effects"]["config"] == {
        "read": True, "write_attempted": False, "write_committed": False,
    }

    assert main(["config", "set", "XAI_API_KEY", "raw-secret"]) == 0
    written = _payload(capsys)
    assert written["operation"] == "config.set"
    assert written["side_effects"]["config"]["write_committed"] is True
    assert "raw-secret" not in json.dumps(written)


def test_v3_config_parameter_failure_does_not_attempt_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))
    assert main(["config", "set", "SMART_SEARCH_API_KEY", "old"]) == 2
    payload = _payload(capsys)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["side_effects"]["config"]["write_attempted"] is False


def test_v3_route_write_projects_masked_routes(monkeypatch, capsys):
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlMutationFacts,
        ControlOperationOutcome,
        ControlOperationStatus,
        ControlSideEffectFacts,
    )
    from smart_search.execution_primitives import ExecutionMetadata

    calls = []

    async def fake_add(*args, **kwargs):
        calls.append((args, kwargs))
        return ControlOperationOutcome(
            operation="provider.routes.add",
            status=ControlOperationStatus.COMPLETE,
            result={
                "action": "add",
                "routes": [{
                    "id": "primary", "provider": "openai-compatible",
                    "api_url": "https://user:pass@example.com/v1?api_key=route-secret",
                    "api_key": "route-secret", "model": "model-a",
                }],
                "route_count": 1,
                "current_route_id": "primary",
                "current_route": {"id": "primary", "provider": "openai-compatible", "api_key": "route-secret", "model": "model-a"},
            },
            side_effects=ControlSideEffectFacts(
                config=ControlMutationFacts(read=True, write_attempted=True, write_committed=True)
            ),
            metadata=ExecutionMetadata("provider.routes.add", 0),
        )

    monkeypatch.setattr(control_operations, "run_provider_routes_add", fake_add)
    code = main([
        "provider", "routes", "add",
        "--id", "primary", "--provider", "openai-compatible",
        "--api-url", "https://user:pass@example.com/v1?api_key=route-secret",
        "--api-key", "route-secret", "--model", "model-a",
    ])
    assert code == 0
    payload = _payload(capsys)
    assert len(calls) == 1
    assert payload["operation"] == "provider.routes.add"
    assert payload["result"]["routes"][0]["id"] == "primary"
    assert payload["side_effects"]["config"]["write_committed"] is True
    rendered = json.dumps(payload)
    assert "route-secret" not in rendered
    assert "user:pass" not in rendered


def test_v3_route_list_double_sanitization_preserves_endpoint(monkeypatch, capsys):
    """A second redaction pass over an already masked route URL keeps the
    host, path and non-sensitive query visible while credentials stay hidden."""
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlMutationFacts,
        ControlNetworkFacts,
        ControlOperationOutcome,
        ControlOperationStatus,
        ControlSideEffectFacts,
    )
    from smart_search.execution_primitives import ExecutionMetadata
    from smart_search.security import redact_url_credentials

    once_masked = redact_url_credentials(
        "https://user:pass@relay.example/v1?api_key=route-secret&region=cn"
    )
    assert once_masked.startswith("https://[REDACTED]@relay.example")

    async def fake_list():
        return ControlOperationOutcome(
            operation="provider.routes.list",
            status=ControlOperationStatus.COMPLETE,
            result={
                "routes": [{
                    "id": "primary", "provider": "openai-compatible",
                    "api_url": once_masked, "model": "model-a",
                }],
                "route_count": 1,
            },
            network=ControlNetworkFacts(),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata("provider.routes.list", 0),
        )

    monkeypatch.setattr(control_operations, "run_provider_routes_list", fake_list)
    assert main(["provider", "routes", "list"]) == 0
    payload = _payload(capsys)
    url = payload["result"]["routes"][0]["api_url"]
    assert url == once_masked
    assert "relay.example" in url
    assert "/v1" in url
    assert "region=cn" in url
    assert "user:pass" not in url
    assert "route-secret" not in url


def test_v3_provider_catalog_and_probe_metadata(monkeypatch, tmp_path, capsys):
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlNetworkFacts,
        ControlOperationOutcome,
        ControlOperationStatus,
    )
    from smart_search.execution_primitives import ExecutionError, ExecutionMetadata

    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))
    assert main(["provider", "list"]) == 0
    catalog = _payload(capsys)
    assert catalog["operation"] == "provider.catalog.list"
    assert catalog["network"]["attempted"] is False
    assert catalog["result"]["provider_count"] > 0

    async def fake_probe(provider):
        return ControlOperationOutcome(
            operation="provider.probe",
            status=ControlOperationStatus.FAILED,
            result={
                "provider": provider, "configured": False, "enabled": True,
                "eligible": False, "status": "not_configured", "message": "missing config",
            },
            error=ExecutionError("config_error", "missing config", False),
            network=ControlNetworkFacts(attempted=False, targets=(provider,)),
            metadata=ExecutionMetadata("provider.probe", 0),
        )

    monkeypatch.setattr(control_operations, "run_provider_probe", fake_probe)
    assert main(["provider", "probe", "tavily"]) == 3
    probe = _payload(capsys)
    assert probe["error"]["code"] == "CONFIGURATION_ERROR"
    assert probe["network"] == {
        "policy": "explicit", "scope": "single_provider", "attempted": False, "targets": ["tavily"],
    }


def test_v3_doctor_status_and_partial_probe(monkeypatch, capsys):

    monkeypatch.setattr(control_executors, "_execute_doctor_status", lambda: {
        "ok": True, "local_only": True, "config_storage_ok": True,
        "minimum_profile": "off", "minimum_profile_ok": True,
        "core_evidence_ready": True, "core_evidence_path": {},
        "capability_status": {}, "intent_router_status": {},
    })
    assert main(["doctor", "status"]) == 0
    status = _payload(capsys)
    assert status["operation"] == "doctor.status"
    assert status["network"]["policy"] == "none"
    assert status["network"]["attempted"] is False

    async def fake_doctor():
        return {
            "ok": False, "error_type": "network_error", "error": "one route failed",
            "minimum_profile": "standard", "minimum_profile_ok": True,
            "main_search_connection_tests": {
                "primary": {"status": "ok", "provider": "openai-compatible"},
                "backup": {"status": "timeout", "provider": "xai-responses"},
            },
        }

    monkeypatch.setattr(control_executors, "_execute_doctor_probe", fake_doctor)
    assert main(["doctor", "probe"]) == 0
    probe = _payload(capsys)
    assert probe["status"] == "degraded"
    assert probe["network"]["attempted"] is True
    assert set(probe["network"]["targets"]) == {"openai-compatible", "xai-responses"}

    async def owner_degraded_doctor():
        return {
            "ok": True, "degraded": True, "degraded_reason": "optional capabilities unavailable",
            "minimum_profile": "standard", "minimum_profile_ok": True,
            "main_search_connection_tests": {
                "primary": {"status": "ok", "provider": "openai-compatible"},
            },
            "exa_connection_test": {"status": "not_configured", "message": "missing"},
        }

    monkeypatch.setattr(control_executors, "_execute_doctor_probe", owner_degraded_doctor)
    assert main(["doctor", "probe"]) == 0
    owner_degraded = _payload(capsys)
    assert owner_degraded["status"] == "degraded"
    assert "optional capabilities unavailable" in owner_degraded["meta"]["warnings"][0]
    assert owner_degraded["error"] is None


def test_v3_route_diagnostics_project_degraded_and_network(monkeypatch, capsys):
    from smart_search import capability_service

    async def fake_route(query, validation="", mode=""):
        return {
            "ok": True, "query": query, "validation_level": validation or "balanced",
            "intent_router_mode": "hybrid", "required_capabilities": ["docs_search"],
            "missing_capabilities": [], "intent_signals": {}, "confidence": 0.8,
            "router_engines_used": ["rules", "embeddings"], "reasons": ["docs"],
            "supplemental_paths": ["docs_search"], "executed_search": False,
            "provider_selection": "not_executed", "degraded": True,
            "degraded_reason": "classifier unavailable",
        }

    async def fake_calibrate(models=""):
        return {
            "ok": True, "models": ["a", "b"], "failed_models": ["b"],
            "model_results": [
                {"model": "a", "ok": True, "availability": "ok", "semantic_macro_f1": 0.9},
                {"model": "b", "ok": False, "availability": "failed", "error_type": "provider_error", "error": "failed"},
            ],
            "recommended_model": "a", "dataset_size": 2,
        }

    monkeypatch.setattr(capability_service, "route", fake_route)
    monkeypatch.setattr(capability_service, "route_calibrate", fake_calibrate)
    assert main(["dev", "route-explain", "docs"]) == 0
    route = _payload(capsys)
    assert route["status"] == "degraded"
    assert route["network"]["attempted"] is True

    assert main(["dev", "route-calibrate", "--models", "a,b"]) == 0
    calibration = _payload(capsys)
    assert calibration["status"] == "degraded"
    assert calibration["result"]["failed_models"] == ["b"]


def test_v3_diagnose_smoke_regression_and_skills(monkeypatch, capsys):
    from smart_search import control_operations, skill_installer

    async def fake_diagnose(timeout_seconds=30):
        return {
            "ok": False, "provider": "openai-compatible", "checks": [],
            "missing": ["OPENAI_COMPATIBLE_API_KEY"], "error_type": "config_error",
            "error": "missing OPENAI_COMPATIBLE_API_KEY",
        }

    async def fake_smoke(mode="mock"):
        return {"ok": True, "mode": mode, "cases": [], "failed_cases": [], "degraded_cases": []}

    monkeypatch.setattr(control_executors, "_execute_diagnose_openai_compatible", fake_diagnose)
    monkeypatch.setattr(control_executors, "_execute_smoke", fake_smoke)
    monkeypatch.setattr(control_operations, "_execute_regression", lambda: {
        "ok": False, "exit_code": 1, "subprocess_started": True, "fallback": "", "error_type": "subprocess_error", "error": "failed",
    })
    monkeypatch.setattr(skill_installer, "status_skill_targets", lambda ids, project_root="": {
        "ok": True, "root": project_root, "selected": ids, "skill": "smart-search-cli",
        "bundled_files": 1, "bundled_hash": "hash", "targets": [], "status_counts": {"missing": len(ids)},
    })
    monkeypatch.setattr(skill_installer, "install_skill_targets", lambda ids, project_root="": {
        "ok": False, "root": project_root, "selected": ids, "installed": [{"target": ids[0]}],
        "skipped": [], "failed": [{"target": ids[-1]}], "installed_count": 1,
        "skipped_count": 0, "failed_count": 1,
    })

    assert main(["dev", "diagnose", "openai-compatible"]) == 3
    diagnose = _payload(capsys)
    assert diagnose["network"]["attempted"] is False

    assert main(["dev", "smoke", "--mock"]) == 0
    smoke = _payload(capsys)
    assert smoke["status"] == "complete"
    assert smoke["network"]["attempted"] is False

    assert main(["dev", "regression"]) == 5
    regression = _payload(capsys)
    assert regression["error"]["code"] == "SUBPROCESS_FAILED"
    assert regression["side_effects"]["subprocess"]["started"] is True

    assert main(["dev", "skills", "status", "--targets", "codex"]) == 0
    skills_status = _payload(capsys)
    assert skills_status["side_effects"]["filesystem"]["read"] is True
    assert skills_status["side_effects"]["filesystem"]["write_attempted"] is False

    assert main(["dev", "skills", "status", "--targets", "not-a-real-target"]) == 2
    skills_bad = _payload(capsys)
    assert skills_bad["error"]["code"] == "INVALID_ARGUMENT"
    assert skills_bad["side_effects"]["filesystem"]["write_attempted"] is False

    assert main(["dev", "skills", "update", "--targets", "codex,claude"]) == 0
    skills_update = _payload(capsys)
    assert skills_update["status"] == "degraded"
    assert skills_update["side_effects"]["filesystem"]["write_attempted"] is True
    assert skills_update["side_effects"]["filesystem"]["write_committed"] is True


def test_v3_rejects_excluded_and_noncanonical_commands(capsys):
    # Canonical v3 namespaces with removed leaves/aliases stay v3-family.
    cases = (
        (["provider", "exa", "search", "q"], "3"),
        (["doctor"], "3"),
        (["model", "list"], "3"),
    )
    for argv, schema in cases:
        assert main(argv) == 2
        payload = _payload(capsys)
        assert payload["schema_version"] == schema
        assert payload["operation"] is None
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
    # The experimental namespace is not part of the canonical tree at all; it
    # falls back to the V2 root parser-error sentinel.
    assert main(["experimental", "anysearch", "domains"]) == 2
    payload = _payload(capsys)
    assert payload["schema_version"] == "2"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_v3_unknown_namespace_leaves_stay_v3_parse_errors(monkeypatch, capsys):
    # Unknown leaves below a canonical V3 namespace are ordinary V3 parse
    # errors; they must not be mislabelled as bare removed spellings.
    for argv in (["config", "badleaf"], ["doctor", "badleaf"], ["provider", "badleaf"], ["dev", "badleaf"]):
        assert main(argv) == 2
        payload = _payload(capsys)
        assert payload["schema_version"] == "3"
        assert payload["operation"] is None
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert "legacy_spelling" not in payload["error"]["details"]
        assert "removed" not in payload["error"]["message"]

    # Defined nested legacy aliases and exact bare reserved spellings keep
    # their replacement-family removal errors.
    cases = (
        (["config", "p"], "config p", "config.path"),
        (["config", "ls"], "config ls", "config.list"),
        (["config"], "config", "config path|list|set|unset"),
        (["doctor"], "doctor", "doctor probe"),
    )
    for argv, spelling, replacement in cases:
        assert main(argv) == 2
        payload = _payload(capsys)
        assert payload["schema_version"] == "3"
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert payload["error"]["details"]["legacy_spelling"] == spelling
        assert payload["error"]["details"]["replacement"] == replacement


def test_v1_and_v3_regression_use_one_shared_owner_per_invocation(monkeypatch, capsys):
    from smart_search import control_operations

    calls = []

    def fake_regression_result():
        calls.append("regression")
        return {"ok": True, "exit_code": 0, "subprocess_started": True, "fallback": ""}

    monkeypatch.setattr(control_operations, "_execute_regression", fake_regression_result)
    # Bare ``regression`` is a removed legacy spelling -> v3 family error.
    assert main(["regression"]) == 2
    payload = _payload(capsys)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["error"]["details"]["legacy_spelling"] == "regression"
    assert calls == []
    # The canonical v3 leaf runs the one shared owner once.
    assert main(["dev", "regression"]) == 0
    payload = _payload(capsys)
    assert payload["operation"] == "dev.regression"
    assert calls == ["regression"]


def test_v3_rejects_root_trace_before_owner(monkeypatch, capsys):
    from smart_search import control_operations

    # The removed v1 config wrapper is gone; the typed owner must also never
    # be reached for a v3 command carrying the root-only --trace flag.
    assert not hasattr(control_executors, "config_list")
    monkeypatch.setattr(
        control_operations,
        "run_config_list",
        lambda **_: (_ for _ in ()).throw(AssertionError("owner called")),
    )
    code = main(["--trace", "config", "list"])
    assert code == 2
    payload = _payload(capsys)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "trace" in payload["error"]["message"]
    assert payload["side_effects"]["config"]["read"] is False


def test_v3_packaged_regression_fallback_works_inside_event_loop(monkeypatch, capsys):
    from smart_search import control_operations

    async def fake_smoke(mode="mock"):
        return {"ok": True, "mode": mode, "failed_cases": [], "cases": []}

    monkeypatch.setattr(control_operations, "_regression_test_files_available", lambda _root: False)
    monkeypatch.setattr(control_executors, "_execute_smoke", fake_smoke)
    assert main(["dev", "regression"]) == 0
    payload = _payload(capsys)
    assert payload["operation"] == "dev.regression"
    assert payload["status"] == "complete"
    assert payload["result"]["fallback"] == "mock_smoke"
    assert payload["side_effects"]["subprocess"]["started"] is False


def test_v3_regression_source_checkout_emits_single_json_document(monkeypatch, capsys):
    """The source-checkout subprocess branch captures the pytest child
    output, so the V3 CLI stdout is exactly one parseable JSON document with
    no pytest progress text before it."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("stdout") is subprocess.PIPE, "child stdout must be captured"
        assert kwargs.get("stderr") is subprocess.PIPE, "child stderr must be captured"
        return subprocess.CompletedProcess(
            cmd, 1, stdout=".....F pytest progress text.....\n", stderr="teardown noise\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert main(["dev", "regression", "--format", "json"]) == 5
    out = capsys.readouterr().out
    payload = json.loads(out)  # raises if pytest text precedes the envelope
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "dev.regression"
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "SUBPROCESS_FAILED"
    assert payload["side_effects"]["subprocess"]["started"] is True
    assert "pytest" not in out

    def fake_run_ok(cmd, **kwargs):
        assert kwargs.get("stdout") is subprocess.PIPE
        assert kwargs.get("stderr") is subprocess.PIPE
        return subprocess.CompletedProcess(cmd, 0, stdout="pytest dots\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_ok)
    assert main(["dev", "regression", "--format", "json"]) == 0
    payload = _payload(capsys)
    assert payload["status"] == "complete"
    assert payload["error"] is None


def _serve_stream_error(status: int, body: bytes) -> ThreadingHTTPServer:
    """Process-local localhost server that fails every POST."""

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


def test_v3_diagnose_streamed_http_error_is_typed_provider_failure(monkeypatch, capsys):
    """A localhost streamed 5xx produces a typed V3 provider/network failure
    (exit 4, PROVIDER_UNAVAILABLE, truthful network facts) with no
    INTERNAL_ERROR, no request secret, and no raw upstream payload."""
    server = _serve_stream_error(503, b'{"error": "raw upstream secret"}')
    try:
        port = server.server_address[1]
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-local-test-secret")
        monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "model-x")
        assert main(["dev", "diagnose", "openai-compatible"]) == 4
    finally:
        server.shutdown()
    payload = _payload(capsys)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "dev.diagnose.openai-compatible"
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert payload["error"]["code"] != "INTERNAL_ERROR"
    assert payload["network"] == {
        "policy": "explicit", "scope": "diagnostic",
        "attempted": True, "targets": ["openai-compatible"],
    }
    rendered = json.dumps(payload)
    assert "sk-local-test-secret" not in rendered
    assert "raw upstream secret" not in rendered
    assert "ResponseNotRead" not in rendered
    assert "StreamClosed" not in rendered
    stream_check = next(
        check for check in payload["result"]["checks"] if check.get("stream") is True
    )
    assert stream_check["status"] == "warning"
    assert stream_check["http_status"] == 503
    assert stream_check["message"] == "HTTP 503: 上游返回错误响应"
    assert "raw upstream secret" not in stream_check["message"]


def test_v3_dev_regression_format_owner_once_and_strict_rejection(monkeypatch, capsys):
    """Explicit dev regression --format json|markdown|content selects one
    stdout document and runs the owner exactly once per invocation;
    --output, --force, and an invalid format value are strictly rejected
    with a v3 INVALID_ARGUMENT document and no owner execution."""
    from smart_search import control_operations

    calls = []
    monkeypatch.setattr(control_operations, "_regression_test_files_available", lambda _root: False)

    async def fake_smoke(mode="mock"):
        return {"ok": True, "mode": mode, "cases": [], "failed_cases": [], "degraded_cases": []}

    monkeypatch.setattr(control_executors, "_execute_smoke", fake_smoke)

    def fake_regression():
        calls.append("regression")
        return {"ok": True, "exit_code": 0, "subprocess_started": True, "fallback": ""}

    monkeypatch.setattr(control_operations, "_execute_regression", fake_regression)

    assert main(["dev", "regression", "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# V3 Regression")
    assert '"schema_version"' not in out
    assert calls == ["regression"]

    assert main(["dev", "regression", "--format", "content"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("dev.regression COMPLETE:")
    assert calls == ["regression", "regression"]

    assert main(["dev", "regression", "--format", "json"]) == 0
    payload = _payload(capsys)
    assert payload["operation"] == "dev.regression"
    assert calls == ["regression", "regression", "regression"]

    # --output / --force remain strictly rejected without running the owner.
    for argv in (
        ["dev", "regression", "--output", "out.md"],
        ["dev", "regression", "--force"],
    ):
        assert main(argv) == 2
        payload = _payload(capsys)
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert payload["operation"] == "dev.regression"
    assert calls == ["regression", "regression", "regression"]

    # An invalid format value is rejected before any owner execution.
    assert main(["dev", "regression", "--format", "yaml"]) == 2
    payload = _payload(capsys)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "yaml" in payload["error"]["message"]
    assert calls == ["regression", "regression", "regression"]



def test_v3_internal_error_does_not_claim_writes_or_leak_secrets(monkeypatch, capsys):
    from smart_search import control_operations

    def boom(key, value):
        raise RuntimeError(f"boom-{value}")

    monkeypatch.setattr(control_operations, "run_config_set", boom)
    code = main(["config", "set", "XAI_API_KEY", "secret-token-xyz"])
    assert code == 5
    payload = _payload(capsys)
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["side_effects"]["config"] == {
        "read": True, "write_attempted": False, "write_committed": False,
    }
    assert "secret-token-xyz" not in json.dumps(payload)


def test_v3_config_atomic_write_failure_reports_attempt_without_commit(monkeypatch, tmp_path, capsys):
    from smart_search.config import ConfigStorageError

    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))

    def boom(key, value):
        raise ConfigStorageError("atomic replace failed")

    # control_executors.config_set catches ConfigStorageError from config.set_config_value.
    monkeypatch.setattr(control_executors.config, "set_config_value", boom)
    code = main(["config", "set", "XAI_API_KEY", "raw-secret"])
    assert code == 3
    payload = _payload(capsys)
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert payload["side_effects"]["config"]["write_attempted"] is True
    assert payload["side_effects"]["config"]["write_committed"] is False
    assert "raw-secret" not in json.dumps(payload)
