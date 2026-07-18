"""Shared redaction helpers for provider errors and machine-readable output."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Iterable


_SENSITIVE_KEY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "token",
}
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|client[_-]?secret|password|secret|token)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer|Basic)(\s+)([^\s,;\"']+)")
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")


def _redact_url_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.query:
        return value
    redacted = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in _SENSITIVE_KEY_NAMES:
            item = "[REDACTED]"
        redacted.append((key, item))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(redacted), parsed.fragment))


def sanitize_text(value: Any, secrets: Iterable[str] = ()) -> str:
    """
    =================================================================================
    步骤1：清理敏感文本
    =================================================================================
    目标：让错误、日志和 URL 元数据不能携带凭据。
    数据源：Provider 异常、上游响应片段和配置值。
    操作：
    1) 先替换调用方已知的完整 secret。
    2) 再清理 Authorization、Token 和敏感 URL 查询参数。
    """
    text = "" if value is None else str(value)
    for secret in sorted({str(item) for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_PATTERN.sub(r"\1\2[REDACTED]", text)
    text = _SENSITIVE_KEY_PATTERN.sub(r"\1\2[REDACTED]", text)
    return _URL_PATTERN.sub(lambda match: _redact_url_query(match.group(0)), text)


def sanitize_data(value: Any, secrets: Iterable[str] = (), *, key: str = "") -> Any:
    """
    =================================================================================
    步骤2：递归清理结构化结果
    =================================================================================
    目标：在不改变业务字段形状的前提下，清理 JSON 中的凭据。
    数据源：Service 结果字典、Provider attempts 和诊断信息。
    操作：
    1) 敏感字段直接替换值。
    2) 其他字符串按文本规则清理，列表和字典递归处理。
    """
    normalized_key = key.lower().replace("-", "_")
    if is_sensitive_key(normalized_key):
        if isinstance(value, str) and (
            value in {"[REDACTED]", "未配置", "not configured"} or "*" in value
        ):
            return value
        return "[REDACTED]" if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(item_key): sanitize_data(item, secrets, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_data(item, secrets, key=key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_data(item, secrets, key=key) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    return value


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEY_NAMES
        or normalized.endswith("_api_key")
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized in {"authorization", "password"}
    )
