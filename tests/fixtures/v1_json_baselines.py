"""Helpers for the stable v1 JSON and exit contract."""
from __future__ import annotations

import json
from typing import Any


V1_ENVELOPE_REQUIRED_KEYS = (
    "version",
    "operation",
    "status",
    "data",
    "attempts",
    "warnings",
    "error",
)
V1_OPERATIONS = ("setup", "search", "read", "research")
V1_STATUSES = ("complete", "degraded", "failed")


def assert_single_json_document(stdout: str) -> dict[str, Any]:
    """Stdout must contain exactly one JSON document for CLI JSON mode."""
    text = stdout.strip()
    assert text, "expected non-empty JSON stdout"
    decoder = json.JSONDecoder()
    payload, index = decoder.raw_decode(text)
    assert text[index:].strip() == "", "stdout must contain a single JSON document"
    assert isinstance(payload, dict)
    return payload


def assert_v1_envelope(payload: dict[str, Any], *, operation: str, status: str | None = None) -> None:
    for key in V1_ENVELOPE_REQUIRED_KEYS:
        assert key in payload, f"missing envelope key: {key}"
    assert payload["version"] == 1
    assert payload["operation"] == operation
    assert payload["operation"] in V1_OPERATIONS
    assert payload["status"] in V1_STATUSES
    if status is not None:
        assert payload["status"] == status
    assert isinstance(payload["data"], dict)
    assert isinstance(payload["attempts"], list)
    assert isinstance(payload["warnings"], list)
    assert payload["error"] is None or isinstance(payload["error"], dict)


def assert_no_secret_leak(payload: dict[str, Any], secrets: list[str]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    for secret in secrets:
        if secret:
            assert secret not in rendered, f"secret leaked into JSON: {secret}"
