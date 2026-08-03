"""Shared capability execution lifecycle for service workflows."""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .capability_service import (
    _parse_provider_filter,
    _provider_status_for_capability,
)
from .execution_primitives import (
    ExecutionAttempt,
    ExecutionOutcome,
    budget_exhausted_attempt,
    empty_attempt,
    error_attempt,
    skipped_attempt,
    success_attempt,
)
from .runtime_cache import CacheExecution, add_request, current_context
from .service_support import (
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


# Compatibility alias for the typed execution outcome. Existing internal
# imports keep the historical name; no second dict-based lifecycle exists.
CapabilityExecution = ExecutionOutcome


def _elapsed_ms(start: float) -> float:
    return round((time.time() - start) * 1000, 2)


def _ordered_provider_statuses(
    capability: str,
    providers: Sequence[str] | None,
    provider_filter: set[str] | None,
    preferred_order: Sequence[str] | None,
) -> tuple[tuple[ExecutionAttempt, ...], list[str]]:
    """
    /*
     * ================================================================================
     * 步骤2：解析 provider 执行链
     * ================================================================================
     * 目标：只让同 capability 且 eligible 的 provider 进入调用链。
     * 数据源：capability registry、显式 route、provider filter 和 preferred order。
     * 操作：
     * 1) 保留配置但不可用 provider 的 typed skipped attempt。
     * 2) 按 registry 顺序筛选并按显式 preferred order 重排。
     * ================================================================================
     */
    """
    statuses = _provider_status_for_capability(capability)
    if providers is not None:
        allowed = set(providers)
        statuses = [item for item in statuses if item.get("provider") in allowed]

    attempts = tuple(
        skipped_attempt(
            capability,
            str(item.get("provider") or ""),
            error_type="config_error",
            message=str(item.get("reason") or "provider_not_eligible"),
            elapsed_ms=0.0,
            retryable=False,
            details={
                "configured": bool(item.get("configured")),
                "enabled": bool(item.get("enabled")),
                "eligible": bool(item.get("eligible")),
                "reason": str(item.get("reason") or "provider_not_eligible"),
            },
        )
        for item in statuses
        if item.get("configured") and not item.get("eligible")
    )
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
        async def uncached() -> CacheExecution:
            return CacheExecution(await factory())

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


# Stable diagnostic fallback messages so a structured failure that carries only
# an error_type (no error message) still produces a non-empty ``ExecutionError``.
_DEFAULT_ERROR_MESSAGES: dict[str, str] = {
    "empty": "provider returned no usable result",
    "budget_exhausted": "request budget exhausted",
    "config_error": "provider configuration error",
    "timeout": "provider request timed out",
    "network_error": "provider network error",
    "quality_error": "provider result failed the quality gate",
    "protocol_error": "provider returned an invalid response",
    "auth_error": "provider authentication failed",
    "rate_limit": "provider rate limit exceeded",
    "fetch_error": "provider fetch failed",
    "challenge": "provider returned a challenge page",
    "parse_error": "provider response could not be parsed",
    "internal_error": "provider execution failed",
}


def _classification_message(error_type: str, error: str) -> str:
    """Return a stable non-empty diagnostic message for an error classification."""
    if error and error.strip():
        return error.strip()
    return _DEFAULT_ERROR_MESSAGES.get(error_type, f"provider execution failed: {error_type}")


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

    # 3.0 校验 cache kind，使不支持的 kind 成为清晰本地错误，而不是被吞掉。
    if operation.cache_kind not in {"source", "fetch", "content", "", "none"}:
        raise ValueError(f"Unsupported capability cache kind: {operation.cache_kind}")

    # 3.1 预留 command 级 fetch 预算。
    if reserve_fetch is not None and not reserve_fetch():
        # 恢复旧 service_support._budget_exhausted_attempt 的 diagnostic parity：
        # 存在当前 RequestContext 且 budget.exhausted_reason 非空时，把 reason 附加到
        # message，否则保持不带 reason 的 base message。
        context = current_context()
        reason = "request budget exhausted"
        if context is not None and context.budget.exhausted_reason:
            reason = f"request budget exhausted: {context.budget.exhausted_reason}"
        attempts = (budget_exhausted_attempt(operation.capability, message=reason, elapsed_ms=0.0),)
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
            extra = _cache_attempt_extra(execution)
            if operation.is_success(value):
                attempts = attempts + (
                    success_attempt(
                        operation.capability,
                        provider,
                        elapsed_ms=_elapsed_ms(provider_start),
                        result_count=operation.result_count(value),
                        details=extra,
                    ),
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
                retryable = False
            if error_type == "empty":
                retryable = False if retryable is None else retryable
            # A structured failure with only an error_type (no message) must still
            # produce a stable non-empty ExecutionError message.
            error = _classification_message(error_type, error)
            if error_type == "empty":
                attempt = empty_attempt(
                    operation.capability,
                    provider,
                    elapsed_ms=_elapsed_ms(provider_start),
                    message=error,
                    retryable=retryable,
                    details=extra,
                )
            else:
                attempt = error_attempt(
                    operation.capability,
                    provider,
                    error_type=error_type,
                    message=error,
                    elapsed_ms=_elapsed_ms(provider_start),
                    retryable=retryable,
                    details=extra,
                )
            attempts = attempts + (attempt,)
            if error_type == "budget_exhausted":
                logger.info("capability 执行停止于预算边界: capability=%s provider=%s", operation.capability, provider)
                break
        # 3.5 将 transport、HTTP、timeout 和未知异常统一转为稳定 attempt。
        except Exception as exc:
            error_type, error, retryable = classify_provider_exception(exc)
            attempts = attempts + (
                error_attempt(
                    operation.capability,
                    provider,
                    error_type=error_type,
                    message=_classification_message(error_type, error),
                    elapsed_ms=_elapsed_ms(provider_start),
                    retryable=retryable,
                ),
            )
            logger.info(
                "capability provider 执行异常: capability=%s provider=%s error_type=%s",
                operation.capability,
                provider,
                error_type,
            )

    budget_hit = any(
        attempt.error is not None and attempt.error.type == "budget_exhausted"
        for attempt in attempts
    )
    value = operation.empty_value("request-budget" if budget_hit else "")
    logger.info(
        "capability 执行完成: capability=%s provider=%s attempts=%s elapsed_ms=%s",
        operation.capability,
        "",
        len(attempts),
        round((time.time() - start) * 1000, 2),
    )
    return CapabilityExecution(value=value, attempts=attempts)


__all__ = ["CapabilityExecution", "CapabilityOperation", "execute_capability"]
