[简体中文](README.zh-CN.md) | English

CLI-first, evidence-first web research for agents and terminal users. v1 is a breaking release: the public commands are `search`, `read`, and `research`; `setup` is for first-time configuration only.

## Install

```sh
npm install -g @onedotmint/smart-search@latest
smart-search setup
```

Node.js 18+ is required for the npm wrapper. Source users need Python 3.10+.

## Daily use

```sh
smart-search search "latest Python release" --format json
smart-search read "https://www.python.org/downloads/" --format json
smart-search research "Compare two current API designs" --format json
```

All commands return the stable v1 JSON contract. `search` discovers candidates, `read` fetches evidence from a known URL, and `research` performs staged evidence collection without writing the final answer. The host agent writes the answer from fetched evidence; discovery snippets are not proof.

`setup` stores discovery provider configuration locally. Environment variables remain supported for CI. No command performs a live provider call unless that command needs it; package checks and `--help` are offline.

## Pi tools

Install the independent Pi package when using Pi:

```sh
pi install npm:@onedotmint/pi-smart-search@latest
```

It provides exactly `web_search`, `web_read`, and `web_research`, backed by the same v1 CLI contract.

## Migration and docs

This release replaces pre-v1 commands, envelopes, and Python facades; there are no runtime aliases. See [the migration guide](docs/migration.md), [getting started](docs/getting-started.md), [commands](docs/commands.md), and [providers](docs/providers.md).

See [development and release](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md) for maintainer checks.

```sh
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest tests -q
npm test
npm pack --dry-run
(cd integrations/pi && npm run typecheck && npm test && npm pack --dry-run)
git diff --check
```

MIT License.
