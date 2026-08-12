"""Version 3 control-plane operation inventory and machine contract.

This module intentionally depends only on the standard library and security
redaction helpers. Parser-error paths must not import config, services,
providers, or the evidence-first v2 contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .security import sanitize_data


V3_SCHEMA_VERSION = "3"
V3_TOP_LEVEL_FIELDS = (
    "schema_version",
    "ok",
    "status",
    "command",
    "operation",
    "result",
    "network",
    "side_effects",
    "error",
    "meta",
)

EXIT_OK = 0
EXIT_INVALID_ARGUMENT = 2
EXIT_CONFIGURATION = 3
EXIT_OPERATION = 4
EXIT_INTERNAL = 5
EXIT_DEGRADED = 6


class V3ContractError(ValueError):
    """Raised when a typed or serialized v3 result violates the contract."""


class V3Status(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


class V3ErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FILE_SYSTEM_ERROR = "FILE_SYSTEM_ERROR"
    SUBPROCESS_FAILED = "SUBPROCESS_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_RETRYABILITY: dict[V3ErrorCode, bool] = {
    V3ErrorCode.INVALID_ARGUMENT: False,
    V3ErrorCode.CONFIGURATION_ERROR: False,
    V3ErrorCode.AUTHENTICATION_FAILED: False,
    V3ErrorCode.UPSTREAM_TIMEOUT: True,
    V3ErrorCode.PROVIDER_UNAVAILABLE: True,
    V3ErrorCode.FILE_SYSTEM_ERROR: False,
    V3ErrorCode.SUBPROCESS_FAILED: False,
    V3ErrorCode.INTERNAL_ERROR: False,
}

ERROR_EXIT_CODES: dict[V3ErrorCode, int] = {
    V3ErrorCode.INVALID_ARGUMENT: EXIT_INVALID_ARGUMENT,
    V3ErrorCode.CONFIGURATION_ERROR: EXIT_CONFIGURATION,
    V3ErrorCode.AUTHENTICATION_FAILED: EXIT_OPERATION,
    V3ErrorCode.UPSTREAM_TIMEOUT: EXIT_OPERATION,
    V3ErrorCode.PROVIDER_UNAVAILABLE: EXIT_OPERATION,
    V3ErrorCode.FILE_SYSTEM_ERROR: EXIT_INTERNAL,
    V3ErrorCode.SUBPROCESS_FAILED: EXIT_INTERNAL,
    V3ErrorCode.INTERNAL_ERROR: EXIT_INTERNAL,
}


@dataclass(frozen=True)
class V3OperationDescriptor:
    path: tuple[str, ...]
    command: str
    operation: str
    network_policy: str
    network_scope: str
    config_read: bool = False
    config_write: bool = False
    filesystem_read: bool = False
    filesystem_write: bool = False
    subprocess: bool = False
    permitted_options: frozenset[str] = frozenset()
    stability: str = "stable"


def _descriptor(
    path: str,
    command: str,
    operation: str,
    network_policy: str,
    network_scope: str,
    **kwargs: Any,
) -> V3OperationDescriptor:
    return V3OperationDescriptor(
        tuple(path.split()),
        command,
        operation,
        network_policy,
        network_scope,
        **kwargs,
    )


V3_OPERATIONS: tuple[V3OperationDescriptor, ...] = (
    _descriptor("config path", "config", "config.path", "none", "none", config_read=True),
    _descriptor("config list", "config", "config.list", "none", "none", config_read=True),
    _descriptor("config set", "config", "config.set", "none", "none", config_read=True, config_write=True),
    _descriptor("config unset", "config", "config.unset", "none", "none", config_read=True, config_write=True),
    _descriptor("provider list", "provider", "provider.catalog.list", "none", "none", config_read=True),
    _descriptor("provider status", "provider", "provider.catalog.status", "none", "none", config_read=True),
    _descriptor("provider probe", "provider", "provider.probe", "explicit", "single_provider", config_read=True),
    _descriptor("provider routes current", "provider", "provider.routes.current", "none", "none", config_read=True),
    _descriptor("provider routes list", "provider", "provider.routes.list", "none", "none", config_read=True),
    _descriptor(
        "provider routes add", "provider", "provider.routes.add", "none", "none",
        config_read=True, config_write=True,
        permitted_options=frozenset({"id", "provider", "api-url", "api-key", "model", "tools", "fallback-models", "stream", "no-stream"}),
    ),
    _descriptor("provider routes remove", "provider", "provider.routes.remove", "none", "none", config_read=True, config_write=True),
    _descriptor("doctor status", "doctor", "doctor.status", "none", "none", config_read=True),
    _descriptor("doctor probe", "doctor", "doctor.probe", "explicit", "aggregate", config_read=True),
    _descriptor(
        "dev route-explain", "dev", "dev.route.explain", "configured", "configured_router",
        config_read=True, permitted_options=frozenset({"validation", "router-mode"}),
    ),
    _descriptor(
        "dev route-calibrate", "dev", "dev.route.calibrate", "configured", "configured_router",
        config_read=True, permitted_options=frozenset({"models"}),
    ),
    _descriptor(
        "dev diagnose openai-compatible", "dev", "dev.diagnose.openai-compatible", "explicit", "diagnostic",
        config_read=True, permitted_options=frozenset({"timeout"}),
    ),
    _descriptor(
        "dev smoke", "dev", "dev.smoke", "mock_or_live", "diagnostic",
        config_read=True, permitted_options=frozenset({"mode", "mock", "live"}),
    ),
    _descriptor("dev regression", "dev", "dev.regression", "none", "none", subprocess=True),
    _descriptor(
        "dev skills status", "dev", "dev.skills.status", "none", "none",
        filesystem_read=True, permitted_options=frozenset({"targets", "all", "skills-root"}),
    ),
    _descriptor(
        "dev skills update", "dev", "dev.skills.update", "none", "none",
        filesystem_read=True, filesystem_write=True,
        permitted_options=frozenset({"targets", "all", "skills-root"}),
    ),
)

V3_OPERATION_BY_ID = {item.operation: item for item in V3_OPERATIONS}
V3_OPERATION_IDS = tuple(item.operation for item in V3_OPERATIONS)
if len(V3_OPERATION_BY_ID) != len(V3_OPERATIONS):
    raise RuntimeError("duplicate v3 operation id")
if len({item.path for item in V3_OPERATIONS}) != len(V3_OPERATIONS):
    raise RuntimeError("duplicate v3 canonical path")


V3_VALUE_OPTIONS = frozenset({
    "format",
    "output",
    "prompt-dir",
    "search-prompt-file",
    "fetch-prompt-file",
    "research-prompt-file",
})


def operation_for_argv(argv: list[str] | None) -> V3OperationDescriptor | None:
    """Resolve only a canonical root-global v3 namespace leaf.

    Option tokens (and the values of value-taking options such as
    ``--format json``) are skipped wherever they appear, so a misplaced
    option never masks the canonical leaf path.
    """
    args = list(argv or ())
    body: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        if token in {"--fail-on-degraded"}:
            index += 1
            continue
        if token.startswith("--"):
            name = token[2:].split("=", 1)[0]
            if name in V3_VALUE_OPTIONS and "=" not in token:
                index += 2
                continue
            index += 1
            continue
        body.append(token)
        index += 1
    body_tuple = tuple(body)
    for descriptor in sorted(V3_OPERATIONS, key=lambda item: len(item.path), reverse=True):
        if body_tuple[: len(descriptor.path)] == descriptor.path:
            return descriptor
    return None


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _tuple_strings(value: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise V3ContractError(f"{name} must be a collection of strings")
    result = tuple(value)
    if any(not isinstance(item, str) for item in result):
        raise V3ContractError(f"{name} must contain only strings")
    return result


@dataclass(frozen=True)
class V3Error:
    code: V3ErrorCode | str
    message: str
    retryable: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            code = self.code if isinstance(self.code, V3ErrorCode) else V3ErrorCode(self.code)
        except ValueError as exc:
            raise V3ContractError(f"unknown v3 error code: {self.code!r}") from exc
        if not isinstance(self.message, str) or not self.message.strip():
            raise V3ContractError("error.message must be non-empty")
        if type(self.retryable) is not bool or self.retryable != ERROR_RETRYABILITY[code]:
            raise V3ContractError(f"retryable does not match registry for {code.value}")
        if not isinstance(self.details, Mapping):
            raise V3ContractError("error.details must be an object")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "details", _freeze_json(self.details))


@dataclass(frozen=True)
class V3Network:
    policy: str
    scope: str
    attempted: bool = False
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", _tuple_strings(self.targets, "network.targets"))


@dataclass(frozen=True)
class V3Mutation:
    read: bool = False
    write_attempted: bool = False
    write_committed: bool = False


@dataclass(frozen=True)
class V3SideEffects:
    config: V3Mutation = field(default_factory=V3Mutation)
    filesystem: V3Mutation = field(default_factory=V3Mutation)
    subprocess_started: bool = False


@dataclass(frozen=True)
class V3Meta:
    duration_ms: int = 0
    warnings: tuple[str, ...] = ()
    deprecations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise V3ContractError("meta.duration_ms must be a non-negative integer")
        object.__setattr__(self, "warnings", _tuple_strings(self.warnings, "meta.warnings"))
        object.__setattr__(self, "deprecations", _tuple_strings(self.deprecations, "meta.deprecations"))


@dataclass(frozen=True)
class V3Envelope:
    status: V3Status | str
    command: str
    operation: str | None
    result: Mapping[str, Any]
    network: V3Network
    side_effects: V3SideEffects
    error: V3Error | None = None
    meta: V3Meta = field(default_factory=V3Meta)

    def __post_init__(self) -> None:
        try:
            status = self.status if isinstance(self.status, V3Status) else V3Status(self.status)
        except ValueError as exc:
            raise V3ContractError(f"unknown v3 status: {self.status!r}") from exc
        if not isinstance(self.result, Mapping):
            raise V3ContractError("result must be an object")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "result", _freeze_json(self.result))

    @property
    def ok(self) -> bool:
        return self.status is not V3Status.FAILED


_STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
_MUTATION_SCHEMA = {
    "type": "object",
    "required": ["read", "write_attempted", "write_committed"],
    "properties": {
        "read": {"type": "boolean"},
        "write_attempted": {"type": "boolean"},
        "write_committed": {"type": "boolean"},
    },
    "additionalProperties": False,
}

V3_ENVELOPE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://smart-search.dev/schemas/control-plane/v3/envelope.json",
    "title": "Smart Search v3 control-plane result",
    "type": "object",
    "required": list(V3_TOP_LEVEL_FIELDS),
    "properties": {
        "schema_version": {"const": V3_SCHEMA_VERSION},
        "ok": {"type": "boolean"},
        "status": {"enum": [item.value for item in V3Status]},
        "command": {"type": "string", "minLength": 1},
        "operation": {"type": ["string", "null"], "enum": [*V3_OPERATION_IDS, None]},
        "result": {"type": "object"},
        "network": {"$ref": "#/$defs/network"},
        "side_effects": {"$ref": "#/$defs/side_effects"},
        "error": {"anyOf": [{"$ref": "#/$defs/error"}, {"type": "null"}]},
        "meta": {"$ref": "#/$defs/meta"},
    },
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"status": {"const": "complete"}}, "required": ["status"]},
            "then": {"properties": {"ok": {"const": True}, "error": {"type": "null"}}},
        },
        {
            "if": {"properties": {"status": {"const": "degraded"}}, "required": ["status"]},
            "then": {
                "properties": {
                    "ok": {"const": True},
                    "error": {"type": "null"},
                    "meta": {"properties": {"warnings": {"minItems": 1}}},
                }
            },
        },
        {
            "if": {"properties": {"status": {"const": "failed"}}, "required": ["status"]},
            "then": {"properties": {"ok": {"const": False}, "error": {"$ref": "#/$defs/error"}}},
        },
    ],
    "$defs": {
        "error": {
            "type": "object",
            "required": ["code", "message", "retryable", "details"],
            "properties": {
                "code": {"enum": [item.value for item in V3ErrorCode]},
                "message": {"type": "string", "minLength": 1},
                "retryable": {"type": "boolean"},
                "details": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "network": {
            "type": "object",
            "required": ["policy", "scope", "attempted", "targets"],
            "properties": {
                "policy": {"enum": ["none", "explicit", "configured", "mock_or_live"]},
                "scope": {"enum": ["none", "single_provider", "aggregate", "configured_router", "diagnostic"]},
                "attempted": {"type": "boolean"},
                "targets": _STRING_ARRAY,
            },
            "additionalProperties": False,
        },
        "side_effects": {
            "type": "object",
            "required": ["config", "filesystem", "subprocess"],
            "properties": {
                "config": {"$ref": "#/$defs/mutation"},
                "filesystem": {"$ref": "#/$defs/mutation"},
                "subprocess": {
                    "type": "object",
                    "required": ["started"],
                    "properties": {"started": {"type": "boolean"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "mutation": _MUTATION_SCHEMA,
        "meta": {
            "type": "object",
            "required": ["duration_ms", "warnings", "deprecations"],
            "properties": {
                "duration_ms": {"type": "integer", "minimum": 0},
                "warnings": _STRING_ARRAY,
                "deprecations": _STRING_ARRAY,
            },
            "additionalProperties": False,
        },
    },
    "x-smart-search-semantic-validator": "smart_search.control_plane_contract.validate_envelope_dict",
}


def _require_exact_fields(raw: Mapping[str, Any], expected: Iterable[str], name: str) -> None:
    expected_set = set(expected)
    if set(raw) != expected_set:
        raise V3ContractError(f"{name} fields must be exactly {sorted(expected_set)!r}")


def _validate_json(value: Any, path: str = "result") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise V3ContractError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise V3ContractError(f"{path} contains a non-string key")
            _validate_json(item, f"{path}.{key}")
        return
    raise V3ContractError(f"{path} contains non-JSON value {type(value).__name__}")


def validate_envelope_dict(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise V3ContractError("v3 envelope must be an object")
    _require_exact_fields(raw, V3_TOP_LEVEL_FIELDS, "envelope")
    if raw["schema_version"] != V3_SCHEMA_VERSION or type(raw["ok"]) is not bool:
        raise V3ContractError("invalid schema_version or ok")
    try:
        status = V3Status(raw["status"])
    except (TypeError, ValueError) as exc:
        raise V3ContractError("invalid status") from exc
    command = raw["command"]
    operation = raw["operation"]
    if not isinstance(command, str) or not command.strip():
        raise V3ContractError("command must be non-empty")
    if operation is not None and operation not in V3_OPERATION_BY_ID:
        raise V3ContractError("unknown operation")
    if not isinstance(raw["result"], dict):
        raise V3ContractError("result must be an object")

    network = raw["network"]
    side_effects = raw["side_effects"]
    meta = raw["meta"]
    if not all(isinstance(item, dict) for item in (network, side_effects, meta)):
        raise V3ContractError("network, side_effects, and meta must be objects")
    _require_exact_fields(network, ("policy", "scope", "attempted", "targets"), "network")
    _require_exact_fields(side_effects, ("config", "filesystem", "subprocess"), "side_effects")
    _require_exact_fields(meta, ("duration_ms", "warnings", "deprecations"), "meta")
    for name in ("config", "filesystem"):
        mutation = side_effects.get(name)
        if not isinstance(mutation, dict):
            raise V3ContractError(f"side_effects.{name} must be an object")
        _require_exact_fields(mutation, ("read", "write_attempted", "write_committed"), f"side_effects.{name}")
        if any(type(mutation[key]) is not bool for key in mutation):
            raise V3ContractError(f"side_effects.{name} fields must be booleans")
        if mutation["write_committed"] and not mutation["write_attempted"]:
            raise V3ContractError(f"side_effects.{name}.write_committed requires write_attempted")
    subprocess_data = side_effects.get("subprocess")
    if not isinstance(subprocess_data, dict):
        raise V3ContractError("side_effects.subprocess must be an object")
    _require_exact_fields(subprocess_data, ("started",), "side_effects.subprocess")
    if type(subprocess_data["started"]) is not bool:
        raise V3ContractError("side_effects.subprocess.started must be boolean")

    if network["policy"] not in {"none", "explicit", "configured", "mock_or_live"}:
        raise V3ContractError("invalid network policy")
    if network["scope"] not in {"none", "single_provider", "aggregate", "configured_router", "diagnostic"}:
        raise V3ContractError("invalid network scope")
    if type(network["attempted"]) is not bool:
        raise V3ContractError("network.attempted must be boolean")
    if not isinstance(network["targets"], list) or any(
        not isinstance(item, str) or not item for item in network["targets"]
    ):
        raise V3ContractError("network.targets must contain non-empty strings")
    if len(network["targets"]) != len(set(network["targets"])):
        raise V3ContractError("network.targets must be unique")
    if network["policy"] == "none" and network["attempted"]:
        raise V3ContractError("network policy none cannot be attempted")

    if type(meta["duration_ms"]) is not int or meta["duration_ms"] < 0:
        raise V3ContractError("meta.duration_ms must be a non-negative integer")
    for name in ("warnings", "deprecations"):
        if not isinstance(meta[name], list) or any(not isinstance(item, str) for item in meta[name]):
            raise V3ContractError(f"meta.{name} must be a string array")

    error = raw["error"]
    if status is V3Status.FAILED:
        if raw["ok"] is not False or not isinstance(error, dict):
            raise V3ContractError("failed results require ok=false and an error")
    else:
        if raw["ok"] is not True or error is not None:
            raise V3ContractError("complete/degraded results require ok=true and error=null")
    if status is V3Status.DEGRADED and not meta["warnings"]:
        raise V3ContractError("degraded results require at least one warning")
    if isinstance(error, dict):
        _require_exact_fields(error, ("code", "message", "retryable", "details"), "error")
        try:
            code = V3ErrorCode(error["code"])
        except (TypeError, ValueError) as exc:
            raise V3ContractError("unknown error code") from exc
        if not isinstance(error["message"], str) or not error["message"].strip():
            raise V3ContractError("error.message must be non-empty")
        if type(error["retryable"]) is not bool or error["retryable"] != ERROR_RETRYABILITY[code]:
            raise V3ContractError("error retryability does not match registry")
        if not isinstance(error["details"], dict):
            raise V3ContractError("error.details must be an object")

    if operation is None:
        if status is not V3Status.FAILED or not isinstance(error, dict) or error["code"] != V3ErrorCode.INVALID_ARGUMENT.value:
            raise V3ContractError("operation=null is reserved for pre-dispatch INVALID_ARGUMENT")
    else:
        descriptor = V3_OPERATION_BY_ID[operation]
        if command != descriptor.command:
            raise V3ContractError("command does not match operation descriptor")
        if network["policy"] != descriptor.network_policy or network["scope"] != descriptor.network_scope:
            raise V3ContractError("network declaration does not match operation descriptor")
        config_effect = side_effects["config"]
        filesystem_effect = side_effects["filesystem"]
        if config_effect["read"] and not descriptor.config_read:
            raise V3ContractError("operation does not permit config reads")
        if not descriptor.config_write and (config_effect["write_attempted"] or config_effect["write_committed"]):
            raise V3ContractError("operation does not permit config writes")
        if filesystem_effect["read"] and not descriptor.filesystem_read:
            raise V3ContractError("operation does not permit filesystem reads")
        if not descriptor.filesystem_write and (filesystem_effect["write_attempted"] or filesystem_effect["write_committed"]):
            raise V3ContractError("operation does not permit filesystem writes")
        if subprocess_data["started"] and not descriptor.subprocess:
            raise V3ContractError("operation does not permit subprocess execution")

    _validate_json(raw)
    return raw


def validate_result(result: V3Envelope) -> V3Envelope:
    serialize_result(result)
    return result


def serialize_result(result: V3Envelope, *, secrets: Iterable[str] = ()) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": V3_SCHEMA_VERSION,
        "ok": result.ok,
        "status": result.status.value,
        "command": result.command,
        "operation": result.operation,
        "result": _thaw_json(result.result),
        "network": {
            "policy": result.network.policy,
            "scope": result.network.scope,
            "attempted": result.network.attempted,
            "targets": list(result.network.targets),
        },
        "side_effects": {
            "config": {
                "read": result.side_effects.config.read,
                "write_attempted": result.side_effects.config.write_attempted,
                "write_committed": result.side_effects.config.write_committed,
            },
            "filesystem": {
                "read": result.side_effects.filesystem.read,
                "write_attempted": result.side_effects.filesystem.write_attempted,
                "write_committed": result.side_effects.filesystem.write_committed,
            },
            "subprocess": {"started": result.side_effects.subprocess_started},
        },
        "error": None if result.error is None else {
            "code": result.error.code.value,
            "message": result.error.message,
            "retryable": result.error.retryable,
            "details": _thaw_json(result.error.details),
        },
        "meta": {
            "duration_ms": result.meta.duration_ms,
            "warnings": list(result.meta.warnings),
            "deprecations": list(result.meta.deprecations),
        },
    }
    safe = sanitize_data(payload, tuple(secrets))
    if not isinstance(safe, dict):
        raise V3ContractError("sanitized envelope is not an object")
    return validate_envelope_dict(safe)


def parser_error_result(
    command: str | None,
    operation: str | None,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> V3Envelope:
    descriptor = V3_OPERATION_BY_ID.get(operation or "")
    return V3Envelope(
        status=V3Status.FAILED,
        command=descriptor.command if descriptor else (command or "unknown"),
        operation=descriptor.operation if descriptor else None,
        result={},
        network=V3Network(
            descriptor.network_policy if descriptor else "none",
            descriptor.network_scope if descriptor else "none",
            False,
            (),
        ),
        side_effects=V3SideEffects(),
        error=V3Error(
            V3ErrorCode.INVALID_ARGUMENT,
            message or "invalid control-plane arguments",
            ERROR_RETRYABILITY[V3ErrorCode.INVALID_ARGUMENT],
            dict(details or {}),
        ),
        meta=V3Meta(),
    )


def exit_code_for(result: V3Envelope | Mapping[str, Any], *, fail_on_degraded: bool = False) -> int:
    status_value = result.status.value if isinstance(result, V3Envelope) else result.get("status")
    if status_value == V3Status.DEGRADED.value:
        return EXIT_DEGRADED if fail_on_degraded else EXIT_OK
    if status_value == V3Status.COMPLETE.value:
        return EXIT_OK
    if isinstance(result, V3Envelope):
        code_value = result.error.code.value if result.error is not None else ""
    else:
        error = result.get("error")
        code_value = error.get("code", "") if isinstance(error, Mapping) else ""
    try:
        return ERROR_EXIT_CODES[V3ErrorCode(code_value)]
    except (KeyError, ValueError):
        return EXIT_INTERNAL


__all__ = [
    "ERROR_EXIT_CODES",
    "ERROR_RETRYABILITY",
    "EXIT_DEGRADED",
    "EXIT_INTERNAL",
    "V3ContractError",
    "V3Envelope",
    "V3Error",
    "V3ErrorCode",
    "V3Meta",
    "V3Mutation",
    "V3Network",
    "V3OperationDescriptor",
    "V3SideEffects",
    "V3Status",
    "V3_ENVELOPE_JSON_SCHEMA",
    "V3_OPERATIONS",
    "V3_OPERATION_BY_ID",
    "V3_OPERATION_IDS",
    "V3_SCHEMA_VERSION",
    "V3_TOP_LEVEL_FIELDS",
    "exit_code_for",
    "operation_for_argv",
    "parser_error_result",
    "serialize_result",
    "validate_envelope_dict",
    "validate_result",
]
