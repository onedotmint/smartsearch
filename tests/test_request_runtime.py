import asyncio
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest
import httpx

from smart_search.config import ConfigSnapshot
from smart_search import service
from smart_search import search_service
from smart_search.runtime_cache import (
    RequestBudget,
    RequestContext,
    RequestBudgetExceeded,
    RuntimeMetrics,
    bounded_retry_delay,
    request_client,
)


def _snapshot() -> ConfigSnapshot:
    empty = MappingProxyType({})
    return ConfigSnapshot(Path("/tmp/smart-search-test-config.json"), "test", empty, empty, empty)


def test_request_budget_uses_fake_clock_and_clamps_retry_delay():
    now = [100.0]
    budget = RequestBudget(
        deadline=105.0,
        max_provider_attempts=1,
        max_retry_attempts=1,
        max_fetches=1,
        clock=lambda: now[0],
    )

    assert budget.reserve_provider_attempt() is True
    assert budget.reserve_provider_attempt() is False
    assert budget.exhausted_reason == "provider_attempts"
    assert budget.clamp_retry_delay(20.0) == 5.0

    now[0] = 105.0
    assert budget.reserve_retry() is False
    assert budget.exhausted_reason == "provider_attempts"


@pytest.mark.asyncio
async def test_request_context_reuses_client_and_closes_it():
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False

        async def aclose(self):
            self.closed = True

    clients = []

    def factory(**kwargs):
        client = FakeClient(**kwargs)
        clients.append(client)
        return client

    context = await RequestContext.create(
        command="search",
        config_snapshot=_snapshot(),
        timeout_seconds=5,
        metrics=RuntimeMetrics(),
        client_factory=factory,
    )

    async with request_client(context) as first:
        async with request_client(context) as second:
            assert first is second
    await context.aclose()

    assert len(clients) == 1
    assert clients[0].closed is True

    other_loop = asyncio.new_event_loop()
    with pytest.raises(RuntimeError, match="event loops"):
        replace(context, loop=other_loop).ensure_loop()
    other_loop.close()


@pytest.mark.asyncio
async def test_request_context_honors_boolean_ssl_snapshot():
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def aclose(self):
            return None

    values = MappingProxyType({"SSL_VERIFY": False})
    snapshot = ConfigSnapshot(Path("/tmp/smart-search-test-config.json"), "test", values, values, MappingProxyType({}))
    clients = []

    def factory(**kwargs):
        client = FakeClient(**kwargs)
        clients.append(client)
        return client

    context = await RequestContext.create(
        command="fetch",
        config_snapshot=snapshot,
        client_factory=factory,
    )
    await context.aclose()

    assert clients[0].kwargs["verify"] is False


@pytest.mark.asyncio
async def test_request_context_closes_client_when_body_raises():
    class FakeClient:
        closed = False

        async def aclose(self):
            self.closed = True

    client = FakeClient()
    context = await RequestContext.create(
        command="fetch",
        config_snapshot=_snapshot(),
        client_factory=lambda **kwargs: client,
    )

    with pytest.raises(ValueError):
        async with context:
            raise ValueError("failure")

    assert client.closed is True


@pytest.mark.asyncio
async def test_request_context_closes_client_when_cancelled():
    class FakeClient:
        closed = False

        async def aclose(self):
            self.closed = True

    client = FakeClient()
    context = await RequestContext.create(
        command="research",
        config_snapshot=_snapshot(),
        client_factory=lambda **kwargs: client,
    )

    async def run():
        async with context:
            await asyncio.sleep(60)

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.closed is True


def test_budget_exhaustion_exception_is_structured():
    error = RequestBudgetExceeded("deadline")
    assert error.error_type == "budget_exhausted"
    assert str(error) == "deadline"
    assert bounded_retry_delay(10.0, None) == 10.0


@pytest.mark.asyncio
async def test_fetch_uses_one_shared_client_and_closes_it(monkeypatch):
    class FakeClient:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            self.calls = 0
            self.instances.append(self)

        async def post(self, endpoint, headers, json, **kwargs):
            self.calls += 1
            return httpx.Response(
                200,
                json={"results": [{"raw_content": "page content"}]},
                request=httpx.Request("POST", endpoint),
            )

        async def aclose(self):
            self.closed = True

    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test-key")
    monkeypatch.setattr("smart_search.runtime_cache.httpx.AsyncClient", FakeClient)

    result = await service.fetch("https://example.com/page")

    assert result["ok"] is True
    assert len(FakeClient.instances) == 1
    assert FakeClient.instances[0].calls == 1
    assert FakeClient.instances[0].closed is True


@pytest.mark.asyncio
async def test_search_skips_provider_when_deadline_is_exhausted(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "openai-test-key")
    calls = 0

    async def should_not_call(self, query, platform="", ctx=None):
        nonlocal calls
        calls += 1
        return "unexpected"

    monkeypatch.setattr(search_service.OpenAICompatibleSearchProvider, "search", should_not_call)

    result = await service.search("budget test", timeout_seconds=0)

    assert calls == 0
    assert result["ok"] is False
    assert result["error_type"] == "budget_exhausted"
    assert result["budget_exhausted"] is True
    assert any(attempt["error_type"] == "budget_exhausted" for attempt in result["provider_attempts"])
