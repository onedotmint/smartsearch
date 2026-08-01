"""Deterministic v3 envelope fixtures used by contract tests."""

from __future__ import annotations

from smart_search.control_plane_contract import (
    ERROR_RETRYABILITY,
    V3Envelope,
    V3Error,
    V3ErrorCode,
    V3Meta,
    V3Mutation,
    V3Network,
    V3SideEffects,
    V3Status,
)


def complete_config_list() -> V3Envelope:
    return V3Envelope(
        V3Status.COMPLETE,
        "config",
        "config.list",
        {"config_file": "/tmp/config.json", "values": {}},
        V3Network("none", "none"),
        V3SideEffects(config=V3Mutation(read=True)),
        meta=V3Meta(1),
    )


def complete_empty_catalog() -> V3Envelope:
    return V3Envelope(
        V3Status.COMPLETE,
        "provider",
        "provider.catalog.list",
        {"provider_count": 0, "providers": []},
        V3Network("none", "none"),
        V3SideEffects(config=V3Mutation(read=True)),
    )


def complete_config_write() -> V3Envelope:
    return V3Envelope(
        V3Status.COMPLETE,
        "config",
        "config.set",
        {"config_file": "/tmp/config.json", "key": "XAI_API_KEY", "value": "***"},
        V3Network("none", "none"),
        V3SideEffects(
            config=V3Mutation(read=True, write_attempted=True, write_committed=True),
        ),
    )


def degraded_probe() -> V3Envelope:
    return V3Envelope(
        V3Status.DEGRADED,
        "provider",
        "provider.probe",
        {"provider": "tavily", "status": "ok", "routes": []},
        V3Network("explicit", "single_provider", True, ("tavily",)),
        V3SideEffects(config=V3Mutation(read=True)),
        meta=V3Meta(warnings=("one route failed",)),
    )


def failed_local_configuration() -> V3Envelope:
    code = V3ErrorCode.CONFIGURATION_ERROR
    return V3Envelope(
        V3Status.FAILED,
        "provider",
        "provider.probe",
        {"provider": "tavily", "status": "config_error"},
        V3Network("explicit", "single_provider", False, ("tavily",)),
        V3SideEffects(config=V3Mutation(read=True)),
        error=V3Error(code, "provider is not configured", ERROR_RETRYABILITY[code], {}),
    )


def failed_filesystem() -> V3Envelope:
    code = V3ErrorCode.FILE_SYSTEM_ERROR
    return V3Envelope(
        V3Status.FAILED,
        "dev",
        "dev.skills.update",
        {"selected": ["codex"], "failed": [{"target": "codex"}]},
        V3Network("none", "none"),
        V3SideEffects(
            filesystem=V3Mutation(read=True, write_attempted=True, write_committed=False),
        ),
        error=V3Error(code, "skill target could not be written", ERROR_RETRYABILITY[code], {}),
    )


def failed_subprocess() -> V3Envelope:
    code = V3ErrorCode.SUBPROCESS_FAILED
    return V3Envelope(
        V3Status.FAILED,
        "dev",
        "dev.regression",
        {"exit_code": 1, "subprocess_started": True},
        V3Network("none", "none"),
        V3SideEffects(subprocess_started=True),
        error=V3Error(code, "regression subprocess failed", ERROR_RETRYABILITY[code], {}),
    )
