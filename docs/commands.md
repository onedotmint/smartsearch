# Command reference

The CLI accepts a command followed by a query, URL, or command-specific arguments. Command aliases are shown in the second column.

## Common output options

Most commands accept:

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

Remote prompt URLs are rejected. Local prompt overrides apply only to the current command.

## Opt-in schema version 2

Root-global flags (before the subcommand):

| Option | Meaning |
| --- | --- |
| `--schema-version 1\|2` | Select the result schema. Default remains `1`. |
| `--fail-on-degraded` | v2 only: exit `6` for degraded envelopes without changing JSON. |
| `--trace` | v2 only: attach redacted non-stable `meta.trace` events. |

Supported v2 Core commands:

```sh
smart-search --schema-version 2 search QUERY
smart-search --schema-version 2 fetch URL
smart-search --schema-version 2 capabilities
```

Advanced v2 command:

```sh
smart-search --schema-version 2 map URL
```

Rules:

- v2 output is JSON only (`--format markdown|content` is `INVALID_ARGUMENT`).
- v2 `search` has no `response_mode`; any `--response-mode` fails before network I/O.
- v2 `search` is evidence-first discovery (candidates + routing/attempts). It never routes through legacy `main_search`.
- v2 `capabilities` uses envelope operation `capability_status` with empty evidence/attempts/routing capability arrays and no Provider calls.
- Only the root-global flag placement is supported: `smart-search --schema-version 2 <command>`.
- Apart from an explicit `--format json`, v2 rejects v1 command options before configuration or Provider work; `map` remains Advanced and accepts only its URL in this release.
- Existing v1 command semantics, JSON wrapper, and Skill workflows are unchanged.

## Command discovery

Root `smart-search --help` intentionally advertises only `search`, `fetch`, `capabilities`, and `setup`. `map` is Advanced; provider, developer, experimental, and legacy-compatible entries remain callable but are not root-help commands. Run `smart-search --help-all` for the deterministic complete command inventory.

## Core commands

| Command | Alias | Use |
| --- | --- | --- |
| `search QUERY` | `s` | Fast live search and broad synthesis |
| `route QUERY` | `rt` | Explain required capabilities without running search/fetch providers |
| `fetch URL` | `f` | Fetch one known URL |
| `map URL` | `m` | Discover a site's structure |
| `deep QUERY` | `dr` | Create an offline Deep Research plan |
| `research QUERY` | `rs` | Execute live staged research |
| `doctor` | `d` | Show masked configuration and connection checks |
| `diagnose openai-compatible` | `diag` | Diagnose OpenAI-compatible search hangs and timeouts |
| `capabilities` | - | Report configured capabilities and fallback metadata |
| `setup` | `init` | Save local provider configuration and optionally install skills |
| `config` | `cfg` | Read or update local configuration |
| `model` | `mdl` | Manage ordered main-search model routes |
| `skills` | `skill` | Inspect or update installed managed skill files |
| `smoke` | `sm` | Run provider routing smoke checks |
| `regression` | `reg` | Run offline CLI regression checks |
| `route-calibrate` | `route-cal`, `rcal` | Evaluate embedding router thresholds |

### Search

```sh
smart-search search "OpenAI Responses API changes" --format json
smart-search search "query" --validation balanced --extra-sources 3 --timeout 90 --format json --output result.json
smart-search search "query" --response-mode evidence --format markdown
smart-search search "query" --stream --format json
smart-search search "query" --no-stream --format json
```

`search` is the fast live entrypoint. `--response-mode` accepts `evidence`, `concise`, or `synthesized`; `--validation` accepts `fast`, `balanced`, or `strict`. `--fallback` accepts `auto` or `off`.

### Route

```sh
smart-search route "React useEffect API docs" --router-mode rules --format markdown
smart-search route "verify this URL https://example.com/source" --router-mode rules --format json
```

`--router-mode` accepts `hybrid`, `rules`, or `off`. Route does not run search, docs, fetch, or vertical provider calls. In `hybrid`, configured embedding and classifier endpoints may be called; use `rules` for a local-only diagnostic.

### Fetch and map

```sh
smart-search fetch "https://example.com/source" --format markdown --output evidence.md
smart-search map "https://docs.example.com" --instructions "Find API reference pages" --max-depth 1 --limit 50 --format json
```

`fetch` is the page-level evidence boundary. `map` returns site or documentation structure candidates and does not replace fetching the pages that support claims.

### Deep Research

```sh
smart-search deep "Deep research recent Bitcoin market movement" --budget standard --format json
smart-search research "Deep research recent Bitcoin market movement" --budget deep --fallback auto --format markdown
```

`deep --budget` accepts `quick`, `standard`, or `deep` and remains offline. `research --budget` accepts the same values and runs the live executor. `research --fallback auto` permits same-capability fallback; `--fallback off` uses only the first eligible provider inside each capability.

### Compatibility namespaces

Namespace leaves preserve the existing v1 renderer, JSON envelope, redaction, file-output behavior, and exit codes. Their JSON `command` is the corresponding legacy canonical command so scripts retain the same compatibility projection. Namespace names have no aliases; legacy commands and aliases remain supported.

| Namespace path | Legacy-compatible handler | Network behavior |
| --- | --- | --- |
| `research plan QUERY` | `deep` | Offline planning |
| `doctor probe` | `doctor` | Live aggregate diagnostic |
| `provider list` / `provider status` | Local provider catalog | Local only; no provider client or probe |
| `provider routes current\|list\|add\|remove` | `model` | Local config |
| `provider exa search\|similar` | `exa-search` / `exa-similar` | Exact Exa operation |
| `provider context7 library\|docs` | Context7 commands | Exact Context7 operation |
| `provider zhipu search` / `provider zhipu-mcp search\|reader` | Zhipu direct commands | Exact provider operation |
| `dev route-explain`, `dev route-calibrate`, `dev diagnose openai-compatible`, `dev smoke`, `dev regression`, `dev skills status\|update` | Matching legacy command | Diagnostic, local, or explicit network behavior |
| `experimental anysearch ...` / `experimental zread ...` | Matching explicit provider command | Experimental exact-provider operation |

Examples:

```sh
smart-search research plan "Compare two current API designs" --budget standard --format json
smart-search doctor probe --format markdown
smart-search provider list --format json
smart-search provider status --format json
smart-search provider routes current --format json
smart-search provider exa search "OpenAI Responses API documentation" --num-results 5 --format json
smart-search dev route-explain "React useEffect docs" --router-mode rules --format json
smart-search experimental anysearch search "CVE-2024-3094" --domain security.cve --format json
```

`research run`, `doctor status`, and `provider probe` are intentionally unavailable in this release. They require dedicated owners and are not aliases for existing live workflows.

## Ordered model routes

Model routes are tried in the order shown by `model list`. A failed timeout, network request, rate-limit, provider, parse, protocol, or empty-result attempt advances to the next route. Local configuration, parameter, and exhausted-budget errors stop the request.

Use the CLI to append a route without editing JSON:

```sh
smart-search model add --id primary --provider openai-compatible --api-url "https://relay-a.example/v1" --api-key "key-a" --model "model-a"
smart-search model add --id backup --provider openai-compatible --api-url "https://relay-b.example/v1" --api-key "key-b" --model "model-b" --stream
smart-search model list --format markdown
smart-search model current --format json
smart-search model remove backup
```

The same list can be edited directly in the local file reported by `smart-search config path`:

```json
{
  "SMART_SEARCH_MODEL_ROUTES": [
    {
      "id": "primary",
      "provider": "openai-compatible",
      "api_url": "https://relay-a.example/v1",
      "api_key": "key-a",
      "model": "model-a"
    },
    {
      "id": "backup",
      "provider": "openai-compatible",
      "api_url": "https://relay-b.example/v1",
      "api_key": "key-b",
      "model": "model-b",
      "stream": true
    }
  ]
}
```

Supported providers are `openai-compatible` and `xai-responses`. xAI routes may set `tools` to `web_search`, `x_search`, or both. OpenAI-compatible routes may set `stream` and same-endpoint `fallback_models`. `model list`, `model current`, `config list`, and `doctor` mask route API keys.

On the first local `model add`, saved legacy `XAI_*` and `OPENAI_COMPATIBLE_*` main-search settings are retained as `legacy-xai-responses` and `legacy-openai-compatible` routes before the new route. This migration never copies environment-controlled legacy settings into the local file. When a legacy provider is controlled by the environment, define the complete `SMART_SEARCH_MODEL_ROUTES` array in the environment instead.

## Provider-specific commands

These commands are explicit tools for focused discovery or extraction. They are not interchangeable fallback providers.

| Command | Alias | Purpose |
| --- | --- | --- |
| `exa-search` | `exa`, `x` | Exa source-first search |
| `exa-similar` | `xs` | Find pages similar to a URL with Exa |
| `zhipu-search` | `z`, `zp` | Zhipu Web Search API |
| `zhipu-mcp-search` | `zmcp-search` | Zhipu Coding Plan MCP `web_search_prime` |
| `zhipu-mcp-reader` | `zmcp-reader` | Zhipu Coding Plan MCP `webReader` |
| `zhipu-mcp-search-doc` | `zmcp-doc` | Search repository docs through zread MCP |
| `zhipu-mcp-repo-structure` | `zmcp-tree` | Read repository structure through zread MCP |
| `zhipu-mcp-read-file` | `zmcp-file` | Read one repository file through zread MCP |
| `context7-library` | `c7`, `ctx7` | Resolve Context7 library candidates |
| `context7-docs` | `c7d`, `c7docs`, `ctx7-docs` | Fetch Context7 docs |
| `anysearch-domains` | `as-domains` | List experimental AnySearch domains |
| `anysearch-search` | `as-search`, `as` | Experimental vertical or general search |
| `anysearch-extract` | `as-extract` | Extract a URL through AnySearch |
| `anysearch-batch` | `as-batch` | Run up to five AnySearch queries in parallel |

Examples:

```sh
smart-search exa-search "OpenAI Responses API documentation" --include-domains platform.openai.com developers.openai.com --num-results 5 --include-text --format json
smart-search exa-similar "https://example.com/source" --num-results 5 --format json
smart-search zhipu-search "today China AI news" --search-engine search_pro_sogou --count 5 --format json
smart-search zhipu-mcp-reader "https://example.com/source" --format json
smart-search context7-library "react" "hooks" --format json
smart-search context7-docs "/facebook/react" "useEffect cleanup" --format json
smart-search anysearch-search "CVE-2024-3094" --domain security.cve --max-results 3 --format json
```

Provider configuration and capability boundaries are documented in [Providers](providers.md).

## Operational commands

```sh
smart-search doctor --format markdown
smart-search diagnose openai-compatible --format markdown
smart-search capabilities --format json
smart-search config path --format json
smart-search config list --format json
smart-search model list --format markdown
smart-search model current --format json
smart-search skills status --targets codex --format json
smart-search skills update --targets codex --format json
smart-search smoke --mock --format json
smart-search regression
```

Use `doctor` as preflight. It does not prove that every possible provider path will succeed. Use mock smoke and regression for deterministic checks; live smoke requires intentional credentials and network access.
