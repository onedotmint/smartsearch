"""Deterministic tests for the repository-only retrieval replay harness."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from benchmarks import retrieval_replay

FIXTURE = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "retrieval_replay" / "brave-exa-shared-url.json"


def test_replay_uses_raw_fixture_and_reports_production_metrics():
    fixture = retrieval_replay.load_fixture(FIXTURE)
    report = retrieval_replay.replay_fixture(fixture, top_k=2)

    assert report["provider_order"] == ["brave", "exa"]
    assert report["totals"] == {"raw": 5, "normalized": 4, "fused": 3, "duplicates": 1, "invalid": 1}
    assert report["deduplication_rate"] == 0.25
    assert report["provider_contribution_counts"] == {"brave": 2, "exa": 2}
    assert [item["canonical_url"] for item in report["rrf_results"]] == [
        "https://example.test/shared",
        "https://example.test/alpha",
        "https://example.test/beta",
    ]
    assert len(report["top_k_results"]) == 2
    assert report["rrf_results"][0]["provider_ranks"] == {"brave": 0, "exa": 0}
    assert report["rrf_results"][0]["display_url"].endswith("utm_source=brave")


def test_replay_is_repeatable_in_json_and_markdown():
    fixtures = retrieval_replay.load_fixtures(FIXTURE.parent)
    first = retrieval_replay.replay_fixtures(fixtures, top_k=3)
    second = retrieval_replay.replay_fixtures(fixtures, top_k=3)

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert retrieval_replay._markdown(first) == retrieval_replay._markdown(second)


def test_replay_rejects_derived_state_and_malformed_provider_entries():
    fixture = retrieval_replay.load_fixture(FIXTURE)
    derived = copy.deepcopy(fixture)
    derived["providers"][0]["results"][0]["canonical_url"] = "https://example.test/shared"
    with pytest.raises(retrieval_replay.FixtureError, match="derived-state"):
        retrieval_replay.validate_fixture(derived)

    malformed = copy.deepcopy(fixture)
    malformed["providers"][0]["results"] = {"not": "a list"}
    with pytest.raises(retrieval_replay.FixtureError, match="results must be an array"):
        retrieval_replay.validate_fixture(malformed)


def test_replay_rejects_secret_variants_and_derived_provenance_fields():
    fixture = retrieval_replay.load_fixture(FIXTURE)
    cases = (
        ({"client_secret": "not-committable"}, "secret field"),
        ({"url": "https://example.test/page#access_token=not-committable"}, "fragment"),
        ({"url": "https://example.test/page#token=not-committable"}, "fragment"),
        ({"providers": ["brave"]}, "derived-state"),
        ({"provenance": {"providers": ["brave"]}}, "derived-state"),
    )
    for extra, message in cases:
        candidate = copy.deepcopy(fixture)
        candidate["providers"][0]["results"][0].update(extra)
        with pytest.raises(retrieval_replay.FixtureError, match=message):
            retrieval_replay.validate_fixture(candidate)


def test_replay_rejects_secret_bearing_fixture_data():
    fixture = retrieval_replay.load_fixture(FIXTURE)
    secret = copy.deepcopy(fixture)
    secret["providers"][0]["results"][0]["headers"] = {"authorization": "secret"}
    with pytest.raises(retrieval_replay.FixtureError, match="secret field"):
        retrieval_replay.validate_fixture(secret)


def test_replay_does_not_construct_registry_or_invoke_reranker(monkeypatch):
    fixture = retrieval_replay.load_fixture(FIXTURE)

    import smart_search.providers.registry as registry_module

    monkeypatch.setattr(registry_module, "default_registry", lambda: (_ for _ in ()).throw(
        AssertionError("replay must not construct a registry")
    ))
    assert retrieval_replay.replay_fixture(fixture)["rrf_results"]


def test_replay_command_supports_markdown(capsys):
    assert retrieval_replay.main([str(FIXTURE), "--format", "markdown", "--top-k", "1"]) == 0
    output = capsys.readouterr().out
    assert "# Offline retrieval replay" in output
    assert "Historical pre-rerank RRF output" in output
    assert "| 0 |" in output


def test_replay_fresh_process_blocks_runtime_imports():
    import os
    import subprocess
    import sys

    source_root = FIXTURE.parents[3] / "src"
    script_path = Path(__file__).parents[1] / "benchmarks" / "retrieval_replay.py"
    child = r'''
import builtins
import runpy
import sys

real_import = builtins.__import__


def guarded_import(name, *args, **kwargs):
    if name == "httpx" or name.startswith("smart_search.config") or name.startswith("smart_search.providers"):
        raise AssertionError("replay imported forbidden runtime module: " + name)
    return real_import(name, *args, **kwargs)


builtins.__import__ = guarded_import
replay_script = sys.argv[2]
sys.argv = ["retrieval_replay.py", sys.argv[1], "--format", "json"]
runpy.run_path(replay_script, run_name="__main__")
'''
    env = {**os.environ, "PYTHONPATH": str(source_root)}
    completed = subprocess.run(
        [sys.executable, "-c", child, str(FIXTURE), str(script_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert '"fixture_id": "brave-exa-shared-url"' in completed.stdout
