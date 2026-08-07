"""Capability registry, routing metadata, and capability validation."""

import time
from typing import Any

from .config import config
from .intent_router import (
    CAPABILITY_UTTERANCES,
    DEFAULT_ROUTE_CALIBRATION_MODELS,
    DEFAULT_SEMANTIC_CONFIDENCE_MARGIN,
    DEFAULT_SEMANTIC_CONFIDENCE_THRESHOLD,
    ROUTABLE_CAPABILITIES,
    ROUTE_CALIBRATION_QUERIES,
    IntentRouter,
    build_rules_route,
    _classifier_can_add_capability,
    _cosine_similarity,
    _ordered_capabilities,
    _semantic_summary,
)
from .logger import logger
from .providers.openai_compatible import OpenAICompatibleSearchProvider
from .providers.xai_responses import XAIResponsesSearchProvider
from .security import sanitize_text
from .service_support import (
    CAPABILITY_PROFILE_NAMES,
    COMMAND_CAPABILITY_MATRIX,
    MINIMUM_PROFILE_ERROR,
    PROFILE_NAMES,
    PROVIDER_PROFILES,
    PROVIDER_REGISTRY,
    _attempt,
    _elapsed_ms,
)

def _provider_capabilities(provider: str) -> tuple[str, ...]:
    profile = PROVIDER_REGISTRY.get(provider, {})
    capabilities = profile.get("capabilities") or [profile.get("capability", "")]
    return tuple(capability for capability in capabilities if capability)

def _provider_chain(capability: str) -> list[str]:
    return [
        provider
        for provider, _profile in sorted(
            (
                (provider, profile)
                for provider, profile in PROVIDER_REGISTRY.items()
                if capability in _provider_capabilities(provider)
            ),
            key=lambda item: item[1].get("fallback_order", {}).get(capability, 999),
        )
    ]


RESEARCH_PROFILE_ORDER = {
    capability: _provider_chain(capability)
    for capability in (
        "main_search",
        "web_search",
        "docs_search",
        "web_fetch",
        "vertical_search",
        "site_map",
        "synthesis",
    )
}
MAIN_SEARCH_FALLBACK_CHAIN = _provider_chain("main_search")
MAIN_SEARCH_PROVIDER_ALIASES = {
    "xai-responses": {"xai-responses", "xai", "grok", "grok-web-tools"},
    "openai-compatible": {"openai-compatible", "openai", "chat-completions", "primary"},
}
def provider_profiles() -> dict[str, dict[str, Any]]:
    return {provider: dict(profile) for provider, profile in PROVIDER_PROFILES.items()}

def intent_router_status() -> dict[str, Any]:
    return IntentRouter(config).status()

def _provider_supports_capability(provider: str, capability: str) -> bool:
    return capability in _provider_capabilities(provider)

def _provider_availability(provider: str, capability: str = "") -> dict[str, Any]:
    """
    =================================================================================
    步骤2：计算 provider 可用性
    =================================================================================
    目标：统一区分 configured、enabled 和 eligible，避免关闭的 provider 进入调用链。
    数据源：PROVIDER_REGISTRY 中的 config_attrs、enabled_attr 和 capability 元数据。
    操作：
    1) 检查 provider 所需配置是否完整。
    2) 检查显式 enabled gate，并生成不含 secret 的诊断原因。
    3) 只有配置完整且已启用的 provider 才标记为 eligible。
    """
    logger.info("开始计算 provider 可用性: provider=%s capability=%s", provider, capability or "*")
    profile = PROVIDER_REGISTRY.get(provider)
    if not profile:
        result = {
            "provider": provider,
            "capabilities": [],
            "configured": False,
            "enabled": False,
            "eligible": False,
            "reason": "unknown_provider",
        }
        logger.info("provider 可用性计算完成: provider=%s reason=%s", provider, result["reason"])
        return result

    capabilities = _provider_capabilities(provider)
    if capability and capability not in capabilities:
        result = {
            "provider": provider,
            "capabilities": list(capabilities),
            "configured": False,
            "enabled": False,
            "eligible": False,
            "reason": f"unsupported_capability:{capability}",
        }
        logger.info("provider 可用性计算完成: provider=%s reason=%s", provider, result["reason"])
        return result

    if provider in {"xai-responses", "openai-compatible"} and (not capability or capability == "main_search"):
        try:
            routes_configured = config.model_routes_configured
            routes = config.model_routes if routes_configured else []
        except ValueError:
            error = "Invalid SMART_SEARCH_MODEL_ROUTES"
            try:
                config.model_routes
            except ValueError as exc:
                error = str(exc)
            result = {
                "provider": provider,
                "capabilities": list(capabilities),
                "config_keys": ["SMART_SEARCH_MODEL_ROUTES"],
                "configured": False,
                "enabled": True,
                "eligible": False,
                "reason": "invalid_model_routes",
                "error": error,
            }
            logger.info("provider 可用性计算完成: provider=%s reason=invalid_model_routes", provider)
            return result
        if routes_configured:
            matching_routes = [route for route in routes if route.get("provider") == provider]
            configured = bool(matching_routes)
            result = {
                "provider": provider,
                "capabilities": list(capabilities),
                "config_keys": ["SMART_SEARCH_MODEL_ROUTES"],
                "configured": configured,
                "enabled": True,
                "eligible": configured,
                "reason": "ready" if configured else "not_in_model_routes",
                "route_ids": [route.get("id", "") for route in matching_routes],
            }
            logger.info(
                "provider 可用性计算完成: provider=%s configured=%s routes=%s",
                provider,
                configured,
                len(matching_routes),
            )
            return result

    config_attrs = tuple(profile.get("config_attrs") or ())
    config_keys = [attribute.upper() for attribute in config_attrs]
    missing_keys: list[str] = []
    configured = True
    for attribute, key in zip(config_attrs, config_keys):
        try:
            value = getattr(config, attribute, None)
        except (TypeError, ValueError):
            value = None
        if not value:
            configured = False
            missing_keys.append(key)

    enabled_attr = str(profile.get("enabled_attr") or "")
    enabled_key = str(profile.get("enabled_key") or (enabled_attr.upper() if enabled_attr else ""))
    if enabled_key and enabled_key not in config_keys:
        config_keys.append(enabled_key)
    enabled = True
    if enabled_attr:
        try:
            enabled = bool(getattr(config, enabled_attr, False))
        except (TypeError, ValueError):
            enabled = False

    if not configured:
        reason = f"missing_config:{','.join(missing_keys)}"
    elif not enabled:
        reason = f"disabled:{enabled_key}=false"
    else:
        reason = "ready"
    eligible = configured and enabled
    result = {
        "provider": provider,
        "capabilities": list(capabilities),
        "config_keys": config_keys,
        "configured": configured,
        "enabled": enabled,
        "eligible": eligible,
        "reason": reason,
    }
    logger.info(
        "provider 可用性计算完成: provider=%s configured=%s enabled=%s eligible=%s reason=%s",
        provider,
        configured,
        enabled,
        eligible,
        reason,
    )
    return result

def _provider_configured(provider: str) -> bool:
    return bool(_provider_availability(provider).get("eligible"))

def _provider_status_for_capability(capability: str) -> list[dict[str, Any]]:
    return [_provider_availability(provider, capability) for provider in _provider_chain(capability)]

def _skipped_provider_attempt(capability: str, status: dict[str, Any]) -> dict[str, Any]:
    reason = str(status.get("reason") or "provider_not_eligible")
    return _attempt(
        capability,
        str(status.get("provider") or ""),
        "skipped",
        time.time(),
        error_type="config_error",
        error=reason,
        retryable=False,
        extra={
            "configured": bool(status.get("configured")),
            "enabled": bool(status.get("enabled")),
            "eligible": bool(status.get("eligible")),
            "reason": reason,
        },
    )

def _configured_for_capability(capability: str, capability_status: dict[str, Any] | None = None) -> list[str]:
    if capability_status is not None:
        configured = set(capability_status.get(capability, {}).get("configured") or [])
        return [
            provider
            for provider in RESEARCH_PROFILE_ORDER.get(capability, [])
            if provider in configured and _provider_supports_capability(provider, capability)
        ]
    return [provider for provider in RESEARCH_PROFILE_ORDER.get(capability, []) if _provider_configured(provider)]

def _safe_provider_overrides() -> tuple[list[str], list[str], list[str]]:
    known = set(PROVIDER_PROFILES)
    preferred = [provider for provider in config.research_preferred_providers if provider in known]
    disabled = [provider for provider in config.research_disabled_providers if provider in known]
    invalid = [
        provider
        for provider in config.research_preferred_providers + config.research_disabled_providers
        if provider not in known
    ]
    return preferred, disabled, invalid

def _apply_research_overrides(capability: str, providers: list[str]) -> list[str]:
    preferred, disabled, _ = _safe_provider_overrides()
    allowed = [
        provider
        for provider in providers
        if provider not in disabled and _provider_supports_capability(provider, capability)
    ]
    ordered = [
        provider
        for provider in preferred
        if provider in allowed and _provider_supports_capability(provider, capability)
    ]
    ordered.extend(provider for provider in allowed if provider not in ordered)
    return ordered

def get_capability_status() -> dict[str, Any]:
    """
    =================================================================================
    步骤3：生成 capability 状态
    =================================================================================
    目标：让 doctor、capabilities、minimum profile 和 fallback 共享同一份 provider 状态。
    数据源：PROVIDER_REGISTRY 的能力链和 _provider_availability 结果。
    操作：
    1) 生成 configured、disabled 和 provider_status，保留旧 configured/fallback_chain 字段。
    2) 只把 eligible provider 放入能力调用链，禁用 provider 保留诊断原因。
    3) 用同一状态计算 deep_research 和 minimum profile 的可用性。
    """
    logger.info("开始生成 capability 状态")
    status: dict[str, Any] = {}
    for capability in ("main_search", "web_search", "docs_search", "web_fetch", "site_map", "vertical_search", "zread"):
        provider_status = _provider_status_for_capability(capability)
        configured = [item["provider"] for item in provider_status if item.get("eligible")]
        disabled = [
            item["provider"]
            for item in provider_status
            if item.get("configured") and not item.get("eligible")
        ]
        status[capability] = {
            "configured": configured,
            "fallback_chain": _provider_chain(capability),
            "provider_status": provider_status,
            "disabled": disabled,
            "ok": bool(configured),
        }
    status["vertical_search"]["experimental"] = True
    status["zread"]["experimental"] = True
    status["zread"]["explicit"] = True

    main_configured = status["main_search"]["configured"]
    deep_research_providers = (
        main_configured
        if main_configured
        and status["web_fetch"]["configured"]
        and (status["web_search"]["configured"] or status["docs_search"]["configured"])
        else []
    )
    status["deep_research"] = {
        "configured": deep_research_providers,
        "fallback_chain": deep_research_providers,
        "ok": bool(deep_research_providers),
    }
    logger.info("capability 状态生成完成: main=%s disabled=%s", main_configured, sum(len(item.get("disabled", [])) for item in status.values()))
    return status

def _minimum_profile_result(profile: str, capability_status: dict[str, Any]) -> dict[str, Any]:
    """
    =================================================================================
    步骤1：计算能力档位
    =================================================================================
    目标：让缺失的可选能力可观察，但不阻断已具备基础搜索和取证能力的部署。
    数据源：Provider capability status 和 SMART_SEARCH_MINIMUM_PROFILE。
    操作：
    1) 保留旧的 recommended required 字段，兼容诊断和安装器。
    2) 根据 lite、standard、full 计算真正的 enforced_required。
    3) 返回缺失能力和降级信息。
    """
    legacy_required = [] if profile == "off" else ["main_search", "docs_search", "web_fetch"]
    available_search = any(
        capability_status.get(capability, {}).get("ok")
        for capability in ("main_search", "web_search", "docs_search")
    )
    if profile == "off":
        enforced_required: list[str] = []
    elif profile == "lite":
        enforced_required = ["search"] if not available_search else []
    elif profile == "standard":
        enforced_required = list(legacy_required)
    elif profile == "full":
        enforced_required = ["main_search", "docs_search", "web_fetch", "site_map"]
    else:
        enforced_required = list(legacy_required)

    missing = [capability for capability in legacy_required if not capability_status.get(capability, {}).get("ok")]
    missing_required = []
    if "search" in enforced_required and not available_search:
        missing_required.append("search")
    for capability in enforced_required:
        if capability == "search":
            continue
        if not capability_status.get(capability, {}).get("ok"):
            missing_required.append(capability)
    ok = not missing_required
    return {
        "ok": ok,
        "error_type": "config_error" if missing_required else "",
        "error": f"{MINIMUM_PROFILE_ERROR} 缺失能力: {', '.join(missing_required)}" if missing_required else "",
        "profile": profile,
        "required": legacy_required,
        "enforced_required": enforced_required,
        "missing": missing,
        "missing_required": missing_required,
        "optional_missing": [capability for capability in missing if capability not in missing_required],
        "degraded": bool(missing and not missing_required),
        "capability_status": capability_status,
    }

def _capability_available(capability_status: dict[str, Any], capability: str) -> bool:
    status = capability_status.get(capability) or {}
    return bool(status.get("ok") or status.get("configured"))

def _required_capability_groups(
    command: str,
    *,
    minimum_profile: str,
    response_mode: str = "",
) -> tuple[tuple[str, ...], bool]:
    """
    ================================================================================
    步骤1：解析命令能力矩阵
    ================================================================================
    目标：把全局 minimum profile 诊断与命令级必需能力分开。
    数据源：COMMAND_CAPABILITY_MATRIX、当前 minimum profile 和 response mode。
    操作：
    1) 读取命令的必需能力和可选能力边界。
    2) 在显式 lite/off 的 evidence search 中允许 web_search/docs_search 二选一。
    3) 返回能力组和是否使用 source-only 路径。
    """
    normalized_command = (command or "").strip().lower()
    profile = (minimum_profile or "standard").strip().lower()
    matrix = COMMAND_CAPABILITY_MATRIX.get(normalized_command, {})
    required = tuple(matrix.get("required", ()))
    source_only = (
        normalized_command == "search"
        and profile in {"lite", "off"}
        and (response_mode or "").strip().lower() == "evidence"
    )
    if source_only:
        return (("web_search", "docs_search"),), True
    return tuple((capability,) for capability in required), False

def validate_command_capabilities(
    command: str,
    *,
    minimum_profile: str = "",
    response_mode: str = "",
    capability_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    ================================================================================
    步骤2：校验命令必需能力
    ================================================================================
    目标：只阻断当前命令缺失的能力，保留 profile 全局诊断结果。
    数据源：当前 provider capability status 和命令能力矩阵。
    操作：
    1) 计算必需能力组，支持 source-only 的同能力替代组。
    2) 生成缺失能力、可选能力和降级原因。
    3) 返回稳定的 config_error 字段供 service、doctor 和 CLI 共用。
    """
    logger.info("开始校验命令能力: command=%s", command)
    try:
        profile = (minimum_profile or config.minimum_profile).strip().lower()
    except ValueError as exc:
        result = {
            "ok": False,
            "command": command,
            "error_type": "parameter_error",
            "error": str(exc),
            "required_capabilities": [],
            "required_capability_groups": [],
            "missing_capabilities": [],
            "required_providers": [],
            "missing_providers": [],
            "optional_missing": [],
            "degraded": False,
            "degraded_reason": "",
            "capability_status": capability_status or {},
        }
        logger.info("命令能力校验完成: command=%s ok=false error_type=parameter_error", command)
        return result

    status = capability_status if capability_status is not None else get_capability_status()
    groups, source_only = _required_capability_groups(
        command,
        minimum_profile=profile,
        response_mode=response_mode,
    )
    required_capabilities: list[str] = []
    missing_capabilities: list[str] = []
    for group in groups:
        for capability in group:
            if capability not in required_capabilities:
                required_capabilities.append(capability)
        if not any(_capability_available(status, capability) for capability in group):
            missing_capabilities.extend(capability for capability in group if capability not in missing_capabilities)

    matrix = COMMAND_CAPABILITY_MATRIX.get((command or "").strip().lower(), {})
    required_providers = tuple(matrix.get("required_providers", ()))
    missing_providers: list[str] = []
    for provider in required_providers:
        provider_status = next(
            (
                item
                for capability_data in status.values()
                for item in capability_data.get("provider_status", [])
                if item.get("provider") == provider
            ),
            _provider_availability(provider),
        )
        if not provider_status.get("eligible"):
            missing_providers.append(provider)
    optional_capabilities = tuple(matrix.get("optional", ()))
    optional_missing = [
        capability
        for capability in optional_capabilities
        if not _capability_available(status, capability)
        and not bool((status.get(capability) or {}).get("experimental"))
    ]
    degraded_reasons: list[str] = []
    if source_only:
        degraded_reasons.append("main_search 未配置，当前返回 source-only 来源候选")
    if optional_missing:
        degraded_reasons.append(f"可选能力不可用: {', '.join(optional_missing)}")
    missing_reasons = [
        str(item.get("reason"))
        for capability in missing_capabilities
        for item in (status.get(capability, {}).get("provider_status") or [])
        if item.get("configured") and item.get("reason")
    ]
    error_parts: list[str] = []
    if missing_capabilities:
        error_parts.append(f"{command} 缺少必需能力: {', '.join(missing_capabilities)}")
    if missing_providers:
        error_parts.append(f"{command} 缺少必需 provider: {', '.join(missing_providers)}")
    error = "; ".join(error_parts)
    if error and missing_reasons:
        error += f" ({'; '.join(dict.fromkeys(missing_reasons))})"
    result = {
        "ok": not missing_capabilities and not missing_providers,
        "command": command,
        "error_type": "config_error" if error else "",
        "error": error,
        "required_capabilities": required_capabilities,
        "required_capability_groups": [list(group) for group in groups],
        "missing_capabilities": missing_capabilities,
        "required_providers": list(required_providers),
        "missing_providers": missing_providers,
        "optional_capabilities": list(optional_capabilities),
        "optional_missing": optional_missing,
        "optional_missing_capabilities": optional_missing,
        "source_only": source_only,
        "degraded": bool(degraded_reasons),
        "degraded_reason": "; ".join(degraded_reasons),
        "capability_status": status,
    }
    logger.info(
        "命令能力校验完成: command=%s ok=%s missing=%s degraded=%s",
        command,
        result["ok"],
        result["missing_capabilities"],
        result["degraded"],
    )
    return result

def validate_minimum_profile() -> dict[str, Any]:
    try:
        profile = config.minimum_profile
    except ValueError as e:
        return {"ok": False, "error_type": "parameter_error", "error": str(e), "missing": []}
    return _minimum_profile_result(profile, get_capability_status())

def _command_capability_metadata(
    command_result: dict[str, Any],
    minimum_result: dict[str, Any],
) -> dict[str, Any]:
    """
    ================================================================================
    步骤3：组装能力观测字段
    ================================================================================
    目标：让命令结果同时表达命令级校验和 minimum profile 诊断。
    数据源：validate_command_capabilities 和 validate_minimum_profile 的结果。
    操作：
    1) 保留 minimum_profile_ok 的旧含义。
    2) 暴露 required/missing/degraded 的命令级字段。
    3) 复用同一 capability_status，避免诊断与执行看到不同状态。
    """
    logger.info("开始组装命令能力观测字段: command=%s", command_result.get("command", ""))
    metadata = {
        "command": command_result.get("command", ""),
        "minimum_profile": minimum_result.get("profile", ""),
        "minimum_profile_ok": bool(minimum_result.get("ok", False)),
        "required_capabilities": list(command_result.get("required_capabilities") or []),
        "required_capability_groups": list(command_result.get("required_capability_groups") or []),
        "missing_capabilities": list(command_result.get("missing_capabilities") or []),
        "required_providers": list(command_result.get("required_providers") or []),
        "missing_providers": list(command_result.get("missing_providers") or []),
        "optional_missing": list(command_result.get("optional_missing") or []),
        "optional_missing_capabilities": list(command_result.get("optional_missing_capabilities") or []),
        "degraded": bool(command_result.get("degraded")),
        "degraded_reason": command_result.get("degraded_reason", ""),
        "capability_status": command_result.get("capability_status") or minimum_result.get("capability_status", {}),
    }
    logger.info(
        "命令能力观测字段组装完成: command=%s missing=%s",
        metadata["command"],
        metadata["missing_capabilities"],
    )
    return metadata

def _capability_preflight(capability: str, provider: str = "") -> dict[str, Any]:
    """Capability-qualified local gate for retained internal provider wrappers.

    Provider command modules no longer reference removed public command
    spellings in the command capability matrix. This gate validates one named
    capability and an optional named provider against the current local status
    without network I/O, and returns the same stable metadata shape as
    ``_command_capability_preflight`` so wrapper results stay compatible.
    """
    logger.info("开始执行能力预检: capability=%s provider=%s", capability, provider)
    minimum = validate_minimum_profile()
    if minimum.get("error_type") == "parameter_error":
        result = {
            "ok": False,
            "command": capability,
            "error_type": "parameter_error",
            "error": minimum.get("error", "Invalid minimum profile"),
            "required_capabilities": [capability],
            "required_capability_groups": [[capability]],
            "missing_capabilities": [],
            "required_providers": [provider] if provider else [],
            "missing_providers": [],
            "optional_missing": [],
            "optional_missing_capabilities": [],
            "source_only": False,
            "degraded": False,
            "degraded_reason": "",
            "capability_status": {},
        }
        result["metadata"] = _command_capability_metadata(result, minimum)
        logger.info("能力预检完成: capability=%s ok=false error_type=parameter_error", capability)
        return result

    status = minimum.get("capability_status", {})
    missing_capabilities = [] if _capability_available(status, capability) else [capability]
    missing_providers: list[str] = []
    if provider:
        availability = _provider_availability(provider, capability)
        if not availability.get("eligible"):
            missing_providers.append(provider)
    error_parts: list[str] = []
    if missing_capabilities:
        error_parts.append(f"{capability} 缺少必需能力: {capability}")
    if missing_providers:
        error_parts.append(f"{capability} 缺少必需 provider: {provider}")
    error = "; ".join(error_parts)
    result = {
        "ok": not missing_capabilities and not missing_providers,
        "command": capability,
        "error_type": "config_error" if error else "",
        "error": error,
        "required_capabilities": [capability],
        "required_capability_groups": [[capability]],
        "missing_capabilities": missing_capabilities,
        "required_providers": [provider] if provider else [],
        "missing_providers": missing_providers,
        "optional_missing": [],
        "optional_missing_capabilities": [],
        "source_only": False,
        "degraded": False,
        "degraded_reason": "",
        "capability_status": status,
    }
    result["metadata"] = _command_capability_metadata(result, minimum)
    logger.info(
        "能力预检完成: capability=%s ok=%s missing=%s providers=%s",
        capability,
        result["ok"],
        result["missing_capabilities"],
        result["missing_providers"],
    )
    return result


def _command_capability_preflight(command: str, *, response_mode: str = "") -> dict[str, Any]:
    """
    /*
     * ==============================================================================
     * 步骤4：执行命令能力预检
     * ==============================================================================
     * 目标：让 provider-specific 命令复用同一套 profile 诊断和能力错误契约。
     * 数据源：minimum profile、capability status 和命令能力矩阵。
     * 操作：
     * 1) 读取一次当前 profile 诊断结果。
     * 2) 校验命令能力和明确要求的 provider，不调用网络。
     * 3) 返回可附加到成功结果或配置错误的稳定元数据。
     * ==============================================================================
     */
    """
    logger.info("开始执行命令能力预检: command=%s", command)
    minimum = validate_minimum_profile()
    if minimum.get("error_type") == "parameter_error":
        result = {
            "ok": False,
            "command": command,
            "error_type": "parameter_error",
            "error": minimum.get("error", "Invalid minimum profile"),
            "metadata": {
                "command": command,
                "minimum_profile": "",
                "minimum_profile_ok": False,
                "required_capabilities": [],
                "required_capability_groups": [],
                "missing_capabilities": [],
                "required_providers": [],
                "missing_providers": [],
                "optional_missing": [],
                "optional_missing_capabilities": [],
                "degraded": False,
                "degraded_reason": "",
                "capability_status": {},
            },
        }
        logger.info("命令能力预检完成: command=%s ok=false error_type=parameter_error", command)
        return result

    command_result = validate_command_capabilities(
        command,
        minimum_profile=minimum.get("profile", ""),
        response_mode=response_mode,
        capability_status=minimum.get("capability_status", {}),
    )
    result = {
        **command_result,
        "metadata": _command_capability_metadata(command_result, minimum),
    }
    logger.info(
        "命令能力预检完成: command=%s ok=%s missing=%s providers=%s",
        command,
        result["ok"],
        result.get("missing_capabilities", []),
        result.get("missing_providers", []),
    )
    return result

def _command_capability_failure(
    preflight: dict[str, Any],
    start: float,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    /*
     * ==============================================================================
     * 步骤5：构造命令能力错误
     * ==============================================================================
     * 目标：在 provider 调用前返回统一 config_error/parameter_error 结果。
     * 数据源：_command_capability_preflight 的错误和元数据。
     * 操作：
     * 1) 保留 required/missing capability 和 provider 字段。
     * 2) 写入稳定错误类型和错误文本。
     * 3) 记录当前命令耗时，并允许补充 url 等命令参数。
     * ==============================================================================
     */
    """
    logger.info("开始构造命令能力错误: command=%s", preflight.get("command", ""))
    result: dict[str, Any] = {
        "ok": False,
        "error_type": preflight.get("error_type", "config_error"),
        "error": preflight.get("error", "命令缺少必需能力"),
        **(preflight.get("metadata") or {}),
        "elapsed_ms": _elapsed_ms(start),
    }
    if extra:
        result.update(extra)
    logger.info(
        "命令能力错误构造完成: command=%s error_type=%s",
        result.get("command", ""),
        result.get("error_type", ""),
    )
    return result

def capabilities() -> dict[str, Any]:
    """
    =================================================================================
    步骤2：生成公共能力清单
    =================================================================================
    目标：让任意客户端、Extension、Adapter 或脚本在执行前发现当前能力。
    数据源：Provider registry、配置状态和固定 CLI 命令集合。
    操作：
    1) 只返回 provider id 和配置状态，不返回凭据。
    2) 同时暴露可用命令、profile 和输出格式。
    3) 保留缺失能力，避免客户端误以为系统拥有未配置功能。
    """
    logger.info("开始生成公共能力清单")
    status = get_capability_status()
    try:
        active_minimum_profile = config.minimum_profile
    except ValueError as exc:
        result = {
            "ok": False,
            "error_type": "parameter_error",
            "error": str(exc),
            "capabilities": {},
        }
        logger.info("公共能力清单生成失败: error_type=parameter_error")
        return result
    public_capabilities: dict[str, dict[str, Any]] = {}
    for name, item in status.items():
        configured = list(item.get("configured") or [])
        public_capabilities[name] = {
            "configured": bool(configured),
            "providers": configured,
            "fallback_providers": list(item.get("fallback_chain") or []),
            "provider_status": list(item.get("provider_status") or []),
            "disabled_providers": list(item.get("disabled") or []),
            "experimental": bool(item.get("experimental", False)),
        }
    command_capabilities = {
        command: {
            "required_capabilities": list(matrix.get("required", ())),
            "required_providers": list(matrix.get("required_providers", ())),
            "optional_capabilities": list(matrix.get("optional", ())),
            "source_only_profiles": ["lite", "off"] if command == "search" else [],
            "source_only_response_mode": "evidence" if command == "search" else "",
        }
        for command, matrix in COMMAND_CAPABILITY_MATRIX.items()
    }
    result = {
        "ok": True,
        "commands": {
            "search": True,
            "fetch": True,
            "map": True,
            "route": True,
            "research": True,
            "doctor": True,
            "capabilities": True,
        },
        "capabilities": public_capabilities,
        "profiles": list(PROFILE_NAMES),
        "minimum_profiles": list(CAPABILITY_PROFILE_NAMES),
        "active_minimum_profile": active_minimum_profile,
        "command_capabilities": command_capabilities,
        "output_formats": ["json", "markdown", "content"],
    }
    logger.info("公共能力清单生成完成: profile=%s", active_minimum_profile)
    return result

def _parse_provider_filter(providers: str = "auto") -> set[str] | None:
    if not providers or providers.strip().lower() == "auto":
        return None
    return {item.strip().lower() for item in providers.split(",") if item.strip()}

def _provider_allowed(provider_id: str, provider_filter: set[str] | None) -> bool:
    if provider_filter is None:
        return True
    aliases = MAIN_SEARCH_PROVIDER_ALIASES.get(provider_id, {provider_id})
    return bool(provider_filter.intersection(aliases))

def _configured_main_search_provider_ids() -> list[str]:
    if config.model_routes_configured:
        return list(dict.fromkeys(route["provider"] for route in config.model_routes))
    return [provider for provider in _provider_chain("main_search") if _provider_configured(provider)]

def _main_search_provider_configs(model_override: str = "", providers: str = "auto") -> list[dict[str, Any]]:
    provider_filter = _parse_provider_filter(providers)
    if config.model_routes_configured:
        route_configs: list[dict[str, Any]] = []
        for route in config.model_routes:
            provider = route["provider"]
            if not _provider_allowed(provider, provider_filter):
                continue
            route_config: dict[str, Any] = {
                "provider": provider,
                "mode": "xai-responses" if provider == "xai-responses" else "chat-completions",
                "api_url": route["api_url"],
                "api_key": route["api_key"],
                "model": model_override or route["model"],
                "route_id": route["id"],
                "source": "SMART_SEARCH_MODEL_ROUTES",
            }
            if provider == "xai-responses":
                route_config["tools"] = list(route.get("tools") or [])
            else:
                route_config["fallback_models"] = [] if model_override else list(route.get("fallback_models") or [])
                route_config["stream"] = bool(route.get("stream", False))
                route_config["tools"] = []
            route_configs.append(route_config)
        return route_configs

    by_provider: dict[str, dict[str, Any]] = {}

    if config.xai_api_key:
        by_provider["xai-responses"] = {
            "provider": "xai-responses",
            "mode": "xai-responses",
            "api_url": config.xai_api_url,
            "api_key": config.xai_api_key,
            "model": model_override or config.xai_model,
            "tools": config.parse_xai_tools(config.xai_tools_raw),
            "source": "XAI_*",
        }

    if config.openai_compatible_api_url and config.openai_compatible_api_key:
        by_provider["openai-compatible"] = {
            "provider": "openai-compatible",
            "mode": "chat-completions",
            "api_url": config.openai_compatible_api_url,
            "api_key": config.openai_compatible_api_key,
            "model": model_override or config.openai_compatible_model,
            "fallback_models": [] if model_override else config.openai_compatible_fallback_models,
            "stream": config.openai_compatible_stream,
            "tools": [],
            "source": "OPENAI_COMPATIBLE_*",
        }

    return [
        by_provider[provider]
        for provider in MAIN_SEARCH_FALLBACK_CHAIN
        if provider in by_provider and _provider_allowed(provider, provider_filter)
    ]

def _main_search_providers(provider_configs: list[dict[str, Any]], fallback: str) -> list[Any]:
    selected = provider_configs if fallback != "off" else provider_configs[:1]
    providers: list[Any] = []
    for provider_config in selected:
        if provider_config["provider"] == "xai-responses":
            providers.append(
                XAIResponsesSearchProvider(
                    provider_config["api_url"],
                    provider_config["api_key"],
                    provider_config["model"],
                    provider_config["tools"],
                )
            )
        else:
            providers.append(
                OpenAICompatibleSearchProvider(
                    provider_config["api_url"],
                    provider_config["api_key"],
                    provider_config["model"],
                    provider_config.get("stream", False),
                )
            )
    return providers

async def route(
    query: str,
    validation: str = "",
    mode: str = "",
    allow_remote: bool = True,
) -> dict[str, Any]:
    start = time.time()
    try:
        validation_level = (validation or config.validation_level).strip().lower()
        if validation_level not in config._ALLOWED_VALIDATION_LEVELS:
            raise ValueError(f"Invalid validation level: {validation_level}")
        route_result = await IntentRouter(config).route(
            query,
            validation_level=validation_level,
            mode=mode,
            allow_remote=allow_remote,
        )
    except ValueError as e:
        return {
            "ok": False,
            "query": query,
            "error_type": "parameter_error",
            "error": str(e),
            "elapsed_ms": _elapsed_ms(start),
        }
    data = route_result.to_dict()
    # ================================================================================
    # 步骤3：补充 route 能力诊断
    # ================================================================================
    # 目标：route 只做本地/可选远程路由，不因缺少 provider 阻断结果。
    # 数据源：IntentRouter required_capabilities、provider registry 和 profile 诊断。
    # 操作：
    # 1) 计算路由建议能力当前是否可用。
    # 2) 缺失能力写入 degraded_reason，不改变 route 的成功语义。
    # 3) 保留 minimum_profile_ok 作为诊断字段，而不是 route 的执行门槛。
    minimum = validate_minimum_profile()
    capability_status = minimum.get("capability_status") or get_capability_status()
    routed_capabilities = list(route_result.required_capabilities)
    missing_capabilities = [
        capability
        for capability in routed_capabilities
        if not _capability_available(capability_status, capability)
    ]
    degraded_reasons = [str(data.get("degraded_reason"))] if data.get("degraded_reason") else []
    if missing_capabilities:
        degraded_reasons.append(f"路由建议能力不可用: {', '.join(missing_capabilities)}")
    logger.info("route 能力诊断完成: missing=%s", missing_capabilities)
    router_status = intent_router_status()
    preset_fields = {
        key: router_status.get(key)
        for key in (
            "embedding_preset_id",
            "embedding_preset_model",
            "embedding_preset_api_url",
            "embedding_preset_threshold",
            "embedding_preset_margin",
            "embedding_preset_threshold_matches",
            "embedding_preset_margin_matches",
            "embedding_preset_recommended",
            "embedding_preset_recommendation",
            "embedding_preset_commands",
        )
        if key in router_status
    }
    data.update(
        {
            "ok": True,
            "query": query,
            "validation_level": validation_level,
            "executed_search": False,
            "provider_selection": "not_executed",
            "required_capabilities": routed_capabilities,
            "missing_capabilities": missing_capabilities,
            "minimum_profile": minimum.get("profile", ""),
            "minimum_profile_ok": bool(minimum.get("ok", False)),
            "capability_status": capability_status,
            "degraded": bool(data.get("degraded") or missing_capabilities),
            "degraded_reason": "; ".join(degraded_reasons),
            "embedding_model": router_status.get("embedding_model", ""),
            "embedding_threshold": router_status.get("embedding_threshold", ""),
            "embedding_margin": router_status.get("embedding_margin", ""),
            "embedding_threshold_source": router_status.get("embedding_threshold_source", ""),
            "embedding_margin_source": router_status.get("embedding_margin_source", ""),
            "elapsed_ms": _elapsed_ms(start),
            **preset_fields,
        }
    )
    return data

class _CalibrationConfigProxy:
    def __init__(self, base_config: Any, model: str, threshold: float, margin: float):
        self._base_config = base_config
        self._model = model
        self._threshold = threshold
        self._margin = margin

    @property
    def intent_router_mode(self) -> str:
        return "hybrid"

    @property
    def intent_embedding_model(self) -> str:
        return self._model

    @property
    def intent_embedding_threshold(self) -> float:
        return self._threshold

    @property
    def intent_embedding_margin(self) -> float:
        return self._margin

    def get_config_source(self, key: str) -> str:
        if key in {"INTENT_EMBEDDING_MODEL", "INTENT_EMBEDDING_THRESHOLD", "INTENT_EMBEDDING_MARGIN"}:
            return "calibration"
        getter = getattr(self._base_config, "get_config_source", None)
        if callable(getter):
            return str(getter(key))
        return "default"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base_config, name)

def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out

def _parse_calibration_models(models: str = "") -> list[str]:
    if models.strip():
        return _dedupe_preserve_order([item.strip() for item in models.split(",")])
    defaults = list(DEFAULT_ROUTE_CALIBRATION_MODELS)
    current = config.intent_embedding_model
    if current:
        defaults.append(current)
    return _dedupe_preserve_order(defaults)

def _configured_embedding_threshold() -> float:
    try:
        return config.intent_embedding_threshold
    except ValueError:
        return DEFAULT_SEMANTIC_CONFIDENCE_THRESHOLD

def _configured_embedding_margin() -> float:
    try:
        return config.intent_embedding_margin
    except ValueError:
        return DEFAULT_SEMANTIC_CONFIDENCE_MARGIN

def _route_calibration_dataset() -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for label, queries in ROUTE_CALIBRATION_QUERIES.items():
        expected = [] if label == "none" else [label]
        for index, query_text in enumerate(queries, 1):
            examples.append(
                {
                    "id": f"{label}-{index:02d}",
                    "query": query_text,
                    "expected_capabilities": list(expected),
                    "expected_label": label,
                }
            )
    return examples

async def _embed_in_batches(router: IntentRouter, inputs: list[str], batch_size: int = 64) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for start_index in range(0, len(inputs), batch_size):
        embeddings.extend(await router._embed(inputs[start_index : start_index + batch_size]))
    return embeddings

def _label_present(capabilities: set[str], label: str) -> bool:
    if label == "none":
        return not capabilities
    return label in capabilities

def _macro_f1(expected: list[set[str]], predicted: list[set[str]], labels: list[str]) -> dict[str, Any]:
    per_label: dict[str, float] = {}
    for label in labels:
        true_positive = 0
        false_positive = 0
        false_negative = 0
        for expected_caps, predicted_caps in zip(expected, predicted):
            expected_has = _label_present(expected_caps, label)
            predicted_has = _label_present(predicted_caps, label)
            if expected_has and predicted_has:
                true_positive += 1
            elif not expected_has and predicted_has:
                false_positive += 1
            elif expected_has and not predicted_has:
                false_negative += 1
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        per_label[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    macro = sum(per_label.values()) / len(labels) if labels else 0.0
    return {
        "macro_f1": round(macro, 4),
        "per_label_f1": {label: round(score, 4) for label, score in per_label.items()},
    }

def _confusion_label(capabilities: set[str]) -> str:
    ordered = _ordered_capabilities(capabilities)
    if not ordered:
        return "none"
    if len(ordered) == 1:
        return ordered[0]
    return "+".join(ordered)

def _confusion_matrix(expected: list[set[str]], predicted: list[set[str]]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for expected_caps, predicted_caps in zip(expected, predicted):
        actual = _confusion_label(expected_caps)
        guessed = _confusion_label(predicted_caps)
        matrix.setdefault(actual, {})
        matrix[actual][guessed] = matrix[actual].get(guessed, 0) + 1
    return matrix

def _semantic_predictions(
    records: list[dict[str, Any]],
    threshold: float,
    margin: float,
) -> tuple[list[set[str]], list[dict[str, Any]]]:
    predictions: list[set[str]] = []
    summaries: list[dict[str, Any]] = []
    for record in records:
        summary = _semantic_summary(record["scores"], threshold, margin)
        summaries.append(summary)
        if summary["passed_threshold"] and summary["passed_margin"]:
            predictions.append({str(summary["top_capability"])})
        else:
            predictions.append(set())
    return predictions, summaries

def _candidate_thresholds(records: list[dict[str, Any]]) -> list[float]:
    values = {round(index / 100, 2) for index in range(50, 96)}
    values.add(round(_configured_embedding_threshold(), 2))
    for record in records:
        summary = _semantic_summary(record["scores"], 0.0, 0.0)
        top_score = float(summary["top_score"])
        for delta in (-0.02, -0.01, 0.0, 0.01, 0.02):
            value = max(0.0, min(1.0, top_score + delta))
            values.add(round(value, 3))
    return sorted(values)

def _candidate_margins(records: list[dict[str, Any]]) -> list[float]:
    values = {round(index / 100, 2) for index in range(0, 21)}
    values.add(round(_configured_embedding_margin(), 2))
    for record in records:
        summary = _semantic_summary(record["scores"], 0.0, 0.0)
        score_margin = float(summary["margin"])
        for delta in (-0.02, -0.01, 0.0, 0.01, 0.02):
            value = max(0.0, min(1.0, score_margin + delta))
            values.add(round(value, 3))
    return sorted(values)

def _select_semantic_parameters(
    records: list[dict[str, Any]],
    expected: list[set[str]],
    labels: list[str],
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    thresholds = _candidate_thresholds(records)
    margins = _candidate_margins(records)
    for threshold in thresholds:
        for margin in margins:
            predictions, _ = _semantic_predictions(records, threshold, margin)
            metrics = _macro_f1(expected, predictions, labels)
            failures = sum(1 for left, right in zip(expected, predictions) if left != right)
            candidate = {
                "threshold": threshold,
                "margin": margin,
                "macro_f1": metrics["macro_f1"],
                "per_label_f1": metrics["per_label_f1"],
                "failures": failures,
            }
            if best is None:
                best = candidate
                continue
            current_key = (candidate["macro_f1"], -candidate["failures"], candidate["threshold"], candidate["margin"])
            best_key = (best["macro_f1"], -best["failures"], best["threshold"], best["margin"])
            if current_key > best_key:
                best = candidate
    return best or {
        "threshold": _configured_embedding_threshold(),
        "margin": _configured_embedding_margin(),
        "macro_f1": 0.0,
        "per_label_f1": {},
        "failures": len(records),
    }

def _representative_failures(
    records: list[dict[str, Any]],
    expected: list[set[str]],
    predicted: list[set[str]],
    summaries: list[dict[str, Any]],
    limit: int = 12,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for record, expected_caps, predicted_caps, summary in zip(records, expected, predicted, summaries):
        if expected_caps == predicted_caps:
            continue
        rounded_scores = {
            capability: round(float(score), 4)
            for capability, score in sorted(record["scores"].items(), key=lambda item: item[0])
        }
        failures.append(
            {
                "id": record["case"]["id"],
                "query": record["case"]["query"],
                "expected": _confusion_label(expected_caps),
                "predicted": _confusion_label(predicted_caps),
                "top_capability": summary["top_capability"],
                "top_score": round(float(summary["top_score"]), 4),
                "second_score": round(float(summary["second_score"]), 4),
                "margin": round(float(summary["margin"]), 4),
                "scores": rounded_scores,
            }
        )
        if len(failures) >= limit:
            break
    return failures

async def _full_route_predictions(
    records: list[dict[str, Any]],
    threshold: float,
    margin: float,
    model: str,
) -> tuple[list[set[str]], list[dict[str, Any]], list[dict[str, Any]]]:
    proxy = _CalibrationConfigProxy(config, model, threshold, margin)
    router = IntentRouter(proxy)
    predictions: list[set[str]] = []
    summaries: list[dict[str, Any]] = []
    component_failures: list[dict[str, Any]] = []
    for record in records:
        query_text = record["case"]["query"]
        rules = build_rules_route(query_text, validation_level="balanced", mode="hybrid")
        merged_caps = set(rules.required_capabilities)
        summary = _semantic_summary(record["scores"], threshold, margin)
        summaries.append(summary)
        semantic = {"scores": record["scores"], **summary}
        if summary["passed_threshold"] and summary["passed_margin"]:
            merged_caps.add(str(summary["top_capability"]))
        if router._classifier_configured():
            try:
                classifier = await router._classifier_route(query_text, rules.to_dict(), semantic)
                for capability in classifier.get("required_capabilities") or []:
                    if capability in ROUTABLE_CAPABILITIES and _classifier_can_add_capability(capability, rules):
                        merged_caps.add(str(capability))
            except Exception as exc:
                if len(component_failures) < 10:
                    component_failures.append(
                        {
                            "id": record["case"]["id"],
                            "query": query_text,
                            "component": "classifier",
                            "error": str(exc),
                        }
                    )
        predictions.append(set(_ordered_capabilities(merged_caps)))
    return predictions, summaries, component_failures

def _model_failure_result(model: str, start: float, error: str, error_type: str = "provider_error") -> dict[str, Any]:
    return {
        "model": model,
        "ok": False,
        "availability": "failed",
        "error_type": error_type,
        "error": sanitize_text(error),
        "dimension": 0,
        "latency_ms": 0.0,
        "semantic_macro_f1": 0.0,
        "full_route_macro_f1": 0.0,
        "recommended_threshold": None,
        "recommended_margin": None,
        "confusion_matrix": {},
        "semantic_failures": [],
        "full_route_failures": [],
        "elapsed_ms": _elapsed_ms(start),
    }

async def _evaluate_calibration_model(model: str, dataset: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    start = time.time()
    proxy = _CalibrationConfigProxy(
        config,
        model,
        _configured_embedding_threshold(),
        _configured_embedding_margin(),
    )
    router = IntentRouter(proxy)
    if not router._embeddings_configured():
        return _model_failure_result(
            model,
            start,
            "INTENT_EMBEDDING_API_URL and INTENT_EMBEDDING_API_KEY must be configured before calibration.",
            "config_error",
        )

    utterances: list[tuple[str, str]] = []
    for capability, examples in CAPABILITY_UTTERANCES.items():
        for example in examples:
            utterances.append((capability, example))
    inputs = [item["query"] for item in dataset] + [example for _capability, example in utterances]
    embed_start = time.time()
    embeddings = await _embed_in_batches(router, inputs)
    latency_ms = _elapsed_ms(embed_start)
    if len(embeddings) != len(inputs):
        return _model_failure_result(
            model,
            start,
            f"Embedding response returned {len(embeddings)} rows for {len(inputs)} inputs.",
        )
    dimension = len(embeddings[0]) if embeddings else 0
    query_embeddings = embeddings[: len(dataset)]
    utterance_embeddings = embeddings[len(dataset) :]

    records: list[dict[str, Any]] = []
    for item, query_embedding in zip(dataset, query_embeddings):
        scores: dict[str, float] = {}
        for index, (capability, _example) in enumerate(utterances):
            score = _cosine_similarity(query_embedding, utterance_embeddings[index])
            scores[capability] = max(scores.get(capability, 0.0), score)
        records.append({"case": item, "scores": scores})

    expected = [set(item["expected_capabilities"]) for item in dataset]
    best = _select_semantic_parameters(records, expected, labels)
    semantic_predictions, semantic_summaries = _semantic_predictions(records, best["threshold"], best["margin"])
    semantic_metrics = _macro_f1(expected, semantic_predictions, labels)
    full_predictions, full_summaries, component_failures = await _full_route_predictions(
        records,
        best["threshold"],
        best["margin"],
        model,
    )
    full_metrics = _macro_f1(expected, full_predictions, labels)

    return {
        "model": model,
        "ok": True,
        "availability": "ok",
        "dimension": dimension,
        "latency_ms": latency_ms,
        "semantic_macro_f1": semantic_metrics["macro_f1"],
        "semantic_per_label_f1": semantic_metrics["per_label_f1"],
        "full_route_macro_f1": full_metrics["macro_f1"],
        "full_route_per_label_f1": full_metrics["per_label_f1"],
        "recommended_threshold": round(float(best["threshold"]), 3),
        "recommended_margin": round(float(best["margin"]), 3),
        "recommendation_basis": "semantic_macro_f1",
        "confusion_matrix": _confusion_matrix(expected, semantic_predictions),
        "full_route_confusion_matrix": _confusion_matrix(expected, full_predictions),
        "semantic_failures": _representative_failures(records, expected, semantic_predictions, semantic_summaries),
        "full_route_failures": _representative_failures(records, expected, full_predictions, full_summaries),
        "component_failures": component_failures,
        "elapsed_ms": _elapsed_ms(start),
    }

async def route_calibrate(models: str = "") -> dict[str, Any]:
    start = time.time()
    selected_models = _parse_calibration_models(models)
    dataset = _route_calibration_dataset()
    labels = [*sorted(ROUTABLE_CAPABILITIES), "none"]
    results: list[dict[str, Any]] = []
    for model in selected_models:
        try:
            results.append(await _evaluate_calibration_model(model, dataset, labels))
        except Exception as exc:
            results.append(_model_failure_result(model, start, str(exc)))

    successful = [item for item in results if item.get("ok")]
    failed_models = [item.get("model") for item in results if not item.get("ok")]
    recommended = None
    if successful:
        recommended = max(
            successful,
            key=lambda item: (
                float(item.get("semantic_macro_f1") or 0.0),
                float(item.get("full_route_macro_f1") or 0.0),
                -float(item.get("latency_ms") or 0.0),
            ),
        )
    ok = bool(successful)
    data: dict[str, Any] = {
        "ok": ok,
        "metric": "semantic_macro_f1",
        "primary_metric": "semantic_macro_f1",
        "full_route_metric_role": "validation",
        "models": selected_models,
        "model_results": results,
        "failed_models": failed_models,
        "dataset_size": len(dataset),
        "dataset_counts": {label: len(queries) for label, queries in ROUTE_CALIBRATION_QUERIES.items()},
        "capabilities": sorted(ROUTABLE_CAPABILITIES),
        "labels": labels,
        "default_threshold": _configured_embedding_threshold(),
        "default_margin": _configured_embedding_margin(),
        "embedding_model": config.intent_embedding_model,
        "recommended_model": recommended.get("model") if recommended else "",
        "recommended_threshold": recommended.get("recommended_threshold") if recommended else None,
        "recommended_margin": recommended.get("recommended_margin") if recommended else None,
        "elapsed_ms": _elapsed_ms(start),
    }
    if ok:
        data["error_type"] = ""
        data["error"] = ""
    else:
        error_types = {
            str(item.get("error_type") or "provider_error")
            for item in results
            if not item.get("ok")
        }
        data["error_type"] = "config_error" if "config_error" in error_types else "provider_error"
        data["error"] = "No embedding model could be calibrated. See model_results for per-model errors."
    return data

__all__ = [name for name in globals() if not name.startswith("__")]
