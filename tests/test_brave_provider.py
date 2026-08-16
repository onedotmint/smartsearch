"""Brave provider adapter tests (v0.3.0): request construction, parsing,
empty results, classified errors, and ProviderResult payload shape."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from smart_search.providers.brave import BraveSearchProvider
from smart_search.providers.base import ProviderError


class _FakeClient:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []

    async def get(self, url, headers=None, params=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}, "kwargs": kwargs})
        if self.exception is not None:
            raise self.exception
        return self.response


def _install_request_client(monkeypatch, client):
    @asynccontextmanager
    async def fake_request_client(*args, **kwargs):
        yield client

    monkeypatch.setattr("smart_search.providers.brave.request_client", fake_request_client)
    return client


def _http_error(status_code: int, url: str = "https://api.search.brave.com/res/v1/web/search"):
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, text=f"HTTP {status_code}", request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def _no_retry(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "0")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MULTIPLIER", "0")


def _brave_payload(monkeypatch, response=None, exception=None):
    client = _FakeClient(response=response, exception=exception)
    _install_request_client(monkeypatch, client)
    provider = BraveSearchProvider("https://api.search.brave.com/res/v1", "brave-secret")
    return provider, client


@pytest.mark.asyncio
async def test_request_construction_headers_and_no_language_by_default(monkeypatch):
    _no_retry(monkeypatch)
    provider, client = _brave_payload(
        monkeypatch,
        response=httpx.Response(
            200,
            json={"web": {"results": [{"title": "t", "url": "https://example.com/1"}]}},
            request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
        ),
    )
    result = await provider.search("hello world", num_results=5)
    assert result.ok is True
    assert client.calls, "transport request must occur"
    sent = client.calls[0]
    assert sent["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert sent["headers"]["x-subscription-token"] == "brave-secret"
    assert sent["headers"]["accept"] == "application/json"
    # count param present; NO language/country/freshness defaults at all.
    assert sent["params"] == {"q": "hello world", "count": 5}


@pytest.mark.asyncio
async def test_language_country_freshness_sent_only_when_explicit(monkeypatch):
    _no_retry(monkeypatch)
    provider, client = _brave_payload(
        monkeypatch,
        response=httpx.Response(
            200,
            json={"web": {"results": []}},
            request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
        ),
    )
    await provider.search("q", language="zh", country="CN", freshness="pd")
    sent = client.calls[0]
    assert sent["params"] == {"q": "q", "count": 5, "language": "zh", "country": "CN", "freshness": "pd"}

    # Explicit None values stay absent.
    await provider.search("q2", language=None, country=None, freshness=None)
    assert client.calls[1]["params"] == {"q": "q2", "count": 5}


@pytest.mark.asyncio
async def test_response_parse_and_payload_shape(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _brave_payload(
        monkeypatch,
        response=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Result one",
                            "url": "https://example.com/1",
                            "description": "desc one",
                            "age": "2d",
                            "language": "en",
                            "family_friendly": True,
                            "page_age": "48h",
                        },
                        {"title": "Result two", "url": "https://example.com/2", "description": ""},
                    ]
                }
            },
            request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
        ),
    )
    data = await provider.search("query")
    assert data.ok is True
    payload = data.to_dict()
    assert payload["ok"] is True
    assert payload["query"] == "query"
    assert payload["provider"] == "brave"
    assert payload["capability"] == "web_search"
    assert payload["total"] == 2
    first = payload["results"][0]
    assert first == {
        "title": "Result one",
        "url": "https://example.com/1",
        "description": "desc one",
        "provider": "brave",
        "age": "2d",
        "language": "en",
        "family_friendly": True,
        "page_age": "48h",
    }
    assert payload["results"][1]["provider"] == "brave"


@pytest.mark.asyncio
async def test_empty_results_are_classified_empty(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _brave_payload(
        monkeypatch,
        response=httpx.Response(
            200,
            json={"web": {"results": []}},
            request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
        ),
    )
    data = await provider.search("query")
    # Mirrors the Exa adapter semantics: an empty result set is a classified
    # ``empty`` ProviderResult, never a fake success payload.
    assert data.ok is False
    assert data.to_dict()["error_type"] == "empty"
    assert data.to_dict()["results"] == []
    assert data.to_dict()["total"] == 0
    assert data.to_dict()["retryable"] is False


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
    provider, _client = _brave_payload(monkeypatch, exception=_http_error(status_code))
    result = await provider.search("query")
    assert result.ok is False
    payload = result.to_dict()
    assert payload["error_type"] == expected_error_type
    assert payload["retryable"] is (expected_error_type in {"timeout", "rate_limited", "network_error"})


@pytest.mark.asyncio
async def test_timeout_and_network_errors_are_classified(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _brave_payload(
        monkeypatch, exception=httpx.ReadTimeout("slow", request=httpx.Request("GET", "https://api.search.brave.com"))
    )
    result = await provider.search("query")
    assert result.ok is False
    assert result.to_dict()["error_type"] == "timeout"
    assert result.to_dict()["retryable"] is True

    provider2, _client2 = _brave_payload(
        monkeypatch, exception=httpx.ConnectError("boom", request=httpx.Request("GET", "https://api.search.brave.com"))
    )
    result2 = await provider2.search("query")
    assert result2.ok is False
    assert result2.to_dict()["error_type"] == "network_error"


@pytest.mark.asyncio
async def test_schema_error_classified_as_parse_error(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _brave_payload(
        monkeypatch,
        response=httpx.Response(
            200,
            json={"web": {"results": "not-a-list"}},
            request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
        ),
    )
    result = await provider.search("query")
    assert result.ok is False
    assert result.to_dict()["error_type"] == "parse_error"


@pytest.mark.asyncio
async def test_secret_masked_in_error_strings(monkeypatch):
    _no_retry(monkeypatch)
    provider, _client = _brave_payload(monkeypatch, exception=httpx.NetworkError("brave-secret leaked"))
    result = await provider.search("query")
    assert result.ok is False
    assert "brave-secret" not in result.error
    assert "***" in result.error


@pytest.mark.asyncio
async def test_retryable_status_codes_retry_with_budget(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MULTIPLIER", "0")
    client = _FakeClient(
        exception=_http_error(503, "https://api.search.brave.com/res/v1/web/search"),
    )
    _install_request_client(monkeypatch, client)
    provider = BraveSearchProvider("https://api.search.brave.com/res/v1", "brave-secret")
    result = await provider.search("query")
    # Initial attempt + one tenacity retry.
    assert len(client.calls) == 2
    assert result.ok is False
    assert result.to_dict()["error_type"] == "network_error"
