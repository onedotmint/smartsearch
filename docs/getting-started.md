# Getting started

This guide takes a new installation from zero configuration to one search and one fetched page. It does not require every provider to be configured.

## Install the npm package

```sh
npm install -g @onedotmint/smart-search@latest
smart-search --version
```

Use the `next` channel when testing an unreleased package:

```sh
npm install -g @onedotmint/smart-search@next
```

The wrapper needs Node.js 18 or newer. It creates an isolated Python 3.10+ runtime inside the package installation and exposes one `smart-search` command.

For source development, install Python dependencies from the repository instead:

```sh
python -m pip install -e ".[dev]"
```

On Windows, use `py -3` when `python` is not available.

## Configure one usable path

Configure keys through the config command (no interactive wizard):

```sh
smart-search config set XAI_API_KEY "your-xai-key"
smart-search config list --format json
smart-search doctor status --format json
```

`doctor status` is local readiness only: configuration storage, capability eligibility, Core evidence path, and minimum-profile health. It does not probe providers. Use `doctor probe` only when you intentionally want a live aggregate connectivity check. A command checks only the capabilities it needs. The historical `standard` profile is fail-closed for the full diagnostic and still expects one `main_search`, one `docs_search`, and one `web_fetch` provider, while evidence-first workflows prioritize source/docs discovery plus content fetch.

The configuration file is stored in the platform config directory:

- Windows: `%LOCALAPPDATA%\smart-search\config.json`
- Linux/macOS: `~/.config/smart-search/config.json`
- Override: `SMART_SEARCH_CONFIG_DIR`

Use an explicit config directory in CI, containers, and tests. Smart Search does not silently write credentials into the current repository when the configured directory cannot be protected.

For a scripted setup, set only the keys needed by the target environment:

```sh
smart-search config set XAI_API_KEY "your-xai-key"
smart-search config set XAI_MODEL "grok-4-fast"
smart-search config set EXA_API_KEY "your-exa-key"
smart-search config set JINA_API_KEY "your-jina-key"
```

See [Providers](providers.md) for the full capability and key matrix.

## Run the first search

Agent default evidence path:

```sh
smart-search capabilities
smart-search search "latest Python release"
smart-search fetch "https://www.python.org/downloads/"
```

v2 search returns discovery candidates only. Host agents write the final answer from fetched evidence items. The canonical V2 surface accepts only `--format json|markdown|content`; V1-era options such as `--output`, `--force`, `--extra-sources`, `--timeout`, and `--stream` are rejected before any provider work.

A successful v2 search keeps the exact V2 envelope:

```json
{
  "schema_version": "2",
  "ok": true,
  "command": "search",
  "operation": "source_discovery",
  "result": {
    "total": 1,
    "items": [
      {"id": "candidate-id"}
    ]
  },
  "evidence": {
    "candidates": [
      {"id": "candidate-id", "resource": "https://example.com", "provider": "tavily", "title": "Example", "snippet": "..."}
    ],
    "items": [],
    "citations": [],
    "gaps": []
  },
  "routing": {
    "requested_capabilities": ["source_discovery"],
    "executed_capabilities": [],
    "policy_version": "v2-parser-1",
    "reason_codes": []
  },
  "attempts": [],
  "degradation": [],
  "error": null,
  "meta": {
    "request_id": "...",
    "duration_ms": 0,
    "warnings": [],
    "deprecations": []
  }
}
```

Provider content, source URLs, and observability counts are runtime values. Use [Evidence](concepts/evidence.md) to decide which sources must be fetched.

## Fetch page evidence

```sh
smart-search fetch "https://www.python.org/downloads/" --format markdown
```

The strict V2/V3/Workflow families reject `--output` and `--force` before any owner work. Save evidence by capturing stdout JSON with shell redirection instead.

For a site structure rather than one page:

```sh
smart-search map "https://docs.python.org/3/" --format json
```

## Plan or execute Deep Research

Use the offline planner when an agent or a person should inspect the plan first:

```sh
smart-search research plan "Compare two current API designs" --budget standard --format json
```

Use the Agent-facing staged executor when the host should receive admitted evidence and write the answer itself:

```sh
smart-search research run "Compare two current API designs" --budget deep --format json
```

Bare `research`, `rs`, `deep`, and `dr` are removed spellings and fail with the workflow family's strict error; the workflow never writes an answer itself.

The distinction is intentional. `research plan` is the offline planner, not an executor; `research run` is the evidence-first live staged workflow, and the host agent writes the final answer. See [Search vs Deep Research vs Research](concepts/search-vs-deep-vs-research.md).

## Install the AI-agent skill

Install managed skill files or refresh them after an npm upgrade:

```sh
smart-search dev skills status --targets codex,claude,cursor,hermes --format json
smart-search dev skills update --targets codex,claude,cursor,hermes --format json
```

The skill installer writes only the managed `smart-search-cli` files under user-level tool directories. It does not create Trellis files, hooks, agents, commands, or provider configuration. Status values are `missing`, `up_to_date`, `stale`, `extra_files`, and `error`.

## Next steps

- [Command reference](commands.md)
- [Provider guide](providers.md)
- [Evidence policy](concepts/evidence.md)
- [Routing](concepts/routing.md)
- [Development](development.md)
