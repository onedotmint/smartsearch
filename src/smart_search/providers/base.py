import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping

import httpx


_logger = logging.getLogger(__name__)

PROVIDER_ERROR_TYPES = frozenset(
    {
        "config_error",
        "auth_error",
        "parameter_error",
        "timeout",
        "network_error",
        "rate_limited",
        "protocol_error",
        "parse_error",
        "quality_error",
        "empty",
        "provider_error",
        "budget_exhausted",
    }
)

RETRYABLE_ERROR_TYPES = frozenset({"timeout", "network_error", "rate_limited", "provider_error"})


def _default_retryable(error_type: str) -> bool:
    return error_type in RETRYABLE_ERROR_TYPES


def _mask_secret(value: str, secret: str) -> str:
    return value.replace(secret, "***") if secret else value


def classify_provider_exception(exc: BaseException) -> tuple[str, str, bool]:
    """
    /*
     * ================================================================================
     * 步骤1：分类 provider 异常
     * ================================================================================
     * 目标：把第三方异常转换为稳定的内部错误协议。
     * 数据源：HTTPX 异常、JSON 解析异常和 adapter 运行异常。
     * 操作：
     * 1) 按 HTTP 状态码区分认证、参数、限流和网络错误。
     * 2) 按异常类型区分超时、网络、解析和协议错误。
     * ================================================================================
     */
    """
    _logger.info("provider error classification started")
    if getattr(exc, "error_type", "") == "budget_exhausted":
        result = ("budget_exhausted", str(exc) or "request budget exhausted", False)
        _logger.info("provider error classification finished")
        return result
    if isinstance(exc, ProviderError):
        result = (exc.error_type, str(exc), exc.retryable)
        _logger.info("provider error classification finished")
        return result

    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status_code = response.status_code if response is not None else 0
        # Containment: never embed arbitrary upstream body bytes in the
        # normalized provider error. The message carries status only so
        # echoed credentials or request fragments cannot cross the public
        # V2/V3/Workflow error boundary.
        if status_code in {401, 403}:
            error_type = "auth_error"
        elif status_code in {400, 422}:
            error_type = "parameter_error"
        elif status_code == 408:
            error_type = "timeout"
        elif status_code == 429:
            error_type = "rate_limited"
        elif status_code >= 500:
            error_type = "network_error"
        else:
            error_type = "protocol_error"
        message = f"HTTP {status_code}"
        result = (error_type, message, _default_retryable(error_type))
        _logger.info("provider error classification finished")
        return result

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        result = ("timeout", str(exc) or "request timed out", True)
        _logger.info("provider error classification finished")
        return result
    if isinstance(exc, (httpx.NetworkError, httpx.RequestError)):
        result = ("network_error", str(exc) or "network request failed", True)
        _logger.info("provider error classification finished")
        return result
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        result = ("parse_error", str(exc) or "provider response could not be parsed", False)
        _logger.info("provider error classification finished")
        return result
    if isinstance(exc, ValueError):
        result = ("parse_error", str(exc) or "provider response could not be parsed", False)
        _logger.info("provider error classification finished")
        return result

    result = ("protocol_error", str(exc) or "provider response violated its protocol", False)
    _logger.info("provider error classification finished")
    return result


class ProviderError(Exception):
    """A classified provider failure that can cross the adapter boundary."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        provider: str = "",
        capability: str = "",
        retryable: bool | None = None,
        elapsed_ms: float = 0.0,
        attempts: list[dict[str, Any]] | None = None,
        data: Any = None,
    ):
        normalized_type = str(error_type or "provider_error")
        if normalized_type not in PROVIDER_ERROR_TYPES:
            normalized_type = "provider_error"
        self.error_type = normalized_type
        self.provider = provider
        self.capability = capability
        self.retryable = _default_retryable(normalized_type) if retryable is None else bool(retryable)
        self.elapsed_ms = float(elapsed_ms or 0.0)
        self.attempts = list(attempts or [])
        self.data = data
        super().__init__(message)

    def to_result(self, *, wire_format: str = "json") -> "ProviderResult":
        return ProviderResult.from_error(
            provider=self.provider,
            capability=self.capability,
            error_type=self.error_type,
            error=str(self),
            retryable=self.retryable,
            elapsed_ms=self.elapsed_ms,
            attempts=self.attempts,
            data=self.data,
            wire_format=wire_format,
        )


class ProviderTimeoutError(ProviderError, httpx.TimeoutException):
    """Classified timeout that remains compatible with HTTPX timeout handling."""

    def __init__(self, *args, **kwargs):
        ProviderError.__init__(self, *args, **kwargs)


class ProviderResult(str):
    """Structured provider result with a legacy string wire representation."""

    def __new__(
        cls,
        *,
        provider: str,
        capability: str,
        ok: bool,
        data: Any = None,
        content: str = "",
        error_type: str = "",
        error: str = "",
        retryable: bool | None = None,
        elapsed_ms: float = 0.0,
        attempts: list[dict[str, Any]] | None = None,
        wire_format: str = "json",
    ):
        payload = _build_payload(
            provider=provider,
            capability=capability,
            ok=ok,
            data=data,
            content=content,
            error_type=error_type,
            error=error,
            retryable=retryable,
            elapsed_ms=elapsed_ms,
            attempts=attempts,
        )
        wire_value = content if wire_format == "content" else json.dumps(payload, ensure_ascii=False, indent=2)
        instance = str.__new__(cls, wire_value)
        instance.provider = str(provider or payload.get("provider") or "")
        instance.capability = str(capability or payload.get("capability") or "")
        instance.ok = bool(payload.get("ok"))
        instance.data = data
        instance.content = str(payload.get("content") or "")
        instance.error_type = str(payload.get("error_type") or "")
        instance.error = str(payload.get("error") or "")
        instance.retryable = bool(payload.get("retryable"))
        instance.elapsed_ms = float(payload.get("elapsed_ms") or 0.0)
        instance.attempts = list(payload.get("provider_attempts") or [])
        instance.provider_attempts = instance.attempts
        instance.wire_format = wire_format
        instance._payload = payload
        return instance

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        provider: str = "",
        capability: str = "",
        wire_format: str = "json",
        attempts: list[dict[str, Any]] | None = None,
    ) -> "ProviderResult":
        """
        /*
         * ================================================================================
         * 步骤2：构造统一结果
         * ================================================================================
         * 目标：保留 provider 原始业务字段，同时补齐稳定结果字段。
         * 数据源：adapter 已完成协议转换的字典。
         * 操作：
         * 1) 识别成功、空结果和已分类错误。
         * 2) 计算 retryable、elapsed_ms 和 provider_attempts。
         * ================================================================================
         */
        """
        _logger.info("provider result construction started")
        source = dict(payload)
        resolved_provider = str(provider or source.get("provider") or "")
        resolved_capability = str(capability or source.get("capability") or "")
        content = _payload_content(source)
        ok = bool(source.get("ok"))
        error_type = str(source.get("error_type") or "")
        error = str(source.get("error") or "")
        if ok and not _has_usable_payload(source, content):
            ok = False
            error_type = "empty"
            error = error or "provider returned no usable result"
        elif not ok and not error_type:
            error_type = "provider_error"
            error = error or "provider returned an error"
        if error_type not in PROVIDER_ERROR_TYPES and error_type:
            error_type = "provider_error"
        elapsed_ms = float(source.get("elapsed_ms") or 0.0)
        resolved_attempts = list(attempts if attempts is not None else source.get("provider_attempts") or [])
        resolved_attempts = [
            {
                **attempt,
                "provider": attempt.get("provider") or resolved_provider,
                "capability": attempt.get("capability") or resolved_capability,
            }
            for attempt in resolved_attempts
            if isinstance(attempt, Mapping)
        ]
        result = cls(
            provider=resolved_provider,
            capability=resolved_capability,
            ok=ok,
            data=source,
            content=content,
            error_type=error_type,
            error=error,
            retryable=source.get("retryable"),
            elapsed_ms=elapsed_ms,
            attempts=resolved_attempts,
            wire_format=wire_format,
        )
        _logger.info("provider result construction finished")
        return result

    @classmethod
    def from_content(
        cls,
        content: str,
        *,
        provider: str,
        capability: str,
        elapsed_ms: float = 0.0,
        attempts: list[dict[str, Any]] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> "ProviderResult":
        source = dict(data or {})
        source["ok"] = bool(content and content.strip())
        source["content"] = content or ""
        source.setdefault("provider", provider)
        source.setdefault("capability", capability)
        source.setdefault("elapsed_ms", elapsed_ms)
        if not source["ok"]:
            source.setdefault("error_type", "empty")
            source.setdefault("error", "provider returned no usable content")
        return cls.from_payload(
            source,
            provider=provider,
            capability=capability,
            wire_format="content",
            attempts=attempts,
        )

    @classmethod
    def from_error(
        cls,
        *,
        provider: str,
        capability: str,
        error_type: str,
        error: str,
        retryable: bool | None = None,
        elapsed_ms: float = 0.0,
        attempts: list[dict[str, Any]] | None = None,
        data: Any = None,
        wire_format: str = "json",
    ) -> "ProviderResult":
        source: dict[str, Any] = {
            "ok": False,
            "provider": provider,
            "capability": capability,
            "error_type": error_type,
            "error": error,
            "elapsed_ms": elapsed_ms,
        }
        if isinstance(data, Mapping):
            source.update(data)
        return cls(
            provider=provider,
            capability=capability,
            ok=False,
            data=source,
            error_type=error_type,
            error=error,
            retryable=retryable,
            elapsed_ms=elapsed_ms,
            attempts=attempts,
            wire_format=wire_format,
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)

    @property
    def status(self) -> str:
        if self.ok:
            return "ok"
        return "empty" if self.error_type == "empty" else "error"


def _payload_content(payload: Mapping[str, Any]) -> str:
    for key in ("content", "raw_content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _has_usable_payload(payload: Mapping[str, Any], content: str) -> bool:
    if content.strip():
        return True
    for key in ("results", "sources", "code_snippets", "info_snippets", "data"):
        value = payload.get(key)
        if isinstance(value, (list, tuple, dict)) and bool(value):
            return True
    return False


def _build_payload(
    *,
    provider: str,
    capability: str,
    ok: bool,
    data: Any,
    content: str,
    error_type: str,
    error: str,
    retryable: bool | None,
    elapsed_ms: float,
    attempts: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    payload = dict(data) if isinstance(data, Mapping) else {}
    payload["ok"] = bool(ok)
    payload["provider"] = str(provider or payload.get("provider") or "")
    payload["capability"] = str(capability or payload.get("capability") or "")
    if content or "content" not in payload:
        payload["content"] = content
    payload["error_type"] = error_type or ("" if ok else "provider_error")
    payload["error"] = error or ("" if ok else "provider returned an error")
    payload["retryable"] = _default_retryable(payload["error_type"]) if retryable is None else bool(retryable)
    payload["elapsed_ms"] = float(elapsed_ms or payload.get("elapsed_ms") or 0.0)
    if attempts:
        payload["provider_attempts"] = list(attempts)
    return payload


def coerce_provider_result(
    value: Any,
    *,
    provider: str,
    capability: str,
    wire_format: str = "json",
) -> ProviderResult:
    """
    /*
     * ================================================================================
     * 步骤3：兼容旧 provider 返回值
     * ================================================================================
     * 目标：让 service 只消费 ProviderResult，同时兼容旧 adapter 和测试替身。
     * 数据源：ProviderResult、结构化字典、JSON 字符串或 content 字符串。
     * 操作：
     * 1) 优先读取统一结果对象。
     * 2) 仅在明确的 wire_format 下解析 legacy 值。
     * 3) 无法解析时返回 parse_error 或 protocol_error，不折叠为 empty。
     * ================================================================================
     */
    """
    _logger.info("provider result coercion started")
    if isinstance(value, ProviderResult):
        _logger.info("provider result coercion finished")
        return value
    if isinstance(value, ProviderError):
        result = value.to_result(wire_format=wire_format)
        _logger.info("provider result coercion finished")
        return result
    if isinstance(value, Mapping):
        result = ProviderResult.from_payload(value, provider=provider, capability=capability, wire_format=wire_format)
        _logger.info("provider result coercion finished")
        return result
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            result = ProviderResult.from_error(
                provider=provider,
                capability=capability,
                error_type="parse_error",
                error=str(exc),
                wire_format=wire_format,
            )
            _logger.info("provider result coercion finished")
            return result
    if isinstance(value, str):
        if wire_format == "content":
            result = ProviderResult.from_content(value, provider=provider, capability=capability)
            _logger.info("provider result coercion finished")
            return result
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            result = ProviderResult.from_error(
                provider=provider,
                capability=capability,
                error_type="parse_error",
                error=str(exc) or value,
                wire_format=wire_format,
            )
            _logger.info("provider result coercion finished")
            return result
        if not isinstance(decoded, Mapping):
            result = ProviderResult.from_error(
                provider=provider,
                capability=capability,
                error_type="protocol_error",
                error="provider response must be a JSON object",
                wire_format=wire_format,
            )
            _logger.info("provider result coercion finished")
            return result
        result = ProviderResult.from_payload(decoded, provider=provider, capability=capability, wire_format=wire_format)
        _logger.info("provider result coercion finished")
        return result
    result = ProviderResult.from_error(
        provider=provider,
        capability=capability,
        error_type="protocol_error",
        error=f"unsupported provider result type: {type(value).__name__}",
        wire_format=wire_format,
    )
    _logger.info("provider result coercion finished")
    return result


class SearchResult:
    def __init__(
        self,
        title: str,
        url: str,
        snippet: str,
        source: str = "",
        published_date: str = "",
    ):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.source = source
        self.published_date = published_date

    def to_dict(self) -> Dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "published_date": self.published_date,
        }


class BaseSearchProvider(ABC):
    provider_id = "provider"
    capability = "main_search"

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.api_key = api_key

    def get_provider_id(self) -> str:
        return self.provider_id

    def get_capability(self) -> str:
        return self.capability

    def result(
        self,
        payload: Mapping[str, Any],
        *,
        capability: str | None = None,
        wire_format: str = "json",
        attempts: list[dict[str, Any]] | None = None,
    ) -> ProviderResult:
        normalized_payload = dict(payload)
        if "error" in normalized_payload:
            normalized_payload["error"] = _mask_secret(str(normalized_payload.get("error") or ""), self.api_key)
        return ProviderResult.from_payload(
            normalized_payload,
            provider=self.get_provider_id(),
            capability=capability or self.get_capability(),
            wire_format=wire_format,
            attempts=attempts,
        )

    def content_result(
        self,
        content: str,
        *,
        capability: str | None = None,
        elapsed_ms: float = 0.0,
        attempts: list[dict[str, Any]] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        return ProviderResult.from_content(
            content,
            provider=self.get_provider_id(),
            capability=capability or self.get_capability(),
            elapsed_ms=elapsed_ms,
            attempts=attempts,
            data=data,
        )

    def error_result(
        self,
        error_type: str,
        error: str,
        *,
        capability: str | None = None,
        elapsed_ms: float = 0.0,
        retryable: bool | None = None,
        attempts: list[dict[str, Any]] | None = None,
        data: Any = None,
        wire_format: str = "json",
    ) -> ProviderResult:
        error = _mask_secret(error, self.api_key)
        return ProviderResult.from_error(
            provider=self.get_provider_id(),
            capability=capability or self.get_capability(),
            error_type=error_type,
            error=error,
            retryable=retryable,
            elapsed_ms=elapsed_ms,
            attempts=attempts,
            data=data,
            wire_format=wire_format,
        )

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> ProviderResult:
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        pass
