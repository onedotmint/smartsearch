from __future__ import annotations

import json

import pytest

from smart_search.providers.exa_reader import ExaReaderProvider
from smart_search.providers.firecrawl import FirecrawlReaderProvider


@pytest.mark.asyncio
async def test_exa_reader_exception_result_redacts_secrets(monkeypatch):
    api_key = "opaque-exa-api-key-123"
    target_url = "https://reader-user:reader-password@example.com/page?api_key=query-secret#token=fragment-secret"
    exception_text = f"transport failed at {target_url} using {api_key}"
    provider = ExaReaderProvider("https://api-user:api-password@exa.example/v1?api_key=endpoint-secret", api_key)

    async def raise_leaky_exception(endpoint, headers, payload, ctx):
        raise RuntimeError(exception_text)

    monkeypatch.setattr(provider.transport, "_request_with_retry", raise_leaky_exception)

    result = await provider.read(target_url)
    serialized = json.dumps(result.to_dict())

    assert result.error_type == "protocol_error"
    assert result.error == "provider response violated its protocol"
    for secret in (api_key, "reader-password", "query-secret", "fragment-secret", "endpoint-secret"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_firecrawl_reader_exception_result_redacts_secrets(monkeypatch):
    api_key = "opaque-firecrawl-api-key-456"
    target_url = "https://reader-user:reader-password@example.com/page?token=query-secret#signature=fragment-secret"
    exception_text = f"request failed at {target_url} with Bearer {api_key}"
    provider = FirecrawlReaderProvider("https://api-user:api-password@firecrawl.example/v1?secret=endpoint-secret", api_key)

    def raise_leaky_exception(*args, **kwargs):
        raise RuntimeError(exception_text)

    monkeypatch.setattr("smart_search.providers.firecrawl.request_client", raise_leaky_exception)

    result = await provider.read(target_url)
    serialized = json.dumps(result.to_dict())

    assert result.error_type == "protocol_error"
    assert result.error == "provider response violated its protocol"
    for secret in (api_key, "reader-password", "query-secret", "fragment-secret", "endpoint-secret"):
        assert secret not in serialized


TARGET_URL = (
    "https://reader-user:reader-password@example.com/page"
    "?api_key=query-secret&token=token-secret#signature=fragment-secret"
)
ENDPOINT_URL = "https://api-user:api-password@provider.example/v1?api_key=endpoint-secret#token=endpoint-fragment-secret"
ALL_SECRETS = (
    "reader-user",
    "reader-password",
    "api-user",
    "api-password",
    "query-secret",
    "token-secret",
    "fragment-secret",
    "endpoint-secret",
    "endpoint-fragment-secret",
    "exa-api-key",
    "firecrawl-api-key",
)


def assert_safe_result(result):
    serialized = json.dumps(result.to_dict())
    for secret in ALL_SECRETS:
        assert secret not in serialized
        assert secret not in result.error
    assert "https://[REDACTED]@example.com/page" in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"results": [{"text": "Exa content", "title": "A page"}]}, "ok"),
        ({"results": []}, "empty"),
    ],
)
async def test_exa_reader_direct_results_redact_url_credentials(monkeypatch, payload, expected_status):
    provider = ExaReaderProvider(ENDPOINT_URL, "exa-api-key")
    calls = []

    async def return_payload(endpoint, headers, request_payload, ctx):
        calls.append(request_payload)
        return payload

    monkeypatch.setattr(provider.transport, "_request_with_retry", return_payload)

    result = await provider.read(TARGET_URL)

    assert result.status == expected_status
    assert calls == [{"ids": [TARGET_URL]}]
    assert_safe_result(result)


class _FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self.body


class _FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeFirecrawlClient:
    def __init__(self, payload, calls):
        self.payload = payload
        self.calls = calls

    def stream(self, method, endpoint, **kwargs):
        self.calls.append({"method": method, "endpoint": endpoint, "kwargs": kwargs})
        response = _FakeResponse(self.payload)
        return _FakeStream(response)


class _FakeClientContext:
    def __init__(self, payload, calls):
        self.client = _FakeFirecrawlClient(payload, calls)

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"data": {"markdown": "Firecrawl content"}}, "ok"),
        ({"data": {"markdown": ""}}, "empty"),
    ],
)
async def test_firecrawl_reader_direct_results_redact_url_credentials(monkeypatch, payload, expected_status):
    calls = []
    provider = FirecrawlReaderProvider(ENDPOINT_URL, "firecrawl-api-key")
    monkeypatch.setattr(
        "smart_search.providers.firecrawl.request_client",
        lambda *args, **kwargs: _FakeClientContext(payload, calls),
    )

    result = await provider.read(TARGET_URL)

    assert result.status == expected_status
    assert calls[0]["endpoint"] == f"{ENDPOINT_URL}/scrape"
    assert calls[0]["kwargs"]["json"]["url"] == TARGET_URL
    assert_safe_result(result)


@pytest.mark.asyncio
async def test_firecrawl_reader_missing_key_redacts_url_credentials():
    provider = FirecrawlReaderProvider(ENDPOINT_URL, "")

    result = await provider.read(TARGET_URL)

    assert result.status == "error"
    assert result.error_type == "config_error"
    assert result.error == "Firecrawl API key is not configured"
    assert_safe_result(result)
