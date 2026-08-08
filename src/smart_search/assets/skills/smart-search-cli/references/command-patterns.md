# Command Patterns

## Table of Contents

- Evidence files
- Common commands
- Removed spellings and aliases
- Timeout retry policy
- Guardrails

## Evidence Files

For multi-source research, capture each command's stdout JSON for later
reference. The strict V2/V3/Workflow families do not define `--output` or
`--force`; they are rejected before any owner work. Save evidence with shell
redirection (for example `smart-search search "query" --format json >
evidence-01.json`) or let the host agent keep the parsed JSON in memory.

For claim-level evidence, prefer this order:

1. Discover candidate URLs with source-focused `search` (the generic command selects the configured `docs_search` / `web_search` providers by intent).
2. Fetch the exact pages that matter.
3. Use broad `search` only as synthesis or discovery, and mark claims as unverified when only discovery candidates are available.

The typed Deep Research plan is an offline artifact that carries only the
workflow plan: `schema_version` and an ordered `operations` list with
`id`, `operation`, `input`, `constraints`, and `depends_on`. It never embeds
shell commands, output paths, or an evidence directory; `--evidence-dir` and
`--output` are rejected by the workflow family. Evidence is persisted only
when the host agent saves the returned JSON.

## Common Commands

```powershell
smart-search search "query" --format json
smart-search search "query" --format content
smart-search search "query" --format markdown
smart-search fetch "https://example.com" --format markdown
smart-search map "https://docs.example.com" --format json
smart-search capabilities --format json
smart-search research plan "Compare two API designs" --budget standard --format json
smart-search research run "Compare two API designs" --budget deep --format json
smart-search config set XAI_API_KEY "value" --format json
smart-search config list --format json
smart-search config path --format json
smart-search config unset XAI_API_KEY --format json
smart-search dev skills status --targets codex --format json
smart-search dev skills update --targets codex --format json
smart-search dev skills update --all --format json
smart-search dev route-explain "React useEffect API docs" --format markdown
smart-search dev route-calibrate --models "Qwen/Qwen3-Embedding-8B" --format json
smart-search dev diagnose openai-compatible --format markdown
smart-search provider routes add --id primary --provider openai-compatible --api-url "https://relay-a.example/v1" --api-key "key-a" --model "model-a"
smart-search provider routes add --id backup --provider openai-compatible --api-url "https://relay-b.example/v1" --api-key "key-b" --model "model-b"
smart-search provider routes list --format markdown
smart-search provider routes current --format json
smart-search provider routes remove primary --format json
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search dev regression
smart-search dev smoke --mock --format json
smart-search dev smoke --mock --format markdown
smart-search --version
```

Canonical V2 evidence commands (`search`, `fetch`, `map`, `capabilities`)
accept only `--format json|markdown|content`; V1-era options such as
`--extra-sources`, `--timeout`, `--stream`, `--platform`, `--model`,
`--validation`, `--fallback`, `--providers`, `--profile`, `--response-mode`,
`--output`, and `--force` are rejected with the V2 strict INVALID_ARGUMENT
error before any provider work. `research plan` / `research run` accept only
`--budget` (and `--profile` for run); `--synthesize`, `--fallback`,
`--evidence-dir`, `--output`, and `--force` are rejected by the workflow
family. V3 leaves accept only their declared options plus `--format`; all of
them reject `--output`, `--force`, and prompt-file overrides.

## Removed Spellings And Aliases

All aliases are removed. Every removed spelling fails with the replacement
family's strict `INVALID_ARGUMENT` envelope that names the canonical
replacement; nothing is silently reinterpreted as a different command.

```powershell
# Removed aliases (each fails with the family error shown)
smart-search s "query"        # -> use search (V2)
smart-search f "https://example.com"   # -> use fetch (V2)
smart-search m "https://example.com"   # -> use map (V2)
smart-search rs "query"       # -> use research run (Workflow)
smart-search dr "query"       # -> use research plan (Workflow)
smart-search deep "query"     # -> use research plan (Workflow)
smart-search rt "query"       # -> use dev route-explain (V3)
smart-search cfg ls           # -> use config list (V3)
smart-search mdl cur          # -> use provider routes current (V3)
smart-search d                # -> use doctor probe (V3)
smart-search sm               # -> use dev smoke (V3)
smart-search reg              # -> use dev regression (V3)

# Removed legacy command spellings (each fails with the family error shown)
smart-search research "query"          # -> use research run (Workflow)
smart-search model list                # -> use provider routes list (V3)
smart-search smoke --mock --format json  # -> use dev smoke --mock --format json (V3)
smart-search doctor --format json      # -> use doctor probe (V3)
smart-search route "query"             # -> use dev route-explain (V3)
smart-search setup                     # -> use config set (V3)
smart-search skills status             # -> use dev skills status (V3)
```

The `--schema-version` selector is removed from the CLI surface entirely.
`--schema-version 1|2|3` (including `-schema-version`, equals syntax, and the
bare flag) fails with the identified command's family error, or with the V2
root parser-error sentinel when no command can be identified. Command domain
alone decides the contract: Evidence -> V2, Control Plane -> V3, Research
Workflow -> `research plan` / `research run`.

## Timeout Retry Policy

When `smart-search search` returns `ok: false` with `error_type: "network_error"` and an error message containing `timed out`, treat it as a retryable CLI-level timeout, not as a terminal research failure.

1. Retry up to 3 total attempts with the same canonical `smart-search search "query" --format json` command, waiting about 5 seconds between attempts.
2. Inspect the saved stdout JSON after each attempt and stop on the first `"ok": true`.
3. The canonical V2 search surface does not define `--timeout`, `--extra-sources`, or `--output`; those V1 options are rejected before any provider work. Do not rely on `SMART_SEARCH_RETRY_*` settings either; search timeouts are surfaced by the CLI result contract and should be handled by the agent workflow.
4. Do not wrap `smart-search` in a shell-level `timeout` command as the primary retry mechanism, because shell termination can prevent structured failure JSON.
5. If all attempts time out, fall back to source-first evidence:
   - Run a source-focused `search` with the original query (provider selection is internal).
   - `fetch` the top 1-2 relevant URLs before making claim-level statements.
   - Mark the final answer as `source_mode: "fallback"` or clearly state that the answer was assembled from fetched sources rather than generated by `search`.

Agent timeout handling contract: `smart-search search "query" --format json` is the retry shape; it is not a shell-level `timeout` wrapper and never uses `--timeout`/`--extra-sources`/`--output`. `SMART_SEARCH_RETRY_*` settings are not the contract for this path. After repeated timeout failures, switch to source-first fallback with source-focused `search` and fetched evidence. Final answers assembled through that fallback should explicitly label the evidence mode, for example `source_mode: "fallback"` or equivalent prose.

## Guardrails

- Prefer JSON for agent parsing and markdown for fetched page text intended for reading.
- Save stdout JSON for multi-source work, long pages, or anything the answer may need to cite later.
- Do not cite discovery candidates as proof for a claim; fetch the URL first (`smart-search fetch "URL" --format json`) or cite it only as a candidate source.
- Do not expose API keys. Treat `doctor` output as safe only because it is expected to mask secrets.
- In this CLI-first workflow, native `web_search` is disabled unless the user explicitly configures another approved route.
- If `doctor` or a command fails, report the failure and recovery steps; do not silently fall back to another web-search route.
- Do not use legacy MCP tool names in prompts, notes, or generated instructions for this workflow.
- Treat key rotation as a hard safety gate when previous key values were pasted into chat or logs.
