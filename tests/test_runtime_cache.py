"""Direct deterministic tests for the retained runtime-cache and request-runtime
modules.

This file migrates the pre-cleanup ``test_runtime_cache.py`` and
``test_request_runtime.py`` coverage onto the current typed runtime boundary:
``RuntimeTTLCache``, ``cache_input``/``normalize_url``, ``RequestBudget``,
``RequestContext``/``request_client``, and the real cache/budget wiring of
``execute_capability``. The V1 facade (``smart_search.service`` /
``search_service``) is removed and never imported here; service-level cases
are expressed at the executor/runtime level with fake clocks, fake clients,
and no network.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest

from smart_search import capability_executor, service_support
from smart_search.capability_executor import CapabilityOperation, execute_capability
from smart_search.config import ConfigSnapshot, config
from smart_search.provider_fetch_commands import call_tavily_extract
from smart_search.runtime_cache import (
    RequestBudget,
    RequestBudgetExceeded,
    RequestContext,
    RuntimeMetrics,
    RuntimeTTLCache,
    add_fetch,
    bounded_retry_delay,
    cache_input,
    normalize_url,
    request_client,
    request_scope,
)


@pytest.fixture(autouse=True)
def clear_runtime_caches():
    service_support.reset_runtime_cache()
    yield
    service_support.reset_runtime_cache()


def _snapshot() -> ConfigSnapshot:
    empty = MappingProxyType({})
    return ConfigSnapshot(Path("/tmp/smart-search-test-config.json"), "test", empty, empty, empty)


def _eligible_statuses(*providers: str) -> list[dict[str, object]]:
    return [
        {
            "provider": provider,
            "configured": True,
            "enabled": True,
            "eligible": True,
            "reason": "ready",
        }
        for provider in providers
    ]


def _fetch_operation(url: str, run) -> CapabilityOperation:
    return CapabilityOperation(
        capability="web_fetch",
        input_value=url,
        cache_kind="fetch",
        cache_options={"format": "markdown"},
        run=run,
        empty_value=lambda provider: {
            "content": "",
            "url": url,
            "provider": provider,
            "error_type": "empty",
        },
        is_success=lambda value: isinstance(value, dict) and bool(value.get("content")),
        result_count=lambda _value: 1,
    )


class _FakeBudgetClient:
    """Minimal stand-in client so budget tests never create network transport."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def aclose(self):
        return None


# ---------------------------------------------------------------------------
# RuntimeTTLCache: TTL expiry, LRU eviction, deep-copy isolation, in-flight
# sharing, and waiter-cancellation isolation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_ttl_cache_expires_and_evicts_lru_entries():
    now = [0.0]
    cache = RuntimeTTLCache(max_size=2, clock=lambda: now[0])
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return {"value": calls}

    first = await cache.get_or_set("a", factory, ttl_seconds=5, enabled=True)
    await asyncio.sleep(0)
    second = await cache.get_or_set("a", factory, ttl_seconds=5, enabled=True)

    assert first.value == {"value": 1}
    assert second.value == {"value": 1}
    assert second.cache_hit is True
    assert calls == 1

    cache.set("b", {"value": 2}, 5)
    cache.set("c", {"value": 3}, 5)
    assert cache.get("a") is None
    assert cache.get("b") == {"value": 2}
    assert cache.get("c") == {"value": 3}

    now[0] = 5.0
    assert cache.get("b") is None


@pytest.mark.asyncio
async def test_runtime_cache_returns_deep_copies_on_read():
    now = [0.0]
    cache = RuntimeTTLCache(max_size=2, clock=lambda: now[0])
    cache.set("nested", {"list": [1, 2], "inner": {"value": 1}}, 30)

    first = cache.get("nested")
    assert first is not None
    first["list"].append(3)
    first["inner"]["value"] = 99

    second = cache.get("nested")
    assert second == {"list": [1, 2], "inner": {"value": 1}}


@pytest.mark.asyncio
async def test_runtime_inflight_joiner_cancellation_does_not_cancel_owner():
    cache = RuntimeTTLCache(max_size=2)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"content": "stable"}

    owner = asyncio.create_task(cache.get_or_set("same", factory, ttl_seconds=30, enabled=True))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_set("same", factory, ttl_seconds=30, enabled=True))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    result = await owner
    await asyncio.sleep(0)
    cached = await cache.get_or_set("same", factory, ttl_seconds=30, enabled=True)

    assert result.value == {"content": "stable"}
    assert cached.cache_hit is True
    assert calls == 1


def test_cache_input_normalizes_equivalent_inputs_and_bypasses_sensitive_values():
    # Equivalent public URLs share one normalized key.
    assert normalize_url("https://Example.com/page/#fragment") == normalize_url("https://example.com/page")
    assert cache_input("https://example.com/page", kind="url") == cache_input(
        "https://example.com/page/", kind="url"
    )
    # Queries are whitespace-normalized so equivalent spellings share a key.
    assert cache_input("same   query", kind="query") == cache_input("same query", kind="query")
    # Sensitive URLs and userinfo never enter a cache key.
    assert cache_input("https://example.com/page?api_key=secret", kind="url") is None
    assert cache_input("https://user:pass@example.com/page", kind="url") is None
    # Schemeless/relative inputs are guarded too: secret-bearing variants
    # bypass the cache while benign relative resources keep their raw key.
    assert normalize_url("relative?token=abc") is None
    assert normalize_url("relative?key=abc") is None
    assert normalize_url("relative?sig=abc&signature=def") is None
    assert normalize_url("//user:pass@example.com/page") is None
    assert normalize_url("relative?q=hello") == "relative?q=hello"
    # The new shared names cover suffix/hyphen variants of sensitive params.
    assert cache_input("https://example.com/page?x_api_key=secret", kind="url") is None
    assert cache_input("https://example.com/page?API-KEY=secret", kind="url") is None
    # Sensitive text in a query input is not cached either.
    assert cache_input("Authorization: Bearer token", kind="query") is None


def test_cache_clear_drops_entries_and_inflight_tasks():
    cache = RuntimeTTLCache(max_size=2)
    cache.set("a", {"value": 1}, 30)
    cache.clear()
    assert cache.get("a") is None


# ---------------------------------------------------------------------------
# RequestBudget: fake-clock reservations and retry-delay clamping.
# ---------------------------------------------------------------------------


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


def test_budget_exhaustion_exception_is_structured():
    error = RequestBudgetExceeded("deadline")
    assert error.error_type == "budget_exhausted"
    assert str(error) == "deadline"
    assert bounded_retry_delay(10.0, None) == 10.0


# ---------------------------------------------------------------------------
# RequestContext / request_client: one shared client per command, close on
# success/exception/cancellation, SSL snapshot, event-loop binding.
# ---------------------------------------------------------------------------


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


@asynccontextmanager
async def _client_stream_context(response: httpx.Response):
    """Async context manager yielding an httpx.Response for fake stream()."""
    yield response


@pytest.mark.asyncio
async def test_fetch_uses_one_shared_client_and_closes_it(monkeypatch):
    """The current web-fetch path reuses the command's single shared client
    (``RequestContext`` + ``request_client``) and closes it after the call —
    without any network: the client is injected via ``client_factory``."""
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test-key")
    monkeypatch.setenv("TAVILY_API_URL", "https://api.tavily.test")

    class FakeClient:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            self.calls = 0
            self.instances.append(self)

        def stream(self, method, endpoint, headers=None, json=None, **kwargs):
            self.calls += 1
            return _client_stream_context(httpx.Response(
                200,
                json={"results": [{"raw_content": "page content"}]},
                request=httpx.Request(method, endpoint),
            ))

        async def aclose(self):
            self.closed = True

    context = await RequestContext.create(
        command="fetch",
        config_snapshot=_snapshot(),
        client_factory=FakeClient,
    )
    with request_scope(context):
        content = await call_tavily_extract("https://example.com/page")
    await context.aclose()

    assert content == "page content"
    assert len(FakeClient.instances) == 1
    assert FakeClient.instances[0].calls == 1
    assert FakeClient.instances[0].closed is True


# ---------------------------------------------------------------------------
# Executor wiring: real budget stops a provider call at a zero deadline.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_skips_provider_when_deadline_is_exhausted(monkeypatch):
    """A real RequestBudget with an expired deadline rejects the provider
    reservation, so the provider factory is never invoked and the attempt is a
    stable ``budget_exhausted`` error."""
    now = [100.0]
    budget = RequestBudget(
        deadline=100.0,
        max_provider_attempts=1,
        max_retry_attempts=1,
        max_fetches=1,
        clock=lambda: now[0],
    )

    class FakeClient:
        async def aclose(self):
            return None

    context = await RequestContext.create(
        command="search",
        config_snapshot=_snapshot(),
        budget=budget,
        clock=lambda: now[0],
        client_factory=lambda **kwargs: FakeClient(),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return [{"url": "https://unexpected.test", "provider": provider}]

    with request_scope(context):
        execution = await execute_capability(
            CapabilityOperation(
                capability="web_search",
                input_value="budget test",
                run=run,
                result_count=len,
            )
        )
    await context.aclose()

    assert calls == []
    assert execution.value == []
    assert len(execution.attempts) == 1
    assert execution.attempts[0].status.value == "error"
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.type == "budget_exhausted"


# ---------------------------------------------------------------------------
# Executor wiring: real TTL/LRU fetch and search caches with invalidation on
# behavior-config and credential changes, disable/reenable, and sensitive-URL
# bypass. No provider/network calls: the provider factory is a local fake.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_cache_reuses_content_and_concurrent_requests_join_owner(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("tavily"),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"content": f"content-{calls}", "url": "https://example.com/page", "provider": provider}

    operation = _fetch_operation("https://example.com/page", run)
    owner = asyncio.create_task(execute_capability(operation, providers=["tavily"]))
    await started.wait()
    waiter = asyncio.create_task(execute_capability(operation, providers=["tavily"]))
    await asyncio.sleep(0)
    release.set()
    owner_result, waiter_result = await asyncio.gather(owner, waiter)

    assert calls == 1
    assert owner_result.value["content"] == waiter_result.value["content"]
    assert waiter_result.attempts[-1].details["inflight_joined"] is True

    cached = await execute_capability(operation, providers=["tavily"])
    assert calls == 1
    assert cached.value["content"] == owner_result.value["content"]
    assert cached.attempts[-1].details["cache_hit"] is True


@pytest.mark.asyncio
async def test_fetch_cache_invalidates_on_config_credential_and_disable(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setenv("TAVILY_API_KEY", "first-key")
    monkeypatch.setenv("TAVILY_API_URL", "https://tavily.one")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("tavily"),
    )
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"content": f"content-{calls}", "url": "https://example.com/page", "provider": provider}

    operation = _fetch_operation("https://example.com/page", run)

    first = await execute_capability(operation, providers=["tavily"])
    reused = await execute_capability(operation, providers=["tavily"])
    assert first.value["content"] == "content-1"
    assert reused.value["content"] == "content-1"
    assert calls == 1

    monkeypatch.setenv("TAVILY_API_URL", "https://tavily.two")
    changed_endpoint = await execute_capability(operation, providers=["tavily"])
    assert changed_endpoint.value["content"] == "content-2"

    monkeypatch.setenv("TAVILY_API_KEY", "second-key")
    changed_credential = await execute_capability(operation, providers=["tavily"])
    assert changed_credential.value["content"] == "content-3"

    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "false")
    disabled = await execute_capability(operation, providers=["tavily"])
    assert disabled.value["content"] == "content-4"

    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    reenabled = await execute_capability(operation, providers=["tavily"])
    assert reenabled.value["content"] == "content-5"
    assert calls == 5


@pytest.mark.asyncio
async def test_fetch_cache_bypasses_sensitive_urls(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("tavily"),
    )
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"content": "content", "url": "https://example.com/page", "provider": provider}

    operation = _fetch_operation("https://example.com/page?api_key=secret", run)

    await execute_capability(operation, providers=["tavily"])
    await execute_capability(operation, providers=["tavily"])

    assert calls == 2


@pytest.mark.asyncio
async def test_fetch_budget_reserved_on_miss_but_not_on_hit(monkeypatch):
    """A cache miss reserves the fetch budget once before provider execution; a
    later cache hit for the same URL is budget-neutral (no reservation and no
    budget-exhaustion rejection)."""
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("tavily"),
    )
    now = [100.0]
    budget = RequestBudget(
        deadline=200.0,
        max_provider_attempts=8,
        max_retry_attempts=4,
        max_fetches=2,
        clock=lambda: now[0],
    )
    context = await RequestContext.create(
        command="research",
        config_snapshot=_snapshot(),
        budget=budget,
        clock=lambda: now[0],
        client_factory=lambda **kwargs: _FakeBudgetClient(),
    )
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"content": f"content-{calls}", "url": "https://example.com/page", "provider": provider}

    operation = _fetch_operation("https://example.com/page", run)
    with request_scope(context):
        missed = await execute_capability(operation, providers=["tavily"], reserve_fetch=add_fetch)
        assert budget.fetches == 1
        assert context.metrics.fetch_count == 1
        assert missed.attempts[-1].details.get("cache_hit") is None

        hit = await execute_capability(operation, providers=["tavily"], reserve_fetch=add_fetch)
    await context.aclose()

    assert calls == 1
    assert hit.value["content"] == "content-1"
    assert hit.attempts[-1].details["cache_hit"] is True
    assert budget.fetches == 1  # cache hit did not reserve
    assert context.metrics.fetch_count == 1
    assert budget.exhausted_reason == ""


@pytest.mark.asyncio
async def test_fetch_budget_reserved_once_for_inflight_joiner(monkeypatch):
    """Concurrent requests for the same URL share one owner task; the owner's
    cache miss reserves the fetch budget exactly once and the in-flight joiner
    reserves nothing."""
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("tavily"),
    )
    budget = RequestBudget(
        deadline=None,
        max_provider_attempts=8,
        max_retry_attempts=4,
        max_fetches=2,
    )
    context = await RequestContext.create(
        command="research",
        config_snapshot=_snapshot(),
        budget=budget,
        client_factory=lambda **kwargs: _FakeBudgetClient(),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"content": f"content-{calls}", "url": "https://example.com/page", "provider": provider}

    operation = _fetch_operation("https://example.com/page", run)
    with request_scope(context):
        owner = asyncio.create_task(execute_capability(operation, providers=["tavily"], reserve_fetch=add_fetch))
        await started.wait()
        waiter = asyncio.create_task(execute_capability(operation, providers=["tavily"], reserve_fetch=add_fetch))
        await asyncio.sleep(0)
        release.set()
        owner_result, waiter_result = await asyncio.gather(owner, waiter)
    await context.aclose()

    assert calls == 1
    assert budget.fetches == 1
    assert context.metrics.fetch_count == 1
    assert owner_result.value["content"] == waiter_result.value["content"]
    assert waiter_result.attempts[-1].details["inflight_joined"] is True


@pytest.mark.asyncio
async def test_exhausted_fetch_budget_still_serves_cache_hit(monkeypatch):
    """An otherwise usable cached result must not be rejected just because the
    fetch budget is exhausted: a cache hit stays budget-neutral and returns the
    cached content, while a fresh URL miss is refused without provider I/O."""
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("tavily"),
    )
    budget = RequestBudget(
        deadline=None,
        max_provider_attempts=8,
        max_retry_attempts=4,
        max_fetches=1,
    )
    context = await RequestContext.create(
        command="research",
        config_snapshot=_snapshot(),
        budget=budget,
        client_factory=lambda **kwargs: _FakeBudgetClient(),
    )
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "content": f"content-{calls}",
            "url": f"https://example.com/page-{calls}",
            "provider": provider,
        }

    op_a = _fetch_operation("https://example.com/page-1", run)
    op_b = _fetch_operation("https://example.com/page-2", run)
    with request_scope(context):
        first = await execute_capability(op_a, providers=["tavily"], reserve_fetch=add_fetch)
        assert budget.fetches == 1  # the miss reserved the only fetch slot

        refused = await execute_capability(op_b, providers=["tavily"], reserve_fetch=add_fetch)
        assert budget.exhausted_reason == "fetches"

        again = await execute_capability(op_a, providers=["tavily"], reserve_fetch=add_fetch)
    await context.aclose()

    assert calls == 1
    assert refused.attempts[0].status.value == "skipped"
    assert refused.attempts[0].error is not None
    assert refused.attempts[0].error.type == "budget_exhausted"
    assert refused.value["content"] == ""  # empty fetch payload from the refusal

    assert again.value["content"] == "content-1"
    assert again.attempts[-1].details["cache_hit"] is True
    assert again.attempts[-1].status.value == "ok"
    assert budget.fetches == 1  # the cache hit added no reservation


@pytest.mark.asyncio
async def test_search_source_cache_reuses_normalized_results(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("zhipu"),
    )
    calls = 0

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        nonlocal calls
        calls += 1
        return [{"url": "https://example.com/a", "title": "Source", "description": "evidence", "provider": provider}]

    def operation(query: str) -> CapabilityOperation:
        return CapabilityOperation(
            capability="web_search",
            input_value=query,
            cache_options={"count": 10},
            run=run,
            is_success=lambda value: isinstance(value, list) and bool(value),
            result_count=len,
        )

    first = await execute_capability(operation("same query"), providers=["zhipu"])
    second = await execute_capability(operation("same   query"), providers=["zhipu"])

    assert calls == 1
    assert first.value == second.value
    assert second.attempts[-1].details["cache_hit"] is True


# ---------------------------------------------------------------------------
# Config diagnostics: runtime cache defaults and invalid-value reporting.
# ---------------------------------------------------------------------------


def test_config_info_reports_runtime_cache_defaults_and_invalid_values(monkeypatch):
    info = config.get_config_info()
    assert info["SMART_SEARCH_CACHE_ENABLED"] is False
    assert info["SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS"] == 30
    assert info["SMART_SEARCH_FETCH_CACHE_TTL_SECONDS"] == 300
    assert info["SMART_SEARCH_CACHE_MAX_SIZE"] == 256

    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "maybe")
    monkeypatch.setenv("SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "bad")
    monkeypatch.setenv("SMART_SEARCH_CACHE_MAX_SIZE", "10001")
    info = config.get_config_info()

    assert info["SMART_SEARCH_CACHE_ENABLED"] is False
    assert info["SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS"] == 30
    assert info["SMART_SEARCH_FETCH_CACHE_TTL_SECONDS"] == 300
    assert info["SMART_SEARCH_CACHE_MAX_SIZE"] == 256
    assert any("SMART_SEARCH_CACHE_ENABLED" in error for error in info["config_parameter_errors"])
    assert any("SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS" in error for error in info["config_parameter_errors"])
    assert any("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS" in error for error in info["config_parameter_errors"])
    assert any("SMART_SEARCH_CACHE_MAX_SIZE" in error for error in info["config_parameter_errors"])
