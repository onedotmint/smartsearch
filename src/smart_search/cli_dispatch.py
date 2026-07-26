"""Command dispatch and command-specific synchronous handlers."""

from .cli_support import *
from .cli_setup import *
from .capability_service import _minimum_profile_result
from .cli import _exit_code, _print_result

async def _run_async(args: argparse.Namespace) -> int:
    if args.command == "search":
        search_kwargs = {
            "platform": args.platform,
            "model": args.model,
            "extra_sources": args.extra_sources,
            "validation": args.validation,
            "fallback": args.fallback,
            "providers": args.providers,
        }
        if _supports_argument(service.search, "profile"):
            search_kwargs["profile"] = args.profile
        if _supports_argument(service.search, "response_mode"):
            search_kwargs["response_mode"] = args.response_mode
        if args.stream is not None:
            search_kwargs["stream"] = args.stream
        if _supports_argument(service.search, "timeout_seconds"):
            search_kwargs["timeout_seconds"] = args.timeout
        with _prompt_override_context(args):
            try:
                data = await asyncio.wait_for(
                    service.search(args.query, **search_kwargs),
                    timeout=args.timeout,
                )
            except asyncio.TimeoutError:
                data = _search_timeout_result(args.query, args.timeout, search_kwargs)
                return _print_result("search", data, args.format, args.output)
        return _print_result("search", data, args.format, args.output)
    if args.command == "route":
        data = await service.route(args.query, validation=args.validation, mode=args.router_mode)
        return _print_result("route", data, args.format, args.output)
    if args.command == "route-calibrate":
        data = await service.route_calibrate(models=args.models)
        return _print_result("route-calibrate", data, args.format, args.output)
    if args.command == "fetch":
        with _prompt_override_context(args):
            data = await service.fetch(args.url)
        return _print_result("fetch", data, args.format, args.output)
    if args.command == "map":
        data = await service.map_site(
            args.url,
            instructions=args.instructions,
            max_depth=args.max_depth,
            max_breadth=args.max_breadth,
            limit=args.limit,
            timeout=args.timeout,
        )
        return _print_result("map", data, args.format, args.output)
    if args.command == "exa-search":
        data = await service.exa_search(
            args.query,
            num_results=args.num_results,
            search_type=args.search_type,
            include_text=args.include_text,
            include_highlights=args.include_highlights,
            start_published_date=args.start_published_date,
            include_domains=args.include_domains,
            exclude_domains=args.exclude_domains,
            category=args.category,
        )
        return _print_result("exa-search", data, args.format, args.output)
    if args.command == "exa-similar":
        data = await service.exa_find_similar(args.url, num_results=args.num_results)
        return _print_result("exa-similar", data, args.format, args.output)
    if args.command == "zhipu-search":
        data = await service.zhipu_search(
            args.query,
            count=args.count,
            search_engine=args.search_engine,
            search_recency_filter=args.search_recency_filter,
            search_domain_filter=args.search_domain_filter,
            content_size=args.content_size,
        )
        return _print_result("zhipu-search", data, args.format, args.output)
    if args.command == "zhipu-mcp-search":
        data = await service.zhipu_mcp_search(args.query, count=args.count)
        return _print_result("zhipu-mcp-search", data, args.format, args.output)
    if args.command == "zhipu-mcp-reader":
        data = await service.zhipu_mcp_reader(args.url)
        return _print_result("zhipu-mcp-reader", data, args.format, args.output)
    if args.command == "zhipu-mcp-search-doc":
        data = await service.zhipu_mcp_search_doc(args.repo, args.query, max_results=args.max_results)
        return _print_result("zhipu-mcp-search-doc", data, args.format, args.output)
    if args.command == "zhipu-mcp-repo-structure":
        data = await service.zhipu_mcp_repo_structure(args.repo, ref=args.ref)
        return _print_result("zhipu-mcp-repo-structure", data, args.format, args.output)
    if args.command == "zhipu-mcp-read-file":
        data = await service.zhipu_mcp_read_file(args.repo, args.path, ref=args.ref)
        return _print_result("zhipu-mcp-read-file", data, args.format, args.output)
    if args.command == "anysearch-domains":
        data = await service.anysearch_domains(args.domain)
        return _print_result("anysearch-domains", data, args.format, args.output)
    if args.command == "anysearch-search":
        data = await service.anysearch_search(
            args.query,
            domain=args.domain,
            sub_domain=args.sub_domain,
            max_results=args.max_results,
        )
        return _print_result("anysearch-search", data, args.format, args.output)
    if args.command == "anysearch-extract":
        data = await service.anysearch_extract(args.url, max_length=args.max_length)
        return _print_result("anysearch-extract", data, args.format, args.output)
    if args.command == "anysearch-batch":
        data = await service.anysearch_batch(args.queries, max_results=args.max_results)
        return _print_result("anysearch-batch", data, args.format, args.output)
    if args.command == "context7-library":
        data = await service.context7_library(args.name, args.query)
        return _print_result("context7-library", data, args.format, args.output)
    if args.command == "context7-docs":
        data = await service.context7_docs(args.library_id, args.query)
        return _print_result("context7-docs", data, args.format, args.output)
    if args.command == "deep":
        data = service.build_deep_research_plan(
            args.query,
            budget=args.budget,
            evidence_dir=args.evidence_dir,
        )
        return _print_result("deep", data, args.format, args.output)
    if args.command == "research":
        research_budget = {
            "fast": "quick",
            "balanced": "standard",
            "deep": "deep",
        }.get(args.profile, args.budget)
        with _prompt_override_context(args):
            data = await service.research(
                args.query,
                budget=research_budget,
                evidence_dir=args.evidence_dir,
                fallback=args.fallback,
            )
        return _print_result("research", data, args.format, args.output)
    if args.command == "smoke":
        data = await service.smoke(args.mode)
        return _print_result("smoke", data, args.format, args.output)
    if args.command == "doctor":
        data = await service.doctor()
        return _print_result("doctor", data, args.format, args.output)
    if args.command == "capabilities":
        data = service.capabilities()
        return _print_result("capabilities", data, args.format, args.output)
    if args.command == "diagnose":
        if args.diagnose_target == "openai-compatible":
            data = await service.diagnose_openai_compatible(timeout_seconds=args.timeout)
            return _print_result("diagnose", data, args.format, args.output)
        return _print_result(
            "diagnose",
            {"ok": False, "error_type": "parameter_error", "error": f"Unknown diagnose target: {args.diagnose_target}"},
            args.format,
            args.output,
        )
    return EXIT_PARAMETER_ERROR

def _run_model(args: argparse.Namespace) -> int:
    if args.model_command == "current":
        data = service.current_model()
    elif args.model_command == "list":
        data = service.model_list()
    elif args.model_command == "add":
        data = service.model_add(
            args.route_id,
            args.provider,
            args.api_url,
            args.api_key,
            args.model_name,
            tools=args.tools,
            stream=args.stream,
            fallback_models=args.fallback_models,
        )
    elif args.model_command == "remove":
        data = service.model_remove(args.route_id)
    else:
        data = {"ok": False, "error_type": "parameter_error", "error": "Unknown model command"}
    return _print_result("model", data, args.format, args.output)

def _run_config(args: argparse.Namespace) -> int:
    if args.config_command == "path":
        data = service.config_path()
    elif args.config_command == "list":
        data = service.config_list(show_secrets=False)
    elif args.config_command == "set":
        data = service.config_set(args.key, args.value)
    elif args.config_command == "unset":
        data = service.config_unset(args.key)
    else:
        data = {"ok": False, "error_type": "parameter_error", "error": "Unknown config command"}
    return _print_result("config", data, args.format, args.output)

def _skill_targets_from_args(args: argparse.Namespace) -> list[str]:
    if getattr(args, "all", False):
        return [target.target_id for target in SKILL_TARGETS]
    raw = getattr(args, "targets", "") or ""
    if raw:
        return parse_skill_targets(raw)
    return list(DEFAULT_SKILL_TARGET_IDS)

def _run_skills(args: argparse.Namespace) -> int:
    try:
        target_ids = _skill_targets_from_args(args)
    except SkillInstallError as e:
        data = {"ok": False, "error_type": "parameter_error", "error": str(e), "selected": []}
        return _print_result("skills", data, args.format, args.output)

    if args.skills_command == "status":
        try:
            data = status_skill_targets(target_ids, project_root=args.skills_root)
        except SkillInstallError as e:
            data = {"ok": False, "error_type": "runtime_error", "error": str(e), "selected": target_ids}
        return _print_result("skills", data, args.format, args.output)

    if args.skills_command == "update":
        try:
            data = install_skill_targets(target_ids, project_root=args.skills_root)
        except SkillInstallError as e:
            data = {"ok": False, "error_type": "runtime_error", "error": str(e), "selected": target_ids}
        return _print_result("skills", data, args.format, args.output)

    data = {"ok": False, "error_type": "parameter_error", "error": "Unknown skills command", "selected": target_ids}
    return _print_result("skills", data, args.format, args.output)

def _run_setup(args: argparse.Namespace) -> int:
    try:
        explicit_skill_targets = parse_skill_targets(args.install_skills) if args.install_skills else []
    except SkillInstallError as e:
        data = {"ok": False, "error_type": "parameter_error", "error": str(e), "config_file": service.config_path()["config_file"]}
        return _print_result("setup", data, args.format, args.output)

    values = {
        "XAI_API_URL": args.xai_api_url,
        "XAI_API_KEY": args.xai_api_key,
        "XAI_MODEL": args.xai_model,
        "XAI_TOOLS": args.xai_tools_explicit,
        "OPENAI_COMPATIBLE_API_URL": args.openai_compatible_api_url,
        "OPENAI_COMPATIBLE_API_KEY": args.openai_compatible_api_key,
        "OPENAI_COMPATIBLE_MODEL": args.openai_compatible_model,
        "OPENAI_COMPATIBLE_FALLBACK_MODELS": args.openai_compatible_fallback_models,
        "OPENAI_COMPATIBLE_STREAM": args.openai_compatible_stream,
        "SMART_SEARCH_VALIDATION_LEVEL": args.validation_level,
        "SMART_SEARCH_FALLBACK_MODE": args.fallback_mode,
        "SMART_SEARCH_MINIMUM_PROFILE": args.minimum_profile,
        "SMART_SEARCH_INTENT_ROUTER": args.intent_router,
        "INTENT_EMBEDDING_API_URL": _normalize_custom_base_url(args.intent_embedding_api_url),
        "INTENT_EMBEDDING_API_KEY": args.intent_embedding_api_key,
        "INTENT_EMBEDDING_MODEL": args.intent_embedding_model,
        "INTENT_EMBEDDING_THRESHOLD": args.intent_embedding_threshold,
        "INTENT_EMBEDDING_MARGIN": args.intent_embedding_margin,
        "INTENT_CLASSIFIER_API_URL": _normalize_custom_base_url(args.intent_classifier_api_url),
        "INTENT_CLASSIFIER_API_KEY": args.intent_classifier_api_key,
        "INTENT_CLASSIFIER_MODEL": args.intent_classifier_model,
        "INTENT_ROUTER_TIMEOUT_SECONDS": args.intent_router_timeout,
        "EXA_API_KEY": args.exa_key,
        "CONTEXT7_API_KEY": args.context7_key,
        "ZHIPU_API_KEY": args.zhipu_key,
        "ZHIPU_API_URL": _normalize_zhipu_api_url(args.zhipu_api_url),
        "ZHIPU_SEARCH_ENGINE": args.zhipu_search_engine,
        "ZHIPU_MCP_API_KEY": args.zhipu_mcp_key,
        "ZHIPU_MCP_SEARCH_API_URL": _normalize_custom_base_url(args.zhipu_mcp_search_api_url),
        "ZHIPU_MCP_READER_API_URL": _normalize_custom_base_url(args.zhipu_mcp_reader_api_url),
        "ZHIPU_MCP_ZREAD_API_URL": _normalize_custom_base_url(args.zhipu_mcp_zread_api_url),
        "ZHIPU_MCP_TIMEOUT_SECONDS": args.zhipu_mcp_timeout,
        "JINA_API_KEY": args.jina_key,
        "JINA_READER_API_URL": _normalize_jina_reader_api_url(args.jina_reader_api_url),
        "JINA_RESPOND_WITH": args.jina_respond_with,
        "JINA_TIMEOUT_SECONDS": args.jina_timeout,
        "TAVILY_API_URL": _normalize_tavily_flag_api_url(args.tavily_api_url, args.tavily_key),
        "TAVILY_API_KEY": args.tavily_key,
        "FIRECRAWL_API_URL": _normalize_firecrawl_api_url(args.firecrawl_api_url),
        "FIRECRAWL_API_KEY": args.firecrawl_key,
        "ANYSEARCH_API_URL": _normalize_custom_base_url(args.anysearch_api_url),
        "ANYSEARCH_API_KEY": args.anysearch_key,
        "ANYSEARCH_TIMEOUT_SECONDS": args.anysearch_timeout,
    }

    lang = args.lang if args.lang in {"zh", "en"} else "zh"
    selected_skill_targets: list[str] = list(explicit_skill_targets)
    setup_warnings: list[str] = []
    current_for_setup: dict[str, str] = {}

    if not args.non_interactive:
        current_for_setup = service.config_list(show_secrets=True)["values"]
        _write_setup_banner(args.lang if args.lang in {"zh", "en"} else "zh")
        lang = _select_setup_language(args.lang)
        if args.advanced:
            _run_advanced_setup_prompts(values, current_for_setup, lang)
        else:
            skill_targets_for_prompt = selected_skill_targets if not args.skip_skills and not selected_skill_targets else None
            _run_guided_setup_prompts(values, current_for_setup, lang, skill_targets=skill_targets_for_prompt, show_banner=False)
        if _has_embedding_setup_values(values):
            setup_warnings.extend(_apply_embedding_setup_preset(values, current_for_setup, interactive=True, lang=lang))
    else:
        current_for_setup = service.config_list(show_secrets=True)["values"]
        if _has_embedding_setup_values(values):
            setup_warnings.extend(_apply_embedding_setup_preset(values, current_for_setup, interactive=False, lang=lang))

    saved: dict[str, str] = {}
    config_error: dict[str, Any] | None = None
    for key, value in values.items():
        if value:
            result = service.config_set(key, value)
            if not result.get("ok", False):
                config_error = result
                break
            saved[key] = result.get("value", "")

    if config_error is not None:
        data = {
            "ok": False,
            "error_type": config_error.get("error_type", "config_error"),
            "error": config_error.get("error", "配置保存失败。"),
            "config_file": config_error.get("config_file", ""),
            "saved": saved,
        }
        if setup_warnings:
            data["warnings"] = setup_warnings
        return _print_result("setup", data, args.format, args.output)

    skill_result = None
    if not args.skip_skills and selected_skill_targets:
        skill_result = install_skill_targets(selected_skill_targets, project_root=args.skills_root)

    ok = True if skill_result is None else bool(skill_result.get("ok", False))
    data = {"ok": ok, "config_file": service.config_path()["config_file"], "saved": saved}
    if setup_warnings:
        data["warnings"] = setup_warnings
    if skill_result is not None:
        data["skills"] = skill_result
        if not skill_result.get("ok", False):
            data["error_type"] = "runtime_error"
            data["error"] = "One or more skill targets failed to install."
    if not args.non_interactive:
        current_after = service.config_list(show_secrets=True)["values"]
        final_values = _merge_setup_values(current_after, values)
        final_status = _setup_status_from_values(final_values)
        configured_profile = final_values.get("SMART_SEARCH_MINIMUM_PROFILE") or "standard"
        minimum_result = _minimum_profile_result(configured_profile, final_status)
        _write_stderr(_t(lang, "\n保存完成。\n", "\nSaved.\n"))
        if skill_result is not None:
            _write_skill_install_summary(skill_result, lang)
        _write_setup_status(final_status, lang, final=True)
        missing = minimum_result.get("missing_required", [])
        if missing:
            _write_stderr(
                _t(
                    lang,
                    f"\n当前配置尚未满足 {configured_profile} 最低配置。\ndoctor 会报告 profile 缺口；普通命令仍按自身 required capability 校验。\n",
                    f"\nThe current config does not satisfy the {configured_profile} minimum profile.\ndoctor reports the profile gap; normal commands validate their own required capabilities.\n",
                )
            )
        else:
            _write_stderr(
                _t(
                    lang,
                    "\n下一步建议:\n  smart-search doctor --format json\n  smart-search smoke --mock --format json\n",
                    "\nNext steps:\n  smart-search doctor --format json\n  smart-search smoke --mock --format json\n",
                )
            )
        data["minimum_profile_ok"] = minimum_result.get("ok", False)
        data["minimum_profile_missing"] = missing
        data["capability_status"] = final_status
    return _print_result("setup", data, args.format, args.output)

def _run_regression() -> int:
    root = Path(__file__).resolve().parents[2]
    patterns = [
        "tests/test_cli.py",
        "tests/test_service.py",
        "tests/test_providers_new.py",
        "tests/test_jina_provider.py",
        "tests/test_zhipu_mcp_provider.py",
        "tests/test_smoke.py",
        "tests/test_intent_router.py",
        "tests/test_regression.py",
        "tests/test_release_workflow.py",
    ]
    if not all((root / pattern).exists() for pattern in patterns):
        print("Packaged install has no test files; running mock smoke regression instead.", file=sys.stderr)
        return asyncio.run(_run_regression_smoke_fallback())
    cmd = [sys.executable, "-m", "pytest", *patterns]
    return subprocess.call(cmd, cwd=str(root))

async def _run_regression_smoke_fallback() -> int:
    data = await service.smoke("mock")
    return _print_result("smoke", data, "json")

__all__ = [name for name in globals() if not name.startswith("__")]
