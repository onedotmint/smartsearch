import json

import httpx
import pytest

from smart_search.providers.exa import ExaSearchProvider


@pytest.mark.asyncio
async def test_exa_provider_reports_bad_request_as_parameter_error(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, endpoint, headers, json):
            return httpx.Response(
                400,
                json={"error": "invalid includeDomains"},
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr("smart_search.providers.exa.httpx.AsyncClient", FakeAsyncClient)
    provider = ExaSearchProvider("https://api.exa.ai", "key")

    data = json.loads(await provider.search("test", include_domains=["github.com freertos.org"]))

    assert data["ok"] is False
    assert data["error_type"] == "parameter_error"
    assert data["error"] == "HTTP 400"
    # Upstream body bytes must never cross the public provider error.
    assert "invalid includeDomains" not in data["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "expected_total"),
    [
        ([{"id": "doc-1", "title": "Docs", "url": "https://docs.example/doc-1"}], 1),
        ([], 0),
    ],
)
async def test_exa_provider_normalizes_candidate_and_empty_results(monkeypatch, results, expected_total):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, endpoint, headers, json):
            return httpx.Response(
                200,
                json={"results": results},
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr("smart_search.providers.exa.httpx.AsyncClient", FakeAsyncClient)
    provider = ExaSearchProvider("https://api.exa.ai", "key")

    data = json.loads(await provider.search("docs"))

    assert data["ok"] is (expected_total == 1)
    assert data["total"] == expected_total
    assert len(data["results"]) == expected_total
    if expected_total:
        assert data["results"][0]["id"] == "doc-1"
        assert data["results"][0]["url"] == "https://docs.example/doc-1"
