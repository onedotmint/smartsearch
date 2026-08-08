# Setup And Config

## Table of Contents

- Config storage
- Doctor and diagnostics
- Setup workflow
- Skill installation sync
- Provider endpoint setup
- Intent router setup

## Config Storage

- Prefer the CLI's local config file managed with `smart-search config set`, `smart-search config path`, and `smart-search config list`.
- Environment variables remain supported for CI and advanced users, and override the local config file.
- The first local `smart-search provider routes add` preserves saved legacy `XAI_*` and `OPENAI_COMPATIBLE_*` settings as ordered routes before adding the new route. It never copies environment-controlled legacy values into the local config file; define `SMART_SEARCH_MODEL_ROUTES` in the environment for that setup.
- Do not ask users to set Windows global API-key environment variables by default.
- If keys are changed with `smart-search config set`, rerun the CLI; no Codex restart is needed.
- If PATH is changed, a new terminal or Codex restart may be needed.
- On Windows, the default local config file is `%LOCALAPPDATA%\smart-search\config.json`. Linux/macOS default to `~/.config/smart-search/config.json`.
- In sandboxed runtimes where the default config directory is not writable or must be pinned, set `SMART_SEARCH_CONFIG_DIR` to an absolute writable path. The CLI uses it for both config and relative logs and skips default-directory selection. It never falls back to a current-working-directory config path; `config path`, `config list`, `doctor`, `config set`, and `config unset` report `config_error` with this remediation when storage is unavailable.
- Earlier Windows source defaults used `~\.config\smart-search\config.json`, while some installs were already pinned to `%LOCALAPPDATA%\smart-search` through `SMART_SEARCH_CONFIG_DIR`. If the new default file is missing but the old file exists, `doctor` reports `legacy_windows_home` as the active source so upgrades do not silently lose configuration.
- The published `0.1.0` persisted-data upgrade contract is frozen in the repository migration guide (`docs/migration.md`): legacy main-search keys become ordered routes only on the first controlled local write, environment-owned values never land in the file, failed writes preserve the source bytes, and removed spellings fail without reinterpretation.
- When a Windows user reports different config paths, troubleshoot in this order: `config_dir_source`, `config_dir_override_value`, `config_dir_override_matches_default`, then `legacy_windows_config_exists`. Do not delete either config file or the user-level override until the upgraded CLI has been verified with `config path`, `doctor`, and the mock smoke quality gate.
- Runtime caching is disabled by default. Set `SMART_SEARCH_CACHE_ENABLED=true` to enable process-local cleaned source/content reuse; `SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS` defaults to `30`, `SMART_SEARCH_FETCH_CACHE_TTL_SECONDS` to `300`, and `SMART_SEARCH_CACHE_MAX_SIZE` to `256`. TTL values must be `1..604800`, and max size must be `1..10000`.
- Cache configuration changes and credential rotation prevent old entries from being used. The cache never stores generated answers, errors, empty results, prompts, credentials, or research artifacts.
- The research workflow records logical artifacts only and never projects output paths; `--evidence-dir` and `--output` are rejected by the workflow family before any owner work. Host agents persist evidence by saving the returned JSON.

## Doctor And Diagnostics

- Use `smart-search doctor status --format json` for local agent/script readiness checks. It makes no Provider request or probe. Use `smart-search doctor probe --format markdown` only when an explicit live aggregate diagnostic is needed.
- If `smart-search doctor status --format json` returns `ok: false`, follow the `error` field's guidance (`smart-search config set KEY VALUE`); do not silently fall back to native web search.
- `doctor probe --format markdown` must render a detailed diagnostic report with overall status, active/default/legacy config paths, log path resolution, file-logging status, masked config values with sources, minimum profile, capability status, main-search provider checks, provider connectivity checks, intent router status, embedding threshold/margin metadata, model metadata, and full long error/message detail.
- Use `smart-search dev diagnose openai-compatible --format markdown` when `doctor status` succeeds but OpenAI-compatible `search` appears to hang or returns a timeout. It is the beginner-facing one-command report for upstream/relay compatibility.
- `dev diagnose openai-compatible --format markdown` must render a short copy-pasteable troubleshooting report with masked config, quick chat check, real search-shape `stream=false` and `stream=true` checks, a plain-language summary, and a next command.

## Setup Workflow

- Legacy interactive `setup` is removed. Configure through `smart-search config set KEY VALUE` (see Provider Endpoint Setup) or environment variables; the grouped wizard no longer exists. `main_search`, `docs_search`, and `web_fetch` are the required capability groups; `web_search` is optional reinforcement, followed by optional smart intent router configuration.
- `smart-search dev skills update --targets codex,claude,cursor,hermes --format json` installs/syncs the bundled `smart-search-cli` skill into selected tool skill directories; there is no interactive setup wizard.
- - - - `SMART_SEARCH_MINIMUM_PROFILE` supports `lite`, `standard`, `full`, and `off`. `standard` keeps the fail-closed profile diagnostic for `main_search`, `docs_search`, and `web_fetch`; command execution is capability-scoped. Explicit `lite`/`off` evidence search permits source-only results from `web_search` or `docs_search`, while `fetch`, `map`, and `research` validate only their own required capabilities.
- Built-in search, fetch, and research Prompts are configured through their `SMART_SEARCH_*_PROMPT_FILE` environment/config keys. CLI prompt-file overrides (`--prompt-dir`, `--search-prompt-file`, `--fetch-prompt-file`, `--research-prompt-file`) are rejected by the strict V2/V3/Workflow families before any owner work. Remote Prompt URLs are rejected.
- Required groups are `main_search`, `docs_search`, and `web_fetch`; `web_search` is optional reinforcement, followed by optional smart intent router configuration.
- Unchecking a configured provider must not delete existing config values; use `smart-search config unset KEY` for deletion.

## Skill Installation Sync

- Skill installation installs the bundled `smart-search-cli` skill into selected AI-tool skill directories and must not run `trellis init`, create hooks, create agents, create commands, or modify other skills.
- Targets are user-level/global directories under the current user's home directory, for example Codex `~/.codex/skills/`, Claude Code `~/.claude/skills/`, Cursor `~/.cursor/skills/`, GitHub Copilot `~/.copilot/skills/`, and Hermes Agent `~/.hermes/skills/`.
- Skill targets are `codex`, `claude`, `cursor`, `opencode`, `copilot`, `gemini`, `kiro`, `qoder`, `codebuddy`, `droid`, `pi`, `kilo`, `antigravity`, `windsurf`, and `hermes`.
- `--skip-skills` disables skill installation.
- `--install-skills codex,claude,cursor,hermes` selects targets explicitly.
- `--skills-root PATH` is an advanced override for the user-level install root used in portable installs or tests. Normal users should omit it.
- `smart-search dev skills status --targets codex,claude,cursor,hermes --format json` compares bundled skill files with installed user-level skill directories. Status values are `missing`, `up_to_date`, `stale`, `extra_files`, and `error`. It reports target paths, bundled file count, installed file count, hashes, hash match flags, missing files, stale files, and extra files. It must not write or delete files.
- `smart-search dev skills update --targets codex,claude,cursor,hermes --format json` overwrites the managed bundled `smart-search-cli` files for selected targets. `smart-search dev skills update --all --format json` selects every target id.
- This daily sync path must not change provider keys, run setup prompts, create Trellis files, create hooks, create agents, create commands, or delete leftover files. Extra installed files are only reported by `dev skills status`.
- `dev skills update --targets codex` is the skill synchronization path; the legacy interactive setup wizard and its `--install-skills` flag are removed.

## Provider Endpoint Setup

- Setup and config output should include `ok` and `config_file`. Saved API keys must be masked in command output.
- Use `smart-search config set ZHIPU_API_URL "https://open.bigmodel.cn/api"` and `smart-search config set ZHIPU_SEARCH_ENGINE "search_std"` to save the Zhipu Web Search API endpoint and search service.
- Set Zhipu API key, API URL, and search service through `config set` when optional `web_search` reinforcement selects Zhipu.
- `config set ZHIPU_SEARCH_ENGINE VALUE` must remain free-form so newly added official services do not require a CLI release.
- `ZHIPU_API_URL` defaults to `https://open.bigmodel.cn/api`.
- `ZHIPU_SEARCH_ENGINE` defaults to `search_std`.
- Official Web Search API service values include `search_std`, `search_pro`, `search_pro_sogou`, and `search_pro_quark`.
- Use `smart-search config set JINA_API_KEY "key"` to let Jina satisfy `web_fetch`; `JINA_RESPOND_WITH=readerlm-v2` also requires `JINA_API_KEY`.
- Use `smart-search config set ZHIPU_MCP_API_KEY "key"` only when the user explicitly wants Coding Plan Remote MCP quota.
- Use `smart-search config set OPENAI_COMPATIBLE_STREAM "true"` only when an OpenAI-compatible relay benefits from SSE streaming for long requests. Default remains false.
- Use `smart-search config set OPENAI_COMPATIBLE_FALLBACK_MODELS "model-a,model-b"` to save ordered OpenAI-compatible backup models for primary model instability. `--fallback off` and `search --model MODEL` disable this model fallback for one invocation.
- Use `smart-search config set ANYSEARCH_API_URL "https://api.anysearch.com/mcp"` and `ANYSEARCH_API_KEY` only for experimental AnySearch acceptance; do not add it to the normal minimum-profile setup.
- `TAVILY_API_URL` defaults to `https://api.tavily.com` and only affects Tavily REST calls. It does not proxy Zhipu.
- `TAVILY_ENABLED` defaults to `true`. Set it to `false` to keep Tavily visible as disabled in diagnostics while excluding it from search, fetch, and map calls.
- Use `TAVILY_API_URL=https://<host>/api/tavily` for Tavily Hikari / pooled endpoints. Root host and `/mcp` inputs are normalized by `config set`; `/mcp` itself is not the REST base Smart Search should call.
- `TAVILY_TIMEOUT_SECONDS` controls the Tavily `doctor` connectivity timeout and defaults to `30`. Raise it for slower pooled/community Tavily endpoints before judging the provider unhealthy.
- `ANYSEARCH_API_URL` defaults to `https://api.anysearch.com/mcp`; `ANYSEARCH_TIMEOUT_SECONDS` defaults to `30`.
- `FIRECRAWL_API_URL` defaults to `https://api.firecrawl.dev/v2`. Use it only for a Firecrawl-compatible REST base.

## Intent Router Setup

- Set `SMART_SEARCH_INTENT_ROUTER`, `INTENT_EMBEDDING_*`, `INTENT_CLASSIFIER_*`, and `INTENT_ROUTER_TIMEOUT_SECONDS` through `config set` when optional smart intent routing is selected. Keep examples official or neutral and keep keys masked.
- `config set` can configure `SMART_SEARCH_INTENT_ROUTER`, `INTENT_EMBEDDING_*`, `INTENT_CLASSIFIER_*`, and `INTENT_ROUTER_TIMEOUT_SECONDS` directly.
- Recommended embeddings are SiliconFlow + `Qwen/Qwen3-Embedding-8B` with threshold `0.475` plus margin `0.053` when no explicit threshold/margin exists.
- Existing mismatched threshold/margin values should produce a warning rather than being silently overwritten.
