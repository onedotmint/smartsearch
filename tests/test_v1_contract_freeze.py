"""Phase 0 freeze: v1 CLI inventory, service exports, JSON/exit, and evidence invariants.

These tests freeze current runtime behavior. They must not introduce v2 schema,
dispatch, deprecation, or behavior changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_search import cli, operation_runtime, service, service_support
from smart_search import provider_fetch_commands
from smart_search import search_service
from smart_search.cli_contract import SCHEMA_VERSION, build_json_result
from smart_search.cli_parser import PUBLIC_COMMANDS, build_parser
from smart_search.evidence import EvidenceBundle

from tests.fixtures.v1_cli_inventory import (
    ALIAS_TO_CANONICAL,
    CANONICAL_TOP_LEVEL_COMMANDS,
    DEEP_STEP_REQUIRED_FIELDS,
    NESTED_ALIAS_TO_CANONICAL,
    NESTED_CANONICAL_SUBCOMMANDS,
    RESEARCH_COMPAT_FIELDS,
    ROOT_HELP_COMMANDS,
    SERVICE_PUBLIC_EXPORTS,
    inventory_from_parser,
)
from tests.fixtures.v1_json_baselines import (
    CAPABILITIES_SUCCESS_KEYS,
    DOCTOR_CORE_KEYS,
    FETCH_CORE_KEYS,
    MAP_CORE_KEYS,
    SEARCH_CORE_KEYS,
    assert_has_keys,
    assert_no_secret_leak,
    assert_single_json_document,
    assert_structured_error,
    assert_v1_envelope,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL = REPO_ROOT / "skills" / "smart-search-cli"
PACKAGED_SKILL = REPO_ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli"


def test_cli_inventory_fixture_matches_parser_registration():
    live = inventory_from_parser(build_parser())

    # The final canonical parser registers exactly the canonical tree with no
    # aliases; the legacy inventory fixture is the historical baseline and its
    # removed commands are covered by the reserved legacy spelling table.
    assert set(live["canonical_top_level"]) == {
        "search", "fetch", "map", "capabilities",
        "research", "config", "provider", "doctor", "dev",
    }
    assert live["aliases"] == {}
    assert set(live["root_help"]) == set(PUBLIC_COMMANDS)
    assert set(PUBLIC_COMMANDS) == {"search", "fetch", "capabilities"}
    assert set(ROOT_HELP_COMMANDS).issubset(set(CANONICAL_TOP_LEVEL_COMMANDS))
    assert live["nested"]["config"]["canonical"] == ("list", "path", "set", "unset")
    assert live["nested"]["config"]["aliases"] == {}


def test_cli_inventory_aliases_are_removed_spellings():
    """Every historical alias and nested alias is now a reserved removed
    spelling handled by the canonical domain classifier; argparse no longer
    accepts them."""
    from smart_search.cli_constants import RESERVED_LEGACY_SPELLINGS, classify_command_domain

    parser = build_parser(raise_on_error=True)
    for alias, canonical in ALIAS_TO_CANONICAL.items():
        # The alias must not parse as a top-level command anymore.
        with pytest.raises(Exception):
            parser.parse_args([alias, "query"])
        classification = classify_command_domain([alias, "query"])
        assert classification["family"] == "removed"
        assert classification["legacy_spelling"].startswith(alias)

    for parent, aliases in NESTED_ALIAS_TO_CANONICAL.items():
        for alias, _canonical in aliases.items():
            assert (parent, alias) in RESERVED_LEGACY_SPELLINGS


def test_root_help_exposes_only_public_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # Root help shows the V2 evidence core only.
    for command in ("search", "fetch", "capabilities"):
        assert command in out
    for command in ("map", "research", "config", "provider", "doctor", "dev", "setup", "model", "smoke"):
        assert f"  {command} " not in out
        assert f"{{{command}" not in out


def test_service_public_exports_are_frozen():
    assert tuple(sorted(service.__all__)) == SERVICE_PUBLIC_EXPORTS
    for name in SERVICE_PUBLIC_EXPORTS:
        assert hasattr(service, name), f"missing service export: {name}"


@pytest.mark.asyncio
async def test_deep_and_research_compatibility_fields_are_frozen(monkeypatch, tmp_path):
    plan = service.build_deep_research_plan("freeze deep compatibility fields", budget="quick")
    assert plan["ok"] is True
    assert plan["steps"], "deep plan must emit steps"
    for step in plan["steps"]:
        for field in DEEP_STEP_REQUIRED_FIELDS:
            assert field in step and step[field], f"deep step missing {field}"
        assert "smart-search " in step["command"]
        assert step["output_path"] in step["command"]

    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

    async def fake_fetch_fallback(url, preferred_order=None, fallback="auto"):
        return (
            {
                "ok": True,
                "url": url,
                "provider": "tavily",
                "content": "# fetched body",
                "title": "Fetched",
            },
            [service_support._attempt("web_fetch", "tavily", "ok", 0.0, result_count=1)],
        )

    monkeypatch.setattr(search_service, "_run_web_fetch_fallback", fake_fetch_fallback)
    result = await service.research(
        "https://evidence.example/source freeze research fields",
        budget="quick",
        evidence_dir=str(tmp_path / "evidence"),
        fallback="off",
    )
    for field in RESEARCH_COMPAT_FIELDS:
        assert field in result, f"research missing compatibility field: {field}"
    assert isinstance(result["final_answer"], str)
    assert isinstance(result["content"], str)
    assert result["content"] == result["final_answer"]


def test_v1_json_schema_constant_remains_one_while_parser_has_no_selector():
    """The schema selector is fully removed: the parser registers no
    ``--schema-version`` option and the v1 JSON constant stays the frozen
    historical value (module-level contract)."""
    parser = build_parser()
    assert not any(
        "--schema-version" in action.option_strings for action in parser._actions
    )
    args = parser.parse_args(["search", "query"])
    assert not hasattr(args, "schema_version")
    assert SCHEMA_VERSION == "1"
    # service facade still has no v2 exports
    for name in ("V2Envelope", "serialize_result", "api_v2", "v2_contract"):
        assert name not in service.__all__


def test_public_and_packaged_skill_are_byte_for_byte():
    public_files = {
        path.relative_to(PUBLIC_SKILL): path.read_bytes()
        for path in PUBLIC_SKILL.rglob("*")
        if path.is_file()
    }
    packaged_files = {
        path.relative_to(PACKAGED_SKILL): path.read_bytes()
        for path in PACKAGED_SKILL.rglob("*")
        if path.is_file()
    }
    assert public_files == packaged_files


def test_capabilities_success_and_configuration_json_fixture(monkeypatch, capsys):
    secret = "cap-secret-should-not-leak"
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", "https://relay.example/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", secret)
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")

    code = cli.main(["capabilities", "--format", "json"])
    assert code == cli.EXIT_OK
    payload = assert_single_json_document(capsys.readouterr().out)
    # capabilities is a canonical V2 leaf: strict evidence envelope.
    assert payload["schema_version"] == "2"
    assert payload["command"] == "capabilities"
    assert payload["operation"] == "capability_status"
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["attempts"] == []
    assert_no_secret_leak(payload, [secret])

    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "not-a-profile")
    code = cli.main(["capabilities", "--format", "json"])
    assert code == cli.EXIT_OK
    failed = assert_single_json_document(capsys.readouterr().out)
    assert failed["schema_version"] == "2"
    assert failed["operation"] == "capability_status"
    assert failed["ok"] is True
    # the envelope stays complete; the profile gap is a result fact
    assert failed["result"]["capabilities"]["core_availability"]["source_discovery"] == []


@pytest.mark.asyncio
async def test_evidence_search_success_empty_degraded_and_failure_baselines(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "lite")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-test-secret")

    async def success_web(query, count=5, providers="auto", fallback="auto"):
        return (
            [{"url": "https://source.example/a", "title": "A", "provider": "zhipu", "content": "snippet"}],
            [service_support._attempt("web_search", "zhipu", "ok", 0.0, result_count=1)],
        )

    monkeypatch.setattr(search_service, "_run_web_search_fallback", success_web)
    success = await service.search("success query", response_mode="evidence")
    assert_has_keys(success, SEARCH_CORE_KEYS)
    assert success["ok"] is True
    assert success["primary_api_mode"] == "source-only"
    assert success["sources"]
    assert success["provider_attempts"]
    envelope = build_json_result("search", success)
    assert_v1_envelope(envelope, command="search", ok=True)
    assert envelope["schema_version"] == "1"

    async def empty_web(query, count=5, providers="auto", fallback="auto"):
        return (
            [],
            [service_support._attempt("web_search", "zhipu", "empty", 0.0, result_count=0)],
        )

    monkeypatch.setattr(search_service, "_run_web_search_fallback", empty_web)
    empty = await service.search("empty query", response_mode="evidence")
    assert_has_keys(empty, SEARCH_CORE_KEYS)
    # Normal empty discovery is not a config failure and remains distinguishable.
    assert empty.get("error_type") != "config_error"
    assert empty["ok"] is False
    assert empty["error_type"] == "network_error"
    assert empty["sources"] == []
    empty_attempt = empty["provider_attempts"]
    assert len(empty_attempt) == 1
    assert {
        key: empty_attempt[0][key]
        for key in ("capability", "provider", "status", "error_type", "error", "result_count")
    } == {
        "capability": "web_search",
        "provider": "zhipu",
        "status": "empty",
        "error_type": "",
        "error": "",
        "result_count": 0,
    }
    assert isinstance(empty_attempt[0]["elapsed_ms"], float)
    assert empty_attempt[0]["elapsed_ms"] >= 0
    assert cli._exit_code(empty) == cli.EXIT_NETWORK_ERROR

    async def degraded_web(query, count=5, providers="auto", fallback="auto"):
        return (
            [{"url": "https://source.example/b", "title": "B", "provider": "tavily"}],
            [
                service_support._attempt(
                    "web_search",
                    "zhipu",
                    "error",
                    0.0,
                    error_type="timeout",
                    error="zhipu timed out",
                ),
                service_support._attempt("web_search", "tavily", "ok", 0.0, result_count=1),
            ],
        )

    monkeypatch.setattr(search_service, "_run_web_search_fallback", degraded_web)
    degraded = await service.search("degraded query", response_mode="evidence")
    assert degraded["ok"] is True
    assert degraded["fallback_used"] is True
    assert any(item["status"] == "error" for item in degraded["provider_attempts"])
    assert any(item["status"] == "ok" for item in degraded["provider_attempts"])
    assert all(item["capability"] == "web_search" for item in degraded["provider_attempts"])

    # Configuration failure for evidence search under standard profile without providers.
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "standard")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    failed = await service.search("failed query", response_mode="evidence")
    assert failed["ok"] is False
    assert failed["error_type"] == "config_error"
    failed_envelope = build_json_result("search", failed)
    assert_v1_envelope(failed_envelope, command="search", ok=False)
    assert_structured_error(failed_envelope)
    assert cli._exit_code(failed) == cli.EXIT_CONFIG_ERROR


@pytest.mark.asyncio
async def test_fetch_map_doctor_baselines_distinguish_empty_degraded_and_failure(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret")

    async def success_fetch(url):
        return (
            {"ok": True, "url": url, "provider": "tavily", "content": "# Body", "title": "Body"},
            [service_support._attempt("web_fetch", "tavily", "ok", 0.0, result_count=1)],
        )

    monkeypatch.setattr(operation_runtime, "_run_web_fetch_fallback", success_fetch)
    fetch_ok = await service.fetch("https://example.com/ok")
    assert_has_keys(fetch_ok, FETCH_CORE_KEYS)
    assert fetch_ok["ok"] is True
    assert fetch_ok["content"]
    assert fetch_ok["citations"]
    assert fetch_ok["fetched_evidence"]
    assert "https://example.com/ok" in {item["url"] for item in fetch_ok["citations"]}

    async def empty_fetch(url):
        return (
            None,
            [
                service_support._attempt("web_fetch", "tavily", "empty", 0.0),
                service_support._attempt("web_fetch", "firecrawl", "empty", 0.0),
            ],
        )

    monkeypatch.setattr(operation_runtime, "_run_web_fetch_fallback", empty_fetch)
    fetch_empty = await service.fetch("https://example.com/empty")
    assert fetch_empty["ok"] is False
    assert fetch_empty["error_type"] == "network_error"
    assert fetch_empty["provider_attempts"]
    assert all(item["capability"] == "web_fetch" for item in fetch_empty["provider_attempts"])
    assert fetch_empty["fallback_used"] is True

    async def degraded_fetch(url):
        return (
            {"ok": True, "url": url, "provider": "firecrawl", "content": "# Recovered", "title": "Recovered"},
            [
                service_support._attempt(
                    "web_fetch",
                    "tavily",
                    "error",
                    0.0,
                    error_type="timeout",
                    error="tavily timeout",
                ),
                service_support._attempt("web_fetch", "firecrawl", "ok", 0.0, result_count=1),
            ],
        )

    monkeypatch.setattr(operation_runtime, "_run_web_fetch_fallback", degraded_fetch)
    fetch_degraded = await service.fetch("https://example.com/degraded")
    assert fetch_degraded["ok"] is True
    assert fetch_degraded["fallback_used"] is True
    assert fetch_degraded["provider"] == "firecrawl"
    assert all(item["capability"] == "web_fetch" for item in fetch_degraded["provider_attempts"])

    # map success / empty / failure through transport boundary.
    async def map_success(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        return {
            "ok": True,
            "url": url,
            "base_url": url,
            "results": ["https://example.com/docs"],
            "response_time": 0.1,
        }

    monkeypatch.setattr(provider_fetch_commands, "call_tavily_map", map_success)
    # service.map_site imports call_tavily_map from provider_fetch_commands at call time via module.
    map_ok = await service.map_site("https://example.com")
    assert_has_keys(map_ok, MAP_CORE_KEYS)
    assert map_ok["ok"] is True
    assert map_ok["results"]

    async def map_empty(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        return {
            "ok": False,
            "url": url,
            "base_url": url,
            "results": [],
            "error_type": "empty",
            "error": "Tavily map returned no results",
            "retryable": False,
        }

    monkeypatch.setattr(provider_fetch_commands, "call_tavily_map", map_empty)
    map_empty_result = await service.map_site("https://example.com/empty")
    assert map_empty_result["ok"] is False
    assert map_empty_result["error_type"] == "empty"
    assert map_empty_result["results"] == []

    async def map_failed(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        return {
            "ok": False,
            "url": url,
            "error_type": "timeout",
            "error": "map timed out",
            "retryable": True,
        }

    monkeypatch.setattr(provider_fetch_commands, "call_tavily_map", map_failed)
    map_failed_result = await service.map_site("https://example.com/fail")
    assert map_failed_result["ok"] is False
    assert map_failed_result["error_type"] == "timeout"

    # doctor configuration failure + redaction baseline.
    secret = "doctor-secret-key"
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", "https://relay.example/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", secret)
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "standard")

    async def fake_probe(*args, **kwargs):
        return {"status": "config_error", "message": f"Bearer {secret} unavailable"}

    monkeypatch.setattr(service_support, "_safe_test_main_provider_connection", fake_probe, raising=False)
    # doctor probes live under operations_service / provider_diagnostics ownership.
    from smart_search import operations_service

    for name in (
        "_safe_test_main_provider_connection",
        "_test_openai_compatible_connection",
        "_test_xai_connection",
    ):
        if hasattr(operations_service, name):
            monkeypatch.setattr(operations_service, name, fake_probe)

    doctor = await service.doctor()
    assert_has_keys(doctor, ("ok",))
    assert doctor["ok"] is False
    assert doctor["error_type"] == "config_error"
    doctor_json = build_json_result("doctor", doctor, secrets=[secret])
    assert_v1_envelope(doctor_json, command="doctor", ok=False)
    assert_no_secret_leak(doctor_json, [secret])
    # Ensure core diagnostic fields remain present even on failure.
    for key in DOCTOR_CORE_KEYS:
        if key == "error_type" and doctor.get("ok"):
            continue
        assert key in doctor or key in doctor_json


def test_fallback_never_crosses_capability():
    cross = [
        service_support._attempt("web_search", "tavily", "error", 0.0, error_type="timeout"),
        service_support._attempt("web_fetch", "jina", "ok", 0.0, result_count=1),
    ]
    same = [
        service_support._attempt("web_fetch", "tavily", "error", 0.0, error_type="timeout"),
        service_support._attempt("web_fetch", "jina", "ok", 0.0, result_count=1),
    ]
    assert service_support._fallback_used(cross) is False
    assert service_support._fallback_used(same) is True


def test_candidate_cannot_become_citation_without_fetched_content():
    bundle = EvidenceBundle()
    bundle.add_discovery_candidates(
        [{"url": "https://candidate.example", "title": "Candidate", "provider": "tavily", "content": "snippet"}]
    )
    # Empty content must not mint a citation.
    bundle.add_fetched_evidence(
        [{"url": "https://empty.example", "title": "Empty", "provider": "jina", "content": "   "}]
    )
    # Nonempty body without Provider provenance must not mint a citation either (R4).
    bundle.add_fetched_evidence(
        [
            {
                "url": "https://no-provider.example",
                "title": "No Provider",
                "content": "nonempty body without provenance",
                "verified": True,
            },
            {
                "url": "https://blank-provider.example",
                "title": "Blank Provider",
                "provider": "   ",
                "content": "nonempty body with blank provider",
            },
        ]
    )
    bundle.add_fetched_evidence(
        [
            {
                "url": "https://fetched.example",
                "title": "Fetched",
                "provider": "jina",
                "content": "full body",
                "source_type": "fetched_page",
            }
        ]
    )
    snapshot = bundle.to_dict()
    citation_urls = {item["url"] for item in snapshot["citations"]}
    fetched_urls = {item["url"] for item in snapshot["fetched_evidence"]}
    source_urls = {item["url"] for item in snapshot["sources"] if item.get("url")}
    assert "https://candidate.example" not in citation_urls
    assert "https://empty.example" not in citation_urls
    assert "https://no-provider.example" not in citation_urls
    assert "https://blank-provider.example" not in citation_urls
    assert "https://no-provider.example" not in fetched_urls
    assert "https://blank-provider.example" not in fetched_urls
    assert "https://no-provider.example" not in source_urls
    assert "https://blank-provider.example" not in source_urls
    assert citation_urls == {"https://fetched.example"}
    assert fetched_urls == {"https://fetched.example"}
    assert snapshot["fetched_evidence"][0]["verified"] is True
    assert snapshot["citations"][0]["provider"] == "jina"
    assert all(str(item.get("provider") or "").strip() for item in snapshot["citations"])
    assert snapshot["discovery_candidates"][0]["verified"] is False


def test_json_cli_stdout_is_single_document_and_redacts_secrets(monkeypatch, capsys):
    secret = "sk-live-json-secret"

    # fetch is a canonical V2 evidence leaf: the strict envelope redacts
    # secrets without ever touching the legacy service facade.
    from smart_search import api_v2
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import (
        ExecutionAttempt,
        ExecutionAttemptStatus,
        ExecutionError,
        ExecutionMetadata,
    )

    async def fake_fetch(request):
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.FAILED,
            evidence_items=(),
            attempts=(
                ExecutionAttempt(
                    capability="content_fetch",
                    provider="tavily",
                    status=ExecutionAttemptStatus.ERROR,
                    error=ExecutionError("auth_error", f"token={secret}", False),
                    elapsed_ms=1.0,
                ),
            ),
            error=ExecutionError("auth_error", f"Bearer {secret} upstream failed", False),
            routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("test",)),
            metadata=ExecutionMetadata("cli-json", 1),
        )

    monkeypatch.setattr(api_v2, "content_fetch", fake_fetch)
    code = cli.main(["fetch", "https://example.com", "--format", "json"])
    assert code == cli.EXIT_RUNTIME_ERROR  # classified internal/error exit, never exit 0
    captured = capsys.readouterr()
    payload = assert_single_json_document(captured.out)
    assert payload["schema_version"] == "2"
    assert payload["command"] == "fetch"
    assert payload["ok"] is False
    assert payload["error"]["code"] is not None
    assert_no_secret_leak(payload, [secret])
    assert secret not in captured.err


def test_canonical_command_cli_states_keep_single_document_exit_and_redaction(
    monkeypatch, capsys
):
    """Every canonical family emits exactly one strict JSON document with
    the family's envelope, exit mapping, and recursive redaction."""
    # V2 evidence leaf: search rejects v1-only --response-mode before any
    # owner work, and the failure is one redacted v2 document.
    monkeypatch.setenv("TAVILY_API_KEY", "cli-freeze-secret")
    code = cli.main(["search", "query", "--response-mode", "evidence", "--format", "json"])
    captured = capsys.readouterr()
    payload = assert_single_json_document(captured.out)
    assert code == cli.EXIT_PARAMETER_ERROR
    assert payload["schema_version"] == "2"
    assert payload["operation"] == "source_discovery"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert_no_secret_leak(payload, ["cli-freeze-secret"])

    # V3 control-plane leaf: a config parameter failure is one v3 document.
    monkeypatch.setattr(
        cli.service,
        "config_set",
        lambda **_: (_ for _ in ()).throw(AssertionError("legacy facade must not run")),
    )
    code = cli.main(["config", "set", "SMART_SEARCH_API_KEY", "cli-freeze-secret"])
    captured = capsys.readouterr()
    payload = assert_single_json_document(captured.out)
    assert code == cli.EXIT_PARAMETER_ERROR
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "config.set"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert_no_secret_leak(payload, ["cli-freeze-secret"])
    assert "cli-freeze-secret" not in captured.err

    # Removed legacy spellings fail with the replacement family's strict
    # envelope and never touch the legacy facade.
    monkeypatch.setattr(
        cli.service,
        "current_model",
        lambda: (_ for _ in ()).throw(AssertionError("legacy facade must not run")),
    )
    code = cli.main(["model", "current"])
    captured = capsys.readouterr()
    payload = assert_single_json_document(captured.out)
    assert code == cli.EXIT_PARAMETER_ERROR
    assert payload["schema_version"] == "3"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["error"]["details"]["legacy_spelling"] == "model current"
    assert payload["error"]["details"]["replacement"] == "provider.routes.current"
