import json

import httpx
import pytest

from smart_search import service
from smart_search import search_service
from smart_search.providers.base import (
    ProviderResult,
    classify_provider_exception,
    coerce_provider_result,
)


def test_provider_result_exposes_stable_fields_and_legacy_content_wire():
    result = ProviderResult.from_content(
        "answer",
        provider="xai-responses",
        capability="main_search",
        elapsed_ms=12.5,
        attempts=[{"status": "ok", "result_count": 1}],
    )

    assert isinstance(result, str)
    assert result.ok is True
    assert result.provider == "xai-responses"
    assert result.capability == "main_search"
    assert result.content == "answer"
    assert result.error_type == ""
    assert result.retryable is False
    assert result.elapsed_ms == 12.5
    assert result.provider_attempts[0]["provider"] == "xai-responses"
    assert result.provider_attempts[0]["capability"] == "main_search"
    assert result == "answer"


def test_provider_result_marks_successful_empty_payload_without_hiding_error():
    result = ProviderResult.from_payload(
        {"ok": True, "provider": "exa", "results": [], "elapsed_ms": 3},
        capability="docs_search",
    )

    assert result.ok is False
    assert result.error_type == "empty"
    assert result.retryable is False
    assert "no usable" in result.error
    assert json.loads(result)["error_type"] == "empty"


def test_coerce_provider_result_distinguishes_content_and_invalid_json():
    content = coerce_provider_result(
        "plain provider answer",
        provider="openai-compatible",
        capability="main_search",
        wire_format="content",
    )
    invalid_json = coerce_provider_result(
        "not-json",
        provider="exa",
        capability="docs_search",
    )

    assert content.ok is True
    assert content.content == "plain provider answer"
    assert invalid_json.ok is False
    assert invalid_json.error_type == "parse_error"
    assert invalid_json.retryable is False


def test_provider_http_and_transport_errors_have_stable_categories():
    request = httpx.Request("GET", "https://provider.example.test")
    cases = [
        (401, "auth_error", False),
        (403, "auth_error", False),
        (408, "timeout", True),
        (422, "parameter_error", False),
        (429, "rate_limited", True),
        (503, "network_error", True),
    ]

    for status_code, expected_type, expected_retryable in cases:
        response = httpx.Response(status_code, text="upstream", request=request)
        error = httpx.HTTPStatusError("upstream", request=request, response=response)
        error_type, message, retryable = classify_provider_exception(error)

        assert error_type == expected_type
        assert "upstream" in message
        assert retryable is expected_retryable

    error_type, _, retryable = classify_provider_exception(httpx.ReadTimeout("slow", request=request))
    assert error_type == "timeout"
    assert retryable is True


@pytest.mark.asyncio
async def test_service_consumes_provider_error_without_treating_it_as_success(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", "https://relay.example.test/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "relay-secret")

    async def failed_xai(self, query, platform="", ctx=None):
        return ProviderResult.from_error(
            provider="xai-responses",
            capability="main_search",
            error_type="auth_error",
            error="invalid credentials",
        )

    async def successful_relay(self, query, platform="", ctx=None):
        return ProviderResult.from_content(
            "fallback answer",
            provider="openai-compatible",
            capability="main_search",
        )

    monkeypatch.setattr(search_service.XAIResponsesSearchProvider, "search", failed_xai)
    monkeypatch.setattr(search_service.OpenAICompatibleSearchProvider, "search", successful_relay)

    result = await service.search("provider contract", fallback="auto")

    assert result["ok"] is True
    assert result["content"] == "fallback answer"
    assert result["fallback_used"] is True
    assert result["provider_attempts"][0]["status"] == "error"
    assert result["provider_attempts"][0]["error_type"] == "auth_error"
