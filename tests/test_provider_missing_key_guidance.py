"""Regression guard: provider missing-key errors name only canonical config commands.

The removed ``smart-search setup`` spelling returns ``INVALID_ARGUMENT``; every
current missing-key recovery message must recommend only the canonical
``smart-search config set <KEY> <value>`` command for the relevant provider
key. These tests force each target key-missing branch with isolated local
configuration (keys unset via the autouse config fixture) and fail if any
provider/HTTP transport is constructed or called.
"""

import pytest

from smart_search import provider_fetch_commands, provider_search_commands


class _FailIfCalledProvider:
    """Provider spy: constructing or calling it fails the test."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("provider transport must not run for a missing-key branch")

    async def search(self, *args, **kwargs):
        raise AssertionError("provider transport must not run for a missing-key branch")

    async def library(self, *args, **kwargs):
        raise AssertionError("provider transport must not run for a missing-key branch")


class _FailIfCalledAsyncClient:
    """httpx.AsyncClient spy: constructing it or posting fails the test."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("HTTP client must not run for a missing-key branch")

    async def post(self, *args, **kwargs):
        raise AssertionError("HTTP transport must not run for a missing-key branch")


def _preflight_ok(capability: str, provider: str = "") -> dict:
    """Stand-in for the capability gate so the shadowed key check is reachable."""
    return {"ok": True, "metadata": {"command": capability, "provider": provider}}


def _assert_canonical_guidance(result: dict, key: str) -> None:
    assert result["ok"] is False
    assert result["error_type"] == "config_error"
    error = result["error"]
    assert f"smart-search config set {key} <key>" in error
    assert "setup" not in error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "key", "env_key", "provider_attr", "call"),
    [
        ("exa_search", "EXA_API_KEY", "EXA_API_KEY", "ExaSearchProvider", "search"),
        ("zhipu_search", "ZHIPU_API_KEY", "ZHIPU_API_KEY", "ZhipuWebSearchProvider", "search"),
        ("context7_library", "CONTEXT7_API_KEY", "CONTEXT7_API_KEY", "Context7Provider", "library"),
    ],
)
async def test_search_command_missing_key_uses_canonical_config_command(
    monkeypatch, command, key, env_key, provider_attr, call
):
    # Isolated local configuration: the key is unset.
    monkeypatch.delenv(env_key, raising=False)
    # The key-missing branch is preflight-shadowed in production; bypass the
    # gate locally so the latent contract is pinned without network I/O.
    monkeypatch.setattr(provider_search_commands, "_capability_preflight", _preflight_ok)
    monkeypatch.setattr(provider_search_commands, provider_attr, _FailIfCalledProvider)

    from smart_search.config import config

    assert not getattr(config, key.lower()), "test requires an unset key"

    func = getattr(provider_search_commands, command)
    if command == "context7_library":
        result = await func("library-name")
    else:
        result = await func("query")

    _assert_canonical_guidance(result, key)


@pytest.mark.asyncio
async def test_tavily_map_missing_key_uses_canonical_config_command(monkeypatch):
    # Isolated local configuration: TAVILY_API_KEY is unset, so the provider is
    # not eligible and the directly callable missing-key branch runs locally.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    # Fail if any HTTP client or transport is constructed.
    monkeypatch.setattr(provider_fetch_commands.httpx, "AsyncClient", _FailIfCalledAsyncClient)

    from smart_search.config import config

    assert not config.tavily_api_key, "test requires an unset key"

    result = await provider_fetch_commands.call_tavily_map("https://example.com")

    _assert_canonical_guidance(result, "TAVILY_API_KEY")
    assert result["retryable"] is False
