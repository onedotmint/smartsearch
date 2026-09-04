# Getting started

## Install and configure

```sh
npm install -g @onedotmint/smart-search@latest
smart-search setup --format json
```

The wrapper needs Node.js 18+. Source checkout users need Python 3.10+ and can
run `python3 -m pip install -e ".[dev]"`. `setup` is the only configuration
wizard; use environment variables for CI and do not store credentials in the
repository.

## First calls

```sh
smart-search search "latest Python release" --format json
smart-search read "https://www.python.org/downloads/" --format json
smart-search research "Compare two current API designs" --format json
```

`search` returns discovery candidates. Use `read` on relevant URLs before
making claim-level statements. `research` performs the same evidence-first
composition and leaves final answer writing to the host agent.

The v1 CLI has no `--version` promise. Use `--help` and the JSON `version` and
`operation` fields to identify the interface. The stable envelope fields are
`version`, `operation`, `status`, `data`, `attempts`, `warnings`, and `error`.
There is no schema selector, legacy command alias, or old envelope compatibility
layer.

## Pi

```sh
pi install npm:@onedotmint/pi-smart-search@latest
```

Pi exposes `web_search`, `web_read`, and `web_research`, which invoke the same
v1 paths. Keep remote content untrusted and preserve citations from read
results.

## Next steps

- [Command reference](commands.md)
- [Provider guide](providers.md)
- [Migration guide](migration.md)
- [Evidence policy](concepts/evidence.md)
