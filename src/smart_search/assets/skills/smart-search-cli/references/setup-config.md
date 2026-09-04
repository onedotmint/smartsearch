# Setup And Config

## First-time setup

Run:

```sh
smart-search setup --format json
```

`setup` is the only configuration wizard. It writes local settings and does not
call providers. Use environment variables for CI and keep credentials outside
the repository. The selected `fast`, `balanced`, or `research` mode controls
search policy; it is not a compatibility namespace.

## Environment and local files

The CLI reads supported local configuration and environment variables. Environment
values take precedence where implemented. `SMART_SEARCH_CONFIG_DIR` can pin an
absolute writable config directory for tests or sandboxed installs. Do not copy
secret values into logs, prompts, issue reports, or source control.

Persisted configuration readers may retain supported files during the v1
migration. This does not preserve removed commands, envelopes, aliases, or
facades.

## Pi and Skill installation

Install the independent stable Pi package with:

```sh
pi install npm:@onedotmint/pi-smart-search@latest
```

It provides exactly `web_search`, `web_read`, and `web_research`. The bundled
`smart-search-cli` Skill provides the same CLI-first guidance for agent hosts.

## Diagnostics

Package installation, `smart-search --help`, and offline tests do not call
providers. For a real provider check, run the relevant v1 operation with
explicit network access and configured credentials; inspect its structured
`status`, `attempts`, `warnings`, and `error` fields.
