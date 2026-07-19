"""Shared helpers for capability-owned provider command modules."""

import json
from typing import Any

from .capability_service import (
    _command_capability_failure,
    _command_capability_metadata,
    _command_capability_preflight,
    _provider_availability,
)
from .providers.base import ProviderResult


async def decode_provider_json(
    raw: Any,
    provider: str = "anysearch",
    capability: str = "vertical_search",
) -> dict[str, Any]:
    """
    /*
     * ================================================================================
     * 步骤1：统一 provider 结果边界
     * ================================================================================
     * 目标：让 capability-owned command 只向 workflow 暴露结构化结果。
     * 数据源：ProviderResult、结构化字典和历史 JSON 字符串。
     * 操作：
     * 1) 优先读取结构化 provider result。
     * 2) 兼容旧 JSON 字符串，只在边界解析一次。
     * 3) 解析失败保留 parse_error 或 protocol_error。
     * ================================================================================
     */
    """
    if isinstance(raw, ProviderResult):
        return raw.to_dict()
    if isinstance(raw, dict):
        return dict(raw)
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "ok": False,
            "provider": provider,
            "capability": capability,
            "error_type": "parse_error",
            "error": str(exc) or str(raw),
            "retryable": False,
        }
    if not isinstance(decoded, dict):
        return {
            "ok": False,
            "provider": provider,
            "capability": capability,
            "error_type": "protocol_error",
            "error": "provider response must be a JSON object",
            "retryable": False,
        }
    data = dict(decoded)
    data.setdefault("provider", provider)
    if not data.get("ok", False):
        data.setdefault("error_type", "network_error")
    return data


__all__ = [
    "_command_capability_failure",
    "_command_capability_metadata",
    "_command_capability_preflight",
    "_provider_availability",
    "decode_provider_json",
]
