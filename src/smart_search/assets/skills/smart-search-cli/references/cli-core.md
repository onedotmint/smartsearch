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
- Private API keys should be saved with `smart-search config set`; environment variables remain supported for CI and advanced users.
- Do not depend on MCP inline `env` values or committed API-key environment variables for CLI use.
- On Windows with mise, the managed package name is `npm:@onedotmint/smart-search`; the executable remains `smart-search`. Troubleshoot mise managed installs with `mise ls "npm:@onedotmint/smart-search"` and `mise which smart-search`.
- The root `smart-search --help` lists only `search`, `fetch`, and `capabilities`. Run `smart-search --help-all` for the complete canonical inventory (V2 evidence, V3 control plane, and Research Workflow). Removed commands and aliases are never advertised and fail with the replacement family's strict error.

## Commands

- `smart-search search QUERY [--format json|markdown|content]` (V2 evidence envelope; V1 options `--platform`, `--model`, `--extra-sources`, `--profile`, `--response-mode`, `--validation`, `--fallback`, `--providers`, `--stream`/`--no-stream`, `--timeout`, `--output`, `--force`, and prompt-file overrides are rejected before owner work)
- `smart-search fetch URL [--format json|markdown|content]` (V2 evidence envelope)
- `smart-search map URL [--format json|markdown|content]` (V2 advanced)
- `smart-search capabilities [--format json|markdown|content]` (V2 local meta operation)
- `smart-search research plan QUERY [--budget quick|standard|deep] [--format json|markdown|content]` is the offline plan member of the workflow family (plan-only result, operation `research.run`).
- `smart-search research run QUERY [--budget quick|standard|deep] [--profile fast|balanced|deep] [--format json|markdown|content]` is the Agent-facing staged evidence workflow (operation `research.run`). Answer-generation flags are rejected; the host agent writes the final answer.
- `smart-search config path|list|set|unset ... [--format json|markdown|content]` (V3 control plane)
- `smart-search provider list|status [--format json|markdown|content]` (V3 local metadata/eligibility, no Provider client or probe)
- `smart-search provider probe PROVIDER [--format json|markdown|content]` (V3 explicit single-provider probe)
- `smart-search provider routes current|list|add|remove ...` (V3 ordered route management)
- `smart-search doctor status [--format json|markdown|content]` (V3 local readiness only, no Provider client or probe)
- `smart-search doctor probe [--format json|markdown|content]` (V3 explicit live aggregate diagnostic)
- `smart-search dev route-explain`, `dev route-calibrate`, `dev diagnose openai-compatible`, `dev smoke`, `dev regression`, `dev skills status`, `dev skills update` (V3 developer diagnostics; the bare non-dev spellings of these commands are removed)
- Agent default Core path: `smart-search capabilities|search|fetch` (JSON-default evidence-first envelope; JSON is the only stable machine contract).

## Aliases

All aliases are removed. Every alias spelling fails with the replacement family's strict `INVALID_ARGUMENT` error that names the canonical replacement.

## Output Format Expectations

- `--format json` is the stable machine-readable contract for agents and scripts. JSON output remains parseable and uses readable non-ASCII text when the terminal encoding supports it.
- Evidence commands emit the V2 envelope, control-plane commands emit the V3 envelope, and research plan/run emit the Workflow envelope. Each family rejects unknown fields and exposes exactly one structured error.
- The strict V2/V3/Workflow families reject `--output` and `--force` before any owner work.
- `--format markdown` is the human-readable report format. `dev route-explain --format markdown`, `dev route-calibrate --format markdown`, `doctor probe --format markdown`, and `dev diagnose openai-compatible --format markdown` must render useful reports rather than raw JSON dumps.
- `--format content` prints only the `content` field for content-bearing commands such as `search`, `fetch`, and `research`. Commands without a `content` field, including `dev route-explain`, `dev route-calibrate`, `doctor`, `dev smoke`, `config`, and `provider`, must print a compact non-empty text summary.
- Search, fetch, and map output uses the V2 envelope: `schema_version`, `ok`, `status`, `command`, `operation`, `result` (`total`, `items`), `evidence` (`candidates`, `items`, `citations`, `gaps`), `routing` (`requested_capabilities`, `executed_capabilities`, `policy_version`, `reason_codes`), `attempts` (`capability`, `provider`, `status`, `error_code`, `elapsed_ms`, `result_count`), `degradation`, `error`, and `meta`. Evidence candidates are `{id, resource, provider, title, snippet}` and admitted items are `{id, resource, provider, title, content}`.
- Route diagnostic output includes `query`, `executed_search`, `provider_selection`, `intent_router_mode`, `required_capabilities`, `intent_signals`, `confidence`, `router_engines_used`, `reasons`, `validation_level`, `missing_capabilities`, and `supplemental_paths` inside the V3 `result`. `smart-search dev route-explain` must not call search/docs/fetch providers.
- Route calibration output includes `metric`, `primary_metric=semantic_macro_f1`, `full_route_metric_role=validation`, `models`, `model_results`, `dataset_size`, `dataset_counts`, `capabilities`, `labels`, `embedding_model`, `default_threshold`, `default_margin`, `recommended_model`, `recommended_threshold`, `recommended_margin`, and `failed_models` inside the V3 `result`.
- Map output includes the V2 envelope with `result.total`/`result.items` ids plus site structure in `evidence.candidates`.
- `research plan` output is the plan-only Workflow result: `schema_version`, `ok`, `status`, `command`, `operation=research.run`, `plan`, `stages`, `evidence`, `citations`, `gaps`, `attempts`, `artifacts`, `error`, and `meta`. The `plan` member carries exactly `schema_version` and an ordered `operations` list (`id`, `operation`, `input`, `constraints`, `depends_on`); it never embeds shell commands, output paths, or an evidence directory. `research plan` runs no provider calls.
- `research run` output is the executed Workflow result with the same envelope: `plan`, `stages`, `evidence`, `citations`, `gaps`, `attempts`, and `artifacts` are populated, while no answer text exists (answer fields are not part of the workflow contract). The host agent writes the final prose from admitted evidence. Bare `research`, `rs`, `deep`, and `dr` are removed spellings and fail with the workflow family's strict error.
- Runtime caching is disabled by default. When enabled, only cleaned successful search/fetch results are cached in process memory; generated answers, errors, empty results, prompts, credentials, and research artifacts are excluded.
- `doctor status` result includes `local_only`, `config_file`, `config_dir`, `config_dir_source`, `config_status`, `config_storage_ok`, `config_parameter_errors`, `minimum_profile`, `minimum_profile_ok`, `minimum_profile_missing`, `minimum_profile_missing_required`, `core_evidence_path`, `core_evidence_ready`, `capability_status` (per-capability `configured`, `fallback_chain`, `provider_status` with provider eligibility), and `intent_router_status`. Diagnostic output masks keys and never prints secrets. OpenAI-compatible health must be validated through `/chat/completions`; `/models` is supplementary metadata.
- Smoke output includes `mode`, `case_count`, `cases`, `failed_cases`, `degraded_cases`, `providers_used`, and `fallback_used`. Live smoke may include `degraded_cases` when a provider fails but a same-capability fallback remains available.

## Exit Codes

- `0`: success
- `2`: parameter error
- `3`: configuration error
- `4`: network or upstream error, also used for strict insufficient-evidence search failures
- `5`: runtime or parse error

## Tool Policy

Web research through this skill should use `smart-search` CLI. If the CLI is unavailable, report the blocker and recovery steps instead of silently falling back to another web-search route.
