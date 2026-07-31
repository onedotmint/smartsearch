"""Phase 4 namespace compatibility coverage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_search import cli
from smart_search.cli_constants import NAMESPACE_COMMANDS
from smart_search.cli_parser import build_parser

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("argv", "command", "operation"),
    [
        (["research", "plan", "topic"], "deep", "research-plan"),
        (["doctor", "probe"], "doctor", "doctor-probe"),
        (["provider", "list"], "provider-list", "provider-list"),
        (["provider", "routes", "current"], "model", "provider-routes-current"),
        (["provider", "exa", "search", "topic"], "exa-search", "provider-exa-search"),
        (["provider", "context7", "docs", "/react", "hooks"], "context7-docs", "provider-context7-docs"),
        (["provider", "zhipu", "search", "topic"], "zhipu-search", "provider-zhipu-search"),
        (["provider", "zhipu-mcp", "reader", "https://example.com"], "zhipu-mcp-reader", "provider-zhipu-mcp-reader"),
        (["dev", "route-explain", "topic"], "route", "dev-route-explain"),
        (["dev", "skills", "status"], "skills", "dev-skills-status"),
        (["experimental", "anysearch", "search", "topic"], "anysearch-search", "experimental-anysearch-search"),
        (["experimental", "zread", "search-doc", "owner/repo", "topic"], "zhipu-mcp-search-doc", "experimental-zread-search-doc"),
    ],
)
def test_namespace_paths_normalize_to_v1_command(argv, command, operation):
    args = build_parser().parse_args(argv)
    assert args.command == command
    assert getattr(args, "namespace_operation", None) == operation


def test_collision_and_deferred_paths_remain_honest():
    parser = build_parser()
    assert parser.parse_args(["research", "plan"]).command == "research"
    assert parser.parse_args(["rs", "plan"]).command == "research"
    assert parser.parse_args(["research", "--budget", "quick", "plan", "topic"]).namespace_operation == "research-plan"
    assert parser.parse_args(["research", "plan", "--budget", "quick", "topic"]).namespace_operation == "research-plan"
    assert parser.parse_args(["research", "plan", "--", "-topic"]).namespace_operation == "research-plan"
    assert parser.parse_args(["doctor", "--format", "json", "probe"]).namespace_operation == "doctor-probe"
    for argv in (
        ["research", "run", "topic"],
        ["doctor", "status"],
        ["provider", "probe", "exa"],
        ["research-plan", "topic"],
        ["doctor-probe"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_parse_args_none_classifies_collision_paths(monkeypatch):
    operations = [item["operation"] for item in NAMESPACE_COMMANDS]
    assert len(operations) == len(set(operations))

    monkeypatch.setattr(sys, "argv", ["smart-search", "research", "plan", "topic"])
    research = build_parser().parse_args()
    assert (research.command, research.namespace_operation, research.query) == ("deep", "research-plan", "topic")

    monkeypatch.setattr(sys, "argv", ["smart-search", "doctor", "probe"])
    doctor = build_parser().parse_args()
    assert (doctor.command, doctor.namespace_operation) == ("doctor", "doctor-probe")


def test_v2_namespace_paths_are_not_normalized_before_parser_errors(capsys):
    assert cli.main(["--schema-version", "2", "research", "plan", "topic"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "research"
    assert payload["operation"] is None


def test_root_help_and_help_all_are_deterministic_and_local(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    root_help = capsys.readouterr().out
    for name in ("search", "fetch", "capabilities", "setup"):
        assert f"    {name}" in root_help
    for name in ("doctor", "provider", "dev", "experimental", "map"):
        assert f"    {name}" not in root_help

    assert cli.main(["--help-all"]) == 0
    full = capsys.readouterr().out
    for item in NAMESPACE_COMMANDS:
        assert item["path"] in full
    assert "Legacy commands and aliases:" in full
    for legacy_path in ("config path (p)", "model current (cur, c)", "skills status (st)"):
        assert legacy_path in full


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


def test_research_plan_and_deep_share_one_handler(monkeypatch, capsys):
    calls = []

    def fake_plan(query, *, budget, evidence_dir):
        calls.append((query, budget, evidence_dir))
        return {"ok": True, "query": query, "steps": [{"command": "smart-search fetch x", "output_path": "x.json"}]}

    monkeypatch.setattr(cli.service, "build_deep_research_plan", fake_plan)
    assert cli.main(["deep", "topic", "--budget", "quick"]) == 0
    deep = json.loads(capsys.readouterr().out)
    assert cli.main(["research", "plan", "topic", "--budget", "quick"]) == 0
    namespaced = json.loads(capsys.readouterr().out)
    assert calls == [("topic", "quick", ""), ("topic", "quick", "")]
    assert deep["command"] == namespaced["command"] == "deep"
    assert deep["steps"] == namespaced["steps"]


def test_direct_provider_namespace_uses_same_exact_handler(monkeypatch, capsys):
    calls = []

    async def fake_exa(query, **kwargs):
        calls.append((query, kwargs))
        return {"ok": True, "provider": "exa", "query": query, "results": []}

    monkeypatch.setattr(cli.service, "exa_search", fake_exa)
    assert cli.main(["exa-search", "topic", "--num-results", "2"]) == 0
    capsys.readouterr()
    assert cli.main(["provider", "exa", "search", "topic", "--num-results", "2"]) == 0
    capsys.readouterr()
    assert len(calls) == 2
    assert calls[0] == calls[1]


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
    assert payload["command"] == "provider-status"
    assert payload["data"]["local_only"] is True
    entry = payload["data"]["providers"][0]
    assert entry["status"][0]["eligible"] is True
    assert entry["v2_capabilities"] == ["docs_discovery"]
    assert entry["qualifications"] == []
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
            "replacement": "provider exa search|similar",
            "network_behavior": "network_on_explicit_command",
            "legacy_commands": ["exa-search", "exa-similar"],
            "legacy_aliases": ["exa", "x", "xs"],
            "qualifications": [{"provider": "exa", "capability": "docs_discovery", "tier": "core", "stability": "stable"}],
        }
    ]


def test_dev_smoke_keeps_legacy_mock_default():
    assert build_parser().parse_args(["dev", "smoke"]).mode == "mock"
