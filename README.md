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

Prerequisites:

- Node.js 18 or newer for the npm package.
- Python 3.10 or newer when using the source checkout directly.
- At least one configured provider for the command you want to run.

## First run

Run the configuration check before the first provider call:

```sh
smart-search doctor --format markdown
```

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

## Choose a workflow

| Need | Command | Network behavior |
| --- | --- | --- |
| Fast answer and broad discovery | `smart-search search QUERY` | Live search |
| Explain the selected intent capabilities | `smart-search route QUERY` | No search/fetch provider call; hybrid may call configured router endpoints |
| Read one known page | `smart-search fetch URL` | Live page fetch |
| Build a research plan | `smart-search deep QUERY` | Offline planner |
| Run staged research | `smart-search research QUERY` | Live discovery, fetch, gap check, and evidence-only synthesis |
| Check configuration and connectivity | `smart-search doctor` | Masked diagnostics and provider checks |

`deep` is offline planning. `research` is live execution. They are separate commands so a plan can be inspected before any provider or page request runs.

## Core examples

```sh
# Fast answer
smart-search search "React useEffect cleanup docs" --format json

# Inspect routing without running search providers
smart-search route "React useEffect cleanup docs" --router-mode rules --format markdown

# Offline plan, then live execution
smart-search deep "Compare two current API designs" --budget standard --format json
smart-search research "Compare two current API designs" --budget deep --format markdown

# Install or refresh the AI-agent skill
smart-search setup --non-interactive --install-skills codex,claude,cursor,hermes
smart-search skills status --targets codex --format json
smart-search skills update --targets codex --format json

# Add and inspect ordered backup model routes
smart-search model add --id primary --provider openai-compatible --api-url "https://relay-a.example/v1" --api-key "key-a" --model "model-a"
smart-search model add --id backup --provider openai-compatible --api-url "https://relay-b.example/v1" --api-key "key-b" --model "model-b"
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
smart-search doctor --format markdown
smart-search diagnose openai-compatible --format markdown
smart-search regression
smart-search smoke --mock --format json
```

`doctor` reports masked configuration and connectivity. `diagnose` is for OpenAI-compatible search hangs or timeouts. `regression` and mock `smoke` do not require provider credentials.

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
