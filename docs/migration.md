# v1 migration guide

Smart Search v1.0.0 is a deliberate breaking release. Update callers before
moving users to the npm `latest` tag. There are no runtime aliases, dual-track
parsers, legacy JSON envelopes, or compatibility facades.

## Public surface

| v1 entrypoint | Use |
| --- | --- |
| `smart-search setup` | First-time local discovery-provider configuration |
| `smart-search search QUERY --format json` | Discover source candidates |
| `smart-search read URL --format json` | Fetch evidence from a known URL |
| `smart-search research QUERY --format json` | Compose staged evidence; the host writes the answer |

The v1 CLI does not promise `--version`. `--format json` is the machine
interface. Every operation emits one envelope with exactly these top-level
fields:

```json
{
  "version": 1,
  "operation": "search",
  "status": "complete",
  "data": {},
  "attempts": [],
  "warnings": [],
  "error": null
}
```

`operation` is `setup`, `search`, `read`, or `research`; `status` is
`complete`, `degraded`, or `failed`. `data` contains the operation result,
`attempts` contains safe provider-attempt summaries, `warnings` is a list of
safe strings, and `error` is either `null` or a structured `{code, message,
details?}` object. Provider payloads and credentials are not part of this
contract.

## Replacement map

Every row below is a caller migration. None of the old spellings is accepted as
a runtime alias.

| Pre-v1 surface | v1 action |
| --- | --- |
| `fetch` or provider-branded search commands | Retired; use `search` for discovery, then `read URL` for evidence. |
| `research plan` / `research run` | Retired; use `research QUERY`, which returns staged evidence for the host agent. |
| `map` | Retired with no replacement; discover sources with `search` and fetch selected URLs with `read`. |
| `capabilities` | Retired with no replacement; configure through `setup` and invoke the v1 operations. |
| `provider` command namespace and provider-branded commands | Retired with no command replacement; use `setup` for local configuration and the role-neutral v1 commands. |
| `doctor` command namespace | Retired with no replacement; use `setup` for configuration and inspect the structured result from each v1 operation. |
| `dev` command namespace | Retired with no CLI replacement; maintainers should run the offline checks in `docs/development.md`. |
| `skills` command namespace | Retired; install the `smart-search-cli` Skill or the independent Pi package, then use `web_search`, `web_read`, and `web_research`. |
| `deep` command/mode and deep-planning namespace | Retired as a separate surface; use `research QUERY` for staged evidence collection. |
| `config`/`control` and related old namespaces | Retired; `setup` is the replacement for first-time local configuration, while environment variables remain the CI path. |
| V2/V3/Workflow envelopes or schema selectors | Retired; parse the single v1 envelope and remove selector logic. |
| Old Python module or facade imports | Retired; call the CLI, or use the three native Pi tools. |
| Old Skill commands or MCP names | Retired; install the v1 Skill and use the three native Pi tools. |

A removed surface must fail instead of being silently reinterpreted. Do not
implement a compatibility shim in a script, Skill, or host adapter.

## Before upgrading

1. Inventory scripts, CI jobs, Skills, and imports for removed command names,
   envelope fields, selectors, and facade modules.
2. Back up the configuration directory and verify provider keys are available
   through the intended local file or environment.
3. Update callers using the replacement table, then run mocked/offline checks.
4. Run `smart-search setup` only when configuration needs changing. Never put
   credentials in a repository, log, issue, or support request.
5. Test `search`, `read`, and `research` separately. Discovery candidates must
   be read before they support important claims.

Persisted configuration readers remain supported only where implemented by this
release. Reading a supported config file does not make removed commands, output
formats, or facades supported, and environment secrets are never copied to disk.

## Release and rollback

Both npm packages release as `1.0.0` to the `latest` dist-tag, with Git tag
`v1.0.0` and a stable, non-prerelease GitHub release. Validate source and both
package tarballs first; publish the root package, then the Pi package, then
create the GitHub release only after both exact versions are confirmed in the
registry.

Before publication, revert the release-candidate commit or select the previous
published package version. After immutable publication, never overwrite or
republish `1.0.0`. If only one package was published, verify registry state and
publish only the missing package when the exact-version query proves it absent.
If registry state cannot be verified, stop. If the other exact version exists,
do not retry the immutable version blindly; investigate and use a new patch
version for corrections. A GitHub release is created only after both packages
are confirmed.

Rollback restores callers and configuration. It does not add a v1 output shim,
old envelope, runtime alias, or facade.
