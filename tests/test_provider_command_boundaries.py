import json

import pytest

from smart_search import (
    operations_service,
    provider_diagnostics,
    provider_search_commands,
)


def test_operations_service_uses_provider_diagnostic_owners():
    assert operations_service._test_exa_connection is provider_diagnostics._test_exa_connection
    assert operations_service._test_tavily_connection is provider_diagnostics._test_tavily_connection
    assert operations_service._test_jina_connection is provider_diagnostics._test_jina_connection


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
