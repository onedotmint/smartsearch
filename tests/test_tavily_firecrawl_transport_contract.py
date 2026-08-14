"""Direct transport contract freezes for Tavily and Firecrawl.

These adapters live as uncached command helpers rather than class providers.
Errors must keep classified error_type values and must never be rewritten as a
successful empty result before same-capability fallback.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from smart_search import operation_runtime, provider_fetch_commands, provider_search_commands
from smart_search.providers.base import ProviderError
from smart_search.runtime_cache import RequestContext, request_scope


class _FakeResponseClient:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []

    async def post(self, url, headers=None, json=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}, "kwargs": kwargs})
        if self.exception is not None:
            raise self.exception
        return self.response


def _install_request_client(monkeypatch, module, client: _FakeResponseClient):
    @asynccontextmanager
    async def fake_request_client(*args, **kwargs):
        yield client

    monkeypatch.setattr(module, "request_client", fake_request_client)
    return client


def _http_error(status_code: int, url: str = "https://provider.example/path") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    response = httpx.Response(status_code, text=f"HTTP {status_code}", request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


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
async def test_tavily_search_http_errors_remain_classified(monkeypatch, status_code, expected_error_type):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    client = _FakeResponseClient(exception=_http_error(status_code, "https://api.tavily.com/search"))
    _install_request_client(monkeypatch, provider_search_commands, client)

    with pytest.raises(ProviderError) as exc:
        await provider_search_commands.call_tavily_search("query")

    assert exc.value.error_type == expected_error_type
    assert exc.value.provider == "tavily"
    assert exc.value.capability == "web_search"
    assert client.calls, "transport request must occur when eligible"


@pytest.mark.asyncio
async def test_tavily_search_timeout_and_schema_errors_are_not_empty_success(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

    timeout_client = _FakeResponseClient(
        exception=httpx.ReadTimeout("slow", request=httpx.Request("POST", "https://api.tavily.com/search"))
    )
    _install_request_client(monkeypatch, provider_search_commands, timeout_client)
    with pytest.raises(ProviderError) as timeout_exc:
        await provider_search_commands.call_tavily_search("query")
    assert timeout_exc.value.error_type == "timeout"
    assert timeout_exc.value.capability == "web_search"

    schema_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"results": "not-a-list"},
            request=httpx.Request("POST", "https://api.tavily.com/search"),
        )
    )
    _install_request_client(monkeypatch, provider_search_commands, schema_client)
    with pytest.raises(ProviderError) as schema_exc:
        await provider_search_commands.call_tavily_search("query")
    assert schema_exc.value.error_type == "parse_error"
    # ValueError path is classified; never silently returns []/None as success without classification.
    assert schema_exc.value.provider == "tavily"


@pytest.mark.asyncio
async def test_tavily_search_empty_results_return_none_not_fake_success_payload(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"results": []},
            request=httpx.Request("POST", "https://api.tavily.com/search"),
        )
    )
    _install_request_client(monkeypatch, provider_search_commands, client)

    result = await provider_search_commands.call_tavily_search("query")
    # Empty discovery is None (empty), not an ok payload with zero rows.
    assert result is None


@pytest.mark.asyncio
async def test_tavily_extract_transport_http_and_schema_errors_raise(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

    transport_client = _FakeResponseClient(
        exception=httpx.ConnectError("boom", request=httpx.Request("POST", "https://api.tavily.com/extract"))
    )
    _install_request_client(monkeypatch, provider_fetch_commands, transport_client)
    with pytest.raises(ProviderError) as transport_exc:
        await provider_fetch_commands.call_tavily_extract("https://example.com")
    assert transport_exc.value.error_type == "network_error"
    assert transport_exc.value.capability == "web_fetch"

    http_client = _FakeResponseClient(exception=_http_error(401, "https://api.tavily.com/extract"))
    _install_request_client(monkeypatch, provider_fetch_commands, http_client)
    with pytest.raises(ProviderError) as http_exc:
        await provider_fetch_commands.call_tavily_extract("https://example.com")
    assert http_exc.value.error_type == "auth_error"

    schema_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"results": "bad"},
            request=httpx.Request("POST", "https://api.tavily.com/extract"),
        )
    )
    _install_request_client(monkeypatch, provider_fetch_commands, schema_client)
    with pytest.raises(ProviderError) as schema_exc:
        await provider_fetch_commands.call_tavily_extract("https://example.com")
    assert schema_exc.value.error_type == "parse_error"
    assert schema_exc.value.provider == "tavily"


@pytest.mark.asyncio
async def test_tavily_extract_empty_body_is_none_not_success_string(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"results": [{"raw_content": "   "}], "failed_results": []},
            request=httpx.Request("POST", "https://api.tavily.com/extract"),
        )
    )
    _install_request_client(monkeypatch, provider_fetch_commands, client)

    result = await provider_fetch_commands.call_tavily_extract("https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_tavily_map_http_timeout_schema_and_empty_classification(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

    http_client = _FakeResponseClient(exception=_http_error(503, "https://api.tavily.com/map"))
    _install_request_client(monkeypatch, provider_fetch_commands, http_client)
    http_result = await provider_fetch_commands.call_tavily_map("https://example.com")
    assert http_result["ok"] is False
    assert http_result["error_type"] == "network_error"
    assert "results" not in http_result or http_result.get("results") in (None, [])

    timeout_client = _FakeResponseClient(exception=httpx.TimeoutException("slow"))
    _install_request_client(monkeypatch, provider_fetch_commands, timeout_client)
    timeout_result = await provider_fetch_commands.call_tavily_map("https://example.com")
    assert timeout_result["ok"] is False
    assert timeout_result["error_type"] == "timeout"

    schema_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"results": "nope"},
            request=httpx.Request("POST", "https://api.tavily.com/map"),
        )
    )
    _install_request_client(monkeypatch, provider_fetch_commands, schema_client)
    schema_result = await provider_fetch_commands.call_tavily_map("https://example.com")
    assert schema_result["ok"] is False
    assert schema_result["error_type"] == "parse_error"

    empty_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"base_url": "https://example.com", "results": [], "response_time": 0.1},
            request=httpx.Request("POST", "https://api.tavily.com/map"),
        )
    )
    _install_request_client(monkeypatch, provider_fetch_commands, empty_client)
    empty_result = await provider_fetch_commands.call_tavily_map("https://example.com")
    assert empty_result["ok"] is False
    assert empty_result["error_type"] == "empty"
    assert empty_result["results"] == []
    assert empty_result["retryable"] is False


@pytest.mark.asyncio
async def test_tavily_map_uses_shared_client_and_clamps_timeout_to_deadline(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

    class RecordingClient:
        def __init__(self):
            self.posts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None, **kwargs):
            self.posts.append({"url": url, "headers": headers or {}, "json": json or {}, "kwargs": kwargs})
            return httpx.Response(
                200,
                json={"base_url": "https://example.com", "results": [], "response_time": 0.1},
                request=httpx.Request("POST", url),
            )

    client = RecordingClient()
    ctx = await RequestContext.create(
        command="map",
        timeout_seconds=5.0,
        client_factory=lambda **kwargs: client,
        clock=lambda: 1000.0,
    )
    with request_scope(ctx):
        result = await provider_fetch_commands.call_tavily_map("https://example.com", timeout=150)

    # Empty map stays an empty result, not a transport error.
    assert result["ok"] is False
    assert result["error_type"] == "empty"
    # The request must flow through the context-bound shared client.
    assert client.posts, "call_tavily_map must issue the request through the shared client"
    sent = client.posts[0]
    assert sent["url"].endswith("/map")
    # Raw timeout (150 + 10 = 160) is clamped to the 5s command deadline.
    assert sent["kwargs"].get("timeout") == pytest.approx(5.0)
    assert sent["kwargs"]["timeout"] < 160.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error_type"),
    [
        (401, "auth_error"),
        (429, "rate_limited"),
        (503, "network_error"),
    ],
)
async def test_firecrawl_search_http_errors_remain_classified(monkeypatch, status_code, expected_error_type):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret")
    client = _FakeResponseClient(exception=_http_error(status_code, "https://api.firecrawl.dev/v2/search"))
    _install_request_client(monkeypatch, provider_search_commands, client)

    with pytest.raises(ProviderError) as exc:
        await provider_search_commands.call_firecrawl_search("query")

    assert exc.value.error_type == expected_error_type
    assert exc.value.provider == "firecrawl"
    assert exc.value.capability == "web_search"


@pytest.mark.asyncio
async def test_firecrawl_search_timeout_schema_and_empty_classification(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret")

    timeout_client = _FakeResponseClient(
        exception=httpx.ReadTimeout("slow", request=httpx.Request("POST", "https://api.firecrawl.dev/v2/search"))
    )
    _install_request_client(monkeypatch, provider_search_commands, timeout_client)
    with pytest.raises(ProviderError) as timeout_exc:
        await provider_search_commands.call_firecrawl_search("query")
    assert timeout_exc.value.error_type == "timeout"

    schema_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"data": {"web": "not-list"}},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/search"),
        )
    )
    _install_request_client(monkeypatch, provider_search_commands, schema_client)
    with pytest.raises(ProviderError) as schema_exc:
        await provider_search_commands.call_firecrawl_search("query")
    assert schema_exc.value.error_type == "parse_error"
    assert schema_exc.value.provider == "firecrawl"

    empty_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"data": {"web": []}},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/search"),
        )
    )
    _install_request_client(monkeypatch, provider_search_commands, empty_client)
    empty = await provider_search_commands.call_firecrawl_search("query")
    assert empty is None


@pytest.mark.asyncio
async def test_firecrawl_scrape_non_retryable_and_schema_errors_are_classified(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "1")

    auth_client = _FakeResponseClient(exception=_http_error(401, "https://api.firecrawl.dev/v2/scrape"))
    _install_request_client(monkeypatch, provider_fetch_commands, auth_client)
    with pytest.raises(ProviderError) as auth_exc:
        await provider_fetch_commands.call_firecrawl_scrape("https://example.com")
    assert auth_exc.value.error_type == "auth_error"
    assert auth_exc.value.capability == "web_fetch"

    schema_client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"data": "bad"},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/scrape"),
        )
    )
    _install_request_client(monkeypatch, provider_fetch_commands, schema_client)
    with pytest.raises(ProviderError) as schema_exc:
        await provider_fetch_commands.call_firecrawl_scrape("https://example.com")
    assert schema_exc.value.error_type == "parse_error"
    assert schema_exc.value.provider == "firecrawl"


@pytest.mark.asyncio
async def test_firecrawl_scrape_empty_markdown_is_none_not_success(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "1")
    client = _FakeResponseClient(
        response=httpx.Response(
            200,
            json={"data": {"markdown": "  "}},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/scrape"),
        )
    )
    _install_request_client(monkeypatch, provider_fetch_commands, client)

    result = await provider_fetch_commands.call_firecrawl_scrape("https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_tavily_and_firecrawl_errors_feed_same_capability_attempts(monkeypatch):
    """Service-level freeze: transport errors stay attempts and do not become ok-empty."""
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret")
    monkeypatch.setenv("SMART_SEARCH_FALLBACK_MODE", "auto")

    async def failing_tavily(url):
        raise ProviderError(
            "timeout",
            "tavily timed out",
            provider="tavily",
            capability="web_fetch",
            retryable=True,
        )

    async def failing_firecrawl(url, ctx=None):
        raise ProviderError(
            "network_error",
            "firecrawl unavailable",
            provider="firecrawl",
            capability="web_fetch",
            retryable=True,
        )

    # Anonymous Jina is now a normal eligible ``web_fetch`` provider, so pin
    # the chain to Tavily and Firecrawl to keep this transport-error freeze
    # focused on those two providers.
    monkeypatch.setattr(operation_runtime, "_default_call_tavily_extract", failing_tavily)
    monkeypatch.setattr(operation_runtime, "_default_call_firecrawl_scrape", failing_firecrawl)

    value, attempts = await operation_runtime._run_web_fetch_fallback(
        "https://example.com/page", providers=["tavily", "firecrawl"]
    )
    assert value is None
    assert attempts
    assert all(item["capability"] == "web_fetch" for item in attempts)
    assert any(item.get("status") == "error" for item in attempts)
    # Must not look like a successful empty fetch.
    assert value is None
    error_attempts = [item for item in attempts if item.get("status") == "error"]
    assert [(item["provider"], item["error_type"]) for item in error_attempts] == [
        ("tavily", "timeout"),
        ("firecrawl", "network_error"),
    ]
