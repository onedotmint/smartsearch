"""Argument parser construction and command argument declarations.

This module is intentionally stdlib-only at import time so parser errors can
serialize without loading service, config, providers, or httpx. The parser
registers only the final canonical command tree: V2 evidence leaves, V3
control-plane leaves, and the research workflow namespace. Removed selectors,
aliases, and legacy spellings never reach argparse; the canonical domain
classifier in ``cli_constants`` intercepts them first.
"""

from __future__ import annotations

import argparse
import sys

from .cli_constants import (
    DEFAULT_SKILL_TARGET_IDS,
    PUBLIC_COMMANDS,
    SmartSearchArgumentParser,
    ZHIPU_SEARCH_ENGINE_CHOICES,
    classify_namespace_argv,
    _get_version,
)

class NamespaceArgumentParser(SmartSearchArgumentParser):
    """Top-level parser that recognizes the collision-safe namespace paths."""

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        raw_args = list(sys.argv[1:] if args is None else args)
        normalized_args, operation, attrs = classify_namespace_argv(raw_args)
        parsed = super().parse_args(normalized_args, namespace)
        if operation is not None:
            parsed.namespace_operation = operation
        for key, value in attrs.items():
            setattr(parsed, key, value)
        return parsed


def _hide_advanced_command_help(subparsers: argparse._SubParsersAction) -> None:
    """
    =================================================================================
    步骤1：收窄根命令帮助
    =================================================================================
    目标：让普通用户先看到核心工作流，同时保留高级命令的解析。
    数据源：已注册的顶层 subparser 和 PUBLIC_COMMANDS 白名单。
    操作：
    1) 只保留核心命令的帮助行。
    2) 保留完整 choices 映射，使高级命令继续可调用。
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
        "--fail-on-degraded",
        action="store_true",
        help="Return exit code 6 for degraded v2 or v3 results without changing the envelope.",
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

    # ------------------------------------------------------------------ V2
    search_parser = sub.add_parser(
        "search", help="Run OpenAI-compatible web search."
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

    fetch_parser = sub.add_parser("fetch", help="Fetch a URL as markdown.")
    fetch_parser.set_defaults(command="fetch")
    fetch_parser.add_argument("url")
    _add_format_args(fetch_parser)

    map_parser = sub.add_parser("map", help="Map a website structure.")
    map_parser.set_defaults(command="map")
    map_parser.add_argument("url")
    map_parser.add_argument("--instructions", default="")
    map_parser.add_argument("--max-depth", type=int, default=1)
    map_parser.add_argument("--max-breadth", type=int, default=20)
    map_parser.add_argument("--limit", type=int, default=50)
    map_parser.add_argument("--timeout", type=int, default=150)
    _add_format_args(map_parser)

    capabilities_parser = sub.add_parser(
        "capabilities",
        help="Report configured capabilities for agents and scripts.",
    )
    capabilities_parser.set_defaults(command="capabilities")
    _add_format_args(capabilities_parser)

    # --------------------------------------------------------- Workflow
    research_parser = sub.add_parser(
        "research",
        help="Run live Deep Research with provider-advantage routing and evidence-only synthesis.",
    )
    research_parser.set_defaults(command="research")
    research_parser.add_argument("query")
    research_parser.add_argument("--budget", choices=["quick", "standard", "deep"], default="deep")
    research_parser.add_argument("--profile", choices=["fast", "balanced", "deep"], default="")
    research_parser.add_argument("--evidence-dir", default="")
    research_parser.add_argument("--fallback", choices=["auto", "off"], default="auto")
    _add_format_args(research_parser)

    # ---------------------------------------------------------- V3 leaves
    config_parser = sub.add_parser(
        "config", help="Read or edit the local Smart Search config file."
    )
    config_parser.set_defaults(command="config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True, parser_class=_SubParser)
    config_path = config_sub.add_parser("path")
    config_path.set_defaults(config_command="path")
    _add_format_args(config_path)
    config_list = config_sub.add_parser("list")
    config_list.set_defaults(config_command="list")
    _add_format_args(config_list)
    config_set = config_sub.add_parser("set")
    config_set.set_defaults(config_command="set")
    config_set.add_argument("key")
    config_set.add_argument("value")
    _add_format_args(config_set)
    config_unset = config_sub.add_parser("unset")
    config_unset.set_defaults(config_command="unset")
    config_unset.add_argument("key")
    _add_format_args(config_unset)

    provider = sub.add_parser("provider", help="Provider operations and local metadata.")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True, parser_class=_SubParser)
    provider_list = provider_sub.add_parser("list", help="List provider metadata without probing.")
    provider_list.set_defaults(command="provider-list", namespace_operation="provider-list")
    _add_format_args(provider_list)
    provider_status = provider_sub.add_parser("status", help="Show local provider eligibility without probing.")
    provider_status.set_defaults(command="provider-status", namespace_operation="provider-status")
    _add_format_args(provider_status)
    provider_probe = provider_sub.add_parser(
        "probe",
        help="Probe one named provider's smallest supported connection operation.",
    )
    provider_probe.set_defaults(command="provider-probe", namespace_operation="provider-probe")
    provider_probe.add_argument("provider", help="Runtime provider id from the provider registry.")
    _add_format_args(provider_probe)
    routes = provider_sub.add_parser("routes", help="Manage ordered main-search routes.")
    routes_sub = routes.add_subparsers(dest="model_command", required=True, parser_class=_SubParser)
    for name in ("current", "list"):
        item = routes_sub.add_parser(name)
        item.set_defaults(command="provider", model_command=name, namespace_operation=f"provider-routes-{name}")
        _add_format_args(item)
    route_add = routes_sub.add_parser("add")
    route_add.set_defaults(command="provider", model_command="add", namespace_operation="provider-routes-add")
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
    route_remove.set_defaults(command="provider", model_command="remove", namespace_operation="provider-routes-remove")
    route_remove.add_argument("route_id")
    _add_format_args(route_remove)

    doctor_parser = sub.add_parser(
        "doctor", help="Show masked configuration and connection checks."
    )
    doctor_parser.set_defaults(command="doctor")
    _add_format_args(doctor_parser)

    dev = sub.add_parser("dev", help="Developer diagnostics and local maintenance commands.")
    dev_sub = dev.add_subparsers(dest="dev_command", required=True, parser_class=_SubParser)
    route_explain = dev_sub.add_parser("route-explain")
    route_explain.set_defaults(command="dev", namespace_operation="dev-route-explain")
    route_explain.add_argument("query")
    route_explain.add_argument("--validation", choices=["fast", "balanced", "strict"], default="")
    route_explain.add_argument("--router-mode", choices=["hybrid", "rules", "off"], default="")
    _add_format_args(route_explain)
    route_calibrate = dev_sub.add_parser("route-calibrate")
    route_calibrate.set_defaults(command="dev", namespace_operation="dev-route-calibrate")
    route_calibrate.add_argument("--models", default="")
    _add_format_args(route_calibrate)
    diagnose = dev_sub.add_parser("diagnose")
    diagnose_sub = diagnose.add_subparsers(dest="diagnose_target", required=True, parser_class=_SubParser)
    diagnose_openai = diagnose_sub.add_parser("openai-compatible")
    diagnose_openai.set_defaults(command="dev", diagnose_target="openai-compatible", namespace_operation="dev-diagnose-openai-compatible")
    diagnose_openai.add_argument("--timeout", type=float, default=30, metavar="SECONDS")
    diagnose_openai.add_argument("--format", choices=["json", "markdown", "content"], default="markdown")
    diagnose_openai.add_argument("--output", default="")
    diagnose_openai.add_argument("--force", action="store_true")
    smoke = dev_sub.add_parser("smoke")
    smoke.set_defaults(command="dev", namespace_operation="dev-smoke")
    smoke_mode = smoke.add_mutually_exclusive_group()
    smoke_mode.add_argument("--mode", choices=["mock", "live"], default=None)
    smoke_mode.add_argument("--mock", dest="mode", action="store_const", const="mock")
    smoke_mode.add_argument("--live", dest="mode", action="store_const", const="live")
    smoke.set_defaults(mode="mock")
    _add_format_args(smoke)
    regression = dev_sub.add_parser("regression")
    regression.set_defaults(command="dev", namespace_operation="dev-regression")
    _add_format_args(regression)
    dev_skills = dev_sub.add_parser("skills")
    dev_skills_sub = dev_skills.add_subparsers(dest="skills_command", required=True, parser_class=_SubParser)
    for name in ("status", "update"):
        item = dev_skills_sub.add_parser(name)
        item.set_defaults(command="dev", skills_command=name, namespace_operation=f"dev-skills-{name}")
        item.add_argument("--targets", default=",".join(DEFAULT_SKILL_TARGET_IDS))
        item.add_argument("--all", action="store_true")
        item.add_argument("--skills-root", default="")
        _add_format_args(item)

    _hide_advanced_command_help(sub)
    return parser

__all__ = [name for name in globals() if not name.startswith("__")]
