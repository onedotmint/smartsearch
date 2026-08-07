"""Pure typed-family presentation views for V2, V3, and Research Workflow.

This module is the human-readable presentation layer for the typed contract
families. It is a pure, one-way transformation: each view accepts exactly one
already-validated, already-redacted envelope payload produced by the family's
own serializer (``v2_contract.serialize_result``,
``control_plane_contract.serialize_result``, or
``research_workflow_contract.serialize_workflow``) and returns one stdout
document as text. It never accepts a raw Provider/service dictionary, never
opens files, never writes stdout, never invokes providers, owners, fallback,
cache/budget behavior, configuration, or any other business code, and never
reclassifies status, error, or exit policy.

Documentation of the compatibility promise: the JSON serializer output of the
three contract families is the only stable machine contract. Markdown and
content presentations are for people and have NO field-level machine
compatibility promise; their labels, ordering, and layout may change at any
time. They preserve redaction already applied by the serializer and can never
add raw nested data to output.

Import surface: only the three pure contract validators and the standard
library. No providers, service, config, owners, runtime cache, or legacy
renderer modules are imported here.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .control_plane_contract import validate_envelope_dict as _validate_v3
from .research_workflow_contract import validate_workflow_dict as _validate_workflow
from .v2_contract import validate_envelope_dict as _validate_v2

PRESENTATION_FORMATS = frozenset({"markdown", "content"})


class PresentationError(ValueError):
    """Raised when a presentation view receives an invalid payload or format."""


# ---------------------------------------------------------------------------
# Shared pure formatting helpers (local to this module; no V1 renderer use)
# ---------------------------------------------------------------------------


def _status_label(value: Any) -> str:
    return str(value or "-").upper()


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _one_line(value: Any, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _compact(value: Any) -> str:
    """Render a JSON-safe value as a single-line text fragment."""
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _cell(value: Any) -> str:
    return _one_line(_compact(value)).replace("|", r"\|")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = list(row)[: len(headers)]
        cells.extend([""] * (len(headers) - len(cells)))
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return lines


def _fence(text: str) -> list[str]:
    fence = "```"
    if fence in text:
        text = text.replace(fence, "` ` `")
    return ["```text", text, "```"]


def _kv_lines(mapping: Mapping[str, Any]) -> list[str]:
    return [f"- {key}: {_one_line(_compact(value))}" for key, value in mapping.items()]


def _error_lines(error: Mapping[str, Any] | None) -> list[str]:
    if not error:
        return []
    lines = ["", "## Error"]
    lines.append(f"- Code: `{error.get('code', '-')}`")
    lines.append(f"- Message: {error.get('message', '-')}")
    retryable = error.get("retryable")
    if retryable is not None:
        lines.append(f"- Retryable: {_yes_no(retryable)}")
    details = error.get("details")
    if details:
        lines.append(f"- Details: {_one_line(_compact(details))}")
    return lines


def _warning_lines(
    warnings: Sequence[Any] | None, deprecations: Sequence[Any] | None = None
) -> list[str]:
    lines: list[str] = []
    for warning in warnings or ():
        lines.append(f"- {warning}")
    for deprecation in deprecations or ():
        lines.append(f"- (deprecated) {deprecation}")
    if not lines:
        return []
    return ["", "## Warnings", *lines]


def _terminal_safe(text: str) -> str:
    """Return text that can be written to the current stdout encoding.

    Characters that cannot be encoded are replaced with backslash escapes so
    Unicode-heavy content stays bounded and never raises on narrow terminals.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    errors = getattr(sys.stdout, "errors", None) or "strict"
    try:
        text.encode(encoding, errors=errors)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding)


def _render(
    payload: Mapping[str, Any],
    validator: Any,
    markdown_fn: Any,
    content_fn: Any,
    fmt: str,
) -> str:
    if fmt not in PRESENTATION_FORMATS:
        raise PresentationError(
            f"unsupported presentation format: {fmt!r}; use markdown or content"
        )
    try:
        validated = validator(dict(payload))
    except (TypeError, ValueError) as exc:
        raise PresentationError(
            "presentation requires a validated typed envelope payload"
        ) from exc
    text = content_fn(validated) if fmt == "content" else markdown_fn(validated)
    if not text.endswith("\n"):
        text += "\n"
    return _terminal_safe(text)


# ---------------------------------------------------------------------------
# V2 evidence-first Core views
# ---------------------------------------------------------------------------

_V2_TITLES = {
    "search": "Search",
    "fetch": "Fetch",
    "map": "Site Map",
    "capabilities": "Capabilities",
}


def _v2_title(command: str) -> str:
    return _V2_TITLES.get(command, command or "Result")


def _v2_evidence_markdown(evidence: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    candidates = evidence.get("candidates") or []
    if candidates:
        rows = [
            [
                index,
                item.get("title", ""),
                item.get("provider", ""),
                item.get("resource", ""),
                _one_line(item.get("snippet", ""), 120),
            ]
            for index, item in enumerate(candidates, 1)
        ]
        lines.extend(["", "## Candidates"])
        lines.extend(_table(["#", "Title", "Provider", "Resource", "Snippet"], rows))
    items = evidence.get("items") or []
    if items:
        lines.extend(["", "## Evidence"])
    for item in items:
        lines.extend(["", f"### {item.get('title') or item.get('resource') or item.get('id', '')}"])
        if item.get("provider"):
            lines.append(f"- Provider: {item['provider']}")
        if item.get("resource"):
            lines.append(f"- Resource: `{item['resource']}`")
        if item.get("content"):
            lines.extend(_fence(str(item["content"])))
    citations = evidence.get("citations") or []
    if citations:
        lines.extend(["", "## Citations"])
        for citation in citations:
            label = citation.get("label") or citation.get("id") or "?"
            lines.append(f"- {label} -> `{citation.get('evidence_id', '')}`")
    gaps = evidence.get("gaps") or []
    if gaps:
        lines.extend(["", "## Gaps"])
        for gap in gaps:
            suffix = " · ".join(
                part for part in (gap.get("capability"), gap.get("resource")) if part
            )
            text = f"- `{gap.get('code', '')}` {gap.get('message', '')}"
            lines.append(text + (f" ({suffix})" if suffix else ""))
    return lines


def _v2_markdown(payload: Mapping[str, Any]) -> str:
    command = str(payload.get("command") or "result")
    meta = payload.get("meta") or {}
    lines = [
        f"# V2 {_v2_title(command)}",
        "",
        f"Status: {_status_label(payload.get('status'))}",
    ]
    if payload.get("operation"):
        lines.append(f"Operation: `{payload['operation']}`")
    if meta.get("request_id"):
        lines.append(f"Request: `{meta['request_id']}`")
    if meta.get("duration_ms") is not None:
        lines.append(f"Duration: {meta['duration_ms']} ms")
    lines.append("")
    result = payload.get("result") or {}
    if "total" in result:
        summary = f"Results: {result.get('total')}"
        items = result.get("items") or []
        ids = ", ".join(
            str(item.get("id", "?")) for item in items if isinstance(item, Mapping)
        )
        if ids:
            summary += f" ({ids})"
        lines.append(summary)
    elif result:
        lines.extend(_kv_lines(result))
    lines.extend(_v2_evidence_markdown(payload.get("evidence") or {}))
    attempts = payload.get("attempts") or []
    if attempts:
        rows = [
            [
                attempt.get("capability", ""),
                attempt.get("provider", ""),
                _status_label(attempt.get("status")),
                attempt.get("error_code") or "-",
                attempt.get("elapsed_ms", "-"),
                attempt.get("result_count", "-"),
            ]
            for attempt in attempts
        ]
        lines.extend(["", "## Attempts"])
        lines.extend(_table(["Capability", "Provider", "Status", "Error", "Elapsed (ms)", "Results"], rows))
    degradation = payload.get("degradation") or []
    if degradation:
        lines.extend(["", "## Degradation"])
        for item in degradation:
            suffix = item.get("capability")
            text = f"- `{item.get('code', '')}` {item.get('message', '')}"
            lines.append(text + (f" ({suffix})" if suffix else ""))
    lines.extend(_warning_lines(meta.get("warnings"), meta.get("deprecations")))
    lines.extend(_error_lines(payload.get("error")))
    return "\n".join(lines).strip() + "\n"


def _v2_content(payload: Mapping[str, Any]) -> str:
    command = str(payload.get("command") or "result")
    status = str(payload.get("status") or "failed")
    label = _status_label(status)
    if status == "failed":
        error = payload.get("error") or {}
        head = f"FAILED: {command}"
        if error.get("code"):
            head += f" {error['code']}"
        if error.get("message"):
            head += f" - {error['message']}"
        return head + "\n"
    evidence = payload.get("evidence") or {}
    items = evidence.get("items") or []
    candidates = evidence.get("candidates") or []
    degradation = payload.get("degradation") or []
    if command in ("search", "fetch"):
        bodies = [str(item.get("content") or "").strip() for item in items if item.get("content")]
        if bodies:
            return "\n\n".join(bodies).rstrip() + "\n"
        snippets = [str(c.get("snippet") or "").strip() for c in candidates if c.get("snippet")]
        if snippets:
            return "\n".join(snippets).rstrip() + "\n"
        if status == "degraded":
            reasons = "; ".join(
                str(item.get("message") or item.get("code", "")) for item in degradation
            )
            return f"DEGRADED: {command}" + (f" - {reasons}" if reasons else "") + "\n"
        return "No results.\n"
    if command == "map":
        resources = [str(item.get("resource") or "") for item in items] or [
            str(candidate.get("resource") or "") for candidate in candidates
        ]
        if resources:
            return "\n".join(resources).rstrip() + "\n"
        if status == "degraded":
            reasons = "; ".join(
                str(item.get("message") or item.get("code", "")) for item in degradation
            )
            return f"DEGRADED: {command}" + (f" - {reasons}" if reasons else "") + "\n"
        return "No results.\n"
    result = payload.get("result") or {}
    if result:
        return "\n".join(
            f"{key}: {_one_line(_compact(value))}" for key, value in result.items()
        ).rstrip() + "\n"
    return f"{command} {label}\n"


def render_v2(payload: Mapping[str, Any], fmt: str) -> str:
    """Render one validated, redacted V2 envelope payload as markdown/content."""
    return _render(payload, _validate_v2, _v2_markdown, _v2_content, fmt)


# ---------------------------------------------------------------------------
# V3 control-plane views
# ---------------------------------------------------------------------------

_V3_TITLES = {
    "config.path": "Config Path",
    "config.list": "Config List",
    "config.set": "Config Set",
    "config.unset": "Config Unset",
    "provider.catalog.list": "Provider Catalog",
    "provider.catalog.status": "Provider Status",
    "provider.probe": "Provider Probe",
    "provider.routes.current": "Provider Routes",
    "provider.routes.list": "Provider Routes",
    "provider.routes.add": "Provider Routes Add",
    "provider.routes.remove": "Provider Routes Remove",
    "doctor.status": "Doctor Status",
    "doctor.probe": "Doctor Probe",
    "dev.route.explain": "Route Explain",
    "dev.route.calibrate": "Route Calibrate",
    "dev.diagnose.openai-compatible": "Diagnose OpenAI-Compatible",
    "dev.smoke": "Smoke",
    "dev.regression": "Regression",
    "dev.skills.status": "Skills Status",
    "dev.skills.update": "Skills Update",
}


def _v3_title(operation: str) -> str:
    return _V3_TITLES.get(operation, operation)


def _v3_result_markdown(operation: str, result: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    if operation.startswith("config."):
        for key in ("config_file", "config_dir", "config_dir_source", "config_status", "config_storage_ok"):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {result[key]}")
        if result.get("key"):
            lines.append(f"- Key: `{result['key']}`")
        if result.get("value") is not None:
            lines.append(f"- Value: `{result['value']}`")
        values = result.get("values")
        if values:
            rows = [[str(key), _one_line(_compact(value))] for key, value in values.items()]
            lines.extend(["", "### Values"])
            lines.extend(_table(["Key", "Value"], rows))
        return lines
    if operation.startswith("provider.catalog."):
        providers = result.get("providers") or []
        lines.append(f"- Providers: {result.get('provider_count', len(providers))}")
        if providers:
            rows = []
            for provider in providers:
                row = [
                    provider.get("provider", ""),
                    _one_line(_compact(provider.get("capabilities")), 80),
                    provider.get("tier", ""),
                    provider.get("stability", ""),
                ]
                if operation.endswith("status"):
                    status = provider.get("status") or []
                    row.append("; ".join(
                        f"{item.get('capability', '')}={item.get('configured', '-')}"
                        for item in status
                    ) or "-")
                rows.append(row)
            headers = ["Provider", "Capabilities", "Tier", "Stability"]
            if operation.endswith("status"):
                headers.append("Configured")
            lines.extend(["", "### Providers"])
            lines.extend(_table(headers, rows))
        return lines
    if operation == "provider.probe":
        for key in (
            "provider", "configured", "enabled", "eligible", "probe_capability",
            "probe_operation", "status", "message", "response_time_ms",
        ):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        routes = result.get("routes") or []
        if routes:
            rows = [
                [
                    route.get("route_id", ""),
                    route.get("provider", ""),
                    route.get("status", ""),
                    route.get("response_time_ms", "-"),
                    _one_line(route.get("message", ""), 100),
                ]
                for route in routes
            ]
            lines.extend(["", "### Routes"])
            lines.extend(_table(["Route", "Provider", "Status", "Latency (ms)", "Message"], rows))
        return lines
    if operation.startswith("provider.routes."):
        if result.get("action"):
            lines.append(f"- Action: {result['action']}")
        lines.append(f"- Route count: {result.get('route_count', '-')}")
        if result.get("current_route_id"):
            lines.append(f"- Current route: `{result['current_route_id']}`")
        if result.get("current_model"):
            lines.append(f"- Current model: `{result['current_model']}`")
        if result.get("config_file"):
            lines.append(f"- Config file: `{result['config_file']}`")
        routes = result.get("routes") or []
        if routes:
            rows = [
                [
                    index,
                    route.get("id", ""),
                    route.get("provider", ""),
                    route.get("model", ""),
                    route.get("api_url", ""),
                    route.get("stream", "-"),
                ]
                for index, route in enumerate(routes, 1)
            ]
            lines.extend(["", "### Routes"])
            lines.extend(_table(["Order", "ID", "Provider", "Model", "API URL", "Stream"], rows))
        return lines
    if operation == "doctor.status":
        for key in (
            "local_only", "config_file", "config_dir_source", "config_status",
            "config_storage_ok", "minimum_profile", "minimum_profile_ok",
            "core_evidence_ready",
        ):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        missing = result.get("minimum_profile_missing") or []
        if missing:
            lines.append(f"- Minimum profile missing: {', '.join(str(item) for item in missing)}")
        return lines
    if operation == "doctor.probe":
        for key in ("minimum_profile", "minimum_profile_ok", "degraded_reason"):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        checks = result.get("checks") or []
        if checks:
            rows = [
                [
                    check.get("name", ""),
                    _status_label(check.get("status")),
                    check.get("response_time_ms", "-"),
                    _one_line(check.get("message", ""), 100),
                ]
                for check in checks
            ]
            lines.extend(["", "### Checks"])
            lines.extend(_table(["Check", "Status", "Latency (ms)", "Message"], rows))
        return lines
    if operation == "dev.route.explain":
        for key in (
            "query", "validation_level", "intent_router_mode", "confidence",
            "executed_search", "provider_selection",
        ):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        capabilities = result.get("required_capabilities") or []
        if capabilities:
            lines.append(f"- Required capabilities: {', '.join(str(item) for item in capabilities)}")
        missing = result.get("missing_capabilities") or []
        if missing:
            lines.append(f"- Missing capabilities: {', '.join(str(item) for item in missing)}")
        reasons = result.get("reasons") or []
        if reasons:
            lines.extend(["", "### Reasons"])
            for reason in reasons:
                lines.append(f"- {reason}")
        signals = result.get("intent_signals") or {}
        if signals:
            rows = [[str(key), _one_line(_compact(value))] for key, value in signals.items()]
            lines.extend(["", "### Signals"])
            lines.extend(_table(["Signal", "Value"], rows))
        return lines
    if operation == "dev.route.calibrate":
        for key in (
            "metric", "primary_metric", "recommended_model", "recommended_threshold",
            "recommended_margin", "dataset_size", "embedding_model",
        ):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        failed = result.get("failed_models") or []
        if failed:
            lines.append(f"- Failed models: {', '.join(str(item) for item in failed)}")
        model_results = result.get("model_results") or []
        if model_results:
            rows = [
                [
                    item.get("model", ""),
                    _status_label(item.get("ok")),
                    item.get("dimension", ""),
                    item.get("semantic_macro_f1", ""),
                    item.get("full_route_macro_f1", ""),
                    item.get("recommended_threshold", ""),
                    item.get("recommended_margin", ""),
                    _one_line(item.get("error", ""), 80),
                ]
                for item in model_results
            ]
            lines.extend(["", "### Models"])
            lines.extend(
                _table(
                    ["Model", "Status", "Dim", "Semantic F1", "Full-route F1", "Threshold", "Margin", "Error"],
                    rows,
                )
            )
        return lines
    if operation == "dev.diagnose.openai-compatible":
        for key in (
            "provider", "api_url", "model", "configured_stream", "timeout_seconds",
            "config_file", "config_dir_source", "summary", "recommendation",
        ):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        missing = result.get("missing") or []
        if missing:
            lines.append(f"- Missing: {', '.join(str(item) for item in missing)}")
        checks = result.get("checks") or []
        if checks:
            rows = [
                [
                    check.get("name", ""),
                    _status_label(check.get("status")),
                    check.get("response_time_ms", "-"),
                    check.get("http_status", "-"),
                    _one_line(check.get("message", ""), 100),
                ]
                for check in checks
            ]
            lines.extend(["", "### Checks"])
            lines.extend(_table(["Check", "Status", "Latency (ms)", "HTTP", "Message"], rows))
        if result.get("next_command"):
            lines.extend(["", "### Next Command"])
            lines.extend(_fence(str(result["next_command"])))
        return lines
    if operation == "dev.smoke":
        cases = result.get("cases") or []
        lines.append(f"- Mode: `{result.get('mode', '')}`")
        lines.append(
            f"- Cases: {result.get('case_count', len(cases))} "
            f"({len(result.get('failed_cases') or [])} failed, "
            f"{len(result.get('degraded_cases') or [])} degraded)"
        )
        if cases:
            rows = [
                [
                    case.get("name", ""),
                    _status_label(case.get("ok")),
                    case.get("severity", ""),
                    case.get("provider", ""),
                    case.get("status", ""),
                ]
                for case in cases
            ]
            lines.extend(["", "### Cases"])
            lines.extend(_table(["Case", "Status", "Severity", "Provider", "State"], rows))
        return lines
    if operation == "dev.regression":
        for key in ("exit_code", "subprocess_started", "fallback"):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        failed = result.get("failed_cases") or []
        if failed:
            lines.extend(["", "### Failed Cases"])
            for case in failed:
                lines.append(f"- {case}")
        return lines
    if operation.startswith("dev.skills."):
        for key in ("root", "selected", "skill", "bundled_files", "bundled_hash"):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact(result[key])}")
        counts = result.get("status_counts") or {}
        if counts:
            lines.append(f"- Status counts: {_one_line(_compact(counts))}")
        for key in ("targets", "installed", "skipped", "failed"):
            rows = result.get(key) or []
            if rows and isinstance(rows, list):
                lines.extend([f"", f"### {key.title()}"])
                for item in rows:
                    if isinstance(item, Mapping):
                        lines.append(f"- {_one_line(_compact(item), 120)}")
                    else:
                        lines.append(f"- {item}")
        for key in ("installed_count", "skipped_count", "failed_count"):
            if result.get(key) is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {result[key]}")
        return lines
    if result:
        lines.extend(_kv_lines(result))
    return lines


def _v3_network_content(network: Mapping[str, Any]) -> str:
    parts = [f"policy={network.get('policy', '-')}", f"scope={network.get('scope', '-')}"]
    parts.append(f"network_attempted={_yes_no(network.get('attempted'))}")
    targets = network.get("targets") or []
    if targets:
        parts.append("targets=" + ",".join(str(target) for target in targets))
    return "; ".join(parts)


def _v3_side_effects_content(side_effects: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for name in ("config", "filesystem"):
        mutation = side_effects.get(name) or {}
        parts.append(
            f"{name}_read={_yes_no(mutation.get('read'))}"
            f" {name}_write_attempted={_yes_no(mutation.get('write_attempted'))}"
            f" {name}_write_committed={_yes_no(mutation.get('write_committed'))}"
        )
    subprocess = side_effects.get("subprocess") or {}
    parts.append(f"subprocess_started={_yes_no(subprocess.get('started'))}")
    return "; ".join(parts)


def _v3_content(payload: Mapping[str, Any]) -> str:
    operation = str(payload.get("operation") or "?")
    status = _status_label(payload.get("status"))
    result = payload.get("result") or {}
    headline: str
    if operation.startswith("config."):
        if operation == "config.list":
            headline = f"values={len(result.get('values') or {})}"
        elif result.get("key"):
            headline = f"key={result['key']}"
        else:
            headline = _one_line(_compact(result), 100)
    elif operation.startswith("provider.catalog."):
        headline = f"providers={result.get('provider_count', len(result.get('providers') or []))}"
    elif operation == "provider.probe":
        headline = (
            f"provider={result.get('provider', '')} status={result.get('status', '')} "
            f"configured={_yes_no(result.get('configured'))} eligible={_yes_no(result.get('eligible'))}"
        )
    elif operation.startswith("provider.routes."):
        headline = f"routes={result.get('route_count', len(result.get('routes') or []))}"
        if result.get("current_route_id"):
            headline += f" current={result['current_route_id']}"
        if result.get("action"):
            headline += f" action={result['action']}"
    elif operation == "doctor.status":
        headline = (
            f"local_only={_yes_no(result.get('local_only'))} "
            f"minimum_profile={result.get('minimum_profile', '-')} "
            f"minimum_profile_ok={_yes_no(result.get('minimum_profile_ok'))}"
        )
    elif operation == "doctor.probe":
        checks = result.get("checks") or []
        ok_checks = sum(1 for check in checks if check.get("status") in ("ok", "configured"))
        headline = f"checks={len(checks)} ({ok_checks} ok)"
    elif operation == "dev.route.explain":
        headline = (
            f"query={_one_line(result.get('query', ''), 80)} "
            f"capabilities={','.join(str(item) for item in result.get('required_capabilities') or [])}"
        )
    elif operation == "dev.route.calibrate":
        headline = f"recommended={result.get('recommended_model', '-')} dataset={result.get('dataset_size', '-')}"
    elif operation == "dev.diagnose.openai-compatible":
        headline = f"provider={result.get('provider', '')} summary={_one_line(result.get('summary', ''), 100)}"
    elif operation == "dev.smoke":
        cases = result.get("cases") or []
        headline = (
            f"mode={result.get('mode', '')} cases={result.get('case_count', len(cases))} "
            f"failed={len(result.get('failed_cases') or [])}"
        )
    elif operation == "dev.regression":
        headline = f"exit_code={result.get('exit_code', '-')} subprocess_started={_yes_no(result.get('subprocess_started'))}"
    elif operation.startswith("dev.skills."):
        counts = result.get("status_counts") or {}
        if counts:
            headline = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        else:
            headline = _one_line(_compact(result), 100)
    else:
        headline = _one_line(_compact(result), 100)
    text = f"{operation} {status}: {headline}"
    text += " | " + _v3_network_content(payload.get("network") or {})
    text += " | " + _v3_side_effects_content(payload.get("side_effects") or {})
    if payload.get("error"):
        error = payload["error"]
        text += f" | {error.get('code', '')}: {error.get('message', '')}"
    return text + "\n"


def _v3_markdown(payload: Mapping[str, Any]) -> str:
    operation = str(payload.get("operation") or "result")
    meta = payload.get("meta") or {}
    lines = [
        f"# V3 {_v3_title(operation)}",
        "",
        f"Status: {_status_label(payload.get('status'))}",
        f"Operation: `{operation}`",
    ]
    if meta.get("duration_ms") is not None:
        lines.append(f"Duration: {meta['duration_ms']} ms")
    lines.append("")
    lines.extend(_v3_result_markdown(operation, payload.get("result") or {}))
    network = payload.get("network") or {}
    lines.extend(["", "## Network"])
    lines.append(f"- Policy: `{network.get('policy', '-')}`")
    lines.append(f"- Scope: `{network.get('scope', '-')}`")
    lines.append(f"- Attempted: {_yes_no(network.get('attempted'))}")
    targets = network.get("targets") or []
    lines.append("- Targets: " + (", ".join(f"`{target}`" for target in targets) if targets else "-"))
    side_effects = payload.get("side_effects") or {}
    lines.extend(["", "## Side Effects"])
    for name in ("config", "filesystem"):
        mutation = side_effects.get(name) or {}
        lines.append(
            f"- {name}: read={_yes_no(mutation.get('read'))} "
            f"write_attempted={_yes_no(mutation.get('write_attempted'))} "
            f"write_committed={_yes_no(mutation.get('write_committed'))}"
        )
    subprocess = side_effects.get("subprocess") or {}
    lines.append(f"- subprocess: started={_yes_no(subprocess.get('started'))}")
    lines.extend(_warning_lines(meta.get("warnings"), meta.get("deprecations")))
    lines.extend(_error_lines(payload.get("error")))
    return "\n".join(lines).strip() + "\n"


def render_v3(payload: Mapping[str, Any], fmt: str) -> str:
    """Render one validated, redacted V3 envelope payload as markdown/content."""
    return _render(payload, _validate_v3, _v3_markdown, _v3_content, fmt)


# ---------------------------------------------------------------------------
# Research Workflow views
# ---------------------------------------------------------------------------


def _workflow_markdown(payload: Mapping[str, Any]) -> str:
    meta = payload.get("meta") or {}
    lines = [
        "# Research Run",
        "",
        f"Status: {_status_label(payload.get('status'))}",
        "Operation: `research.run`",
    ]
    if meta.get("request_id"):
        lines.append(f"Request: `{meta['request_id']}`")
    if meta.get("duration_ms") is not None:
        lines.append(f"Duration: {meta['duration_ms']} ms")
    plan = payload.get("plan") or {}
    operations = plan.get("operations") or []
    if operations:
        lines.extend(["", "## Plan"])
        for operation in operations:
            resource = ""
            input_data = operation.get("input") or {}
            if isinstance(input_data, Mapping) and input_data.get("resource"):
                resource = f" -> {input_data['resource']}"
            lines.append(f"- `{operation.get('id', '')}` ({operation.get('operation', '')}){resource}")
    stages = payload.get("stages") or []
    if stages:
        rows = [
            [
                stage.get("order", ""),
                stage.get("id", ""),
                stage.get("operation", ""),
                _status_label(stage.get("status")),
                stage.get("result_count", "-"),
                len(stage.get("evidence_ids") or []),
                len(stage.get("artifact_ids") or []),
                _one_line((stage.get("error") or {}).get("message", ""), 80) if stage.get("error") else "-",
            ]
            for stage in stages
        ]
        lines.extend(["", "## Stages"])
        lines.extend(
            _table(
                ["#", "Stage", "Operation", "Status", "Results", "Evidence", "Artifacts", "Error"],
                rows,
            )
        )
    evidence = payload.get("evidence") or []
    if evidence:
        lines.extend(["", "## Evidence"])
        for item in evidence:
            title = item.get("title") or item.get("resource") or item.get("id", "")
            lines.append(f"- {title} (`{item.get('resource', '')}`) — {item.get('provider', '')}")
    citations = payload.get("citations") or []
    if citations:
        lines.extend(["", "## Citations"])
        for citation in citations:
            label = citation.get("label") or citation.get("id") or "?"
            lines.append(f"- {label} -> `{citation.get('evidence_id', '')}`")
    gaps = payload.get("gaps") or []
    if gaps:
        lines.extend(["", "## Gaps"])
        for gap in gaps:
            suffix = " · ".join(
                part for part in (gap.get("capability"), gap.get("resource")) if part
            )
            text = f"- `{gap.get('code', '')}` {gap.get('message', '')}"
            lines.append(text + (f" ({suffix})" if suffix else ""))
    attempts = payload.get("attempts") or []
    if attempts:
        rows = [
            [
                attempt.get("capability", ""),
                attempt.get("provider", ""),
                _status_label(attempt.get("status")),
                attempt.get("error_type") or "-",
                attempt.get("elapsed_ms", "-"),
                attempt.get("result_count", "-"),
            ]
            for attempt in attempts
        ]
        lines.extend(["", "## Attempts"])
        lines.extend(_table(["Capability", "Provider", "Status", "Error", "Elapsed (ms)", "Results"], rows))
    artifacts = payload.get("artifacts") or []
    if artifacts:
        rows = [
            [
                artifact.get("id", ""),
                artifact.get("kind", ""),
                _status_label(artifact.get("status")),
                artifact.get("name", ""),
                artifact.get("media_type", ""),
                artifact.get("byte_length", "-"),
                artifact.get("digest", ""),
            ]
            for artifact in artifacts
        ]
        lines.extend(["", "## Artifacts"])
        lines.extend(
            _table(["ID", "Kind", "Status", "Name", "Media", "Bytes", "Digest"], rows)
        )
    lines.extend(_warning_lines(meta.get("warnings")))
    lines.extend(_error_lines(payload.get("error")))
    return "\n".join(lines).strip() + "\n"


def _workflow_content(payload: Mapping[str, Any]) -> str:
    stages = payload.get("stages") or []
    evidence = payload.get("evidence") or []
    citations = payload.get("citations") or []
    artifacts = payload.get("artifacts") or []
    status = _status_label(payload.get("status"))
    text = (
        f"research.run {status}: {len(stages)} stages, "
        f"{len(evidence)} evidence items, {len(citations)} citations, "
        f"{len(artifacts)} artifacts"
    )
    if payload.get("error"):
        error = payload["error"]
        text += f" | {error.get('code', '')}: {error.get('message', '')}"
    return text + "\n"


def render_workflow(payload: Mapping[str, Any], fmt: str) -> str:
    """Render one validated, redacted Research Workflow payload as markdown/content."""
    return _render(payload, _validate_workflow, _workflow_markdown, _workflow_content, fmt)


__all__ = [
    "PRESENTATION_FORMATS",
    "PresentationError",
    "render_v2",
    "render_v3",
    "render_workflow",
]
