import pytest

from smart_search import capability_executor
from smart_search.capability_executor import CapabilityOperation, execute_capability
from smart_search.execution_primitives import (
    ExecutionAttemptStatus,
    project_attempt_dict,
)
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
    assert isinstance(execution.attempts, tuple)
    assert execution.attempts[-1].status is ExecutionAttemptStatus.OK
    assert execution.attempts[-1].result_count == 1
    # legacy projection keeps the historical dict shape.
    legacy = project_attempt_dict(execution.attempts[-1])
    assert legacy["status"] == "ok"
    assert legacy["result_count"] == 1
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
    assert [attempt.status for attempt in execution.attempts] == [
        ExecutionAttemptStatus.EMPTY,
        ExecutionAttemptStatus.OK,
    ]
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
    assert [attempt.provider for attempt in execution.attempts] == ["first"]
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
    assert execution.attempts[0].status is ExecutionAttemptStatus.ERROR
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.type == "budget_exhausted"
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
    assert execution.attempts[0].status is ExecutionAttemptStatus.SKIPPED
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.type == "budget_exhausted"
    assert execution.attempts[0].details["budget_exhausted"] is True
    logger.info("capability executor fetch budget 测试完成")


class _FakeBudget:
    def __init__(self, exhausted_reason: str = ""):
        self.exhausted_reason = exhausted_reason


class _FakeContext:
    def __init__(self, exhausted_reason: str = ""):
        self.budget = _FakeBudget(exhausted_reason)


@pytest.mark.asyncio
async def test_fetch_budget_refusal_preserves_reason_in_legacy_projection(monkeypatch):
    """Fetch reservation refusal must restore the old diagnostic parity: when a
    current RequestContext exists with a non-empty budget.exhausted_reason, the
    projected legacy error message carries ``request budget exhausted: <reason>``."""
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )
    monkeypatch.setattr(capability_executor, "current_context", lambda: _FakeContext("fetches"))

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        raise AssertionError("provider must not be called on fetch budget refusal")

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_fetch",
            input_value="https://example.test",
            run=run,
        ),
        reserve_fetch=lambda: False,
    )
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.message == "request budget exhausted: fetches"
    legacy = project_attempt_dict(execution.attempts[0])
    assert legacy["error"] == "request budget exhausted: fetches"


@pytest.mark.asyncio
async def test_fetch_budget_refusal_base_message_without_context(monkeypatch):
    """Without a current RequestContext (or an exhausted_reason), the fetch
    budget refusal keeps the original base message unchanged."""
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )
    monkeypatch.setattr(capability_executor, "current_context", lambda: None)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        raise AssertionError("provider must not be called on fetch budget refusal")

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_fetch",
            input_value="https://example.test",
            run=run,
        ),
        reserve_fetch=lambda: False,
    )
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.message == "request budget exhausted"
    legacy = project_attempt_dict(execution.attempts[0])
    assert legacy["error"] == "request budget exhausted"


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
    assert execution.attempts[0].details["cache_hit"] is True
    assert execution.attempts[0].details["inflight_joined"] is True
    logger.info("capability executor cache attempt 测试完成")


@pytest.mark.asyncio
async def test_executor_records_configured_disabled_skip_before_eligible(monkeypatch):
    """
    Configured-but-ineligible providers produce a typed skipped attempt that
    precedes the eligible provider attempt in stable order.
    """
    statuses = [
        {"provider": "disabled", "configured": True, "enabled": False, "eligible": False, "reason": "missing key"},
        {"provider": "active", "configured": True, "enabled": True, "eligible": True, "reason": "ready"},
    ]
    monkeypatch.setattr(capability_executor, "_provider_status_for_capability", lambda capability: statuses)
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)
    calls: list[str] = []

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        return [{"url": "https://example.test", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
            result_count=len,
        )
    )
    assert calls == ["active"]
    assert [a.status for a in execution.attempts] == [
        ExecutionAttemptStatus.SKIPPED,
        ExecutionAttemptStatus.OK,
    ]
    skipped = execution.attempts[0]
    assert skipped.provider == "disabled"
    assert skipped.error is not None and skipped.error.type == "config_error"
    assert skipped.details["configured"] is True
    assert skipped.details["eligible"] is False
    assert skipped.details["reason"] == "missing key"


@pytest.mark.asyncio
async def test_executor_classified_exception_then_same_capability_success(monkeypatch):
    from smart_search.providers.base import ProviderError

    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first", "second"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)
    calls: list[str] = []

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        calls.append(provider)
        if provider == "first":
            raise ProviderError("timeout", "first timed out")
        return [{"url": "https://example.test/ok", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
            result_count=len,
        )
    )
    assert calls == ["first", "second"]
    assert [a.status for a in execution.attempts] == [
        ExecutionAttemptStatus.ERROR,
        ExecutionAttemptStatus.OK,
    ]
    assert execution.attempts[0].error is not None
    assert execution.attempts[0].error.type == "timeout"
    assert execution.provider == "second"


@pytest.mark.asyncio
async def test_executor_no_eligible_provider_returns_empty_value(monkeypatch):
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: [
            {"provider": "disabled", "configured": True, "enabled": False, "eligible": False, "reason": "no key"}
        ],
    )
    calls: list[str] = []

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
    assert execution.provider == ""
    assert len(execution.attempts) == 1
    assert execution.attempts[0].status is ExecutionAttemptStatus.SKIPPED


@pytest.mark.asyncio
async def test_executor_attempts_are_immutable_tuple(monkeypatch):
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        return [{"url": "https://example.test", "provider": provider}]

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            run=run,
            result_count=len,
        )
    )
    assert isinstance(execution.attempts, tuple)
    with pytest.raises(AttributeError):
        execution.attempts.append  # type: ignore[attr-defined]
    for attempt in execution.attempts:
        assert isinstance(attempt.details, dict) is False  # read-only mapping


@pytest.mark.asyncio
async def test_executor_structured_failure_without_message_gets_stable_error(monkeypatch):
    """A structured failure carrying only an error_type (no message) must not
    raise ValueError and must produce a stable non-empty ExecutionError message."""
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)

    async def run(provider: str, outcome: dict[str, object]) -> dict[str, object]:
        return {"error_type": "timeout", "ok": False, "results": []}

    execution = await execute_capability(
        CapabilityOperation(
            capability="web_search",
            input_value="query",
            cache_kind="none",
            run=run,
            is_success=lambda value: bool(value.get("results")),
            result_count=lambda value: len(value.get("results") or []),
        )
    )
    assert len(execution.attempts) == 1
    attempt = execution.attempts[0]
    assert attempt.status is ExecutionAttemptStatus.ERROR
    assert attempt.error is not None
    assert attempt.error.type == "timeout"
    assert attempt.error.message.strip()  # stable non-empty message
    assert attempt.error.message == "provider request timed out"
    legacy = project_attempt_dict(attempt)
    assert legacy["error_type"] == "timeout"
    assert legacy["error"]


@pytest.mark.asyncio
async def test_executor_unsupported_cache_kind_clear_error(monkeypatch):
    monkeypatch.setattr(
        capability_executor,
        "_provider_status_for_capability",
        lambda capability: _eligible_statuses("first"),
    )
    monkeypatch.setattr(capability_executor, "add_request", lambda: True)

    async def run(provider: str, outcome: dict[str, object]) -> list[dict[str, str]]:
        return [{"url": "https://example.test", "provider": provider}]

    with pytest.raises(ValueError, match="Unsupported capability cache kind"):
        await execute_capability(
            CapabilityOperation(
                capability="web_search",
                input_value="query",
                cache_kind="bogus",
                run=run,
            )
        )


async def _cached_execution_without_factory() -> CacheExecution:
    return CacheExecution(
        value=[{"url": "https://cached.test", "provider": "cached"}],
        cache_hit=True,
        inflight_joined=True,
    )
