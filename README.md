# smart-search

[简体中文](README.zh-CN.md) | English

CLI-first, skill-driven web research for AI agents and terminal users. `smart-search` gives one reproducible command layer for live search, source discovery, page fetching, site mapping, provider diagnostics, offline Deep Research planning, and live Deep Research execution.

`smart-search` is a normal CLI, not an MCP server. AI tools can install the bundled `smart-search-cli` skill, while scripts and terminal users call the same `smart-search` command.

## Install

```sh
npm install -g @onedotmint/smart-search@latest
smart-search --version
smart-search config path --format json
```

The npm package creates an isolated Python runtime during installation. Direct Python use is also supported; see [Getting started](docs/getting-started.md).

Root help intentionally shows only the evidence core: `search`, `fetch`, and `capabilities`. Use `smart-search --help-all` to discover the complete canonical inventory (research workflow and V3 control-plane commands).

Prerequisites:

- Node.js 18 or newer for the npm package.
- Python 3.10 or newer when using the source checkout directly.
- `fetch` works with **zero configuration** through anonymous Jina Reader.
  Configure a discovery provider to run `search`; model routes are optional
  LLM synthesis and never a Core requirement.

## First run

Run the local readiness check before the first provider call:

```sh
smart-search doctor status --format json
```

Run `smart-search doctor probe --format markdown` only when an explicit live aggregate connectivity check is needed.

Run a fast live search:

```sh
smart-search search "latest Python release" --format json
```

Fetch the exact page when the answer needs claim-level evidence:

```sh
smart-search fetch "https://www.python.org/downloads/" --format markdown
```

The first local check has deterministic output:

```text
$ smart-search --version
smart-search 0.3.0
```

Search responses use a strict versioned JSON envelope: evidence commands return the V2 envelope, control-plane commands return the V3 envelope, and `research run` returns the Research Workflow envelope. Provider text and URLs vary; the machine contract never changes.

### V2 evidence Core API

The evidence-first Core API is the recommended Agent default. The canonical command domain selects it; no selector flag exists:

```sh
smart-search capabilities
smart-search search "example query"
smart-search fetch "https://example.com/page"
smart-search fetch "https://example.com/page" --full
```

`map` is available as the Advanced `site_discovery` operation.

- V2 defaults to JSON, the only stable machine contract, and returns the strict envelope (`status`, `operation`, `evidence`, `routing`, `attempts`, ...). `--format markdown|content` renders a non-stable human view of the same validated envelope.
- V2 `search` returns discovery candidates only; it never calls legacy `main_search` or accepts `--response-mode`.
- v0.3.0: `search`/`source_discovery` runs through the multi-source retrieval gateway when Brave or Exa is configured (Tavily for research intent): one normalized candidate model, deterministic URL dedup with provenance, reciprocal-rank fusion (RRF), and optional best-effort Jina reranking. Setups without Brave/Exa/Tavily keep the exact pre-v0.3.0 behavior. See the [provider guide](docs/providers.md#retrieval-policy-and-fusion-v030).
- Fetched evidence is bounded to 8,000 characters per item by default; every item reports `truncated`, `original_length`, and `returned_length`, and `fetch --full` bypasses the cap for the available full content. `research run` admits at most five evidence items per run.
- Host agents write the final answer from fetched `evidence.items`; discovery candidates are not claim-level proof.
- `capabilities` uses envelope-only meta operation `capability_status` (local inspection, no Provider network).
- `--fail-on-degraded` is available for v2 and v3.

### V3 control-plane JSON API

V3 is a separate JSON family for stable local administration, explicit probes, developer diagnostics, filesystem work, and the developer quality-gate subprocess. It is not an evidence envelope:

```sh
smart-search config list
smart-search provider status
smart-search doctor status
smart-search dev smoke --mock
```

V3 returns `complete` / `degraded` / `failed` plus explicit `network` and `side_effects` objects. JSON is the only stable machine contract; `--format markdown|content` renders non-stable human views of the validated envelope. See the [command reference](docs/commands.md#control-plane-v3-json-api) for its allowlist, errors, exits, and migration boundary.

## Choose a workflow

| Need | Command | Network behavior |
| --- | --- | --- |
| Evidence-first discovery and fetch (Agent default) | `smart-search search\|fetch\|capabilities` | Live discovery/fetch; capabilities is local |
| Control-plane automation | `smart-search config\|provider\|doctor\|dev ...` | Explicit local, network, filesystem, and subprocess metadata |
| Read one known page | `smart-search fetch URL` | Live page fetch |
| Build a research plan | `smart-search research plan QUERY` | Offline planner |
| Run staged evidence research | `smart-search research run QUERY` | Live discovery, fetch, gaps; host writes the answer |
| Local readiness | `smart-search doctor status` | Local only; no provider probe |
| Live aggregate connectivity | `smart-search doctor probe` | Masked diagnostics and provider checks |
| One provider reachability check | `smart-search provider probe PROVIDER` | Exactly one provider/family |

`research plan` is offline planning. `research run` is the Agent-facing evidence workflow. Legacy commands, aliases, and the schema selector are removed and fail with the replacement family's strict error.

## Core examples

```sh
# Agent default evidence path
smart-search capabilities
smart-search search "React useEffect cleanup docs"
smart-search fetch "https://react.dev/reference/react/useEffect"

# Staged multi-source research without automatic synthesis
smart-search research run "Compare two current API designs" --format json

# Offline plan
smart-search research plan "Compare two current API designs" --budget standard --format json

# Local readiness, then explicit live checks
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search provider probe exa --format json

# Local provider metadata and ordered backup routes
smart-search provider status --format json
smart-search provider routes list --format markdown

```

Use `--format json` for agents and scripts, `--format markdown` for reports, and `--format content` for compact terminal reading. See the [command reference](docs/commands.md) for flags and provider-specific commands.

## Evidence boundary

Search results are discovery candidates. For high-risk claims, fetch the relevant pages and cite fetched text. Unsupported key claims must be fetched or downgraded to unverified candidates. The full policy is in [Evidence](docs/concepts/evidence.md).

## Documentation

- [Getting started](docs/getting-started.md) — installation, setup, first successful calls, and skill installation.
- [Command reference](docs/commands.md) — commands, aliases, common flags, and output formats.
- [Migration guide](docs/migration.md) — upgrading published 0.1.0 persisted data.
- [Provider guide](docs/providers.md) — capabilities, fallback boundaries, API keys, and minimum profiles.
- [Search vs Deep Research vs Research](docs/concepts/search-vs-deep-vs-research.md) — planner and executor contracts.
- [Evidence policy](docs/concepts/evidence.md) — discovery, fetch, citations, and gaps.
- [Routing](docs/concepts/routing.md) — intent modes, remote router calls, and observability.
- [Development](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md) — verification, packaging, and release lanes.
- [Contributing](https://github.com/onedotmint/smartsearch/blob/main/CONTRIBUTING.md) — source changes, documentation parity, and pull requests.

The public AI-agent contract is maintained in the [repository skill directory](https://github.com/onedotmint/smartsearch/tree/main/skills/smart-search-cli). The bundled copy is packaged with the Python runtime and must stay synchronized.

## Troubleshooting

```sh
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search provider probe exa --format json
smart-search dev diagnose openai-compatible --format markdown
smart-search dev regression
smart-search dev smoke --mock --format json
```

`doctor status` is local readiness only. `doctor probe` is the live aggregate diagnostic. `provider probe PROVIDER` checks one named provider. `provider list` and `provider status` remain local-only metadata and eligibility views. Use the `dev` namespace for the developer diagnostics: `dev diagnose`, `dev smoke`, `dev regression`, and `dev skills`.

## Development

Source-checkout verification and release instructions live in [Development](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md). The short verification set is:

```sh
python -m compileall -q src tests
python -m pytest tests -q
python -m smart_search.cli dev regression
python -m smart_search.cli dev smoke --mock --format json
npm test
npm pack --dry-run
git diff --check
```

On Windows, replace `python` with `py -3` when `python` is not on `PATH`.

## License

MIT
