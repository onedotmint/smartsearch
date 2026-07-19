import json

import pytest

from smart_search import (
    operations_service,
    provider_commands,
    provider_diagnostics,
    provider_fetch_commands,
    provider_mcp_commands,
    provider_search_commands,
    provider_vertical_commands,
)


def test_provider_command_facade_reexports_owning_modules():
    assert provider_commands.exa_search is provider_search_commands.exa_search
    assert provider_commands.fetch is provider_fetch_commands.fetch
    assert provider_commands.zhipu_mcp_search is provider_mcp_commands.zhipu_mcp_search
    assert provider_commands.anysearch_search is provider_vertical_commands.anysearch_search


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
