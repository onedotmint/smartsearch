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
    r"(\s*[:=]\s*)([^\s,;\"'&#]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer|Basic)(\s+)([^\s,;\"']+)")
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")


def redact_url_credentials(value: str) -> str:
    """
    /*
     * ================================================================================
     * 步骤1：脱敏 URL 凭据
     * ================================================================================
     * 目标：保留可识别的服务端点，同时移除 URL userinfo 和敏感查询参数。
     * 数据源：模型路由、诊断消息和结构化输出中的 URL 字符串。
     * 操作：
     * 1) 用统一标记替换 userinfo，保留主机、端口和路径。
     * 2) 仅替换敏感查询参数值，保留其他查询参数用于诊断。
     * ================================================================================
    */
    """
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED]"

    # 1.1 移除 URL userinfo，避免用户名或密码进入展示和诊断结果。
    redacted_netloc = parsed.netloc
    has_userinfo = "@" in redacted_netloc
    if has_userinfo:
        redacted_netloc = f"[REDACTED]@{redacted_netloc.rsplit('@', 1)[1]}"

    # 1.2 仅改写敏感查询参数，非敏感参数保留原始形式。
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    has_sensitive_query = any(is_sensitive_key(key) for key, _ in query_items)
    if not has_userinfo and not has_sensitive_query:
        return value

    redacted_query = []
    for key, item in query_items:
        if is_sensitive_key(key):
            item = "[REDACTED]"
        redacted_query.append((key, item))
    result = urlunsplit(
        (
            parsed.scheme,
            redacted_netloc,
            parsed.path,
            urlencode(redacted_query) if has_sensitive_query else parsed.query,
            parsed.fragment,
        )
    )
    return result


def sanitize_text(value: Any, secrets: Iterable[str] = ()) -> str:
    """
    =================================================================================
    步骤1：清理敏感文本
    =================================================================================
    目标：让错误、日志和 URL 元数据不能携带凭据。
    数据源：Provider 异常、上游响应片段和配置值。
    操作：
    1) 先替换调用方已知的完整 secret。
    2) 再清理 Authorization、Token、URL userinfo 和敏感 URL 查询参数。
    """
    text = "" if value is None else str(value)
    for secret in sorted({str(item) for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_PATTERN.sub(r"\1\2[REDACTED]", text)
    text = _SENSITIVE_KEY_PATTERN.sub(r"\1\2[REDACTED]", text)
    # 2.1 统一处理文本中出现的 URL，避免输出边界遗漏 userinfo。
    return _URL_PATTERN.sub(lambda match: redact_url_credentials(match.group(0)), text)


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
