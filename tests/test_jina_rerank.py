"""Jina rerank tests (v0.3.0): success shape, config gating, classified
failures, and RRF fallback preservation in the retrieval gateway."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from smart_search.providers import jina_rerank
from smart_search.providers.base import ProviderResult
from smart_search.providers.jina_rerank import rerank
from smart_search.retrieval import DiscoveryCandidate, resolve_retrieval_policy, retrieve


class _FakeClient:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []

    async def post(self, url, headers=None, json=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}, "kwargs": kwargs})
        if self.exception is not None:
            raise self.exception
        return self.response


def _install_request_client(monkeypatch, client):
    @asynccontextmanager
    async def fake_request_client(*args, **kwargs):
        yield client

    monkeypatch.setattr(jina_rerank, "request_client", fake_request_client)
    return client


def _rerank_response(results):
    return httpx.Response(
        200,
        json={"results": results},
        request=httpx.Request("POST", "https://api.jina.ai/v1/rerank"),
    )


@pytest.mark.asyncio
async def test_rerank_missing_key_is_config_error_before_network(monkeypatch):
    # conftest clears JINA_API_KEY; a spy client proves no request is issued.
    client = _FakeClient()
    _install_request_client(monkeypatch, client)

    result = await rerank("query", ["doc a", "doc b"])

    assert result.ok is False
    assert result.error_type == "config_error"
    assert result.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_rerank_success_shape_and_request_body(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    client = _FakeClient(
        response=_rerank_response(
            [{"index": 1, "relevance_score": 0.92}, {"index": 0, "relevance_score": 0.55}]
        )
    )
    _install_request_client(monkeypatch, client)

    result = await rerank("query", ["doc a", "doc b"], model="custom-model", top_n=2)

    assert result.ok is True
    payload = result.to_dict()
    assert payload["results"] == [
        {"index": 1, "relevance_score": 0.92},
        {"index": 0, "relevance_score": 0.55},
    ]
    assert payload["total"] == 2
    sent = client.calls[0]
    assert sent["url"] == "https://api.jina.ai/v1/rerank"
    assert sent["headers"]["authorization"] == "Bearer jina-secret"
    assert sent["json"] == {
        "model": "custom-model",
        "query": "query",
        "documents": ["doc a", "doc b"],
        "top_n": 2,
    }


@pytest.mark.asyncio
async def test_rerank_transport_failure_is_classified(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    client = _FakeClient(
        exception=httpx.ConnectError("boom", request=httpx.Request("POST", "https://api.jina.ai/v1/rerank"))
    )
    _install_request_client(monkeypatch, client)

    result = await rerank("query", ["doc a"])

    assert result.ok is False
    assert result.error_type == "network_error"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_rerank_http_error_classified(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    request = httpx.Request("POST", "https://api.jina.ai/v1/rerank")
    client = _FakeClient(
        exception=httpx.HTTPStatusError(
            "HTTP 429", request=request, response=httpx.Response(429, text="slow down", request=request)
        )
    )
    _install_request_client(monkeypatch, client)

    result = await rerank("query", ["doc a"])

    assert result.ok is False
    assert result.error_type == "rate_limited"


@pytest.mark.asyncio
async def test_rerank_empty_results_and_malformed_are_non_fatal(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")

    empty_client = _FakeClient(response=_rerank_response([]))
    _install_request_client(monkeypatch, empty_client)
    empty = await rerank("query", ["doc a"])
    assert empty.ok is False
    assert empty.error_type == "empty"

    malformed_client = _FakeClient(response=_rerank_response("not-a-list"))
    _install_request_client(monkeypatch, malformed_client)
    malformed = await rerank("query", ["doc a"])
    assert malformed.ok is False
    assert malformed.error_type == "parse_error"


# ---------------------------------------------------------------------------
# Gateway integration: RRF fallback preserved, never a single point of failure
# ---------------------------------------------------------------------------


def _candidate(url, provider, rank):
    return DiscoveryCandidate(url=url, title=url, provider=provider, provider_rank=rank)


async def _run_retrieve(monkeypatch, brave_results, exa_results, *, rerank_impl, jina_key="secret"):
    monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")
    monkeypatch.setenv("EXA_API_KEY", "exa-secret")
    monkeypatch.setenv("JINA_API_KEY", jina_key)

    async def fake_brave(query, max_results=5):
        return {"ok": True, "query": query, "results": brave_results, "total": len(brave_results)}

    async def fake_exa(query, num_results=5, include_highlights=False):
        return {"ok": True, "query": query, "results": exa_results, "total": len(exa_results)}

    monkeypatch.setattr("smart_search.retrieval.call_brave_search", fake_brave)
    monkeypatch.setattr("smart_search.retrieval.exa_search", fake_exa)
    monkeypatch.setattr("smart_search.retrieval._jina_rerank", rerank_impl)
    return await retrieve("query", ["brave", "exa"], 5, intent="general")


@pytest.mark.asyncio
async def test_retrieve_rerank_success_reorders_candidates(monkeypatch):
    calls = {}

    async def rerank_impl(query, documents, top_n=None, ctx=None):
        calls["documents"] = documents
        return ProviderResult.from_payload(
            {"ok": True, "results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.4}]},
            provider="jina",
            capability="rerank",
        )

    outcome = await _run_retrieve(
        monkeypatch,
        brave_results=[{"title": "B0", "url": "https://example.com/0", "description": "d"}],
        exa_results=[{"title": "E0", "url": "https://example.com/1", "description": "d"}],
        rerank_impl=rerank_impl,
    )
    # RRF order is [0, 1]; Jina scores flip it to [1, 0].
    assert [item.candidate.url for item in outcome.ranked] == [
        "https://example.com/1",
        "https://example.com/0",
    ]
    assert calls["documents"] == ["B0 d", "E0"]
    assert outcome.warnings == ()


@pytest.mark.asyncio
async def test_retrieve_rerank_transport_failure_keeps_rrf_order_with_warning(monkeypatch):
    async def rerank_impl(query, documents, top_n=None, ctx=None):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", "https://api.jina.ai/v1/rerank"))

    outcome = await _run_retrieve(
        monkeypatch,
        brave_results=[{"title": "B0", "url": "https://example.com/0", "description": "d"}],
        exa_results=[{"title": "E0", "url": "https://example.com/1", "description": "d"}],
        rerank_impl=rerank_impl,
    )
    assert [item.candidate.url for item in outcome.ranked] == [
        "https://example.com/0",
        "https://example.com/1",
    ]
    assert len(outcome.warnings) == 1
    assert "keeping RRF order" in outcome.warnings[0]


@pytest.mark.asyncio
async def test_retrieve_rerank_empty_result_keeps_rrf_order(monkeypatch):
    async def rerank_impl(query, documents, top_n=None, ctx=None):
        return ProviderResult.from_payload(
            {"ok": False, "error_type": "empty", "error": "no usable ranking"},
            provider="jina",
            capability="rerank",
        )

    outcome = await _run_retrieve(
        monkeypatch,
        brave_results=[{"title": "B0", "url": "https://example.com/0", "description": "d"}],
        exa_results=[{"title": "E0", "url": "https://example.com/1", "description": "d"}],
        rerank_impl=rerank_impl,
    )
    assert [item.candidate.url for item in outcome.ranked] == [
        "https://example.com/0",
        "https://example.com/1",
    ]
    assert outcome.warnings and "keeping RRF order" in outcome.warnings[0]


@pytest.mark.asyncio
async def test_retrieve_without_jina_key_skips_rerank_silently(monkeypatch):
    async def rerank_impl(query, documents, top_n=None, ctx=None):
        raise AssertionError("rerank must not be called without a key")

    outcome = await _run_retrieve(
        monkeypatch,
        brave_results=[{"title": "B0", "url": "https://example.com/0", "description": "d"}],
        exa_results=[{"title": "E0", "url": "https://example.com/1", "description": "d"}],
        rerank_impl=rerank_impl,
        jina_key="",
    )
    assert [item.candidate.url for item in outcome.ranked] == [
        "https://example.com/0",
        "https://example.com/1",
    ]
    assert outcome.warnings == ()


@pytest.mark.asyncio
async def test_retrieve_policy_selects_providers_and_merges_attempts(monkeypatch):
    async def rerank_impl(query, documents, top_n=None, ctx=None):
        raise AssertionError("unexpected rerank call")

    outcome = await _run_retrieve(
        monkeypatch,
        brave_results=[{"title": "B0", "url": "https://example.com/0", "description": "d"}],
        exa_results=[{"title": "E0", "url": "https://example.com/1", "description": "d"}],
        rerank_impl=rerank_impl,
    )
    assert outcome.policy == ("brave", "exa")
    assert [attempt.provider for attempt in outcome.attempts] == ["brave", "exa"]
    assert all(attempt.status.value == "ok" for attempt in outcome.attempts)
    # Provider-native score must never influence the RRF order.
    assert outcome.ranked[0].candidate.url == "https://example.com/0"
    assert resolve_retrieval_policy("general", ["brave", "exa"]) == ["brave", "exa"]
