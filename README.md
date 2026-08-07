# smart-search

[简体中文](README.zh-CN.md) | English

CLI-first, skill-driven web research for AI agents and terminal users. `smart-search` gives one reproducible command layer for live search, source discovery, page fetching, site mapping, provider diagnostics, offline Deep Research planning, and live Deep Research execution.

`smart-search` is a normal CLI, not an MCP server. AI tools can install the bundled `smart-search-cli` skill, while scripts and terminal users call the same `smart-search` command.

## Install

```sh
npm install -g @onedotmint/smart-search@latest
smart-search --version
smart-search setup
```

The npm package creates an isolated Python runtime during installation. Direct Python use is also supported; see [Getting started](docs/getting-started.md).

Root help intentionally shows only `search`, `fetch`, `capabilities`, and `setup`. Use `smart-search --help-all` to discover Advanced, provider, developer, and legacy-compatible commands.

Prerequisites:

- Node.js 18 or newer for the npm package.
- Python 3.10 or newer when using the source checkout directly.
- At least one configured provider for the command you want to run.

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
smart-search 0.1.0
```

Search responses use a versioned JSON envelope. Provider text and URLs vary; the stable shape is `schema_version`, `command`, `data`, and `meta`.

### Opt-in v2 Core JSON API

The evidence-first Core API is the recommended Agent default. It is selected with a root-global flag:

```sh
smart-search --schema-version 2 capabilities
smart-search --schema-version 2 search "example query"
smart-search --schema-version 2 fetch "https://example.com/page"
```

`map` is available as the Advanced `site_discovery` operation; see the command reference for its v2 invocation.

- v2 defaults to JSON, the only stable machine contract, and returns the Phase 2 envelope (`status`, `operation`, `evidence`, `routing`, `attempts`, ...). `--format markdown|content` renders a non-stable human view of the same validated envelope; JSON keeps no field-level compatibility promise for those views.
- v2 `search` returns discovery candidates only; it never calls legacy `main_search` or accepts `--response-mode`.
- Host agents write the final answer from fetched `evidence.items`; discovery candidates are not claim-level proof.
- `capabilities` uses envelope-only meta operation `capability_status` (local inspection, no Provider network).
- `--fail-on-degraded` is available for v2 and v3; `--trace` remains v2-only. Post-subcommand `--schema-version` placement is not supported.

### Opt-in v3 control-plane JSON API

V3 is a separate JSON family for stable local administration, explicit probes, developer diagnostics, filesystem work, and regression subprocesses. It is not an evidence envelope and is not the Agent default:

```sh
smart-search --schema-version 3 config list
smart-search --schema-version 3 provider status
smart-search --schema-version 3 doctor status
smart-search --schema-version 3 dev smoke --mock
```

V3 returns `complete` / `degraded` / `failed` plus explicit `network` and `side_effects` objects. It is root-global, JSON-default, and additive: v1 stays the default and v2 stays the evidence-first Core API. JSON is the only stable machine contract; `--format markdown|content` renders non-stable human views of the validated envelope. See the [command reference](docs/commands.md#opt-in-schema-version-3-control-plane-api) for its allowlist, errors, exits, and migration boundary.

## Choose a workflow

| Need | Command | Network behavior |
| --- | --- | --- |
| Evidence-first discovery and fetch (Agent default) | `smart-search --schema-version 2 search\|fetch\|capabilities` | Live discovery/fetch; capabilities is local |
| Control-plane automation (opt-in) | `smart-search --schema-version 3 config\|provider\|doctor\|dev ...` | Explicit local, network, filesystem, and subprocess metadata |
| Fast v1 answer and broad discovery | `smart-search search QUERY` | Live search with optional synthesis |
| Explain the selected intent capabilities | `smart-search route QUERY` | No search/fetch provider call; hybrid may call configured router endpoints |
| Read one known page | `smart-search fetch URL` | Live page fetch |
| Build a research plan | `smart-search deep QUERY` / `research plan QUERY` | Offline planner |
| Run staged evidence research | `smart-search research run QUERY` | Live discovery, fetch, gaps; host writes the answer |
| Run staged research with synthesis | `smart-search research run QUERY --synthesize` or bare `research` | Live discovery, fetch, and evidence-only synthesis |
| Local readiness | `smart-search doctor status` | Local only; no provider probe |
| Live aggregate connectivity | `smart-search doctor` / `doctor probe` | Masked diagnostics and provider checks |
| One provider reachability check | `smart-search provider probe PROVIDER` | Exactly one provider/family |

`deep` / `research plan` are offline planning. `research run` is the Agent-facing evidence workflow. Bare `research` remains the legacy synthesized executor.

## Core examples

```sh
# Agent default evidence path
smart-search --schema-version 2 capabilities
smart-search --schema-version 2 search "React useEffect cleanup docs"
smart-search --schema-version 2 fetch "https://react.dev/reference/react/useEffect"

# Staged multi-source research without automatic synthesis
smart-search research run "Compare two current API designs" --format json

# Offline plan, then legacy synthesized live execution
smart-search research plan "Compare two current API designs" --budget standard --format json
smart-search research "Compare two current API designs" --budget deep --format markdown

# Local readiness, then explicit live checks
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search provider probe exa --format json

# Local provider metadata and ordered backup routes
smart-search provider status --format json
smart-search provider routes list --format markdown

# Compatibility entries remain valid
smart-search deep "Compare two current API designs" --budget standard --format json
smart-search model list --format markdown
```

Use `--format json` for agents and scripts, `--format markdown` for reports, and `--format content` for compact terminal reading. See the [command reference](docs/commands.md) for flags and provider-specific commands.

## Evidence boundary

Search results are discovery candidates. For high-risk claims, fetch the relevant pages and cite fetched text. Unsupported key claims must be fetched or downgraded to unverified candidates. The full policy is in [Evidence](docs/concepts/evidence.md).

## Documentation

- [Getting started](docs/getting-started.md) — installation, setup, first successful calls, and skill installation.
- [Command reference](docs/commands.md) — commands, aliases, common flags, and output formats.
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
smart-search doctor --format markdown
smart-search provider probe exa --format json
smart-search diagnose openai-compatible --format markdown
smart-search regression
smart-search smoke --mock --format json
```

`doctor status` is local readiness only. `doctor` / `doctor probe` are the live aggregate diagnostic. `provider probe PROVIDER` checks one named provider. `provider list` and `provider status` remain local-only metadata and eligibility views.

## Development

Source-checkout verification and release instructions live in [Development](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md). The short verification set is:

```sh
python -m compileall -q src tests
python -m pytest tests -q
python -m smart_search.cli regression
python -m smart_search.cli smoke --mock --format json
npm test
npm pack --dry-run
git diff --check
```

On Windows, replace `python` with `py -3` when `python` is not on `PATH`.

## License

MIT
