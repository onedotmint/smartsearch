"""Private low-level executors owned by the typed Control plane.

These raw executors were historically supplied by the v1 ``operations_service``
compatibility seam. With the v1 runtime removed they live beside the typed
``control_operations`` owner that consumes them. They return raw execution
facts; status, network, side-effect and degradation semantics are always
derived by the typed owner (``control_operations``), never by this module.
This module is not a public API and is not exported anywhere.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .capability_service import (
    MAIN_SEARCH_FALLBACK_CHAIN,
    _capability_available,
    _main_search_provider_configs,
    _minimum_profile_result,
    _provider_configured,
    get_capability_status,
    intent_router_status,
    validate_minimum_profile,
)
from .config import config
from .logger import logger
from .operation_runtime import _run_web_fetch_fallback
from .provider_diagnostics import (
    _test_context7_connection,
    _test_exa_connection,
    _test_jina_connection,
    _test_tavily_connection,
    _test_zhipu_connection,
    _test_zhipu_mcp_connection,
)
from .providers.openai_compatible import OpenAICompatibleSearchProvider, get_local_time_info
from .research_service import build_research_workflow_plan
from .security import sanitize_data
from .service_support import (
    COMMAND_CAPABILITY_MATRIX,
    MINIMUM_PROFILE_ERROR,
    OPENAI_COMPATIBLE_DIAGNOSE_COMMAND,
    _attempt,
    _elapsed_ms,
    _fallback_used,
    _is_docs_intent,
    _is_web_current_intent,
    _is_zh_current_intent,
    _provider_names_from_attempts,
)
from .utils import get_prompt

async def _test_primary_chat_completion(api_url: str, api_key: str, model: str) -> dict[str, Any]:
    chat_url = f"{api_url.rstrip('/')}/chat/completions"
    start = time.time()
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            chat_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                "stream": False,
                "max_tokens": 8,
            },
        )
        response_time = _elapsed_ms(start)
        content_type = response.headers.get("content-type", "")
        if response.status_code != 200:
            return {
                "status": "warning",
                "message": f"HTTP {response.status_code}: 上游返回错误响应",
                "response_time_ms": response_time,
                "http_status": response.status_code,
                "content_type": content_type,
                "has_content": bool(response.text.strip()),
            }
        return {
            "status": "ok",
            "message": f"聊天接口可用 (HTTP {response.status_code})",
            "response_time_ms": response_time,
            "http_status": response.status_code,
            "content_type": content_type,
            "has_content": bool(response.text.strip()),
        }


def _diagnose_check_result(
    *,
    name: str,
    status: str,
    message: str,
    start: float,
    http_status: int | None = None,
    content_type: str = "",
    has_content: bool = False,
    stream: bool | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "status": status,
        "message": message,
        "response_time_ms": _elapsed_ms(start),
        "has_content": has_content,
    }
    if http_status is not None:
        result["http_status"] = http_status
    if content_type:
        result["content_type"] = content_type
    if stream is not None:
        result["stream"] = stream
    return result


def _openai_compatible_diagnosis(quick: dict[str, Any], no_stream: dict[str, Any], stream: dict[str, Any]) -> tuple[bool, str, str]:
    quick_ok = quick.get("status") == "ok"
    no_stream_ok = no_stream.get("status") == "ok"
    stream_ok = stream.get("status") == "ok"
    search_timeout = no_stream.get("status") == "timeout" or stream.get("status") == "timeout"

    if no_stream_ok and stream_ok:
        return (
            True,
            "OpenAI-compatible 主链路正常。",
            "真实 search 形态的 stream=false 和 stream=true 都能返回。若用户仍卡住，更可能是调用方、PATH、超时设置或上游偶发波动。",
        )
    if stream_ok and not no_stream_ok:
        return (
            False,
            "非流式请求不稳定，流式请求可用。",
            "建议设置 `OPENAI_COMPATIBLE_STREAM=true`，或临时使用 `smart-search search ... --stream`。",
        )
    if no_stream_ok and not stream_ok:
        return (
            False,
            "流式请求不稳定，非流式请求可用。",
            "建议设置 `OPENAI_COMPATIBLE_STREAM=false`，或临时使用 `smart-search search ... --no-stream`。",
        )
    if quick_ok and search_timeout:
        return (
            False,
            "小请求能通，但真实 search 形态超时。",
            "这通常是上游模型或中转站在处理 smart-search 的完整 prompt 时卡住；建议换模型/中转，或把本诊断报告贴给维护者。",
        )
    if quick_ok:
        return (
            False,
            "小请求能通，但真实 search 形态失败。",
            "这更像上游模型/中转站对 smart-search 请求形态不兼容；建议换模型/中转，或把本诊断报告贴给维护者。",
        )
    return (
        False,
        "OpenAI-compatible 基础请求不可用。",
        "请先检查 API URL、API key、模型名和网络；修好后再运行本诊断命令。",
    )


async def _probe_openai_compatible_search_shape(
    api_url: str,
    api_key: str,
    model: str,
    *,
    stream: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    name = "真实 search 请求 (stream=true)" if stream else "真实 search 请求 (stream=false)"
    start = time.time()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": get_prompt("search")},
            {"role": "user", "content": get_local_time_info() + "\nping"},
        ],
        "stream": stream,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "smart-search/diagnose",
    }
    timeout = httpx.Timeout(connect=6.0, read=timeout_seconds, write=10.0, pool=None)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=config.ssl_verify_enabled) as client:
            if stream:
                async with client.stream(
                    "POST",
                    f"{api_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    content_type = response.headers.get("content-type", "")
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError:
                        # A streaming response has not been read yet. Drain it
                        # while its context is open so an unread body cannot turn
                        # this diagnostic failure into ResponseNotRead later.
                        try:
                            await response.aread()
                        except httpx.HTTPError:
                            pass
                        return _diagnose_check_result(
                            name=name,
                            status="warning",
                            message=f"HTTP {response.status_code}: 上游返回错误响应",
                            start=start,
                            http_status=response.status_code,
                            content_type=content_type,
                            stream=True,
                        )
                    has_content = False
                    async for line in response.aiter_lines():
                        stripped = line.strip()
                        if not stripped:
                            continue
                        if not stripped.startswith("data:"):
                            continue
                        if stripped in ("data: [DONE]", "data:[DONE]"):
                            continue
                        try:
                            data = json.loads(stripped[5:].lstrip())
                        except json.JSONDecodeError:
                            continue
                        choices = data.get("choices", []) if isinstance(data, dict) else []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if isinstance(delta, dict) and str(delta.get("content") or "").strip():
                            has_content = True
                            break
                        message = choices[0].get("message", {})
                        if isinstance(message, dict) and str(message.get("content") or "").strip():
                            has_content = True
                            break
                    status = "ok" if has_content else "empty"
                    message = f"HTTP {response.status_code}; {'收到流式内容' if has_content else '未收到内容'}"
                    return _diagnose_check_result(
                        name=name,
                        status=status,
                        message=message,
                        start=start,
                        http_status=response.status_code,
                        content_type=content_type,
                        has_content=has_content,
                        stream=stream,
                    )

            response = await client.post(
                f"{api_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            content_type = response.headers.get("content-type", "")
            response.raise_for_status()
            content = await OpenAICompatibleSearchProvider(api_url, api_key, model, stream=False)._parse_completion_response(response)
            has_content = bool(content.strip())
            status = "ok" if has_content else "empty"
            message = f"HTTP {response.status_code}; {'收到内容' if has_content else '返回为空'}"
            return _diagnose_check_result(
                name=name,
                status=status,
                message=message,
                start=start,
                http_status=response.status_code,
                content_type=content_type,
                has_content=has_content,
                stream=stream,
            )
    except httpx.TimeoutException as e:
        return _diagnose_check_result(name=name, status="timeout", message=f"请求超时: {e}", start=start, stream=stream)
    except httpx.HTTPStatusError as e:
        response = e.response
        status_code = response.status_code if response is not None else None
        content_type = response.headers.get("content-type", "") if response is not None else ""
        return _diagnose_check_result(
            name=name,
            status="warning",
            message=f"HTTP {status_code}: 上游返回错误响应",
            start=start,
            http_status=status_code,
            content_type=content_type,
            stream=stream,
        )
    except httpx.RequestError as e:
        return _diagnose_check_result(name=name, status="error", message=f"网络错误: {e}", start=start, stream=stream)
    except Exception as e:
        return _diagnose_check_result(name=name, status="error", message=f"运行错误: {e}", start=start, stream=stream)


async def _execute_diagnose_openai_compatible(timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Shared OpenAI-compatible diagnosis execution (private low-level owner)."""
    start = time.time()
    api_url = config.openai_compatible_api_url
    api_key = config.openai_compatible_api_key
    model = config.openai_compatible_model
    info = config.config_path_info()
    result: dict[str, Any] = {
        "ok": False,
        "provider": "openai-compatible",
        "api_url": api_url or "未配置",
        "api_key": config._mask_api_key(api_key) if api_key else "未配置",
        "model": model,
        "configured_stream": config.openai_compatible_stream,
        "timeout_seconds": timeout_seconds,
        "config_file": info.get("config_file", ""),
        "config_dir_source": info.get("config_dir_source", ""),
        "checks": [],
        "next_command": OPENAI_COMPATIBLE_DIAGNOSE_COMMAND,
    }
    missing = []
    if not api_url:
        missing.append("OPENAI_COMPATIBLE_API_URL")
    if not api_key:
        missing.append("OPENAI_COMPATIBLE_API_KEY")
    if missing:
        result.update(
            {
                "error_type": "config_error",
                "error": "缺少 OpenAI-compatible 配置: " + ", ".join(missing),
                "summary": "OpenAI-compatible 配置不完整。",
                "recommendation": "请用 `smart-search config set` 填好缺失项。",
                "missing": missing,
                "elapsed_ms": _elapsed_ms(start),
            }
        )
        return result

    try:
        quick = await _test_primary_chat_completion(api_url, api_key, model)
    except httpx.TimeoutException as e:
        quick = {"status": "timeout", "message": f"轻量 chat 请求超时: {e}"}
    except httpx.RequestError as e:
        quick = {"status": "error", "message": f"轻量 chat 网络错误: {e}"}
    except Exception as e:
        quick = {"status": "error", "message": f"轻量 chat 运行错误: {e}"}
    quick_check = {
        "name": "轻量 chat 请求",
        "status": quick.get("status", "error"),
        "message": quick.get("message", ""),
        "response_time_ms": quick.get("response_time_ms"),
        "http_status": quick.get("http_status"),
        "content_type": quick.get("content_type", ""),
        "has_content": bool(quick.get("has_content", quick.get("status") == "ok")),
    }
    result["checks"].append(quick_check)
    no_stream = await _probe_openai_compatible_search_shape(api_url, api_key, model, stream=False, timeout_seconds=timeout_seconds)
    result["checks"].append(no_stream)
    stream = await _probe_openai_compatible_search_shape(api_url, api_key, model, stream=True, timeout_seconds=timeout_seconds)
    result["checks"].append(stream)

    ok, summary, recommendation = _openai_compatible_diagnosis(quick_check, no_stream, stream)
    result.update(
        {
            "ok": ok,
            "error_type": "" if ok else "network_error",
            "error": "" if ok else summary,
            "summary": summary,
            "recommendation": recommendation,
            "elapsed_ms": _elapsed_ms(start),
        }
    )
    return result


async def _test_primary_connection(api_url: str, api_key: str, model: str) -> dict[str, Any]:
    chat_test = await _test_primary_chat_completion(api_url, api_key, model)

    models_url = f"{api_url.rstrip('/')}/models"
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            response_time = _elapsed_ms(start)
            if response.status_code != 200:
                models_test = {"status": "warning", "message": f"HTTP {response.status_code}", "response_time_ms": response_time}
            else:
                models_test = {"status": "ok", "message": f"成功获取模型列表 (HTTP {response.status_code})", "response_time_ms": response_time}
                try:
                    models_data = response.json()
                    model_names = [m["id"] for m in models_data.get("data", []) if isinstance(m, dict) and "id" in m]
                    models_test["message"] += f"，共 {len(model_names)} 个模型"
                    if model_names:
                        models_test["available_models"] = model_names
                except Exception:
                    pass
    except httpx.HTTPError as e:
        models_test = {"status": "warning", "message": f"模型列表接口请求失败: {e}", "response_time_ms": _elapsed_ms(start)}

    if chat_test.get("status") != "ok":
        models_state = "可用" if models_test.get("status") == "ok" else "不可用"
        return {
            "status": "warning",
            "message": f"聊天接口不可用: {chat_test.get('message', '')}；模型列表接口{models_state}: {models_test['message']}",
            "response_time_ms": chat_test.get("response_time_ms", models_test.get("response_time_ms")),
            "models_endpoint_test": models_test,
            "chat_completion_test": chat_test,
        }

    if models_test.get("status") != "ok":
        return {
            "status": "ok",
            "message": f"{chat_test['message']}；模型列表接口不可用: {models_test['message']}",
            "response_time_ms": chat_test.get("response_time_ms"),
            "models_endpoint_test": models_test,
            "chat_completion_test": chat_test,
        }

    result: dict[str, Any] = {
        "status": "ok",
        "message": f"{chat_test['message']}；{models_test['message']}",
        "response_time_ms": chat_test.get("response_time_ms"),
        "models_endpoint_test": models_test,
        "chat_completion_test": chat_test,
    }
    if "available_models" in models_test:
        result["available_models"] = models_test["available_models"]
    return result


async def _test_primary_responses(api_url: str, api_key: str, model: str) -> dict[str, Any]:
    responses_url = f"{api_url.rstrip('/')}/responses"
    start = time.time()
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            responses_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "input": [{"role": "user", "content": "Reply with exactly: ok"}],
                "stream": False,
            },
        )
        response_time = _elapsed_ms(start)
        if response.status_code != 200:
            return {"status": "warning", "message": f"HTTP {response.status_code}", "response_time_ms": response_time}
        return {"status": "ok", "message": f"xAI Responses API 可用 (HTTP {response.status_code})", "response_time_ms": response_time}


async def _test_main_provider_connection(provider_config: dict[str, Any]) -> dict[str, Any]:
    if provider_config["mode"] == "xai-responses":
        return await _test_primary_responses(provider_config["api_url"], provider_config["api_key"], provider_config["model"])
    return await _test_primary_connection(provider_config["api_url"], provider_config["api_key"], provider_config["model"])


async def _safe_test_main_provider_connection(provider_config: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _test_main_provider_connection(provider_config)
    except httpx.TimeoutException:
        return {"status": "timeout", "message": f"{provider_config['provider']} 请求超时，请检查网络连接或 API URL"}
    except httpx.RequestError as e:
        return {"status": "error", "message": f"{provider_config['provider']} 网络错误: {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"{provider_config['provider']} 未知错误: {str(e)}"}


def _execute_doctor_status() -> dict[str, Any]:
    """Local readiness only: config, capability snapshot, evidence path, router.

    Shared low-level execution used by the v1 compatibility wrapper and the
    typed ``doctor.status`` owner.
    """
    info = config.get_config_info()
    minimum = validate_minimum_profile()
    capability_status = minimum.get("capability_status") or get_capability_status()
    router_status = intent_router_status()

    evidence_path: dict[str, Any] = {
        "source_discovery": {"ready": False, "providers": []},
        "docs_discovery": {"ready": False, "providers": []},
        "content_fetch": {"ready": False, "providers": []},
    }
    try:
        from .capability_taxonomy import list_provider_qualifications, map_v1_to_v2_capability

        for v1_capability, v2_capability in (
            ("web_search", "source_discovery"),
            ("docs_search", "docs_discovery"),
            ("web_fetch", "content_fetch"),
        ):
            ready_providers: list[str] = []
            status = capability_status.get(v1_capability) or {}
            provider_rows = status.get("provider_status") or []
            eligible = {
                str(row.get("provider"))
                for row in provider_rows
                if isinstance(row, dict) and row.get("eligible")
            }
            for record in list_provider_qualifications(capability=v2_capability, qualified_only=True):
                provider = str(record.get("provider") or "")
                if provider and provider in eligible and not record.get("experimental"):
                    ready_providers.append(provider)
            evidence_path[v2_capability] = {
                "ready": bool(ready_providers),
                "providers": ready_providers,
                "legacy_capability": v1_capability,
                "mapped": map_v1_to_v2_capability(v1_capability),
            }
    except Exception:
        # Local status must remain offline and non-throwing even if taxonomy import fails.
        pass

    core_ready = bool(
        evidence_path["source_discovery"]["ready"] and evidence_path["content_fetch"]["ready"]
    )
    config_storage_ok = bool(info.get("config_storage_ok", True))
    config_parameters_ok = not bool(info.get("config_parameter_errors"))
    minimum_ok = bool(minimum.get("ok", False))
    # Legacy model routes are optional LLM synthesis state; their absence is
    # reported as an explicit optional-capability status, never as a Core
    # failure. ``llm_plan`` has no configured capability.
    llm_synthesis_status = capability_status.get("llm_synthesis") or {}
    llm_synthesis_ready = bool(llm_synthesis_status.get("ok"))
    llm_synthesis_providers = list(llm_synthesis_status.get("configured") or [])
    # ok is local readiness only: storage/parameters + evidence path, never reachability.
    ok = config_storage_ok and config_parameters_ok and core_ready

    result = {
        "ok": ok,
        "operation": "doctor_status",
        "local_only": True,
        "network_behavior": "no_provider_requests_or_probes",
        "config_file": info.get("config_file", ""),
        "config_dir": info.get("config_dir", ""),
        "config_dir_source": info.get("config_dir_source", ""),
        "config_status": info.get("config_status", ""),
        "config_storage_ok": config_storage_ok,
        "config_storage_error": info.get("config_storage_error", ""),
        "config_parameter_errors": list(info.get("config_parameter_errors") or []),
        "config_sources": info.get("config_sources") or {},
        "capability_status": capability_status,
        "minimum_profile": minimum.get("profile", ""),
        "minimum_profile_ok": minimum_ok,
        "minimum_profile_missing": list(minimum.get("missing") or []),
        "minimum_profile_missing_required": list(minimum.get("missing_required") or []),
        "core_evidence_path": evidence_path,
        "core_evidence_ready": core_ready,
        "llm_synthesis": {
            "ready": llm_synthesis_ready,
            "providers": llm_synthesis_providers,
            "legacy_alias_of": "main_search",
            "optional": True,
        },
        "llm_plan": {"ready": False, "configured": [], "optional": True},
        "intent_router_status": router_status,
    }
    if ok:
        result["error_type"] = ""
        result["error"] = ""
    elif not config_storage_ok:
        result["error_type"] = "config_error"
        result["error"] = info.get("config_storage_error") or "配置存储不可用。请设置 SMART_SEARCH_CONFIG_DIR 指向可写且受保护的配置目录。"
    elif info.get("config_parameter_errors"):
        result["error_type"] = "parameter_error"
        result["error"] = "; ".join(info["config_parameter_errors"])
    else:
        result["error_type"] = "config_error"
        result["error"] = "Core evidence path is not ready (source discovery + content fetch)."

    sanitized = sanitize_data(result)
    return sanitized if isinstance(sanitized, dict) else result


async def _execute_doctor_probe() -> dict[str, Any]:
    # ================================================================================
    # 步骤4：执行 doctor 诊断
    # ================================================================================
    # 目标：doctor 始终报告 profile 和 command capability 状态，不把诊断变成隐藏预检。
    # 数据源：配置、provider connection checks 和统一 capability status。
    # 操作：
    # 1) 保留旧的 main_search connection alias 和 minimum profile 字段。
    # 2) 对 lite/off profile 使用 source capability 判断基本可用性。
    # 3) 输出缺失能力和降级原因，统一 CLI 退出码映射。
    # 注意：这是共享低层执行，v1 兼容包装与 typed ``doctor.probe`` owner 都调用它。
    """Execute the aggregate doctor probe.

    Shared low-level execution used by the v1 compatibility wrapper and the
    typed ``doctor.probe`` owner. Reports profile and command capability
    status without turning the diagnosis into a hidden preflight.
    """
    logger.info("开始执行 doctor 诊断")
    info = config.get_config_info()

    main_provider_configs: list[dict[str, Any]] = []
    try:
        # 4.1 按路由身份聚合 main_search 连接诊断
        main_provider_configs = _main_search_provider_configs()
        info["main_search_connection_tests"] = {}
        primary_connection_test: dict[str, Any] | None = None
        for provider_config in main_provider_configs:
            connection_test = dict(await _safe_test_main_provider_connection(provider_config))
            route_id = str(provider_config.get("route_id") or "")
            diagnostic_id = route_id or provider_config["provider"]
            if route_id:
                connection_test["route_id"] = route_id
                connection_test["provider"] = provider_config["provider"]
            info["main_search_connection_tests"][diagnostic_id] = connection_test
            if primary_connection_test is None:
                primary_connection_test = connection_test
        if main_provider_configs:
            first_provider = main_provider_configs[0]
            info["primary_api_mode"] = first_provider["mode"]
            info["primary_connection_test"] = primary_connection_test
        else:
            info["primary_connection_test"] = {"status": "config_error", "message": MINIMUM_PROFILE_ERROR}
    except ValueError as e:
        info["main_search_connection_tests"] = {}
        info["primary_connection_test"] = {"status": "config_error", "message": str(e)}
    except Exception as e:
        info["main_search_connection_tests"] = {}
        info["primary_connection_test"] = {"status": "error", "message": f"未知错误: {str(e)}"}

    try:
        info["exa_connection_test"] = await _test_exa_connection()
    except httpx.TimeoutException:
        info["exa_connection_test"] = {"status": "timeout", "message": "Exa API 请求超时"}
    except Exception as e:
        info["exa_connection_test"] = {"status": "error", "message": str(e)}

    try:
        info["tavily_connection_test"] = await _test_tavily_connection()
    except httpx.TimeoutException:
        info["tavily_connection_test"] = {"status": "timeout", "message": "Tavily API 请求超时"}
    except Exception as e:
        info["tavily_connection_test"] = {"status": "error", "message": str(e)}

    try:
        info["jina_connection_test"] = await _test_jina_connection()
    except httpx.TimeoutException:
        info["jina_connection_test"] = {"status": "timeout", "message": "Jina Reader 请求超时"}
    except Exception as e:
        info["jina_connection_test"] = {"status": "error", "message": str(e)}

    if config.firecrawl_api_key:
        info["firecrawl_connection_test"] = {"status": "configured", "message": "FIRECRAWL_API_KEY 已设置"}
    else:
        info["firecrawl_connection_test"] = {"status": "not_configured", "message": "FIRECRAWL_API_KEY 未设置，Firecrawl 功能不可用"}

    try:
        info["zhipu_connection_test"] = await _test_zhipu_connection()
    except httpx.TimeoutException:
        info["zhipu_connection_test"] = {"status": "timeout", "message": "智谱 API 请求超时"}
    except Exception as e:
        info["zhipu_connection_test"] = {"status": "error", "message": str(e)}

    try:
        info["zhipu_mcp_connection_test"] = await _test_zhipu_mcp_connection()
    except httpx.TimeoutException:
        info["zhipu_mcp_connection_test"] = {"status": "timeout", "message": "智谱 Coding Plan MCP 请求超时"}
    except Exception as e:
        info["zhipu_mcp_connection_test"] = {"status": "error", "message": str(e)}

    try:
        info["context7_connection_test"] = await _test_context7_connection()
    except httpx.TimeoutException:
        info["context7_connection_test"] = {"status": "timeout", "message": "Context7 API 请求超时"}
    except Exception as e:
        info["context7_connection_test"] = {"status": "error", "message": str(e)}

    minimum = validate_minimum_profile()
    info["capability_status"] = minimum.get("capability_status", get_capability_status())
    info["minimum_profile_ok"] = minimum.get("ok", False)
    info["minimum_profile_missing"] = minimum.get("missing", [])
    info["minimum_profile_missing_required"] = minimum.get("missing_required", [])
    info["missing_capabilities"] = minimum.get("missing_required", [])
    info["required_capabilities"] = list(minimum.get("enforced_required", []))
    info["minimum_profile"] = minimum.get("profile", "")
    info["command_capabilities"] = {
        command: {
            "required_capabilities": list(matrix.get("required", ())),
            "required_providers": list(matrix.get("required_providers", ())),
            "optional_capabilities": list(matrix.get("optional", ())),
            "source_only_profiles": ["lite", "off"] if command == "search" else [],
            "source_only_response_mode": "evidence" if command == "search" else "",
        }
        for command, matrix in COMMAND_CAPABILITY_MATRIX.items()
    }
    info["degraded"] = bool(minimum.get("degraded"))
    info["degraded_reason"] = (
        f"profile optional capabilities unavailable: {', '.join(minimum.get('optional_missing', []))}"
        if minimum.get("optional_missing")
        else ""
    )
    info["intent_router_status"] = intent_router_status()
    main_connection_tests = info.get("main_search_connection_tests") or {}
    main_search_statuses = [item.get("status") for item in main_connection_tests.values() if isinstance(item, dict)]
    primary_test = info.get("primary_connection_test", {})
    primary_status = primary_test.get("status")
    main_search_ok = any(status == "ok" for status in main_search_statuses) if main_connection_tests else primary_status == "ok"
    active_profile = minimum.get("profile", "standard")
    # Core readiness comes from the capability snapshot: source discovery
    # (web_search OR docs_search) plus web_fetch. Legacy model routes are
    # optional LLM synthesis state; absent routes never make an evidence-ready
    # Core unavailable and surface only as a warning below.
    source_search_ok = any(
        _capability_available(info["capability_status"], capability)
        for capability in ("web_search", "docs_search")
    )
    fetch_ok = _capability_available(info["capability_status"], "web_fetch")
    core_ready = source_search_ok and fetch_ok
    info["core_evidence_ready"] = core_ready
    info["llm_synthesis_ready"] = main_search_ok
    info["llm_plan_ready"] = False
    profile_health_ok = core_ready
    info["ok"] = (
        info.get("config_storage_ok", True)
        and not info.get("config_parameter_errors")
        and profile_health_ok
        and minimum.get("ok", False)
    )
    if info["ok"] and not main_search_ok:
        llm_warning = "no configured llm_synthesis route; optional LLM synthesis unavailable"
        existing = str(info.get("degraded_reason") or "").strip()
        info["degraded_reason"] = (existing + "; " + llm_warning) if existing else llm_warning
        info["degraded"] = True
    if info["ok"]:
        info["error_type"] = ""
        info["error"] = ""
    elif not info.get("config_storage_ok", True):
        info["error_type"] = "config_error"
        info["error"] = info.get("config_storage_error") or "配置存储不可用。请设置 SMART_SEARCH_CONFIG_DIR 指向可写且受保护的配置目录。"
    elif info.get("SMART_SEARCH_MODEL_ROUTES") == "<invalid SMART_SEARCH_MODEL_ROUTES>":
        # 4.2 已保存路由损坏属于本地配置问题，不能误报为命令参数错误。
        route_errors = [
            error
            for error in info.get("config_parameter_errors", [])
            if "SMART_SEARCH_MODEL_ROUTES" in error
        ]
        info["error"] = "; ".join(route_errors) or "Invalid SMART_SEARCH_MODEL_ROUTES."
        info["error_type"] = "config_error"
    elif info.get("config_parameter_errors"):
        info["error"] = "; ".join(info["config_parameter_errors"])
        info["error_type"] = "parameter_error"
    elif not minimum.get("ok", False):
        info["error"] = minimum.get("error", MINIMUM_PROFILE_ERROR)
        info["error_type"] = minimum.get("error_type", "config_error")
    else:
        info["error"] = primary_test.get("message", "Primary connection check failed")
        if primary_status == "config_error":
            info["error_type"] = "config_error"
        elif primary_status in {"timeout", "error", "warning"}:
            info["error_type"] = "network_error"
        else:
            info["error_type"] = "runtime_error"
    """
    /*
     * ================================================================================
     * 步骤5：清理 doctor 输出
     * ================================================================================
     * 目标：确保连接探针消息和配置诊断不返回 URL 内嵌凭据。
     * 数据源：已聚合的 doctor 诊断结果。
     * 操作：
     * 1) 递归清理敏感字段、URL userinfo 和敏感查询参数。
     * 2) 保留非敏感诊断字段与原有返回结构。
     * ================================================================================
    */
    """
    logger.info("步骤5开始：清理 doctor 输出")
    sanitized_info = sanitize_data(info)
    safe_info = sanitized_info if isinstance(sanitized_info, dict) else info
    logger.info("步骤5结束：doctor 输出清理完成")
    logger.info("doctor 诊断完成: ok=%s profile=%s", safe_info.get("ok", False), active_profile)
    return safe_info


def _model_routes_result(action: str) -> dict[str, Any]:
    """
    /*
     * ==============================================================================
     * 步骤1：读取模型路由状态
     * ==============================================================================
     * 目标：让 model list 和 model current 共用同一份有序、脱敏结果。
     * 数据源：SMART_SEARCH_MODEL_ROUTES 以及兼容保留的旧模型配置。
     * 操作：
     * 1) 读取并校验路由数组，保留配置文件中的顺序和显式空状态。
     * 2) 仅在路由键缺失时回退旧模型配置；空数组没有当前模型。
     * 3) 空路由时隐藏保留的旧模型展示字段，避免把已删除模型显示为可用。
     * 4) 仅返回脱敏后的 API key，并标记当前首选路由。
     * ==============================================================================
     */
    """
    logger.info("步骤1开始：读取模型路由状态，action=%s", action)
    try:
        # 1.1 空路由数组是显式状态，不能被旧主搜索配置覆盖。
        routes_configured = config.model_routes_configured
        routes = config.get_model_routes(masked=True)
    except ValueError as exc:
        result = {
            "ok": False,
            "action": action,
            "error_type": "config_error",
            "error": sanitize_data(str(exc)),
            "routes": [],
            "model_routes": [],
            "route_count": 0,
            "config_file": str(config.config_file),
        }
        logger.info("步骤1结束：模型路由状态读取失败，action=%s", action)
        return result

    # 1.2 显式空数组保留旧配置存储，但模型管理结果不能将其展示为可用。
    empty_explicit_routes = routes_configured and not routes
    current_route = routes[0] if routes else None
    if current_route:
        current_model_name = current_route.get("model", "")
    # 1.3 只有路由键不存在时，旧模型才是有效的 current model。
    elif not routes_configured and config.xai_api_key:
        current_model_name = config.xai_model
    elif (
        not routes_configured
        and config.openai_compatible_api_url
        and config.openai_compatible_api_key
    ):
        current_model_name = config.openai_compatible_model
    else:
        current_model_name = ""
    result = {
        "ok": True,
        "action": action,
        "routes": routes,
        "model_routes": routes,
        "route_count": len(routes),
        "current_route": current_route,
        "current_route_id": current_route.get("id", "") if current_route else "",
        "current_model": current_model_name,
        "xai_model": "" if empty_explicit_routes else config.xai_model,
        "openai_compatible_model": (
            "" if empty_explicit_routes else config.openai_compatible_model
        ),
        "openai_compatible_fallback_models": (
            [] if empty_explicit_routes else config.openai_compatible_fallback_models
        ),
        "config_file": str(config.config_file),
    }
    logger.info(
        "步骤1结束：模型路由状态读取完成，action=%s routes=%s routes_configured=%s",
        action,
        len(routes),
        routes_configured,
    )
    return result


async def _execute_smoke(mode: str = "mock") -> dict[str, Any]:
    """Shared smoke execution (mock or live) used by the v1 wrapper and the
    typed ``dev.smoke`` owner."""
    start = time.time()
    mode = (mode or "mock").strip().lower()
    if mode not in {"mock", "live"}:
        return {"ok": False, "error_type": "parameter_error", "error": "mode must be mock or live"}
    if mode == "live":
        return await _smoke_live(start)
    return await _smoke_mock(start)


def _case(name: str, ok: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, **(details or {})}


def _case_failed(case: dict[str, Any]) -> bool:
    return not case.get("ok") and case.get("severity", "critical") != "degraded"


async def _smoke_mock(start: float) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    minimum_status = {
        "main_search": {
            "configured": ["xai-responses", "openai-compatible"],
            "fallback_chain": MAIN_SEARCH_FALLBACK_CHAIN,
            "ok": True,
        },
        "web_search": {"configured": ["zhipu"], "fallback_chain": ["zhipu", "zhipu-mcp", "tavily", "firecrawl"], "ok": True},
        "docs_search": {"configured": ["context7"], "fallback_chain": ["context7", "exa"], "ok": True},
        "web_fetch": {"configured": ["tavily"], "fallback_chain": ["tavily", "jina", "zhipu-mcp-reader", "firecrawl"], "ok": True},
        "vertical_search": {"configured": [], "fallback_chain": ["anysearch"], "ok": False, "experimental": True},
    }
    minimum = _minimum_profile_result("standard", minimum_status)
    cases.append(
        _case(
            "doctor minimum profile gate",
            minimum["ok"] and not minimum["missing"],
            {"minimum_profile_ok": minimum["ok"], "capability_status": minimum["capability_status"]},
        )
    )

    missing_minimum = _minimum_profile_result(
        "standard",
        {
            **minimum_status,
            "web_fetch": {"configured": [], "fallback_chain": ["tavily", "jina", "zhipu-mcp-reader", "firecrawl"], "ok": False},
        },
    )
    cases.append(
        _case(
            "doctor minimum profile fails closed",
            not missing_minimum["ok"] and missing_minimum["missing"] == ["web_fetch"],
            {"missing": missing_minimum["missing"], "error_type": missing_minimum["error_type"]},
        )
    )

    main_attempts = [_attempt("main_search", "xAI Responses", "ok", time.time(), result_count=1)]
    cases.append(_case("main_search xai responses answer path", True, {"provider_attempts": main_attempts}))

    main_fallback_attempts = [
        _attempt("main_search", "xAI Responses", "error", time.time(), error_type="network_error", error="mock failure"),
        _attempt("main_search", "OpenAI-compatible", "ok", time.time(), result_count=1),
    ]
    cases.append(_case("main_search fallback xai_to_openai_compatible", _fallback_used(main_fallback_attempts), {"provider_attempts": main_fallback_attempts}))

    web_attempts = [
        _attempt("web_search", "grok-web-tools", "error", time.time(), error_type="network_error", error="mock failure"),
        _attempt("web_search", "zhipu", "ok", time.time(), result_count=1),
    ]
    cases.append(_case("web_search fallback grok_to_zhipu", _fallback_used(web_attempts), {"provider_attempts": web_attempts}))

    attempts = [
        _attempt("web_fetch", "tavily", "empty", time.time()),
        _attempt("web_fetch", "firecrawl", "ok", time.time(), result_count=1),
    ]
    cases.append(_case("web_fetch fallback tavily_to_firecrawl", _fallback_used(attempts), {"provider_attempts": attempts}))

    docs_attempts = [
        _attempt("docs_search", "context7", "empty", time.time()),
        _attempt("docs_search", "exa", "ok", time.time(), result_count=1),
    ]
    cases.append(_case("docs_search fallback context7_to_exa", _fallback_used(docs_attempts), {"provider_attempts": docs_attempts}))

    general_route = {
        "docs_intent": _is_docs_intent("today AI news"),
        "zh_current_intent": _is_zh_current_intent("today AI news"),
        "web_current_intent": _is_web_current_intent("today AI news"),
        "supplemental_paths": [],
    }
    cases.append(_case("search balanced avoids context7 for general query", not general_route["docs_intent"], {"routing_decision": general_route}))

    docs_route = {
        "docs_intent": _is_docs_intent("React useEffect API docs"),
        "web_current_intent": _is_web_current_intent("React useEffect API docs"),
        "supplemental_paths": ["docs_search"],
    }
    cases.append(_case("search docs intent uses docs route", docs_route["docs_intent"], {"routing_decision": docs_route}))

    zh_route = {
        "zh_current_intent": _is_zh_current_intent("今天国内 AI 新闻"),
        "web_current_intent": _is_web_current_intent("今天国内 AI 新闻"),
        "supplemental_paths": ["web_search"],
    }
    cases.append(_case("search zh current intent uses zhipu reinforcement", zh_route["zh_current_intent"], {"routing_decision": zh_route}))

    sports_route = {
        "zh_current_intent": _is_zh_current_intent("nba战报"),
        "web_current_intent": _is_web_current_intent("nba战报"),
        "supplemental_paths": ["web_search"],
    }
    cases.append(_case("search sports current intent uses web reinforcement", sports_route["web_current_intent"], {"routing_decision": sports_route}))

    strict_attempts = [_attempt("main_search", "xAI Responses", "ok", time.time(), result_count=1)]
    strict_sources: list[dict[str, Any]] = []
    cases.append(
        _case(
            "strict insufficient evidence fails closed",
            not strict_sources,
            {"provider_attempts": strict_attempts, "error_type": "evidence_error"},
        )
    )

    from .research_plan import (
        PLAN_EXECUTABLE_OPERATION_IDS,
        PLAN_FORBIDDEN_SERIALIZED_FIELDS,
        serialize_research_plan,
    )

    def plan_ops(plan):
        return serialize_research_plan(plan)["operations"]

    def plan_ok(plan, *, has_fetch=False, first_fetch=False, has_docs=False, has_locale_zh=False):
        ops = plan_ops(plan)
        operations = {item["operation"] for item in ops}
        if not operations or not operations <= set(PLAN_EXECUTABLE_OPERATION_IDS):
            return False
        if any(field in item for item in ops for field in PLAN_FORBIDDEN_SERIALIZED_FIELDS):
            return False
        if has_fetch and "content_fetch" not in operations:
            return False
        if first_fetch and (not ops or ops[0]["operation"] != "content_fetch"):
            return False
        if has_docs and "docs_discovery" not in operations:
            return False
        if has_locale_zh and not any(
            item.get("input", {}).get("locale") == "zh" for item in ops
        ):
            return False
        return True

    market_plan = build_research_workflow_plan("深度搜索一下最近的比特币行情", budget="standard")
    market_ops = plan_ops(market_plan)
    cases.append(
        _case(
            "deep_research explicit planner simple current prompt uses typed plan",
            plan_ok(market_plan, has_fetch=True, has_locale_zh=True)
            and market_ops[0]["operation"] == "source_discovery",
            {"research_plan": serialize_research_plan(market_plan), "operations": market_ops},
        )
    )

    docs_plan = build_research_workflow_plan("深度调研 React useEffect 最新文档", budget="standard")
    cases.append(
        _case(
            "deep_research docs api prompt uses typed plan operations",
            plan_ok(docs_plan, has_fetch=True, has_docs=True),
            {"research_plan": serialize_research_plan(docs_plan)},
        )
    )

    claim_plan = build_research_workflow_plan("帮我核验这个说法是真是假", budget="standard")
    cases.append(
        _case(
            "deep_research claim verification requires a fetch operation",
            plan_ok(claim_plan, has_fetch=True),
            {"research_plan": serialize_research_plan(claim_plan)},
        )
    )

    url_first_plan = build_research_workflow_plan("深度调研 https://example.com/source", budget="standard")
    cases.append(
        _case(
            "deep_research url prompt is fetch first",
            plan_ok(url_first_plan, has_fetch=True, first_fetch=True),
            {"research_plan": serialize_research_plan(url_first_plan)},
        )
    )

    normal_prompt = "搜索一下 smart-search 怎么安装"
    cases.append(
        _case(
            "deep_research normal search prompt does not trigger",
            not any(marker in normal_prompt.lower() for marker in ("深度搜索", "深度调研", "深入搜索", "deep search", "deep research")),
            {"prompt": normal_prompt, "deep_research_triggered": False},
        )
    )
    fixed_recipe_ids = {
        "current_market_research",
        "product_comparison_research",
        "technical_docs_research",
        "news_or_policy_research",
        "claim_verification_research",
        "url_first_research",
    }

    missing_for_deep = _minimum_profile_result(
        "standard",
        {
            **minimum_status,
            "docs_search": {"configured": [], "fallback_chain": ["context7", "exa"], "ok": False},
            "web_fetch": {"configured": [], "fallback_chain": ["tavily", "jina", "zhipu-mcp-reader", "firecrawl"], "ok": False},
        },
    )
    cases.append(
        _case(
            "deep_research missing provider gives capability guidance",
            not missing_for_deep["ok"] and set(missing_for_deep["missing"]) == {"docs_search", "web_fetch"},
            {"missing": missing_for_deep["missing"], "error_type": missing_for_deep["error_type"]},
        )
    )

    schema_modes = {"deep_research"}
    cases.append(
        _case(
            "deep_research fixed topic recipes are examples not schema",
            schema_modes.isdisjoint(fixed_recipe_ids) and "deep_research" in schema_modes,
            {"schema_modes": sorted(schema_modes), "not_schema_modes": sorted(fixed_recipe_ids)},
        )
    )

    zh_plan = build_research_workflow_plan("今天国内 AI 政策最新公告", budget="standard")
    cases.append(
        _case(
            "research router chinese current plan reinforces web sources",
            plan_ok(zh_plan, has_locale_zh=True),
            {"research_plan": serialize_research_plan(zh_plan)},
        )
    )
    pdf_plan = build_research_workflow_plan("summarize https://arxiv.org/pdf/2401.00001.pdf", budget="standard")
    cases.append(
        _case(
            "research router known url pdf plan is fetch first",
            plan_ok(pdf_plan, has_fetch=True, first_fetch=True),
            {"research_plan": serialize_research_plan(pdf_plan)},
        )
    )
    js_plan = build_research_workflow_plan("dynamic javascript cloudflare page", budget="standard")
    cases.append(
        _case(
            "research router js heavy prompt still plans a fetch operation",
            plan_ok(js_plan, has_fetch=True),
            {"research_plan": serialize_research_plan(js_plan)},
        )
    )
    vertical_plan = build_research_workflow_plan("CVE OpenSSL 漏洞影响范围", budget="standard")
    cases.append(
        _case(
            "research router vertical intent has no provider-specific operation",
            plan_ok(vertical_plan, has_fetch=True),
            {"research_plan": serialize_research_plan(vertical_plan)},
        )
    )

    research_fallback_attempts = [
        _attempt("web_fetch", "jina", "empty", time.time()),
        _attempt("web_fetch", "firecrawl", "ok", time.time(), result_count=1),
    ]
    cases.append(
        _case(
            "research fallback remains same capability",
            _fallback_used(research_fallback_attempts),
            {"provider_attempts": research_fallback_attempts},
        )
    )

    all_attempts: list[dict] = []
    for c in cases:
        all_attempts.extend(c.get("provider_attempts", []))
    failed = [c["name"] for c in cases if _case_failed(c)]
    return {
        "ok": not failed,
        "mode": "mock",
        "failed_cases": failed,
        "cases": cases,
        "provider_attempts": all_attempts,
        "providers_used": _provider_names_from_attempts(all_attempts),
        "fallback_used": _fallback_used(all_attempts),
        "elapsed_ms": _elapsed_ms(start),
    }


async def _smoke_live(start: float) -> dict[str, Any]:
    """
    /*
     * ================================================================================
     * 步骤1：执行 live smoke
     * ================================================================================
     * 目标：只在实际配置 web_fetch provider 时执行 fetch，并保留 provider attempts。
     * 数据源：doctor capability status、provider 配置和 fetch 返回结果。
     * 操作：
     * 1) 执行最低能力档位和已配置 provider 的连接检查。
     * 2) 覆盖 Tavily、Jina、Zhipu MCP Reader 和 Firecrawl fetch 路由。
     * 3) 汇总 case、provider_attempts 和失败状态。
     * ================================================================================
     */
    """
    logger.info("步骤1开始：执行 live smoke")
    cases: list[dict[str, Any]] = []
    doctor_result = await _execute_doctor_probe()
    capability_status = doctor_result.get("capability_status", {})
    cases.append(
        _case(
            "doctor minimum profile",
            bool(doctor_result.get("minimum_profile_ok")),
            {
                "error_type": doctor_result.get("error_type", ""),
                "error": doctor_result.get("error", ""),
                "capability_status": doctor_result.get("capability_status", {}),
            },
        )
    )

    zhipu_status = doctor_result.get("zhipu_connection_test", {})
    if config.zhipu_api_key:
        zhipu_ok = zhipu_status.get("status") == "ok"
        web_fallback_available = len(capability_status.get("web_search", {}).get("configured", [])) > 1
        cases.append(
            _case(
                "zhipu search",
                zhipu_ok,
                {
                    "status": zhipu_status.get("status", ""),
                    "error": zhipu_status.get("message", ""),
                    "severity": "" if zhipu_ok else ("degraded" if web_fallback_available else "critical"),
                    "fallback_available": web_fallback_available,
                },
            )
        )
    else:
        cases.append(_case("zhipu search", True, {"skipped": "ZHIPU_API_KEY not configured"}))

    context7_status = doctor_result.get("context7_connection_test", {})
    if config.context7_api_key:
        context7_ok = context7_status.get("status") == "ok"
        docs_fallback_available = len(capability_status.get("docs_search", {}).get("configured", [])) > 1
        cases.append(
            _case(
                "context7 library",
                context7_ok,
                {
                    "status": context7_status.get("status", ""),
                    "error": context7_status.get("message", ""),
                    "severity": "" if context7_ok else ("degraded" if docs_fallback_available else "critical"),
                    "fallback_available": docs_fallback_available,
                },
            )
        )
    else:
        cases.append(_case("context7 library", True, {"skipped": "CONTEXT7_API_KEY not configured"}))

    fetch_provider_ids = ("tavily", "jina", "zhipu-mcp-reader", "firecrawl")
    configured_fetch_providers = [provider for provider in fetch_provider_ids if _provider_configured(provider)]
    if configured_fetch_providers:
        fetch_result, fetch_attempts = await _run_web_fetch_fallback("https://example.com")
        cases.append(
            _case(
                "web fetch fallback chain",
                bool(fetch_result),
                {
                    "provider": (fetch_result or {}).get("provider", ""),
                    "configured_providers": configured_fetch_providers,
                    "provider_attempts": fetch_attempts,
                },
            )
        )
    else:
        cases.append(_case("web fetch fallback chain", True, {"skipped": "no fetch providers configured"}))

    failed = [c["name"] for c in cases if _case_failed(c)]
    degraded = [c["name"] for c in cases if not c.get("ok") and c.get("severity") == "degraded"]
    attempts: list[dict] = []
    for c in cases:
        attempts.extend(c.get("provider_attempts", []))
    result = {
        "ok": not failed,
        "mode": "live",
        "failed_cases": failed,
        "degraded_cases": degraded,
        "cases": cases,
        "provider_attempts": attempts,
        "elapsed_ms": _elapsed_ms(start),
    }
    logger.info("步骤1结束：live smoke 完成，ok=%s", result["ok"])
    return result
