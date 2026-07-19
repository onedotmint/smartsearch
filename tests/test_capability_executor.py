import pytest

from smart_search import capability_executor
from smart_search.capability_executor import CapabilityOperation, execute_capability
from smart_search.runtime_cache import CacheExecution
from smart_search.logger import logger


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


@pytest.mark.asyncio
async def test_executor_returns_success_and_attempt_metadata(monkeypatch):
    """
    /*
     * ==============================================================================
     * 步骤1：校验 capability 成功路径
     * ==============================================================================
     * 目标：成功 provider 只执行一次，并返回统一 attempt 元数据。
     * 数据源：两个 eligible provider 和 operation 的标准化 source 结果。
     * 操作：
     * 1) 替换 provider registry 状态，避免依赖本地凭据。
     * 2) 执行 operation，确认首个 provider 成功后不再调用后续 provider。
     * ==============================================================================
     */
    """
    logger.info("开始测试 capability executor 成功路径")
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first", "second"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return [{"url": "https://example.test/source", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
            result_count=len,
        )
    )

    assert calls == ["first"]
    assert execution.provider == "first"
    assert execution.value == [{"url": "https://example.test/source", "provider": "first"}]
    assert execution.attempts[-1]["status"] == "ok"
    assert execution.attempts[-1]["result_count"] == 1
    logger.info("capability executor 成功路径测试完成")


@pytest.mark.asyncio
async def test_executor_falls_back_after_empty_result(monkeypatch):
    """
    /*
     * ==============================================================================
     * 步骤2：校验同 capability fallback
     * ==============================================================================
     * 目标：空结果触发同 capability 的下一个 provider，不跨 capability 兜底。
     * 数据源：首个 provider 的空列表和第二个 provider 的 source 列表。
     * 操作：
     * 1) 按 registry 顺序运行两个 provider。
     * 2) 检查 empty -> ok 的 attempt 顺序和最终 provider。
     * ==============================================================================
     */
    """
    logger.info("开始测试 capability executor 空结果 fallback")
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first", "second"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        if provider == "first":
            return []
        return [{"url": "https://example.test/fallback", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="docs_search",
            input_value="query",
            run=run,
            result_count=len,
        )
    )

    assert calls == ["first", "second"]
    assert [attempt["status"] for attempt in execution.attempts] == ["empty", "ok"]
    assert execution.provider == "second"
    logger.info("capability executor 空结果 fallback 测试完成")


@pytest.mark.asyncio
async def test_executor_fallback_off_limits_chain_to_first_provider(monkeypatch):
    """
    /*
     * ==============================================================================
     * 步骤3：校验关闭 fallback
     * ==============================================================================
     * 目标：fallback=off 只调用第一个 eligible provider。
     * 数据源：两个 eligible provider 和首个 provider 的空结果。
     * 操作：执行 operation，确认不会尝试第二个 provider。
     */
    """
    logger.info("开始测试 capability executor fallback=off")
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first", "second"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return []

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
            result_count=len,
        ),
        fallback="off",
    )

    assert calls == ["first"]
    assert execution.value == []
    assert [attempt["provider"] for attempt in execution.attempts] == ["first"]
    logger.info("capability executor fallback=off 测试完成")


@pytest.mark.asyncio
async def test_executor_stops_when_request_budget_is_refused(monkeypatch):
    """
    /*
     * ==============================================================================
     * 步骤4：校验 request budget 拒绝
     * ==============================================================================
     * 目标：预算拒绝时不调用 provider，也不继续尝试后续 provider。
     * 数据源：两个 eligible provider 和拒绝 request reservation 的预算函数。
     * 操作：执行 operation，检查 budget_exhausted attempt。
     */
    """
    logger.info("开始测试 capability executor request budget")
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first", "second"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: False)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return [{"url": "https://unexpected.test", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
        )
    )

    assert calls == []
    assert execution.value == []
    assert len(execution.attempts) == 1
    assert execution.attempts[0]["error_type"] == "budget_exhausted"
    logger.info("capability executor request budget 测试完成")


@pytest.mark.asyncio
async def test_executor_records_fetch_budget_refusal(monkeypatch):
    """
    /*
     * ==============================================================================
     * 步骤5：校验 fetch budget 拒绝
     * ==============================================================================
     * 目标：fetch reservation 失败时在 provider 选择前返回 budget attempt。
     * 数据源：fetch budget reservation 和一个 eligible provider。
     * 操作：执行 operation，确认 provider operation 没有被调用。
     */
    """
    logger.info("开始测试 capability executor fetch budget")
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return [{"url": "https://unexpected.test", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_fetch",
            input_value="https://example.test",
            run=run,
        ),
        reserve_fetch=lambda: False,
    )

    assert calls == []
    assert execution.attempts[0]["error_type"] == "budget_exhausted"
    logger.info("capability executor fetch budget 测试完成")


@pytest.mark.asyncio
async def test_executor_preserves_cache_attempt_metadata(monkeypatch):
    """
    /*
     * ==============================================================================
     * 步骤6：校验 cache attempt 元数据
     * ==============================================================================
     * 目标：缓存命中不重复调用 provider，并保留 cache_hit/inflight_joined 字段。
     * 数据源：模拟的 CacheExecution 和一个 eligible provider。
     * 操作：替换共享 source cache，执行 operation，检查 attempt 元数据。
     */
    """
    logger.info("开始测试 capability executor cache attempt")
    calls: list[str] = []
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("cached"),
    )
    monkeypatch.setattr(
        capability_executor,
        "_cached_source_provider",
        lambda *args: _cached_execution_without_factory(),
    )

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return [{"url": "https://unexpected.test", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
        )
    )

    assert calls == []
    assert execution.value == [{"url": "https://cached.test", "provider": "cached"}]
    assert execution.attempts[0]["cache_hit"] is True
    assert execution.attempts[0]["inflight_joined"] is True
    logger.info("capability executor cache attempt 测试完成")


async def _cached_execution_without_factory() -> CacheExecution:
    return CacheExecution(
        value=[{"url": "https://cached.test", "provider": "cached"}],
        cache_hit=True,
        inflight_joined=True,
    )
