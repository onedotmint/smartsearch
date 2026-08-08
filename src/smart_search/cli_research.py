"""Narrow strict research workflow CLI route.

Routes the canonical ``research plan QUERY`` and ``research run QUERY`` paths
(and only those) to the strict typed Research Workflow owner
(``research_workflow``) and its contract serializer
(``research_workflow_contract``). ``--format json|markdown|content`` selects
one stdout document after the workflow result is validated; JSON is the only
stable machine contract and the presentation views are pure human renderings
of the same validated redacted payload. The route validates options and
input before any owner/provider/config work: invalid argv fails with a strict
workflow INVALID_ARGUMENT result and never imports providers or
configuration.

The route is schema-neutral and does not participate in schema selection.
The schema selector is removed from the CLI surface entirely; command domain
alone decides the family. Missing-query diagnostics name the exact canonical
spelling (``research plan`` or ``research run``) and keep the workflow strict
INVALID_ARGUMENT envelope.
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
    if fmt not in ("json", "markdown", "content"):
        return (
            f"research.run supports only --format json|markdown|content; got --format {fmt}",
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


def emit_parser_error(
    message: str,
    argument: str = "",
    details: dict[str, str] | None = None,
) -> int:
    """Emit exactly one strict workflow INVALID_ARGUMENT JSON result."""
    if details is not None:
        error_details: dict[str, object] = dict(details)
    elif argument:
        error_details = {"argument": argument}
    else:
        error_details = None
    result = workflow_parser_error_result(message, error_details)
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
    # Canonical ``research plan QUERY`` is the offline plan member of the
    # workflow family: it builds the typed plan and emits a plan-only workflow
    # result (operation research.run, empty execution collections).
    if getattr(args, "namespace_operation", None) == "research-plan":
        return await _dispatch_plan(args, argv=argv)

    # Option and input validation happen before any owner/provider/config
    # import, so invalid argv can never reach the workflow owner or the legacy
    # research service.
    rejected = _reject_invalid_options(args, argv=argv)
    if rejected is not None:
        message, argument = rejected
        return emit_parser_error(message, argument)

    query = getattr(args, "query", "")
    if not isinstance(query, str) or not query.strip():
        return emit_parser_error("research run requires a non-blank query")

    try:
        from .research_service import build_research_workflow_plan
        from .research_workflow import WorkflowRequest, run_research_workflow

        budget = _budget_from_args(args)
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
    return _emit_payload(args, payload)


def _budget_from_args(args: Any) -> str:
    return {
        "fast": "quick",
        "balanced": "standard",
        "deep": "deep",
    }.get(getattr(args, "profile", ""), getattr(args, "budget", "deep") or "deep")


def _emit_payload(args: Any, payload: Mapping[str, Any]) -> int:
    fmt = getattr(args, "format", "json")
    if fmt == "json":
        _json_stdout(payload)
    else:
        # The validated workflow payload above is the contract authority; the
        # presentation view is a pure one-way human rendering of the same
        # validated redacted payload. Exactly one stdout document is emitted.
        from .presentation import render_workflow

        sys.stdout.write(render_workflow(payload, fmt))
    return exit_code_for(payload, fail_on_degraded=bool(getattr(args, "fail_on_degraded", False)))


async def _dispatch_plan(args: Any, *, argv: list[str] | None = None) -> int:
    """Emit a plan-only workflow result for canonical ``research plan QUERY``.

    No owner, provider, config, or cache code runs: the typed plan is built
    offline and the workflow result carries it with empty execution
    collections (operation research.run, status complete).
    """
    rejected = _reject_invalid_options(args, argv=argv)
    if rejected is not None:
        message, argument = rejected
        return emit_parser_error(message, argument)

    query = getattr(args, "query", "")
    if not isinstance(query, str) or not query.strip():
        return emit_parser_error("research plan requires a non-blank query")

    try:
        from .research_plan import RESEARCH_PLAN_SCHEMA_VERSION, ResearchPlan
        from .research_service import build_research_workflow_plan
        from .research_workflow import WorkflowMeta, WorkflowOutcome, WorkflowStatus

        budget = _budget_from_args(args)
        plan = build_research_workflow_plan(query.strip(), budget=budget)
        if not isinstance(plan, ResearchPlan):
            plan = ResearchPlan(RESEARCH_PLAN_SCHEMA_VERSION, ())
        outcome = WorkflowOutcome(
            status=WorkflowStatus.COMPLETE,
            plan=plan,
            stages=(),
            evidence=(),
            citations=(),
            gaps=(),
            attempts=(),
            artifacts=(),
            error=None,
            meta=WorkflowMeta("research-plan", 0),
        )
        payload = serialize_workflow(outcome)
    except Exception as exc:  # noqa: BLE001 - classified below, never leaked
        from .research_workflow import WorkflowDomainError

        if isinstance(exc, WorkflowDomainError):
            return emit_parser_error(str(exc))
        _json_stdout(_internal_error_payload())
        return EXIT_INTERNAL
    return _emit_payload(args, payload)


__all__ = ["dispatch", "emit_parser_error"]