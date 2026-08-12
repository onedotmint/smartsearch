"""Schema-neutral typed owners for the fixed v3 control-plane operation set.

This module owns the semantic facts of the 20 canonical Control Plane
operations: terminal status, classified errors, actual network activity,
config/filesystem mutation commitment, subprocess facts and degradation
warnings. It is the single typed authority for Control state and I/O
semantics; ``control_plane_adapters`` only projects these outcomes into the
v3 envelope and the private ``control_executors`` module supplies the raw
low-level executions the owners consume.

Dependency rules:

- The module must not import CLI/parser/render modules, V1/V2/V3/Workflow
  contracts, the broad ``service`` facade, ``service_support`` or
  ``control_plane_adapters``.
- It reuses ``ExecutionError`` and ``ExecutionMetadata`` from
  ``execution_primitives`` and never duplicates shared primitive semantics.
- Raw low-level executors live in the private ``control_executors`` module
  and are called only through module attribute access. Status, network,
  write/subprocess and degradation facts are always derived here from those
  raw execution results and owned by ``ControlOperationOutcome``; the
  executor module never derives them.
"""

from __future__ import annotations

import asyncio
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Iterable, Mapping

from .capability_service import _main_search_provider_configs
from . import capability_service
from .config import ConfigStorageError, ModelRoutesConfigurationError, config
from .execution_primitives import ExecutionError, ExecutionMetadata
from .provider_catalog import provider_catalog
from .provider_diagnostics import (
    _error_type_for_status,
    _normalize_probe_status,
    provider_probe_base,
    run_probe_adapter,
)
from .security import sanitize_data
from .skill_installer import (
    SKILL_TARGETS,
    SkillInstallError,
    parse_skill_targets,
)
from . import control_executors
from . import skill_installer

# ---------------------------------------------------------------------------
# Fixed schema-neutral operation inventory
# ---------------------------------------------------------------------------

CONTROL_OPERATION_IDS = (
    "config.path",
    "config.list",
    "config.set",
    "config.unset",
    "provider.catalog.list",
    "provider.catalog.status",
    "provider.probe",
    "provider.routes.current",
    "provider.routes.list",
    "provider.routes.add",
    "provider.routes.remove",
    "doctor.status",
    "doctor.probe",
    "dev.route.explain",
    "dev.route.calibrate",
    "dev.diagnose.openai-compatible",
    "dev.smoke",
    "dev.regression",
    "dev.skills.status",
    "dev.skills.update",
)
CONTROL_OPERATION_SET = frozenset(CONTROL_OPERATION_IDS)

# Legacy semantic keys that are derived by the typed owner and never part of
# the canonical outcome result. The V3 projection and the legacy v1 projection
# both re-derive them from the typed outcome fields.
_LEGACY_SEMANTIC_KEYS = frozenset({"ok", "error_type", "error", "network_attempted", "elapsed_ms"})

# Statuses that mean the probe/check never started network activity.
_LOCAL_PROBE_STATUSES = frozenset(
    {"", "not_configured", "disabled", "config_error", "unsupported", "configured", "skipped"}
)


# ---------------------------------------------------------------------------
# Typed domain model
# ---------------------------------------------------------------------------


class ControlOperationStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


def _freeze_json(value: Any, path: str = "value") -> Any:
    """Validate and freeze a JSON tree into immutable tuple/read-only mapping storage."""
    if value is None or isinstance(value, str) or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    raise ValueError(f"{path} must be JSON-compatible")


def _thaw_json(value: Any) -> Any:
    """Return a fresh JSON-compatible dict/list tree from frozen storage."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class ControlNetworkFacts:
    """Actual network activity observed during one control operation."""

    attempted: bool = False
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.attempted) is not bool:
            raise ValueError("ControlNetworkFacts.attempted must be boolean")
        targets = tuple(self.targets)
        for target in targets:
            if not isinstance(target, str) or not target.strip():
                raise ValueError("ControlNetworkFacts.targets must be non-blank strings")
        if len(targets) != len(set(targets)):
            raise ValueError("ControlNetworkFacts.targets must be unique")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True)
class ControlMutationFacts:
    """Config/filesystem mutation facts for one control operation."""

    read: bool = False
    write_attempted: bool = False
    write_committed: bool = False

    def __post_init__(self) -> None:
        for name in ("read", "write_attempted", "write_committed"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"ControlMutationFacts.{name} must be boolean")
        if self.write_committed and not self.write_attempted:
            raise ValueError("ControlMutationFacts.write_committed requires write_attempted")


@dataclass(frozen=True)
class ControlSideEffectFacts:
    """Config/filesystem/subprocess side effects of one control operation."""

    config: ControlMutationFacts = field(default_factory=ControlMutationFacts)
    filesystem: ControlMutationFacts = field(default_factory=ControlMutationFacts)
    subprocess_started: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.config, ControlMutationFacts):
            raise ValueError("ControlSideEffectFacts.config must be ControlMutationFacts")
        if not isinstance(self.filesystem, ControlMutationFacts):
            raise ValueError("ControlSideEffectFacts.filesystem must be ControlMutationFacts")
        if type(self.subprocess_started) is not bool:
            raise ValueError("ControlSideEffectFacts.subprocess_started must be boolean")


@dataclass(frozen=True)
class ControlOperationOutcome:
    """Schema-neutral outcome of one control-plane operation.

    Invariants:

    - ``operation`` is exactly one of the fixed 20 Control operation ids;
    - ``complete`` has no error; ``degraded`` has no error and at least one
      non-blank warning; ``failed`` has one classified error and no warnings;
    - network targets are non-blank and unique; committed writes require an
      attempted write;
    - ``result`` and error details are defensively frozen, finite and
      JSON-safe;
    - no schema version, command spelling, V3 error code or envelope type
      appears in the domain model.
    """

    operation: str
    status: ControlOperationStatus | str
    result: Mapping[str, Any]
    network: ControlNetworkFacts = field(default_factory=ControlNetworkFacts)
    side_effects: ControlSideEffectFacts = field(default_factory=ControlSideEffectFacts)
    error: ExecutionError | None = None
    warnings: tuple[str, ...] = ()
    metadata: ExecutionMetadata = field(default_factory=lambda: ExecutionMetadata("control"))

    def __post_init__(self) -> None:
        if self.operation not in CONTROL_OPERATION_SET:
            raise ValueError(f"unknown control operation: {self.operation!r}")
        if isinstance(self.status, ControlOperationStatus):
            status = self.status
        else:
            try:
                status = ControlOperationStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid control status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if not isinstance(self.result, Mapping):
            raise ValueError("ControlOperationOutcome.result must be a mapping")
        object.__setattr__(self, "result", _freeze_json(dict(self.result), "outcome.result"))
        if not isinstance(self.network, ControlNetworkFacts):
            raise ValueError("ControlOperationOutcome.network must be ControlNetworkFacts")
        if not isinstance(self.side_effects, ControlSideEffectFacts):
            raise ValueError("ControlOperationOutcome.side_effects must be ControlSideEffectFacts")
        if status is ControlOperationStatus.COMPLETE:
            if self.error is not None:
                raise ValueError("complete outcome cannot carry an error")
        elif status is ControlOperationStatus.DEGRADED:
            if self.error is not None:
                raise ValueError("degraded outcome cannot carry an error")
            if not self.warnings or not any(str(warning).strip() for warning in self.warnings):
                raise ValueError("degraded outcome requires at least one non-blank warning")
        else:
            if self.error is None:
                raise ValueError("failed outcome requires a classified error")
            if self.warnings:
                raise ValueError("failed outcome cannot carry warnings")
        if not isinstance(self.warnings, tuple):
            object.__setattr__(self, "warnings", tuple(self.warnings))
        for warning in self.warnings:
            if not isinstance(warning, str):
                raise ValueError("ControlOperationOutcome.warnings must contain only strings")
        if not isinstance(self.metadata, ExecutionMetadata):
            raise ValueError("ControlOperationOutcome.metadata must be ExecutionMetadata")

    @property
    def result_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible copy of the canonical result."""
        return _thaw_json(self.result)


def _outcome(
    operation: str,
    status: ControlOperationStatus,
    result: Mapping[str, Any],
    *,
    error: ExecutionError | None = None,
    warnings: Iterable[str] = (),
    network: ControlNetworkFacts | None = None,
    side_effects: ControlSideEffectFacts | None = None,
    metadata: ExecutionMetadata | None = None,
) -> ControlOperationOutcome:
    return ControlOperationOutcome(
        operation=operation,
        status=status,
        result=result,
        error=error,
        warnings=tuple(warnings),
        network=network or ControlNetworkFacts(),
        side_effects=side_effects or ControlSideEffectFacts(),
        metadata=metadata or ExecutionMetadata("control"),
    )


def _elapsed_ms(start: float) -> int:
    return max(0, int(round((time.perf_counter() - start) * 1000)))


def _strip_legacy_semantics(data: Mapping[str, Any]) -> dict[str, Any]:
    """Drop legacy semantic keys so the canonical result is owner-derived."""
    return {key: value for key, value in data.items() if key not in _LEGACY_SEMANTIC_KEYS}


def _config_error(message: str) -> ExecutionError:
    return ExecutionError("config_error", str(sanitize_data(message)), False)


def _parameter_error(message: str) -> ExecutionError:
    return ExecutionError("parameter_error", str(sanitize_data(message)), False)


def _network_error(message: str) -> ExecutionError:
    return ExecutionError("network_error", str(sanitize_data(message)), True)


# ---------------------------------------------------------------------------
# Configuration owners
# ---------------------------------------------------------------------------


async def run_config_path() -> ControlOperationOutcome:
    start = time.perf_counter()
    info = config.config_path_info()
    result = _strip_legacy_semantics(info)
    side_effects = ControlSideEffectFacts(config=ControlMutationFacts(read=True))
    if bool(info.get("ok")):
        return _outcome(
            "config.path",
            ControlOperationStatus.COMPLETE,
            result,
            side_effects=side_effects,
            metadata=ExecutionMetadata("config.path", _elapsed_ms(start)),
        )
    return _outcome(
        "config.path",
        ControlOperationStatus.FAILED,
        result,
        error=_config_error(info.get("error") or "config storage unavailable"),
        side_effects=side_effects,
        metadata=ExecutionMetadata("config.path", _elapsed_ms(start)),
    )


async def run_config_list(show_secrets: bool = False) -> ControlOperationOutcome:
    start = time.perf_counter()
    path_info = config.config_path_info()
    side_effects = ControlSideEffectFacts(config=ControlMutationFacts(read=True))
    if not path_info.get("ok"):
        result = _strip_legacy_semantics(path_info)
        result["values"] = {}
        return _outcome(
            "config.list",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(path_info.get("error") or "config storage unavailable"),
            side_effects=side_effects,
            metadata=ExecutionMetadata("config.list", _elapsed_ms(start)),
        )
    load_error = path_info.get("config_load_error")
    if load_error is not None:
        result = _strip_legacy_semantics(path_info)
        result["values"] = {}
        return _outcome(
            "config.list",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(
                f"config file is malformed ({load_error.get('kind')}); repair the file before editing"
            ),
            side_effects=side_effects,
            metadata=ExecutionMetadata("config.list", _elapsed_ms(start)),
        )
    try:
        config.validate_saved_model_routes()
        config.validate_effective_model_routes()
    except ModelRoutesConfigurationError as exc:
        result = {"config_file": str(path_info.get("config_file") or ""), "values": {}}
        return _outcome(
            "config.list",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(str(exc)),
            side_effects=side_effects,
            metadata=ExecutionMetadata("config.list", _elapsed_ms(start)),
        )
    values = config.get_saved_config(masked=not show_secrets)
    if show_secrets:
        routes = values.get("SMART_SEARCH_MODEL_ROUTES")
        if isinstance(routes, list):
            values["SMART_SEARCH_MODEL_ROUTES"] = config._mask_nested_secrets(routes)
    result = {"config_file": str(path_info.get("config_file") or ""), "values": values}
    return _outcome(
        "config.list",
        ControlOperationStatus.COMPLETE,
        result,
        side_effects=side_effects,
        metadata=ExecutionMetadata("config.list", _elapsed_ms(start)),
    )


async def run_config_set(key: str, value: str) -> ControlOperationOutcome:
    start = time.perf_counter()
    normalized_key = str(key).strip().upper()
    read_facts = ControlMutationFacts(read=True)
    try:
        config.set_config_value(key, value)
    except ConfigStorageError as exc:
        result = {"config_file": str(config.config_file), "key": normalized_key}
        return _outcome(
            "config.set",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(str(exc)),
            side_effects=ControlSideEffectFacts(
                config=ControlMutationFacts(read=True, write_attempted=True, write_committed=False)
            ),
            metadata=ExecutionMetadata("config.set", _elapsed_ms(start)),
        )
    except ValueError as exc:
        result = {"config_file": str(config.config_file)}
        return _outcome(
            "config.set",
            ControlOperationStatus.FAILED,
            result,
            error=_parameter_error(str(exc)),
            side_effects=ControlSideEffectFacts(config=read_facts),
            metadata=ExecutionMetadata("config.set", _elapsed_ms(start)),
        )
    saved = config.get_saved_config(masked=True)
    result = {
        "config_file": str(config.config_file),
        "key": normalized_key,
        "value": saved.get(normalized_key, ""),
    }
    return _outcome(
        "config.set",
        ControlOperationStatus.COMPLETE,
        result,
        side_effects=ControlSideEffectFacts(
            config=ControlMutationFacts(read=True, write_attempted=True, write_committed=True)
        ),
        metadata=ExecutionMetadata("config.set", _elapsed_ms(start)),
    )


async def run_config_unset(key: str) -> ControlOperationOutcome:
    start = time.perf_counter()
    normalized_key = str(key).strip().upper()
    try:
        config.unset_config_value(key)
    except ConfigStorageError as exc:
        result = {"config_file": str(config.config_file), "key": normalized_key}
        return _outcome(
            "config.unset",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(str(exc)),
            side_effects=ControlSideEffectFacts(
                config=ControlMutationFacts(read=True, write_attempted=True, write_committed=False)
            ),
            metadata=ExecutionMetadata("config.unset", _elapsed_ms(start)),
        )
    except ValueError as exc:
        result = {"config_file": str(config.config_file), "key": normalized_key}
        return _outcome(
            "config.unset",
            ControlOperationStatus.FAILED,
            result,
            error=_parameter_error(str(exc)),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata("config.unset", _elapsed_ms(start)),
        )
    result = {"config_file": str(config.config_file), "key": normalized_key}
    return _outcome(
        "config.unset",
        ControlOperationStatus.COMPLETE,
        result,
        side_effects=ControlSideEffectFacts(
            config=ControlMutationFacts(read=True, write_attempted=True, write_committed=True)
        ),
        metadata=ExecutionMetadata("config.unset", _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Provider catalog and routes owners
# ---------------------------------------------------------------------------


def _catalog_outcome(operation: str, *, include_status: bool) -> ControlOperationOutcome:
    start = time.perf_counter()
    data = provider_catalog(include_status=include_status)
    providers = [item for item in data.get("providers") or [] if isinstance(item, Mapping)]
    result = {"providers": providers, "provider_count": len(providers)}
    return _outcome(
        operation,
        ControlOperationStatus.COMPLETE,
        result,
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_provider_catalog_list() -> ControlOperationOutcome:
    return _catalog_outcome("provider.catalog.list", include_status=False)


async def run_provider_catalog_status() -> ControlOperationOutcome:
    return _catalog_outcome("provider.catalog.status", include_status=True)


def _routes_outcome(operation: str, action: str) -> ControlOperationOutcome:
    start = time.perf_counter()
    data = control_executors._model_routes_result(action)
    result = _strip_legacy_semantics(data)
    if bool(data.get("ok")):
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=_config_error(str(data.get("error") or "model route configuration failed")),
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_provider_routes_current() -> ControlOperationOutcome:
    return _routes_outcome("provider.routes.current", "current")


async def run_provider_routes_list() -> ControlOperationOutcome:
    return _routes_outcome("provider.routes.list", "list")


async def run_provider_routes_add(
    route_id: str,
    provider: str,
    api_url: str,
    api_key: str,
    model: str,
    *,
    tools: str = "",
    stream: bool = False,
    fallback_models: str = "",
) -> ControlOperationOutcome:
    start = time.perf_counter()
    secrets = (str(api_key), str(api_url))
    route: dict[str, Any] = {
        "id": route_id,
        "provider": provider,
        "api_url": api_url,
        "api_key": api_key,
        "model": model,
    }
    if tools:
        route["tools"] = tools
    if provider.strip().lower() in {"openai", "openai-compatible", "chat-completions"}:
        route["stream"] = bool(stream)
        if fallback_models:
            route["fallback_models"] = fallback_models
    try:
        config.add_model_route(route)
    except ConfigStorageError as exc:
        result = {"action": "add", "config_file": str(config.config_file)}
        return _outcome(
            "provider.routes.add",
            ControlOperationStatus.FAILED,
            result,
            error=ExecutionError("config_error", str(sanitize_data(str(exc), secrets)), False),
            side_effects=ControlSideEffectFacts(
                config=ControlMutationFacts(read=True, write_attempted=True, write_committed=False)
            ),
            metadata=ExecutionMetadata("provider.routes.add", _elapsed_ms(start)),
        )
    except ModelRoutesConfigurationError as exc:
        result = {"action": "add", "config_file": str(config.config_file)}
        return _outcome(
            "provider.routes.add",
            ControlOperationStatus.FAILED,
            result,
            error=ExecutionError("config_error", str(sanitize_data(str(exc), secrets)), False),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata("provider.routes.add", _elapsed_ms(start)),
        )
    except ValueError as exc:
        result = {"action": "add", "config_file": str(config.config_file)}
        return _outcome(
            "provider.routes.add",
            ControlOperationStatus.FAILED,
            result,
            error=ExecutionError("parameter_error", str(sanitize_data(str(exc), secrets)), False),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata("provider.routes.add", _elapsed_ms(start)),
        )
    data = control_executors._model_routes_result("add")
    result = _strip_legacy_semantics(data)
    return _outcome(
        "provider.routes.add",
        ControlOperationStatus.COMPLETE,
        result,
        side_effects=ControlSideEffectFacts(
            config=ControlMutationFacts(read=True, write_attempted=True, write_committed=True)
        ),
        metadata=ExecutionMetadata("provider.routes.add", _elapsed_ms(start)),
    )


async def run_provider_routes_remove(route_id: str) -> ControlOperationOutcome:
    start = time.perf_counter()
    try:
        config.remove_model_route(route_id)
    except ConfigStorageError as exc:
        result = {"action": "remove", "config_file": str(config.config_file)}
        return _outcome(
            "provider.routes.remove",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(str(exc)),
            side_effects=ControlSideEffectFacts(
                config=ControlMutationFacts(read=True, write_attempted=True, write_committed=False)
            ),
            metadata=ExecutionMetadata("provider.routes.remove", _elapsed_ms(start)),
        )
    except ModelRoutesConfigurationError as exc:
        result = {"action": "remove", "config_file": str(config.config_file)}
        return _outcome(
            "provider.routes.remove",
            ControlOperationStatus.FAILED,
            result,
            error=_config_error(str(exc)),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata("provider.routes.remove", _elapsed_ms(start)),
        )
    except ValueError as exc:
        result = {"action": "remove", "config_file": str(config.config_file)}
        return _outcome(
            "provider.routes.remove",
            ControlOperationStatus.FAILED,
            result,
            error=_parameter_error(str(exc)),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata("provider.routes.remove", _elapsed_ms(start)),
        )
    data = control_executors._model_routes_result("remove")
    result = _strip_legacy_semantics(data)
    return _outcome(
        "provider.routes.remove",
        ControlOperationStatus.COMPLETE,
        result,
        side_effects=ControlSideEffectFacts(
            config=ControlMutationFacts(read=True, write_attempted=True, write_committed=True)
        ),
        metadata=ExecutionMetadata("provider.routes.remove", _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Provider probe owner
# ---------------------------------------------------------------------------


def _probe_failure_outcome(
    operation: str,
    base: Mapping[str, Any],
    *,
    error: ExecutionError,
    start: float,
    result: Mapping[str, Any] | None = None,
    provider: str = "",
) -> ControlOperationOutcome:
    data = result if result is not None else _strip_legacy_semantics(base)
    targets = (str(provider),) if provider else ()
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        data,
        error=error,
        network=ControlNetworkFacts(attempted=False, targets=targets),
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_provider_probe(provider: str) -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "provider.probe"
    base = provider_probe_base(provider)
    provider_id = str(base.get("provider") or "")

    if base.get("error_type") == "parameter_error":
        return _probe_failure_outcome(
            operation, base, start=start, error=_parameter_error(str(base.get("error") or "unknown provider")), provider=provider_id
        )
    if base.get("status") == "unsupported":
        return _probe_failure_outcome(
            operation, base, start=start, error=_config_error(str(base.get("error") or "provider has no safe probe")), provider=provider_id
        )
    if base.get("availability_reason") == "invalid_model_routes":
        message = str(base.get("availability_error") or "Invalid SMART_SEARCH_MODEL_ROUTES")
        result = _strip_legacy_semantics(base)
        result["status"] = "config_error"
        result["message"] = message
        return _probe_failure_outcome(operation, base, start=start, error=_config_error(message), result=result, provider=provider_id)
    if not base.get("configured"):
        message = str(base.get("availability_reason") or "not_configured")
        result = _strip_legacy_semantics(base)
        result["status"] = "not_configured"
        result["message"] = message
        return _probe_failure_outcome(operation, base, start=start, error=_config_error(message), result=result, provider=provider_id)
    if not base.get("enabled"):
        message = str(base.get("availability_reason") or "disabled")
        result = _strip_legacy_semantics(base)
        result["status"] = "disabled"
        result["message"] = message
        return _probe_failure_outcome(operation, base, start=start, error=_config_error(message), result=result, provider=provider_id)
    if not base.get("eligible"):
        message = str(
            base.get("availability_error") or base.get("availability_reason") or "provider_not_eligible"
        )
        result = _strip_legacy_semantics(base)
        result["status"] = "config_error"
        result["message"] = message
        result["response_time_ms"] = 0
        return _probe_failure_outcome(operation, base, start=start, error=_config_error(message), result=result, provider=provider_id)

    if base.get("route_family"):
        try:
            routes = [
                item for item in _main_search_provider_configs() if item.get("provider") == provider_id
            ]
        except ValueError as exc:
            result = _strip_legacy_semantics(base)
            result["status"] = "config_error"
            result["message"] = str(exc)
            result["routes"] = []
            return _probe_failure_outcome(operation, base, start=start, error=_config_error(str(exc)), result=result, provider=provider_id)
        if not routes:
            message = f"No configured {provider_id} routes"
            result = _strip_legacy_semantics(base)
            result["status"] = "not_configured"
            result["message"] = message
            result["routes"] = []
            return _probe_failure_outcome(operation, base, start=start, error=_config_error(message), result=result, provider=provider_id)

        route_results: list[dict[str, Any]] = []
        for route in routes:
            probe = _normalize_probe_status(dict(await control_executors._safe_test_main_provider_connection(route)))
            probe["route_id"] = route.get("route_id") or ""
            probe["provider"] = provider_id
            route_results.append(probe)
        ok = any(item.get("status") == "ok" for item in route_results)
        primary = next((item for item in route_results if item.get("status") == "ok"), route_results[0])
        status = "ok" if ok else str(primary.get("status") or "network_error")
        result = _strip_legacy_semantics(base)
        result["status"] = status
        result["message"] = str(primary.get("message") or ("ok" if ok else status))
        result["response_time_ms"] = primary.get("response_time_ms", 0)
        result["routes"] = route_results
        if ok:
            if any(item.get("status") != "ok" for item in route_results):
                return _outcome(
                    operation,
                    ControlOperationStatus.DEGRADED,
                    result,
                    warnings=("one or more provider routes failed their probe",),
                    network=ControlNetworkFacts(attempted=True, targets=(provider_id,)),
                    side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
                    metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
                )
            return _outcome(
                operation,
                ControlOperationStatus.COMPLETE,
                result,
                network=ControlNetworkFacts(attempted=True, targets=(provider_id,)),
                side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
                metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
            )
        error_type = _error_type_for_status(status, network_attempted=True)
        return _outcome(
            operation,
            ControlOperationStatus.FAILED,
            result,
            error=ExecutionError(error_type, str(primary.get("message") or status), True),
            network=ControlNetworkFacts(attempted=True, targets=(provider_id,)),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )

    probe = await run_probe_adapter(provider_id)
    status = str(probe.get("status") or "provider_error")
    network_attempted = status not in {"not_configured", "disabled", "config_error", "unsupported"}
    ok = status == "ok"
    result = _strip_legacy_semantics(base)
    result["status"] = status
    result["message"] = str(probe.get("message") or status)
    result["response_time_ms"] = probe.get("response_time_ms", 0)
    if probe.get("experimental"):
        result["experimental"] = True
    if ok:
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            network=ControlNetworkFacts(attempted=network_attempted, targets=(provider_id,) if network_attempted else ()),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    error_type = _error_type_for_status(status, network_attempted=network_attempted)
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=ExecutionError(error_type, str(probe.get("message") or status), network_attempted),
        network=ControlNetworkFacts(attempted=network_attempted, targets=(provider_id,) if network_attempted else ()),
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Doctor owners
# ---------------------------------------------------------------------------


def _connection_checks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the canonical doctor connection-check rows from owner facts."""
    checks: list[dict[str, Any]] = []
    main = data.get("main_search_connection_tests")
    if isinstance(main, Mapping):
        for check_id, raw in main.items():
            if isinstance(raw, Mapping):
                checks.append(
                    {
                        "id": str(check_id),
                        **{key: raw[key] for key in ("route_id", "provider", "status", "message", "response_time_ms") if key in raw},
                    }
                )
    primary = data.get("primary_connection_test")
    if isinstance(primary, Mapping) and not main:
        checks.append(
            {
                "id": "primary",
                **{key: primary[key] for key in ("route_id", "provider", "status", "message", "response_time_ms") if key in primary},
            }
        )
    for key, raw in data.items():
        if key == "primary_connection_test" or not key.endswith("_connection_test") or not isinstance(raw, Mapping):
            continue
        checks.append(
            {
                "id": key.removesuffix("_connection_test").replace("_", "-"),
                **{item: raw[item] for item in ("status", "message", "response_time_ms") if item in raw},
            }
        )
    return checks


def _doctor_network_facts(data: Mapping[str, Any]) -> ControlNetworkFacts:
    """Actual network targets recorded at the probe execution boundary."""
    targets: list[str] = []
    for check in _connection_checks(data):
        status = str(check.get("status") or "")
        if status in _LOCAL_PROBE_STATUSES:
            continue
        target = str(check.get("provider") or check.get("id") or "")
        if target and target not in targets:
            targets.append(target)
    return ControlNetworkFacts(attempted=bool(targets), targets=tuple(targets))


def _any_doctor_check_ok(data: Mapping[str, Any]) -> bool:
    return any(str(check.get("status") or "") == "ok" for check in _connection_checks(data))


async def run_doctor_status() -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "doctor.status"
    data = control_executors._execute_doctor_status()
    result = _strip_legacy_semantics(data)
    if bool(data.get("ok")):
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            network=ControlNetworkFacts(),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    error_type = str(data.get("error_type") or "config_error")
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=ExecutionError(error_type, str(data.get("error") or "local readiness failed"), False),
        network=ControlNetworkFacts(),
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_doctor_probe() -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "doctor.probe"
    data = await control_executors._execute_doctor_probe()
    result = _strip_legacy_semantics(data)
    ok = bool(data.get("ok"))
    owner_degraded = bool(ok and data.get("degraded"))
    partial = bool(not ok and _any_doctor_check_ok(data))
    network = _doctor_network_facts(data)
    if ok and not owner_degraded:
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    if owner_degraded or partial:
        warning = str(data.get("degraded_reason") or "doctor completed with reduced coverage")
        if partial and not owner_degraded:
            warning = "aggregate doctor completed with partial connectivity"
        return _outcome(
            operation,
            ControlOperationStatus.DEGRADED,
            result,
            warnings=(warning,),
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    error_type = str(data.get("error_type") or "internal_error")
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=ExecutionError(error_type, str(data.get("error") or "aggregate doctor failed"), False),
        network=network,
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Dev route / diagnose / smoke owners
# ---------------------------------------------------------------------------


async def run_dev_route_explain(query: str, validation: str = "", mode: str = "") -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.route.explain"
    data = await capability_service.route(query, validation=validation, mode=mode)
    result = _strip_legacy_semantics(data)
    if not bool(data.get("ok")):
        return _outcome(
            operation,
            ControlOperationStatus.FAILED,
            result,
            error=_parameter_error(str(data.get("error") or "route explanation failed")),
            network=ControlNetworkFacts(),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    degraded = bool(data.get("degraded"))
    engines = [str(item) for item in data.get("router_engines_used") or []]
    unavailable = "unavailable" in str(data.get("degraded_reason") or "")
    attempted = any(item in {"embeddings", "classifier"} for item in engines) or unavailable
    targets = tuple(item for item in ("embeddings", "classifier") if item in engines)
    network = ControlNetworkFacts(attempted=attempted, targets=targets)
    if degraded:
        return _outcome(
            operation,
            ControlOperationStatus.DEGRADED,
            result,
            warnings=(str(data.get("degraded_reason") or "route completed with reduced router coverage"),),
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    return _outcome(
        operation,
        ControlOperationStatus.COMPLETE,
        result,
        network=network,
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_dev_route_calibrate(models: str = "") -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.route.calibrate"
    data = await capability_service.route_calibrate(models=models)
    result = _strip_legacy_semantics(data)
    model_results = [item for item in data.get("model_results") or [] if isinstance(item, Mapping)]
    successful = [item for item in model_results if item.get("ok")]
    failed_models = list(data.get("failed_models") or [])
    degraded = bool(successful and failed_models)
    attempted_models = [
        str(item.get("model") or "")
        for item in model_results
        if item.get("ok") or str(item.get("error_type") or "") != "config_error"
    ]
    network = ControlNetworkFacts(attempted=bool(attempted_models), targets=tuple(attempted_models))
    if not bool(data.get("ok")):
        error_types = {
            str(item.get("error_type") or "provider_error")
            for item in model_results
            if not item.get("ok")
        }
        error_type = "config_error" if "config_error" in error_types else "provider_error"
        return _outcome(
            operation,
            ControlOperationStatus.FAILED,
            result,
            error=ExecutionError(error_type, str(data.get("error") or "no embedding model could be calibrated"), False),
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    if degraded:
        return _outcome(
            operation,
            ControlOperationStatus.DEGRADED,
            result,
            warnings=("one or more calibration models failed",),
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    return _outcome(
        operation,
        ControlOperationStatus.COMPLETE,
        result,
        network=network,
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_dev_diagnose_openai_compatible(timeout_seconds: float = 30.0) -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.diagnose.openai-compatible"
    data = await control_executors._execute_diagnose_openai_compatible(timeout_seconds=timeout_seconds)
    result = _strip_legacy_semantics(data)
    checks = list(data.get("checks") or [])
    network = ControlNetworkFacts(attempted=bool(checks), targets=("openai-compatible",) if checks else ())
    if bool(data.get("ok")):
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    error_type = str(data.get("error_type") or "network_error")
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=ExecutionError(error_type, str(data.get("error") or "diagnosis failed"), False),
        network=network,
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_dev_smoke(mode: str = "mock") -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.smoke"
    mode = (mode or "mock").strip().lower()
    if mode not in {"mock", "live"}:
        return _outcome(
            operation,
            ControlOperationStatus.FAILED,
            {"mode": mode},
            error=_parameter_error("mode must be mock or live"),
            network=ControlNetworkFacts(),
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    data = await control_executors._execute_smoke(mode)
    result = _strip_legacy_semantics(data)
    degraded_cases = list(data.get("degraded_cases") or [])
    degraded = bool(data.get("ok") and degraded_cases)
    live = mode == "live"
    cases = [item for item in data.get("cases") or [] if isinstance(item, Mapping)]
    attempted = bool(
        live
        and any(
            item.get("provider_attempts")
            or str(item.get("status") or "") in {"ok", "warning", "timeout", "error", "network_error", "provider_error"}
            for item in cases
        )
    )
    if attempted:
        # Live targets are the providers that actually started connection
        # attempts, in first-seen order. Mock case data is never a network
        # fact: when no network was started, targets stay empty even if the
        # legacy/mock result carries ``providers_used``.
        targets = tuple(
            dict.fromkeys(
                str(item.get("provider"))
                for case in cases
                for item in (case.get("provider_attempts") or [])
                if isinstance(item, Mapping) and item.get("provider")
            )
        )
        if not targets:
            targets = tuple(str(item) for item in data.get("providers_used") or [])
    else:
        targets = ()
    network = ControlNetworkFacts(attempted=attempted, targets=targets)
    if not bool(data.get("ok")):
        return _outcome(
            operation,
            ControlOperationStatus.FAILED,
            result,
            error=ExecutionError("network_error", str(data.get("error") or "smoke checks failed"), True),
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    if degraded:
        return _outcome(
            operation,
            ControlOperationStatus.DEGRADED,
            result,
            warnings=("live smoke completed with optional degraded cases",),
            network=network,
            side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    return _outcome(
        operation,
        ControlOperationStatus.COMPLETE,
        result,
        network=network,
        side_effects=ControlSideEffectFacts(config=ControlMutationFacts(read=True)),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Regression execution (shared by v1 dispatch and typed control)
# ---------------------------------------------------------------------------

_REGRESSION_PATTERNS = (
    "tests/test_cli_v2.py",
    "tests/test_cli_v3.py",
    "tests/test_research_cli.py",
    "tests/test_control_operations.py",
    "tests/test_control_plane_v3_contract.py",
    "tests/test_evidence_operations.py",
    "tests/test_execution_primitives.py",
    "tests/test_providers_new.py",
    "tests/test_jina_provider.py",
    "tests/test_zhipu_mcp_provider.py",
    "tests/test_regression.py",
    "tests/test_release_workflow.py",
)


def _regression_test_files_available(root: Path) -> bool:
    return all((root / pattern).exists() for pattern in _REGRESSION_PATTERNS)


# Config-free safety net bounding a single source-checkout regression
# subprocess. A hung child would otherwise block the async v3 owner
# indefinitely (R2-P1-1); with this bound subprocess.run kills and reaps the
# child before raising TimeoutExpired. It is a hang guard, not a performance
# gate, so it is deliberately far above a healthy run's wall time.
_REGRESSION_SUBPROCESS_TIMEOUT_SECONDS = 600.0

# Stable nonzero exit code reported when the regression subprocess times out.
# 124 matches the conventional ``timeout(1)`` exit code and stays distinct
# from any pytest failure code, so consumers can recognize the timeout case
# even without the ``subprocess_timeout`` result flag.
_REGRESSION_TIMEOUT_EXIT_CODE = 124


def _run_coroutine_sync(coro: Any) -> Any:
    """Run a coroutine from sync or already-running event-loop contexts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # v3 dispatch already owns the loop; run the fallback smoke in a worker thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _legacy_exit_code(data: Mapping[str, Any]) -> int:
    """v1 exit-code mapping used by the packaged mock-smoke regression fallback."""
    if data.get("ok", False):
        return 0
    error_type = str(data.get("error_type") or "")
    if error_type == "config_error":
        return 3
    if error_type == "parameter_error":
        return 2
    if error_type in {"network_error", "provider_error", "evidence_error"}:
        return 4
    return 5


# Upper bound for failed-case records exposed on a failed source-checkout
# regression; keeps the V3 result bounded even for a massively broken tree.
_FAILED_CASE_LIMIT = 500


def _is_failed_node_id(node_id: str) -> bool:
    """Conservative shape check for a pytest short-summary node id.

    Accepts only ``<path>.py::<test>`` records (optionally class-scoped or
    parametrized) with printable ASCII characters. Anything else is dropped
    so arbitrary captured output can never leak into the typed result.
    """
    if "::" not in node_id or "\n" in node_id or "\r" in node_id:
        return False
    path_part, sep, tail = node_id.partition("::")
    if not path_part.endswith(".py") or not sep or not tail:
        return False
    if any(ch.isspace() for ch in node_id):
        return False
    return all(32 <= ord(ch) < 127 for ch in node_id)


def _extract_failed_test_cases(output: str) -> list[str]:
    """Extract deterministic failed pytest node ids from captured child output.

    Pytest's short-test-summary records are the only stable failed-node
    format: ``FAILED <node-id> - <reason>``. Only records whose node id
    matches ``_is_failed_node_id`` are kept, deduplicated in first-seen
    order, and capped at ``_FAILED_CASE_LIMIT``. Returns an empty list when
    no stable record is recognized. Raw output lines are never surfaced.
    """
    cases: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("FAILED "):
            continue
        node_id = line[len("FAILED "):].split(" - ", 1)[0].strip()
        if not _is_failed_node_id(node_id) or node_id in seen:
            continue
        seen.add(node_id)
        cases.append(node_id)
        if len(cases) >= _FAILED_CASE_LIMIT:
            break
    return cases


def _execute_regression() -> dict[str, Any]:
    """Run the shared regression owner and return a structured process outcome.

    Source checkouts start the pytest subprocess under a config-free bounded
    timeout. Packaged installs without the test tree fall back to mock smoke
    without starting a subprocess. Safe to call from both the sync v1 path and
    the async v3 dispatcher.
    """
    root = Path(__file__).resolve().parents[2]
    if not _regression_test_files_available(root):
        data = _run_coroutine_sync(control_executors._execute_smoke("mock"))
        return {
            "ok": bool(data.get("ok", False)),
            "exit_code": _legacy_exit_code(data),
            "subprocess_started": False,
            "fallback": "mock_smoke",
            "failed_cases": list(data.get("failed_cases") or []),
        }
    cmd = [sys.executable, "-m", "pytest", *_REGRESSION_PATTERNS]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=_REGRESSION_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has already killed and reaped the child before
        # raising, so no orphaned process or zombie remains. ``exc`` may carry
        # partially captured stdout/stderr bytes; those stay private under the
        # same containment rule as pytest output, because test output can
        # include sensitive fixture values. The outcome is deterministic:
        # stable nonzero exit, explicit timeout flag, empty failed cases.
        return {
            "ok": False,
            "exit_code": _REGRESSION_TIMEOUT_EXIT_CODE,
            "subprocess_started": True,
            "fallback": "",
            "test_files": list(_REGRESSION_PATTERNS),
            "failed_cases": [],
            "subprocess_timeout": True,
        }
    code = completed.returncode
    data = {
        "ok": code == 0,
        "exit_code": code,
        "subprocess_started": True,
        "fallback": "",
        "test_files": list(_REGRESSION_PATTERNS),
    }
    if code != 0:
        # The pytest short summary on stdout is the only stable failed-node
        # source; child stdout/stderr remain captured and never become result
        # fields because test output can include sensitive fixture values.
        data["failed_cases"] = _extract_failed_test_cases(completed.stdout or "")
    return data


async def run_dev_regression() -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.regression"
    data = _execute_regression()
    started = bool(data.get("subprocess_started"))
    ok = bool(data.get("ok"))
    side_effects = ControlSideEffectFacts(subprocess_started=started)
    result = {
        key: data[key]
        for key in (
            "exit_code",
            "subprocess_started",
            "fallback",
            "test_files",
            "failed_cases",
            "subprocess_timeout",
        )
        if key in data
    }
    if ok:
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            side_effects=side_effects,
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    if started:
        # Deterministic message only; the timeout flag on the result is the
        # explicit indicator and no exception string or child output leaks.
        message = (
            "regression subprocess timed out"
            if data.get("subprocess_timeout")
            else "regression subprocess failed"
        )
        error = ExecutionError("subprocess_error", message, False)
    elif data.get("fallback") == "mock_smoke":
        error = ExecutionError("config_error", str(data.get("error") or "packaged mock-smoke regression failed"), False)
    else:
        error = ExecutionError("internal_error", str(data.get("error") or "regression checks failed"), False)
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=error,
        side_effects=side_effects,
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Skills owners
# ---------------------------------------------------------------------------


def _skill_target_ids(all_targets: bool, targets: str) -> list[str]:
    if all_targets:
        return [target.target_id for target in SKILL_TARGETS]
    return parse_skill_targets(targets)


def _skills_file_facts(update: bool, *, read: bool = True) -> ControlSideEffectFacts:
    return ControlSideEffectFacts(filesystem=ControlMutationFacts(read=read))


def _skills_parameter_failure(operation: str, exc: Exception, start: float) -> ControlOperationOutcome:
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        {"selected": []},
        error=_parameter_error(str(exc)),
        side_effects=_skills_file_facts(update=operation.endswith("update")),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


def _skills_execution_failure(
    operation: str,
    exc: Exception,
    target_ids: list[str],
    start: float,
) -> ControlOperationOutcome:
    error_type = "filesystem_error" if isinstance(exc, OSError) else "parameter_error"
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        {"selected": target_ids},
        error=ExecutionError(error_type, str(exc), False),
        side_effects=_skills_file_facts(update=operation.endswith("update")),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_dev_skills_status(
    targets: str = "",
    *,
    all_targets: bool = False,
    project_root: str | Path | None = None,
) -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.skills.status"
    try:
        target_ids = _skill_target_ids(all_targets, targets)
    except SkillInstallError as exc:
        return _skills_parameter_failure(operation, exc, start)
    try:
        data = skill_installer.status_skill_targets(target_ids, project_root=project_root)
    except (SkillInstallError, OSError) as exc:
        return _skills_execution_failure(operation, exc, target_ids, start)
    result = _strip_legacy_semantics(data)
    if bool(data.get("ok")):
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            side_effects=_skills_file_facts(update=False),
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    error_type = str(data.get("error_type") or "internal_error")
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=ExecutionError(error_type, str(data.get("error") or "skill status failed"), False),
        side_effects=_skills_file_facts(update=False),
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


async def run_dev_skills_update(
    targets: str = "",
    *,
    all_targets: bool = False,
    project_root: str | Path | None = None,
) -> ControlOperationOutcome:
    start = time.perf_counter()
    operation = "dev.skills.update"
    try:
        target_ids = _skill_target_ids(all_targets, targets)
    except SkillInstallError as exc:
        return _skills_parameter_failure(operation, exc, start)
    try:
        data = skill_installer.install_skill_targets(target_ids, project_root=project_root)
    except (SkillInstallError, OSError) as exc:
        return _skills_execution_failure(operation, exc, target_ids, start)
    result = _strip_legacy_semantics(data)
    installed_count = int(data.get("installed_count") or 0)
    failed_count = int(data.get("failed_count") or 0)
    degraded = bool(installed_count and failed_count)
    error_type = str(data.get("error_type") or "")
    attempted = bool(data.get("write_attempted", True))
    filesystem_error = error_type in {"filesystem_error", "file_system_error"} or bool(
        not data.get("ok") and not degraded and failed_count > 0
    )
    side_effects = ControlSideEffectFacts(
        filesystem=ControlMutationFacts(
            read=True,
            write_attempted=attempted,
            write_committed=bool(installed_count),
        )
    )
    if bool(data.get("ok")) and not degraded:
        return _outcome(
            operation,
            ControlOperationStatus.COMPLETE,
            result,
            side_effects=side_effects,
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    if degraded:
        return _outcome(
            operation,
            ControlOperationStatus.DEGRADED,
            result,
            warnings=("some skill targets were updated and some failed",),
            side_effects=side_effects,
            metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
        )
    error_type_final = "filesystem_error" if filesystem_error else (error_type or "internal_error")
    return _outcome(
        operation,
        ControlOperationStatus.FAILED,
        result,
        error=ExecutionError(error_type_final, str(data.get("error") or "skill update failed"), False),
        side_effects=side_effects,
        metadata=ExecutionMetadata(operation, _elapsed_ms(start)),
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

ControlOwner = Callable[..., Awaitable[ControlOperationOutcome]]

CONTROL_OPERATION_OWNERS: dict[str, ControlOwner] = {
    "config.path": run_config_path,
    "config.list": run_config_list,
    "config.set": run_config_set,
    "config.unset": run_config_unset,
    "provider.catalog.list": run_provider_catalog_list,
    "provider.catalog.status": run_provider_catalog_status,
    "provider.probe": run_provider_probe,
    "provider.routes.current": run_provider_routes_current,
    "provider.routes.list": run_provider_routes_list,
    "provider.routes.add": run_provider_routes_add,
    "provider.routes.remove": run_provider_routes_remove,
    "doctor.status": run_doctor_status,
    "doctor.probe": run_doctor_probe,
    "dev.route.explain": run_dev_route_explain,
    "dev.route.calibrate": run_dev_route_calibrate,
    "dev.diagnose.openai-compatible": run_dev_diagnose_openai_compatible,
    "dev.smoke": run_dev_smoke,
    "dev.regression": run_dev_regression,
    "dev.skills.status": run_dev_skills_status,
    "dev.skills.update": run_dev_skills_update,
}


__all__ = [
    "CONTROL_OPERATION_IDS",
    "CONTROL_OPERATION_OWNERS",
    "CONTROL_OPERATION_SET",
    "ControlMutationFacts",
    "ControlNetworkFacts",
    "ControlOperationOutcome",
    "ControlOperationStatus",
    "ControlSideEffectFacts",
    "_connection_checks",
    "_execute_regression",
    "run_config_list",
    "run_config_path",
    "run_config_set",
    "run_config_unset",
    "run_dev_diagnose_openai_compatible",
    "run_dev_regression",
    "run_dev_route_calibrate",
    "run_dev_route_explain",
    "run_dev_skills_status",
    "run_dev_skills_update",
    "run_dev_smoke",
    "run_doctor_probe",
    "run_doctor_status",
    "run_provider_catalog_list",
    "run_provider_catalog_status",
    "run_provider_probe",
    "run_provider_routes_add",
    "run_provider_routes_current",
    "run_provider_routes_list",
    "run_provider_routes_remove",
]