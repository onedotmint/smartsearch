import json

import pytest

from smart_search import (
    control_executors,
    provider_diagnostics,
    provider_search_commands,
)


def test_control_executors_use_provider_diagnostic_owners():
    assert control_executors._test_exa_connection is provider_diagnostics._test_exa_connection
    assert control_executors._test_tavily_connection is provider_diagnostics._test_tavily_connection
    assert control_executors._test_jina_connection is provider_diagnostics._test_jina_connection


@pytest.mark.asyncio
async def test_jina_connection_probe_anonymous_and_keyed_statuses(monkeypatch):
    """Jina diagnostics normalize anonymous success to ``anonymous_ready``,
    keyed success to ``ready``, and never leak the key or the configured
    ``JINA_RESPOND_WITH`` value."""
    calls: list[str] = []

    async def fake_jina_fetch(url):
        calls.append(url)
        return {"ok": True, "provider": "jina", "content": "Title: Example"}

    monkeypatch.setattr(provider_diagnostics, "jina_fetch", fake_jina_fetch)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_RESPOND_WITH", raising=False)

    anonymous = await provider_diagnostics._test_jina_connection()
    assert anonymous["status"] == "anonymous_ready"
    assert calls == ["https://example.com"]

    monkeypatch.setenv("JINA_API_KEY", "jina-probe-secret")
    keyed = await provider_diagnostics._test_jina_connection()
    assert keyed["status"] == "ready"
    assert "jina-probe-secret" not in json.dumps(keyed)

    monkeypatch.setenv("JINA_RESPOND_WITH", "readerlm-v2")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    calls_before_blocked = len(calls)
    blocked = await provider_diagnostics._test_jina_connection()
    assert blocked["status"] == "config_error"
    assert len(calls) == calls_before_blocked  # no network for the blocked probe
    assert "readerlm-v2" not in json.dumps(blocked)


@pytest.mark.asyncio
async def test_jina_connection_probe_preserves_classified_failure(monkeypatch):
    async def failing_jina_fetch(url):
        return {"ok": False, "error_type": "rate_limited", "error": "too many requests"}

    monkeypatch.setattr(provider_diagnostics, "jina_fetch", failing_jina_fetch)
    monkeypatch.setenv("JINA_API_KEY", "jina-probe-secret")
    result = await provider_diagnostics._test_jina_connection()
    assert result["status"] == "rate_limited"
    assert "too many requests" in result["message"]


@pytest.mark.asyncio
async def test_exa_command_remains_uncached(monkeypatch):
    calls = []

    class FakeExaProvider:
        def __init__(self, *args):
            pass

        async def search(self, **kwargs):
            calls.append(kwargs)
            return json.dumps({"ok": True, "results": [{"url": "https://example.com"}]})

    monkeypatch.setenv("EXA_API_KEY", "exa-test-secret")
    monkeypatch.setattr(provider_search_commands, "ExaSearchProvider", FakeExaProvider)

    first = await provider_search_commands.exa_search("query")
    second = await provider_search_commands.exa_search("query")

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(calls) == 2
    assert "cache_hit" not in first
    assert "cache_hit" not in second
