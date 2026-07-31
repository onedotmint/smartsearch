"""Argument parser construction and command argument declarations.

This module is intentionally stdlib-only at import time so v2 parser errors can
serialize without loading service, config, providers, or httpx.
"""

from __future__ import annotations

import argparse
import sys

from .cli_constants import (
    COMMAND_ALIASES,
    CONFIG_COMMAND_ALIASES,
    DEFAULT_SKILL_TARGET_IDS,
    MODEL_COMMAND_ALIASES,
    PUBLIC_COMMANDS,
    SKILLS_COMMAND_ALIASES,
    SmartSearchArgumentParser,
    ZHIPU_SEARCH_ENGINE_CHOICES,
    classify_namespace_argv,
    prescan_schema_version,
    _get_version,
)

class NamespaceArgumentParser(SmartSearchArgumentParser):
    """Top-level parser that recognizes the two collision-safe namespace paths."""

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        raw_args = list(sys.argv[1:] if args is None else args)
        if prescan_schema_version(raw_args).get("v2"):
            normalized_args, operation = raw_args, None
        else:
            normalized_args, operation = classify_namespace_argv(raw_args)
        parsed = super().parse_args(normalized_args, namespace)
        if operation is not None:
            parsed.namespace_operation = operation
        return parsed


def _hide_advanced_command_help(subparsers: argparse._SubParsersAction) -> None:
    """
    =================================================================================
    步骤1：收窄根命令帮助
    =================================================================================
    目标：让普通用户先看到核心工作流，同时保留高级命令的兼容解析。
    数据源：已注册的顶层 subparser 和 PUBLIC_COMMANDS 白名单。
    操作：
    1) 只保留核心命令的帮助行。
    2) 保留完整 choices 映射，使隐藏命令和别名继续可调用。
    """
    subparsers._choices_actions[:] = [
        action for action in subparsers._choices_actions if action.dest in PUBLIC_COMMANDS
    ]


def _add_format_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "markdown", "content"], default="json")
    parser.add_argument("--output", default="", help="Write rendered output to a file.")
    parser.add_argument("--force", action="store_true", help="Allow replacing an existing output file.")
    parser.add_argument("--prompt-dir", default="", help="Load local UTF-8 Prompt files from this directory.")
    parser.add_argument("--search-prompt-file", default="", help="Use a local UTF-8 search Prompt file.")
    parser.add_argument("--fetch-prompt-file", default="", help="Use a local UTF-8 fetch Prompt file.")
    parser.add_argument("--research-prompt-file", default="", help="Use a local UTF-8 research Prompt file.")

def build_parser(*, raise_on_error: bool = False) -> argparse.ArgumentParser:
    parser = NamespaceArgumentParser(
        prog="smart-search",
        description="Smart Search CLI for AI-agent web research.",
        raise_on_error=raise_on_error,
    )
    parser.add_argument("-v", "--v", "--version", action="version", version=f"%(prog)s {_get_version()}")
    parser.add_argument(
        "--schema-version",
        choices=["1", "2"],
        default="1",
        help="Select the result schema. Version 2 is JSON-only evidence-first Core API.",
    )
    parser.add_argument(
        "--fail-on-degraded",
        action="store_true",
        help="Return exit code 6 for degraded v2 results without changing the envelope.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Include redacted non-stable v2 trace events in meta.trace.",
    )

    class _SubParser(SmartSearchArgumentParser):
        def __init__(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["raise_on_error"] = raise_on_error
            super().__init__(*args, **kwargs)

    sub = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_SubParser,
        metavar="{" + ",".join(PUBLIC_COMMANDS) + "}",
    )

    search_parser = sub.add_parser(
        "search", aliases=COMMAND_ALIASES["search"], help="Run OpenAI-compatible web search."
    )
    search_parser.set_defaults(command="search")
    search_parser.add_argument("query")
    search_parser.add_argument("--platform", default="")
    search_parser.add_argument("--model", default="")
    search_parser.add_argument("--extra-sources", type=int, default=0)
    search_parser.add_argument("--profile", choices=["fast", "balanced", "deep"], default="")
    search_parser.add_argument(
        "--response-mode",
        choices=["evidence", "concise", "synthesized"],
        default="concise",
        help="Choose evidence-only, concise, or full synthesized output.",
    )
    search_parser.add_argument("--validation", choices=["fast", "balanced", "strict"], default="")
    search_parser.add_argument("--fallback", choices=["auto", "off"], default="")
    search_parser.add_argument("--providers", default="auto")
    stream_group = search_parser.add_mutually_exclusive_group()
    stream_group.add_argument("--stream", dest="stream", action="store_true", default=None, help="Use stream=true for OpenAI-compatible main search.")
    stream_group.add_argument("--no-stream", dest="stream", action="store_false", help="Force stream=false for OpenAI-compatible main search.")
    search_parser.add_argument("--timeout", type=float, default=90, metavar="SECONDS", help="Hard timeout in seconds.")
    _add_format_args(search_parser)

    route_parser = sub.add_parser(
        "route", aliases=COMMAND_ALIASES["route"], help="Explain intent routing without running providers."
    )
    route_parser.set_defaults(command="route")
    route_parser.add_argument("query")
    route_parser.add_argument("--validation", choices=["fast", "balanced", "strict"], default="")
    route_parser.add_argument(
        "--router-mode",
        choices=["hybrid", "rules", "off"],
        default="",
        help="Override SMART_SEARCH_INTENT_ROUTER for this diagnostic call.",
    )
    _add_format_args(route_parser)

    route_calibrate_parser = sub.add_parser(
        "route-calibrate",
        aliases=COMMAND_ALIASES["route-calibrate"],
        help="Evaluate embedding intent-routing models and recommend threshold/margin.",
    )
    route_calibrate_parser.set_defaults(command="route-calibrate")
    route_calibrate_parser.add_argument(
        "--models",
        default="",
        help="Comma-separated embedding model names. Defaults to known candidates plus the configured model.",
    )
    _add_format_args(route_calibrate_parser)

    fetch_parser = sub.add_parser("fetch", aliases=COMMAND_ALIASES["fetch"], help="Fetch a URL as markdown.")
    fetch_parser.set_defaults(command="fetch")
    fetch_parser.add_argument("url")
    _add_format_args(fetch_parser)

    map_parser = sub.add_parser("map", aliases=COMMAND_ALIASES["map"], help="Map a website structure.")
    map_parser.set_defaults(command="map")
    map_parser.add_argument("url")
    map_parser.add_argument("--instructions", default="")
    map_parser.add_argument("--max-depth", type=int, default=1)
    map_parser.add_argument("--max-breadth", type=int, default=20)
    map_parser.add_argument("--limit", type=int, default=50)
    map_parser.add_argument("--timeout", type=int, default=150)
    _add_format_args(map_parser)

    exa_parser = sub.add_parser(
        "exa-search", aliases=COMMAND_ALIASES["exa-search"], help="Run Exa source-first search."
    )
    exa_parser.set_defaults(command="exa-search")
    exa_parser.add_argument("query")
    exa_parser.add_argument("--num-results", type=int, default=5)
    exa_parser.add_argument("--search-type", choices=["neural", "keyword", "auto"], default="neural")
    exa_parser.add_argument("--include-text", action="store_true")
    exa_parser.add_argument("--include-highlights", action="store_true")
    exa_parser.add_argument("--start-published-date", default="")
    exa_parser.add_argument("--include-domains", nargs="+", default="")
    exa_parser.add_argument("--exclude-domains", nargs="+", default="")
    exa_parser.add_argument("--category", default="")
    _add_format_args(exa_parser)

    similar_parser = sub.add_parser(
        "exa-similar", aliases=COMMAND_ALIASES["exa-similar"], help="Find pages similar to a URL with Exa."
    )
    similar_parser.set_defaults(command="exa-similar")
    similar_parser.add_argument("url")
    similar_parser.add_argument("--num-results", type=int, default=5)
    _add_format_args(similar_parser)

    zhipu_parser = sub.add_parser(
        "zhipu-search", aliases=COMMAND_ALIASES["zhipu-search"], help="Run Zhipu Web Search source-first search."
    )
    zhipu_parser.set_defaults(command="zhipu-search")
    zhipu_parser.add_argument("query")
    zhipu_parser.add_argument("--count", type=int, default=10)
    zhipu_parser.add_argument("--search-engine", default="")
    zhipu_parser.add_argument("--search-recency-filter", default="noLimit")
    zhipu_parser.add_argument("--search-domain-filter", default="")
    zhipu_parser.add_argument("--content-size", choices=["medium", "high"], default="medium")
    _add_format_args(zhipu_parser)

    zhipu_mcp_search_parser = sub.add_parser(
        "zhipu-mcp-search",
        aliases=COMMAND_ALIASES["zhipu-mcp-search"],
        help="Run Zhipu Coding Plan Remote MCP web_search_prime.",
    )
    zhipu_mcp_search_parser.set_defaults(command="zhipu-mcp-search")
    zhipu_mcp_search_parser.add_argument("query")
    zhipu_mcp_search_parser.add_argument("--count", type=int, default=5)
    _add_format_args(zhipu_mcp_search_parser)

    zhipu_mcp_reader_parser = sub.add_parser(
        "zhipu-mcp-reader",
        aliases=COMMAND_ALIASES["zhipu-mcp-reader"],
        help="Run Zhipu Coding Plan Remote MCP webReader.",
    )
    zhipu_mcp_reader_parser.set_defaults(command="zhipu-mcp-reader")
    zhipu_mcp_reader_parser.add_argument("url")
    _add_format_args(zhipu_mcp_reader_parser)

    zhipu_mcp_search_doc_parser = sub.add_parser(
        "zhipu-mcp-search-doc",
        aliases=COMMAND_ALIASES["zhipu-mcp-search-doc"],
        help="Search repository docs through Zhipu Coding Plan zread MCP.",
    )
    zhipu_mcp_search_doc_parser.set_defaults(command="zhipu-mcp-search-doc")
    zhipu_mcp_search_doc_parser.add_argument("repo")
    zhipu_mcp_search_doc_parser.add_argument("query")
    zhipu_mcp_search_doc_parser.add_argument("--max-results", type=int, default=5)
    _add_format_args(zhipu_mcp_search_doc_parser)

    zhipu_mcp_repo_structure_parser = sub.add_parser(
        "zhipu-mcp-repo-structure",
        aliases=COMMAND_ALIASES["zhipu-mcp-repo-structure"],
        help="Read repository structure through Zhipu Coding Plan zread MCP.",
    )
    zhipu_mcp_repo_structure_parser.set_defaults(command="zhipu-mcp-repo-structure")
    zhipu_mcp_repo_structure_parser.add_argument("repo")
    zhipu_mcp_repo_structure_parser.add_argument("--ref", default="")
    _add_format_args(zhipu_mcp_repo_structure_parser)

    zhipu_mcp_read_file_parser = sub.add_parser(
        "zhipu-mcp-read-file",
        aliases=COMMAND_ALIASES["zhipu-mcp-read-file"],
        help="Read a repository file through Zhipu Coding Plan zread MCP.",
    )
    zhipu_mcp_read_file_parser.set_defaults(command="zhipu-mcp-read-file")
    zhipu_mcp_read_file_parser.add_argument("repo")
    zhipu_mcp_read_file_parser.add_argument("path")
    zhipu_mcp_read_file_parser.add_argument("--ref", default="")
    _add_format_args(zhipu_mcp_read_file_parser)

    anysearch_domains_parser = sub.add_parser(
        "anysearch-domains",
        aliases=COMMAND_ALIASES["anysearch-domains"],
        help="List AnySearch vertical search domains.",
    )
    anysearch_domains_parser.set_defaults(command="anysearch-domains")
    anysearch_domains_parser.add_argument("domain", nargs="?", default="")
    _add_format_args(anysearch_domains_parser)

    anysearch_search_parser = sub.add_parser(
        "anysearch-search",
        aliases=COMMAND_ALIASES["anysearch-search"],
        help="Run experimental AnySearch vertical/general search.",
    )
    anysearch_search_parser.set_defaults(command="anysearch-search")
    anysearch_search_parser.add_argument("query")
    anysearch_search_parser.add_argument("--domain", default="")
    anysearch_search_parser.add_argument("--sub-domain", default="")
    anysearch_search_parser.add_argument("--max-results", type=int, default=5)
    _add_format_args(anysearch_search_parser)

    anysearch_extract_parser = sub.add_parser(
        "anysearch-extract",
        aliases=COMMAND_ALIASES["anysearch-extract"],
        help="Extract a URL through AnySearch experimental extract.",
    )
    anysearch_extract_parser.set_defaults(command="anysearch-extract")
    anysearch_extract_parser.add_argument("url")
    anysearch_extract_parser.add_argument("--max-length", type=int, default=20000)
    _add_format_args(anysearch_extract_parser)

    anysearch_batch_parser = sub.add_parser(
        "anysearch-batch",
        aliases=COMMAND_ALIASES["anysearch-batch"],
        help="Run up to 5 AnySearch queries in parallel.",
    )
    anysearch_batch_parser.set_defaults(command="anysearch-batch")
    anysearch_batch_parser.add_argument("queries", nargs="+")
    anysearch_batch_parser.add_argument("--max-results", type=int, default=3)
    _add_format_args(anysearch_batch_parser)

    context7_library_parser = sub.add_parser(
        "context7-library",
        aliases=COMMAND_ALIASES["context7-library"],
        help="Resolve Context7 library candidates.",
    )
    context7_library_parser.set_defaults(command="context7-library")
    context7_library_parser.add_argument("name")
    context7_library_parser.add_argument("query", nargs="?", default="")
    _add_format_args(context7_library_parser)

    context7_docs_parser = sub.add_parser(
        "context7-docs",
        aliases=COMMAND_ALIASES["context7-docs"],
        help="Fetch Context7 docs for a library.",
    )
    context7_docs_parser.set_defaults(command="context7-docs")
    context7_docs_parser.add_argument("library_id")
    context7_docs_parser.add_argument("query")
    _add_format_args(context7_docs_parser)

    deep_parser = sub.add_parser(
        "deep",
        aliases=COMMAND_ALIASES["deep"],
        help="Create an offline Deep Research plan without calling providers.",
    )
    deep_parser.set_defaults(command="deep")
    deep_parser.add_argument("query")
    deep_parser.add_argument("--budget", choices=["quick", "standard", "deep"], default="standard")
    deep_parser.add_argument("--evidence-dir", default="")
    _add_format_args(deep_parser)

    research_parser = sub.add_parser(
        "research",
        aliases=COMMAND_ALIASES["research"],
        help="Run live Deep Research with provider-advantage routing and evidence-only synthesis.",
    )
    research_parser.set_defaults(command="research")
    research_parser.add_argument("query")
    research_parser.add_argument("--budget", choices=["quick", "standard", "deep"], default="deep")
    research_parser.add_argument("--profile", choices=["fast", "balanced", "deep"], default="")
    research_parser.add_argument("--evidence-dir", default="")
    research_parser.add_argument("--fallback", choices=["auto", "off"], default="auto")
    _add_format_args(research_parser)

    smoke_parser = sub.add_parser(
        "smoke", aliases=COMMAND_ALIASES["smoke"], help="Run provider routing and fallback smoke checks."
    )
    smoke_parser.set_defaults(command="smoke")
    smoke_mode = smoke_parser.add_mutually_exclusive_group()
    smoke_mode.add_argument("--mode", choices=["mock", "live"], default=None)
    smoke_mode.add_argument("--mock", dest="mode", action="store_const", const="mock", help="Run offline mock smoke checks.")
    smoke_mode.add_argument("--live", dest="mode", action="store_const", const="live", help="Run live provider smoke checks.")
    smoke_parser.set_defaults(mode="mock")
    _add_format_args(smoke_parser)

    doctor_parser = sub.add_parser(
        "doctor", aliases=COMMAND_ALIASES["doctor"], help="Show masked configuration and connection checks."
    )
    doctor_parser.set_defaults(command="doctor")
    _add_format_args(doctor_parser)

    capabilities_parser = sub.add_parser(
        "capabilities",
        help="Report configured capabilities for agents and scripts.",
    )
    capabilities_parser.set_defaults(command="capabilities")
    _add_format_args(capabilities_parser)

    diagnose_parser = sub.add_parser(
        "diagnose",
        aliases=COMMAND_ALIASES["diagnose"],
        help="Run focused troubleshooting checks for a provider.",
    )
    diagnose_parser.set_defaults(command="diagnose")
    diagnose_parser.add_argument("diagnose_target", choices=["openai-compatible"])
    diagnose_parser.add_argument("--timeout", type=float, default=30, metavar="SECONDS", help="Per search-shape probe timeout in seconds.")
    diagnose_parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    diagnose_parser.add_argument("--output", default="", help="Write rendered output to a file.")
    diagnose_parser.add_argument("--force", action="store_true", help="Allow replacing an existing output file.")

    model_parser = sub.add_parser(
        "model",
        aliases=COMMAND_ALIASES["model"],
        help="Manage ordered main-search model routes.",
    )
    model_parser.set_defaults(command="model")
    model_sub = model_parser.add_subparsers(dest="model_command", required=True, parser_class=_SubParser)
    model_current = model_sub.add_parser("current", aliases=MODEL_COMMAND_ALIASES["current"])
    model_current.set_defaults(model_command="current")
    _add_format_args(model_current)
    model_list = model_sub.add_parser("list", aliases=MODEL_COMMAND_ALIASES["list"], help="List ordered model routes.")
    model_list.set_defaults(model_command="list")
    _add_format_args(model_list)
    model_add = model_sub.add_parser("add", aliases=MODEL_COMMAND_ALIASES["add"], help="Append one model route.")
    model_add.set_defaults(model_command="add")
    model_add.add_argument("--id", dest="route_id", required=True, help="Stable route ID used by model remove.")
    model_add.add_argument(
        "--provider",
        choices=["xai-responses", "openai-compatible"],
        default="openai-compatible",
        help="Provider protocol for this route.",
    )
    model_add.add_argument("--api-url", required=True, help="Provider API base URL.")
    model_add.add_argument("--api-key", required=True, help="Provider API key.")
    model_add.add_argument("--model", dest="model_name", required=True, help="Provider model name.")
    model_add.add_argument("--tools", default="", help="xAI tools, comma-separated: web_search,x_search.")
    model_add.add_argument("--fallback-models", default="", help="Same-endpoint fallback models, comma-separated.")
    stream_group = model_add.add_mutually_exclusive_group()
    stream_group.add_argument("--stream", dest="stream", action="store_true", default=False, help="Use stream=true for this OpenAI-compatible route.")
    stream_group.add_argument("--no-stream", dest="stream", action="store_false", help="Use stream=false for this OpenAI-compatible route.")
    _add_format_args(model_add)
    model_remove = model_sub.add_parser("remove", aliases=MODEL_COMMAND_ALIASES["remove"], help="Remove one model route by ID.")
    model_remove.set_defaults(model_command="remove")
    model_remove.add_argument("route_id")
    _add_format_args(model_remove)

    skills_parser = sub.add_parser(
        "skills",
        aliases=COMMAND_ALIASES["skills"],
        help="Inspect or update installed smart-search-cli skills.",
    )
    skills_parser.set_defaults(command="skills")
    skills_sub = skills_parser.add_subparsers(dest="skills_command", required=True, parser_class=_SubParser)
    skills_status = skills_sub.add_parser("status", aliases=SKILLS_COMMAND_ALIASES["status"], help="Compare bundled and installed skill files.")
    skills_status.set_defaults(skills_command="status")
    skills_status.add_argument(
        "--targets",
        default=",".join(DEFAULT_SKILL_TARGET_IDS),
        help="Comma-separated AI tool targets, e.g. codex,claude,cursor,hermes.",
    )
    skills_status.add_argument("--all", action="store_true", help="Check every known skill target.")
    skills_status.add_argument(
        "--skills-root",
        default="",
        help="Advanced override for the user-level skill root; defaults to the current user's home directory.",
    )
    _add_format_args(skills_status)
    skills_update = skills_sub.add_parser("update", aliases=SKILLS_COMMAND_ALIASES["update"], help="Overwrite selected installed skill files with bundled assets.")
    skills_update.set_defaults(skills_command="update")
    skills_update.add_argument(
        "--targets",
        default=",".join(DEFAULT_SKILL_TARGET_IDS),
        help="Comma-separated AI tool targets, e.g. codex,claude,cursor,hermes.",
    )
    skills_update.add_argument("--all", action="store_true", help="Update every known skill target.")
    skills_update.add_argument(
        "--skills-root",
        default="",
        help="Advanced override for the user-level skill root; defaults to the current user's home directory.",
    )
    _add_format_args(skills_update)

    setup_parser = sub.add_parser(
        "setup", aliases=COMMAND_ALIASES["setup"], help="Interactively save local provider configuration."
    )
    setup_parser.set_defaults(command="setup")
    setup_parser.add_argument("--non-interactive", action="store_true", help="Only save values passed as flags.")
    setup_parser.add_argument("--lang", choices=["zh", "en"], default="", help="Interactive setup language.")
    setup_parser.add_argument("--advanced", action="store_true", help="Show every low-level config key in interactive setup.")
    setup_parser.add_argument("--skip-skills", action="store_true", help="Skip user-level smart-search-cli skill installation.")
    setup_parser.add_argument(
        "--install-skills",
        default="",
        help="Comma-separated AI tool targets for smart-search-cli skill installation, e.g. codex,claude,cursor,hermes.",
    )
    setup_parser.add_argument(
        "--skills-root",
        default="",
        help="Advanced override for the user-level skill root; defaults to the current user's home directory.",
    )
    setup_parser.add_argument("--xai-api-url", default="", help="Save XAI_API_URL.")
    setup_parser.add_argument("--xai-api-key", default="", help="Save XAI_API_KEY.")
    setup_parser.add_argument("--xai-model", default="", help="Save XAI_MODEL.")
    setup_parser.add_argument("--xai-tools-explicit", default="", help="Save XAI_TOOLS.")
    setup_parser.add_argument("--openai-compatible-api-url", default="", help="Save OPENAI_COMPATIBLE_API_URL.")
    setup_parser.add_argument("--openai-compatible-api-key", default="", help="Save OPENAI_COMPATIBLE_API_KEY.")
    setup_parser.add_argument("--openai-compatible-model", default="", help="Save OPENAI_COMPATIBLE_MODEL.")
    setup_parser.add_argument("--openai-compatible-fallback-models", default="", help="Save OPENAI_COMPATIBLE_FALLBACK_MODELS.")
    setup_parser.add_argument("--openai-compatible-stream", default="", help="Save OPENAI_COMPATIBLE_STREAM.")
    setup_parser.add_argument("--validation-level", default="", help="Save SMART_SEARCH_VALIDATION_LEVEL.")
    setup_parser.add_argument("--fallback-mode", default="", help="Save SMART_SEARCH_FALLBACK_MODE.")
    setup_parser.add_argument("--minimum-profile", default="", help="Save SMART_SEARCH_MINIMUM_PROFILE.")
    setup_parser.add_argument("--intent-router", default="", help="Save SMART_SEARCH_INTENT_ROUTER.")
    setup_parser.add_argument("--intent-embedding-api-url", default="", help="Save INTENT_EMBEDDING_API_URL.")
    setup_parser.add_argument("--intent-embedding-api-key", default="", help="Save INTENT_EMBEDDING_API_KEY.")
    setup_parser.add_argument("--intent-embedding-model", default="", help="Save INTENT_EMBEDDING_MODEL.")
    setup_parser.add_argument("--intent-embedding-threshold", default="", help="Save INTENT_EMBEDDING_THRESHOLD.")
    setup_parser.add_argument("--intent-embedding-margin", default="", help="Save INTENT_EMBEDDING_MARGIN.")
    setup_parser.add_argument("--intent-classifier-api-url", default="", help="Save INTENT_CLASSIFIER_API_URL.")
    setup_parser.add_argument("--intent-classifier-api-key", default="", help="Save INTENT_CLASSIFIER_API_KEY.")
    setup_parser.add_argument("--intent-classifier-model", default="", help="Save INTENT_CLASSIFIER_MODEL.")
    setup_parser.add_argument("--intent-router-timeout", default="", help="Save INTENT_ROUTER_TIMEOUT_SECONDS.")
    setup_parser.add_argument("--exa-key", default="", help="Save EXA_API_KEY.")
    setup_parser.add_argument("--context7-key", default="", help="Save CONTEXT7_API_KEY.")
    setup_parser.add_argument("--zhipu-key", default="", help="Save ZHIPU_API_KEY.")
    setup_parser.add_argument("--zhipu-api-url", default="", help="Save ZHIPU_API_URL.")
    setup_parser.add_argument("--zhipu-search-engine", default="", help="Save ZHIPU_SEARCH_ENGINE.")
    setup_parser.add_argument("--zhipu-mcp-key", default="", help="Save ZHIPU_MCP_API_KEY.")
    setup_parser.add_argument("--zhipu-mcp-search-api-url", default="", help="Save ZHIPU_MCP_SEARCH_API_URL.")
    setup_parser.add_argument("--zhipu-mcp-reader-api-url", default="", help="Save ZHIPU_MCP_READER_API_URL.")
    setup_parser.add_argument("--zhipu-mcp-zread-api-url", default="", help="Save ZHIPU_MCP_ZREAD_API_URL.")
    setup_parser.add_argument("--zhipu-mcp-timeout", default="", help="Save ZHIPU_MCP_TIMEOUT_SECONDS.")
    setup_parser.add_argument("--jina-key", default="", help="Save JINA_API_KEY.")
    setup_parser.add_argument("--jina-reader-api-url", default="", help="Save JINA_READER_API_URL.")
    setup_parser.add_argument("--jina-respond-with", default="", help="Save JINA_RESPOND_WITH, e.g. readerlm-v2.")
    setup_parser.add_argument("--jina-timeout", default="", help="Save JINA_TIMEOUT_SECONDS.")
    setup_parser.add_argument("--tavily-api-url", default="", help="Save TAVILY_API_URL.")
    setup_parser.add_argument("--tavily-key", default="", help="Save TAVILY_API_KEY.")
    setup_parser.add_argument("--firecrawl-api-url", default="", help="Save FIRECRAWL_API_URL.")
    setup_parser.add_argument("--firecrawl-key", default="", help="Save FIRECRAWL_API_KEY.")
    setup_parser.add_argument("--anysearch-api-url", default="", help="Save ANYSEARCH_API_URL.")
    setup_parser.add_argument("--anysearch-key", default="", help="Save ANYSEARCH_API_KEY.")
    setup_parser.add_argument("--anysearch-timeout", default="", help="Save ANYSEARCH_TIMEOUT_SECONDS.")
    _add_format_args(setup_parser)

    config_parser = sub.add_parser(
        "config", aliases=COMMAND_ALIASES["config"], help="Read or edit the local Smart Search config file."
    )
    config_parser.set_defaults(command="config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True, parser_class=_SubParser)
    config_path = config_sub.add_parser("path", aliases=CONFIG_COMMAND_ALIASES["path"])
    config_path.set_defaults(config_command="path")
    _add_format_args(config_path)
    config_list = config_sub.add_parser("list", aliases=CONFIG_COMMAND_ALIASES["list"])
    config_list.set_defaults(config_command="list")
    _add_format_args(config_list)
    config_set = config_sub.add_parser("set", aliases=CONFIG_COMMAND_ALIASES["set"])
    config_set.set_defaults(config_command="set")
    config_set.add_argument("key")
    config_set.add_argument("value")
    _add_format_args(config_set)
    config_unset = config_sub.add_parser("unset", aliases=CONFIG_COMMAND_ALIASES["unset"])
    config_unset.set_defaults(config_command="unset")
    config_unset.add_argument("key")
    _add_format_args(config_unset)

    regression_parser = sub.add_parser(
        "regression", aliases=COMMAND_ALIASES["regression"], help="Run offline CLI regression tests."
    )
    regression_parser.set_defaults(command="regression")

    provider = sub.add_parser("provider", help="Provider operations and local metadata.")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True, parser_class=_SubParser)
    provider_list = provider_sub.add_parser("list", help="List provider metadata without probing.")
    provider_list.set_defaults(command="provider-list", namespace_operation="provider-list")
    _add_format_args(provider_list)
    provider_status = provider_sub.add_parser("status", help="Show local provider eligibility without probing.")
    provider_status.set_defaults(command="provider-status", namespace_operation="provider-status")
    _add_format_args(provider_status)
    routes = provider_sub.add_parser("routes", help="Manage ordered main-search routes.")
    routes_sub = routes.add_subparsers(dest="model_command", required=True, parser_class=_SubParser)
    for name in ("current", "list"):
        item = routes_sub.add_parser(name)
        item.set_defaults(command="model", model_command=name, namespace_operation=f"provider-routes-{name}")
        _add_format_args(item)
    route_add = routes_sub.add_parser("add")
    route_add.set_defaults(command="model", model_command="add", namespace_operation="provider-routes-add")
    route_add.add_argument("--id", dest="route_id", required=True)
    route_add.add_argument("--provider", choices=["xai-responses", "openai-compatible"], default="openai-compatible")
    route_add.add_argument("--api-url", required=True)
    route_add.add_argument("--api-key", required=True)
    route_add.add_argument("--model", dest="model_name", required=True)
    route_add.add_argument("--tools", default="")
    route_add.add_argument("--fallback-models", default="")
    route_stream = route_add.add_mutually_exclusive_group()
    route_stream.add_argument("--stream", dest="stream", action="store_true", default=False)
    route_stream.add_argument("--no-stream", dest="stream", action="store_false")
    _add_format_args(route_add)
    route_remove = routes_sub.add_parser("remove")
    route_remove.set_defaults(command="model", model_command="remove", namespace_operation="provider-routes-remove")
    route_remove.add_argument("route_id")
    _add_format_args(route_remove)

    provider_exa = provider_sub.add_parser("exa", help="Run exact Exa operations.")
    exa_sub = provider_exa.add_subparsers(dest="provider_exa_command", required=True, parser_class=_SubParser)
    exa_search = exa_sub.add_parser("search")
    exa_search.set_defaults(command="exa-search", namespace_operation="provider-exa-search")
    exa_search.add_argument("query")
    exa_search.add_argument("--num-results", type=int, default=5)
    exa_search.add_argument("--search-type", choices=["neural", "keyword", "auto"], default="neural")
    exa_search.add_argument("--include-text", action="store_true")
    exa_search.add_argument("--include-highlights", action="store_true")
    exa_search.add_argument("--start-published-date", default="")
    exa_search.add_argument("--include-domains", nargs="+", default="")
    exa_search.add_argument("--exclude-domains", nargs="+", default="")
    exa_search.add_argument("--category", default="")
    _add_format_args(exa_search)
    exa_similar = exa_sub.add_parser("similar")
    exa_similar.set_defaults(command="exa-similar", namespace_operation="provider-exa-similar")
    exa_similar.add_argument("url")
    exa_similar.add_argument("--num-results", type=int, default=5)
    _add_format_args(exa_similar)

    provider_context7 = provider_sub.add_parser("context7", help="Run exact Context7 operations.")
    context7_sub = provider_context7.add_subparsers(dest="provider_context7_command", required=True, parser_class=_SubParser)
    context7_library = context7_sub.add_parser("library")
    context7_library.set_defaults(command="context7-library", namespace_operation="provider-context7-library")
    context7_library.add_argument("name")
    context7_library.add_argument("query", nargs="?", default="")
    _add_format_args(context7_library)
    context7_docs = context7_sub.add_parser("docs")
    context7_docs.set_defaults(command="context7-docs", namespace_operation="provider-context7-docs")
    context7_docs.add_argument("library_id")
    context7_docs.add_argument("query")
    _add_format_args(context7_docs)

    provider_zhipu = provider_sub.add_parser("zhipu", help="Run exact Zhipu REST operations.")
    zhipu_sub = provider_zhipu.add_subparsers(dest="provider_zhipu_command", required=True, parser_class=_SubParser)
    zhipu_search_ns = zhipu_sub.add_parser("search")
    zhipu_search_ns.set_defaults(command="zhipu-search", namespace_operation="provider-zhipu-search")
    zhipu_search_ns.add_argument("query")
    zhipu_search_ns.add_argument("--count", type=int, default=10)
    zhipu_search_ns.add_argument("--search-engine", default="")
    zhipu_search_ns.add_argument("--search-recency-filter", default="noLimit")
    zhipu_search_ns.add_argument("--search-domain-filter", default="")
    zhipu_search_ns.add_argument("--content-size", choices=["medium", "high"], default="medium")
    _add_format_args(zhipu_search_ns)
    provider_mcp = provider_sub.add_parser("zhipu-mcp", help="Run exact Zhipu Coding Plan MCP operations.")
    mcp_sub = provider_mcp.add_subparsers(dest="provider_mcp_command", required=True, parser_class=_SubParser)
    mcp_search = mcp_sub.add_parser("search")
    mcp_search.set_defaults(command="zhipu-mcp-search", namespace_operation="provider-zhipu-mcp-search")
    mcp_search.add_argument("query")
    mcp_search.add_argument("--count", type=int, default=5)
    _add_format_args(mcp_search)
    mcp_reader = mcp_sub.add_parser("reader")
    mcp_reader.set_defaults(command="zhipu-mcp-reader", namespace_operation="provider-zhipu-mcp-reader")
    mcp_reader.add_argument("url")
    _add_format_args(mcp_reader)

    dev = sub.add_parser("dev", help="Developer diagnostics and local maintenance commands.")
    dev_sub = dev.add_subparsers(dest="dev_command", required=True, parser_class=_SubParser)
    route_explain = dev_sub.add_parser("route-explain")
    route_explain.set_defaults(command="route", namespace_operation="dev-route-explain")
    route_explain.add_argument("query")
    route_explain.add_argument("--validation", choices=["fast", "balanced", "strict"], default="")
    route_explain.add_argument("--router-mode", choices=["hybrid", "rules", "off"], default="")
    _add_format_args(route_explain)
    route_calibrate = dev_sub.add_parser("route-calibrate")
    route_calibrate.set_defaults(command="route-calibrate", namespace_operation="dev-route-calibrate")
    route_calibrate.add_argument("--models", default="")
    _add_format_args(route_calibrate)
    diagnose = dev_sub.add_parser("diagnose")
    diagnose_sub = diagnose.add_subparsers(dest="diagnose_target", required=True, parser_class=_SubParser)
    diagnose_openai = diagnose_sub.add_parser("openai-compatible")
    diagnose_openai.set_defaults(command="diagnose", diagnose_target="openai-compatible", namespace_operation="dev-diagnose-openai-compatible")
    diagnose_openai.add_argument("--timeout", type=float, default=30, metavar="SECONDS")
    diagnose_openai.add_argument("--format", choices=["json", "markdown"], default="markdown")
    diagnose_openai.add_argument("--output", default="")
    diagnose_openai.add_argument("--force", action="store_true")
    smoke = dev_sub.add_parser("smoke")
    smoke.set_defaults(command="smoke", namespace_operation="dev-smoke")
    smoke_mode = smoke.add_mutually_exclusive_group()
    smoke_mode.add_argument("--mode", choices=["mock", "live"], default=None)
    smoke_mode.add_argument("--mock", dest="mode", action="store_const", const="mock")
    smoke_mode.add_argument("--live", dest="mode", action="store_const", const="live")
    smoke.set_defaults(mode="mock")
    _add_format_args(smoke)
    regression = dev_sub.add_parser("regression")
    regression.set_defaults(command="regression", namespace_operation="dev-regression")
    dev_skills = dev_sub.add_parser("skills")
    dev_skills_sub = dev_skills.add_subparsers(dest="skills_command", required=True, parser_class=_SubParser)
    for name in ("status", "update"):
        item = dev_skills_sub.add_parser(name)
        item.set_defaults(command="skills", skills_command=name, namespace_operation=f"dev-skills-{name}")
        item.add_argument("--targets", default=",".join(DEFAULT_SKILL_TARGET_IDS))
        item.add_argument("--all", action="store_true")
        item.add_argument("--skills-root", default="")
        _add_format_args(item)

    experimental = sub.add_parser("experimental", help="Explicit experimental provider operations.")
    experimental_sub = experimental.add_subparsers(dest="experimental_command", required=True, parser_class=_SubParser)
    anysearch = experimental_sub.add_parser("anysearch")
    any_sub = anysearch.add_subparsers(dest="anysearch_command", required=True, parser_class=_SubParser)
    any_domains = any_sub.add_parser("domains")
    any_domains.set_defaults(command="anysearch-domains", namespace_operation="experimental-anysearch-domains")
    any_domains.add_argument("domain", nargs="?", default="")
    _add_format_args(any_domains)
    any_search = any_sub.add_parser("search")
    any_search.set_defaults(command="anysearch-search", namespace_operation="experimental-anysearch-search")
    any_search.add_argument("query")
    any_search.add_argument("--domain", default="")
    any_search.add_argument("--sub-domain", default="")
    any_search.add_argument("--max-results", type=int, default=5)
    _add_format_args(any_search)
    any_extract = any_sub.add_parser("extract")
    any_extract.set_defaults(command="anysearch-extract", namespace_operation="experimental-anysearch-extract")
    any_extract.add_argument("url")
    any_extract.add_argument("--max-length", type=int, default=20000)
    _add_format_args(any_extract)
    any_batch = any_sub.add_parser("batch")
    any_batch.set_defaults(command="anysearch-batch", namespace_operation="experimental-anysearch-batch")
    any_batch.add_argument("queries", nargs="+")
    any_batch.add_argument("--max-results", type=int, default=3)
    _add_format_args(any_batch)
    zread = experimental_sub.add_parser("zread")
    zread_sub = zread.add_subparsers(dest="zread_command", required=True, parser_class=_SubParser)
    zread_doc = zread_sub.add_parser("search-doc")
    zread_doc.set_defaults(command="zhipu-mcp-search-doc", namespace_operation="experimental-zread-search-doc")
    zread_doc.add_argument("repo")
    zread_doc.add_argument("query")
    zread_doc.add_argument("--max-results", type=int, default=5)
    _add_format_args(zread_doc)
    zread_tree = zread_sub.add_parser("repo-structure")
    zread_tree.set_defaults(command="zhipu-mcp-repo-structure", namespace_operation="experimental-zread-repo-structure")
    zread_tree.add_argument("repo")
    zread_tree.add_argument("--ref", default="")
    _add_format_args(zread_tree)
    zread_file = zread_sub.add_parser("read-file")
    zread_file.set_defaults(command="zhipu-mcp-read-file", namespace_operation="experimental-zread-read-file")
    zread_file.add_argument("repo")
    zread_file.add_argument("path")
    zread_file.add_argument("--ref", default="")
    _add_format_args(zread_file)

    _hide_advanced_command_help(sub)
    return parser

__all__ = [name for name in globals() if not name.startswith("__")]
