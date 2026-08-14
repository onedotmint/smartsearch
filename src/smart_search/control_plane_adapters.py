"""Projection from typed control owners to the v3 contract.

This module is the only v3 projection boundary. It converts parsed argparse
values into typed request arguments, invokes exactly one typed owner once, and
mechanically maps the resulting ``ControlOperationOutcome`` into the v3
envelope. It never derives status, error, network, write, subprocess or
degradation facts from result contents and never calls low-level execution
owners (config, capability service, provider catalog/diagnostics, operations
service, skill installer or the regression runner).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable, Mapping

from .control_operations import (
    ControlOperationOutcome,
    _connection_checks,
)
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
from .execution_primitives import ExecutionError
from .security import sanitize_data


def _clean(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    return sanitize_data(value, tuple(item for item in secrets if item))


def _pick(data: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: data[field] for field in fields if field in data}


# One explicit internal error-type -> v3 error-code mapping. Retryability is
# fixed by the v3 registry; unknown internal types fail closed to internal.
_EXECUTION_ERROR_TO_V3: dict[str, V3ErrorCode] = {
    "parameter_error": V3ErrorCode.INVALID_ARGUMENT,
    "invalid_argument": V3ErrorCode.INVALID_ARGUMENT,
    "config_error": V3ErrorCode.CONFIGURATION_ERROR,
    "configuration_error": V3ErrorCode.CONFIGURATION_ERROR,
    "not_configured": V3ErrorCode.CONFIGURATION_ERROR,
    "disabled": V3ErrorCode.CONFIGURATION_ERROR,
    "auth_error": V3ErrorCode.AUTHENTICATION_FAILED,
    "authentication_failed": V3ErrorCode.AUTHENTICATION_FAILED,
    "timeout": V3ErrorCode.UPSTREAM_TIMEOUT,
    "upstream_timeout": V3ErrorCode.UPSTREAM_TIMEOUT,
    "network_error": V3ErrorCode.PROVIDER_UNAVAILABLE,
    "provider_error": V3ErrorCode.PROVIDER_UNAVAILABLE,
    "rate_limited": V3ErrorCode.PROVIDER_UNAVAILABLE,
    "protocol_error": V3ErrorCode.PROVIDER_UNAVAILABLE,
    "filesystem_error": V3ErrorCode.FILE_SYSTEM_ERROR,
    "file_system_error": V3ErrorCode.FILE_SYSTEM_ERROR,
    "subprocess_error": V3ErrorCode.SUBPROCESS_FAILED,
    "subprocess_failed": V3ErrorCode.SUBPROCESS_FAILED,
    "internal_error": V3ErrorCode.INTERNAL_ERROR,
}


def _execution_error_to_v3(error: ExecutionError, *, secrets: Iterable[str] = ()) -> V3Error:
    code = _EXECUTION_ERROR_TO_V3.get(error.type, V3ErrorCode.INTERNAL_ERROR)
    message = str(_clean(error.message, secrets=secrets))
    details = {"owner_error_type": error.type}
    return V3Error(code, message, ERROR_RETRYABILITY[code], details)


def _side_effects(
    descriptor: V3OperationDescriptor,
    outcome: ControlOperationOutcome,
) -> V3SideEffects:
    # Read flags are declared by the descriptor; write/subprocess facts are the
    # owner-recorded actual facts.
    return V3SideEffects(
        config=V3Mutation(
            read=descriptor.config_read,
            write_attempted=outcome.side_effects.config.write_attempted,
            write_committed=outcome.side_effects.config.write_committed,
        ),
        filesystem=V3Mutation(
            read=descriptor.filesystem_read,
            write_attempted=outcome.side_effects.filesystem.write_attempted,
            write_committed=outcome.side_effects.filesystem.write_committed,
        ),
        subprocess_started=outcome.side_effects.subprocess_started,
    )


def _project_envelope(
    descriptor: V3OperationDescriptor,
    outcome: ControlOperationOutcome,
    *,
    secrets: Iterable[str] = (),
) -> V3Envelope:
    safe_result = _clean(outcome.result_dict, secrets=secrets)
    if not isinstance(safe_result, dict):
        safe_result = {}
    error = None
    if outcome.error is not None:
        error = _execution_error_to_v3(outcome.error, secrets=secrets)
    return V3Envelope(
        status=V3Status(outcome.status.value),
        command=descriptor.command,
        operation=descriptor.operation,
        result=safe_result,
        network=V3Network(
            descriptor.network_policy,
            descriptor.network_scope,
            outcome.network.attempted,
            outcome.network.targets,
        ),
        side_effects=_side_effects(descriptor, outcome),
        error=error,
        meta=V3Meta(
            duration_ms=int(outcome.metadata.duration_ms),
            warnings=outcome.warnings,
            deprecations=(),
        ),
    )


# ---------------------------------------------------------------------------
# Canonical result display projection (field-name picking only; no semantic
# inference). The typed owner already produced all facts; these helpers only
# reshape the canonical JSON result into the exact v3 result shape.
# ---------------------------------------------------------------------------


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


def _doctor_status_result(data: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        (
            "local_only", "config_file", "config_dir", "config_dir_source", "config_status",
            "config_storage_ok", "config_parameter_errors", "minimum_profile",
            "minimum_profile_ok", "minimum_profile_missing", "minimum_profile_missing_required",
            "core_evidence_path", "core_evidence_ready", "intent_router_status", "capability_status",
            "llm_synthesis", "llm_plan",
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


def _project_result(operation: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Field-name projection of the canonical result into the exact v3 shape."""
    if operation.startswith("config."):
        return _config_result(operation, result)
    if operation.startswith("provider.catalog."):
        include_status = operation.endswith("status")
        providers = [
            _catalog_provider(item, include_status=include_status)
            for item in result.get("providers") or []
            if isinstance(item, Mapping)
        ]
        return {"providers": providers, "provider_count": len(providers)}
    if operation == "provider.probe":
        return _probe_result(result)
    if operation.startswith("provider.routes."):
        return _routes_result(result)
    if operation == "doctor.status":
        return _doctor_status_result(result)
    if operation == "doctor.probe":
        data = _pick(
            result,
            ("minimum_profile", "minimum_profile_ok", "minimum_profile_missing", "missing_capabilities", "degraded_reason"),
        )
        data["checks"] = _connection_checks(result)
        return data
    if operation == "dev.route.explain":
        return _route_result(result)
    if operation == "dev.route.calibrate":
        return _calibration_result(result)
    if operation == "dev.diagnose.openai-compatible":
        return _diagnose_result(result)
    if operation == "dev.smoke":
        return _smoke_result(result)
    if operation == "dev.regression":
        return _pick(
            result,
            ("exit_code", "subprocess_started", "fallback", "test_files", "failed_cases", "subprocess_timeout"),
        )
    if operation in {"dev.skills.status", "dev.skills.update"}:
        return _skills_result(result, update=operation.endswith("update"))
    return dict(result)


# ---------------------------------------------------------------------------
# Argv -> typed request conversion and one-owner dispatch
# ---------------------------------------------------------------------------


async def _invoke_owner(owner: Callable[..., Awaitable[ControlOperationOutcome]], **kwargs: Any) -> ControlOperationOutcome:
    return await owner(**kwargs)


async def run_operation(args: Any, descriptor: V3OperationDescriptor) -> V3Envelope:
    """Invoke exactly one typed owner once and project its outcome to v3."""
    from . import control_operations

    operation = descriptor.operation
    secrets: tuple[str, ...] = ()

    if operation == "config.path":
        outcome = await _invoke_owner(control_operations.run_config_path)
    elif operation == "config.list":
        outcome = await _invoke_owner(control_operations.run_config_list)
    elif operation == "config.set":
        secrets = (str(args.value),)
        outcome = await _invoke_owner(control_operations.run_config_set, key=args.key, value=args.value)
    elif operation == "config.unset":
        outcome = await _invoke_owner(control_operations.run_config_unset, key=args.key)
    elif operation == "provider.catalog.list":
        outcome = await _invoke_owner(control_operations.run_provider_catalog_list)
    elif operation == "provider.catalog.status":
        outcome = await _invoke_owner(control_operations.run_provider_catalog_status)
    elif operation == "provider.probe":
        outcome = await _invoke_owner(control_operations.run_provider_probe, provider=args.provider)
    elif operation == "provider.routes.current":
        outcome = await _invoke_owner(control_operations.run_provider_routes_current)
    elif operation == "provider.routes.list":
        outcome = await _invoke_owner(control_operations.run_provider_routes_list)
    elif operation == "provider.routes.add":
        secrets = (str(args.api_key), str(args.api_url))
        outcome = await _invoke_owner(
            control_operations.run_provider_routes_add,
            route_id=args.route_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key=args.api_key,
            model=args.model_name,
            tools=args.tools,
            stream=args.stream,
            fallback_models=args.fallback_models,
        )
    elif operation == "provider.routes.remove":
        outcome = await _invoke_owner(control_operations.run_provider_routes_remove, route_id=args.route_id)
    elif operation == "doctor.status":
        outcome = await _invoke_owner(control_operations.run_doctor_status)
    elif operation == "doctor.probe":
        outcome = await _invoke_owner(control_operations.run_doctor_probe)
    elif operation == "dev.route.explain":
        outcome = await _invoke_owner(
            control_operations.run_dev_route_explain,
            query=args.query,
            validation=args.validation,
            mode=args.router_mode,
        )
    elif operation == "dev.route.calibrate":
        outcome = await _invoke_owner(control_operations.run_dev_route_calibrate, models=args.models)
    elif operation == "dev.diagnose.openai-compatible":
        outcome = await _invoke_owner(
            control_operations.run_dev_diagnose_openai_compatible,
            timeout_seconds=args.timeout,
        )
    elif operation == "dev.smoke":
        outcome = await _invoke_owner(control_operations.run_dev_smoke, mode=args.mode)
    elif operation == "dev.regression":
        outcome = await _invoke_owner(control_operations.run_dev_regression)
    elif operation == "dev.skills.status":
        outcome = await _invoke_owner(
            control_operations.run_dev_skills_status,
            targets=getattr(args, "targets", "") or "",
            all_targets=bool(getattr(args, "all", False)),
            project_root=getattr(args, "skills_root", None),
        )
    elif operation == "dev.skills.update":
        outcome = await _invoke_owner(
            control_operations.run_dev_skills_update,
            targets=getattr(args, "targets", "") or "",
            all_targets=bool(getattr(args, "all", False)),
            project_root=getattr(args, "skills_root", None),
        )
    else:
        raise ValueError(f"unsupported v3 operation: {operation}")

    envelope = _project_envelope(descriptor, outcome, secrets=secrets)
    # Project the canonical result into the exact v3 result shape.
    projected = _project_result(operation, envelope.result)
    return V3Envelope(
        status=envelope.status,
        command=envelope.command,
        operation=envelope.operation,
        result=projected,
        network=envelope.network,
        side_effects=envelope.side_effects,
        error=envelope.error,
        meta=envelope.meta,
    )


__all__ = ["run_operation"]