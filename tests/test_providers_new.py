import json

import httpx
import pytest

from smart_search.providers.context7 import Context7Provider
from smart_search.providers.exa import ExaSearchProvider
from smart_search.providers.zhipu import ZhipuWebSearchProvider


@pytest.mark.asyncio
async def test_zhipu_provider_normalizes_search_results(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects=True):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, endpoint, headers, json):
            return httpx.Response(
                200,
                json={
                    "request_id": "r1",
                    "search_result": [
                        {
                            "title": "Title",
                            "content": "Snippet",
                            "link": "https://example.com",
                            "media": "Example",
                            "publish_date": "2026-05-12",
                        }
                    ],
                },
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr("smart_search.providers.zhipu.httpx.AsyncClient", FakeAsyncClient)
    provider = ZhipuWebSearchProvider("https://open.bigmodel.cn/api", "key")

    data = json.loads(await provider.search("hello"))

    assert data["ok"] is True
    assert data["results"][0]["url"] == "https://example.com"
    assert data["results"][0]["provider"] == "zhipu"


@pytest.mark.asyncio
async def test_zhipu_provider_uses_configured_engine_and_call_override(monkeypatch):
    payloads = []

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects=True):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, endpoint, headers, json):
            payloads.append(json.copy())
            return httpx.Response(
                200,
                json={"request_id": "r1", "search_result": []},
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr("smart_search.providers.zhipu.httpx.AsyncClient", FakeAsyncClient)
    provider = ZhipuWebSearchProvider("https://open.bigmodel.cn/api", "key", search_engine="search_pro")

    data = json.loads(await provider.search("hello"))
    override_data = json.loads(await provider.search("hello", search_engine="search_pro_quark"))

    assert data["search_engine"] == "search_pro"
    assert override_data["search_engine"] == "search_pro_quark"
    assert payloads[0]["search_engine"] == "search_pro"
    assert payloads[1]["search_engine"] == "search_pro_quark"


@pytest.mark.asyncio
async def test_zhipu_provider_reports_rate_limit_without_retry(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects=True):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, endpoint, headers, json):
            calls.append(endpoint)
            return httpx.Response(
                429,
                json={"error": "rate limited"},
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr("smart_search.providers.zhipu.httpx.AsyncClient", FakeAsyncClient)
    provider = ZhipuWebSearchProvider("https://open.bigmodel.cn/api", "key")

    data = json.loads(await provider.search("test"))

    assert data["ok"] is False
    assert data["error_type"] == "rate_limited"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_context7_provider_normalizes_library_results(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects=True):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, endpoint, headers):
            return httpx.Response(
                200,
                json=[{"id": "/facebook/react", "title": "React", "description": "UI"}],
                headers={"content-type": "application/json"},
                request=httpx.Request("GET", endpoint),
            )

    monkeypatch.setattr("smart_search.providers.context7.httpx.AsyncClient", FakeAsyncClient)
    provider = Context7Provider("https://context7.com", "key")

    data = json.loads(await provider.library("react", "hooks"))

    assert data["ok"] is True
    assert data["results"][0]["id"] == "/facebook/react"
    assert data["results"][0]["provider"] == "context7"


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
