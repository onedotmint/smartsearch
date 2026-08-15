# Development and release

This repository contains a Python CLI, a bundled AI-agent skill, and an npm wrapper. Keep their public boundaries aligned when a user-visible command or configuration contract changes.

## Source checkout

Use Python 3.10 or newer and install the development extras:

```sh
python -m pip install -e ".[dev]"
```

The source entrypoint is:

```sh
python -m smart_search.cli --help
```

The npm wrapper remains a separate packaging path:

```sh
npm ci
npm test
npm pack --dry-run
```

## Verification

Run deterministic checks before any live provider probe:

```sh
python -m compileall -q src tests
python -m pytest tests -q
python -m smart_search.cli dev regression
python -m smart_search.cli dev smoke --mock --format json
npm test
npm pack --dry-run
git diff --check
```

Live `doctor`, live smoke, and provider probes require intentional credentials and network access. They supplement deterministic checks; they do not replace them.

The developer quality-gate suite checks the public README entrypoint, docs links, public/packaged skill parity, CLI contract markers, provider contract references, and release workflow assumptions. README should not become the source of truth for provider internals or AI-agent orchestration.

### Versioned JSON contracts

Keep the schema families independent during maintenance. The canonical command domain alone decides the contract: evidence commands always use V2, retained control-plane leaves always use V3, and `research plan` / `research run` use the Research Workflow family. There is no schema v1 runtime path and no schema selector. V3 fixtures live in `tests/fixtures/control_plane_v3.py`; contract and CLI coverage lives in `tests/test_control_plane_v3_contract.py` and `tests/test_cli_v3.py`.

For a v3 change, verify the exact ten top-level fields, the operation allowlist, structured error/exit mapping, recursive redaction, and actual `network` / `side_effects` metadata. Run at least:

```sh
python -m pytest tests/test_control_plane_v3_contract.py tests/test_cli_v3.py tests/test_cli_v2.py tests/test_cli_namespace.py -q
```

Do not add non-evidence operations to v2 or let evidence commands receive V3 JSON. The canonical command domain alone decides the contract family: evidence commands use V2, retained control-plane leaves use V3, and research plan/run use the Workflow family. Keep the public and packaged Skill on the canonical command boundary unless its own contract intentionally changes.

### JSON compatibility policy

The envelopes are stable machine contracts that agents depend on. Removing an existing field or changing its type or semantics is a major breaking change; **additive optional fields are compatible** (and update the golden baselines in the same commit). Input unknown fields are rejected by strict schema validation; output shapes change only through a deliberate schema bump. Byte-wise golden snapshots for representative V2 and Workflow envelopes live in `tests/fixtures/json_compat_baselines.py` and are enforced by `tests/test_json_compat_baselines.py` through the real projection + serializer + CLI emit path, so any shape drift fails without a deliberate fixture update.

## Documentation boundaries

| Concern | Source of truth |
| --- | --- |
| Product entry, install, core workflows | `README.md`, `README.zh-CN.md` |
| Detailed terminal command behavior | `docs/commands.md` |
| Provider capability, keys, and fallback boundaries | `docs/providers.md` and executable provider contract |
| Search, Deep Research, and evidence policy | `docs/concepts/` and `skills/smart-search-cli/references/` |
| AI-agent invocation contract | `skills/smart-search-cli/` and its packaged copy |
| Internal engineering conventions | project-local engineering specs |
| Release workflow behavior | `.github/workflows/publish-npm.yml` and `.github/releases/` |

When a command or provider changes, update both README languages, the relevant public docs, the skill reference when agent behavior changes, and the packaged skill copy when its source changes. Do not expose credentials, private configuration, or internal workbench files in public docs.

## Release lanes

Stable releases use a Git tag and npm `latest`:

```sh
git tag vX.Y.Z
git push origin vX.Y.Z
```

Pushes to `main` publish the next `<package.json version>-beta.N` to npm `next`. The beta counter restarts at 1 for each stable base version. For example: `0.1.10-beta.1`, `0.1.10-beta.2`, then `0.1.10-beta.3`.

npm versions are immutable. Old `*-dev.*` packages cannot be renamed in place; publish a new `*-beta.N` version instead. A prerelease must never be published with the `latest` dist-tag. The beta lane uses the npm dist-tag `next`.

Stable and prerelease releases can be dispatched manually with `workflow_dispatch`, `target_ref`, `version`, and `npm_tag`. The workflow rejects a prerelease sent to `latest`, checks whether the exact npm version already exists, and can skip GitHub release creation with `create_github_release=false`.

npm publish authentication uses a granular access token stored as the repository secret `NPM_TOKEN`; the workflow wires it as `NODE_AUTH_TOKEN` for the publish step and never prints it. `--provenance` uses the OIDC `id-token` permission. Without the `NPM_TOKEN` secret in repository settings, publishing fails authentication; the secret must exist before the first publish.

Stable release notes live in `.github/releases/vX.Y.Z.md`. The workflow appends npm package, dist-tag, and workflow-run metadata. Keep the release note focused on user-visible changes and include a machine-readable gap check when the release changes a structured contract.

Stable version bumps use a commit subject such as `chore(release): bump version to X.Y.Z`. Use `mise use -g` only for a deliberate local runtime selection; it is not part of the npm publish contract.

## Release closeout checklist

1. Check `npm view @onedotmint/smart-search versions --json` and `npm view @onedotmint/smart-search dist-tags --json` before choosing a version.
2. Check `gh release list --repo onedotmint/smartsearch --limit 100` before creating or editing a release.
3. Keep beta releases on `next`; do not move `latest`.
4. When npm returns `E409`, verify whether the exact version already exists before retrying; npm `E409` is not a reason to mutate a published version.
5. Install the published version and run `smart-search --version`, `smart-search dev regression`, and `smart-search dev smoke --mock --format json`.
6. For npm/mise wrapper validation on Windows, check the non-ASCII JSON path by piping the Chinese Deep Research JSON through `ConvertFrom-Json`.

The release contract also covers `gh release create vX.Y.Z-beta.N`, `npm versions are immutable`, and the distinction between `latest` and `next`; keep these boundaries visible in release notes and tests.

## Pull requests

Before opening a pull request:

```sh
git diff --check
python -m pytest tests -q
npm test
```

Describe behavior changes, affected docs, deterministic verification, and any live-provider limitation. Do not include API keys or local configuration files.
