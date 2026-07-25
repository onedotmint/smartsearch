"""Shared capability execution lifecycle for service workflows."""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .capability_service import (
    _parse_provider_filter,
    _provider_status_for_capability,
    _skipped_provider_attempt,
)
from .runtime_cache import add_request
from .service_support import (
    _attempt,
    _budget_exhausted_attempt,
    _cache_attempt_extra,
    _cached_content_provider,
    _cached_fetch_provider,
    _cached_source_provider,
)
from .providers.base import classify_provider_exception


OperationRunner = Callable[[str, dict[str, Any]], Awaitable[Any]]
ValuePredicate = Callable[[Any], bool]
ValueCounter = Callable[[Any], int]
EmptyValueFactory = Callable[[str], Any]
BudgetReservation = Callable[[], bool]


@dataclass(frozen=True)
class CapabilityOperation:
    """
    /*
     * ================================================================================
     * 步骤1：定义 capability operation
     * ================================================================================
     * 目标：把 provider-specific 调用和共享执行生命周期分开。
     * 数据源：owning workflow 提供的 provider 列表、调用函数和结果判定函数。
     * 操作：
     * 1) 描述输入、缓存类型和 provider 调用参数。
     * 2) 由 workflow 负责归一化 provider 结果，executor 只处理生命周期。
     * ================================================================================
     */
    """

    capability: str
    input_value: str
    run: OperationRunner
    cache_kind: str = "source"
    cache_options: Mapping[str, Any] = field(default_factory=dict)
    empty_value: EmptyValueFactory = lambda _provider: []
    is_success: ValuePredicate = lambda value: bool(value)
    result_count: ValueCounter = lambda value: len(value) if isinstance(value, (list, tuple, set, dict)) else 0


@dataclass(frozen=True)
class CapabilityExecution:
    """Normalized result of one same-capability provider chain."""

    value: Any
    attempts: list[dict[str, Any]]
    provider: str = ""


def _ordered_provider_statuses(
    capability: str,
    providers: Sequence[str] | None,
    provider_filter: set[str] | None,
    preferred_order: Sequence[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    /*
     * ================================================================================
     * 步骤2：解析 provider 执行链
     * ================================================================================
     * 目标：只让同 capability 且 eligible 的 provider 进入调用链。
     * 数据源：capability registry、显式 route、provider filter 和 preferred order。
     * 操作：
     * 1) 保留配置但不可用 provider 的 skipped attempt。
     * 2) 按 registry 顺序筛选并按显式 preferred order 重排。
     * ================================================================================
     */
    """
    statuses = _provider_status_for_capability(capability)
    if providers is not None:
        allowed = set(providers)
        statuses = [item for item in statuses if item.get("provider") in allowed]

    attempts = [
        _skipped_provider_attempt(capability, item)
        for item in statuses
        if item.get("configured") and not item.get("eligible")
    ]
    eligible = [
        str(item["provider"])
        for item in statuses
        if item.get("eligible") and (provider_filter is None or item.get("provider") in provider_filter)
    ]
    if preferred_order:
        available = set(eligible)
        ordered = [provider for provider in preferred_order if provider in available]
        ordered.extend(provider for provider in eligible if provider not in ordered)
        eligible = ordered
    return attempts, eligible


def _cache_execution(
    operation: CapabilityOperation,
    provider: str,
    factory: Callable[[], Awaitable[Any]],
):
    if operation.cache_kind == "source":
        return _cached_source_provider(
            operation.capability,
            provider,
            operation.input_value,
            dict(operation.cache_options),
            factory,
        )
    if operation.cache_kind == "fetch":
        return _cached_fetch_provider(
            provider,
            operation.input_value,
            dict(operation.cache_options),
            factory,
        )
    if operation.cache_kind == "content":
        return _cached_content_provider(
            operation.capability,
            provider,
            operation.input_value,
            dict(operation.cache_options),
            factory,
        )
    if operation.cache_kind in {"", "none"}:
        async def uncached() -> Any:
            return await factory()

        return uncached()
    raise ValueError(f"Unsupported capability cache kind: {operation.cache_kind}")


def _value_error_fields(value: Any) -> tuple[str, str, bool | None]:
    if not isinstance(value, dict):
        return "", "", None
    return (
        str(value.get("error_type") or ""),
        str(value.get("error") or ""),
        value.get("retryable") if isinstance(value.get("retryable"), bool) else None,
    )


async def execute_capability(
    operation: CapabilityOperation,
    *,
    providers: Sequence[str] | None = None,
    provider_filter: str | set[str] | None = None,
    fallback: str = "auto",
    preferred_order: Sequence[str] | None = None,
    reserve_fetch: BudgetReservation | None = None,
) -> CapabilityExecution:
    """
    /*
     * ================================================================================
     * 步骤3：执行同 capability provider 链
     * ================================================================================
     * 目标：统一 provider 选择、预算、缓存、attempt 和 fallback 生命周期。
     * 数据源：CapabilityOperation、provider registry 和当前 RequestContext。
     * 操作：
     * 1) 预留 fetch 预算并解析 eligible provider。
     * 2) 在缓存未命中时预留 provider request，再执行 owning workflow 的 operation。
     * 3) 记录成功、空结果、provider 错误和运行时异常，然后按 fallback 继续。
     * ================================================================================
     */
    """
    start = time.time()
    logger = logging.getLogger("smart_search")
    logger.info("开始执行 capability: capability=%s input=%s", operation.capability, bool(operation.input_value))

    # 3.1 预留 command 级 fetch 预算。
    if reserve_fetch is not None and not reserve_fetch():
        attempts = [_budget_exhausted_attempt(operation.capability)]
        value = operation.empty_value("request-budget")
        logger.info("capability 执行因 fetch 预算结束: capability=%s", operation.capability)
        return CapabilityExecution(value=value, attempts=attempts)

    # 3.2 解析显式 provider filter。
    normalized_filter = (
        _parse_provider_filter(provider_filter)
        if isinstance(provider_filter, str)
        else provider_filter
    )
    attempts, selected = _ordered_provider_statuses(
        operation.capability,
        providers,
        normalized_filter,
        preferred_order,
    )

    # 3.3 fallback=off 只保留首个 eligible provider。
    if fallback == "off":
        selected = selected[:1]

    # 3.4 对每个 provider 共享 request、cache 和 attempt 生命周期。
    for provider in selected:
        provider_start = time.time()
        outcome: dict[str, Any] = {}

        async def provider_factory() -> Any:
            if not add_request():
                outcome.update(
                    {
                        "error_type": "budget_exhausted",
                        "error": "request budget exhausted",
                        "retryable": False,
                    }
                )
                return operation.empty_value(provider)
            return await operation.run(provider, outcome)

        try:
            execution = await _cache_execution(operation, provider, provider_factory)
            value = execution.value
            if operation.is_success(value):
                attempts.append(
                    _attempt(
                        operation.capability,
                        provider,
                        "ok",
                        provider_start,
                        result_count=operation.result_count(value),
                        extra=_cache_attempt_extra(execution),
                    )
                )
                logger.info("capability provider 执行成功: capability=%s provider=%s", operation.capability, provider)
                return CapabilityExecution(value=value, attempts=attempts, provider=provider)

            error_type, error, retryable = _value_error_fields(value)
            error_type = str(outcome.get("error_type") or error_type or "")
            error = str(outcome.get("error") or error or "")
            if isinstance(outcome.get("retryable"), bool):
                retryable = outcome["retryable"]
            if not error_type:
                error_type = "empty"
                error = error or "provider returned no usable result"
                retryable = False
            elif error_type == "empty":
                error = error or "provider returned no usable result"
                retryable = False if retryable is None else retryable
            status = "empty" if error_type == "empty" else "error"
            attempts.append(
                _attempt(
                    operation.capability,
                    provider,
                    status,
                    provider_start,
                    error_type=error_type,
                    error=error,
                    retryable=retryable,
                    extra=_cache_attempt_extra(execution),
                )
            )
            if error_type == "budget_exhausted":
                logger.info("capability 执行停止于预算边界: capability=%s provider=%s", operation.capability, provider)
                break
        # 3.5 将 transport、HTTP、timeout 和未知异常统一转为稳定 attempt。
        except Exception as exc:
            error_type, error, retryable = classify_provider_exception(exc)
            attempts.append(
                _attempt(
                    operation.capability,
                    provider,
                    "error",
                    provider_start,
                    error_type=error_type,
                    error=error,
                    retryable=retryable,
                )
            )
            logger.info(
                "capability provider 执行异常: capability=%s provider=%s error_type=%s",
                operation.capability,
                provider,
                error_type,
            )

    value = operation.empty_value("request-budget" if any(item.get("error_type") == "budget_exhausted" for item in attempts) else "")
    logger.info(
        "capability 执行完成: capability=%s provider=%s attempts=%s elapsed_ms=%s",
        operation.capability,
        "",
        len(attempts),
        round((time.time() - start) * 1000, 2),
    )
    return CapabilityExecution(value=value, attempts=attempts)


__all__ = ["CapabilityExecution", "CapabilityOperation", "execute_capability"]
