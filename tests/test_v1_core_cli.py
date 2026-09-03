"""Deterministic checks for the v1 core boundary."""
from __future__ import annotations

import asyncio
import json

from smart_search.core.models import Candidate, Evidence, RetrievalPolicy
from smart_search.core.retrieval import search
from smart_search.evidence.fetch import FetchOutcome, read
from smart_search.providers.registry import Registry
from smart_search.research.runner import run
from smart_search.providers.brave import to_discovery_candidates as brave_normalize
from smart_search.providers.exa import to_discovery_candidates as exa_normalize
from smart_search.providers.tavily import to_discovery_candidates as tavily_normalize


class SearchStub:
    def __init__(self, provider_id, rows):
        self.provider_id = provider_id
        self.rows = rows

    async def search(self, query, limit=5):
        return {"ok": True, "results": self.rows[:limit]}


class ReaderStub:
    def __init__(self, provider_id, result):
        self.provider_id = provider_id
        self.result = result
        self.calls = []

    async def read(self, url):
        self.calls.append(url)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_normalizers_accept_raw_results_lists():
    for normalize, provider in ((brave_normalize, "brave"), (exa_normalize, "exa"), (tavily_normalize, "tavily")):
        candidates = normalize([{"url": "https://example.com", "title": provider}])
        assert len(candidates) == 1
        assert candidates[0].provider == provider
        assert candidates[0].provider_rank == 0


def test_search_fuses_provenance_and_rerank_failure_keeps_rrf_order():
    registry = Registry(search=[
        SearchStub("brave", [{"url": "https://example.com/a?utm_source=x", "title": "A"}]),
        SearchStub("exa", [{"url": "https://example.com/a", "title": "A2"}, {"url": "https://example.com/b", "title": "B"}]),
    ])
    outcome = asyncio.run(search("query", RetrievalPolicy(rerank=False), registry=registry))
    assert outcome.ranked[0].candidate.providers == ("brave", "exa")
    assert outcome.ranked[0].candidate.provider_ranks == {"brave": 0, "exa": 0}
    assert [item.rank for item in outcome.ranked] == [0, 1]


def test_read_fallback_is_ordered_bounded_and_visible():
    readers = [
        ReaderStub("jina", RuntimeError("offline")),
        ReaderStub("firecrawl", {"ok": True, "content": "  "}),
        ReaderStub("exa", {"ok": True, "content": "0123456789", "title": "Page"}),
    ]
    outcome = asyncio.run(read("https://example.com/page", registry=Registry(readers=readers), max_chars=4))
    assert outcome.evidence is not None
    assert outcome.evidence.content == "0123"
    assert outcome.evidence.truncated is True
    assert [attempt.provider for attempt in outcome.attempts] == ["jina", "firecrawl", "exa"]
    assert [attempt.status for attempt in outcome.attempts] == ["failed", "empty", "complete"]


def test_research_is_evidence_only_and_deduplicates_urls():
    search_outcome = asyncio.run(search(
        "query",
        RetrievalPolicy(providers=("brave",), rerank=False),
        registry=Registry(search=[SearchStub("brave", [
            {"url": "https://example.com/a?utm_campaign=x", "title": "A"},
            {"url": "https://example.com/a", "title": "A duplicate"},
        ])]),
    ))
    reader = ReaderStub("jina", {"ok": True, "content": "evidence"})
    result = asyncio.run(run("query", search_fn=lambda _query: search_outcome,
                             read_fn=lambda url: read(url, registry=Registry(readers=[reader]))))
    payload = result.to_dict()
    assert len(result.evidence) == 1
    assert result.citations[0]["evidence_id"] == result.evidence[0].id
    assert "answer" not in json.dumps(payload)


class FailingReranker:
    provider_id = "jina"

    async def rerank(self, query, documents, top_n=5):
        raise RuntimeError("unlabelled-reranker-secret")


def test_rerank_failure_preserves_rrf_order_and_is_safe():
    registry = Registry(
        search=[SearchStub("brave", [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.com/b", "title": "B"},
        ])],
        rerankers=[FailingReranker()],
    )
    outcome = asyncio.run(search("query", RetrievalPolicy(rerank=True), registry=registry))
    assert [item.candidate.url for item in outcome.ranked] == ["https://example.com/a", "https://example.com/b"]
    assert outcome.warnings and "secret" not in " ".join(outcome.warnings)


def test_malformed_provider_metadata_is_an_attempt_not_a_fanout_crash():
    class Malformed(SearchStub):
        async def search(self, query, limit=5):
            return {"ok": True, "elapsed_ms": "not-a-number", "results": []}

    registry = Registry(search=[Malformed("brave", []), SearchStub("exa", [
        {"url": "https://example.com/ok", "title": "OK"}
    ])])
    outcome = asyncio.run(search("query", RetrievalPolicy(rerank=False), registry=registry))
    assert outcome.ranked[0].candidate.url == "https://example.com/ok"
    assert outcome.attempts[0].status == "failed"
    assert outcome.attempts[0].error_type == "parse_error"
    assert "not-a-number" not in outcome.attempts[0].error


def test_reader_rejects_non_text_payloads_without_stringifying_them():
    outcome = asyncio.run(read(
        "https://example.com/page",
        registry=Registry(readers=[
            ReaderStub("jina", {"ok": True, "content": {"secret": "payload"}}),
            ReaderStub("firecrawl", ["not evidence"]),
            ReaderStub("exa", {"ok": True, "content": "valid"}),
        ]),
    ))
    assert outcome.evidence is not None and outcome.evidence.content == "valid"
    assert [attempt.status for attempt in outcome.attempts] == ["failed", "failed", "complete"]
    assert all("payload" not in attempt.error for attempt in outcome.attempts)


def test_research_failure_has_error_and_failed_exit_status():
    from smart_search.cli import _exit_code, run_research
    payload = asyncio.run(run_research("query", registry=Registry()))
    assert payload["status"] == "failed"
    assert payload["error"] is not None
    assert _exit_code(payload) == 4
    assert "answer" not in json.dumps(payload)


def test_cli_known_and_unknown_parser_errors_are_v1_envelopes(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1] / "src")}
    known = subprocess.run([sys.executable, "-m", "smart_search.cli", "read"], capture_output=True, text=True, env=env)
    unknown = subprocess.run([sys.executable, "-m", "smart_search.cli", "secret-token"], capture_output=True, text=True, env=env)
    assert known.returncode == 2
    assert json.loads(known.stdout)["operation"] == "read"
    unknown_payload = json.loads(unknown.stdout)
    assert unknown.returncode == 2
    assert unknown_payload["operation"] == "unknown"
    assert "secret-token" not in known.stdout + unknown.stdout



def test_research_stages_bound_concurrency_and_record_failed_reads():
    from smart_search.providers.registry import ProviderAttempt

    search_outcome = asyncio.run(search(
        "query",
        RetrievalPolicy(providers=("brave",), rerank=False),
        registry=Registry(search=[SearchStub("brave", [
            {"url": "https://example.com/0", "title": "zero"},
            {"url": "https://example.com/1", "title": "one"},
            {"url": "https://example.com/2", "title": "two"},
        ])]),
    ))
    active = 0
    maximum = 0

    async def read_fn(url):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        if url.endswith("/0"):
            return FetchOutcome(None, (ProviderAttempt("jina", "read", "failed", "network_error", "safe"),))
        return FetchOutcome(Evidence(url, "evidence", "jina", "page"), ())

    result = asyncio.run(run("query", search_fn=lambda _: search_outcome, read_fn=read_fn, concurrency=2))
    assert maximum == 2
    assert [stage["name"] for stage in result.stages] == ["search", "read"]
    assert result.stages[1]["status"] == "degraded"
    assert any(gap["reason"] == "read_failed" for gap in result.gaps)
    assert {citation["evidence_id"] for citation in result.citations} == {item.id for item in result.evidence}


def test_cli_read_failure_has_safe_error_and_provider_exit():
    from smart_search.cli import _exit_code, run_read

    payload = asyncio.run(run_read(
        "https://example.com/page",
        registry=Registry(readers=[ReaderStub("jina", RuntimeError("unlabelled-secret"))]),
    ))
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "PROVIDER_ERROR"
    assert _exit_code(payload) == 4
    assert "unlabelled-secret" not in json.dumps(payload)


def test_reader_untrusted_error_type_is_classified_and_not_serialized():
    from smart_search.cli import run_read

    secret = "reader-error-token"
    registry = Registry(readers=[
        ReaderStub("jina", {
            "ok": False,
            "error_type": f"untrusted-{secret}",
            "error": f"provider leaked {secret}",
        }),
    ])
    outcome = asyncio.run(read("https://example.com/page", registry=registry))
    assert outcome.attempts[0].error_type == "protocol_error"
    payload = asyncio.run(run_read("https://example.com/page", registry=registry))
    rendered = json.dumps(payload)
    assert payload["status"] == "failed"
    assert payload["attempts"][0]["error_type"] == "protocol_error"
    assert secret not in rendered


def test_empty_search_with_a_failed_provider_is_failed():
    from smart_search.cli import _exit_code, run_search

    class FailedSearch(SearchStub):
        async def search(self, query, limit=5):
            return {"ok": False, "error_type": "network_error", "results": []}

    registry = Registry(search=[SearchStub("brave", []), FailedSearch("exa", [])])
    outcome = asyncio.run(search("query", RetrievalPolicy(rerank=False), registry=registry))
    assert not outcome.ranked
    assert outcome.failed
    payload = asyncio.run(run_search("query", rerank=False, registry=registry))
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "PROVIDER_ERROR"
    assert _exit_code(payload) == 4


def test_research_surfaces_degraded_reader_fallback():
    from smart_search.cli import _exit_code, run_research

    registry = Registry(
        search=[SearchStub("brave", [{"url": "https://example.com/page", "title": "Page"}])],
        readers=[
            ReaderStub("jina", {"ok": False, "error_type": "network_error"}),
            ReaderStub("exa", {"ok": True, "content": "evidence"}),
        ],
    )
    payload = asyncio.run(run_research("query", registry=registry))
    assert payload["status"] == "degraded"
    assert payload["error"] is None
    assert _exit_code(payload) == 0
    assert payload["data"]["stages"][1]["status"] == "degraded"
    assert any(gap["reason"] == "read_degraded" for gap in payload["data"]["gaps"])
    assert any(attempt["status"] == "failed" for attempt in payload["attempts"])


def test_research_with_all_readers_failed_is_failed():
    from smart_search.cli import _exit_code, run_research

    registry = Registry(
        search=[SearchStub("brave", [{"url": "https://example.com/page", "title": "Page"}])],
        readers=[
            ReaderStub("jina", RuntimeError("offline")),
            ReaderStub("exa", {"ok": False, "error_type": "network_error"}),
        ],
    )
    payload = asyncio.run(run_research("query", registry=registry))
    assert payload["status"] == "failed"
    assert payload["error"] is not None
    assert _exit_code(payload) == 4
    assert any(gap["reason"] == "read_failed" for gap in payload["data"]["gaps"])
    assert all(
        attempt["status"] == "failed"
        for attempt in payload["attempts"]
        if attempt["role"] == "read"
    )


def test_fresh_process_keeps_retired_owners_and_parser_paths_lightweight(tmp_path):
    import os
    import subprocess
    import sys

    config_dir = tmp_path / "config"
    source_root = __import__("pathlib").Path(__file__).parents[1] / "src"
    script = r'''
import importlib
import os
import sys
from pathlib import Path

retired = (
    "api_v2", "canonical_operations", "capability_executor", "capability_service",
    "capability_taxonomy", "cli_constants", "cli_parser", "cli_research", "cli_v2", "cli_v3",
    "control_executors", "control_operations", "control_plane_adapters", "control_plane_contract",
    "evidence_operations", "execution_primitives", "intent_router", "operation_runtime",
    "presentation", "provider_catalog", "provider_command_support", "provider_diagnostics",
    "provider_fetch_commands", "provider_mcp_commands", "provider_search_commands", "provider_vertical_commands",
    "research_plan", "research_plan_render", "research_service", "research_workflow", "research_workflow_contract",
    "retrieval", "service_support", "v2_contract",
)
for suffix in retired:
    name = "smart_search." + suffix
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        assert exc.name == name, (name, exc.name)
    else:
        raise AssertionError("retired owner imported: " + name)

from smart_search import cli
assert cli.main(["--help"]) == 0
assert cli.main(["read"]) == 2
assert cli.main(["not-a-command"]) == 2
assert "smart_search.config" not in sys.modules
assert not any(name == "smart_search.providers" or name.startswith("smart_search.providers.") for name in sys.modules)
assert not any(name == "httpx" or name.startswith("httpx.") for name in sys.modules)
assert not Path(os.environ["V1_TEST_CONFIG_DIR"]).exists()
'''
    env = {
        **os.environ,
        "PYTHONPATH": str(source_root),
        "V1_TEST_CONFIG_DIR": str(config_dir),
        "SMART_SEARCH_CONFIG_DIR": str(config_dir),
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_empty_search_from_all_providers_remains_complete():
    from smart_search.cli import run_search

    registry = Registry(search=[SearchStub("brave", []), SearchStub("exa", [])])
    payload = asyncio.run(run_search("query", rerank=False, registry=registry))
    assert payload["status"] == "complete"
    assert payload["error"] is None
