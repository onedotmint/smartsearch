"""Process-local runtime cache and command-scoped observability helpers."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, Iterator, TypeVar
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from weakref import WeakKeyDictionary


logger = logging.getLogger("smart_search")

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Awaitable[dict[str, Any]]])

_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "signature",
    "sig",
    "token",
}
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|client[_-]?secret|password|secret|token)\s*[:=]"
)


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
            "budget_exhausted": bool(self.budget_exhausted),
            "stage_elapsed_ms": dict(self.stage_elapsed_ms),
        }


_CURRENT_METRICS: ContextVar[RuntimeMetrics | None] = ContextVar(
    "smart_search_runtime_metrics",
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


def add_request(count: int = 1) -> None:
    metrics = current_metrics()
    if metrics:
        metrics.add_request(count)


def add_retry(count: int = 1) -> None:
    metrics = current_metrics()
    if metrics:
        metrics.add_retry(count)


def add_remote_router_call(count: int = 1) -> None:
    metrics = current_metrics()
    if metrics:
        metrics.add_remote_router_call(count)


def mark_budget_exhausted() -> None:
    metrics = current_metrics()
    if metrics:
        metrics.mark_budget_exhausted()


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
    async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        metrics = RuntimeMetrics()
        started_at = metrics.clock()
        logger.info("开始 command 观测: %s", func.__name__)
        with metrics_scope(metrics):
            try:
                result = await func(*args, **kwargs)
            finally:
                metrics.record_stage("command", started_at)
                logger.info("结束 command 观测: %s", func.__name__)
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
        if not parsed.scheme or not parsed.netloc:
            return raw
        if parsed.username or parsed.password:
            return None
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key.lower() in _SENSITIVE_QUERY_KEYS for key, _ in query_pairs):
            return None
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
    except ValueError:
        return None


def cache_input(value: str, *, kind: str) -> str | None:
    normalized = normalize_url(value) if kind == "url" else normalize_query(value)
    if normalized is None or not normalized:
        return None
    if kind != "url" and _SENSITIVE_TEXT_PATTERN.search(normalized):
        return None
    return normalized
