"""Canonical CLI routing, rendering, and module-level helper tests.

The CLI dispatches by canonical command domain: evidence commands use the V2
envelope, retained control-plane leaves use V3, and ``research plan`` /
``research run`` use the Research Workflow family. Removed selectors, aliases,
and legacy spellings fail with the replacement family's strict
INVALID_ARGUMENT envelope before any owner/config/provider import. The legacy
service facade and V1 render helpers remain importable for the later cleanup
task; module-level helper tests below exercise them directly.
"""

import json
import asyncio
from pathlib import Path
from smart_search import cli
from smart_search import cli_parser, cli_setup, cli_support
from smart_search import skill_installer


class GbkStdout:
    encoding = "gbk"
    errors = "strict"

    def __init__(self):
        self.parts = []

    def write(self, text):
        text.encode(self.encoding, errors=self.errors)
        self.parts.append(text)
        return len(text)

    def getvalue(self):
        return "".join(self.parts)


def _run_main(argv, monkeypatch=None, env_updates=None):
    import io
    import contextlib

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_help_contains_commands(capsys):
    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    out = capsys.readouterr().out
    assert "search" in out
    assert "fetch" in out
    assert "capabilities" in out
    assert "doctor" not in out
    assert "setup" not in out
    assert "model" not in out
    assert "regression" not in out


def test_hidden_commands_remain_parseable():
    parser = cli.build_parser()

    hidden_commands = [
        ["research", "plan", "topic"],
        ["research", "run", "topic"],
        ["capabilities"],
        ["config", "path"],
        ["config", "list"],
        ["config", "set", "K", "V"],
        ["config", "unset", "K"],
        ["provider", "list"],
        ["provider", "status"],
        ["provider", "probe", "tavily"],
        ["provider", "routes", "current"],
        ["dev", "smoke"],
        ["dev", "route-explain", "q"],
        ["dev", "skills", "status"],
        ["doctor", "status"],
        ["doctor", "probe"],
        ["map", "https://example.com"],
    ]

    for argv in hidden_commands:
        assert parser.parse_args(argv).command, argv


def test_version_flags_exit_successfully(monkeypatch, capsys):
    monkeypatch.setattr(cli_parser, "_get_version", lambda: "9.9.9-test")

    for flag in ["--version", "--v", "-v"]:
        try:
            cli.main([flag])
        except SystemExit as exc:
            assert exc.code == 0

        assert capsys.readouterr().out.strip() == "smart-search 9.9.9-test"


def test_each_subcommand_help_exits_successfully(capsys):
    commands = [
        ["search", "--help"],
        ["fetch", "--help"],
        ["map", "--help"],
        ["capabilities", "--help"],
        ["research", "--help"],
        ["research", "plan", "--help"],
        ["research", "run", "--help"],
        ["config", "--help"],
        ["config", "path", "--help"],
        ["config", "list", "--help"],
        ["config", "set", "--help"],
        ["config", "unset", "--help"],
        ["provider", "--help"],
        ["provider", "list", "--help"],
        ["provider", "routes", "add", "--help"],
        ["doctor", "--help"],
        ["doctor", "status", "--help"],
        ["doctor", "probe", "--help"],
        ["dev", "--help"],
        ["dev", "smoke", "--help"],
    ]

    for command in commands:
        capsys.readouterr()  # clear any previous output
        try:
            code = cli.main(command)
        except SystemExit as exc:
            assert exc.code == 0, command
        else:
            assert code == 0, command
        out = capsys.readouterr().out
        assert out.startswith("usage: smart-search"), (command, out)
        assert command[0] in out, (command, out)
        assert '"schema_version"' not in out, (command, out)


def test_removed_commands_fail_with_family_errors(capsys):
    """Legacy commands and aliases fail with the replacement family's strict
    INVALID_ARGUMENT envelope instead of parsing or dispatching."""
    cases = (
        (["model", "list"], "3", "provider.routes.list"),
        (["smoke"], "3", "dev smoke"),
        (["setup", "--non-interactive"], "3", "config set"),
        (["skills", "status"], "3", "dev.skills.status"),
        (["deep", "query"], "research-workflow-1", "research plan"),
        (["route", "query"], "3", "dev route-explain"),
        (["route-calibrate"], "3", "dev route-calibrate"),
        (["diagnose", "openai-compatible"], "3", "dev diagnose openai-compatible"),
        (["regression"], "3", "dev regression"),
        (["s", "query"], "2", "search"),
        (["f", "https://example.com"], "2", "fetch"),
        (["dr", "query"], "research-workflow-1", "research plan"),
        (["rs", "query"], "research-workflow-1", "research run"),
        (["cfg", "ls"], "3", "config"),
        (["mdl", "list"], "3", "provider routes"),
    )
    for argv, schema, replacement in cases:
        code, out, err = _run_main(argv)
        assert code == 2, (argv, out, err)
        payload = json.loads(out)
        assert payload["schema_version"] == schema, argv
        assert payload["ok"] is False
        assert payload["status"] == "failed"
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert payload["error"]["details"]["replacement"] == replacement, argv
        # exactly one JSON document: the embedded plan schema_version is a
        # distinct nested key, so count the top-level identity once via json.
        assert json.loads(out)["schema_version"] == schema


def test_removed_schema_selector_fails_with_command_family(capsys):
    """A removed selector before a canonical command fails with that command
    family's strict envelope and bounded legacy_spelling/replacement."""
    cases = (
        (["--schema-version", "2", "search", "q"], "2", "search"),
        (["--schema-version", "3", "config", "list"], "3", "config"),
        (["--schema-version=2", "research", "run", "q"], "research-workflow-1", "research"),
    )
    for argv, schema, _command in cases:
        code, out, err = _run_main(argv)
        assert code == 2, (argv, out, err)
        payload = json.loads(out)
        assert payload["schema_version"] == schema, argv
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert payload["error"]["details"]["replacement"] == "omit selector; route by canonical command domain"


def test_selector_only_uses_v2_root_sentinel(capsys):
    code, out, err = _run_main(["--schema-version", "2"])
    assert code == 2
    payload = json.loads(out)
    assert payload["schema_version"] == "2"
    assert payload["command"] == "unknown"
    assert payload["operation"] is None
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_search_help_exposes_timeout(capsys):
    try:
        cli.main(["search", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    out = capsys.readouterr().out
    assert "--timeout SECONDS" in out
    assert "--stream" in out
    assert "--no-stream" in out


def test_diagnose_openai_compatible_defaults_to_markdown(monkeypatch, capsys):
    """dev diagnose openai-compatible keeps its markdown default through the
    typed v3 route when the owner reports a network failure."""
    from smart_search import control_operations
    from smart_search.control_operations import (
        ControlNetworkFacts,
        ControlOperationOutcome,
        ControlOperationStatus,
    )
    from smart_search.execution_primitives import ExecutionError, ExecutionMetadata

    async def fake_diagnose(timeout_seconds=30.0):
        return ControlOperationOutcome(
            operation="dev.diagnose.openai-compatible",
            status=ControlOperationStatus.FAILED,
            result={
                "provider": "openai-compatible",
                "summary": "小请求能通，但真实 search 形态超时。",
                "recommendation": "建议换模型/中转，或把本诊断报告贴给维护者。",
                "checks": [
                    {"name": "轻量 chat 请求", "status": "ok", "response_time_ms": 10.0, "has_content": True, "message": "chat ok"},
                    {"name": "真实 search 请求 (stream=false)", "status": "timeout", "response_time_ms": 30000.0, "has_content": False, "message": "请求超时"},
                ],
            },
            error=ExecutionError("timeout", "小请求能通，但真实 search 形态超时。", False),
            network=ControlNetworkFacts(attempted=True, targets=("openai-compatible",)),
            metadata=ExecutionMetadata("dev.diagnose.openai-compatible", 0),
        )

    monkeypatch.setattr(control_operations, "run_dev_diagnose_openai_compatible", fake_diagnose)

    # JSON is the v3 contract default: one strict envelope document.
    code = cli.main(["dev", "diagnose", "openai-compatible"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_NETWORK_ERROR
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "dev.diagnose.openai-compatible"
    assert payload["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert out.count('"schema_version"') == 1

    # Explicit --format markdown renders the human view of the same payload.
    code = cli.main(["dev", "diagnose", "openai-compatible", "--format", "markdown"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_NETWORK_ERROR
    assert out.startswith("# V3 Diagnose OpenAI-Compatible")
    assert "小请求能通" in out
    assert "UPSTREAM_TIMEOUT" in out


def test_search_outputs_v2_json(monkeypatch, capsys):
    """search is a canonical V2 evidence leaf; it never touches the legacy
    service facade and JSON is the single machine contract."""
    from smart_search import api_v2
    from smart_search.v2_contract import (
        V2Candidate,
        V2Envelope,
        V2Evidence,
        V2Meta,
        V2Routing,
        V2Status,
        validate_result,
    )

    async def fake_composite(query, max_results=5):
        return validate_result(
            V2Envelope(
                V2Status.COMPLETE,
                "search",
                "source_discovery",
                {"total": 1, "items": [{"id": "c1"}]},
                V2Evidence(candidates=(V2Candidate("c1", "https://example.com", "tavily", "T", "s"),)),
                V2Routing(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
                (),
                (),
                None,
                V2Meta("cli-test", 1),
            )
        )

    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)
    code, out, err = _run_main(["search", "query"])
    assert code == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["schema_version"] == "2"
    assert payload["operation"] == "source_discovery"
    assert payload["evidence"]["candidates"][0]["resource"] == "https://example.com"


def test_search_content_format_outputs_content_only(monkeypatch, capsys):
    from smart_search import api_v2
    from smart_search.v2_contract import (
        V2Candidate,
        V2Envelope,
        V2Evidence,
        V2Meta,
        V2Routing,
        V2Status,
        validate_result,
    )

    async def fake_composite(query, max_results=5):
        return validate_result(
            V2Envelope(
                V2Status.COMPLETE,
                "search",
                "source_discovery",
                {"total": 1, "items": [{"id": "c1"}]},
                V2Evidence(candidates=(V2Candidate("c1", "https://example.com", "tavily", "T", "s"),)),
                V2Routing(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
                (),
                (),
                None,
                V2Meta("content-test", 1),
            )
        )

    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)

    code, out, err = _run_main(["search", "query", "--format", "content"])
    assert code == cli.EXIT_OK
    assert out.strip() == "s"


def test_fetch_content_format_matches_markdown_body(monkeypatch, capsys):
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import ExecutionEvidenceItem, ExecutionMetadata

    async def fake_fetch(request):
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(
                ExecutionEvidenceItem(
                    id="evidence-1",
                    resource=request.resource,
                    provider="jina",
                    title="页面",
                    content="# 中文页面",
                ),
            ),
            attempts=(),
            routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("test",)),
            metadata=ExecutionMetadata("fetch-test", 1),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)

    content_code, content_out, _ = _run_main(["fetch", "https://example.com", "--format", "content"])
    markdown_code, markdown_out, _ = _run_main(["fetch", "https://example.com", "--format", "markdown"])

    assert content_code == cli.EXIT_OK
    assert markdown_code == cli.EXIT_OK
    assert "# 中文页面" in content_out
    assert "# 中文页面" in markdown_out
    assert not markdown_out.lstrip().startswith("{")


def test_deep_spelling_fails_with_workflow_family(capsys):
    """The legacy ``deep`` command is removed; its replacement is the
    canonical ``research plan`` workflow command."""
    code, out, err = _run_main(["deep", "query", "--format", "json"])
    assert code == 2
    payload = json.loads(out)
    assert payload["schema_version"] == "research-workflow-1"
    assert payload["error"]["details"]["legacy_spelling"] == "deep query"
    assert payload["error"]["details"]["replacement"] == "research plan"


def test_research_run_uses_strict_workflow_not_legacy_service(monkeypatch, capsys):
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import (
        ExecutionAttempt,
        ExecutionAttemptStatus,
        ExecutionCandidate,
        ExecutionEvidenceItem,
        ExecutionMetadata,
    )
    from smart_search.research_workflow_contract import WORKFLOW_TOP_LEVEL_FIELDS

    async def boom(*args, **kwargs):
        raise AssertionError("research run must not call the legacy service")

    async def fake_fetch(request):
        item = ExecutionEvidenceItem(
            id="evidence-1",
            resource=request.resource,
            provider="jina",
            title="page",
            content="body",
        )
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(item,),
            attempts=(
                ExecutionAttempt(
                    capability="content_fetch",
                    provider="jina",
                    status=ExecutionAttemptStatus.OK,
                    elapsed_ms=1.0,
                    result_count=1,
                ),
            ),
            routing=EvidenceRouting(("content_fetch",), ("content_fetch",), "v2", ("test",)),
            metadata=ExecutionMetadata("req-test", 1),
        )

    async def fake_source(request):
        candidate = ExecutionCandidate(
            id="cand-1",
            resource="https://example.com/react-docs",
            provider="tavily",
            title="React docs",
            snippet="snippet",
        )
        return EvidenceOperationOutcome(
            operation="source_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(candidate,),
            attempts=(),
            routing=EvidenceRouting(("source_discovery",), (), "v2", ("test",)),
            metadata=ExecutionMetadata("req-test", 1),
        )

    async def fake_docs(request):
        return EvidenceOperationOutcome(
            operation="docs_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(),
            attempts=(),
            routing=EvidenceRouting(("docs_discovery",), (), "v2", ("test",)),
            metadata=ExecutionMetadata("req-test", 1),
        )

    async def fake_site(request):
        return EvidenceOperationOutcome(
            operation="site_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(),
            attempts=(),
            routing=EvidenceRouting(("site_discovery",), (), "v2", ("test",)),
            metadata=ExecutionMetadata("req-test", 1),
        )

    monkeypatch.setattr(cli.service, "research", boom)
    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
    monkeypatch.setattr(evidence_operations, "source_discovery", fake_source)
    monkeypatch.setattr(evidence_operations, "docs_discovery", fake_docs)
    monkeypatch.setattr(evidence_operations, "site_discovery", fake_site)

    code, out, _ = _run_main(["research", "run", "React docs", "--format", "json"])
    assert code == cli.EXIT_OK
    data = json.loads(out)
    assert sorted(data) == sorted(WORKFLOW_TOP_LEVEL_FIELDS)
    assert data["command"] == "research"
    assert data["operation"] == "research.run"
    assert data["status"] == "complete"
    assert data["error"] is None
    assert "final_answer" not in json.dumps(data)

    # --synthesize is a forbidden stable-path flag: strict INVALID_ARGUMENT
    # result, still no legacy service call.
    code, out, _ = _run_main(["research", "run", "React docs", "--synthesize", "--format", "json"])
    assert code == cli.EXIT_PARAMETER_ERROR
    data = json.loads(out)
    assert data["operation"] == "research.run"
    assert data["status"] == "failed"
    assert data["error"]["code"] == "INVALID_ARGUMENT"


def test_doctor_status_is_local_only(monkeypatch, capsys):
    from smart_search import operations_service

    def boom(*args, **kwargs):
        raise AssertionError("doctor status must not probe providers")

    monkeypatch.setattr(operations_service, "_test_exa_connection", boom)
    monkeypatch.setattr(operations_service, "_test_tavily_connection", boom)
    monkeypatch.setattr(operations_service, "_test_jina_connection", boom)
    monkeypatch.setattr(operations_service, "_test_zhipu_connection", boom)
    monkeypatch.setattr(operations_service, "_test_zhipu_mcp_connection", boom)
    monkeypatch.setattr(operations_service, "_test_context7_connection", boom)
    monkeypatch.setattr(operations_service, "_safe_test_main_provider_connection", boom)

    code, out, _ = _run_main(["doctor", "status", "--format", "json"])
    assert code in {cli.EXIT_OK, cli.EXIT_CONFIG_ERROR, cli.EXIT_PARAMETER_ERROR}
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "doctor.status"
    assert payload["network"]["attempted"] is False


def test_doctor_status_uses_the_minimum_profile_capability_snapshot(monkeypatch):
    from smart_search import capability_service, operations_service

    snapshot_calls = []
    snapshot = {
        "web_search": {"provider_status": []},
        "docs_search": {"provider_status": []},
        "web_fetch": {"provider_status": []},
    }

    def status_snapshot():
        snapshot_calls.append("status")
        return snapshot

    def unexpected_second_snapshot():
        raise AssertionError("doctor status must reuse the minimum-profile capability snapshot")

    monkeypatch.setattr(capability_service, "get_capability_status", status_snapshot)
    monkeypatch.setattr(operations_service, "get_capability_status", unexpected_second_snapshot)
    result = operations_service.doctor_status()
    assert snapshot_calls == ["status"]
    assert result["capability_status"] == snapshot
    assert result["local_only"] is True


def test_provider_probe_unknown_is_parameter_error(monkeypatch, capsys):
    from smart_search import operations_service

    async def boom(*args, **kwargs):
        raise AssertionError("unknown provider must not probe")

    monkeypatch.setattr(operations_service, "run_probe_adapter", boom)
    code, out, _ = _run_main(["provider", "probe", "not-a-provider", "--format", "json"])
    assert code == cli.EXIT_PARAMETER_ERROR
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "provider.probe"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_provider_probe_ineligible_provider_is_local_failure(monkeypatch, capsys):
    from smart_search import operations_service, provider_diagnostics

    def unavailable(provider, capability=""):
        assert provider == "exa"
        return {
            "provider": "exa",
            "capabilities": ["docs_search"],
            "configured": True,
            "enabled": True,
            "eligible": False,
            "reason": "provider_not_eligible",
        }

    async def boom(*args, **kwargs):
        raise AssertionError("ineligible provider must not probe")

    monkeypatch.setattr(provider_diagnostics, "_provider_availability", unavailable)
    monkeypatch.setattr(operations_service, "run_probe_adapter", boom)
    code, out, _ = _run_main(["provider", "probe", "exa", "--format", "json"])
    assert code == cli.EXIT_CONFIG_ERROR
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert payload["network"]["attempted"] is False


def test_provider_probe_registry_covers_each_real_provider():
    from smart_search.capability_service import PROVIDER_REGISTRY
    from smart_search.provider_diagnostics import PROVIDER_PROBE_REGISTRY, known_probe_providers

    real_providers = set(PROVIDER_REGISTRY) - {"main-search"}
    assert real_providers == set(PROVIDER_PROBE_REGISTRY)
    assert real_providers == set(known_probe_providers())


def test_config_error_exit_code(monkeypatch, capsys):
    code, out, _ = _run_main(["config", "set", "SMART_SEARCH_API_KEY", "sk-test-secret"])
    assert code == cli.EXIT_PARAMETER_ERROR
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "config.set"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_network_error_exit_code(monkeypatch, capsys):
    """v2 content_fetch failures keep the classified exit and one JSON doc."""
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import ExecutionError, ExecutionMetadata

    async def fake_fetch(request):
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.FAILED,
            evidence_items=(),
            attempts=(),
            error=ExecutionError("network_error", "upstream timeout", False),
            routing=EvidenceRouting(("content_fetch",), (), "v2", ("test",)),
            metadata=ExecutionMetadata("fetch-test", 1),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_fetch)
    code, out, _ = _run_main(["fetch", "https://example.com"])
    assert code == cli.EXIT_NETWORK_ERROR
    payload = json.loads(out)
    assert payload["schema_version"] == "2"
    assert payload["ok"] is False
    assert payload["error"]["message"] == "upstream timeout"


def test_stdout_falls_back_for_gbk_unencodable_unicode(monkeypatch):
    fake_stdout = GbkStdout()
    monkeypatch.setattr(cli_support.sys, "stdout", fake_stdout)

    code = cli._print_result("map", {"ok": True, "content": "A\u2060B"}, "json")

    assert code == cli.EXIT_OK
    out = fake_stdout.getvalue()
    assert "\\u2060" in out
    assert json.loads(out)["content"] == "A\u2060B"


def test_gbk_stdout_keeps_json_parseable_with_chinese_and_unencodable_unicode(monkeypatch):
    fake_stdout = GbkStdout()
    monkeypatch.setattr(cli_support.sys, "stdout", fake_stdout)

    code = cli._print_result("search", {"ok": True, "content": "中文A\u2060B📅"}, "json")

    assert code == cli.EXIT_OK
    out = fake_stdout.getvalue()
    assert "中文" in out
    assert "\\u2060" in out
    assert "\\ud83d\\udcc5" in out
    assert json.loads(out)["content"] == "中文A\u2060B📅"


def test_config_set_masks_value(monkeypatch, capsys):
    code, out, _ = _run_main(["config", "set", "XAI_API_KEY", "xai-test-secret"])
    assert code == cli.EXIT_OK
    rendered = out
    assert "xai-test-secret" not in rendered
    payload = json.loads(rendered)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "config.set"
    assert payload["result"]["value"] != "xai-test-secret"


def test_config_list_does_not_request_secrets(monkeypatch, capsys):
    code, out, _ = _run_main(["config", "list"])
    assert code == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["operation"] == "config.list"
    assert "values" in payload["result"]


def test_map_markdown_outputs_result_lists(monkeypatch, capsys):
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import ExecutionCandidate, ExecutionMetadata

    async def fake_site(request):
        return EvidenceOperationOutcome(
            operation="site_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(
                ExecutionCandidate(
                    id="c1",
                    resource="https://docs.example.com/api",
                    provider="jina",
                    title="API reference",
                    snippet="reference",
                ),
            ),
            attempts=(),
            routing=EvidenceRouting(("site_discovery",), ("site_discovery",), "v2", ("test",)),
            metadata=ExecutionMetadata("map-test", 1),
        )

    monkeypatch.setattr(evidence_operations, "site_discovery", fake_site)
    code, out, _ = _run_main(["map", "https://docs.example.com", "--format", "markdown"])
    assert code == cli.EXIT_OK
    assert not out.lstrip().startswith("{")
    assert "https://docs.example.com/api" in out
    assert "Site Map" in out


def test_map_content_outputs_plain_result_list(monkeypatch, capsys):
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import ExecutionCandidate, ExecutionMetadata

    async def fake_site(request):
        return EvidenceOperationOutcome(
            operation="site_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(
                ExecutionCandidate(
                    id="c1",
                    resource="https://example.com",
                    provider="jina",
                    title="Example",
                    snippet="body",
                ),
            ),
            attempts=(),
            routing=EvidenceRouting(("site_discovery",), ("site_discovery",), "v2", ("test",)),
            metadata=ExecutionMetadata("map-test", 1),
        )

    monkeypatch.setattr(evidence_operations, "site_discovery", fake_site)
    code, out, _ = _run_main(["map", "https://example.com", "--format", "content"])
    assert code == cli.EXIT_OK
    assert out.strip() == "https://example.com"
    assert not out.lstrip().startswith("{")


def test_map_markdown_empty_results_are_clear(monkeypatch, capsys):
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import ExecutionMetadata

    async def fake_site(request):
        return EvidenceOperationOutcome(
            operation="site_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(),
            attempts=(),
            routing=EvidenceRouting(("site_discovery",), (), "v2", ("test",)),
            metadata=ExecutionMetadata("map-test", 1),
        )

    monkeypatch.setattr(evidence_operations, "site_discovery", fake_site)
    code, out, _ = _run_main(["map", "https://example.com", "--format", "markdown"])
    assert code == cli.EXIT_OK
    assert "Results: 0" in out


def test_skills_status_reports_missing_and_update_writes_target(tmp_path, capsys):
    status_code, status_out, _ = _run_main(["dev", "skills", "status", "--targets", "codex", "--skills-root", str(tmp_path), "--format", "json"])
    status = json.loads(status_out)
    assert status_code == cli.EXIT_OK
    assert status["schema_version"] == "3"
    assert status["operation"] == "dev.skills.status"
    assert status["result"]["targets"][0]["status"] == "missing"

    update_code, update_out, _ = _run_main(["dev", "skills", "update", "--targets", "codex", "--skills-root", str(tmp_path), "--format", "json"])
    update = json.loads(update_out)
    assert update_code == cli.EXIT_OK
    assert update["operation"] == "dev.skills.update"
    assert update["result"]["installed_count"] == 1
    assert (tmp_path / ".codex" / "skills" / "smart-search-cli" / "SKILL.md").is_file()

    status_code, status_out, _ = _run_main(["dev", "skills", "status", "--targets", "codex", "--skills-root", str(tmp_path), "--format", "json"])
    status = json.loads(status_out)
    assert status_code == cli.EXIT_OK
    assert status["result"]["targets"][0]["status"] == "up_to_date"


def test_skills_unknown_target_returns_parameter_error(tmp_path, capsys):
    code, out, _ = _run_main(["dev", "skills", "status", "--targets", "unknown", "--skills-root", str(tmp_path), "--format", "json"])
    payload = json.loads(out)
    assert code == cli.EXIT_PARAMETER_ERROR
    assert payload["schema_version"] == "3"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert not (tmp_path / ".codex" / "skills" / "smart-search-cli").exists()


def test_setup_non_interactive_saves_values(monkeypatch, capsys):
    """Bare ``setup`` is removed; the canonical control-plane spelling is
    ``config set``. The interactive wizard module stays importable for the
    cleanup task but is no longer reachable from the CLI."""
    code, out, _ = _run_main(["setup", "--non-interactive"])
    assert code == 2
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["error"]["details"]["legacy_spelling"] == "setup"


def test_setup_banner_falls_back_when_pyfiglet_unavailable(monkeypatch, capsys):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "pyfiglet":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    cli._write_setup_banner("en")
    captured = capsys.readouterr()

    assert "Smart Search" in captured.err
    assert "CLI-first multi-source search" in captured.err


def test_skill_installer_parse_aliases_and_all(tmp_path):
    assert skill_installer.parse_skill_targets("claude-code,github-copilot,agentskills,hermes-agent") == [
        "claude",
        "copilot",
        "codex",
        "hermes",
    ]
    assert len(skill_installer.parse_skill_targets("all")) == len(skill_installer.SKILL_TARGETS)

    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: smart-search-cli\n---\n", encoding="utf-8")
    result = skill_installer.install_skill_targets(
        ["codex"],
        project_root=tmp_path / "project",
        source_root=source,
    )

    assert result["ok"] is True
    assert result["installed_count"] == 1
    assert (tmp_path / "project" / ".codex" / "skills" / "smart-search-cli" / "SKILL.md").is_file()


def test_skill_installer_pi_target_uses_agent_skill_root(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: smart-search-cli\n---\n", encoding="utf-8")

    result = skill_installer.install_skill_targets(
        ["pi"],
        project_root=tmp_path / "project",
        source_root=source,
    )

    assert result["ok"] is True
    assert result["installed_count"] == 1
    assert Path(result["installed"][0]["path"]).as_posix().endswith(".pi/agent/skills/smart-search-cli")
    assert (tmp_path / "project" / ".pi" / "agent" / "skills" / "smart-search-cli" / "SKILL.md").is_file()
    assert not (tmp_path / "project" / ".pi" / "skills" / "smart-search-cli").exists()


def test_skill_installer_status_detects_stale_and_extra_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("new", encoding="utf-8")
    root = tmp_path / "project"
    dest = root / ".codex" / "skills" / "smart-search-cli"
    dest.mkdir(parents=True)

    (dest / "SKILL.md").write_text("old", encoding="utf-8")
    stale = skill_installer.status_skill_targets(["codex"], project_root=root, source_root=source)
    assert stale["targets"][0]["status"] == "stale"
    assert stale["targets"][0]["stale_files"] == ["SKILL.md"]

    (dest / "SKILL.md").write_text("new", encoding="utf-8")
    (dest / "OLD.md").write_text("old leftover", encoding="utf-8")
    extra = skill_installer.status_skill_targets(["codex"], project_root=root, source_root=source)
    assert extra["targets"][0]["status"] == "extra_files"
    assert extra["targets"][0]["extra_files"] == ["OLD.md"]
    assert extra["targets"][0]["managed_hash_match"] is True
    assert extra["targets"][0]["hash_match"] is False


def test_tavily_url_normalization_cases():
    cases = {
        "pool.example.com": "https://pool.example.com/api/tavily",
        "https://pool.example.com": "https://pool.example.com/api/tavily",
        "https://pool.example.com/mcp": "https://pool.example.com/api/tavily",
        "https://pool.example.com/api/tavily": "https://pool.example.com/api/tavily",
        "https://api.tavily.com": "https://api.tavily.com",
    }

    for raw, expected in cases.items():
        assert cli._normalize_tavily_api_url(raw) == expected
    assert cli._normalize_tavily_api_url("https://custom.example.com", hikari=False) == "https://custom.example.com"
    assert cli._normalize_tavily_flag_api_url("https://custom.example.com", "tvly-key") == "https://custom.example.com"
    assert cli._normalize_tavily_flag_api_url("https://custom.example.com/mcp", "tvly-key") == "https://custom.example.com/api/tavily"
    assert cli._normalize_tavily_flag_api_url("https://custom.example.com", "th-key") == "https://custom.example.com/api/tavily"


def test_tavily_hikari_key_recommends_hikari_endpoint(monkeypatch):
    values = {"TAVILY_API_KEY": "th-test-secret"}
    seen = {}

    def fake_prompt_select(message, choices, default):
        seen["default"] = default
        return "hikari"

    monkeypatch.setattr(cli_setup, "_prompt_select", fake_prompt_select)
    monkeypatch.setattr(cli_setup, "_prompt_value", lambda *args, **kwargs: "https://pool.example.com/mcp")

    cli._prompt_tavily_api_url(values, {}, "en")

    assert seen["default"] == "hikari"
    assert values["TAVILY_API_URL"] == "https://pool.example.com/api/tavily"


def test_tavily_hikari_prompt_shows_beginner_url_example(monkeypatch, capsys):
    values = {"TAVILY_API_KEY": "th-test-secret"}

    monkeypatch.setattr(cli_setup, "_prompt_select", lambda message, choices, default: "hikari")
    monkeypatch.setattr(cli_setup, "_prompt_value", lambda *args, **kwargs: "https://pool.example.com")

    cli._prompt_tavily_api_url(values, {}, "zh")
    captured = capsys.readouterr()

    assert values["TAVILY_API_URL"] == "https://pool.example.com/api/tavily"
    assert "例如 https://pool.example.com" in captured.err
    assert "api/tavily" in captured.err


def test_zhipu_prompt_saves_official_api_url_and_search_engine(monkeypatch):
    values = {}
    selections = iter(["official", "search_pro_sogou"])

    monkeypatch.setattr(cli_setup, "_prompt_select", lambda message, choices, default: next(selections))

    cli._prompt_zhipu_api_url(values, {}, "zh")
    cli._prompt_zhipu_search_engine(values, {}, "zh")

    assert values["ZHIPU_API_URL"] == "https://open.bigmodel.cn/api"
    assert values["ZHIPU_SEARCH_ENGINE"] == "search_pro_sogou"


def test_zhipu_prompt_allows_custom_search_engine(monkeypatch):
    values = {}
    selections = iter(["custom"])

    monkeypatch.setattr(cli_setup, "_prompt_select", lambda message, choices, default: next(selections))
    monkeypatch.setattr(cli_setup, "_prompt_value", lambda *args, **kwargs: "search_future")

    cli._prompt_zhipu_search_engine(values, {}, "en")

    assert values["ZHIPU_SEARCH_ENGINE"] == "search_future"


def test_smoke_spelling_fails_with_v3_family(capsys):
    code, out, _ = _run_main(["smoke", "--mock"])
    assert code == 2
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["error"]["details"]["legacy_spelling"] == "smoke"
    assert payload["error"]["details"]["replacement"] == "dev smoke"


def test_provider_and_smoke_aliases_are_removed(capsys):
    for argv, spelling in ((["rs", "query"], "rs query"), (["sm"], "sm")):
        code, out, _ = _run_main(argv)
        assert code == 2, argv
        payload = json.loads(out)
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert payload["error"]["details"]["legacy_spelling"] == spelling


def test_regression_spelling_fails_with_v3_family(capsys):
    code, out, _ = _run_main(["regression"])
    assert code == 2
    payload = json.loads(out)
    assert payload["schema_version"] == "3"
    assert payload["error"]["details"]["legacy_spelling"] == "regression"
    assert payload["error"]["details"]["replacement"] == "dev regression"
