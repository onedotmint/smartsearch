# Command reference

The CLI accepts a command followed by a query, URL, or command-specific arguments. Routing is canonical command-domain based: evidence commands use the V2 envelope, retained control-plane commands use the V3 envelope, and `research plan` / `research run` use the Research Workflow envelope. The schema selector, legacy aliases, and removed commands fail with the replacement family's strict `INVALID_ARGUMENT` error and are never reinterpreted.

## Common output options

The strict V2/V3/Workflow families accept only `--format json|markdown|content`. The parser also recognizes these legacy options, which are rejected before any owner work:

| Option | Meaning |
| --- | --- |
| `--format json` | Versioned machine-readable output; the default for most commands |
| `--format markdown` | Human-readable report |
| `--format content` | Compact terminal content where supported |
| `--output PATH` | Write the rendered result to a file |
| `--force` | Replace an existing output file |
| `--prompt-dir PATH` | Load local UTF-8 prompt files |
| `--search-prompt-file PATH` | Override the search prompt for this call |
| `--fetch-prompt-file PATH` | Override the fetch prompt for this call |
| `--research-prompt-file PATH` | Override the research prompt for this call |

Remote prompt URLs are rejected. The rejected options never reach owner work; JSON remains the only stable machine contract.

## V2 evidence Core API

Root flags:

| Option | Meaning |
| --- | --- |
| `--fail-on-degraded` | v2 and v3 only: exit `6` for degraded envelopes without changing JSON. |

Supported V2 Core commands:

```sh
smart-search search QUERY
smart-search fetch URL
smart-search capabilities
```

Advanced V2 command:

```sh
smart-search map URL
```

Rules:

- V2 output defaults to JSON, the only stable machine contract. `--format markdown|content` selects one non-stable human presentation document of the same validated envelope.
- V2 `search` has no answer-mode field; any `--response-mode` fails before network I/O.
- V2 `search` is evidence-first discovery (candidates + routing/attempts). It never routes through legacy `main_search`.
- V2 `capabilities` uses envelope operation `capability_status` with empty evidence/attempts/routing capability arrays and no Provider calls.
- V2 rejects v1 command options (`--output`, `--force`, `--platform`, `--model`, `--extra-sources`, `--profile`, `--response-mode`, `--validation`, `--fallback`, `--providers`, `--stream`/`--no-stream`, `--timeout`, and prompt-file overrides) before configuration or Provider work.
- The schema selector is removed; the command domain alone decides the contract.

## Control-plane V3 JSON API

V3 is a separate JSON contract for stable control-plane operations. It is **not** an evidence envelope:

```sh
smart-search config list
smart-search provider status
smart-search doctor status
smart-search dev smoke --mock
```

V3 accepts these canonical namespace leaves only:

| Area | Leaves | Operation ids | Execution boundary |
| --- | --- | --- | --- |
| Config | `config path\|list\|set\|unset` | `config.*` | Local config read or atomic write |
| Provider catalog and routes | `provider list\|status\|probe`, `provider routes current\|list\|add\|remove` | `provider.catalog.*`, `provider.probe`, `provider.routes.*` | Local catalog/route reads and writes; probe is explicit single-provider network |
| Doctor | `doctor status\|probe` | `doctor.status`, `doctor.probe` | Local readiness or explicit aggregate probe |
| Developer | `dev route-explain`, `dev route-calibrate`, `dev diagnose openai-compatible`, `dev smoke`, `dev regression`, `dev skills status\|update` | `dev.*` | Configured router, explicit diagnostic, mock/live smoke, subprocess, or filesystem |

Every result has exactly these top-level fields:

```text
schema_version, ok, status, command, operation, result, network,
side_effects, error, meta
```

- `status` is `complete`, `degraded`, or `failed`. Empty successful lists and successful no-op removals are `complete`; `degraded` means the requested operation completed with observable partial outcomes; `failed` includes a structured error.
- `network` reports declared policy/scope and actual `attempted` state. `side_effects` separately reports config/filesystem reads and write attempt/commit state plus subprocess start. Do not infer I/O from status.
- V3 error codes are `INVALID_ARGUMENT`, `CONFIGURATION_ERROR`, `AUTHENTICATION_FAILED`, `UPSTREAM_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `FILE_SYSTEM_ERROR`, `SUBPROCESS_FAILED`, and `INTERNAL_ERROR`. `--fail-on-degraded` changes only the process exit to `6`.
- V3 output defaults to JSON, the only stable machine contract. `--format markdown|content` selects one non-stable human presentation document of the same validated envelope. V3 rejects `--output`, `--force`, prompt overrides, aliases, evidence commands, exact Provider direct commands, and all `experimental` leaves before an owner runs.
- Values, error details, URLs, and route credentials are recursively redacted. V3 does not expose v2 `evidence`, `routing`, or capability-attempt fields.

Removed legacy control spellings fail with the V3 family's strict `INVALID_ARGUMENT` envelope and name the canonical replacement.

## Research Workflow

```sh
smart-search research plan QUERY [--budget quick|standard|deep]
smart-search research run QUERY [--budget quick|standard|deep] [--profile fast|balanced|deep]
```

`research plan` builds the typed plan offline and returns a plan-only workflow result (operation `research.run`, empty execution collections). `research run` executes the staged evidence workflow: discovery, bounded fetches, evidence admission, citations, gaps, attempts, and logical artifact records. The host agent writes the final answer from admitted evidence.

Every result has exactly these top-level fields:

```text
schema_version, ok, status, command, operation, plan, stages,
evidence, citations, gaps, attempts, artifacts, error, meta
```

The workflow family forbids answer fields, answer-generation controls, shell commands, output paths, and raw Provider payloads. Bare `research`, `rs`, `deep`, and `dr` are removed spellings that fail with the workflow family's strict `INVALID_ARGUMENT` envelope.

## Command discovery

Root `smart-search --help` intentionally advertises only `search`, `fetch`, and `capabilities`. Run `smart-search --help-all` for the deterministic complete canonical inventory (V2 evidence, V3 control plane, and Research Workflow). Removed commands and aliases are never advertised.

## Core commands

| Command | Use |
| --- | --- |
| `search QUERY` | Fast live search and broad discovery (V2 evidence envelope) |
| `fetch URL` | Fetch one known URL (V2 evidence envelope) |
| `map URL` | Discover a site's structure (V2 advanced) |
| `capabilities` | Report configured capabilities and fallback metadata (V2 local) |
| `research plan QUERY` | Create an offline Deep Research plan (Workflow) |
| `research run QUERY` | Execute live staged evidence research (Workflow) |
| `config path\|list\|set\|unset` | Read or update local configuration (V3) |
| `provider list\|status\|probe` | Provider catalog and one explicit probe (V3) |
| `provider routes current\|list\|add\|remove` | Manage ordered main-search routes (V3) |
| `doctor status\|probe` | Local readiness or explicit live aggregate check (V3) |
| `dev route-explain`, `dev route-calibrate`, `dev diagnose openai-compatible`, `dev smoke`, `dev regression`, `dev skills status\|update` | Developer diagnostics (V3) |

### Search

```sh
smart-search search "OpenAI Responses API changes" --format json
smart-search search "query" --format markdown
smart-search search "query" --format content
```

`search` is the fast live entrypoint and accepts only `--format json|markdown|content` (plus the root `--fail-on-degraded` flag). V1-era options (`--validation`, `--fallback`, `--stream`, `--no-stream`, `--timeout`, `--extra-sources`, `--platform`, `--model`, `--providers`, `--profile`, `--response-mode`, `--output`, `--force`) are rejected before network I/O with the V2 strict `INVALID_ARGUMENT` error. V2 search returns discovery candidates and never writes an answer itself.

### Fetch and map

```sh
smart-search fetch "https://example.com/source" --format markdown
smart-search fetch "https://example.com/source" --full
smart-search map "https://docs.example.com" --format json
```

`fetch` is the page-level evidence boundary. Each fetched evidence item is bounded to 8,000 characters by default and always reports `truncated`, `original_length`, and `returned_length` so no truncation is silent; `fetch --full` bypasses the per-item cap and preserves the available full content. `map` returns site or documentation structure candidates and does not replace fetching the pages that support claims. `fetch` and `map` accept `--format json|markdown|content`; `map` additionally accepts `--instructions`, `--max-depth`, `--max-breadth`, and `--limit`. `--timeout`, `--output`, and `--force` are rejected before any owner work.

### Research Workflow

```sh
smart-search research plan "Deep research recent Bitcoin market movement" --budget standard --format json
smart-search research run "Deep research recent Bitcoin market movement" --budget deep --format json
```

`research plan` is the collision-safe offline planner. `research run` is the Agent-facing staged executor. Bare `research QUERY` is a removed legacy spelling; use `research run` instead.

### Config

```sh
smart-search config path --format json
smart-search config list --format json
smart-search config set XAI_API_KEY "value" --format json
smart-search config unset XAI_API_KEY --format json
```

Config reads and writes are local and atomic. Writes never expose raw values, and environment-controlled credentials are never copied into the file. Legacy main-search keys (`XAI_*`, `OPENAI_COMPATIBLE_*`) remain readable through the persisted-data upgrade readers.

### Provider catalog, probes, and routes

```sh
smart-search provider list --format json
smart-search provider status --format json
smart-search provider probe exa --format json
smart-search provider routes current --format json
smart-search provider routes list --format json
smart-search provider routes add --id primary --provider openai-compatible --api-url "https://relay-a.example/v1" --api-key "key-a" --model "model-a"
smart-search provider routes remove primary --format json
```

`provider probe PROVIDER` validates the id against the runtime registry, checks local eligibility first, and then runs only that provider's smallest supported connection operation. `provider list` and `provider status` are local-only metadata and eligibility views.

On the first local `provider routes add`, saved legacy `XAI_*` and `OPENAI_COMPATIBLE_*` main-search settings are retained as `legacy-xai-responses` and `legacy-openai-compatible` routes before the new route. This migration never copies environment-controlled legacy settings into the local file.

### Doctor and developer diagnostics

```sh
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search dev route-explain "React useEffect docs" --router-mode rules --format json
smart-search dev route-calibrate --models "a,b" --format json
smart-search dev diagnose openai-compatible --format markdown
smart-search dev smoke --mock --format json
smart-search dev regression
smart-search dev skills status --targets codex --format json
smart-search dev skills update --targets codex --format json
```

`doctor status` reports local configuration and evidence-path readiness (`local_only=true`). `doctor probe` is the live aggregate diagnostic. Bare non-dev spellings of these commands are removed; use `doctor probe` and `dev smoke`.

## Ordered model routes

Model routes are tried in the order shown by `provider routes list`. A failed timeout, network request, rate-limit, provider, parse, protocol, or empty-result attempt advances to the next route. Local configuration, parameter, and exhausted-budget errors stop the request.

Supported providers are `openai-compatible` and `xai-responses`. xAI routes may set `tools` to `web_search`, `x_search`, or both. OpenAI-compatible routes may set `stream` and same-endpoint `fallback_models`. `provider routes list`, `config list`, and `doctor` mask route API keys.

## Provider capability routing

Provider selection is internal to the generic `search`, `fetch`, and `map` commands; there are no provider-branded public commands. Configured `docs_search` (Context7/Exa), `web_search` (Zhipu REST/MCP, Tavily, Firecrawl), `web_fetch` (Tavily, Jina, Zhipu MCP Reader, Firecrawl), and `vertical_search` (AnySearch) providers are chosen by capability and intent. Exact-provider leaves and the `provider exa|context7|zhipu` / `experimental` namespaces are removed; their spellings fail with the strict family error before any provider call.

Provider configuration and capability boundaries are documented in [Providers](providers.md).

## Operational commands

```sh
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search capabilities --format json
smart-search config path --format json
smart-search config list --format json
smart-search provider routes list --format markdown
smart-search provider routes current --format json
smart-search dev skills status --targets codex --format json
smart-search dev smoke --mock --format json
smart-search dev regression
```

Use `doctor status` as preflight. It does not prove that every possible provider path will succeed. Use mock smoke and the developer quality gate for deterministic checks; live smoke requires intentional credentials and network access.
