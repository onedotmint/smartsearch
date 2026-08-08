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

from smart_search import capability_executor
from smart_search.capability_executor import CapabilityOperation, execute_capability
from smart_search.execution_primitives import ExecutionAttemptStatus, project_attempts_dict
from smart_search.providers.base import (
    ProviderResult,
    classify_provider_exception,
    coerce_provider_result,
)
from smart_search.service_support import _fallback_used


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
