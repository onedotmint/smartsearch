"""Canonical namespace routing coverage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_search import cli
from smart_search.cli_constants import CLIParseError, NAMESPACE_COMMANDS
from smart_search.cli_parser import build_parser

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("argv", "command", "operation"),
    [
        (["research", "plan", "topic"], "research", "research-plan"),
        (["research", "run", "topic"], "research", "research-run"),
        (["doctor", "probe"], "doctor", "doctor-probe"),
        (["doctor", "status"], "doctor", "doctor-status"),
        (["provider", "list"], "provider-list", "provider-list"),
        (["provider", "status"], "provider-status", "provider-status"),
        (["provider", "probe", "exa"], "provider-probe", "provider-probe"),
        (["provider", "routes", "current"], "provider", "provider-routes-current"),
        (["dev", "route-explain", "topic"], "dev", "dev-route-explain"),
        (["dev", "skills", "status"], "dev", "dev-skills-status"),
    ],
)
def test_namespace_paths_normalize_to_canonical_command(argv, command, operation):
    args = build_parser().parse_args(argv)
    assert args.command == command
    assert getattr(args, "namespace_operation", None) == operation


def test_removed_provider_and_experimental_namespace_paths_are_rejected():
    parser = build_parser(raise_on_error=True)
    for argv in (
        ["provider", "exa", "search", "topic"],
        ["provider", "context7", "docs", "/react", "hooks"],
        ["provider", "zhipu", "search", "topic"],
        ["provider", "zhipu-mcp", "reader", "https://example.com"],
        ["experimental", "anysearch", "search", "topic"],
        ["experimental", "zread", "search-doc", "owner/repo", "topic"],
    ):
        with pytest.raises(CLIParseError):
            parser.parse_args(argv)


def test_collision_and_deferred_paths_remain_honest():
    parser = build_parser()
    assert parser.parse_args(["research", "plan"]).command == "research"
    assert parser.parse_args(["research", "run"]).command == "research"
    assert parser.parse_args(["research", "--budget", "quick", "plan", "topic"]).namespace_operation == "research-plan"
    assert parser.parse_args(["research", "plan", "--budget", "quick", "topic"]).namespace_operation == "research-plan"
    assert parser.parse_args(["research", "plan", "--", "-topic"]).namespace_operation == "research-plan"
    assert parser.parse_args(["doctor", "--format", "json", "probe"]).namespace_operation == "doctor-probe"
    assert parser.parse_args(["doctor", "--format", "json", "status"]).namespace_operation == "doctor-status"
    run_args = parser.parse_args(["research", "run", "topic"])
    assert (run_args.command, run_args.namespace_operation, run_args.query) == ("research", "research-run", "topic")
    assert getattr(run_args, "synthesize", False) is False
    synth_args = parser.parse_args(["research", "run", "topic", "--synthesize"])
    assert synth_args.namespace_operation == "research-run"
    assert synth_args.synthesize is True
    with pytest.raises(SystemExit):
        parser.parse_args(["research", "topic", "--synthesize"])
    for argv in (
        ["research-plan", "topic"],
        ["doctor-probe"],
        ["doctor-status"],
        ["research-run", "topic"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_parse_args_none_classifies_collision_paths(monkeypatch):
    operations = [item["operation"] for item in NAMESPACE_COMMANDS]
    assert len(operations) == len(set(operations))

    monkeypatch.setattr(sys, "argv", ["smart-search", "research", "plan", "topic"])
    research = build_parser().parse_args()
    assert (research.command, research.namespace_operation, research.query) == ("research", "research-plan", "topic")

    monkeypatch.setattr(sys, "argv", ["smart-search", "doctor", "probe"])
    doctor = build_parser().parse_args()
    assert (doctor.command, doctor.namespace_operation) == ("doctor", "doctor-probe")


def test_root_help_and_help_all_are_deterministic_and_local(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    root_help = capsys.readouterr().out
    for name in ("search", "fetch", "capabilities"):
        assert f"    {name}" in root_help
    for name in ("doctor", "provider", "dev", "map", "setup", "model", "smoke"):
        assert f"    {name}" not in root_help

    assert cli.main(["--help-all"]) == 0
    full = capsys.readouterr().out
    for item in NAMESPACE_COMMANDS:
        assert item["path"] in full
    assert "Evidence Core (V2):" in full
    assert "Control plane (V3):" in full
    assert "Research workflow:" in full
    # removed spellings are no longer advertised
    for removed in ("model add", "skills status (st)", "Legacy commands and aliases:"):
        assert removed not in full


def test_help_all_does_not_import_runtime_modules():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    script = """
import sys
from smart_search.cli import main
assert main(['--help-all']) == 0
for name in ('smart_search.service', 'smart_search.config', 'smart_search.providers', 'smart_search.skill_installer', 'httpx'):
    assert name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_research_plan_and_run_route_to_workflow(monkeypatch, capsys):
    """Canonical research plan/run both enter the workflow family."""
    from smart_search import research_service
    from smart_search.research_plan import ResearchPlanOperation, build_research_plan

    calls: list[str] = []

    def fake_plan(query, budget="deep", evidence_dir=""):
        calls.append(query)
        return build_research_plan(
            [
                ResearchPlanOperation(
                    id="fetch-1", operation="content_fetch",
                    input={"resource": "https://example.com/page"},
                    constraints={}, depends_on=(),
                )
            ]
        )

    monkeypatch.setattr(research_service, "build_research_workflow_plan", fake_plan)
    assert cli.main(["research", "plan", "topic", "--budget", "quick"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "research.run"
    assert payload["stages"] == []
    assert calls == ["topic"]


def test_provider_catalog_is_one_snapshot_and_never_probes(monkeypatch, capsys):
    from smart_search import provider_catalog

    calls = []
    secret = "namespace-secret"

    def fake_status():
        calls.append("status")
        return {
            "docs_search": {
                "provider_status": [{"provider": "exa", "configured": True, "enabled": True, "eligible": True, "reason": "ready"}]
            }
        }

    monkeypatch.setattr(provider_catalog, "get_capability_status", fake_status)
    monkeypatch.setattr(provider_catalog, "provider_profiles", lambda: {"exa": {"capabilities": ["docs_search"]}})
    monkeypatch.setattr(provider_catalog, "list_provider_qualifications", lambda **kwargs: [])
    assert cli.main(["provider", "status", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == ["status"]
    assert payload["command"] == "provider"
    assert payload["operation"] == "provider.catalog.status"
    assert payload["result"]["providers"][0]["v2_capabilities"] == ["docs_discovery"]
    assert payload["result"]["providers"][0]["status"][0]["eligible"] is True
    assert secret not in json.dumps(payload)


def test_provider_catalog_maps_qualifications_and_excludes_synthetic_profile(monkeypatch):
    from smart_search import provider_catalog

    monkeypatch.setattr(
        provider_catalog,
        "get_capability_status",
        lambda: {
            "docs_search": {"provider_status": [{"provider": "exa", "configured": True, "enabled": True, "eligible": True, "reason": "ready"}]},
            "synthesis": {"provider_status": []},
        },
    )
    monkeypatch.setattr(
        provider_catalog,
        "provider_profiles",
        lambda: {
            "exa": {"capability": "docs_search"},
            "main-search": {"capability": "synthesis"},
        },
    )
    monkeypatch.setattr(
        provider_catalog,
        "list_provider_qualifications",
        lambda **kwargs: [{"provider": "exa", "capability": "docs_discovery", "tier": "core", "stability": "stable"}],
    )

    entry = provider_catalog.provider_catalog(include_status=False)["providers"]
    assert entry == [
        {
            "provider": "exa",
            "capabilities": ["docs_search"],
            "v2_capabilities": ["docs_discovery"],
            "tier": "core",
            "stability": "stable",
            "replacement": "search (V2 source/docs discovery)",
            "network_behavior": "network_on_explicit_command",
            "legacy_commands": [],
            "legacy_aliases": [],
            "qualifications": [{"provider": "exa", "capability": "docs_discovery", "tier": "core", "stability": "stable"}],
        }
    ]


def test_dev_smoke_keeps_mock_default():
    assert build_parser().parse_args(["dev", "smoke"]).mode == "mock"
