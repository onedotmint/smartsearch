"""Stable JSON contract for Smart Search CLI consumers."""

from __future__ import annotations

from typing import Any, Iterable

from .security import is_sensitive_key, sanitize_data


SCHEMA_VERSION = "1"

ERROR_CODE_BY_TYPE = {
    "parameter_error": "INVALID_ARGUMENT",
    "config_error": "CONFIGURATION_ERROR",
    "auth_error": "AUTHENTICATION_FAILED",
    "rate_limited": "RATE_LIMITED",
    "timeout": "UPSTREAM_TIMEOUT",
    "network_error": "PROVIDER_UNAVAILABLE",
    "provider_error": "PROVIDER_UNAVAILABLE",
    "quality_error": "FETCH_FAILED",
    "fetch_error": "FETCH_FAILED",
    "parse_error": "PARSE_FAILED",
    "evidence_error": "PARSE_FAILED",
    "runtime_error": "INTERNAL_ERROR",
    "output_exists": "OUTPUT_EXISTS",
    "output_error": "OUTPUT_WRITE_FAILED",
}
RETRYABLE_ERROR_CODES = {"PROVIDER_UNAVAILABLE", "UPSTREAM_TIMEOUT", "RATE_LIMITED"}


def _unique_attempted_providers(data: dict[str, Any]) -> list[str]:
    attempted: list[str] = []
    for provider in data.get("attempted_providers") or []:
        if provider and provider not in attempted:
            attempted.append(str(provider))
    for attempt in data.get("provider_attempts") or []:
        if not isinstance(attempt, dict):
            continue
        provider = attempt.get("provider")
        if provider and provider not in attempted:
            attempted.append(str(provider))
    return attempted


def _provider_name(data: dict[str, Any], attempted: list[str]) -> str:
    provider = data.get("provider")
    if provider:
        return str(provider)
    providers_used = data.get("providers_used") or []
    if providers_used:
        return str(providers_used[0])
    return attempted[0] if attempted else ""


def _error_code(data: dict[str, Any]) -> str:
    explicit = data.get("error_code")
    if explicit:
        return str(explicit)
    error_type = str(data.get("error_type") or "").lower()
    if error_type == "network_error":
        attempts = data.get("provider_attempts") or []
        if any(isinstance(item, dict) and item.get("error_type") in {"timeout", "upstream_timeout"} for item in attempts):
            return "UPSTREAM_TIMEOUT"
    return ERROR_CODE_BY_TYPE.get(error_type, "INTERNAL_ERROR")


def _warnings(data: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for value in [data.get("source_warning"), data.get("warning")]:
        if value and str(value) not in warnings:
            warnings.append(str(value))
    for value in data.get("warnings") or []:
        if value and str(value) not in warnings:
            warnings.append(str(value))
    return warnings


def _secret_values(data: dict[str, Any], secrets: Iterable[str]) -> list[str]:
    values = [str(item) for item in secrets if item]
    for key, value in data.items():
        if is_sensitive_key(str(key)):
            if (
                isinstance(value, str)
                and value
                and value not in {"[REDACTED]", "未配置", "not configured"}
                and "*" not in value
            ):
                values.append(value)
    return values


def _configured_secret_values() -> list[str]:
    """
    =================================================================================
    步骤2：收集运行时凭据
    =================================================================================
    目标：清理 provider 原始错误体中可能重复出现的配置密钥。
    数据源：当前进程的环境变量和本地配置文件。
    操作：
    1) 只读取配置键集合中标记为敏感的键。
    2) 忽略空值和脱敏占位符。
    """
    from .config import config

    values: list[str] = []
    for key in config._CONFIG_KEYS:
        if not is_sensitive_key(key):
            continue
        value = config._get_config_value(key, "") or ""
        if value and value not in {"[REDACTED]", "未配置", "not configured"} and "*" not in value:
            values.append(value)
    return values


def build_json_result(command: str, data: dict[str, Any], *, secrets: Iterable[str] = ()) -> dict[str, Any]:
    """
    =================================================================================
    步骤1：构造稳定 JSON 结果
    =================================================================================
    目标：为所有命令提供统一顶层协议，同时保留旧业务字段。
    数据源：Service 返回的扁平结果字典。
    操作：
    1) 递归脱敏原始结果。
    2) 计算 request、provider、duration 和 warning 元数据。
    3) 在 data 中保留旧字段，供迁移期消费者读取。
    """
    all_secrets = [*secrets, *_configured_secret_values()]
    safe_data = sanitize_data(data, _secret_values(data, all_secrets))
    if not isinstance(safe_data, dict):
        safe_data = {"value": safe_data}
    attempted = _unique_attempted_providers(safe_data)
    warnings = _warnings(safe_data)
    ok = bool(safe_data.get("ok", False))
    legacy = dict(safe_data)
    legacy.pop("schema_version", None)
    legacy.pop("command", None)
    legacy.pop("data", None)
    legacy.pop("meta", None)

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "command": command,
        "data": legacy,
        "meta": {
            "provider": _provider_name(safe_data, attempted),
            "attempted_providers": attempted,
            "duration_ms": safe_data.get("elapsed_ms", safe_data.get("duration_ms", 0)),
            "warnings": warnings,
        },
    }
    request_id = safe_data.get("request_id") or safe_data.get("session_id")
    if request_id:
        result["request_id"] = request_id

    # Keep legacy flat fields during the schema migration. The old top-level
    # error string remains available while the structured error is exposed as
    # error_detail and inside data.error for new consumers.
    for key, value in legacy.items():
        result[key] = value

    if not ok:
        code = _error_code(safe_data)
        message = safe_data.get("error") or safe_data.get("message") or "Smart Search command failed."
        structured_error = {
            "code": code,
            "message": str(message),
            "retryable": code in RETRYABLE_ERROR_CODES,
            "details": sanitize_data(safe_data.get("error_details") or {}, secrets),
        }
        result["error_code"] = code
        result["error_detail"] = structured_error
        result["data"]["error"] = structured_error
        result["error_type"] = safe_data.get("error_type", "")
        result["error_message"] = str(message)
    return result
