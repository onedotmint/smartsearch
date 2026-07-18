from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

"""
================================================================================
步骤1：JSON 与文本输出基础
================================================================================
目标：提供 JSON 序列化、终端编码兼容和通用文本处理。
数据源：CLI 命令返回的 Python 字典与终端编码信息。
操作：
1) 序列化命令结果。
2) 处理非 UTF-8 终端的回退输出。
"""
logger.info("开始加载 CLI 输出基础工具...")
def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _json_stdout_safe(data: Any) -> str:
    text = _json(data)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    errors = getattr(sys.stdout, "errors", None) or "strict"
    try:
        text.encode(encoding, errors=errors)
        return text
    except UnicodeEncodeError:
        return "".join(_escape_unencodable_json_char(char, encoding) for char in text)


def _escape_unencodable_json_char(char: str, encoding: str) -> str:
    try:
        char.encode(encoding)
        return char
    except UnicodeEncodeError:
        return json.dumps(char, ensure_ascii=True)[1:-1]


def _format_seconds(seconds: float) -> str:
    return f"{seconds:g}"


logger.info("步骤1完成：CLI 输出基础工具已加载")

"""
================================================================================
步骤2：Markdown 与结果格式化
================================================================================
目标：把统一结果字典转换为 Markdown、表格和错误摘要。
数据源：service 层返回的结果字典。
操作：
1) 生成结果、诊断、配置和技能状态的 Markdown。
2) 保留既有字段、标题和错误展示格式。
"""
logger.info("开始加载 CLI Markdown 格式化工具...")
def _one_line(value: Any, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _md_cell(value: Any) -> str:
    return _one_line(value).replace("|", r"\|")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return []
    lines = [
        "| " + " | ".join(_md_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = list(row)[: len(headers)]
        cells.extend([""] * (len(headers) - len(cells)))
        lines.append("| " + " | ".join(_md_cell(cell) for cell in cells) + " |")
    return lines


def _markdown_code_block(value: Any) -> list[str]:
    text = "" if value is None else str(value)
    fence = "```"
    if fence in text:
        text = text.replace(fence, "` ` `")
    return ["```text", text, "```"]


def _status_label(value: Any) -> str:
    if isinstance(value, bool):
        return "OK" if value else "FAIL"
    status = str(value or "").strip()
    normalized = status.lower()
    labels = {
        "ok": "OK",
        "true": "OK",
        "configured": "CONFIGURED",
        "warning": "WARN",
        "timeout": "TIMEOUT",
        "error": "ERROR",
        "config_error": "CONFIG ERROR",
        "not_configured": "NOT CONFIGURED",
        "false": "FAIL",
        "failed": "FAIL",
        "empty": "EMPTY",
        "skipped": "SKIPPED",
    }
    return labels.get(normalized, status.upper() if status else "-")


def _yes_no(value: Any) -> str:
    return "YES" if bool(value) else "NO"


def _latency_text(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.2f} ms"
    return str(value)


def _configured_text(items: Any) -> str:
    if isinstance(items, (list, tuple)):
        return ", ".join(str(item) for item in items) if items else "-"
    return str(items) if items else "-"


def _error_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if data.get("error_type") or data.get("error"):
        lines.extend(["", "## Errors"])
        if data.get("error_type"):
            lines.append(f"- Type: `{data.get('error_type')}`")
        if data.get("error"):
            lines.append(f"- Message: {data.get('error')}")
    parameter_errors = data.get("config_parameter_errors") or []
    for error in parameter_errors:
        lines.append(f"- Config: {error}")
    return lines


def _error_summary(data: dict[str, Any]) -> str:
    error_type = data.get("error_type")
    error = data.get("error")
    if error_type and error:
        return f"{error_type}: {error}"
    if error:
        return str(error)
    if error_type:
        return str(error_type)
    return ""


def _result_title(item: Any, index: int) -> str:
    if not isinstance(item, dict):
        return f"Result {index}"
    return (
        item.get("title")
        or item.get("id")
        or item.get("library_id")
        or item.get("url")
        or item.get("provider")
        or f"Result {index}"
    )


def _result_target(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    return item.get("url") or item.get("id") or item.get("library_id") or ""


def _result_summary(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        highlights = " ".join(str(part) for part in highlights[:2])
    return (
        item.get("description")
        or item.get("content")
        or item.get("snippet")
        or item.get("text")
        or highlights
        or item.get("source")
        or ""
    )


def _result_rows(results: list[Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for index, item in enumerate(results, 1):
        rows.append([index, _result_title(item, index), _result_target(item), _result_summary(item)])
    return rows


def _format_result_markdown(command: str, data: dict[str, Any], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"Status: {_status_label(data.get('ok'))}",
    ]
    if data.get("query"):
        lines.append(f"Query: `{data.get('query')}`")
    if data.get("url"):
        lines.append(f"URL: {data.get('url')}")
    if data.get("base_url"):
        lines.append(f"Base URL: {data.get('base_url')}")
    if data.get("provider"):
        lines.append(f"Provider: {data.get('provider')}")
    if data.get("tool"):
        lines.append(f"Tool: `{data.get('tool')}`")
    if data.get("elapsed_ms") is not None:
        lines.append(f"Elapsed: {_latency_text(data.get('elapsed_ms'))}")

    results = data.get("results") or []
    lines.append("")
    if results:
        lines.append("## Results")
        lines.extend(_markdown_table(["#", "Title", "URL / ID", "Summary"], _result_rows(results)))
    elif data.get("content"):
        lines.append("## Content")
        lines.extend(_markdown_code_block(data.get("content")))
    elif data.get("ok"):
        lines.append("No results.")
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_doctor_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Smart Search Doctor",
        "",
        f"Overall: {_status_label(data.get('ok'))}",
        f"Config file: `{data.get('config_file', '')}`",
        f"Config dir: `{data.get('config_dir', '')}`",
        f"Config dir source: `{data.get('config_dir_source', '-')}`",
        f"Default config file: `{data.get('default_config_file', '')}`",
        f"Config status: {data.get('config_status', '-')}",
        f"Minimum profile: {_status_label(data.get('minimum_profile_ok'))}",
        f"Log dir config value: `{data.get('log_dir_config_value', data.get('SMART_SEARCH_LOG_DIR', ''))}`",
        f"Resolved log dir: `{data.get('resolved_log_dir', '')}`",
        f"File logging enabled: {_yes_no(data.get('file_logging_enabled'))}",
    ]
    if data.get("legacy_windows_config_file"):
        lines.append(f"Legacy Windows config file: `{data.get('legacy_windows_config_file')}`")
        lines.append(f"Legacy Windows config exists: {_status_label(data.get('legacy_windows_config_exists'))}")
    if data.get("config_dir_override_value"):
        lines.append(f"SMART_SEARCH_CONFIG_DIR: `{data.get('config_dir_override_value')}`")
        lines.append(f"Override matches default: {_yes_no(data.get('config_dir_override_matches_default'))}")
        if data.get("config_dir_source") == "environment" and data.get("config_dir_override_matches_default"):
            lines.append(
                "The active config path comes from `SMART_SEARCH_CONFIG_DIR`, but that override matches the current Windows default path."
            )
    if data.get("config_dir_source") == "legacy_windows_home":
        lines.append(
            "Active config is using the old Windows `~\\.config\\smart-search` location because the new default file does not exist."
        )
    missing = data.get("minimum_profile_missing") or []
    if missing:
        lines.append(f"Missing: `{', '.join(str(item) for item in missing)}`")

    config_sources = data.get("config_sources") or {}
    if config_sources:
        rows = []
        for key in sorted(config_sources):
            rows.append([key, config_sources.get(key), data.get(key, "-")])
        lines.extend(["", "## Configuration Values"])
        lines.extend(_markdown_table(["Key", "Source", "Value"], rows))

    capability_status = data.get("capability_status") or {}
    if capability_status:
        rows = []
        for capability, status in capability_status.items():
            if isinstance(status, dict):
                rows.append(
                    [
                        capability,
                        _status_label(status.get("ok")),
                        _configured_text(status.get("configured")),
                        _configured_text(status.get("fallback_chain")),
                    ]
                )
        if rows:
            lines.extend(["", "## Capabilities"])
            lines.extend(_markdown_table(["Capability", "Status", "Configured", "Fallback chain"], rows))

    main_tests = data.get("main_search_connection_tests") or {}
    if main_tests:
        rows = []
        for provider, test in main_tests.items():
            if isinstance(test, dict):
                rows.append(
                    [
                        provider,
                        _status_label(test.get("status")),
                        _latency_text(test.get("response_time_ms")),
                        test.get("message", ""),
                    ]
                )
        lines.extend(["", "## Main Search Providers"])
        lines.extend(_markdown_table(["Provider", "Status", "Latency", "Message"], rows))
        lines.extend(_provider_detail_lines("Provider Details", main_tests))

    provider_tests = [
        ("exa", data.get("exa_connection_test") or {}),
        ("tavily", data.get("tavily_connection_test") or {}),
        ("jina", data.get("jina_connection_test") or {}),
        ("firecrawl", data.get("firecrawl_connection_test") or {}),
        ("zhipu", data.get("zhipu_connection_test") or {}),
        ("zhipu-mcp", data.get("zhipu_mcp_connection_test") or {}),
        ("context7", data.get("context7_connection_test") or {}),
    ]
    rows = []
    for provider, test in provider_tests:
        if isinstance(test, dict) and test:
            rows.append(
                [
                    provider,
                    _status_label(test.get("status")),
                    _latency_text(test.get("response_time_ms")),
                    test.get("message", ""),
                ]
            )
    if rows:
        lines.extend(["", "## Provider Checks"])
        lines.extend(_markdown_table(["Provider", "Status", "Latency", "Message"], rows))
        lines.extend(_provider_detail_lines("Provider Check Details", dict(provider_tests)))

    router = data.get("intent_router_status") or {}
    if router:
        lines.extend(["", "## Intent Router"])
        lines.extend(
            _markdown_table(
                ["Field", "Value"],
                [
                    ["mode", router.get("mode", "")],
                    ["embeddings_configured", _yes_no(router.get("embeddings_configured"))],
                    ["classifier_configured", _yes_no(router.get("classifier_configured"))],
                    ["embedding_model", router.get("embedding_model", "")],
                    ["embedding_threshold", router.get("embedding_threshold", "")],
                    ["embedding_margin", router.get("embedding_margin", "")],
                    ["embedding_threshold_source", router.get("embedding_threshold_source", "")],
                    ["embedding_margin_source", router.get("embedding_margin_source", "")],
                    ["embedding_preset", router.get("embedding_preset_id", "")],
                    ["embedding_preset_threshold", router.get("embedding_preset_threshold", "")],
                    ["embedding_preset_margin", router.get("embedding_preset_margin", "")],
                    ["embedding_preset_recommended", _yes_no(router.get("embedding_preset_recommended"))],
                    ["classifier_model", router.get("classifier_model", "")],
                    ["timeout_seconds", router.get("timeout_seconds", "")],
                    ["degrades_to_rules", _yes_no(router.get("degrades_to_rules"))],
                ],
            )
        )
        if router.get("embedding_preset_recommendation"):
            lines.extend(["", "### Embedding Preset Recommendation", "", router.get("embedding_preset_recommendation")])
            commands = router.get("embedding_preset_commands") or []
            if commands:
                lines.extend(_markdown_code_block("\n".join(str(command) for command in commands)))
        if router.get("error"):
            lines.append(f"Intent router error: {router.get('error')}")

    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _provider_detail_lines(title: str, provider_tests: dict[str, Any]) -> list[str]:
    details: list[str] = []
    for provider, test in provider_tests.items():
        if not isinstance(test, dict) or not test:
            continue
        message = test.get("message")
        available_models = test.get("available_models") or []
        nested_checks = [
            ("models_endpoint_test", test.get("models_endpoint_test")),
            ("chat_completion_test", test.get("chat_completion_test")),
        ]
        if not message and not available_models and not any(isinstance(item, dict) for _, item in nested_checks):
            continue
        details.extend(
            [
                "",
                f"### {provider}",
                "",
                f"- Status: {_status_label(test.get('status'))}",
                f"- Latency: {_latency_text(test.get('response_time_ms'))}",
            ]
        )
        if message:
            details.extend(["- Message:"])
            details.extend(_markdown_code_block(message))
        if available_models:
            details.append("- Available models: `" + "`, `".join(str(model) for model in available_models) + "`")
        for name, nested in nested_checks:
            if not isinstance(nested, dict):
                continue
            details.extend(
                [
                    f"- {name}: {_status_label(nested.get('status'))}, {_latency_text(nested.get('response_time_ms'))}",
                ]
            )
            if nested.get("message"):
                details.extend(_markdown_code_block(nested.get("message")))
    if not details:
        return []
    return ["", f"## {title}", *details]


def _format_smoke_markdown(data: dict[str, Any]) -> str:
    cases = data.get("cases") or []
    failed = data.get("failed_cases") or []
    degraded = data.get("degraded_cases") or []
    lines = [
        "# Smart Search Smoke",
        "",
        f"Mode: `{data.get('mode', '')}`",
        f"Overall: {_status_label(data.get('ok'))}",
        f"Cases: {len(cases)} total, {len(failed)} failed, {len(degraded)} degraded",
    ]
    if cases:
        rows = []
        for case in cases:
            rows.append(
                [
                    case.get("name", ""),
                    _status_label(case.get("ok")),
                    case.get("severity", ""),
                    case.get("error") or case.get("error_type") or case.get("skipped", ""),
                ]
            )
        lines.extend(["", "## Cases"])
        lines.extend(_markdown_table(["Case", "Status", "Severity", "Details"], rows))
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_diagnose_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Smart Search Diagnose",
        "",
        f"Provider: `{data.get('provider', '')}`",
        f"Status: {_status_label(data.get('ok'))}",
        f"Summary: {data.get('summary', '-')}",
        f"Recommendation: {data.get('recommendation', '-')}",
        f"Config file: `{data.get('config_file', '')}`",
        f"Config dir source: `{data.get('config_dir_source', '-')}`",
        f"API URL: `{data.get('api_url', '')}`",
        f"API key: `{data.get('api_key', '')}`",
        f"Model: `{data.get('model', '')}`",
        f"Configured stream: {_yes_no(data.get('configured_stream'))}",
        f"Timeout: {_format_seconds(float(data.get('timeout_seconds', 0) or 0))} seconds",
    ]
    checks = data.get("checks") or []
    if checks:
        rows = []
        for check in checks:
            rows.append(
                [
                    check.get("name", ""),
                    _status_label(check.get("status")),
                    _latency_text(check.get("response_time_ms")),
                    check.get("http_status", "-"),
                    check.get("content_type", "-"),
                    _yes_no(check.get("has_content")),
                    check.get("message", ""),
                ]
            )
        lines.extend(["", "## Checks"])
        lines.extend(_markdown_table(["Check", "Status", "Latency", "HTTP", "Content-Type", "Has content", "Message"], rows))
    if data.get("next_command"):
        lines.extend(["", "## Next Command"])
        lines.extend(_markdown_code_block(data.get("next_command")))
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_route_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Intent Route",
        "",
        f"Status: {_status_label(data.get('ok'))}",
        f"Query: `{data.get('query', '')}`",
        f"Mode: `{data.get('intent_router_mode', '')}`",
        f"Executed search: {_yes_no(data.get('executed_search'))}",
        f"Required capabilities: `{', '.join(data.get('required_capabilities') or [])}`",
        f"Confidence: `{data.get('confidence', '')}`",
        f"Engines: `{', '.join(data.get('router_engines_used') or [])}`",
        f"Embedding model: `{data.get('embedding_model', '')}`",
        f"Embedding threshold: `{data.get('embedding_threshold', '')}` ({data.get('embedding_threshold_source', '')})",
        f"Embedding margin: `{data.get('embedding_margin', '')}` ({data.get('embedding_margin_source', '')})",
        f"Degraded: {_yes_no(data.get('degraded'))}",
    ]
    if data.get("embedding_preset_recommendation"):
        lines.extend(["", "## Embedding Preset Recommendation", "", data.get("embedding_preset_recommendation")])
        commands = data.get("embedding_preset_commands") or []
        if commands:
            lines.extend(_markdown_code_block("\n".join(str(command) for command in commands)))
    if data.get("degraded_reason"):
        lines.append(f"Degraded reason: {data.get('degraded_reason')}")
    reasons = data.get("reasons") or []
    if reasons:
        lines.extend(["", "## Reasons"])
        for reason in reasons:
            lines.append(f"- {reason}")
    signals = data.get("intent_signals") or {}
    if signals:
        rows = [[key, value] for key, value in sorted(signals.items())]
        lines.extend(["", "## Signals"])
        lines.extend(_markdown_table(["Signal", "Value"], rows))
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_route_calibrate_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Route Calibration",
        "",
        f"Status: {_status_label(data.get('ok'))}",
        f"Primary metric: `{data.get('primary_metric', data.get('metric', ''))}`",
        f"Dataset size: `{data.get('dataset_size', '')}`",
        f"Recommended model: `{data.get('recommended_model') or '-'}`",
        f"Recommended threshold: `{data.get('recommended_threshold') if data.get('recommended_threshold') is not None else '-'}`",
        f"Recommended margin: `{data.get('recommended_margin') if data.get('recommended_margin') is not None else '-'}`",
    ]
    results = data.get("model_results") or []
    if results:
        rows = []
        for item in results:
            rows.append(
                [
                    item.get("model", ""),
                    _status_label(item.get("ok")),
                    item.get("dimension", ""),
                    _latency_text(item.get("latency_ms")),
                    item.get("semantic_macro_f1", ""),
                    item.get("full_route_macro_f1", ""),
                    item.get("recommended_threshold", ""),
                    item.get("recommended_margin", ""),
                    item.get("error", ""),
                ]
            )
        lines.extend(["", "## Models"])
        lines.extend(
            _markdown_table(
                ["Model", "Status", "Dim", "Latency", "Semantic F1", "Full-route F1", "Threshold", "Margin", "Error"],
                rows,
            )
        )
    failed = data.get("failed_models") or []
    if failed:
        lines.extend(["", "## Failed Models"])
        for model in failed:
            lines.append(f"- `{model}`")
    best = next((item for item in results if item.get("model") == data.get("recommended_model")), None)
    if isinstance(best, dict):
        failures = best.get("semantic_failures") or []
        if failures:
            lines.extend(["", "## Representative Semantic Failures"])
            rows = []
            for failure in failures[:8]:
                rows.append(
                    [
                        failure.get("id", ""),
                        failure.get("expected", ""),
                        failure.get("predicted", ""),
                        failure.get("top_capability", ""),
                        failure.get("top_score", ""),
                        failure.get("margin", ""),
                        failure.get("query", ""),
                    ]
                )
            lines.extend(_markdown_table(["Case", "Expected", "Predicted", "Top", "Score", "Margin", "Query"], rows))
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_config_markdown(data: dict[str, Any]) -> str:
    lines = ["# Smart Search Config", "", f"Status: {_status_label(data.get('ok'))}"]
    if data.get("config_file"):
        lines.append(f"Config file: `{data.get('config_file')}`")
    if data.get("config_dir"):
        lines.append(f"Config dir: `{data.get('config_dir')}`")
    if data.get("config_dir_source"):
        lines.append(f"Config dir source: `{data.get('config_dir_source')}`")
    if data.get("default_config_file"):
        lines.append(f"Default config file: `{data.get('default_config_file')}`")
    if data.get("legacy_windows_config_file"):
        lines.append(f"Legacy Windows config file: `{data.get('legacy_windows_config_file')}`")
        lines.append(f"Legacy Windows config exists: {_status_label(data.get('legacy_windows_config_exists'))}")
    if data.get("config_dir_override_value"):
        lines.append(f"SMART_SEARCH_CONFIG_DIR: `{data.get('config_dir_override_value')}`")
        lines.append(f"Override matches default: {_yes_no(data.get('config_dir_override_matches_default'))}")
    if "exists" in data:
        lines.append(f"Exists: {_status_label(bool(data.get('exists')))}")
    if data.get("key"):
        lines.append(f"Key: `{data.get('key')}`")
    if data.get("value"):
        lines.append(f"Value: `{data.get('value')}`")
    values = data.get("values") or {}
    if values:
        lines.extend(["", "## Values"])
        lines.extend(_markdown_table(["Key", "Value"], [[key, value] for key, value in values.items()]))
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_model_markdown(data: dict[str, Any]) -> str:
    lines = ["# Smart Search Model", "", f"Status: {_status_label(data.get('ok'))}"]
    rows = []
    if data.get("xai_model"):
        rows.append(["xai-responses", data.get("xai_model")])
    if data.get("openai_compatible_model"):
        rows.append(["openai-compatible", data.get("openai_compatible_model")])
    fallback_models = data.get("openai_compatible_fallback_models") or []
    if fallback_models:
        rows.append(["openai-compatible fallback", ", ".join(fallback_models)])
    if data.get("current_model"):
        rows.append(["current", data.get("current_model")])
    if rows:
        lines.extend(["", "## Models"])
        lines.extend(_markdown_table(["Provider", "Model"], rows))
    if data.get("config_file"):
        lines.extend(["", f"Config file: `{data.get('config_file')}`"])
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_setup_markdown(data: dict[str, Any]) -> str:
    lines = ["# Smart Search Setup", "", f"Status: {_status_label(data.get('ok'))}"]
    if data.get("config_file"):
        lines.append(f"Config file: `{data.get('config_file')}`")
    saved = data.get("saved") or data.get("values") or {}
    if saved:
        lines.extend(["", "## Saved Values"])
        lines.extend(_markdown_table(["Key", "Value"], [[key, value] for key, value in saved.items()]))
    skills = data.get("skills") or {}
    if isinstance(skills, dict) and skills:
        installed = skills.get("installed") or []
        failed = skills.get("failed") or []
        lines.extend(["", "## Skills", f"Installed: {len(installed)}", f"Failed: {len(failed)}"])
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_skills_markdown(data: dict[str, Any]) -> str:
    lines = ["# Smart Search Skills", "", f"Status: {_status_label(data.get('ok'))}"]
    if data.get("root"):
        lines.append(f"Root: `{data.get('root')}`")
    if data.get("skill"):
        lines.append(f"Skill: `{data.get('skill')}`")
    if data.get("bundled_files") is not None:
        lines.append(f"Bundled files: {data.get('bundled_files')}")

    targets = data.get("targets") or data.get("installed") or []
    if targets:
        rows = []
        for item in targets:
            rows.append(
                [
                    item.get("target", ""),
                    item.get("status", "installed"),
                    item.get("files", item.get("installed_files", "")),
                    item.get("installed_files", ""),
                    _yes_no(item.get("hash_match")),
                    len(item.get("extra_files") or []),
                    item.get("path", ""),
                ]
            )
        lines.extend(["", "## Targets"])
        lines.extend(_markdown_table(["Target", "Status", "Files", "Installed", "Hash match", "Extra", "Path"], rows))
    if data.get("failed"):
        lines.extend(["", "## Failed"])
        lines.extend(_markdown_table(["Target", "Path", "Error"], [[item.get("target"), item.get("path"), item.get("error")] for item in data.get("failed", [])]))
    lines.extend(_error_lines(data))
    return "\n".join(lines).strip() + "\n"


def _format_markdown(command: str, data: dict[str, Any]) -> str:
    if command == "search":
        if not data.get("ok", False) and (data.get("error") or data.get("error_type")):
            lines = ["# Smart Search Search", ""]
            if data.get("query"):
                lines.append(f"Query: `{data.get('query')}`")
            if data.get("provider") is not None:
                lines.append(f"Provider: `{data.get('provider')}`")
            if data.get("model") is not None:
                lines.append(f"Model: `{data.get('model')}`")
            if data.get("stream") is not None:
                lines.append(f"Stream: {_yes_no(data.get('stream'))}")
            if data.get("recommendation"):
                lines.extend(["", "## Recommendation", str(data.get("recommendation"))])
            if data.get("diagnose_command"):
                lines.extend(["", "## Next Command"])
                lines.extend(_markdown_code_block(data.get("diagnose_command")))
            lines.extend(_error_lines(data))
            return "\n".join(lines).strip() + "\n"
        lines = [data.get("content", "")]
        primary_sources = data.get("primary_sources") or []
        extra_sources = data.get("extra_sources") or []
        if primary_sources or extra_sources:
            warning = data.get("source_warning") or ""
            if warning:
                lines.append(f"\n> {warning}")
            if primary_sources:
                lines.append("\n## Primary Sources")
                for item in primary_sources:
                    url = item.get("url", "")
                    title = item.get("title") or item.get("provider") or url
                    lines.append(f"- [{title}]({url})")
            if extra_sources:
                lines.append("\n## Extra Sources")
                for item in extra_sources:
                    url = item.get("url", "")
                    title = item.get("title") or item.get("provider") or url
                    lines.append(f"- [{title}]({url})")
            return "\n".join(lines).strip() + "\n"

        sources = data.get("sources") or []
        if sources:
            lines.append("\n## Sources")
            for item in sources:
                url = item.get("url", "")
                title = item.get("title") or item.get("provider") or url
                lines.append(f"- [{title}]({url})")
        return "\n".join(lines).strip() + "\n"
    if command == "fetch":
        return (data.get("content") or "") + ("\n" if data.get("content") else "")
    if command == "context7-docs":
        content = data.get("content") or ""
        lines = [
            "# Context7 Docs",
            "",
            f"Status: {_status_label(data.get('ok'))}",
            f"Library: `{data.get('library_id', '')}`",
            f"Query: `{data.get('query', '')}`",
        ]
        if content:
            lines.extend(["", content])
        lines.extend(_error_lines(data))
        return "\n".join(lines).strip() + "\n"
    if command == "deep":
        lines = [
            "# Deep Research Plan",
            "",
            f"**Question:** {data.get('question', '')}",
            f"**Mode:** {data.get('mode', '')}",
            f"**Difficulty:** {data.get('difficulty', '')}",
            f"**Evidence policy:** {data.get('evidence_policy', '')}",
            "",
            "## Boundary",
        ]
        usage_boundary = data.get("usage_boundary") or {}
        for key in ("search", "deep", "execution"):
            if usage_boundary.get(key):
                lines.append(f"- **{key}:** {usage_boundary[key]}")
        decomposition = data.get("decomposition") or []
        if decomposition:
            lines.extend(["", "## Decomposition"])
            for item in decomposition:
                lines.append(f"- **{item.get('id', '')}:** {item.get('question', '')}")
        steps = data.get("steps") or []
        if steps:
            lines.extend(["", "## Steps"])
            for step in steps:
                lines.append(f"{step.get('id', '')}. `{step.get('tool', '')}` ({step.get('subquestion_id', '')}) - {step.get('purpose', '')}")
                lines.append(f"   ```powershell\n   {step.get('command', '')}\n   ```")
        gap_check = data.get("gap_check") or {}
        if gap_check:
            lines.extend(["", "## Gap Check", gap_check.get("rule", "")])
        return "\n".join(lines).strip() + "\n"
    if command == "route":
        return _format_route_markdown(data)
    if command == "route-calibrate":
        return _format_route_calibrate_markdown(data)
    if command == "research":
        lines = [
            "# Research Report",
            "",
            f"**Question:** {data.get('question', '')}",
            f"**Status:** {_status_label(data.get('ok'))}",
            f"**Route policy:** {data.get('route_policy_version', '')}",
            f"**Evidence dir:** `{data.get('evidence_dir', '')}`",
            f"**Fallback used:** {bool(data.get('fallback_used'))}",
            f"**Degraded:** {bool(data.get('degraded'))}",
            "",
            "## Answer",
            data.get("final_answer") or data.get("content") or "",
        ]
        citations = data.get("citations") or []
        if citations:
            lines.extend(["", "## Citations"])
            for item in citations:
                url = item.get("url", "")
                title = item.get("title") or url
                provider = item.get("provider") or ""
                lines.append(f"- [{title}]({url})" + (f" ({provider})" if provider else ""))
        gaps = (data.get("gap_check") or {}).get("gaps") or []
        if gaps:
            lines.extend(["", "## Gaps"])
            for gap in gaps:
                reason = gap.get("reason", "")
                url = gap.get("url", "")
                lines.append(f"- {reason}" + (f" - {url}" if url else ""))
        return "\n".join(lines).strip() + "\n"
    if command == "doctor":
        return _format_doctor_markdown(data)
    if command == "diagnose":
        return _format_diagnose_markdown(data)
    if command == "smoke":
        return _format_smoke_markdown(data)
    if command == "config":
        return _format_config_markdown(data)
    if command == "model":
        return _format_model_markdown(data)
    if command == "setup":
        return _format_setup_markdown(data)
    if command == "skills":
        return _format_skills_markdown(data)
    titles = {
        "map": "Site Map",
        "exa-search": "Exa Search",
        "exa-similar": "Exa Similar Pages",
        "zhipu-search": "Zhipu Search",
        "zhipu-mcp-search": "Zhipu Coding Plan MCP Search",
        "zhipu-mcp-reader": "Zhipu Coding Plan MCP Reader",
        "zhipu-mcp-search-doc": "Zhipu Coding Plan MCP Search Doc",
        "zhipu-mcp-repo-structure": "Zhipu Coding Plan MCP Repo Structure",
        "zhipu-mcp-read-file": "Zhipu Coding Plan MCP Read File",
        "anysearch-domains": "AnySearch Domains",
        "anysearch-search": "AnySearch Search",
        "anysearch-extract": "AnySearch Extract",
        "anysearch-batch": "AnySearch Batch",
        "context7-library": "Context7 Library Search",
    }
    if command in titles:
        return _format_result_markdown(command, data, titles[command])
    return _format_config_markdown(data)


logger.info("步骤2完成：CLI Markdown 格式化工具已加载")

"""
================================================================================
步骤3：纯文本渲染与安全输出
================================================================================
目标：提供 content/plain 输出和终端编码安全写入。
数据源：格式化结果与 stdout/stderr。
操作：
1) 根据命令选择纯文本内容。
2) 向终端输出时处理编码不兼容。
"""
logger.info("开始加载 CLI 纯文本与安全输出工具...")
def _plain_result_lines(data: dict[str, Any]) -> list[str]:
    results = data.get("results") or []
    if not results:
        return ["No results."] if data.get("ok") else []
    lines = []
    for index, item in enumerate(results, 1):
        title = _result_title(item, index)
        target = _result_target(item)
        summary = _one_line(_result_summary(item), 120)
        line = f"{index}. {title}"
        if target:
            line += f" - {target}"
        if summary:
            line += f" - {summary}"
        lines.append(line)
    return lines


def _format_content(command: str, data: dict[str, Any]) -> str:
    if command in {"search", "fetch", "context7-docs", "research"}:
        content = data.get("content")
        if content:
            return str(content) + "\n"
        if data.get("ok"):
            return ""
        error = _error_summary(data)
        if error:
            return f"{_status_label(data.get('ok'))}: {error}\n"
        return ""
    if command == "route":
        capabilities = ", ".join(data.get("required_capabilities") or []) or "none"
        lines = [
            f"Intent route {_status_label(data.get('ok'))}: capabilities={capabilities}",
            f"mode={data.get('intent_router_mode', '')}; confidence={data.get('confidence', '')}; engines={','.join(data.get('router_engines_used') or [])}",
            f"embedding_model={data.get('embedding_model', '')}; threshold={data.get('embedding_threshold', '')}({data.get('embedding_threshold_source', '')}); margin={data.get('embedding_margin', '')}({data.get('embedding_margin_source', '')})",
        ]
        if data.get("embedding_preset_recommendation"):
            lines.append(
                "embedding_preset_recommendation="
                f"threshold={data.get('embedding_preset_threshold')} "
                f"margin={data.get('embedding_preset_margin')}"
            )
        if data.get("degraded_reason"):
            lines.append(f"degraded={data.get('degraded_reason')}")
        if data.get("error"):
            lines.append(f"Error: {_error_summary(data)}")
        return "\n".join(lines).strip() + "\n"
    if command == "route-calibrate":
        results = data.get("model_results") or []
        ok_count = sum(1 for item in results if item.get("ok"))
        lines = [
            f"Route calibration {_status_label(data.get('ok'))}: {ok_count}/{len(results)} models calibrated",
            f"primary_metric={data.get('primary_metric', data.get('metric', ''))}; dataset={data.get('dataset_size', '')}",
        ]
        if data.get("recommended_model"):
            lines.append(
                "recommended="
                f"{data.get('recommended_model')} "
                f"threshold={data.get('recommended_threshold')} "
                f"margin={data.get('recommended_margin')}"
            )
        if data.get("failed_models"):
            lines.append("failed=" + ",".join(str(item) for item in data.get("failed_models") or []))
        if data.get("error"):
            lines.append(f"Error: {_error_summary(data)}")
        return "\n".join(lines).strip() + "\n"
    if command == "deep" or data.get("mode") == "deep_research":
        lines = [
            f"Deep Research plan for: {data.get('question', '')}",
            "This command only plans; execute the listed CLI steps to perform live research.",
        ]
        return "\n".join(lines) + "\n"
    if command == "doctor":
        configured = data.get("capability_status", {})
        capability_bits = []
        for name, status in configured.items():
            if isinstance(status, dict):
                capability_bits.append(f"{name}={_status_label(status.get('ok'))}")
        lines = [
            f"Doctor {_status_label(data.get('ok'))}: {data.get('config_status', '')}".strip(),
            f"Minimum profile: {_status_label(data.get('minimum_profile_ok'))}",
        ]
        if capability_bits:
            lines.append("Capabilities: " + ", ".join(capability_bits))
        router = data.get("intent_router_status") or {}
        if router.get("embedding_preset_recommendation"):
            lines.append(
                "Embedding preset recommendation: "
                f"threshold={router.get('embedding_preset_threshold')} "
                f"margin={router.get('embedding_preset_margin')}"
            )
        if data.get("error"):
            lines.append(f"Error: {_error_summary(data)}")
        return "\n".join(lines).strip() + "\n"
    if command == "diagnose":
        lines = [
            f"Diagnose {data.get('provider', '')} {_status_label(data.get('ok'))}: {data.get('summary', '')}".strip(),
        ]
        if data.get("recommendation"):
            lines.append(f"Recommendation: {data.get('recommendation')}")
        if data.get("error"):
            lines.append(f"Error: {_error_summary(data)}")
        return "\n".join(lines).strip() + "\n"
    if command == "smoke":
        cases = data.get("cases") or []
        failed = data.get("failed_cases") or []
        degraded = data.get("degraded_cases") or []
        return f"Smoke {data.get('mode', '')} {_status_label(data.get('ok'))}: {len(cases)} cases, {len(failed)} failed, {len(degraded)} degraded\n"
    if command == "config":
        parts = [f"Config {_status_label(data.get('ok'))}"]
        if data.get("config_file"):
            parts.append(f"file={data.get('config_file')}")
        if data.get("config_dir_source"):
            parts.append(f"source={data.get('config_dir_source')}")
        if data.get("config_dir_override_value"):
            parts.append(f"override={data.get('config_dir_override_value')}")
        if data.get("key"):
            parts.append(f"key={data.get('key')}")
        if data.get("value"):
            parts.append(f"value={data.get('value')}")
        values = data.get("values") or {}
        if values:
            parts.append(f"values={len(values)}")
        if data.get("error"):
            parts.append(f"error={_error_summary(data)}")
        return "; ".join(parts) + "\n"
    if command == "model":
        if data.get("error"):
            return f"Model {_status_label(data.get('ok'))}: {_error_summary(data)}\n"
        rows = []
        if data.get("xai_model"):
            rows.append(f"xai-responses={data.get('xai_model')}")
        if data.get("openai_compatible_model"):
            rows.append(f"openai-compatible={data.get('openai_compatible_model')}")
        if data.get("current_model"):
            rows.append(f"current={data.get('current_model')}")
        return ("Models: " + ", ".join(rows) if rows else f"Model {_status_label(data.get('ok'))}") + "\n"
    if command == "setup":
        if data.get("error"):
            return f"Setup {_status_label(data.get('ok'))}: {_error_summary(data)}\n"
        saved = data.get("saved") or data.get("values") or {}
        return f"Setup {_status_label(data.get('ok'))}: {len(saved)} values saved\n"
    if command == "skills":
        if data.get("error"):
            return f"Skills {_status_label(data.get('ok'))}: {_error_summary(data)}\n"
        targets = data.get("targets") or data.get("installed") or []
        counts = data.get("status_counts") or {}
        if counts:
            summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            return f"Skills {_status_label(data.get('ok'))}: {summary}\n"
        return f"Skills {_status_label(data.get('ok'))}: {len(targets)} targets\n"
    if command in {
        "map",
        "exa-search",
        "exa-similar",
        "zhipu-search",
        "zhipu-mcp-search",
        "zhipu-mcp-reader",
        "zhipu-mcp-search-doc",
        "zhipu-mcp-repo-structure",
        "zhipu-mcp-read-file",
        "anysearch-domains",
        "anysearch-search",
        "anysearch-extract",
        "anysearch-batch",
        "context7-library",
    }:
        lines = _plain_result_lines(data)
        if data.get("error"):
            lines.append(f"Error: {_error_summary(data)}")
        return "\n".join(lines).strip() + "\n"
    if data.get("error"):
        return f"{_status_label(data.get('ok'))}: {_error_summary(data)}\n"
    return f"{command}: {_status_label(data.get('ok'))}\n"


def _render(command: str, data: dict[str, Any], fmt: str) -> str:
    logger.info("开始渲染 CLI 输出: command=%s format=%s", command, fmt)
    if fmt == "content":
        rendered = _format_content(command, data)
    elif fmt == "markdown":
        rendered = _format_markdown(command, data)
    else:
        rendered = _json(data)
    logger.info("CLI 输出渲染完成: command=%s format=%s length=%d", command, fmt, len(rendered))
    return rendered


def _stdout_safe(text: str) -> str:
    return _stream_safe(sys.stdout, text)


def _stream_safe(stream: Any, text: str) -> str:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "strict"
    try:
        text.encode(encoding, errors=errors)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding)

logger.info("步骤3完成：CLI 输出模块加载完成")
