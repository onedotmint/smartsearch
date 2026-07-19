import asyncio

import pytest

from smart_search import service
from smart_search.runtime_cache import RuntimeTTLCache


@pytest.fixture(autouse=True)
def clear_runtime_caches():
    service.reset_runtime_cache()
    yield
    service.reset_runtime_cache()


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


@pytest.mark.asyncio
async def test_fetch_cache_reuses_clean_content_and_public_metrics(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    calls = 0

    async def fake_extract(url):
        nonlocal calls
        calls += 1
        return "# Result\nAuthorization: Bearer hidden-token"

    monkeypatch.setattr(service, "call_tavily_extract", fake_extract)

    first = await service.fetch("https://Example.com/page/#fragment")
    second = await service.fetch("https://example.com/page")

    assert calls == 1
    assert first["content"] == "# Result\nAuthorization: [REDACTED] [REDACTED]"
    assert second["content"] == first["content"]
    assert first["request_count"] == 1
    assert first["cache_hit"] == 0
    assert second["request_count"] == 0
    assert second["cache_hit"] == 1
    assert second["stage_elapsed_ms"]["fetch.providers"] >= 0
    assert second["stage_elapsed_ms"]["command"] >= 0
    assert second["provider_attempts"][0]["cache_hit"] is True


@pytest.mark.asyncio
async def test_fetch_concurrent_requests_share_one_owner_task(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_extract(url):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "shared-content"

    monkeypatch.setattr(service, "call_tavily_extract", fake_extract)
    owner = asyncio.create_task(service.fetch("https://example.com/shared"))
    await started.wait()
    waiter = asyncio.create_task(service.fetch("https://example.com/shared"))
    await asyncio.sleep(0)
    release.set()
    owner_result, waiter_result = await asyncio.gather(owner, waiter)

    assert calls == 1
    assert owner_result["request_count"] == 1
    assert waiter_result["request_count"] == 0
    assert waiter_result["inflight_joined"] == 1
    assert waiter_result["cache_hit"] == 0


@pytest.mark.asyncio
async def test_search_source_cache_reuses_normalized_results(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    calls = 0

    async def fake_search(query, count=10, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "ok": True,
            "results": [{"title": "Source", "url": "https://example.com/a", "content": "evidence"}],
        }

    monkeypatch.setattr(service, "zhipu_search", fake_search)

    first = await service.search("same query")
    second = await service.search("same   query")

    assert calls == 1
    assert first["sources"] == second["sources"]
    assert first["request_count"] == 1
    assert second["request_count"] == 0
    assert second["cache_hit"] == 1
    assert second["provider_attempts"][0]["cache_hit"] is True


@pytest.mark.asyncio
async def test_fetch_cache_invalidates_on_ttl_config_credential_and_disable(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "30")
    monkeypatch.setenv("TAVILY_API_KEY", "first-key")
    monkeypatch.setenv("TAVILY_API_URL", "https://tavily.one")
    calls = 0

    async def fake_extract(url):
        nonlocal calls
        calls += 1
        return f"content-{calls}"

    monkeypatch.setattr(service, "call_tavily_extract", fake_extract)

    first = await service.fetch("https://example.com/page")
    assert first["content"] == "content-1"

    monkeypatch.setenv("TAVILY_API_URL", "https://tavily.two")
    changed_endpoint = await service.fetch("https://example.com/page")
    assert changed_endpoint["content"] == "content-2"

    monkeypatch.setenv("TAVILY_API_KEY", "second-key")
    changed_credential = await service.fetch("https://example.com/page")
    assert changed_credential["content"] == "content-3"

    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "false")
    disabled = await service.fetch("https://example.com/page")
    assert disabled["content"] == "content-4"

    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    reenabled = await service.fetch("https://example.com/page")
    assert reenabled["content"] == "content-5"
    assert calls == 5


@pytest.mark.asyncio
async def test_sensitive_fetch_url_bypasses_cache(monkeypatch):
    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    calls = 0

    async def fake_extract(url):
        nonlocal calls
        calls += 1
        return "content"

    monkeypatch.setattr(service, "call_tavily_extract", fake_extract)

    await service.fetch("https://example.com/page?api_key=secret")
    await service.fetch("https://example.com/page?api_key=secret")

    assert calls == 2


def test_config_info_reports_runtime_cache_defaults_and_invalid_values(monkeypatch):
    assert service.config.get_config_info()["SMART_SEARCH_CACHE_ENABLED"] is False
    assert service.config.get_config_info()["SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS"] == 30
    assert service.config.get_config_info()["SMART_SEARCH_FETCH_CACHE_TTL_SECONDS"] == 300
    assert service.config.get_config_info()["SMART_SEARCH_CACHE_MAX_SIZE"] == 256

    monkeypatch.setenv("SMART_SEARCH_CACHE_ENABLED", "maybe")
    monkeypatch.setenv("SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS", "bad")
    monkeypatch.setenv("SMART_SEARCH_CACHE_MAX_SIZE", "10001")
    info = service.config.get_config_info()

    assert info["SMART_SEARCH_CACHE_ENABLED"] is False
    assert info["SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS"] == 30
    assert info["SMART_SEARCH_FETCH_CACHE_TTL_SECONDS"] == 300
    assert info["SMART_SEARCH_CACHE_MAX_SIZE"] == 256
    assert any("SMART_SEARCH_CACHE_ENABLED" in error for error in info["config_parameter_errors"])
    assert any("SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS" in error for error in info["config_parameter_errors"])
    assert any("SMART_SEARCH_FETCH_CACHE_TTL_SECONDS" in error for error in info["config_parameter_errors"])
    assert any("SMART_SEARCH_CACHE_MAX_SIZE" in error for error in info["config_parameter_errors"])
