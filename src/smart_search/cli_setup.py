"""Interactive setup prompts and provider URL normalization."""

from .cli_support import *

def _config_value_source(key: str) -> str:
    getter = getattr(service.config, "get_config_source", None)
    if callable(getter):
        return str(getter(key))
    return "default"

def _current_config_value(key: str, current: dict[str, str]) -> str:
    value = str(current.get(key, "") or "")
    if value:
        return value
    if key == "INTENT_EMBEDDING_API_URL":
        return str(getattr(service.config, "intent_embedding_api_url", "") or "")
    if key == "INTENT_EMBEDDING_THRESHOLD":
        try:
            return str(getattr(service.config, "intent_embedding_threshold", "") or "")
        except ValueError:
            return ""
    if key == "INTENT_EMBEDDING_MARGIN":
        try:
            return str(getattr(service.config, "intent_embedding_margin", "") or "")
        except ValueError:
            return ""
    return ""

def _matches_float_text(value: str, expected: str) -> bool:
    try:
        return abs(float(str(value).strip()) - float(expected)) < 0.0005
    except (TypeError, ValueError):
        return False

def _apply_embedding_setup_preset(
    values: dict[str, str],
    current: dict[str, str],
    *,
    interactive: bool,
    lang: str,
) -> list[str]:
    warnings: list[str] = []
    merged = _merge_setup_values(current, values)
    model = merged.get("INTENT_EMBEDDING_MODEL", "")
    preset = embedding_preset_for_model(model)
    if not preset:
        return warnings

    for key, preset_value in (
        ("INTENT_EMBEDDING_API_URL", preset.api_url),
        ("INTENT_EMBEDDING_THRESHOLD", preset.threshold),
        ("INTENT_EMBEDDING_MARGIN", preset.margin),
    ):
        if values.get(key):
            continue
        source = _config_value_source(key)
        current_value = _current_config_value(key, current)
        if source == "default" or not current_value:
            values[key] = preset_value
            continue
        if key == "INTENT_EMBEDDING_API_URL":
            matches = current_value.rstrip("/") == preset_value
        else:
            matches = _matches_float_text(current_value, preset_value)
        if not matches:
            warning = (
                f"{key} is currently {current_value}; recommended for {preset.model} is {preset_value}."
            )
            warnings.append(warning)
            if interactive:
                _write_stderr(
                    _t(
                        lang,
                        f"提示: {warning}\n",
                        f"Note: {warning}\n",
                    )
                )
    return warnings

def _has_embedding_setup_values(values: dict[str, str]) -> bool:
    return any(
        bool(values.get(key))
        for key in (
            "INTENT_EMBEDDING_API_URL",
            "INTENT_EMBEDDING_API_KEY",
            "INTENT_EMBEDDING_MODEL",
            "INTENT_EMBEDDING_THRESHOLD",
            "INTENT_EMBEDDING_MARGIN",
        )
    )

def _setup_status_from_values(values: dict[str, str]) -> dict[str, Any]:
    def has(key: str) -> bool:
        return bool(values.get(key))

    main_configured: set[str] = set()
    if has("XAI_API_KEY"):
        main_configured.add("xai-responses")
    if has("OPENAI_COMPATIBLE_API_URL") and has("OPENAI_COMPATIBLE_API_KEY"):
        main_configured.add("openai-compatible")

    status = {
        "main_search": {
            "configured": [provider for provider in ("xai-responses", "openai-compatible") if provider in main_configured],
            "fallback_chain": ["xai-responses", "openai-compatible"],
        },
        "web_search": {
            "configured": [
                provider
                for provider, configured in [
                    ("zhipu", has("ZHIPU_API_KEY")),
                    ("zhipu-mcp", has("ZHIPU_MCP_API_KEY")),
                    ("tavily", has("TAVILY_API_KEY")),
                    ("firecrawl", has("FIRECRAWL_API_KEY")),
                ]
                if configured
            ],
            "fallback_chain": ["zhipu", "zhipu-mcp", "tavily", "firecrawl"],
        },
        "docs_search": {
            "configured": [
                provider
                for provider, configured in [
                    ("context7", has("CONTEXT7_API_KEY")),
                    ("exa", has("EXA_API_KEY")),
                ]
                if configured
            ],
            "fallback_chain": ["context7", "exa"],
        },
        "web_fetch": {
            "configured": [
                provider
                for provider, configured in [
                    ("tavily", has("TAVILY_API_KEY")),
                    ("jina", has("JINA_API_KEY")),
                    ("zhipu-mcp-reader", has("ZHIPU_MCP_API_KEY")),
                    ("firecrawl", has("FIRECRAWL_API_KEY")),
                ]
                if configured
            ],
            "fallback_chain": ["tavily", "jina", "zhipu-mcp-reader", "firecrawl"],
        },
        "vertical_search": {
            "configured": ["anysearch"] if has("ANYSEARCH_API_KEY") else [],
            "fallback_chain": ["anysearch"],
            "experimental": True,
        },
        "site_map": {
            "configured": ["tavily"] if has("TAVILY_API_KEY") else [],
            "fallback_chain": ["tavily"],
        },
    }
    for item in status.values():
        item["ok"] = bool(item["configured"])
    return status

def _merge_setup_values(current: dict[str, str], values: dict[str, str]) -> dict[str, str]:
    merged = dict(current)
    merged.update({key: value for key, value in values.items() if value})
    return merged

def _write_setup_status(status: dict[str, Any], lang: str, *, final: bool = False) -> None:
    title = _t(lang, "最低配置检查", "Minimum profile check") if final else _t(lang, "当前状态", "Current status")
    _write_stderr(f"\n{title}:\n")
    required = {"main_search", "docs_search", "web_fetch"}
    labels = {
        "main_search": _t(lang, "main_search 主搜索", "main_search primary search"),
        "docs_search": _t(lang, "docs_search 文档搜索", "docs_search documentation search"),
        "web_fetch": _t(lang, "web_fetch 网页抓取", "web_fetch page fetch"),
        "web_search": _t(lang, "web_search 网页补强", "web_search web reinforcement"),
        "vertical_search": _t(lang, "vertical_search 垂直搜索", "vertical_search vertical search"),
    }
    for capability in ("main_search", "docs_search", "web_fetch", "web_search", "vertical_search"):
        item = status.get(capability, {})
        configured = item.get("configured") or []
        configured_text = ", ".join(_display_provider(provider, lang) for provider in configured)
        if item.get("ok"):
            marker = "OK"
            value = configured_text
        elif capability in required:
            marker = "MISSING"
            value = _t(lang, "需要至少配置一个 provider", "at least one provider is required")
        else:
            marker = "OPTIONAL"
            value = _t(lang, "未配置", "not configured")
        _write_stderr(f"  [{marker}] {labels[capability]}: {value}\n")

def _prompt_choice(prompt: str, default: str = "") -> str:
    _write_stderr(prompt)
    value = input("").strip()
    return value or default

def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    default_text = "Y/n" if default else "y/N"
    answer = _prompt_choice(f"{prompt} [{default_text}]: ", "y" if default else "n").strip().lower()
    return answer in {"y", "yes", "是", "好", "1", "true"}

def _prompt_value(key: str, label: str, current: str = "", optional: bool = False, lang: str = "en") -> str:
    suffix = _t(lang, " 可选", " optional") if optional else _t(lang, " 必填", " required")
    current_display = (
        _t(lang, "已配置，回车保留", "configured, press Enter to keep")
        if current and (_is_secret_key(key) or _is_private_display_key(key))
        else current
    )
    if current:
        prompt = f"{label}{suffix} [{current_display}]: "
    else:
        prompt = f"{label}{suffix}: "
    if _is_secret_key(key):
        value = getpass.getpass(_stream_safe(sys.stderr, prompt)).strip()
    else:
        _write_stderr(prompt)
        value = input("").strip()
    return value or current

def _ascii_choice_values(choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**choice, "name": _stream_safe(sys.stderr, str(choice.get("name", "")))}
        for choice in choices
    ]

def _select_with_tui(message: str, choices: list[dict[str, Any]], default: Any = None) -> Any:
    if not _is_interactive_setup_stream():
        return None
    try:
        from InquirerPy import inquirer
    except Exception:
        return None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return inquirer.select(
                message=_stream_safe(sys.stderr, message),
                choices=_ascii_choice_values(choices),
                default=default,
                qmark="",
                pointer=">",
                marker=">",
            ).execute()
    except (KeyboardInterrupt, EOFError):
        raise
    except Exception:
        return None

def _checkbox_with_tui(message: str, choices: list[dict[str, Any]]) -> list[str] | None:
    if not _is_interactive_setup_stream():
        return None
    try:
        from InquirerPy import inquirer
    except Exception:
        return None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = inquirer.checkbox(
                message=_stream_safe(sys.stderr, message),
                choices=_ascii_choice_values(choices),
                instruction="(Up/Down move, Space select, Enter confirm)",
                qmark="",
                pointer=">",
                enabled_symbol="[x]",
                disabled_symbol="[ ]",
            ).execute()
        return [str(item) for item in result]
    except (KeyboardInterrupt, EOFError):
        raise
    except Exception:
        return None

def _provider_choices(providers: list[str], selected: list[str], lang: str) -> list[dict[str, Any]]:
    selected_set = set(selected)
    return [
        {"name": _display_provider(provider, lang), "value": provider, "enabled": provider in selected_set}
        for provider in providers
    ]

def _prompt_provider_multi_select(
    message: str,
    providers: list[str],
    default_selected: list[str],
    lang: str,
) -> list[str]:
    tui_value = _checkbox_with_tui(message, _provider_choices(providers, default_selected, lang))
    if tui_value is not None:
        return [provider for provider in providers if provider in set(tui_value)]

    default_text = ",".join(default_selected) if default_selected else "skip"
    _write_stderr(f"{message} [{'/'.join(providers)}/skip] ({default_text}): ")
    raw = input("").strip().lower()
    if not raw:
        return [provider for provider in providers if provider in set(default_selected)]
    aliases = {
        "跳过": "skip",
        "无": "skip",
        "n": "skip",
        "no": "skip",
        "否": "skip",
        "都配": "all",
        "全部": "all",
        "两个": "all",
        "both": "all",
        "all": "all",
        "xai": "xai-responses",
        "openai": "openai-compatible",
        "ctx7": "context7",
        "context": "context7",
    }
    tokens = [aliases.get(part.strip(), part.strip()) for part in raw.replace("+", ",").replace(";", ",").split(",")]
    if len(tokens) == 1 and " " in tokens[0]:
        tokens = [aliases.get(part.strip(), part.strip()) for part in tokens[0].split()]
    if "skip" in tokens or "none" in tokens:
        return []
    if "all" in tokens:
        return providers
    selected = [provider for provider in providers if provider in tokens]
    return selected if selected else [provider for provider in providers if provider in set(default_selected)]

def _prompt_select(message: str, choices: list[dict[str, Any]], default: str) -> str:
    tui_value = _select_with_tui(message, choices, default)
    if tui_value is not None:
        return str(tui_value)
    choice_values = [str(choice["value"]) for choice in choices]
    _write_stderr(f"{message} [{'/'.join(choice_values)}] ({default}): ")
    value = input("").strip().lower()
    return value if value in set(choice_values) else default

def _select_setup_language(lang: str = "") -> str:
    if lang in {"zh", "en"}:
        return lang
    choices = [
        {"name": "中文", "value": "zh"},
        {"name": "English", "value": "en"},
    ]
    answer = _prompt_select("Language / 语言", choices, "zh").strip().lower()
    if answer in {"en", "english"}:
        return "en"
    return "zh"

def _skill_target_choices(selected: list[str], lang: str) -> list[dict[str, Any]]:
    selected_set = set(selected)
    choices: list[dict[str, Any]] = []
    for target in SKILL_TARGETS:
        label = target.label
        name = f"{label} (~/{target.relative_root})"
        choices.append({"name": name, "value": target.target_id, "enabled": target.target_id in selected_set})
    return choices

def _prompt_skill_targets(lang: str) -> list[str]:
    _write_stderr(
        _t(
            lang,
            "\n[可选] 安装 smart-search-cli skill\n用途: 让本机全局 AI 工具知道优先调用 smart-search CLI。\n提示: 只安装 Smart Search skill；不会初始化 Trellis，也不会生成 hooks、agents 或 commands。\n",
            "\n[Optional] Install the smart-search-cli skill\nPurpose: teach user-level AI tools on this machine to call the smart-search CLI first.\nNote: this only installs the Smart Search skill; it does not initialize Trellis or generate hooks, agents, or commands.\n",
        )
    )
    tui_value = _checkbox_with_tui(
        _t(lang, "安装给哪些 AI 工具使用?", "Install for which AI tools?"),
        _skill_target_choices(DEFAULT_SKILL_TARGET_IDS, lang),
    )
    if tui_value is not None:
        return [target.target_id for target in SKILL_TARGETS if target.target_id in set(tui_value)]

    default_text = ",".join(DEFAULT_SKILL_TARGET_IDS)
    _write_stderr(
        _t(
            lang,
            f"安装 skill 目标 [codex,claude,cursor,.../all/skip] ({default_text}): ",
            f"Skill install targets [codex,claude,cursor,.../all/skip] ({default_text}): ",
        )
    )
    raw = input("").strip()
    if not raw:
        return list(DEFAULT_SKILL_TARGET_IDS)
    try:
        return parse_skill_targets(raw)
    except SkillInstallError as e:
        _write_stderr(f"{e}\n")
        return list(DEFAULT_SKILL_TARGET_IDS)

def _setup_choice(prompt: str, choices: set[str], default: str) -> str:
    value = _prompt_choice(prompt, default).strip().lower()
    aliases = {
        "保持": "keep",
        "跳过": "skip",
        "都配": "both",
        "两个": "both",
        "是": "yes",
        "否": "no",
    }
    value = aliases.get(value, value)
    return value if value in choices else default

def _prompt_main_search(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    status = _setup_status_from_values(_merge_setup_values(current, values))
    configured = status["main_search"]["configured"]
    default_selected = configured or ["xai-responses"]
    _write_stderr(
        _t(
            lang,
            "\n[1/3 标准档位] main_search（可选合成）\n用途: 兼容 v1 答案生成与显式 synthesis；evidence-first 默认路径不依赖它。\n推荐: 需要 legacy search 合成时再配 xAI 或 OpenAI-compatible。\n",
            "\n[1/3 Standard profile] main_search (optional synthesis)\nPurpose: legacy v1 answer generation and explicit synthesis; the evidence-first default path does not require it.\nRecommended: configure xAI or OpenAI-compatible when you need legacy search synthesis.\n",
        )
    )
    selected = _prompt_provider_multi_select(
        _t(
            lang,
            "选择 main_search provider",
            "Choose main_search providers",
        ),
        ["xai-responses", "openai-compatible"],
        default_selected,
        lang,
    )
    if "xai-responses" in selected:
        values["XAI_API_KEY"] = _prompt_value("XAI_API_KEY", "xAI API key", current.get("XAI_API_KEY", ""), lang=lang)
        values["XAI_MODEL"] = _prompt_value(
            "XAI_MODEL",
            _t(lang, "xAI Responses 模型", "xAI Responses model"),
            current.get("XAI_MODEL", ""),
            optional=True,
            lang=lang,
        )
    if "openai-compatible" in selected:
        values["OPENAI_COMPATIBLE_API_URL"] = _prompt_value(
            "OPENAI_COMPATIBLE_API_URL",
            _t(
                lang,
                "OpenAI-compatible API 地址（示例: https://api.openai.com/v1）",
                "OpenAI-compatible API URL (example: https://api.openai.com/v1)",
            ),
            current.get("OPENAI_COMPATIBLE_API_URL", ""),
            lang=lang,
        )
        values["OPENAI_COMPATIBLE_API_KEY"] = _prompt_value(
            "OPENAI_COMPATIBLE_API_KEY",
            "OpenAI-compatible API key",
            current.get("OPENAI_COMPATIBLE_API_KEY", ""),
            lang=lang,
        )
        values["OPENAI_COMPATIBLE_MODEL"] = _prompt_value(
            "OPENAI_COMPATIBLE_MODEL",
            _t(lang, "OpenAI-compatible 模型", "OpenAI-compatible model"),
            current.get("OPENAI_COMPATIBLE_MODEL", ""),
            optional=True,
            lang=lang,
        )
        values["OPENAI_COMPATIBLE_FALLBACK_MODELS"] = _prompt_value(
            "OPENAI_COMPATIBLE_FALLBACK_MODELS",
            _t(lang, "OpenAI-compatible 备用模型（逗号分隔，可留空）", "OpenAI-compatible fallback models (comma-separated, optional)"),
            current.get("OPENAI_COMPATIBLE_FALLBACK_MODELS", ""),
            optional=True,
            lang=lang,
        )
        stream_default = current.get("OPENAI_COMPATIBLE_STREAM", "")
        if _prompt_yes_no(
            _t(
                lang,
                f"是否启用 OpenAI-compatible stream=true？用于部分中转长请求兼容 [{stream_default or 'false'}]: ",
                f"Enable OpenAI-compatible stream=true for relay long-request compatibility [{stream_default or 'false'}]: ",
            ),
            default=(str(stream_default).lower() in {"true", "1", "yes"}),
        ):
            values["OPENAI_COMPATIBLE_STREAM"] = "true"
        elif stream_default:
            values["OPENAI_COMPATIBLE_STREAM"] = "false"

def _prompt_docs_search(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    status = _setup_status_from_values(_merge_setup_values(current, values))
    default_selected = status["docs_search"]["configured"] or ["context7"]
    _write_stderr(
        _t(
            lang,
            "\n[2/3 必选] docs_search 文档搜索\n用途: 查官方文档、SDK、API、框架和库说明。\n推荐: 文档/API/库优先 Context7；官方域名、论文和低噪声发现再配 Exa。\n",
            "\n[2/3 Required] docs_search documentation search\nPurpose: official docs, SDKs, APIs, frameworks, and library references.\nRecommended: Context7 for docs/API/library intent; Exa for official domains, papers, and low-noise discovery.\n",
        )
    )
    selected = _prompt_provider_multi_select(
        _t(
            lang,
            "选择 docs_search provider",
            "Choose docs_search providers",
        ),
        ["exa", "context7"],
        default_selected,
        lang,
    )
    if "exa" in selected:
        values["EXA_API_KEY"] = _prompt_value("EXA_API_KEY", "Exa API key", current.get("EXA_API_KEY", ""), lang=lang)
    if "context7" in selected:
        values["CONTEXT7_API_KEY"] = _prompt_value(
            "CONTEXT7_API_KEY",
            "Context7 API key",
            current.get("CONTEXT7_API_KEY", ""),
            lang=lang,
        )

def _prompt_tavily_api_url(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    current_url = current.get("TAVILY_API_URL", "")
    tavily_key = values.get("TAVILY_API_KEY") or current.get("TAVILY_API_KEY", "")
    if current_url:
        default_choice = "current"
    elif _is_tavily_hikari_key(tavily_key):
        default_choice = "hikari"
    else:
        default_choice = "official"
    choices = []
    if current_url:
        choices.append({"name": _t(lang, "保留当前地址（已配置）", "Keep current URL (configured)"), "value": "current"})
    choices.extend([
        {"name": _t(lang, "官方 Tavily (https://api.tavily.com)", "Official Tavily (https://api.tavily.com)"), "value": "official"},
        {"name": _t(lang, "Tavily Hikari / 号池", "Tavily Hikari / pooled endpoint"), "value": "hikari"},
        {"name": _t(lang, "自定义 Tavily REST base", "Custom Tavily REST base"), "value": "custom"},
    ])
    choice = _prompt_select(_t(lang, "选择 Tavily endpoint", "Choose Tavily endpoint"), choices, default_choice)
    if choice == "current":
        return
    if choice == "official":
        values["TAVILY_API_URL"] = TAVILY_DEFAULT_API_URL
        return
    if choice == "hikari":
        _write_stderr(
            _t(
                lang,
                "号池地址填服务商给你的域名或 URL，例如 https://pool.example.com 或 https://pool.example.com/mcp；setup 会保存为 https://pool.example.com/api/tavily。\n",
                "For pooled endpoints, paste the provider domain or URL, for example https://pool.example.com or https://pool.example.com/mcp; setup saves it as https://pool.example.com/api/tavily.\n",
            )
        )
    label = _t(
        lang,
        "Tavily REST 地址",
        "Tavily REST URL",
    )
    raw = _prompt_value("TAVILY_API_URL", label, current_url, optional=False, lang=lang)
    normalized = _normalize_tavily_api_url(raw) if choice == "hikari" else _normalize_tavily_api_url(raw, hikari=False)
    if normalized:
        values["TAVILY_API_URL"] = normalized
        if normalized != raw.rstrip("/"):
            _write_stderr(_t(lang, f"已规范化 Tavily REST base: {normalized}\n", f"Normalized Tavily REST base: {normalized}\n"))

def _prompt_firecrawl_api_url(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    current_url = current.get("FIRECRAWL_API_URL", "")
    choices = []
    if current_url:
        choices.append({"name": _t(lang, "保留当前地址（已配置）", "Keep current URL (configured)"), "value": "current"})
    choices.extend([
        {
            "name": _t(
                lang,
                "官方 Firecrawl (https://api.firecrawl.dev/v2)",
                "Official Firecrawl (https://api.firecrawl.dev/v2)",
            ),
            "value": "official",
        },
        {"name": _t(lang, "自定义 Firecrawl REST base", "Custom Firecrawl REST base"), "value": "custom"},
    ])
    default_choice = "current" if current_url else "official"
    choice = _prompt_select(_t(lang, "选择 Firecrawl endpoint", "Choose Firecrawl endpoint"), choices, default_choice)
    if choice == "current":
        return
    if choice == "official":
        values["FIRECRAWL_API_URL"] = FIRECRAWL_DEFAULT_API_URL
        return
    raw = _prompt_value(
        "FIRECRAWL_API_URL",
        _t(lang, "Firecrawl 自定义 REST base", "Firecrawl custom REST base"),
        current_url,
        optional=False,
        lang=lang,
    )
    normalized = _normalize_firecrawl_api_url(raw)
    if normalized:
        values["FIRECRAWL_API_URL"] = normalized

def _prompt_zhipu_api_url(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    current_url = current.get("ZHIPU_API_URL", "")
    choices = []
    if current_url:
        choices.append({"name": _t(lang, "保留当前地址（已配置）", "Keep current URL (configured)"), "value": "current"})
    choices.extend([
        {
            "name": _t(
                lang,
                "官方智谱 Web Search API (https://open.bigmodel.cn/api)",
                "Official Zhipu Web Search API (https://open.bigmodel.cn/api)",
            ),
            "value": "official",
        },
        {
            "name": _t(
                lang,
                "自定义智谱 API 地址",
                "Custom Zhipu API URL",
            ),
            "value": "custom",
        },
    ])
    default_choice = "current" if current_url else "official"
    choice = _prompt_select(_t(lang, "选择智谱 API 地址", "Choose Zhipu API URL"), choices, default_choice)
    if choice == "current":
        return
    if choice == "official":
        values["ZHIPU_API_URL"] = ZHIPU_DEFAULT_API_URL
        return
    raw = _prompt_value(
        "ZHIPU_API_URL",
        _t(lang, "智谱 API 地址", "Zhipu API URL"),
        current_url,
        optional=False,
        lang=lang,
    )
    normalized = _normalize_zhipu_api_url(raw)
    if normalized:
        values["ZHIPU_API_URL"] = normalized

def _prompt_zhipu_search_engine(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    current_engine = current.get("ZHIPU_SEARCH_ENGINE", "")
    choices = []
    if current_engine:
        choices.append(
            {
                "name": _t(
                    lang,
                    f"保留当前搜索服务（{current_engine}）",
                    f"Keep current search service ({current_engine})",
                ),
                "value": "current",
            }
        )
    choices.extend(
        {"name": engine, "value": engine}
        for engine in ZHIPU_SEARCH_ENGINE_CHOICES
    )
    choices.append({"name": _t(lang, "自定义搜索服务", "Custom search service"), "value": "custom"})
    default_choice = "current" if current_engine else "search_std"
    choice = _prompt_select(_t(lang, "选择智谱搜索服务", "Choose Zhipu search service"), choices, default_choice)
    if choice == "current":
        return
    if choice == "custom":
        raw = _prompt_value(
            "ZHIPU_SEARCH_ENGINE",
            _t(lang, "智谱搜索服务", "Zhipu search service"),
            current_engine,
            optional=False,
            lang=lang,
        )
        if raw:
            values["ZHIPU_SEARCH_ENGINE"] = raw.strip()
        return
    values["ZHIPU_SEARCH_ENGINE"] = choice

def _prompt_web_fetch(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    status = _setup_status_from_values(_merge_setup_values(current, values))
    default_selected = status["web_fetch"]["configured"] or ["tavily"]
    _write_stderr(
        _t(
            lang,
            "\n[3/3 必选] web_fetch 网页抓取\n用途: 已知 URL 抓正文；高风险事实核验必须用。\n推荐: Tavily 优先；Jina 需要 key 才算标准配置；Firecrawl 可作为抓取兜底。\n",
            "\n[3/3 Required] web_fetch page fetch\nPurpose: extract known URLs; required for high-risk fact checks.\nRecommended: Tavily first; Jina requires a key to satisfy standard config; Firecrawl as fetch fallback.\n",
        )
    )
    selected = _prompt_provider_multi_select(
        _t(
            lang,
            "选择 web_fetch provider",
            "Choose web_fetch providers",
        ),
        ["tavily", "jina", "firecrawl"],
        default_selected,
        lang,
    )
    if "tavily" in selected:
        values["TAVILY_API_KEY"] = _prompt_value("TAVILY_API_KEY", "Tavily API key", current.get("TAVILY_API_KEY", ""), lang=lang)
        _prompt_tavily_api_url(values, current, lang)
    if "jina" in selected:
        values["JINA_API_KEY"] = _prompt_value("JINA_API_KEY", "Jina API key", current.get("JINA_API_KEY", ""), lang=lang)
        raw_url = _prompt_value(
            "JINA_READER_API_URL",
            "Jina Reader API URL",
            current.get("JINA_READER_API_URL", "https://r.jina.ai"),
            optional=True,
            lang=lang,
        )
        values["JINA_READER_API_URL"] = _normalize_jina_reader_api_url(raw_url)
    if "firecrawl" in selected:
        values["FIRECRAWL_API_KEY"] = _prompt_value(
            "FIRECRAWL_API_KEY",
            "Firecrawl API key",
            current.get("FIRECRAWL_API_KEY", ""),
            lang=lang,
        )
        _prompt_firecrawl_api_url(values, current, lang)

def _prompt_optional_enhancements(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    _write_stderr(
        _t(
            lang,
            "\n[可选增强] web_search 网页补强\n用途: 中文、国内、时效、域名过滤类来源检索。\n推荐: 中文场景建议配置 Zhipu。\n",
            "\n[Optional] web_search web reinforcement\nPurpose: Chinese, domestic, current, or domain-filtered source discovery.\nRecommended: configure Zhipu for Chinese/current scenarios.\n",
        )
    )
    default_selected = ["zhipu"] if current.get("ZHIPU_API_KEY") else []
    selected = _prompt_provider_multi_select(
        _t(lang, "选择可选 web_search 增强", "Choose optional web_search reinforcement"),
        ["zhipu"],
        default_selected,
        lang,
    )
    if "zhipu" in selected:
        values["ZHIPU_API_KEY"] = _prompt_value("ZHIPU_API_KEY", "Zhipu API key", current.get("ZHIPU_API_KEY", ""), lang=lang)
        _prompt_zhipu_api_url(values, current, lang)
        _prompt_zhipu_search_engine(values, current, lang)
    if _prompt_yes_no(_t(lang, "是否调整验证/兜底默认值?", "Adjust validation/fallback defaults?"), default=False):
        values["SMART_SEARCH_VALIDATION_LEVEL"] = _prompt_value(
            "SMART_SEARCH_VALIDATION_LEVEL",
            _t(lang, "验证强度 (fast/balanced/strict)", "Validation level (fast/balanced/strict)"),
            current.get("SMART_SEARCH_VALIDATION_LEVEL", ""),
            optional=True,
            lang=lang,
        )
        values["SMART_SEARCH_FALLBACK_MODE"] = _prompt_value(
            "SMART_SEARCH_FALLBACK_MODE",
            _t(lang, "兜底模式 (auto/off)", "Fallback mode (auto/off)"),
            current.get("SMART_SEARCH_FALLBACK_MODE", ""),
            optional=True,
            lang=lang,
        )
        values["SMART_SEARCH_MINIMUM_PROFILE"] = _prompt_value(
            "SMART_SEARCH_MINIMUM_PROFILE",
            _t(lang, "最低配置门槛 (lite/standard/full/off)", "Minimum profile (lite/standard/full/off)"),
            current.get("SMART_SEARCH_MINIMUM_PROFILE", ""),
            optional=True,
            lang=lang,
        )

def _has_intent_router_config(values: dict[str, str]) -> bool:
    keys = {
        "SMART_SEARCH_INTENT_ROUTER",
        "INTENT_EMBEDDING_API_URL",
        "INTENT_EMBEDDING_API_KEY",
        "INTENT_EMBEDDING_MODEL",
        "INTENT_EMBEDDING_THRESHOLD",
        "INTENT_EMBEDDING_MARGIN",
        "INTENT_CLASSIFIER_API_URL",
        "INTENT_CLASSIFIER_API_KEY",
        "INTENT_CLASSIFIER_MODEL",
        "INTENT_ROUTER_TIMEOUT_SECONDS",
    }
    return any(bool(values.get(key)) for key in keys)

def _prompt_intent_router(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    merged = _merge_setup_values(current, values)
    _write_stderr(
        _t(
            lang,
            "\n[可选增强] 智能意图路由\n用途: 先判断问题需要 docs_search、web_search、web_fetch 还是 vertical_search，再进入同能力 provider 兜底。\n说明: rules 永远可本地兜底；hybrid 可额外配置 embeddings 语义路由和 classifier 结构化分类。\n",
            "\n[Optional] smart intent routing\nPurpose: decide whether a query needs docs_search, web_search, web_fetch, or vertical_search before same-capability provider fallback.\nNote: rules always remain the local fallback; hybrid can add semantic embeddings and structured classifier routing.\n",
        )
    )
    default_configure = _has_intent_router_config(merged)
    if not _prompt_yes_no(
        _t(lang, "是否配置/更新智能意图路由?", "Configure or update smart intent routing?"),
        default=default_configure,
    ):
        return

    mode_default = values.get("SMART_SEARCH_INTENT_ROUTER") or current.get("SMART_SEARCH_INTENT_ROUTER") or "hybrid"
    if mode_default not in {"hybrid", "rules", "off"}:
        mode_default = "hybrid"
    mode = _prompt_select(
        _t(lang, "选择 intent router 模式", "Choose intent router mode"),
        [
            {"name": _t(lang, "hybrid: 规则 + embeddings + classifier，缺配置自动降级 rules", "hybrid: rules + embeddings + classifier, degrading to rules when optional config is missing"), "value": "hybrid"},
            {"name": _t(lang, "rules: 只用本地规则", "rules: local rules only"), "value": "rules"},
            {"name": _t(lang, "off: 关闭额外意图路由", "off: disable additional intent routing"), "value": "off"},
        ],
        mode_default,
    )
    values["SMART_SEARCH_INTENT_ROUTER"] = mode
    if mode != "hybrid":
        return

    if _prompt_yes_no(
        _t(lang, "配置 embeddings 语义路由?", "Configure embeddings semantic routing?"),
        default=bool(merged.get("INTENT_EMBEDDING_API_URL") or merged.get("INTENT_EMBEDDING_API_KEY") or merged.get("INTENT_EMBEDDING_MODEL")),
    ):
        _write_stderr(
            _t(
                lang,
                "推荐 preset: SiliconFlow + Qwen/Qwen3-Embedding-8B；setup 会自动使用 threshold=0.475、margin=0.053。\n",
                "Recommended preset: SiliconFlow + Qwen/Qwen3-Embedding-8B; setup will use threshold=0.475 and margin=0.053 automatically.\n",
            )
        )
        raw_url = _prompt_value(
            "INTENT_EMBEDDING_API_URL",
            _t(
                lang,
                f"Embeddings API 地址（推荐: {QWEN3_EMBEDDING_8B_PRESET.api_url}）",
                f"Embeddings API URL (recommended: {QWEN3_EMBEDDING_8B_PRESET.api_url})",
            ),
            current.get("INTENT_EMBEDDING_API_URL", ""),
            optional=True,
            lang=lang,
        )
        values["INTENT_EMBEDDING_API_URL"] = _normalize_custom_base_url(raw_url)
        values["INTENT_EMBEDDING_API_KEY"] = _prompt_value(
            "INTENT_EMBEDDING_API_KEY",
            "Embeddings API key",
            current.get("INTENT_EMBEDDING_API_KEY", ""),
            optional=True,
            lang=lang,
        )
        values["INTENT_EMBEDDING_MODEL"] = _prompt_value(
            "INTENT_EMBEDDING_MODEL",
            _t(lang, "Embeddings 模型（推荐: Qwen/Qwen3-Embedding-8B）", "Embeddings model (recommended: Qwen/Qwen3-Embedding-8B)"),
            current.get("INTENT_EMBEDDING_MODEL", "") or QWEN3_EMBEDDING_8B_PRESET.model,
            optional=True,
            lang=lang,
        )

    if _prompt_yes_no(
        _t(lang, "配置 classifier 模型路由?", "Configure classifier model routing?"),
        default=bool(merged.get("INTENT_CLASSIFIER_API_URL") or merged.get("INTENT_CLASSIFIER_API_KEY") or merged.get("INTENT_CLASSIFIER_MODEL")),
    ):
        raw_url = _prompt_value(
            "INTENT_CLASSIFIER_API_URL",
            _t(
                lang,
                "Classifier API 地址（示例: https://api.openai.com/v1/chat/completions）",
                "Classifier API URL (example: https://api.openai.com/v1/chat/completions)",
            ),
            current.get("INTENT_CLASSIFIER_API_URL", ""),
            optional=True,
            lang=lang,
        )
        values["INTENT_CLASSIFIER_API_URL"] = _normalize_custom_base_url(raw_url)
        values["INTENT_CLASSIFIER_API_KEY"] = _prompt_value(
            "INTENT_CLASSIFIER_API_KEY",
            "Classifier API key",
            current.get("INTENT_CLASSIFIER_API_KEY", ""),
            optional=True,
            lang=lang,
        )
        values["INTENT_CLASSIFIER_MODEL"] = _prompt_value(
            "INTENT_CLASSIFIER_MODEL",
            _t(lang, "Classifier 模型", "Classifier model"),
            current.get("INTENT_CLASSIFIER_MODEL", ""),
            optional=True,
            lang=lang,
        )

    values["INTENT_ROUTER_TIMEOUT_SECONDS"] = _prompt_value(
        "INTENT_ROUTER_TIMEOUT_SECONDS",
        _t(lang, "Intent router 超时秒数", "Intent router timeout seconds"),
        current.get("INTENT_ROUTER_TIMEOUT_SECONDS", ""),
        optional=True,
        lang=lang,
    )

def _write_setup_keep_note(lang: str) -> None:
    _write_stderr(
        _t(
            lang,
            "\n提示: setup 不会删除旧配置；删除请运行 `smart-search config unset KEY`。\n",
            "\nNote: setup does not delete saved values; use `smart-search config unset KEY` to remove one.\n",
        )
    )

def _write_setup_examples(lang: str) -> None:
    _write_stderr(
        _t(
            lang,
            "\n不知道怎么填: 先配齐 evidence path（docs_search + web_search/source discovery + web_fetch）。\n"
            "  source/docs discovery: 文档/API 优先 Context7；通用网页发现配 Tavily/Zhipu；官方域名与论文可加 Exa。\n"
            "  web_fetch: Tavily 官方地址是 https://api.tavily.com；号池填 https://<host>/api/tavily；也可配 Jina。\n"
            "  main_search/synthesis: 可选，仅用于 v1 答案生成与 research --synthesize。\n"
            "  历史 standard 档位仍要求 main_search + docs_search + web_fetch。\n"
            "  intent embeddings: 推荐 SiliconFlow + Qwen/Qwen3-Embedding-8B，setup 会自动补 threshold=0.475、margin=0.053。\n",
            "\nIf unsure: first configure the evidence path (docs_search + web_search/source discovery + web_fetch).\n"
            "  source/docs discovery: Context7 for docs/API; Tavily/Zhipu for general discovery; Exa for official domains/papers.\n"
            "  web_fetch: official Tavily endpoint is https://api.tavily.com; pooled endpoints use https://<host>/api/tavily; Jina is also fine.\n"
            "  main_search/synthesis: optional for v1 answer generation and research --synthesize.\n"
            "  The historical standard profile still requires main_search + docs_search + web_fetch.\n"
            "  intent embeddings: recommended SiliconFlow + Qwen/Qwen3-Embedding-8B; setup auto-fills threshold=0.475 and margin=0.053.\n",
        )
    )

def _run_guided_setup_prompts(
    values: dict[str, str],
    current: dict[str, str],
    lang: str,
    *,
    skill_targets: list[str] | None = None,
    show_banner: bool = True,
) -> None:
    config_file = service.config_path()["config_file"]
    if show_banner:
        _write_setup_banner(lang)
    _write_panel(
        _t(
            lang,
            f"\nSmart Search 配置向导\n配置文件: {config_file}\n\n目标: evidence-first 工作流 + 兼容 standard 档位\n操作: 方向键移动，空格勾选，回车确认；API key 输入不显示。\n证据路径: source/docs discovery + content fetch；main_search 合成可选。\n历史 standard 档位仍报告 main_search + docs_search + web_fetch。\n",
            f"\nSmart Search setup wizard\nConfig file: {config_file}\n\nGoal: evidence-first workflow with standard profile compatibility\nKeys: move with arrow keys, select with Space, confirm with Enter; API key input is hidden.\nEvidence path: source/docs discovery + content fetch; main_search synthesis is optional.\nHistorical standard profile still reports main_search + docs_search + web_fetch.\n",
        ),
        lang,
    )
    _write_setup_keep_note(lang)
    _write_setup_examples(lang)
    _write_setup_status(_setup_status_from_values(_merge_setup_values(current, values)), lang)
    if skill_targets is not None:
        skill_targets[:] = _prompt_skill_targets(lang)
    _prompt_main_search(values, current, lang)
    _prompt_docs_search(values, current, lang)
    _prompt_web_fetch(values, current, lang)
    _prompt_optional_enhancements(values, current, lang)
    _prompt_intent_router(values, current, lang)

def _write_skill_install_summary(result: dict[str, Any], lang: str) -> None:
    if not result.get("selected"):
        _write_stderr(_t(lang, "\nSkill 安装: 已跳过。\n", "\nSkill install: skipped.\n"))
        return
    _write_stderr(
        _t(
            lang,
            f"\nSkill 安装结果: installed {result.get('installed_count', 0)}, skipped {result.get('skipped_count', 0)}, failed {result.get('failed_count', 0)}\n",
            f"\nSkill install result: installed {result.get('installed_count', 0)}, skipped {result.get('skipped_count', 0)}, failed {result.get('failed_count', 0)}\n",
        )
    )
    for item in result.get("installed", []):
        _write_stderr(f"  [OK] {item.get('label')} -> {item.get('path')}\n")
    for item in result.get("failed", []):
        _write_stderr(f"  [FAILED] {item.get('label')} -> {item.get('path')}: {item.get('error')}\n")

def _run_advanced_setup_prompts(values: dict[str, str], current: dict[str, str], lang: str) -> None:
    _write_stderr(
        _t(
            lang,
            "\n高级模式: 逐项配置底层键。一般用户建议直接使用默认分组向导。\n",
            "\nAdvanced mode: configure low-level keys one by one. Most users should use the grouped wizard.\n",
        )
    )
    prompts = [
        ("XAI_API_URL", "xAI Responses API URL", True),
        ("XAI_API_KEY", "xAI API key", True),
        ("XAI_MODEL", "xAI Responses model", True),
        ("XAI_TOOLS", "xAI Responses tools (web_search,x_search)", True),
        ("OPENAI_COMPATIBLE_API_URL", "OpenAI-compatible API URL", True),
        ("OPENAI_COMPATIBLE_API_KEY", "OpenAI-compatible API key", True),
        ("OPENAI_COMPATIBLE_MODEL", "OpenAI-compatible model", True),
        ("OPENAI_COMPATIBLE_FALLBACK_MODELS", "OpenAI-compatible fallback models (comma-separated)", True),
        ("OPENAI_COMPATIBLE_STREAM", "OpenAI-compatible stream mode (true/false)", True),
        ("SMART_SEARCH_VALIDATION_LEVEL", "Validation level (fast/balanced/strict)", True),
        ("SMART_SEARCH_FALLBACK_MODE", "Fallback mode (auto/off)", True),
        ("SMART_SEARCH_MINIMUM_PROFILE", "Minimum profile (lite/standard/full/off)", True),
        ("SMART_SEARCH_INTENT_ROUTER", "Intent router mode (hybrid/rules/off)", True),
        ("INTENT_EMBEDDING_API_URL", "Intent embedding API URL", True),
        ("INTENT_EMBEDDING_API_KEY", "Intent embedding API key", True),
        ("INTENT_EMBEDDING_MODEL", "Intent embedding model", True),
        ("INTENT_EMBEDDING_THRESHOLD", "Intent embedding threshold (0-1)", True),
        ("INTENT_EMBEDDING_MARGIN", "Intent embedding margin (0-1)", True),
        ("INTENT_CLASSIFIER_API_URL", "Intent classifier API URL", True),
        ("INTENT_CLASSIFIER_API_KEY", "Intent classifier API key", True),
        ("INTENT_CLASSIFIER_MODEL", "Intent classifier model", True),
        ("INTENT_ROUTER_TIMEOUT_SECONDS", "Intent router timeout seconds", True),
        ("EXA_API_KEY", "Exa API key", True),
        ("CONTEXT7_API_KEY", "Context7 API key", True),
        ("ZHIPU_API_KEY", "Zhipu API key", True),
        ("ZHIPU_API_URL", "Zhipu Web Search API URL", True),
        ("ZHIPU_SEARCH_ENGINE", "Zhipu search service (search_std/search_pro/search_pro_sogou/search_pro_quark/custom)", True),
        ("ZHIPU_MCP_API_KEY", "Zhipu Coding Plan MCP API key", True),
        ("ZHIPU_MCP_SEARCH_API_URL", "Zhipu Coding Plan search MCP URL", True),
        ("ZHIPU_MCP_READER_API_URL", "Zhipu Coding Plan reader MCP URL", True),
        ("ZHIPU_MCP_ZREAD_API_URL", "Zhipu Coding Plan zread MCP URL", True),
        ("ZHIPU_MCP_TIMEOUT_SECONDS", "Zhipu Coding Plan MCP timeout seconds", True),
        ("JINA_API_KEY", "Jina API key", True),
        ("JINA_READER_API_URL", "Jina Reader API URL", True),
        ("JINA_RESPOND_WITH", "Jina respond-with mode (optional, e.g. readerlm-v2)", True),
        ("JINA_TIMEOUT_SECONDS", "Jina timeout seconds", True),
        ("TAVILY_API_URL", "Tavily API URL", True),
        ("TAVILY_API_KEY", "Tavily API key", True),
        ("FIRECRAWL_API_URL", "Firecrawl API URL", True),
        ("FIRECRAWL_API_KEY", "Firecrawl API key", True),
        ("ANYSEARCH_API_URL", "AnySearch MCP API URL", True),
        ("ANYSEARCH_API_KEY", "AnySearch API key", True),
        ("ANYSEARCH_TIMEOUT_SECONDS", "AnySearch timeout seconds", True),
    ]
    for key, label, optional in prompts:
        if values[key]:
            continue
        value = _prompt_value(key, label, current.get(key, ""), optional=optional, lang=lang)
        if key == "TAVILY_API_URL":
            value = _normalize_tavily_api_url(value)
        elif key == "FIRECRAWL_API_URL":
            value = _normalize_firecrawl_api_url(value)
        elif key == "ZHIPU_API_URL":
            value = _normalize_zhipu_api_url(value)
        elif key == "JINA_READER_API_URL":
            value = _normalize_jina_reader_api_url(value)
        elif key in {"ZHIPU_MCP_SEARCH_API_URL", "ZHIPU_MCP_READER_API_URL", "ZHIPU_MCP_ZREAD_API_URL"}:
            value = _normalize_custom_base_url(value)
        values[key] = value

__all__ = [name for name in globals() if not name.startswith("__")]
