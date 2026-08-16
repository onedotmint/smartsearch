"""Gateway (v0.3.0) source_discovery integration tests.

Deterministic, network-free: provider runners are mocked at the retrieval
module boundary. Covers the V2 source_discovery envelope shape, attempts,
degraded partial failure, candidate/evidence boundary, gateway exclusivity
(no hidden legacy search call), and the no-policy-provider legacy path.
"""

from __future__ import annotations

import pytest

from smart_search import api_v2
from smart_search.evidence_operations import SourceDiscoveryRequest, source_discovery
from smart_search.execution_primitives import (
    ExecutionOutcome,
    success_attempt,
)
from smart_search.v2_contract import serialize_result, validate_envelope_dict


pytestmark = pytest.mark.asyncio


def _enable_gateway(monkeypatch, *, brave=True, exa=True, tavily=False):
    if brave:
        monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")
    if exa:
        monkeypatch.setenv("EXA_API_KEY", "exa-secret")
    if tavily:
        monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")


def _install_runners(
    monkeypatch,
    *,
    brave_results=None,
    exa_results=None,
    brave_error=None,
    exa_error=None,
    legacy=None,
):
    async def fake_brave(query, max_results=5):
        if brave_error is not None:
            return {"ok": False, "error_type": brave_error, "error": "brave failed"}
        return {"ok": True, "query": query, "results": brave_results or [], "total": len(brave_results or [])}

    async def fake_exa(query, num_results=5, include_highlights=False):
        if exa_error is not None:
            return {"ok": False, "error_type": exa_error, "error": "exa failed"}
        return {"ok": True, "query": query, "results": exa_results or [], "total": len(exa_results or [])}

    monkeypatch.setattr("smart_search.retrieval.call_brave_search", fake_brave)
    monkeypatch.setattr("smart_search.retrieval.exa_search", fake_exa)
    if legacy is not None:
        monkeypatch.setattr("smart_search.evidence_operations._execute_web_search", legacy)


def _candidate_dict(url, title, provider):
    return {"url": url, "title": title, "description": "snippet", "provider": provider}


class TestGatewaySourceDiscovery:
    async def test_envelope_shape_candidates_only_and_dedup(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(
            monkeypatch,
            brave_results=[_candidate_dict("https://example.com/a?utm_source=x", "Brave A", "brave")],
            exa_results=[_candidate_dict("https://example.com/a", "Exa A", "exa")],
        )
        envelope = await api_v2.source_discovery(SourceDiscoveryRequest("hello"))
        payload = serialize_result(envelope)
        validate_envelope_dict(payload)  # strict local + schema-compatible shape

        assert payload["schema_version"] == "2"
        assert payload["ok"] is True
        assert payload["status"] == "complete"
        assert payload["operation"] == "source_discovery"
        assert payload["command"] == "search"
        assert payload["result"]["total"] == 1
        assert payload["degradation"] == []
        assert payload["error"] is None

        # Candidate/evidence boundary: candidates only, no evidence items.
        candidates = payload["evidence"]["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["provider"] == "brave"  # first provider in policy order
        assert candidates[0]["resource"] == "https://example.com/a"
        assert candidates[0]["title"] == "Brave A"
        assert payload["evidence"]["items"] == []
        assert payload["result"]["items"] == [{"id": candidates[0]["id"]}]

        attempts = payload["attempts"]
        assert [(item["provider"], item["status"], item["capability"]) for item in attempts] == [
            ("brave", "ok", "source_discovery"),
            ("exa", "ok", "source_discovery"),
        ]
        assert [item["result_count"] for item in attempts] == [1, 1]
        assert payload["routing"]["requested_capabilities"] == ["source_discovery"]
        assert payload["routing"]["executed_capabilities"] == ["source_discovery"]

    async def test_degraded_on_partial_failure(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(
            monkeypatch,
            brave_error="timeout",
            exa_results=[_candidate_dict("https://example.com/b", "Exa B", "exa")],
        )
        outcome = await source_discovery(SourceDiscoveryRequest("hello"))
        assert outcome.status.value == "degraded"
        assert outcome.candidates
        assert len(outcome.degradation) == 1
        assert outcome.degradation[0].code == "provider_partial_failure"
        assert outcome.error is None
        assert [(item.provider, item.status.value, item.error.type if item.error else None) for item in outcome.attempts] == [
            ("brave", "error", "timeout"),
            ("exa", "ok", None),
        ]
        assert outcome.evidence_items == ()

    async def test_gateway_exclusivity_all_failed_no_legacy_call(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(monkeypatch, brave_error="network_error", exa_error="auth_error")

        async def legacy(*args, **kwargs):
            raise AssertionError("legacy _execute_web_search must never run after gateway start")

        monkeypatch.setattr("smart_search.evidence_operations._execute_web_search", legacy)

        outcome = await source_discovery(SourceDiscoveryRequest("hello"))
        assert outcome.status.value == "failed"
        assert outcome.candidates == ()
        # Gateway attempts ONLY: never a second hidden legacy search call.
        assert [(item.provider, item.status.value, item.error.type if item.error else None) for item in outcome.attempts] == [
            ("brave", "error", "network_error"),
            ("exa", "error", "auth_error"),
        ]
        assert outcome.error is not None
        assert outcome.error.type == "auth_error"
        assert outcome.degradation == ()

    async def test_empty_gateway_results_are_complete(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(monkeypatch, brave_results=[], exa_results=[])
        outcome = await source_discovery(SourceDiscoveryRequest("hello"))
        # Empty is complete, never automatic degradation (V2 semantics).
        assert outcome.status.value == "complete"
        assert outcome.candidates == ()
        assert outcome.degradation == ()
        assert len(outcome.attempts) == 2
        assert all(item.status.value == "empty" for item in outcome.attempts)

    async def test_fresh_intent_selects_brave_only(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(
            monkeypatch,
            brave_results=[_candidate_dict("https://example.com/news", "News", "brave")],
            exa_results=[_candidate_dict("https://example.com/x", "X", "exa")],
        )
        outcome = await source_discovery(SourceDiscoveryRequest("latest news today"))
        assert [item.provider for item in outcome.attempts] == ["brave"]
        assert outcome.candidates
        assert outcome.candidates[0].provider == "brave"

    async def test_technical_intent_selects_brave_and_exa(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(
            monkeypatch,
            brave_results=[_candidate_dict("https://example.com/api", "API", "brave")],
            exa_results=[_candidate_dict("https://example.com/docs", "Docs", "exa")],
        )
        outcome = await source_discovery(SourceDiscoveryRequest("React useEffect API docs"))
        assert [item.provider for item in outcome.attempts] == ["brave", "exa"]

    async def test_fused_candidates_ranked_deterministically(self, monkeypatch):
        _enable_gateway(monkeypatch)
        _install_runners(
            monkeypatch,
            brave_results=[
                _candidate_dict("https://example.com/1", "B1", "brave"),
                _candidate_dict("https://example.com/2", "B2", "brave"),
            ],
            exa_results=[
                _candidate_dict("https://example.com/1", "E1", "exa"),
            ],
        )
        outcome = await source_discovery(SourceDiscoveryRequest("hello"))
        # Shared URL (rank 0 in both providers) outranks the brave-only URL.
        assert outcome.candidates[0].resource == "https://example.com/1"
        assert outcome.candidates[1].resource == "https://example.com/2"


class TestLegacyCompatibilityPath:
    async def test_no_policy_provider_uses_legacy_lane(self, monkeypatch):
        # No brave/exa/tavily configured: pure v0.2.0 behavior.
        monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-secret")

        async def fake_legacy(query, count=5, providers="auto", fallback="auto"):
            return ExecutionOutcome(
                value=[_candidate_dict("https://example.com/legacy", "Legacy", "zhipu")],
                attempts=(
                    success_attempt(
                        "web_search",
                        "zhipu",
                        elapsed_ms=1.0,
                        result_count=1,
                    ),
                ),
            )

        monkeypatch.setattr("smart_search.evidence_operations._execute_web_search", fake_legacy)

        outcome = await source_discovery(SourceDiscoveryRequest("hello"))
        assert outcome.status.value == "complete"
        assert [item.provider for item in outcome.attempts] == ["zhipu"]
        assert outcome.candidates[0].provider == "zhipu"
        assert outcome.candidates[0].resource == "https://example.com/legacy"
        assert outcome.degradation == ()

    async def test_tavily_only_setup_keeps_legacy_lane(self, monkeypatch):
        # Tavily is configured but the general/fresh/technical auto-detected
        # policies never include tavily alone: the v0.2.0 legacy lane must run
        # unchanged so tavily-only setups keep their exact behavior.
        monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

        async def fake_legacy(query, count=5, providers="auto", fallback="auto"):
            return ExecutionOutcome(
                value=[_candidate_dict("https://example.com/legacy", "Legacy", "tavily")],
                attempts=(
                    success_attempt(
                        "web_search",
                        "tavily",
                        elapsed_ms=1.0,
                        result_count=1,
                    ),
                ),
            )

        monkeypatch.setattr("smart_search.evidence_operations._execute_web_search", fake_legacy)

        outcome = await source_discovery(SourceDiscoveryRequest("hello world"))
        assert outcome.status.value == "complete"
        assert [item.provider for item in outcome.attempts] == ["tavily"]
        assert outcome.candidates[0].resource == "https://example.com/legacy"
        assert outcome.degradation == ()

    async def test_no_policy_provider_and_no_qualified_provider_is_config_failed(self, monkeypatch):
        # Nothing configured at all: unchanged config-failed outcome.
        outcome = await source_discovery(SourceDiscoveryRequest("hello"))
        assert outcome.status.value == "failed"
        assert outcome.error is not None
        assert outcome.error.type == "config_error"
        assert outcome.attempts == ()
        assert outcome.candidates == ()
