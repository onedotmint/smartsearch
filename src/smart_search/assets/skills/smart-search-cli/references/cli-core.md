# CLI Core

## Table of Contents

- Entrypoints
- Commands
- Aliases
- Output format expectations
- Exit codes
- Tool policy

## Entrypoints

- `smart-search` is the primary CLI and should resolve from the user's PATH.
- `smart-search --version`, `smart-search --v`, and `smart-search -v` print the installed version and exit with code `0`.
- This bundled skill is maintained with the `smartsearch` repository.
- Private API keys should be saved with `smart-search setup` or `smart-search config set`; environment variables remain supported for CI and advanced users.
- Do not depend on MCP inline `env` values or committed API-key environment variables for CLI use.
- On Windows with mise, the managed package name is `npm:@onedotmint/smart-search`; the executable remains `smart-search`. Diagnose mise managed installs with `mise ls "npm:@onedotmint/smart-search"` and `mise which smart-search`.
- The root `smart-search --help` lists only `search`, `fetch`, `capabilities`, and `setup`. Run `smart-search --help-all` for complete namespace and legacy discovery; provider-specific, diagnostic, calibration, smoke, regression, and model commands remain callable.

## Commands

- `smart-search search QUERY [--platform NAME] [--model ID] [--extra-sources N] [--profile fast|balanced|deep] [--response-mode evidence|concise|synthesized] [--validation fast|balanced|strict] [--fallback auto|off] [--providers auto|CSV] [--stream|--no-stream] [--timeout SECONDS] [--prompt-dir PATH] [--search-prompt-file PATH] [--fetch-prompt-file PATH] [--research-prompt-file PATH] [--format json|markdown|content] [--output PATH] [--force]`
- `smart-search route QUERY [--validation fast|balanced|strict] [--router-mode hybrid|rules|off] [--format json|markdown|content] [--output PATH]`
- `smart-search fetch URL [--prompt-dir PATH] [--search-prompt-file PATH] [--fetch-prompt-file PATH] [--research-prompt-file PATH] [--format json|markdown|content] [--output PATH] [--force]`
- `smart-search exa-search QUERY [--num-results N] [--search-type neural|keyword|auto] [--include-text] [--include-highlights] [--start-published-date YYYY-MM-DD] [--include-domains DOMAIN...] [--exclude-domains DOMAIN...] [--category NAME] [--format json|markdown|content] [--output PATH]`
- `smart-search exa-similar URL [--num-results N] [--format json|markdown|content] [--output PATH]`
- `smart-search zhipu-search QUERY [--count N] [--search-engine NAME] [--search-recency-filter VALUE] [--search-domain-filter DOMAIN] [--content-size medium|high] [--format json|markdown|content] [--output PATH]`
- `smart-search zhipu-mcp-search QUERY [--count N] [--format json|markdown|content] [--output PATH]`
- `smart-search zhipu-mcp-reader URL [--format json|markdown|content] [--output PATH]`
- `smart-search zhipu-mcp-search-doc REPO QUERY [--max-results N] [--format json|markdown|content] [--output PATH]`
- `smart-search zhipu-mcp-repo-structure REPO [--ref REF] [--format json|markdown|content] [--output PATH]`
- `smart-search zhipu-mcp-read-file REPO PATH [--ref REF] [--format json|markdown|content] [--output PATH]`
- `smart-search anysearch-domains [DOMAIN] [--format json|markdown|content] [--output PATH]`
- `smart-search anysearch-search QUERY [--domain DOMAIN] [--sub-domain SUB_DOMAIN] [--max-results N] [--format json|markdown|content] [--output PATH]`
- `smart-search anysearch-extract URL [--max-length N] [--format json|markdown|content] [--output PATH]`
- `smart-search anysearch-batch QUERY... [--max-results N] [--format json|markdown|content] [--output PATH]`
- `smart-search context7-library NAME [QUERY] [--format json|markdown|content] [--output PATH]`
- `smart-search context7-docs LIBRARY_ID QUERY [--format json|markdown|content] [--output PATH]`
- `smart-search deep QUERY [--budget quick|standard|deep] [--evidence-dir PATH] [--format json|markdown|content] [--output PATH]`
- `smart-search research QUERY [--budget quick|standard|deep] [--profile fast|balanced|deep] [--evidence-dir PATH] [--fallback auto|off] [--prompt-dir PATH] [--search-prompt-file PATH] [--fetch-prompt-file PATH] [--research-prompt-file PATH] [--format json|markdown|content] [--output PATH] [--force]`
- `smart-search capabilities [--format json|markdown|content] [--output PATH] [--force]`
- `smart-search route-calibrate [--models CSV] [--format json|markdown|content] [--output PATH]`
- `smart-search map URL [--instructions TEXT] [--max-depth N] [--max-breadth N] [--limit N] [--timeout SECONDS] [--format json|markdown|content] [--output PATH]`
- `smart-search doctor [--format json|markdown|content] [--output PATH]`
- `smart-search diagnose openai-compatible [--timeout SECONDS] [--format json|markdown] [--output PATH]`
- `smart-search setup [--lang zh|en] [--advanced] [--non-interactive] [--skip-skills] [--install-skills CSV] [--skills-root PATH] [--minimum-profile lite|standard|full|off] [--format json|markdown|content] [--output PATH]`
- `smart-search config path|list|set|unset ... [--format json|markdown|content] [--output PATH]`
- `smart-search model current|list [--format json|markdown|content] [--output PATH]`
- `smart-search model add --id ID [--provider xai-responses|openai-compatible] --api-url URL --api-key KEY --model MODEL [--tools CSV] [--fallback-models CSV] [--stream|--no-stream] [--format json|markdown|content] [--output PATH]`
- `smart-search model remove ID [--format json|markdown|content] [--output PATH]`
- `smart-search regression`
- `smart-search research plan QUERY [--budget quick|standard|deep] [--evidence-dir PATH] [--format json|markdown|content] [--output PATH]` is the namespace-compatible offline plan entry and projects as legacy `deep`.
- `smart-search research run QUERY [--budget quick|standard|deep] [--profile fast|balanced|deep] [--evidence-dir PATH] [--fallback auto|off] [--synthesize] [--format json|markdown|content] [--output PATH]` is the Agent-facing staged evidence executor (`command=research-run`). Default is evidence-only; `--synthesize` opts into evidence-only synthesis.
- `smart-search doctor probe [--format json|markdown|content] [--output PATH]` is the explicit live aggregate diagnostic and projects as legacy `doctor`.
- `smart-search doctor status [--format json|markdown|content] [--output PATH]` is local readiness only (`command=doctor-status`, no Provider client or probe).
- `smart-search provider list|status [--format json|markdown|content] [--output PATH]` is local-only metadata/eligibility inspection with no Provider client or probe.
- `smart-search provider probe PROVIDER [--format json|markdown|content] [--output PATH]` probes exactly one named provider/family (`command=provider-probe`).
- `smart-search provider routes current|list|add|remove ...`, `provider exa search|similar`, `provider context7 library|docs`, `provider zhipu search`, and `provider zhipu-mcp search|reader` preserve their matching legacy command behavior.
- `smart-search dev route-explain|route-calibrate|diagnose|smoke|regression|skills ...` and `smart-search experimental anysearch|zread ...` are namespace-compatible developer and explicit experimental entries with no new aliases.
- Agent default Core path: `smart-search --schema-version 2 capabilities|search|fetch` (JSON-only evidence-first envelope).

## Aliases

Top-level aliases normalize to the same service behavior as their full command: `search`/`s`, `route`/`rt`, `fetch`/`f`, `map`/`m`, `exa-search`/`exa`/`x`, `exa-similar`/`xs`, `zhipu-search`/`z`/`zp`, `zhipu-mcp-search`/`zmcp-search`, `zhipu-mcp-reader`/`zmcp-reader`, `zhipu-mcp-search-doc`/`zmcp-doc`, `zhipu-mcp-repo-structure`/`zmcp-tree`, `zhipu-mcp-read-file`/`zmcp-file`, `anysearch-domains`/`as-domains`, `anysearch-search`/`as-search`/`as`, `anysearch-extract`/`as-extract`, `anysearch-batch`/`as-batch`, `context7-library`/`c7`/`ctx7`, `context7-docs`/`c7d`/`c7docs`/`ctx7-docs`, `deep`/`dr`, `research`/`rs`, `route-calibrate`/`route-cal`/`rcal`, `doctor`/`d`, `diagnose`/`diag`, `setup`/`init`, `config`/`cfg`, `model`/`mdl`, `smoke`/`sm`, and `regression`/`reg`.

Nested aliases: `config path`/`cfg p`, `config list`/`cfg ls`/`cfg l`, `config set`/`cfg s`, `config unset`/`cfg rm`/`cfg u`, `model current`/`mdl cur`/`mdl c`, `model list`/`mdl ls`/`mdl l`, `model add`/`mdl a`, and `model remove`/`mdl rm`/`mdl r`.

## Output Format Expectations

- `--format json` is the stable machine-readable contract for agents and scripts. JSON output remains parseable and uses readable non-ASCII text when the terminal encoding supports it.
- JSON results add `schema_version: "1"`, `command`, `data`, and `meta` while retaining legacy flat fields. Failed results keep the legacy top-level `error` string and expose structured `data.error`, `error_detail`, and `error_code` values.
- `--output` never overwrites an existing file unless `--force` is supplied. Output files are written atomically with restrictive permissions where the platform supports them.
- `--format markdown` is the human-readable report format. `route --format markdown`, `route-calibrate --format markdown`, `doctor --format markdown`, and `diagnose openai-compatible --format markdown` must render useful reports rather than raw JSON dumps.
- `--format content` prints only the `content` field for content-bearing commands such as `search`, `fetch`, `context7-docs`, and `research`. Commands without a `content` field, including `route`, `route-calibrate`, `doctor`, `smoke`, `config`, and `model`, must print a compact non-empty text summary.
- Successful search output includes `ok`, `query`, `primary_api_mode`, `content`, `sources`, `sources_count`, `primary_sources`, `primary_sources_count`, `extra_sources`, `extra_sources_count`, `source_warning`, `routing_decision`, `providers_used`, `provider_attempts`, `fallback_used`, `validation_level`, `capability_execution_plan`, `evidence_bundle`, `discovery_candidates`, `fetched_evidence`, `evidence_items`, `citations`, `gaps`, `degraded`, `request_count`, `cache_hit`, `inflight_joined`, `remote_router_calls`, `retry_count`, `budget_exhausted`, `stage_elapsed_ms`, and `elapsed_ms`.
- Route diagnostic output includes `ok`, `query`, `executed_search=false`, `provider_selection=not_executed`, backward-compatible fields `docs_intent`, `zh_current_intent`, `web_current_intent`, `fetch_intent`, `supplemental_paths`, and unified intent-router fields `intent_router_mode`, `required_capabilities`, `intent_signals`, `confidence`, `router_engines_used`, `degraded`, `degraded_reason`, `reasons`, `embedding_model`, `embedding_threshold`, `embedding_margin`, `embedding_threshold_source`, and `embedding_margin_source`. `smart-search route` must not call search/docs/fetch providers.
- Route calibration output includes `ok`, `metric`, `primary_metric=semantic_macro_f1`, `full_route_metric_role=validation`, `models`, `model_results`, `dataset_size`, `dataset_counts`, `capabilities`, `recommended_model`, `recommended_threshold`, `recommended_margin`, and `failed_models`.
- Fetch output includes `ok`, `url`, `provider`, `content`, `provider_attempts`, `fallback_used`, `capability_execution_plan`, `evidence_bundle`, `discovery_candidates`, `fetched_evidence`, `evidence_items`, `citations`, `gaps`, `degraded`, `request_count`, `cache_hit`, `inflight_joined`, `remote_router_calls`, `retry_count`, `budget_exhausted`, `stage_elapsed_ms`, and `elapsed_ms`.
- Exa search output includes `ok`, `query`, `search_type`, `results`, `total`, and `elapsed_ms`. Exa similar output includes `ok`, `url`, `results`, `total`, and `elapsed_ms`.
- Zhipu search output includes `ok`, `query`, `provider`, `search_engine`, `results`, `total`, and `elapsed_ms`.
- Zhipu MCP command output includes `ok`, `provider`, `tool`, `elapsed_ms`, and either `content` for reader/file-like tools or `results` plus `total` for search-like tools.
- Context7 library output includes `ok`, `query`, `provider`, `results`, `total`, and `elapsed_ms`; Context7 docs output also includes `library_id`, `content`, and result metadata.
- Map output includes `ok`, `base_url`, `results`, `response_time`, `url`, and `elapsed_ms`.
- Deep planner output includes `ok`, `mode`, `query_mode`, `question`, `trigger_source`, `difficulty`, `intent_signals`, `decomposition`, `capability_plan`, `evidence_policy`, `preflight`, `steps`, `gap_check`, `final_answer_policy`, `usage_boundary`, `allowed_tools`, `evidence_dir`, and `elapsed_ms`.
- Research executor output includes `ok`, `mode=deep_research_execution`, `query_mode=research`, `question`, `budget`, `research_plan`, `capability_execution_plan`, `routing_decision`, `stage_results`, `discovery_sources`, `discovery_candidates`, `final_answer`, `content`, `citations`, `evidence_items`, `fetched_evidence`, `evidence_bundle`, `gap_check`, `gaps`, `provider_attempts`, `providers_used`, `fallback_used`, `degraded`, `synthesis_error`, `response_mode`, `synthesis_enabled`, `artifacts_persisted`, `route_policy_version`, `evidence_dir`, `minimum_profile_ok`, `capability_status`, `request_count`, `cache_hit`, `inflight_joined`, `remote_router_calls`, `retry_count`, `budget_exhausted`, `stage_elapsed_ms`, and `elapsed_ms`. `evidence_bundle` keeps discovery candidates separate from fetched/read evidence; synthesis consumes only the latter. `research run` defaults to `response_mode=evidence` with empty `final_answer`/`content`; bare `research` remains synthesized.
- Runtime caching is disabled by default. When enabled, only cleaned successful search/fetch results are cached in process memory; synthesis answers, errors, empty results, prompts, credentials, and research artifacts are excluded.
- Diagnostic output masks keys and reports config paths, Windows legacy config metadata, provider timeout values, `capability_status`, `minimum_profile_ok`, `intent_router_status`, `main_search_connection_tests`, and provider connectivity checks. OpenAI-compatible health must be validated through `/chat/completions`; `/models` is supplementary metadata.
- Smoke output includes `ok`, `mode`, `failed_cases`, `cases`, `provider_attempts`, and `elapsed_ms`. Live smoke may include `degraded_cases` when a provider fails but a same-capability fallback remains available.

## Exit Codes

- `0`: success
- `2`: parameter error
- `3`: configuration error
- `4`: network or upstream error, also used for strict insufficient-evidence search failures
- `5`: runtime or parse error

## Tool Policy

Web research through this skill should use `smart-search` CLI. If the CLI is unavailable, report the blocker and recovery steps instead of silently falling back to another web-search route.
