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
python -m smart_search.cli regression
python -m smart_search.cli smoke --mock --format json
npm test
npm pack --dry-run
git diff --check
```

Live `doctor`, live smoke, and provider probes require intentional credentials and network access. They supplement deterministic checks; they do not replace them.

The regression suite checks the public README entrypoint, docs links, public/packaged skill parity, CLI contract markers, provider contract references, and release workflow assumptions. README should not become the source of truth for provider internals or AI-agent orchestration.

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

Stable release notes live in `.github/releases/vX.Y.Z.md`. The workflow appends npm package, dist-tag, and workflow-run metadata. Keep the release note focused on user-visible changes and include a machine-readable gap check when the release changes a structured contract.

Stable version bumps use a commit subject such as `chore(release): bump version to X.Y.Z`. Use `mise use -g` only for a deliberate local runtime selection; it is not part of the npm publish contract.

## Release closeout checklist

1. Check `npm view @konbakuyomu/smart-search versions --json` and `npm view @konbakuyomu/smart-search dist-tags --json` before choosing a version.
2. Check `gh release list --repo konbakuyomu/smartsearch --limit 100` before creating or editing a release.
3. Keep beta releases on `next`; do not move `latest`.
4. When npm returns `E409`, verify whether the exact version already exists before retrying; npm `E409` is not a reason to mutate a published version.
5. Install the published version and run `smart-search --version`, `smart-search regression`, and `smart-search smoke --mock --format json`.
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
