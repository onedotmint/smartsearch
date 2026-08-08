import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_SKILL_DIR = ROOT / "skills" / "smart-search-cli"
PACKAGED_SKILL_DIR = ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli"
PUBLIC_DOCS_DIR = ROOT / "docs"
LOCAL_TRELLIS_DIR = ROOT / ".trellis"


def test_regression_does_not_create_repo_log_file():
    log_dir = ROOT / "logs"
    if not log_dir.exists():
        return
    assert not list(log_dir.glob("smart_search_*.log"))


def test_smart_search_skill_contract_enforces_cli_first():
    skill_dir = Path.home() / ".codex" / "skills" / "smart-search-cli"
    if not skill_dir.exists():
        return
    skill_files = [
        p
        for p in skill_dir.rglob("*")
        if p.is_file() and p.suffix in {".md", ".yaml", ".yml"}
    ]
    if not skill_files:
        return

    text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in skill_files
    )

    forbidden_text = [
        "mcp__smart-search__",
        "get_sources",
        "get_config_info",
        "toggle_builtin_tools",
        "native web search fallback",
        "silently fallback",
    ]
    for phrase in forbidden_text:
        assert phrase not in text

    assert "native `web_search` is disabled" in text or "native web search is disabled" in text
    assert "do not silently fall back" in text


def _read_skill_tree(path: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(path.rglob("*"))
        if p.is_file() and p.suffix in {".md", ".yaml", ".yml"}
    )


def _read_reference_tree(path: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((path / "references").rglob("*"))
        if p.is_file() and p.suffix == ".md"
    )


def _skill_text_files(path: Path) -> dict[str, str]:
    return {
        p.relative_to(path).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(path.rglob("*"))
        if p.is_file() and p.suffix in {".md", ".yaml", ".yml"}
    }


def _read_public_docs() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(PUBLIC_DOCS_DIR.rglob("*.md"))
    )


def test_deep_research_skill_contract_public_and_packaged_assets_match():
    public_text = _read_skill_tree(PUBLIC_SKILL_DIR)
    packaged_text = _read_skill_tree(PACKAGED_SKILL_DIR)
    required_markers = [
        "Deep Research Mode",
        "深度搜索",
        "深度调研",
        "deep search",
        "deep research",
        "capability-based orchestration",
        "intent_signals",
        "gap_check",
        "fetch_before_claim",
        "smart-search dev skills status",
        "smart-search dev skills update",
        "Provider selection inside `search` is internal and intent-driven",
        "docs/API, Chinese/current, or official-domain routes are selected internally by capability",
        "smart-search research plan",
        "ordered `operations` list",
        "`depends_on` links must stay valid",
        "`doctor status` is a preflight action",
        "fixed topic recipe",
        "深度搜索一下最近的比特币行情",
        "records logical artifacts only and never projects output paths",
        "mock-full plus live-limited",
        "public planner entrypoint",
        "public live executor entrypoint",
        "not an executor",
        "does not change default `smart-search search`",
        "does not depend on an MCP session",
        "SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS",
        "provider advantage routing",
        "smart-search dev route-explain",
        "Intent Routing Diagnostics",
        "SMART_SEARCH_INTENT_ROUTER=hybrid|rules|off",
        "INTENT_EMBEDDING_API_URL",
        "INTENT_CLASSIFIER_API_URL",
        "required_capabilities",
        "Classifier output cannot select providers",
    ]
    for marker in required_markers:
        assert marker in public_text
        assert marker in packaged_text


def test_deep_research_cli_contract_documents_plan_and_smoke_matrix():
    public_contract = _read_reference_tree(PUBLIC_SKILL_DIR)
    packaged_contract = _read_reference_tree(PACKAGED_SKILL_DIR)
    required_markers = [
        "Deep Research Skill Contract",
        "`smart-search research plan \"question\"` is the public offline planner command",
        "`smart-search research run \"question\"` is the public live executor command",
        "must not change default `smart-search search` behavior",
        "`research plan` returns a plan-only Workflow result whose `plan` member is the\ntyped plan",
        "`operations` is an ordered executable plan",
        "Each operation has `id` (unique within the plan)",
        "The plan never contains shell commands, output paths, provider raw payloads",
        "`research plan` returns it with empty `stages`/`evidence`/`citations`/`gaps`/`attempts`/`artifacts`",
        "Default evidence policy is `fetch_before_claim`",
        "`doctor status` is a preflight action, not a planned operation",
        "must not require fixed topic recipe ids",
        "fixed topic recipe ids are not required schema",
        "Mock-full coverage should cover trigger phrases",
        "research provider advantage routing",
        "`research run` returns the strict workflow result",
        "Live-limited coverage should run `doctor status`, one broad `search`, and one `fetch`",
        "`smart-search dev skills status --targets codex,claude,cursor,hermes --format json`",
        "`smart-search dev skills update --targets codex,claude,cursor,hermes --format json`",
        "Status values are `missing`, `up_to_date`, `stale`, `extra_files`, and",
        "must not change provider keys, run setup",
        "`dev skills update --targets codex` is the skill synchronization path",
        "rerun the affected smoke until it passes or is proven to be an external provider blocker",
        "Budget limits must not break evidence policy",
        "Even `--budget quick` plans must retain at least one `content_fetch` operation",
        "The `depends_on` links must stay valid",
        "and the workflow records logical artifacts",
        "`smart-search dev route-explain` must not call search/docs/fetch providers",
        "Route diagnostic output includes",
        "`intent_router_mode`",
        "`required_capabilities`",
        "`SMART_SEARCH_INTENT_ROUTER` accepts `hybrid`, `rules`, and `off`",
        "`INTENT_EMBEDDING_API_URL`",
        "`INTENT_CLASSIFIER_API_URL`",
        "`INTENT_ROUTER_TIMEOUT_SECONDS` defaults to `8`",
        "`research plan` remains an offline planner",
    ]
    for marker in required_markers:
        assert marker in public_contract
        assert marker in packaged_contract


def test_search_timeout_retry_policy_is_distributable():
    public_text = _read_skill_tree(PUBLIC_SKILL_DIR)
    packaged_text = _read_skill_tree(PACKAGED_SKILL_DIR)
    public_contract = _read_reference_tree(PUBLIC_SKILL_DIR)
    packaged_contract = _read_reference_tree(PACKAGED_SKILL_DIR)

    skill_markers = [
        "Timeout Retry Policy",
        "error_type: \"network_error\"",
        "Retry up to 3 total attempts with the same canonical `smart-search search \"query\" --format json` command",
        "The canonical V2 search surface does not define `--timeout`, `--extra-sources`, or `--output`",
        "Do not wrap `smart-search` in a shell-level `timeout` command",
        "Do not rely on `SMART_SEARCH_RETRY_*` settings",
        "fall back to source-first evidence",
        "Run a source-focused `search` with the original query",
        "`fetch` the top 1-2 relevant URLs",
        "source_mode: \"fallback\"",
    ]
    contract_markers = [
        "Agent timeout handling contract",
        "`smart-search search \"query\" --format json` is the retry shape",
        "not a shell-level `timeout` wrapper and never uses `--timeout`/`--extra-sources`/`--output`",
        "`SMART_SEARCH_RETRY_*` settings are not the contract",
        "switch to source-first fallback",
        "source-focused `search`",
        "`source_mode: \"fallback\"`",
    ]

    for marker in skill_markers:
        assert marker in public_text
        assert marker in packaged_text
    for marker in contract_markers:
        assert marker in public_contract
        assert marker in packaged_contract


def test_public_docs_document_workflow_boundaries():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    public_docs = _read_public_docs()
    english_markers = [
        "Deep Research is not a fixed topic recipe system",
        "smart-search research",
        "`schema_version`",
        "provider advantage routing",
        "`intent_signals`",
        "ordered executable list",
        "`depends_on`",
        "`content_fetch`",
        "`smart-search research plan` is the public offline planner command",
        "smart-search research plan",
        "smart-search dev route-explain",
        "`intent_router_mode`",
        "`required_capabilities`",
        "degraded_reason",
        "Unsupported key claims must be fetched or downgraded to unverified candidates",
        "answer fields",
    ]
    for marker in english_markers:
        assert marker in public_docs

    assert "docs/concepts/search-vs-deep-vs-research.md" in readme
    assert "docs/concepts/evidence.md" in readme
    assert "docs/concepts/routing.md" in readme
    assert "docs/concepts/search-vs-deep-vs-research.md" in readme_zh
    assert "没有 fetch 的来源标为未验证候选" in readme_zh


def test_public_docs_structure_is_packaged_and_linked():
    required_paths = [
        "docs/getting-started.md",
        "docs/commands.md",
        "docs/migration.md",
        "docs/providers.md",
        "docs/concepts/search-vs-deep-vs-research.md",
        "docs/concepts/evidence.md",
        "docs/concepts/routing.md",
        "docs/development.md",
        "CONTRIBUTING.md",
    ]
    packaged_paths = [
        "docs/getting-started.md",
        "docs/commands.md",
        "docs/migration.md",
        "docs/providers.md",
        "docs/concepts/search-vs-deep-vs-research.md",
        "docs/concepts/evidence.md",
        "docs/concepts/routing.md",
    ]
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    package_files = package_json["files"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for relative_path in required_paths:
        assert (ROOT / relative_path).exists()
    for relative_path in packaged_paths:
        assert relative_path in package_files or (
            relative_path.startswith("docs/concepts/") and "docs/concepts/*.md" in package_files
        )
    assert "skills/smart-search-cli/**" not in package_files
    assert "CONTRIBUTING.md" not in package_files
    assert "docs/development.md" not in package_files
    assert "src/smart_search/assets/skills/smart-search-cli/**" in package_files
    assert "docs/getting-started.md" in readme
    assert "docs/getting-started.md" in readme_zh
    assert "docs/migration.md" in readme
    assert "docs/migration.md" in readme_zh
    assert "https://github.com/onedotmint/smartsearch/blob/main/docs/development.md" in readme
    assert "https://github.com/onedotmint/smartsearch/blob/main/docs/development.md" in readme_zh
    assert "](docs/development.md)" not in readme
    assert "](docs/development.md)" not in readme_zh


def test_local_trellis_contracts_keep_current_ownership_boundaries():
    spec_dir = LOCAL_TRELLIS_DIR / "spec"
    if not spec_dir.exists():
        return

    provider_contract = (spec_dir / "backend" / "provider-capability-contract.md").read_text(encoding="utf-8")
    workflow = (LOCAL_TRELLIS_DIR / "workflow.md").read_text(encoding="utf-8")
    config = (LOCAL_TRELLIS_DIR / "config.yaml").read_text(encoding="utf-8")
    local_architecture_dir = (
        ROOT / ".agents" / "skills" / "trellis-meta" / "references" / "local-architecture"
    )
    if not local_architecture_dir.exists():
        return
    local_architecture = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(local_architecture_dir.glob("*.md"))
    )
    current_specs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(spec_dir.rglob("*.md"))
    )

    assert "provider_commands.py" not in current_specs
    assert "_decode_provider_json" not in provider_contract
    assert "@konbakuyomu/smart-search" not in current_specs
    assert provider_contract.index("Context7 first") < provider_contract.index("Exa only after")
    assert ".trellis/spec/cli/" not in local_architecture
    # Generic Trellis documentation may name the OpenCode plugin, but this
    # repository owns a Python injector and must not vendor or register a JS hook.
    assert "inject-workflow-state.py" in workflow
    assert ".codex/hooks/inject-workflow-state.js" not in workflow
    assert "hooks/inject-workflow-state.js" not in workflow
    assert "session_auto_commit: false" in config


def test_readme_language_split_and_provider_links_are_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")
    provider_docs = (ROOT / "docs" / "providers.md").read_text(encoding="utf-8")

    assert "[简体中文](README.zh-CN.md) | English" in readme
    assert "简体中文 | [English](README.md)" in readme_zh
    assert "## 中文" not in readme
    assert "## English" not in readme
    assert "README.zh-CN.md" in package_json
    assert "docs/providers.md" in readme
    assert "docs/providers.md" in readme_zh

    provider_markers = [
        "https://docs.x.ai/docs",
        "https://console.x.ai/team/default/api-keys",
        "https://platform.openai.com/docs",
        "https://platform.openai.com/api-keys",
        "https://docs.exa.ai/",
        "https://dashboard.exa.ai/api-keys",
        "https://context7.com/docs",
        "https://docs.bigmodel.cn/cn/guide/tools/web-search",
        "https://open.bigmodel.cn/usercenter/apikeys",
        "https://docs.tavily.com/",
        "https://app.tavily.com/home",
        "https://docs.firecrawl.dev/",
        "https://www.firecrawl.dev/app/api-keys",
    ]
    for marker in provider_markers:
        assert marker in provider_docs


def test_deep_research_shared_skill_files_are_synchronized():
    assert _skill_text_files(PUBLIC_SKILL_DIR) == _skill_text_files(PACKAGED_SKILL_DIR)


def test_zhipu_setup_contract_public_and_packaged_assets_match():
    provider_docs = (ROOT / "docs" / "providers.md").read_text(encoding="utf-8")
    public_text = _read_skill_tree(PUBLIC_SKILL_DIR)
    packaged_text = _read_skill_tree(PACKAGED_SKILL_DIR)
    public_contract = _read_reference_tree(PUBLIC_SKILL_DIR)
    packaged_contract = _read_reference_tree(PACKAGED_SKILL_DIR)
    required_markers = [
        "ZHIPU_API_URL",
        "ZHIPU_SEARCH_ENGINE",
        "search_std",
        "search_pro",
        "search_pro_sogou",
        "search_pro_quark",
        "Web Search API",
        "TAVILY_API_URL",
        "does not proxy Zhipu",
        "not Zhipu Chat Completions",
        "not the MCP Server",
    ]
    for marker in required_markers:
        assert marker in provider_docs
        assert marker in public_text
        assert marker in packaged_text
    for marker in ["ZHIPU_API_URL", "ZHIPU_SEARCH_ENGINE"]:
        assert marker in provider_docs
        assert marker in public_contract
        assert marker in packaged_contract


def test_jina_and_zhipu_mcp_contract_public_and_packaged_assets_match():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    provider_docs = (ROOT / "docs" / "providers.md").read_text(encoding="utf-8")
    public_text = _read_skill_tree(PUBLIC_SKILL_DIR)
    packaged_text = _read_skill_tree(PACKAGED_SKILL_DIR)
    public_contract = _read_reference_tree(PUBLIC_SKILL_DIR)
    packaged_contract = _read_reference_tree(PACKAGED_SKILL_DIR)

    required_markers = [
        "JINA_API_KEY",
        "JINA_READER_API_URL",
        "JINA_RESPOND_WITH",
        "Jina Reader is `web_fetch` only",
        "Anonymous Jina Reader calls",
        "ZHIPU_MCP_API_KEY",
        "ZHIPU_MCP_SEARCH_API_URL",
        "ZHIPU_MCP_READER_API_URL",
        "ZHIPU_MCP_ZREAD_API_URL",
        "web_search_prime",
        "webReader",
        "search_doc",
        "get_repo_structure",
        "read_file",
        "Remote MCP",
        "Do not route it through the existing `/paas/v4/web_search`",
        "Coding Plan entitlement",
        "does not affect the standard minimum profile",
    ]
    for marker in required_markers:
        assert marker in provider_docs
        assert marker in public_text
        assert marker in packaged_text
        assert marker in public_contract
        assert marker in packaged_contract

    assert "docs/providers.md" in readme
    assert "docs/providers.md" in readme_zh


def test_streaming_and_anysearch_contract_public_and_packaged_assets_match():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    public_docs = _read_public_docs()
    public_text = _read_skill_tree(PUBLIC_SKILL_DIR)
    packaged_text = _read_skill_tree(PACKAGED_SKILL_DIR)
    public_contract = _read_reference_tree(PUBLIC_SKILL_DIR)
    packaged_contract = _read_reference_tree(PACKAGED_SKILL_DIR)

    required_markers = [
        "OPENAI_COMPATIBLE_STREAM",
        "--stream",
        "--no-stream",
        "ANYSEARCH_API_URL",
        "ANYSEARCH_API_KEY",
        "ANYSEARCH_TIMEOUT_SECONDS",
        "vertical_search",
        "not part of the `web_search` fallback",
        "not required by the `standard` minimum profile",
    ]
    for marker in required_markers:
        assert marker in public_docs
        assert marker in public_text
        assert marker in packaged_text
        assert marker in public_contract
        assert marker in packaged_contract

    assert "docs/providers.md" in readme
    assert "docs/providers.md" in readme_zh
