# Migration guide

This guide is the published contract for upgrading persisted data created by
the `0.1.0` release to the current configuration structure. It covers the five
approved input-side readers only. It is **not** a runtime compatibility
contract: removed commands, aliases, selector spellings, and old Skill
instructions fail deterministically and are never silently reinterpreted as
different commands.

The machine-readable authority for every removed surface and its single
replacement is the frozen legacy surface inventory
(`tests/fixtures/legacy_surface_inventory.json`, repository-internal). This
guide summarizes the data upgrade contract; the inventory row for each removed
entry carries its exact replacement, owning tasks, fixture, and rollback point.

## What migrates

| Published `0.1.0` persisted state | Approved reader | Upgrade outcome |
| --- | --- | --- |
| Local `config.json` in the platform default directory | Snapshot file reader (`get_saved_config` / `ConfigSnapshot`) | Source file is never rewritten by a reader; values are read through one immutable file-plus-environment snapshot |
| Windows legacy `~\.config\smart-search\config.json` location | Windows legacy config-directory fallback | Consulted only when the current default file is missing on Windows; the active source is reported as `legacy_windows_home`. The fallback reader itself never writes, but the first controlled route write upgrades that resolved active file in place without copying secrets elsewhere |
| Effective environment values for persisted config and routes | Environment-precedence reader | Environment values override saved values for reads and remain ephemeral; an environment-controlled legacy provider or route list blocks local upgrade writes rather than being copied into `config.json` |
| Saved `XAI_*` and `OPENAI_COMPATIBLE_*` main-search keys | Legacy main-search route conversion | Converted into ordered `SMART_SEARCH_MODEL_ROUTES` entries (`legacy-xai-responses`, then `legacy-openai-compatible`) on the first controlled local route write; the original keys remain in the file unchanged |
| `SMART_SEARCH_MODEL_ROUTES` persisted array | Model-route read/validate/write | Retained in the same deterministic order with stable ids; provider-specific fields (`tools`, `stream`, `fallback_models`) are normalized by the same parser that validates new routes |

Only these five readers are perpetual persisted-data surfaces. No other legacy
state becomes a runtime API, and none of them projects V1 runtime output.

## Invariants

- **Environment precedence.** Environment values override file values for
  reads. An environment-controlled legacy provider or route list rejects a
  local upgrade write; the caller defines `SMART_SEARCH_MODEL_ROUTES` in the
  environment instead.
- **Environment credentials never land on disk.** An upgrade or route write
  never copies an environment-owned credential or provider setting into
  `config.json`. Pre-existing file secrets stay exactly where they were
  published.
- **Deterministic upgrade.** The first controlled route write preserves
  eligible saved xAI then OpenAI-compatible values in that fallback order with
  stable ids and provider-specific fields.
- **Atomic writes.** A write serializes to a temporary file beside the target,
  flushes and fsyncs it, then replaces the target atomically. Invalid,
  duplicate, conflicting, or partially writable input leaves the previous file
  bytes unchanged and removes the temporary file; failures report typed write
  commitment (attempted/committed) without raw exception details or secret
  values.
- **Redaction.** Every output boundary masks nested `api_key`/token/secret
  fields and credentials embedded in URLs (userinfo, sensitive query
  parameters, sensitive fragments) recursively. Raw values exist only in the
  persisted file or an explicitly requested unmasked internal read.

## Old invocations and Skill instructions

Every removed runtime entry fails deterministically and is replaced by exactly
one canonical spelling. Legacy spellings are never accepted as synonyms for the
new commands:

| Removed surface | Canonical replacement |
| --- | --- |
| The schema selector (`--schema-version` with any value, including `=` and single-dash spellings) | Omit the selector; the canonical command domain decides the contract |
| Legacy control commands and aliases (`cfg`, `mdl`, the legacy `model` family, bare diagnostic/dev commands) | The canonical V3 control-plane spellings under `config`, `provider routes`, `doctor`, and `dev` |
| Legacy provider-branded and Experimental commands | The generic V2 Evidence commands `search`, `fetch`, `map`, `capabilities` |
| Research synthesis controls and the `final_answer` compatibility field | `research run` without synthesis controls; host agents author the final answer |
| Removed public Python facade exports | The typed V2/V3/Workflow entrypoints the inventory replacement names |

Old Skill instructions that still reference the removed spellings are
incompatible: an agent or script that invokes them receives the strict failure
for the owning command family, never a reinterpreted execution. Update scripts
and Skills to the canonical replacement before upgrading.

## Rollback

A reader transform is rolled back by restoring the preceding reader revision;
the source `config.json` bytes are never part of the rollback because every
upgrade preserves them (only the route-list key is added on a controlled
write). Do not add a V1 output shim, a reverse migration writer, or a runtime
alias when rolling back. The migrated file remains readable by the preceding
reader for the keys that existed before the upgrade.

## Windows legacy location

Earlier Windows builds defaulted to `~\.config\smart-search\config.json`, while
some installs pinned `%LOCALAPPDATA%\smart-search` through
`SMART_SEARCH_CONFIG_DIR`. The legacy location is consulted only when the new
default file is missing; when both exist the new default wins. `config path`
and `doctor status` report `legacy_windows_home` as the active source so
upgrades never silently lose configuration. Do not delete either file or the
user-level override until the upgraded CLI has been verified with
`config path`, `doctor status`, and the smoke/regression checks.
