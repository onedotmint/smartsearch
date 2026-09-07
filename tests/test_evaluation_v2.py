"""Offline tests for evaluation v2 dataset, metrics, artifacts, and CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks import evaluation, retrieval_replay

pytestmark = pytest.mark.evaluation

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval"
DATASET = ROOT / "benchmarks" / "fixtures" / "evaluation" / "v1"
REPLAY = ROOT / "benchmarks" / "fixtures" / "retrieval_replay" / "brave-exa-shared-url.json"
FROZEN_METADATA = {"generated_at": "2026-09-07T00:00:00Z", "commit": "test", "live": False}


def _help_env() -> dict[str, str]:
    blocked = {
        "BRAVE_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "JINA_API_KEY", "FIRECRAWL_API_KEY",
    }
    return {key: value for key, value in os.environ.items() if key not in blocked}


class _FakeRegistry:
    def __init__(self, search=(), rerank=False):
        self._search = {name: object() for name in search}
        self._rerank = object() if rerank else None

    def search_provider(self, provider_id: str):
        return self._search.get(provider_id)

    def reranker(self, provider_id: str = "jina"):
        return self._rerank


def test_dataset_covers_required_categories_and_canonical_ids():
    dataset = evaluation.load_dataset(DATASET)
    assert dataset.dataset_id == "evaluation-v1"
    assert [item["category"] for item in dataset.queries] == list(evaluation.CATEGORIES)
    assert all(item["url"] == evaluation.document_id(item["url"]) for item in dataset.qrels)
    assert any(item.get("freshness_expectation") for item in dataset.queries)
    assert any(item.get("freshness") == "current" for item in dataset.qrels)


def test_dataset_rejects_invalid_relevance_and_secrets():
    with pytest.raises(evaluation.DatasetError, match="0..3"):
        evaluation.validate_qrel(
            {
                "schema_version": 1,
                "query_id": "france-capital",
                "url": "https://example.test/x",
                "relevance": True,
                "source_type": "other",
                "judged_at": "2026-09-07",
            }
        )
    with pytest.raises(evaluation.DatasetError, match="secret"):
        evaluation.validate_query(
            {
                "schema_version": 1,
                "query_id": "x",
                "query": "x",
                "category": "general",
                "captured_at": "2026-09-07",
                "authoritative_domains": ["example.test"],
                "preferred_domains": ["example.test"],
                "api_key": "not-committable",
            }
        )


def test_standard_metrics_match_frozen_ir_measures_values():
    pytest.importorskip("ir_measures")
    qrels = {"q1": {"https://docs.python.org/a": 3, "https://docs.python.org/b": 1, "https://other.test/c": 0}}
    run = {"q1": {"https://docs.python.org/a": 1.0, "https://other.test/c": 0.5, "https://docs.python.org/b": 0.2}}
    metrics = evaluation.standard_metrics(qrels, run, k=5)["aggregate"]
    assert metrics["Recall@5"] == 1.0
    assert metrics["MRR@5"] == 1.0
    assert metrics["MAP@5"] == pytest.approx(0.833333, abs=1e-6)
    assert metrics["nDCG@5"] == pytest.approx(0.963940, abs=1e-6)


def test_fixture_evaluation_is_deterministic_and_compares_modes():
    pytest.importorskip("ir_measures")
    dataset = evaluation.load_dataset(DATASET)
    runs = evaluation.load_runs(DATASET / "runs.jsonl")
    first = evaluation.evaluate_report(
        queries=dataset.queries, qrels=dataset.qrels, runs_by_mode=runs, metadata=FROZEN_METADATA,
    )
    second = evaluation.evaluate_report(
        queries=dataset.queries, qrels=dataset.qrels, runs_by_mode=runs, metadata=FROZEN_METADATA,
    )
    assert first == second
    assert evaluation.render_json(first) == evaluation.render_json(second)
    assert evaluation.render_jsonl(first) == evaluation.render_jsonl(second)
    assert evaluation.render_markdown(first) == evaluation.render_markdown(second)
    for mode in evaluation.EVAL_MODES:
        assert first["modes"][mode]["status"] == "ok"
        standard = first["modes"][mode]["standard"]
        assert set(standard) == {"Recall@5", "MRR@5", "nDCG@5", "MAP@5"}
    assert first["modes"]["rrf"]["standard"]["nDCG@5"] >= first["modes"]["brave"]["standard"]["nDCG@5"]
    web = first["modes"]["rrf"]["web"]
    assert web["official_source_hit_rate"] == 1.0
    assert web["freshness_correctness"] == 1.0
    assert web["provider_contribution"]["brave"] > 0
    assert web["evidence_coverage"] == 1.0
    assert web["fetch_success_rate"] == 0.5
    assert first["modes"]["tavily"]["web"]["failure_rate"] > 0
    markdown = evaluation.render_markdown(first)
    assert "not a ci" in markdown.lower()
    assert "| rrf |" in markdown


def test_replay_fixture_converts_to_stable_metrics():
    pytest.importorskip("ir_measures")
    report = retrieval_replay.replay_fixtures(retrieval_replay.load_fixtures(REPLAY.parent), top_k=5)
    runs = evaluation.runs_from_replay(report, query_id="replay-shared")
    queries = [
        {
            "query_id": "replay-shared",
            "query": "example retrieval query",
            "category": "general",
            "authoritative_domains": ["example.test"],
            "preferred_domains": ["example.test"],
        }
    ]
    qrels = [
        evaluation.validate_qrel(
            {
                "schema_version": 1,
                "query_id": "replay-shared",
                "url": "https://example.test/shared?utm_source=brave",
                "relevance": 3,
                "source_type": "other",
                "judged_at": "2026-09-07",
            }
        ),
        evaluation.validate_qrel(
            {
                "schema_version": 1,
                "query_id": "replay-shared",
                "url": "https://example.test/alpha",
                "relevance": 1,
                "source_type": "other",
                "judged_at": "2026-09-07",
            }
        ),
        evaluation.validate_qrel(
            {
                "schema_version": 1,
                "query_id": "replay-shared",
                "url": "https://example.test/beta",
                "relevance": 0,
                "source_type": "other",
                "judged_at": "2026-09-07",
            }
        ),
    ]
    result = evaluation.evaluate_mode(queries, qrels, runs, k=2)
    assert result["status"] == "ok"
    assert result["standard"]["Recall@2"] == 1.0
    assert result["standard"]["MRR@2"] == 1.0
    assert runs[0]["urls"][0] == "https://example.test/shared"


def test_skipped_modes_do_not_receive_zero_metrics():
    dataset = evaluation.load_dataset(DATASET)
    report = evaluation.evaluate_report(
        queries=dataset.queries,
        qrels=dataset.qrels,
        runs_by_mode={},
        skipped={"brave": "missing BRAVE_API_KEY", "exa": "missing EXA_API_KEY", "tavily": "missing TAVILY_API_KEY", "rrf": "missing BRAVE_API_KEY", "rrf_jina": "missing JINA_API_KEY"},
        metadata=FROZEN_METADATA,
    )
    for mode, result in report["modes"].items():
        assert result["status"] == "skipped"
        assert "standard" not in result
        assert "reason" in result
    text = evaluation.render_markdown(report)
    assert "missing BRAVE_API_KEY" in text


def test_unconfigured_registry_skips_modes():
    registry = _FakeRegistry(search=("brave",), rerank=False)
    skipped = {}
    enabled = {}
    for mode, spec in evaluation.EVAL_MODES.items():
        ok, reason = evaluation.mode_configured(registry, spec)
        if ok:
            enabled[mode] = spec
        else:
            skipped[mode] = reason
    assert "brave" in enabled
    assert skipped["exa"].startswith("missing EXA_API_KEY")
    assert skipped["rrf"].startswith("missing")
    assert skipped["rrf_jina"].startswith("missing")
    ok_jina, reason_jina = evaluation.mode_configured(_FakeRegistry(search=("brave", "exa"), rerank=False), evaluation.EVAL_MODES["rrf_jina"])
    assert ok_jina is False
    assert reason_jina == "missing JINA_API_KEY"


def test_eval_help_is_offline_and_live_requires_opt_in():
    env = _help_env()
    help_run = subprocess.run(
        [sys.executable, str(SCRIPT), "retrieval", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert help_run.returncode == 0, help_run.stderr
    assert "usage:" in help_run.stdout.lower()
    assert "not a ci gate" in help_run.stdout.lower()
    refused = subprocess.run(
        [sys.executable, str(SCRIPT), "retrieval"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert refused.returncode == 2
    assert "--live" in refused.stderr
    child = r"""
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.argv = ["eval", "retrieval", "--help"]
runpy.run_path(str(root / "scripts" / "eval"), run_name="not_main")
loaded = [name for name in sys.modules if name == "httpx" or name.startswith("smart_search") or name == "ir_measures"]
assert loaded == [], loaded
"""
    imported = subprocess.run(
        [sys.executable, "-c", child, str(ROOT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr


def test_src_and_production_dependencies_do_not_include_ir_measures():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ir_measures" not in text
        assert "ir-measures" not in text
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    production, rest = pyproject.split("[project.optional-dependencies]", 1)
    assert "ir-measures" not in production
    assert 'ir-measures>=0.4.3,<0.5' in rest
    assert 'eval = [' in rest


def test_development_docs_describe_live_eval_without_making_it_a_gate():
    text = (ROOT / "docs" / "development.md").read_text(encoding="utf-8")
    assert "./scripts/eval retrieval" in text
    lower = text.lower()
    assert "not a ci" in lower or "never a ci" in lower
    assert "never" in lower and "correctness" in lower
