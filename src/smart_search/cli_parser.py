"""Argument parser construction and command argument declarations."""

from .cli_support import *

class SmartSearchArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


PUBLIC_COMMANDS = (
    "search",
    "route",
    "fetch",
    "map",
    "deep",
    "research",
    "doctor",
    "setup",
    "config",
    "skills",
)


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

def build_parser() -> argparse.ArgumentParser:
    parser = SmartSearchArgumentParser(
        prog="smart-search",
        description="Smart Search CLI for AI-agent web research.",
    )
    parser.add_argument("-v", "--v", "--version", action="version", version=f"%(prog)s {_get_version()}")
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=SmartSearchArgumentParser,
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
    model_sub = model_parser.add_subparsers(dest="model_command", required=True, parser_class=SmartSearchArgumentParser)
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
    skills_sub = skills_parser.add_subparsers(dest="skills_command", required=True, parser_class=SmartSearchArgumentParser)
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
    config_sub = config_parser.add_subparsers(dest="config_command", required=True, parser_class=SmartSearchArgumentParser)
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
    _hide_advanced_command_help(sub)
    return parser

__all__ = [name for name in globals() if not name.startswith("__")]
