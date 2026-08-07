"""Narrow strict research workflow CLI route.

Routes only the canonical ``research run QUERY --format json`` path to the
strict typed Research Workflow owner (``research_workflow``) and its contract
serializer (``research_workflow_contract``). The route validates options and
input before any owner/provider/config work: invalid argv fails with a strict
workflow INVALID_ARGUMENT result and never imports the legacy research
service, providers, or configuration.

The route is schema-neutral and does not participate in schema selection. The
``--schema-version`` selector, the offline ``research plan`` planner, and the
legacy bare ``research`` path are untouched.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from .research_workflow_contract import (
    EXIT_INTERNAL,
    EXIT_INVALID_ARGUMENT,
    exit_code_for,
    serialize_workflow,
    workflow_parser_error_result,
)

# Options that are never valid on the strict ``research run`` route. Values
# are user-facing reasons surfaced in the strict INVALID_ARGUMENT result.
_RESEARCH_RUN_FORBIDDEN_OPTIONS: Mapping[str, str] = {
    "synthesize": "research.run does not define answer synthesis",
    "output": "research.run never projects an output path",
    "force": "research.run never projects an output path",
    "evidence_dir": "research.run records logical artifacts only",
    "fallback": "research.run does not define provider fallback",
    "prompt_dir": "research.run does not load prompt files",
    "search_prompt_file": "research.run does not load prompt files",
    "fetch_prompt_file": "research.run does not load prompt files",
    "research_prompt_file": "research.run does not load prompt files",
    "trace": "research.run has no trace events",
}

# Defaults for every forbidden option name; a programmatic dispatch call that
# passes a non-default value is rejected exactly like an explicit argv option.
_DEFAULTS: Mapping[str, Any] = {
    "synthesize": False,
    "output": "",
    "force": False,
    "evidence_dir": "",
    "fallback": "auto",
    "prompt_dir": "",
    "search_prompt_file": "",
    "fetch_prompt_file": "",
    "research_prompt_file": "",
    "trace": False,
}


def _json_stdout(payload: Mapping[str, Any]) -> None:
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def _argv_option_names(argv: list[str] | None) -> set[str]:
    """Return explicitly supplied long-option names before an argv ``--`` marker."""
    names: set[str] = set()
    for token in argv or ():
        if token == "--":
            break
        if token.startswith("--"):
            names.add(token[2:].split("=", 1)[0])
    return names


def _reject_invalid_options(args: Any, *, argv: list[str] | None) -> tuple[str, str] | None:
    """Return ``(message, argument)`` for the first invalid option, else None."""
    fmt = getattr(args, "format", "json")
    if fmt and fmt != "json":
        return (
            f"research.run emits only strict workflow JSON; got --format {fmt}",
            "--format",
        )
    present = _argv_option_names(argv)
    for name, reason in _RESEARCH_RUN_FORBIDDEN_OPTIONS.items():
        option = name.replace("_", "-")
        if name in present:
            return f"{reason}; omit --{option}", f"--{option}"
        if hasattr(args, name) and getattr(args, name) != _DEFAULTS[name]:
            return f"{reason}; got --{option}", f"--{option}"
    return None


def emit_parser_error(message: str, argument: str = "") -> int:
    """Emit exactly one strict workflow INVALID_ARGUMENT JSON result."""
    details = {"argument": argument} if argument else None
    result = workflow_parser_error_result(message, details)
    _json_stdout(serialize_workflow(result))
    return EXIT_INVALID_ARGUMENT


def _internal_error_payload() -> dict[str, Any]:
    """Strict workflow FAILED result for an unexpected execution failure.

    The message is a stable classified string; raw provider, filesystem, or
    configuration exception text never enters the stable workflow result.
    """
    from .research_plan import RESEARCH_PLAN_SCHEMA_VERSION, ResearchPlan
    from .research_workflow import (
        WorkflowError,
        WorkflowErrorCode,
        WorkflowMeta,
        WorkflowOutcome,
        WorkflowStatus,
    )

    outcome = WorkflowOutcome(
        status=WorkflowStatus.FAILED,
        plan=ResearchPlan(RESEARCH_PLAN_SCHEMA_VERSION, ()),
        stages=(),
        evidence=(),
        citations=(),
        gaps=(),
        attempts=(),
        artifacts=(),
        error=WorkflowError(
            WorkflowErrorCode.INTERNAL_ERROR,
            "research.run failed unexpectedly",
            False,
        ),
        meta=WorkflowMeta("workflow-internal", 0),
    )
    return serialize_workflow(outcome)


async def dispatch(args: Any, *, argv: list[str] | None = None) -> int:
    # Option and input validation happen before any owner/provider/config
    # import, so invalid argv can never reach the workflow owner or the legacy
    # research service.
    rejected = _reject_invalid_options(args, argv=argv)
    if rejected is not None:
        message, argument = rejected
        return emit_parser_error(message, argument)

    query = getattr(args, "query", "")
    if not isinstance(query, str) or not query.strip():
        return emit_parser_error("research.run requires a non-blank query")

    try:
        from .research_service import build_research_workflow_plan
        from .research_workflow import WorkflowRequest, run_research_workflow

        budget = {
            "fast": "quick",
            "balanced": "standard",
            "deep": "deep",
        }.get(getattr(args, "profile", ""), getattr(args, "budget", "deep") or "deep")
        plan = build_research_workflow_plan(query.strip(), budget=budget)
        request = WorkflowRequest(query=query.strip(), plan=plan)
        outcome = await run_research_workflow(request)
        payload = serialize_workflow(outcome)
    except Exception as exc:  # noqa: BLE001 - classified below, never leaked
        from .research_workflow import WorkflowDomainError

        if isinstance(exc, WorkflowDomainError):
            return emit_parser_error(str(exc))
        _json_stdout(_internal_error_payload())
        return EXIT_INTERNAL
    _json_stdout(payload)
    return exit_code_for(payload, fail_on_degraded=bool(getattr(args, "fail_on_degraded", False)))


__all__ = ["dispatch", "emit_parser_error"]