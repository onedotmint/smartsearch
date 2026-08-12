"""Direct deterministic tests for the retained provider result stabilization.

Migrated from the pre-cleanup ``test_provider_result_contract.py``: the
``ProviderResult`` field contract, ``coerce_provider_result`` coercion, and
``classify_provider_exception`` status categories all live in the retained
``providers/base.py``. The final test expresses the historical "provider error
is consumed and falls back within the same capability" service case on the
current typed executor, and never imports the removed V1 facade.
"""

from __future__ import annotations

import json

import httpx
import pytest

from smart_search import canonical_operations, capability_executor, evidence_operations
from smart_search.capability_executor import CapabilityOperation, execute_capability
from smart_search.evidence_operations import SourceDiscoveryRequest
from smart_search.execution_primitives import ExecutionAttemptStatus, project_attempts_dict
from smart_search.providers.base import (
    ProviderError,
    ProviderResult,
    classify_provider_exception,
    coerce_provider_result,
)
from smart_search.service_support import _fallback_used
from smart_search.v2_contract import serialize_result


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
        assert message == f"HTTP {status_code}"
        assert "upstream" not in message
        assert retryable is expected_retryable

    error_type, _, retryable = classify_provider_exception(httpx.ReadTimeout("slow", request=request))
    assert error_type == "timeout"
    assert retryable is True


@pytest.mark.asyncio
async def test_executor_consumes_structured_provider_error_and_falls_back(monkeypatch):
    """A structured provider error (auth_error) is not treated as success: the
    typed executor records the classified error attempt, falls back within the
    same capability, and the legacy projection still reports fallback_used."""
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [
            {
                "provider": provider,
                "configured": True,
                "enabled": True,
                "eligible": True,
                "reason": "ready",
            }
            for provider in ("first", "second")
        ],
    )
    calls: list[str] = []

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        if provider == "first":
            outcome.update({"error_type": "auth_error", "error": "invalid credentials", "retryable": False})
            return []
        return [{"url": "https://example.test/ok", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="main_search",
            input_value="provider contract",
            run=run,
            result_count=len,
        )
    )

    assert calls == ["first", "second"]
    assert [attempt.status for attempt in execution.attempts] == [
        ExecutionAttemptStatus.ERROR,
        ExecutionAttemptStatus.OK,
    ]
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.type == "auth_error"
    assert execution.provider == "second"

    legacy_attempts = project_attempts_dict(execution.attempts)
    assert legacy_attempts[0]["status"] == "error"
    assert legacy_attempts[0]["error_type"] == "auth_error"
    assert _fallback_used(legacy_attempts) is True


# ---------------------------------------------------------------------------
# Upstream response-body containment (P0: no body bytes in public errors)
# ---------------------------------------------------------------------------


def _malicious_body() -> str:
    return (
        '{"error": "echo api_key=sk-leaked-123 request fragment '
        '\\"Bearer sk-leaked-123\\""}'
    )


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example.test/v1/search?q=request-fragment")
    response = httpx.Response(status_code, text=_malicious_body(), request=request)
    return httpx.HTTPStatusError("upstream", request=request, response=response)


def test_classified_provider_error_never_embeds_response_body():
    """An upstream body echoing credentials or request fragments must never
    cross the classified provider-error boundary: the message is status-only
    and the ProviderError/ProviderResult wire stays body-free."""
    for status_code in (400, 401, 403, 429, 503):
        error_type, message, retryable = classify_provider_exception(_http_error(status_code))

        assert message == f"HTTP {status_code}"
        assert "sk-leaked-123" not in message
        assert "api_key=" not in message
        assert "request-fragment" not in message

        provider_error = ProviderError(
            error_type,
            message,
            provider="xai-responses",
            capability="main_search",
            retryable=retryable,
        )
        rendered_wires = (
            str(provider_error.to_result()),
            str(coerce_provider_result(provider_error, provider="xai-responses", capability="main_search")),
        )
        for rendered in rendered_wires:
            assert "sk-leaked-123" not in rendered
            assert "api_key=" not in rendered


@pytest.mark.asyncio
async def test_executor_attempt_errors_never_embed_response_body(monkeypatch):
    """A provider exception whose body echoes credentials must produce an
    attempt error carrying status only, so the legacy attempt projection used
    by Workflow/synthesis boundaries stays body-free."""
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [
            {
                "provider": "xai-responses",
                "configured": True,
                "enabled": True,
                "eligible": True,
                "reason": "ready",
            }
        ],
    )
    error = _http_error(429)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        raise error

    execution = await execute_capability(
        CapabilityOperation(
            capability="main_search",
            input_value="query",
            run=run,
            result_count=len,
        )
    )

    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.type == "rate_limited"
    assert execution.attempts[0].error.message == "HTTP 429"
    rendered = json.dumps(project_attempts_dict(execution.attempts))
    assert "sk-leaked-123" not in rendered
    assert "api_key=" not in rendered


@pytest.mark.asyncio
async def test_v2_json_contains_no_provider_body_bytes(monkeypatch):
    """A full V2 source_discovery flow fed an error body echoing credentials
    must serialize public V2 JSON without any of those body bytes while
    preserving the classified error category."""
    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [
            {
                "provider": "tavily",
                "configured": True,
                "enabled": True,
                "eligible": True,
                "reason": "ready",
            }
        ],
    )
    error = _http_error(429)

    async def fake_web(query, count=5, providers="auto", fallback="auto"):
        async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
            raise error

        return await execute_capability(
            CapabilityOperation(
                capability="web_search",
                input_value=query,
                run=run,
                empty_value=lambda _provider: [],
                is_success=lambda value: isinstance(value, list) and bool(value),
                result_count=len,
            ),
            provider_filter=providers,
            fallback=fallback,
        )

    monkeypatch.setattr(evidence_operations, "_execute_web_search", fake_web)
    envelope = await canonical_operations.source_discovery(SourceDiscoveryRequest("query"))
    payload = serialize_result(envelope)
    rendered = json.dumps(payload)

    assert "sk-leaked-123" not in rendered
    assert "api_key=" not in rendered
    assert "request-fragment" not in rendered
    # Error category and retryability semantics survive the containment change.
    assert payload["attempts"][0]["error_code"] == "RATE_LIMITED"
    assert payload["error"]["code"] == "RATE_LIMITED"
    assert payload["error"]["retryable"] is True
