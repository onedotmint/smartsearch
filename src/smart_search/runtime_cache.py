"""Process-local runtime cache and command-scoped observability helpers."""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, Iterator, TypeVar
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from weakref import WeakKeyDictionary

import httpx

from .config import ConfigSnapshot, config
from .security import is_sensitive_key


logger = logging.getLogger("smart_search")

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Awaitable[dict[str, Any]]])

_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|client[_-]?secret|password|secret|token)\s*[:=]"
)


class RequestBudgetExceeded(RuntimeError):
    """
    /*
     * ================================================================================
     * 步骤1：终止超出预算的远程动作
     * ================================================================================
     * 目标：让 provider、retry、fetch 和 synthesis 在同一个预算边界内停止。
     * 数据源：RequestBudget 的 deadline、计数上限和 allow_synthesis。
     * 操作：
     * 1) 保存稳定的 budget_exhausted 错误类型。
     * 2) 让 provider adapter 可以将异常转换为结构化结果。
     * ================================================================================
     */
    """

    error_type = "budget_exhausted"

    def __init__(self, reason: str = "request budget exhausted"):
        self.reason = str(reason or "request budget exhausted")
        super().__init__(self.reason)


@dataclass
class RequestBudget:
    """
    /*
     * ================================================================================
     * 步骤2：维护单次 command 请求预算
     * ================================================================================
     * 目标：限制总 deadline、provider attempt、retry、fetch 和 synthesis。
     * 数据源：command 的绝对 deadline、预算上限和单调时钟。
     * 操作：
     * 1) 每个远程 provider 请求先预留 provider attempt。
     * 2) 每次 retry、URL fetch 和 synthesis 都在实际动作前预留额度。
     * 3) deadline 或任一上限耗尽后记录原因并拒绝后续动作。
     * ================================================================================
     */
    """

    deadline: float | None = None
    max_provider_attempts: int = 32
    max_retry_attempts: int = 16
    max_fetches: int = 8
    allow_synthesis: bool = True
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)
    provider_attempts: int = 0
    retry_attempts: int = 0
    fetches: int = 0
    synthesis_attempts: int = 0
    exhausted_reason: str = ""

    def __post_init__(self) -> None:
        self.max_provider_attempts = max(0, int(self.max_provider_attempts))
        self.max_retry_attempts = max(0, int(self.max_retry_attempts))
        self.max_fetches = max(0, int(self.max_fetches))
        self.allow_synthesis = bool(self.allow_synthesis)

    def remaining_seconds(self, now: float | None = None) -> float | None:
        if self.deadline is None:
            return None
        current = self.clock() if now is None else float(now)
        return max(0.0, float(self.deadline) - current)

    @property
    def exhausted(self) -> bool:
        return bool(self.exhausted_reason)

    def _reject(self, reason: str) -> bool:
        if not self.exhausted_reason:
            self.exhausted_reason = reason
        return False

    def _time_available(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is None or remaining > 0

    def can_provider_attempt(self) -> bool:
        return self._time_available() and self.provider_attempts < self.max_provider_attempts

    def reserve_provider_attempt(self, count: int = 1) -> bool:
        count = max(0, int(count))
        if not count:
            return True
        if not self._time_available():
            return self._reject("deadline")
        if self.provider_attempts + count > self.max_provider_attempts:
            return self._reject("provider_attempts")
        self.provider_attempts += count
        return True

    def can_retry(self) -> bool:
        return self._time_available() and self.retry_attempts < self.max_retry_attempts

    def reserve_retry(self, count: int = 1) -> bool:
        count = max(0, int(count))
        if not count:
            return True
        if not self._time_available():
            return self._reject("deadline")
        if self.retry_attempts + count > self.max_retry_attempts:
            return self._reject("retry_attempts")
        self.retry_attempts += count
        return True

    def can_fetch(self) -> bool:
        return self._time_available() and self.fetches < self.max_fetches

    def reserve_fetch(self, count: int = 1) -> bool:
        count = max(0, int(count))
        if not count:
            return True
        if not self._time_available():
            return self._reject("deadline")
        if self.fetches + count > self.max_fetches:
            return self._reject("fetches")
        self.fetches += count
        return True

    def reserve_synthesis(self) -> bool:
        if not self._time_available():
            return self._reject("deadline")
        if not self.allow_synthesis:
            return self._reject("synthesis_disabled")
        if self.synthesis_attempts:
            return self._reject("synthesis_attempts")
        self.synthesis_attempts = 1
        return True

    def clamp_retry_delay(self, delay: float) -> float:
        proposed = max(0.0, float(delay or 0.0))
        remaining = self.remaining_seconds()
        if remaining is None:
            return proposed
        return min(proposed, remaining)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deadline": self.deadline,
            "remaining_seconds": self.remaining_seconds(),
            "provider_attempts": self.provider_attempts,
            "max_provider_attempts": self.max_provider_attempts,
            "retry_attempts": self.retry_attempts,
            "max_retry_attempts": self.max_retry_attempts,
            "fetches": self.fetches,
            "max_fetches": self.max_fetches,
            "synthesis_allowed": self.allow_synthesis,
            "synthesis_attempts": self.synthesis_attempts,
            "exhausted_reason": self.exhausted_reason,
        }


@dataclass
class RuntimeMetrics:
    """
    ================================================================================
    步骤1：维护单次 command 观测数据
    ================================================================================
    目标：让缓存命中、远程调用和阶段耗时都落在本次 command 的结果中。
    数据源：service/provider 调用边界和 runtime cache 状态。
    操作：
    1) 统计逻辑 provider 请求、重试、缓存命中和 in-flight 合并。
    2) 记录稳定的 stage-name 到毫秒数映射。
    """

    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    request_count: int = 0
    cache_hit: int = 0
    inflight_joined: int = 0
    remote_router_calls: int = 0
    retry_count: int = 0
    provider_attempt_count: int = 0
    fetch_count: int = 0
    synthesis_count: int = 0
    budget_exhausted: bool = False
    stage_elapsed_ms: dict[str, float] = field(default_factory=dict)

    def add_request(self, count: int = 1) -> None:
        self.request_count += max(0, int(count))

    def add_cache_hit(self, count: int = 1) -> None:
        self.cache_hit += max(0, int(count))

    def add_inflight_join(self, count: int = 1) -> None:
        self.inflight_joined += max(0, int(count))

    def add_remote_router_call(self, count: int = 1) -> None:
        self.remote_router_calls += max(0, int(count))

    def add_retry(self, count: int = 1) -> None:
        self.retry_count += max(0, int(count))

    def add_provider_attempt(self, count: int = 1) -> None:
        self.provider_attempt_count += max(0, int(count))

    def add_fetch(self, count: int = 1) -> None:
        self.fetch_count += max(0, int(count))

    def add_synthesis(self, count: int = 1) -> None:
        self.synthesis_count += max(0, int(count))

    def mark_budget_exhausted(self) -> None:
        self.budget_exhausted = True

    def record_stage(self, name: str, started_at: float) -> None:
        elapsed = max(0.0, (self.clock() - started_at) * 1000)
        self.stage_elapsed_ms[name] = round(self.stage_elapsed_ms.get(name, 0.0) + elapsed, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_count": int(self.request_count),
            "cache_hit": int(self.cache_hit),
            "inflight_joined": int(self.inflight_joined),
            "remote_router_calls": int(self.remote_router_calls),
            "retry_count": int(self.retry_count),
            "provider_attempt_count": int(self.provider_attempt_count),
            "fetch_count": int(self.fetch_count),
            "synthesis_count": int(self.synthesis_count),
            "budget_exhausted": bool(self.budget_exhausted),
            "stage_elapsed_ms": dict(self.stage_elapsed_ms),
        }


@dataclass(frozen=True)
class RequestContext:
    """
    /*
     * ================================================================================
     * 步骤3：建立 command 运行时边界
     * ================================================================================
     * 目标：固定配置快照、session 标识、deadline、预算、观测计数器和 HTTP client。
     * 数据源：command 名称、ConfigSnapshot、RequestBudget 和当前 event loop。
     * 操作：
     * 1) 在 command 开始时只读取一次配置快照并创建一个 AsyncClient。
     * 2) provider 通过 request_client 复用该 client，并按剩余 deadline 传 timeout。
     * 3) command 正常结束、异常或取消时关闭 client，禁止跨 event loop 使用。
     * ================================================================================
     */
    """

    config: ConfigSnapshot
    command: str
    session_id: str
    started_at: float
    deadline: float | None
    budget: RequestBudget
    metrics: RuntimeMetrics
    client: Any = field(default=None, repr=False, compare=False)
    loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False, compare=False)
    _closed: bool = field(default=False, repr=False, compare=False)

    @classmethod
    async def create(
        cls,
        *,
        command: str,
        config_snapshot: ConfigSnapshot | None = None,
        session_id: str = "",
        timeout_seconds: float | None = None,
        budget: RequestBudget | None = None,
        metrics: RuntimeMetrics | None = None,
        clock: Callable[[], float] = time.monotonic,
        client_factory: Callable[..., Any] | None = None,
    ) -> "RequestContext":
        """
        /*
         * ================================================================================
         * 步骤4：创建共享 HTTP transport
         * ================================================================================
         * 目标：让一个 command 内所有 provider 复用同一个 AsyncClient。
         * 数据源：不可变配置快照、command deadline 和当前 event loop。
         * 操作：
         * 1) 计算绝对 deadline 和默认预算上限。
         * 2) 创建 timeout=None 的 client，把每次请求 timeout 留给 provider 计算。
         * 3) 将 client 与 loop 绑定到 RequestContext。
         * ================================================================================
         */
        """
        snapshot = config_snapshot or config.snapshot
        started_at = clock()
        deadline = None if timeout_seconds is None else started_at + max(0.0, float(timeout_seconds))
        request_budget = budget or RequestBudget(deadline=deadline, clock=clock)
        if request_budget.deadline is None:
            request_budget.deadline = deadline
        effective_deadline = request_budget.deadline
        request_metrics = metrics or RuntimeMetrics(clock=clock)
        loop = asyncio.get_running_loop()
        factory = client_factory or httpx.AsyncClient
        client_kwargs: dict[str, Any] = {"timeout": None, "follow_redirects": True}
        ssl_value = snapshot.values.get("SSL_VERIFY", True)
        if ssl_value is False or (isinstance(ssl_value, str) and ssl_value.strip().lower() in {"false", "0", "no"}):
            client_kwargs["verify"] = False
        logger.info("开始创建共享 HTTP client: command=%s session_id=%s", command, session_id or "generated")
        client = factory(**client_kwargs)
        logger.info("共享 HTTP client 创建完成: command=%s session_id=%s", command, session_id or "generated")
        return cls(
            config=snapshot,
            command=command,
            session_id=session_id or uuid.uuid4().hex,
            started_at=started_at,
            deadline=effective_deadline,
            budget=request_budget,
            metrics=request_metrics,
            client=client,
            loop=loop,
        )

    @classmethod
    async def open(cls, **kwargs: Any) -> "RequestContext":
        return await cls.create(**kwargs)

    async def __aenter__(self) -> "RequestContext":
        self.ensure_loop()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.aclose()

    def ensure_loop(self) -> None:
        current_loop = asyncio.get_running_loop()
        if self.loop is not None and current_loop is not self.loop:
            raise RuntimeError("RequestContext cannot be reused across event loops")

    def remaining_seconds(self) -> float | None:
        return self.budget.remaining_seconds()

    def request_timeout(self, default: float | None = None) -> float | None:
        remaining = self.remaining_seconds()
        if remaining is None:
            return default
        if remaining <= 0:
            return 0.001
        if default is None:
            return max(0.001, remaining)
        return max(0.001, min(float(default), remaining))

    async def info(self, message: str) -> None:
        del message

    async def aclose(self) -> None:
        """
        /*
         * ================================================================================
         * 步骤5：关闭 command transport
         * ================================================================================
         * 目标：释放 AsyncClient，覆盖成功、异常和取消路径。
         * 数据源：RequestContext.client 和创建该 client 的 event loop。
         * 操作：
         * 1) 检查 loop 归属和幂等关闭状态。
         * 2) 用 shield 防止外层取消中断 client.aclose。
         * 3) 记录关闭结果，不影响原始 command 异常传播。
         * ================================================================================
         */
        """
        if self._closed or self.client is None:
            return
        self.ensure_loop()
        object.__setattr__(self, "_closed", True)
        close = getattr(self.client, "aclose", None)
        if not callable(close):
            return
        close_task = asyncio.create_task(close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                pass
            except Exception as close_error:
                logger.warning("共享 HTTP client 取消路径关闭失败: %s", close_error)
            raise
        except Exception as close_error:
            logger.warning("共享 HTTP client 关闭失败: %s", close_error)
        finally:
            logger.info("共享 HTTP client 已关闭: command=%s session_id=%s", self.command, self.session_id)


@contextmanager
def request_scope(context: RequestContext) -> Iterator[RequestContext]:
    """
    /*
     * ================================================================================
     * 步骤6：绑定 request context
     * ================================================================================
     * 目标：让 service、router 和 provider 在不扩大公共签名的前提下读取同一 context。
     * 数据源：RequestContext.metrics 和 ContextVar。
     * 操作：
     * 1) 同时绑定 context 与 metrics。
     * 2) 退出时恢复父协程上下文，隔离并发 command。
     * ================================================================================
     */
    """
    context_token = _CURRENT_REQUEST_CONTEXT.set(context)
    metrics_token = _CURRENT_METRICS.set(context.metrics)
    try:
        yield context
    finally:
        _CURRENT_METRICS.reset(metrics_token)
        _CURRENT_REQUEST_CONTEXT.reset(context_token)


@asynccontextmanager
async def request_client(context: RequestContext | None = None, **client_kwargs: Any):
    """
    /*
     * ================================================================================
     * 步骤7：选择共享或独立 client
     * ================================================================================
     * 目标：command 内复用 transport，低层 provider 单测仍支持独立 client。
     * 数据源：显式 context 或当前 task 的 context。
     * 操作：
     * 1) 有 context 时校验 event loop 并返回共享 client，不负责关闭。
     * 2) 无 context 时创建并关闭本次调用的 AsyncClient。
     * ================================================================================
     */
    """
    resolved = context or current_context()
    if resolved is not None:
        resolved.ensure_loop()
        yield resolved.client
        return
    async with httpx.AsyncClient(**client_kwargs) as client:
        yield client


_CURRENT_METRICS: ContextVar[RuntimeMetrics | None] = ContextVar(
    "smart_search_runtime_metrics",
    default=None,
)
_CURRENT_REQUEST_CONTEXT: ContextVar["RequestContext | None"] = ContextVar(
    "smart_search_request_context",
    default=None,
)


@contextmanager
def metrics_scope(metrics: RuntimeMetrics) -> Iterator[RuntimeMetrics]:
    """
    ================================================================================
    步骤2：绑定 command 观测上下文
    ================================================================================
    目标：让同一 command 的协程共享计数器，同时隔离并发 command。
    数据源：command decorator 创建的 RuntimeMetrics。
    操作：
    1) 用 ContextVar 绑定当前 task 的 metrics。
    2) 退出时恢复上层上下文，避免 event loop 和测试之间泄漏状态。
    """

    token = _CURRENT_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        _CURRENT_METRICS.reset(token)


def current_metrics() -> RuntimeMetrics | None:
    return _CURRENT_METRICS.get()


def current_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()


def add_request(count: int = 1) -> bool:
    """
    /*
     * ================================================================================
     * 步骤3：预留 provider 请求额度
     * ================================================================================
     * 目标：预算耗尽时不再进入 provider 网络调用。
     * 数据源：当前 RequestContext 的 RequestBudget。
     * 操作：
     * 1) 校验 deadline 和 provider attempt 上限。
     * 2) 成功后更新旧 request_count，并增加新的 attempt 计数。
     * 3) 失败时设置 budget_exhausted，返回 False 给调用方跳过请求。
     * ================================================================================
     */
    """
    context = current_context()
    if context is not None and not context.budget.reserve_provider_attempt(count):
        context.metrics.mark_budget_exhausted()
        logger.info("provider 请求被预算拒绝: command=%s reason=%s", context.command, context.budget.exhausted_reason)
        return False
    metrics = current_metrics()
    if metrics:
        metrics.add_request(count)
        metrics.add_provider_attempt(count)
    return True


def add_retry(count: int = 1) -> bool:
    context = current_context()
    if context is not None and not context.budget.reserve_retry(count):
        context.metrics.mark_budget_exhausted()
        logger.info("retry 被预算拒绝: command=%s reason=%s", context.command, context.budget.exhausted_reason)
        return False
    metrics = current_metrics()
    if metrics:
        metrics.add_retry(count)
    return True


def add_fetch(count: int = 1) -> bool:
    context = current_context()
    if context is not None and not context.budget.reserve_fetch(count):
        context.metrics.mark_budget_exhausted()
        logger.info("fetch 被预算拒绝: command=%s reason=%s", context.command, context.budget.exhausted_reason)
        return False
    metrics = current_metrics()
    if metrics:
        metrics.add_fetch(count)
    return True


def allow_synthesis() -> bool:
    context = current_context()
    if context is not None and not context.budget.reserve_synthesis():
        context.metrics.mark_budget_exhausted()
        logger.info("synthesis 被预算拒绝: command=%s reason=%s", context.command, context.budget.exhausted_reason)
        return False
    metrics = current_metrics()
    if metrics:
        metrics.add_synthesis()
    return True


def add_remote_router_call(count: int = 1) -> None:
    metrics = current_metrics()
    if metrics:
        metrics.add_remote_router_call(count)


def mark_budget_exhausted() -> None:
    metrics = current_metrics()
    if metrics:
        metrics.mark_budget_exhausted()


def request_timeout(default: float | None = None) -> float | None:
    context = current_context()
    if context is None:
        return default
    return context.request_timeout(default)


def request_timeout_kwargs(default: float | None = None, context: RequestContext | None = None) -> dict[str, Any]:
    resolved = context or current_context()
    if resolved is None:
        return {}
    return {"timeout": resolved.request_timeout(default)}


def bounded_retry_delay(delay: float, context: RequestContext | None = None) -> float:
    resolved = context or current_context()
    if resolved is None:
        return max(0.0, float(delay or 0.0))
    return resolved.budget.clamp_retry_delay(delay)


@contextmanager
def observe_stage(name: str) -> Iterator[None]:
    """
    ================================================================================
    步骤3：记录阶段耗时
    ================================================================================
    目标：把 provider、路由和 command 阶段耗时统一写入观测字段。
    数据源：当前 command 的单调时钟。
    操作：
    1) 阶段进入时记录起点。
    2) 阶段退出时累计耗时，即使内部抛出异常也不丢失数据。
    """

    metrics = current_metrics()
    started_at = metrics.clock() if metrics else 0.0
    if metrics:
        logger.info("开始阶段: %s", name)
    try:
        yield
    finally:
        if metrics:
            metrics.record_stage(name, started_at)
            logger.info("阶段完成: %s, elapsed_ms=%s", name, metrics.stage_elapsed_ms.get(name, 0.0))


def attach_metrics(result: dict[str, Any], metrics: RuntimeMetrics | None = None) -> dict[str, Any]:
    if metrics is None:
        metrics = current_metrics()
    if metrics:
        result.update(metrics.to_dict())
    return result


def _command_budget_arguments(func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    bound_arguments: dict[str, Any] = {}
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        bound_arguments = dict(bound.arguments)
    except (TypeError, ValueError):
        bound_arguments = dict(kwargs)

    command = func.__name__
    if command in {"search", "source_discovery", "docs_discovery", "composite_search"}:
        timeout_seconds = bound_arguments.get("timeout_seconds")
        if timeout_seconds is None:
            timeout_seconds = 120.0
        return {
            "timeout_seconds": timeout_seconds,
            "max_provider_attempts": 16,
            "max_retry_attempts": 8,
            "max_fetches": 2,
        }
    if command in {"fetch", "content_fetch"}:
        return {
            "timeout_seconds": 90.0,
            "max_provider_attempts": 8,
            "max_retry_attempts": 8,
            "max_fetches": 1,
        }
    if command == "site_discovery":
        return {
            "timeout_seconds": 160.0,
            "max_provider_attempts": 4,
            "max_retry_attempts": 4,
            "max_fetches": 1,
        }
    if command == "research":
        budget_name = str(bound_arguments.get("budget") or "deep").strip().lower()
        budget_limits = {
            "quick": (60.0, 12, 6, 4),
            "standard": (120.0, 20, 10, 8),
            "deep": (240.0, 32, 16, 12),
        }
        timeout_seconds, provider_limit, retry_limit, fetch_limit = budget_limits.get(
            budget_name,
            budget_limits["deep"],
        )
        return {
            "timeout_seconds": timeout_seconds,
            "max_provider_attempts": provider_limit,
            "max_retry_attempts": retry_limit,
            "max_fetches": fetch_limit,
        }
    return {
        "timeout_seconds": None,
        "max_provider_attempts": 32,
        "max_retry_attempts": 16,
        "max_fetches": 8,
    }


def observe_command(func: F) -> F:
    """
    ================================================================================
    步骤4：装配公共 command 观测字段
    ================================================================================
    目标：保证 search、fetch、research 等入口的所有返回路径都带有一致字段。
    数据源：被装饰函数返回值和 command-scoped RuntimeMetrics。
    操作：
    1) 创建并绑定一次 command 的 metrics。
    2) 函数返回后补字段，再恢复外层上下文。
    """

    @wraps(func)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        # Composite canonical operations share one command context. Nested
        # decorated handlers must not reset deadlines, budgets, metrics, or the
        # shared HTTP client.
        if current_context() is not None:
            return await func(*args, **kwargs)

        budget_args = _command_budget_arguments(func, args, kwargs)
        metrics = RuntimeMetrics()
        started_at = metrics.clock()
        context = await RequestContext.create(
            command=func.__name__,
            session_id="",
            timeout_seconds=budget_args["timeout_seconds"],
            budget=RequestBudget(
                max_provider_attempts=budget_args["max_provider_attempts"],
                max_retry_attempts=budget_args["max_retry_attempts"],
                max_fetches=budget_args["max_fetches"],
                clock=metrics.clock,
            ),
            metrics=metrics,
            clock=metrics.clock,
        )
        logger.info("开始 command 观测: %s", func.__name__)
        try:
            with request_scope(context):
                try:
                    result = await func(*args, **kwargs)
                finally:
                    metrics.record_stage("command", started_at)
                    logger.info("结束 command 观测: %s", func.__name__)
        finally:
            await context.aclose()
        if isinstance(result, dict):
            attach_metrics(result, metrics)
        return result

    return wrapped  # type: ignore[return-value]


@dataclass(frozen=True)
class CacheExecution:
    value: Any
    cache_hit: bool = False
    inflight_joined: bool = False


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class RuntimeTTLCache:
    """
    ================================================================================
    步骤5：管理进程内 TTL/LRU 结果缓存
    ================================================================================
    目标：限制缓存容量和生命周期，只保存成功的标准化结果。
    数据源：cache key、TTL、max size 和异步 factory。
    操作：
    1) 命中时刷新 LRU 顺序并返回深拷贝。
    2) 写入时设置过期时间并淘汰最旧条目。
    3) in-flight task 按 event loop 隔离，禁止跨 loop 复用 asyncio Task。
    """

    def __init__(self, max_size: int = 256, clock: Callable[[], float] = time.monotonic):
        self._max_size = max(1, int(max_size))
        self._clock = clock
        self._cache: OrderedDict[Any, _CacheEntry] = OrderedDict()
        self._inflight: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[Any, asyncio.Task[Any]]] = WeakKeyDictionary()
        self._lock = threading.RLock()
        self._generation = 0

    @property
    def max_size(self) -> int:
        return self._max_size

    def configure(self, max_size: int) -> None:
        with self._lock:
            self._max_size = max(1, int(max_size))
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._inflight = WeakKeyDictionary()
            self._generation += 1

    def get(self, key: Any) -> Any | None:
        now = self._clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return copy.deepcopy(entry.value)

    def set(self, key: Any, value: Any, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._cache[key] = _CacheEntry(copy.deepcopy(value), self._clock() + float(ttl_seconds))
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def _loop_tasks(self, loop: asyncio.AbstractEventLoop) -> dict[Any, asyncio.Task[Any]]:
        with self._lock:
            return self._inflight.setdefault(loop, {})

    def _remove_task(self, loop: asyncio.AbstractEventLoop, key: Any, task: asyncio.Task[Any]) -> None:
        with self._lock:
            tasks = self._inflight.get(loop)
            if tasks and tasks.get(key) is task:
                tasks.pop(key, None)

    def _finish_task(
        self,
        loop: asyncio.AbstractEventLoop,
        key: Any,
        task: asyncio.Task[Any],
        ttl_seconds: float,
        cacheable: Callable[[Any], bool],
        generation: int,
    ) -> None:
        self._remove_task(loop, key, task)
        with self._lock:
            if generation != self._generation:
                return
        if task.cancelled():
            return
        try:
            value = task.result()
        except Exception:
            return
        if cacheable(value):
            self.set(key, value, ttl_seconds)

    async def get_or_set(
        self,
        key: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        ttl_seconds: float,
        enabled: bool,
        cacheable: Callable[[T], bool] | None = None,
    ) -> CacheExecution:
        """
        ================================================================================
        步骤6：合并相同 key 的异步请求
        ================================================================================
        目标：让并发等待者共享 owner 的 provider task，且取消等待者不影响 owner。
        数据源：当前 event loop、cache key 和异步 factory。
        操作：
        1) 先查 TTL/LRU 缓存。
        2) 未命中时创建或加入当前 loop 的 task。
        3) 用 shield 隔离等待者取消，并在成功后写入缓存。
        """

        cacheable = cacheable or (lambda value: bool(value))
        if not enabled:
            return CacheExecution(await factory())

        cached = self.get(key)
        if cached is not None:
            metrics = current_metrics()
            if metrics:
                metrics.add_cache_hit()
            return CacheExecution(cached, cache_hit=True)

        loop = asyncio.get_running_loop()
        with self._lock:
            tasks = self._loop_tasks(loop)
            task = tasks.get(key)
            if task is not None and task.done():
                self._finish_task(loop, key, task, ttl_seconds, cacheable, self._generation)
                task = tasks.get(key)
                if task is None:
                    cached = self.get(key)
                    if cached is not None:
                        metrics = current_metrics()
                        if metrics:
                            metrics.add_cache_hit()
                        return CacheExecution(cached, cache_hit=True)
            joined = task is not None
            if task is None:
                task = loop.create_task(factory())
                tasks[key] = task
                generation = self._generation
                task.add_done_callback(
                    lambda completed: self._finish_task(
                        loop,
                        key,
                        completed,
                        ttl_seconds,
                        cacheable,
                        generation,
                    )
                )

        if joined:
            metrics = current_metrics()
            if metrics:
                metrics.add_inflight_join()
        value = await asyncio.shield(task)
        return CacheExecution(copy.deepcopy(value), inflight_joined=joined)


def normalize_query(value: str) -> str:
    return " ".join(str(value or "").split())


def normalize_url(value: str) -> str | None:
    """
    ================================================================================
    步骤7：规范化缓存输入并过滤敏感 URL
    ================================================================================
    目标：让等价 URL 共享缓存，同时拒绝把签名或凭据放入 key。
    数据源：用户 query/URL。
    操作：
    1) URL scheme/host 小写，移除 fragment 和默认端口。
    2) 保留 query 参数，不凭经验删除业务参数。
    3) 发现敏感参数或 userinfo 时返回 None，绕过缓存。
    """

    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    # Credentials never enter a cache key: userinfo and sensitive query
    # parameters bypass caching even for schemeless/relative inputs.
    if parsed.username is not None or parsed.password is not None:
        return None
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(is_sensitive_key(key) for key, _ in query_pairs):
        return None
    if not parsed.scheme or not parsed.netloc:
        return raw
    hostname = parsed.hostname
    if not hostname:
        return None
    hostname = hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if not port or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def cache_input(value: str, *, kind: str) -> str | None:
    normalized = normalize_url(value) if kind == "url" else normalize_query(value)
    if normalized is None or not normalized:
        return None
    if kind != "url" and _SENSITIVE_TEXT_PATTERN.search(normalized):
        return None
    return normalized
