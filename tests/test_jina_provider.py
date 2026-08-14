import json

import httpx
import pytest

from smart_search.providers.jina import JinaReaderProvider


class FakeJinaClient:
    calls = []
    response: httpx.Response | None = None
    exception: Exception | None = None

    def __init__(self, timeout, follow_redirects=True):
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers):
        self.__class__.calls.append({"url": url, "headers": headers, "timeout": self.timeout})
        if self.__class__.exception:
            raise self.__class__.exception
        return self.__class__.response


@pytest.fixture(autouse=True)
def reset_fake_client():
    FakeJinaClient.calls = []
    FakeJinaClient.response = None
    FakeJinaClient.exception = None


@pytest.mark.asyncio
async def test_jina_reader_fetch_sends_auth_and_markdown_header(monkeypatch):
    FakeJinaClient.response = httpx.Response(
        200,
        text="Title: Example\n\nExample content.",
        request=httpx.Request("GET", "https://r.jina.ai/https://example.com"),
    )
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", "jina-secret", timeout=12)
    data = json.loads(await provider.fetch("https://example.com"))

    assert data["ok"] is True
    assert data["provider"] == "jina"
    assert data["content"].startswith("Title: Example")
    call = FakeJinaClient.calls[0]
    assert call["url"] == "https://r.jina.ai/https://example.com"
    assert call["headers"]["Authorization"] == "Bearer jina-secret"
    assert call["headers"]["X-Return-Format"] == "markdown"
    assert call["timeout"].read == 12.0


@pytest.mark.asyncio
async def test_jina_reader_anonymous_fetch_omits_authorization(monkeypatch):
    FakeJinaClient.response = httpx.Response(
        200,
        text="Title: Example\n\nAnonymous content.",
        request=httpx.Request("GET", "https://r.jina.ai/https://example.com"),
    )
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", None, timeout=12)
    data = json.loads(await provider.fetch("https://example.com"))

    assert data["ok"] is True
    assert data["provider"] == "jina"
    call = FakeJinaClient.calls[0]
    assert "Authorization" not in call["headers"]
    assert call["headers"]["X-Return-Format"] == "markdown"


@pytest.mark.asyncio
async def test_jina_reader_error_diagnostics_never_leak_key(monkeypatch):
    class LeakyClient(FakeJinaClient):
        async def get(self, url, headers):
            self.__class__.calls.append({"url": url, "headers": headers, "timeout": self.timeout})
            raise httpx.HTTPStatusError(
                "401 jina-secret leaked into message",
                request=httpx.Request("GET", url),
                response=httpx.Response(401, request=httpx.Request("GET", url)),
            )

    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", LeakyClient)

    provider = JinaReaderProvider("https://r.jina.ai", "jina-secret")
    data = json.loads(await provider.fetch("https://example.com"))

    assert data["ok"] is False
    assert data["error_type"] == "auth_error"
    assert "jina-secret" not in json.dumps(data)


@pytest.mark.asyncio
async def test_jina_reader_rejects_readerlm_without_key_before_network(monkeypatch):
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", None, respond_with="readerlm-v2")
    data = json.loads(await provider.fetch("https://example.com"))

    assert data["ok"] is False
    assert data["error_type"] == "config_error"
    assert FakeJinaClient.calls == []


@pytest.mark.asyncio
async def test_jina_reader_challenge_page_is_quality_error(monkeypatch):
    FakeJinaClient.response = httpx.Response(
        200,
        text="Title: Just a moment...\n\nChecking if the site connection is secure.",
        request=httpx.Request("GET", "https://r.jina.ai/https://blocked.example.com"),
    )
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", "jina-secret")
    data = json.loads(await provider.fetch("https://blocked.example.com"))

    assert data["ok"] is False
    assert data["error_type"] == "quality_error"
    assert "challenge" in data["error"]


@pytest.mark.asyncio
async def test_jina_reader_http_422_is_parameter_error(monkeypatch):
    FakeJinaClient.response = httpx.Response(
        422,
        text="bad url",
        request=httpx.Request("GET", "https://r.jina.ai/bad"),
    )
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", "jina-secret")
    data = json.loads(await provider.fetch("bad"))

    assert data["ok"] is False
    assert data["error_type"] == "parameter_error"
    assert "HTTP 422" in data["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error_type"),
    [
        (401, "auth_error"),
        (429, "rate_limited"),
    ],
)
async def test_jina_reader_http_status_error_types(monkeypatch, status_code, expected_error_type):
    FakeJinaClient.response = httpx.Response(
        status_code,
        text="provider error",
        request=httpx.Request("GET", "https://r.jina.ai/https://example.com"),
    )
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", "jina-secret")
    data = json.loads(await provider.fetch("https://example.com"))

    assert data["ok"] is False
    assert data["error_type"] == expected_error_type
    assert f"HTTP {status_code}" in data["error"]


@pytest.mark.asyncio
async def test_jina_reader_timeout_is_timeout_error(monkeypatch):
    FakeJinaClient.exception = httpx.TimeoutException("slow")
    monkeypatch.setattr("smart_search.providers.jina.httpx.AsyncClient", FakeJinaClient)

    provider = JinaReaderProvider("https://r.jina.ai", "jina-secret")
    data = json.loads(await provider.fetch("https://example.com"))

    assert data["ok"] is False
    assert data["error_type"] == "timeout"
