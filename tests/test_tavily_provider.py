"""Tavily direct provider adapter + registration tests (v1).

Covers request construction, complete/empty/malformed payloads, classified
HTTP/timeout/network failures, retry behavior, secret containment, enabled/
keyed registry registration, and concurrent multi-provider fusion/provenance.
Every transport is mocked; no test performs a live Tavily call.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from smart_search.core.models import RetrievalPolicy
from smart_search.core.retrieval import search as core_search
from smart_search.providers.registry import Registry, default_registry
from smart_search.providers.tavily import TavilySearchProvider

ENDPOINT = "https://api.tavily.com/search"


class _FakeClient:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []
        self.on_post = None

    async def post(self, url, headers=None, json=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "json": json, "kwargs": kwargs})
        if self.on_post is not None:
            await self.on_post()
        if self.exception is not None:
            raise self.exception
        return self.response


def _install_request_client(monkeypatch, client):
    @asynccontextmanager
    async def fake_request_client(*args, **kwargs):
        yield client

    monkeypatch.setattr("smart_search.providers.tavily.request_client", fake_request_client)
    return client


def _http_error(status_code: int):
    request = httpx.Request("POST", ENDPOINT)
    response = httpx.Response(status_code, text=f"HTTP {status_code}", request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def _no_retry(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "0")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MULTIPLIER", "0")


def _tavily(monkeypatch, response=None, exception=None):
    client = _FakeClient(response=response, exception=exception)
    _install_request_client(monkeypatch, client)
    provider = TavilySearchProvider("https://api.tavily.com", "tavily-secret")
    return provider, client


def _response(payload):
    return httpx.Response(200, json=payload, request=httpx.Request("POST", ENDPOINT))


@pytest.mark.asyncio
async def test_request_construction_headers_and_discovery_payload(monkeypatch):
    _no_retry(monkeypatch)
    provider, client = _tavily(monkeypatch, response=_response({"results": [
        {"title": "t", "url": "https://example.com/1", "content": "c"},
    ]}))
    result = await provider.search("hello world", num_results=5)
    assert result.ok is True
    assert client.calls, "transport request must occur"
    sent = client.calls[0]
    assert sent["url"] == ENDPOINT
    assert sent["headers"]["authorization"] == "Bearer tavily-secret"
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["headers"]["accept"] == "application/json"
    assert sent["json"] == {
        "query": "hello world",
        "max_results": 5,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }


@pytest.mark.asyncio
async def test_response_parse_and_payload_shape(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(
        monkeypatch,
        response=_response(
            {
                "query": "query",
                "results": [
                    {"title": "Result one", "url": "https://example.com/1",
                     "content": "desc one", "score": 0.98, "raw_content": "ignored"},
                    {"title": "Result two", "url": "https://example.com/2", "content": ""},
                    "not-a-mapping",
                ],
            }
        ),
    )
    data = await provider.search("query")
    assert data.ok is True
    payload = data.to_dict()
    assert payload["ok"] is True
    assert payload["query"] == "query"
    assert payload["provider"] == "tavily"
    assert payload["capability"] == "web_search"
    assert payload["total"] == 2
    assert payload["results"] == [
        {"title": "Result one", "url": "https://example.com/1",
         "content": "desc one", "score": 0.98},
        {"title": "Result two", "url": "https://example.com/2", "content": ""},
    ]


@pytest.mark.asyncio
async def test_success_result_urls_are_redacted_at_provider_boundary(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(
        monkeypatch,
        response=_response({"results": [{
            "title": "Private result",
            "url": "https://user:password@example.invalid/page?api_key=url-secret&keep=yes#token=fragment-secret&ok=1",
            "content": "content",
        }]}),
    )

    result = await provider.search("query")

    returned_url = result.to_dict()["results"][0]["url"]
    assert returned_url == "https://[REDACTED]@example.invalid/page?api_key=%5BREDACTED%5D&keep=yes#token=%5BREDACTED%5D&ok=1"
    rendered = json.dumps(result.to_dict())
    assert "user" not in rendered
    assert "password" not in rendered
    assert "url-secret" not in rendered
    assert "fragment-secret" not in rendered


@pytest.mark.asyncio
async def test_empty_results_are_a_complete_success(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, response=_response({"results": []}))
    data = await provider.search("query")
    assert data.ok is True
    payload = data.to_dict()
    assert payload["error_type"] == ""
    assert payload["error"] == ""
    assert payload["results"] == []
    assert payload["total"] == 0
    assert payload["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"results": "not-a-list"},
        {"results": {"url": "https://example.com"}},
        {"answer": "no discovery payload"},
        ["a", "b"],
    ],
)
async def test_malformed_responses_are_parse_errors(monkeypatch, payload):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, response=_response(payload))
    result = await provider.search("query")
    assert result.ok is False
    assert result.to_dict()["error_type"] == "parse_error"
    assert result.error == "provider response could not be parsed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error_type"),
    [
        (401, "auth_error"),
        (403, "auth_error"),
        (408, "timeout"),
        (422, "parameter_error"),
        (429, "rate_limited"),
        (503, "network_error"),
    ],
)
async def test_http_errors_are_classified(monkeypatch, status_code, expected_error_type):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, exception=_http_error(status_code))
    result = await provider.search("query")
    assert result.ok is False
    payload = result.to_dict()
    assert payload["error_type"] == expected_error_type
    assert payload["retryable"] is (expected_error_type in {"timeout", "rate_limited", "network_error"})


@pytest.mark.asyncio
async def test_timeout_and_network_errors_are_classified(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, exception=httpx.ReadTimeout("slow", request=httpx.Request("POST", ENDPOINT)))
    result = await provider.search("query")
    assert result.ok is False
    assert result.to_dict()["error_type"] == "timeout"
    assert result.error == "provider request timed out"
    assert result.to_dict()["retryable"] is True

    provider2, _client2 = _tavily(monkeypatch, exception=httpx.ConnectError("boom", request=httpx.Request("POST", ENDPOINT)))
    result2 = await provider2.search("query")
    assert result2.ok is False
    assert result2.to_dict()["error_type"] == "network_error"
    assert result2.error == "provider request failed"


@pytest.mark.asyncio
async def test_secret_masked_in_error_strings(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, exception=httpx.NetworkError("tavily-secret leaked"))
    result = await provider.search("query")
    assert result.ok is False
    assert result.error == "provider request failed"
    rendered = json.dumps(result.to_dict())
    assert "tavily-secret" not in rendered


@pytest.mark.asyncio
async def test_secret_in_error_query_is_redacted(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, exception=httpx.NetworkError("request failed"))

    result = await provider.search("find tavily-secret documentation")

    assert result.to_dict()["query"] == "find [REDACTED] documentation"
    assert "tavily-secret" not in json.dumps(result.to_dict())



@pytest.mark.asyncio
async def test_http_error_output_excludes_url_secrets_and_response_body(monkeypatch):
    _no_retry(monkeypatch)
    secret_url = "https://user:password@example.invalid/search?api_key=url-secret"
    request = httpx.Request("POST", secret_url)
    response = httpx.Response(401, text="upstream token=body-secret", request=request)
    provider, _client = _tavily(
        monkeypatch,
        exception=httpx.HTTPStatusError("upstream token=body-secret", request=request, response=response),
    )

    result = await provider.search("query")

    assert result.error == "provider authentication failed"
    rendered = json.dumps(result.to_dict())
    assert "url-secret" not in rendered
    assert "body-secret" not in rendered
    assert "password" not in rendered


@pytest.mark.asyncio
async def test_retryable_status_codes_retry_with_budget(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MULTIPLIER", "0")
    client = _FakeClient(exception=_http_error(503))
    _install_request_client(monkeypatch, client)
    provider = TavilySearchProvider("https://api.tavily.com", "tavily-secret")
    result = await provider.search("query")
    # Initial attempt + one tenacity retry.
    assert len(client.calls) == 2
    assert result.ok is False
    assert result.to_dict()["error_type"] == "network_error"


@pytest.mark.asyncio
async def test_empty_tavily_is_complete_in_retrieval(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _tavily(monkeypatch, response=_response({"results": []}))
    registry = Registry(search=[provider])

    outcome = await core_search(
        "query", RetrievalPolicy(providers=("tavily",), rerank=False), registry=registry
    )

    assert outcome.failed is False
    assert outcome.degraded is False
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].status == "complete"
    assert outcome.attempts[0].result_count == 0
    assert outcome.attempts[0].error == ""


def test_default_policy_includes_tavily_after_brave_and_exa():
    assert RetrievalPolicy().providers == ("brave", "exa", "tavily")


def test_default_registry_omits_tavily_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_ENABLED", raising=False)
    registry = default_registry()
    assert "tavily" not in registry.search_ids


def test_default_registry_omits_tavily_when_disabled(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("TAVILY_ENABLED", "false")
    registry = default_registry()
    assert "tavily" not in registry.search_ids


def test_default_registry_registers_tavily_when_enabled_and_keyed(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    registry = default_registry()
    assert "tavily" in registry.search_ids
    provider = registry.search_provider("tavily")
    assert isinstance(provider, TavilySearchProvider)
    assert provider.api_key == "tavily-secret"
    assert provider.timeout == 30.0


def test_registered_tavily_runs_with_other_providers_and_fuses_provenance(monkeypatch):
    _no_retry(monkeypatch)
    active = 0
    maximum_active = 0

    async def mark_active():
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    class ExaStub:
        provider_id = "exa"

        async def search(self, query, limit=5):
            await mark_active()
            return {"ok": True, "results": [
                {"id": "https://example.com/shared", "title": "Shared", "text": "exa text"},
                {"id": "https://example.com/exa-only", "title": "Exa only"},
            ]}

    tavily, tavily_client = _tavily(
        monkeypatch,
        response=_response(
            {"results": [
                {"title": "Shared", "url": "https://example.com/shared", "content": "tavily text", "score": 0.9},
                {"title": "Tavily only", "url": "https://example.com/tavily-only", "content": "t text"},
            ]}
        ),
    )
    tavily_client.on_post = mark_active
    registry = Registry(search=[ExaStub(), tavily])

    outcome = asyncio.run(core_search("query", RetrievalPolicy(rerank=False), registry=registry))

    # The active counter observed both provider calls overlapping.
    assert maximum_active == 2
    assert {attempt.provider for attempt in outcome.attempts} == {"exa", "tavily"}
    assert all(attempt.status == "complete" for attempt in outcome.attempts)

    urls = [item.candidate.url for item in outcome.ranked]
    assert urls[0] == "https://example.com/shared"
    shared = next(item.candidate for item in outcome.ranked if item.candidate.url == "https://example.com/shared")
    # Canonicalization merged the shared URL with provenance from both providers.
    assert shared.providers == ("exa", "tavily")
    assert shared.provider_ranks == {"exa": 0, "tavily": 0}
    # Tavily's native score stayed provider metadata via normalize_tavily.
    assert shared.metadata.get("tavily_score") == 0.9
    assert "native_score" not in shared.metadata


def test_default_registry_search_calls_do_not_include_unconfigured_tavily(monkeypatch):
    """A configured Brave/Exa default registry performs no HTTP for Tavily."""
    _no_retry(monkeypatch)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")
    monkeypatch.setenv("EXA_API_KEY", "exa-secret")

    import smart_search.providers.tavily as tavily_module
    monkeypatch.setattr(tavily_module, "request_client", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("unconfigured Tavily must not construct an HTTP client")
    ))
    registry = default_registry()
    assert "tavily" not in registry.search_ids
    assert "brave" in registry.search_ids and "exa" in registry.search_ids
