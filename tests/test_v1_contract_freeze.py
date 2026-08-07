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

    assert live["canonical_top_level"] == CANONICAL_TOP_LEVEL_COMMANDS
    assert len(live["canonical_top_level"]) == 16
    assert live["aliases"] == ALIAS_TO_CANONICAL
    assert set(live["root_help"]) == set(ROOT_HELP_COMMANDS)
    assert set(live["root_help"]) == set(PUBLIC_COMMANDS)
    assert set(ROOT_HELP_COMMANDS).issubset(set(CANONICAL_TOP_LEVEL_COMMANDS))

    for parent, expected in NESTED_CANONICAL_SUBCOMMANDS.items():
        assert live["nested"][parent]["canonical"] == expected
        assert live["nested"][parent]["aliases"] == NESTED_ALIAS_TO_CANONICAL[parent]


def test_cli_inventory_aliases_and_nested_commands_parse_to_canonical():
    parser = build_parser()

    for alias, canonical in ALIAS_TO_CANONICAL.items():
        if canonical in {"search", "route", "fetch", "map", "deep", "research"}:
            argv = {
                "search": [alias, "query"],
                "route": [alias, "query"],
                "fetch": [alias, "https://example.com"],
                "map": [alias, "https://example.com"],
                "deep": [alias, "query"],
                "research": [alias, "query"],
            }[canonical]
        elif canonical in {"route-calibrate", "smoke", "doctor", "regression"}:
            argv = [alias]
        elif canonical == "diagnose":
            argv = [alias, "openai-compatible"]
        elif canonical == "model":
            argv = [alias, "current"]
        elif canonical == "skills":
            argv = [alias, "status"]
        elif canonical == "setup":
            argv = [alias, "--non-interactive"]
        elif canonical == "config":
            argv = [alias, "list"]
        else:
            argv = [alias]

        assert parser.parse_args(argv).command == canonical

    for parent, aliases in NESTED_ALIAS_TO_CANONICAL.items():
        dest = {
            "config": "config_command",
            "model": "model_command",
            "skills": "skills_command",
        }[parent]
        for alias, canonical in aliases.items():
            if parent == "config" and canonical == "set":
                argv = [parent, alias, "XAI_MODEL", "grok"]
            elif parent == "config" and canonical == "unset":
                argv = [parent, alias, "XAI_MODEL"]
            elif parent == "model" and canonical == "add":
                argv = [
                    parent,
                    alias,
                    "--id",
                    "primary",
                    "--api-url",
                    "https://relay.example/v1",
                    "--api-key",
                    "secret",
                    "--model",
                    "model-a",
                ]
            elif parent == "model" and canonical == "remove":
                argv = [parent, alias, "primary"]
            else:
                argv = [parent, alias]
            assert getattr(parser.parse_args(argv), dest) == canonical


def test_root_help_exposes_only_public_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in ROOT_HELP_COMMANDS:
        assert command in out
    hidden = sorted(set(CANONICAL_TOP_LEVEL_COMMANDS) - set(ROOT_HELP_COMMANDS))
    for command in hidden:
        # Root help must not advertise advanced commands as top-level choices.
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


def test_v1_json_schema_constant_remains_one_while_parser_accepts_opt_in_v2():
    """Phase 3 exposes root-global --schema-version 2; v1 JSON constant stays 1."""
    parser = build_parser()
    args = parser.parse_args(["--schema-version", "2", "search", "query"])
    assert args.schema_version == "2"
    v1_args = parser.parse_args(["search", "query"])
    assert v1_args.schema_version == "1"
    assert SCHEMA_VERSION == "1"
    assert SCHEMA_VERSION != "2"
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

    code = cli.main(["capabilities", "--format", "json"])
    assert code == cli.EXIT_OK
    payload = assert_single_json_document(capsys.readouterr().out)
    assert_v1_envelope(payload, command="capabilities", ok=True)
    assert_has_keys(payload["data"], CAPABILITIES_SUCCESS_KEYS)
    assert payload["data"]["commands"]["capabilities"] is True
    assert payload["data"]["output_formats"] == ["json", "markdown", "content"]
    assert_no_secret_leak(payload, [secret])

    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "not-a-profile")
    code = cli.main(["capabilities", "--format", "json"])
    assert code != cli.EXIT_OK
    failed = assert_single_json_document(capsys.readouterr().out)
    assert_v1_envelope(failed, command="capabilities", ok=False)
    assert_structured_error(failed)
    assert failed["error_type"] == "parameter_error"
    assert failed["data"]["error_type"] == "parameter_error"


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

    async def fake_fetch(url):
        return {
            "ok": False,
            "url": url,
            "error_type": "network_error",
            "error": f"Bearer {secret} upstream failed",
            "OPENAI_COMPATIBLE_API_KEY": secret,
            "provider_attempts": [
                {
                    "capability": "web_fetch",
                    "provider": "tavily",
                    "status": "error",
                    "error_type": "auth_error",
                    "error": f"token={secret}",
                }
            ],
        }

    monkeypatch.setattr(cli.service, "fetch", fake_fetch)
    code = cli.main(["fetch", "https://example.com", "--format", "json"])
    assert code == cli.EXIT_NETWORK_ERROR
    captured = capsys.readouterr()
    payload = assert_single_json_document(captured.out)
    assert_v1_envelope(payload, command="fetch", ok=False)
    assert_structured_error(payload)
    assert_no_secret_leak(payload, [secret])
    assert secret not in captured.err


@pytest.mark.parametrize(
    ("command", "argv", "service_name", "result", "expected_exit"),
    [
        (
            "search",
            ["search", "query", "--response-mode", "evidence", "--format", "json"],
            "search",
            {
                "ok": True,
                "query": "query",
                "content": "",
                "sources": [],
                "provider_attempts": [
                    service_support._attempt("web_search", "tavily", "empty", 0.0, result_count=0)
                ],
            },
            cli.EXIT_OK,
        ),
        (
            "search",
            ["search", "query", "--response-mode", "evidence", "--format", "json"],
            "search",
            {
                "ok": True,
                "query": "query",
                "content": "",
                "sources": [{"url": "https://recovered.example", "provider": "firecrawl"}],
                "provider_attempts": [
                    service_support._attempt(
                        "web_search", "tavily", "error", 0.0, error_type="timeout", error="timed out"
                    ),
                    service_support._attempt("web_search", "firecrawl", "ok", 0.0, result_count=1),
                ],
                "fallback_used": True,
            },
            cli.EXIT_OK,
        ),
        (
            "fetch",
            ["fetch", "https://example.com", "--format", "json"],
            "fetch",
            {
                "ok": False,
                "url": "https://example.com",
                "error_type": "network_error",
                "error": "Bearer cli-freeze-secret unavailable",
                "provider_attempts": [
                    service_support._attempt(
                        "web_fetch", "tavily", "error", 0.0, error_type="timeout", error="cli-freeze-secret"
                    )
                ],
            },
            cli.EXIT_NETWORK_ERROR,
        ),
        (
            "map",
            ["map", "https://example.com", "--format", "json"],
            "map_site",
            {"ok": True, "url": "https://example.com", "results": []},
            cli.EXIT_OK,
        ),
        (
            "map",
            ["map", "https://example.com", "--format", "json"],
            "map_site",
            {
                "ok": False,
                "url": "https://example.com",
                "error_type": "timeout",
                "error": "map provider timed out",
            },
            cli.EXIT_RUNTIME_ERROR,
        ),
        (
            "doctor",
            ["doctor", "--format", "json"],
            "doctor",
            {
                "ok": False,
                "error_type": "config_error",
                "error": "OPENAI_COMPATIBLE_API_KEY=cli-freeze-secret is missing",
            },
            cli.EXIT_CONFIG_ERROR,
        ),
    ],
)
def test_v1_command_json_cli_states_keep_single_document_exit_and_redaction(
    monkeypatch, capsys, command, argv, service_name, result, expected_exit
):
    """Freeze CLI rendering/exit behavior without invoking providers or local credentials."""

    async def fake_service(*args, **kwargs):
        return result

    monkeypatch.setenv("TAVILY_API_KEY", "cli-freeze-secret")
    monkeypatch.setattr(cli.service, service_name, fake_service)
    code = cli.main(argv)
    captured = capsys.readouterr()
    payload = assert_single_json_document(captured.out)
    assert code == expected_exit
    assert_v1_envelope(payload, command=command, ok=result["ok"])
    if not result["ok"]:
        assert_structured_error(payload)
    assert_no_secret_leak(payload, ["cli-freeze-secret"])
    assert "cli-freeze-secret" not in captured.err
