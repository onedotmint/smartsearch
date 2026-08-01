"""Adapters from established control-plane owners to the v3 contract."""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

from .control_plane_contract import (
    ERROR_RETRYABILITY,
    V3Envelope,
    V3Error,
    V3ErrorCode,
    V3Meta,
    V3Mutation,
    V3Network,
    V3OperationDescriptor,
    V3SideEffects,
    V3Status,
)
from .security import sanitize_data


def _elapsed_ms(start: float, data: Mapping[str, Any]) -> int:
    value = data.get("elapsed_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return int(round(value))
    return max(0, int(round((time.perf_counter() - start) * 1000)))


def _clean(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    return sanitize_data(value, tuple(item for item in secrets if item))


def _pick(data: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: data[field] for field in fields if field in data}


def _error_code(data: Mapping[str, Any], *, filesystem: bool = False, subprocess: bool = False) -> V3ErrorCode:
    error_type = str(data.get("error_type") or "").strip().lower()
    # Prefer the owner's classified error type over execution-channel hints so a
    # preflight parameter/config failure never becomes FILE_SYSTEM/SUBPROCESS.
    if error_type in {"parameter_error", "invalid_argument"}:
        return V3ErrorCode.INVALID_ARGUMENT
    if error_type in {"config_error", "configuration_error", "not_configured", "disabled"}:
        return V3ErrorCode.CONFIGURATION_ERROR
    if error_type in {"auth_error", "authentication_failed"}:
        return V3ErrorCode.AUTHENTICATION_FAILED
    if error_type in {"timeout", "upstream_timeout"}:
        return V3ErrorCode.UPSTREAM_TIMEOUT
    if error_type in {"network_error", "provider_error", "rate_limited", "protocol_error"}:
        return V3ErrorCode.PROVIDER_UNAVAILABLE
    if error_type in {"filesystem_error", "file_system_error"} or filesystem:
        return V3ErrorCode.FILE_SYSTEM_ERROR
    if error_type in {"subprocess_error", "subprocess_failed"} or subprocess:
        return V3ErrorCode.SUBPROCESS_FAILED
    return V3ErrorCode.INTERNAL_ERROR


def _error(data: Mapping[str, Any], *, secrets: Iterable[str] = (), filesystem: bool = False, subprocess: bool = False) -> V3Error:
    code = _error_code(data, filesystem=filesystem, subprocess=subprocess)
    message = str(data.get("error") or data.get("message") or data.get("summary") or "control-plane operation failed")
    safe_message = _clean(message, secrets=secrets)
    details = {"owner_error_type": str(data.get("error_type") or "")}
    return V3Error(code, str(safe_message), ERROR_RETRYABILITY[code], details)


def _side_effects(
    descriptor: V3OperationDescriptor,
    *,
    config_write_attempted: bool = False,
    config_write_committed: bool = False,
    filesystem_write_attempted: bool = False,
    filesystem_write_committed: bool = False,
    subprocess_started: bool = False,
) -> V3SideEffects:
    return V3SideEffects(
        config=V3Mutation(
            read=descriptor.config_read,
            write_attempted=config_write_attempted,
            write_committed=config_write_committed,
        ),
        filesystem=V3Mutation(
            read=descriptor.filesystem_read,
            write_attempted=filesystem_write_attempted,
            write_committed=filesystem_write_committed,
        ),
        subprocess_started=subprocess_started,
    )


def _envelope(
    descriptor: V3OperationDescriptor,
    data: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    start: float,
    status: V3Status | None = None,
    warnings: Iterable[str] = (),
    network_attempted: bool = False,
    targets: Iterable[str] = (),
    side_effects: V3SideEffects | None = None,
    secrets: Iterable[str] = (),
    filesystem_error: bool = False,
    subprocess_error: bool = False,
) -> V3Envelope:
    warning_tuple = tuple(str(item) for item in warnings if str(item))
    if status is None:
        status = V3Status.COMPLETE if data.get("ok", False) else V3Status.FAILED
    safe_result = _clean(dict(result), secrets=secrets)
    if not isinstance(safe_result, dict):
        safe_result = {}
    error = None
    if status is V3Status.FAILED:
        error = _error(
            data,
            secrets=secrets,
            filesystem=filesystem_error,
            subprocess=subprocess_error,
        )
    return V3Envelope(
        status=status,
        command=descriptor.command,
        operation=descriptor.operation,
        result=safe_result,
        network=V3Network(
            descriptor.network_policy,
            descriptor.network_scope,
            network_attempted,
            tuple(dict.fromkeys(str(item) for item in targets if item)),
        ),
        side_effects=side_effects or _side_effects(descriptor),
        error=error,
        meta=V3Meta(
            duration_ms=_elapsed_ms(start, data),
            warnings=warning_tuple,
            deprecations=(),
        ),
    )


def _config_result(operation: str, data: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "config.path": ("config_file", "config_dir", "config_dir_source", "config_status", "config_storage_ok"),
        "config.list": ("config_file", "values"),
        "config.set": ("config_file", "key", "value"),
        "config.unset": ("config_file", "key"),
    }
    return _pick(data, fields[operation])


def _route_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(raw, ("id", "provider", "api_url", "api_key", "model", "tools", "stream", "fallback_models"))


def _routes_result(data: Mapping[str, Any]) -> dict[str, Any]:
    routes = [_route_item(item) for item in data.get("routes") or [] if isinstance(item, Mapping)]
    current = data.get("current_route")
    return {
        "action": str(data.get("action") or ""),
        "route_count": len(routes),
        "routes": routes,
        "current_route_id": str(data.get("current_route_id") or ""),
        "current_route": _route_item(current) if isinstance(current, Mapping) else None,
        "current_model": str(data.get("current_model") or ""),
        "config_file": str(data.get("config_file") or ""),
    }


def _catalog_provider(raw: Mapping[str, Any], *, include_status: bool) -> dict[str, Any]:
    result = _pick(
        raw,
        ("provider", "capabilities", "v2_capabilities", "tier", "stability", "replacement", "network_behavior"),
    )
    if include_status:
        result["status"] = [
            _pick(item, ("capability", "configured", "enabled", "eligible", "reason"))
            for item in raw.get("status") or []
            if isinstance(item, Mapping)
        ]
    return result


def _probe_result(data: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(
        data,
        (
            "provider", "capabilities", "configured", "enabled", "eligible",
            "probe_capability", "probe_operation", "experimental", "status",
            "message", "response_time_ms", "route_ids",
        ),
    )
    result["routes"] = [
        _pick(item, ("route_id", "provider", "status", "message", "response_time_ms"))
        for item in data.get("routes") or []
        if isinstance(item, Mapping)
    ]
    return result


def _connection_checks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    main = data.get("main_search_connection_tests")
    if isinstance(main, Mapping):
        for check_id, raw in main.items():
            if isinstance(raw, Mapping):
                checks.append({"id": str(check_id), **_pick(raw, ("route_id", "provider", "status", "message", "response_time_ms"))})
    primary = data.get("primary_connection_test")
    # Legacy doctor payloads may only expose the primary alias.
    if isinstance(primary, Mapping) and not main:
        checks.append({"id": "primary", **_pick(primary, ("route_id", "provider", "status", "message", "response_time_ms"))})
    for key, raw in data.items():
        if key == "primary_connection_test" or not key.endswith("_connection_test") or not isinstance(raw, Mapping):
            continue
        checks.append({"id": key.removesuffix("_connection_test").replace("_", "-"), **_pick(raw, ("status", "message", "response_time_ms"))})
    return checks


def _network_checks(checks: Iterable[Mapping[str, Any]]) -> tuple[bool, tuple[str, ...]]:
    local_statuses = {"", "not_configured", "disabled", "config_error", "unsupported", "configured", "skipped"}
    targets = tuple(
        str(item.get("provider") or item.get("id") or "")
        for item in checks
        if str(item.get("status") or "") not in local_statuses
    )
    return bool(targets), tuple(dict.fromkeys(item for item in targets if item))


def _doctor_status_result(data: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        (
            "local_only", "config_file", "config_dir", "config_dir_source", "config_status",
            "config_storage_ok", "config_parameter_errors", "minimum_profile",
            "minimum_profile_ok", "minimum_profile_missing", "minimum_profile_missing_required",
            "core_evidence_path", "core_evidence_ready", "intent_router_status", "capability_status",
        ),
    )


def _route_result(data: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        (
            "query", "validation_level", "intent_router_mode", "required_capabilities",
            "missing_capabilities", "intent_signals", "confidence", "router_engines_used",
            "reasons", "supplemental_paths", "executed_search", "provider_selection",
        ),
    )


def _calibration_result(data: Mapping[str, Any]) -> dict[str, Any]:
    model_results = [
        _pick(
            item,
            (
                "model", "ok", "availability", "error_type", "error", "dimension", "latency_ms",
                "semantic_macro_f1", "full_route_macro_f1", "recommended_threshold", "recommended_margin",
            ),
        )
        for item in data.get("model_results") or []
        if isinstance(item, Mapping)
    ]
    result = _pick(
        data,
        (
            "metric", "primary_metric", "full_route_metric_role", "models", "failed_models",
            "dataset_size", "dataset_counts", "capabilities", "labels", "default_threshold",
            "default_margin", "embedding_model", "recommended_model", "recommended_threshold",
            "recommended_margin",
        ),
    )
    result["model_results"] = model_results
    return result


def _diagnose_result(data: Mapping[str, Any]) -> dict[str, Any]:
    checks = [
        _pick(item, ("name", "status", "message", "response_time_ms", "http_status", "content_type", "has_content", "stream"))
        for item in data.get("checks") or []
        if isinstance(item, Mapping)
    ]
    result = _pick(
        data,
        (
            "provider", "api_url", "api_key", "model", "configured_stream", "timeout_seconds",
            "config_file", "config_dir_source", "summary", "recommendation", "missing", "next_command",
        ),
    )
    result["checks"] = checks
    return result


def _smoke_result(data: Mapping[str, Any]) -> dict[str, Any]:
    cases = [
        _pick(item, ("name", "ok", "severity", "status", "skipped", "provider", "fallback_available"))
        for item in data.get("cases") or []
        if isinstance(item, Mapping)
    ]
    return {
        "mode": str(data.get("mode") or ""),
        "case_count": len(cases),
        "cases": cases,
        "failed_cases": list(data.get("failed_cases") or []),
        "degraded_cases": list(data.get("degraded_cases") or []),
        "providers_used": list(data.get("providers_used") or []),
        "fallback_used": bool(data.get("fallback_used", False)),
    }


def _skills_result(data: Mapping[str, Any], *, update: bool) -> dict[str, Any]:
    fields = (
        "root", "selected", "skill", "bundled_files", "bundled_hash", "status_counts", "targets"
    ) if not update else (
        "root", "selected", "installed", "skipped", "failed", "installed_count", "skipped_count", "failed_count"
    )
    return _pick(data, fields)


def _write_attempted(data: Mapping[str, Any]) -> bool:
    return str(data.get("error_type") or "") != "parameter_error"


async def run_operation(args: Any, descriptor: V3OperationDescriptor) -> V3Envelope:
    """Invoke one established owner and adapt its result to v3."""
    start = time.perf_counter()
    operation = descriptor.operation
    secrets: tuple[str, ...] = ()

    if operation.startswith("config."):
        from . import operations_service

        if operation == "config.path":
            data = operations_service.config_path()
        elif operation == "config.list":
            data = operations_service.config_list(show_secrets=False)
        elif operation == "config.set":
            secrets = (str(args.value),)
            data = operations_service.config_set(args.key, args.value)
        else:
            data = operations_service.config_unset(args.key)
        write_operation = descriptor.config_write
        attempted = write_operation and _write_attempted(data)
        return _envelope(
            descriptor,
            data,
            _config_result(operation, data),
            start=start,
            secrets=secrets,
            side_effects=_side_effects(
                descriptor,
                config_write_attempted=attempted,
                config_write_committed=bool(attempted and data.get("ok")),
            ),
        )

    if operation.startswith("provider.catalog."):
        from .provider_catalog import provider_catalog

        include_status = operation.endswith("status")
        data = provider_catalog(include_status=include_status)
        result = {
            "providers": [
                _catalog_provider(item, include_status=include_status)
                for item in data.get("providers") or []
                if isinstance(item, Mapping)
            ]
        }
        result["provider_count"] = len(result["providers"])
        return _envelope(descriptor, data, result, start=start)

    if operation == "provider.probe":
        from .operations_service import provider_probe

        data = await provider_probe(args.provider)
        routes = [item for item in data.get("routes") or [] if isinstance(item, Mapping)]
        partial = bool(data.get("ok") and routes and any(item.get("status") != "ok" for item in routes))
        status = V3Status.DEGRADED if partial else None
        warnings = ("one or more provider routes failed their probe",) if partial else ()
        return _envelope(
            descriptor,
            data,
            _probe_result(data),
            start=start,
            status=status,
            warnings=warnings,
            network_attempted=bool(data.get("network_attempted", False)),
            targets=(str(data.get("provider") or args.provider),),
        )

    if operation.startswith("provider.routes."):
        from . import operations_service

        if operation == "provider.routes.current":
            data = operations_service.current_model()
        elif operation == "provider.routes.list":
            data = operations_service.model_list()
        elif operation == "provider.routes.add":
            secrets = (str(args.api_key), str(args.api_url))
            data = operations_service.model_add(
                args.route_id,
                args.provider,
                args.api_url,
                args.api_key,
                args.model_name,
                tools=args.tools,
                stream=args.stream,
                fallback_models=args.fallback_models,
            )
        else:
            data = operations_service.model_remove(args.route_id)
        attempted = descriptor.config_write and _write_attempted(data)
        return _envelope(
            descriptor,
            data,
            _routes_result(data),
            start=start,
            secrets=secrets,
            side_effects=_side_effects(
                descriptor,
                config_write_attempted=attempted,
                config_write_committed=bool(attempted and data.get("ok")),
            ),
        )

    if operation == "doctor.status":
        from .operations_service import doctor_status

        data = doctor_status()
        return _envelope(descriptor, data, _doctor_status_result(data), start=start)

    if operation == "doctor.probe":
        from .operations_service import doctor

        data = await doctor()
        checks = _connection_checks(data)
        attempted, targets = _network_checks(checks)
        owner_degraded = bool(data.get("ok") and data.get("degraded"))
        partial = bool(not data.get("ok") and any(item.get("status") == "ok" for item in checks))
        status = V3Status.DEGRADED if owner_degraded or partial else None
        if owner_degraded:
            warnings = (str(data.get("degraded_reason") or "doctor completed with reduced coverage"),)
        elif partial:
            warnings = ("aggregate doctor completed with partial connectivity",)
        else:
            warnings = ()
        result = _pick(
            data,
            ("minimum_profile", "minimum_profile_ok", "minimum_profile_missing", "missing_capabilities", "degraded_reason"),
        )
        result["checks"] = checks
        return _envelope(
            descriptor,
            data,
            result,
            start=start,
            status=status,
            warnings=warnings,
            network_attempted=attempted,
            targets=targets,
        )

    if operation == "dev.route.explain":
        from .capability_service import route

        data = await route(args.query, validation=args.validation, mode=args.router_mode)
        degraded = bool(data.get("ok") and data.get("degraded"))
        engines = [str(item) for item in data.get("router_engines_used") or []]
        unavailable = "unavailable" in str(data.get("degraded_reason") or "")
        attempted = any(item in {"embeddings", "classifier"} for item in engines) or unavailable
        targets = tuple(item for item in ("embeddings", "classifier") if item in engines)
        return _envelope(
            descriptor,
            data,
            _route_result(data),
            start=start,
            status=V3Status.DEGRADED if degraded else None,
            warnings=(str(data.get("degraded_reason") or "route completed with reduced router coverage"),) if degraded else (),
            network_attempted=attempted,
            targets=targets,
        )

    if operation == "dev.route.calibrate":
        from .capability_service import route_calibrate

        data = await route_calibrate(models=args.models)
        model_results = [item for item in data.get("model_results") or [] if isinstance(item, Mapping)]
        successful = [item for item in model_results if item.get("ok")]
        failed_models = list(data.get("failed_models") or [])
        degraded = bool(successful and failed_models)
        attempted_models = [
            str(item.get("model") or "")
            for item in model_results
            if item.get("ok") or str(item.get("error_type") or "") != "config_error"
        ]
        return _envelope(
            descriptor,
            data,
            _calibration_result(data),
            start=start,
            status=V3Status.DEGRADED if degraded else None,
            warnings=("one or more calibration models failed",) if degraded else (),
            network_attempted=bool(attempted_models),
            targets=attempted_models,
        )

    if operation == "dev.diagnose.openai-compatible":
        from .operations_service import diagnose_openai_compatible

        data = await diagnose_openai_compatible(timeout_seconds=args.timeout)
        checks = list(data.get("checks") or [])
        return _envelope(
            descriptor,
            data,
            _diagnose_result(data),
            start=start,
            network_attempted=bool(checks),
            targets=("openai-compatible",),
        )

    if operation == "dev.smoke":
        from .operations_service import smoke

        data = await smoke(args.mode)
        degraded_cases = list(data.get("degraded_cases") or [])
        degraded = bool(data.get("ok") and degraded_cases)
        live = str(data.get("mode") or args.mode) == "live"
        cases = [item for item in data.get("cases") or [] if isinstance(item, Mapping)]
        attempted = bool(live and any(
            item.get("provider_attempts")
            or str(item.get("status") or "") in {"ok", "warning", "timeout", "error", "network_error", "provider_error"}
            for item in cases
        ))
        targets = tuple(str(item) for item in data.get("providers_used") or [])
        return _envelope(
            descriptor,
            data,
            _smoke_result(data),
            start=start,
            status=V3Status.DEGRADED if degraded else None,
            warnings=("live smoke completed with optional degraded cases",) if degraded else (),
            network_attempted=attempted,
            targets=targets,
        )

    if operation == "dev.regression":
        from .cli_dispatch import regression_result

        data = dict(regression_result())
        result = _pick(data, ("exit_code", "subprocess_started", "fallback", "test_files", "failed_cases"))
        started = bool(data.get("subprocess_started"))
        if not data.get("ok") and not data.get("error_type"):
            if started:
                data["error_type"] = "subprocess_error"
                data["error"] = str(data.get("error") or "regression subprocess failed")
            elif data.get("fallback") == "mock_smoke":
                data["error_type"] = "config_error"
                data["error"] = str(data.get("error") or "packaged mock-smoke regression failed")
            else:
                data["error_type"] = "internal_error"
                data["error"] = str(data.get("error") or "regression checks failed")
        return _envelope(
            descriptor,
            data,
            result,
            start=start,
            side_effects=_side_effects(descriptor, subprocess_started=started),
            subprocess_error=bool(started and not data.get("ok")),
        )

    if operation in {"dev.skills.status", "dev.skills.update"}:
        from .skill_installer import (
            SKILL_TARGETS,
            SkillInstallError,
            install_skill_targets,
            parse_skill_targets,
            status_skill_targets,
        )

        try:
            target_ids = (
                [target.target_id for target in SKILL_TARGETS]
                if getattr(args, "all", False)
                else parse_skill_targets(getattr(args, "targets", ""))
            )
            update = operation.endswith("update")
            if update:
                data = install_skill_targets(target_ids, project_root=args.skills_root)
            else:
                data = status_skill_targets(target_ids, project_root=args.skills_root)
        except SkillInstallError as exc:
            data = {"ok": False, "error_type": "parameter_error", "error": str(exc), "selected": []}
            update = operation.endswith("update")
        except OSError as exc:
            data = {"ok": False, "error_type": "filesystem_error", "error": str(exc), "selected": []}
            update = operation.endswith("update")

        installed_count = int(data.get("installed_count") or 0)
        failed_count = int(data.get("failed_count") or 0)
        degraded = bool(update and installed_count and failed_count)
        error_type = str(data.get("error_type") or "")
        attempted = bool(update and error_type != "parameter_error")
        filesystem_error = error_type in {"filesystem_error", "file_system_error"} or (
            update and attempted and not data.get("ok") and not degraded and failed_count > 0
        )
        return _envelope(
            descriptor,
            data,
            _skills_result(data, update=update),
            start=start,
            status=V3Status.DEGRADED if degraded else None,
            warnings=("some skill targets were updated and some failed",) if degraded else (),
            side_effects=_side_effects(
                descriptor,
                filesystem_write_attempted=attempted,
                filesystem_write_committed=bool(installed_count),
            ),
            filesystem_error=filesystem_error,
        )

    raise ValueError(f"unsupported v3 operation: {operation}")


__all__ = ["run_operation"]
