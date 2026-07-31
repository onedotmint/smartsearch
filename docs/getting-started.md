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

The interactive wizard is the normal path:

```sh
smart-search setup
smart-search doctor status --format json
```

`doctor status` is local readiness only: configuration storage, capability eligibility, Core evidence path, and minimum-profile health. It does not probe providers. Use `doctor` / `doctor probe` only when you intentionally want a live aggregate connectivity check. A command checks only the capabilities it needs. The historical `standard` profile is fail-closed for the full diagnostic and still expects one `main_search`, one `docs_search`, and one `web_fetch` provider, while evidence-first workflows prioritize source/docs discovery plus content fetch.

The configuration file is stored in the platform config directory:

- Windows: `%LOCALAPPDATA%\smart-search\config.json`
- Linux/macOS: `~/.config/smart-search/config.json`
- Override: `SMART_SEARCH_CONFIG_DIR`

Use an explicit config directory in CI, containers, and tests. Smart Search does not silently write credentials into the current repository when the configured directory cannot be protected.

For a scripted setup, pass only the keys needed by the target environment:

```sh
smart-search setup --non-interactive \
  --xai-api-key "your-xai-key" \
  --xai-model "grok-4-fast" \
  --exa-key "your-exa-key" \
  --jina-key "your-jina-key"
```

See [Providers](providers.md) for the full capability and key matrix.

## Run the first search

Agent default evidence path:

```sh
smart-search --schema-version 2 capabilities
smart-search --schema-version 2 search "latest Python release"
smart-search --schema-version 2 fetch "https://www.python.org/downloads/"
```

v2 search returns discovery candidates only. Host agents write the final answer from fetched evidence items.

Compatibility v1 search remains available:

```sh
smart-search search "latest Python release" --format json
```

Use `--format markdown` for a report or `--format content` for a compact terminal result. Add `--extra-sources 2` when you want more discovery candidates, not when you need proof for a claim.

A successful v1 JSON response keeps the stable envelope:

```json
{
  "schema_version": "1",
  "command": "search",
  "data": {
    "content": "provider answer",
    "sources": []
  },
  "meta": {
    "providers_used": [],
    "fallback_used": false
  }
}
```

Provider content, source URLs, and observability counts are runtime values. Use [Evidence](concepts/evidence.md) to decide which sources must be fetched.

## Fetch page evidence

```sh
smart-search fetch "https://www.python.org/downloads/" --format markdown --output evidence.md
```

Output files are not overwritten by default. Add `--force` only when replacing an existing file is intentional.

For a site structure rather than one page:

```sh
smart-search map "https://docs.python.org/3/" --max-depth 1 --limit 20 --format json
```

## Plan or execute Deep Research

Use the offline planner when an agent or a person should inspect the plan first:

```sh
smart-search deep "Compare two current API designs" --budget standard --format json
```

Use the Agent-facing staged executor when the host should receive admitted evidence and write the answer itself:

```sh
smart-search research run "Compare two current API designs" --budget deep --format json
```

Use bare `research` or `research run --synthesize` when the CLI should also run evidence-only synthesis:

```sh
smart-search research "Compare two current API designs" --budget deep --format markdown
```

The distinction is intentional. `deep` / `research plan` are not executors; `research run` is the evidence-first live staged workflow, and bare `research` remains the synthesized compatibility path. See [Search vs Deep Research vs Research](concepts/search-vs-deep-vs-research.md).

## Install the AI-agent skill

Install managed skill files during setup or refresh them after an npm upgrade:

```sh
smart-search setup --non-interactive --install-skills codex,claude,cursor,hermes
smart-search skills status --targets codex,claude,cursor,hermes --format json
smart-search skills update --targets codex,claude,cursor,hermes --format json
```

The skill installer writes only the managed `smart-search-cli` files under user-level tool directories. It does not create Trellis files, hooks, agents, commands, or provider configuration. Status values are `missing`, `up_to_date`, `stale`, `extra_files`, and `error`.

## Next steps

- [Command reference](commands.md)
- [Provider guide](providers.md)
- [Evidence policy](concepts/evidence.md)
- [Routing](concepts/routing.md)
- [Development](development.md)
