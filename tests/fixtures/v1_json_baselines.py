"""Helpers for Phase 0 v1 JSON / exit contract freezes.

Fixtures assert stable protocol fields and deliberately ignore dynamic values
such as elapsed_ms, request_id, and free-form duration text.
"""

from __future__ import annotations

import json
from typing import Any


V1_ENVELOPE_REQUIRED_KEYS = (
    "schema_version",
    "ok",
    "command",
    "data",
    "meta",
)

V1_META_REQUIRED_KEYS = (
    "provider",
    "attempted_providers",
    "duration_ms",
    "warnings",
)

# Stable fields that success/empty/degraded/failure fixtures must preserve.
CAPABILITIES_SUCCESS_KEYS = (
    "ok",
    "commands",
    "capabilities",
    "profiles",
    "minimum_profiles",
    "active_minimum_profile",
    "command_capabilities",
    "output_formats",
)

SEARCH_CORE_KEYS = (
    "ok",
    "query",
    "content",
    "sources",
    "primary_sources",
    "extra_sources",
    "provider_attempts",
    "providers_used",
    "fallback_used",
    "routing_decision",
)

FETCH_CORE_KEYS = (
    "ok",
    "url",
    "content",
    "provider",
    "provider_attempts",
    "fallback_used",
)

MAP_CORE_KEYS = (
    "ok",
    "url",
)

DOCTOR_CORE_KEYS = (
    "ok",
    "error_type",
)


def assert_single_json_document(stdout: str) -> dict[str, Any]:
    """Stdout must contain exactly one JSON document for CLI JSON mode."""
    text = stdout.strip()
    assert text, "expected non-empty JSON stdout"
    # Reject concatenated documents by requiring one full decode with no leftover.
    decoder = json.JSONDecoder()
    payload, index = decoder.raw_decode(text)
    assert text[index:].strip() == "", "stdout must contain a single JSON document"
    assert isinstance(payload, dict)
    return payload


def assert_v1_envelope(payload: dict[str, Any], *, command: str, ok: bool | None = None) -> None:
    for key in V1_ENVELOPE_REQUIRED_KEYS:
        assert key in payload, f"missing envelope key: {key}"
    assert payload["schema_version"] == "1"
    assert payload["command"] == command
    assert isinstance(payload["data"], dict)
    assert isinstance(payload["meta"], dict)
    for key in V1_META_REQUIRED_KEYS:
        assert key in payload["meta"], f"missing meta key: {key}"
    if ok is not None:
        assert payload["ok"] is ok
        assert payload["data"].get("ok") is ok


def assert_structured_error(payload: dict[str, Any]) -> None:
    assert payload["ok"] is False
    assert "error" in payload
    assert "error_code" in payload
    assert "error_detail" in payload
    assert isinstance(payload["error_detail"], dict)
    assert payload["error_detail"]["code"] == payload["error_code"]
    assert "message" in payload["error_detail"]
    assert "retryable" in payload["error_detail"]
    assert isinstance(payload["data"].get("error"), dict)
    assert payload["data"]["error"]["code"] == payload["error_code"]


def assert_no_secret_leak(payload: dict[str, Any], secrets: list[str]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    for secret in secrets:
        if not secret:
            continue
        assert secret not in rendered, f"secret leaked into JSON: {secret}"


def assert_has_keys(payload: dict[str, Any], keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if key not in payload]
    assert not missing, f"missing stable keys: {missing}"
