# Development and release

Smart Search v1 is a Python CLI with a root npm wrapper and an independent Pi
package. Public commands are `setup`, `search`, `read`, and `research`; keep
README, docs, Skills, and package metadata aligned.

## Offline verification

Daily loop (offline, no credentials, target 2–5 seconds):

```sh
./scripts/check quick
```

Pre-commit / PR gate:

```sh
./scripts/check full
```

`quick` compiles `src`/`tests`/`benchmarks`/`scripts`, runs a representative
offline pytest subset (ranking/selection unit tests, role-specific provider
contracts, v1 search/read/research integration, evaluator schema/import tests,
and the benchmark import/`--help` smoke), then smokes
`benchmarks/retrieval_benchmark.py --help`. Remaining contract, replay, and
regression tests stay in `full`. `full` runs
the complete offline gate: all Python tests, root `npm test` plus package
dry-run, Pi typecheck/tests/package dry-run when `integrations/pi` exists, and
`git diff --check`.
Direct commands remain valid; these wrappers are the documented entry points.
Neither command calls live providers, reads API keys, or publishes packages.
Inspect tarball contents in a temporary directory and keep generated archives
out of commits.

## Live retrieval evaluation

Maintainer-only quality measurement. It is never a CI gate, never a
correctness requirement, and never free: it makes live provider calls and
consumes API quota. Install the optional eval extra for standard IR metrics:

```sh
python -m pip install -e ".[eval]"
./scripts/eval retrieval --help
./scripts/eval retrieval --live
./scripts/eval retrieval --live --output /tmp/smart-search-eval
```

`--help` is offline. `--live` is the explicit opt-in. Unconfigured Brave, Exa,
Tavily, RRF, and RRF+Jina modes are skipped with a reason instead of scoring
as zero. Artifacts are JSON (`report.json`), JSONL (`results.jsonl` plus
sanitized `runs.jsonl`), and a Markdown summary. Use a supplied `--output`
directory or accept a timestamped directory under `benchmarks/artifacts/`.
Offline fixture evaluation lives in pytest, not this command.

## Release

A release is a single reviewed commit on `main` that synchronizes the root
`package.json`/`package-lock.json`, the Pi `integrations/pi/package.json` and
its lockfile, and `pyproject.toml` to one stable `x.y.z` version, and adds a
non-empty `.github/releases/vX.Y.Z.md` notes file. Pushing that commit to
`main` triggers the stage-only `.github/workflows/publish-npm.yml`: it proves
both manifest version fields changed together from the pushed commit's parent,
validates the synchronized metadata and notes, runs the offline checks, and
checks fail-closed public npm registry state. Each package whose exact version
is absent is staged independently with OIDC/provenance; an existing
present/latest package is skipped. The workflow ends with an Actions Summary
that tells a maintainer to review each staged package on npmjs.com and approve
it with npm 2FA. It does not verify public presence after staging or create a
Git tag or GitHub release.

After npm website review and approval, wait until both exact versions are
public and both `latest` tags point to `X.Y.Z`. Then manually run
`.github/workflows/finalize-npm-release.yml` with the same full immutable commit
SHA. The finalizer verifies that SHA is reachable from `origin/main`, checks the
synchronized metadata and release notes again, verifies both public exact/latest
states, and only then creates or updates the `vX.Y.Z` Git tag and stable GitHub
release from the committed notes.

Ordinary documentation or code pushes without a synchronized version change do
not stage packages. One-sided version bumps, metadata drift, missing or empty
notes, prerelease versions, registry uncertainty, and mismatched existing tags
stop before any side effect. Manual stage dispatch and finalization are
recovery-only: each requires a full commit SHA reachable from `origin/main`,
with the version derived from that commit; branch names, short SHAs, and mutable
refs are rejected.

npm versions are immutable: if a staged package is not yet public, resolve it
in npm's staged-package UI rather than re-staging based on guessed queue state.
If the immutable version is already public, fix forward with a new patch version.
Deterministic tests never stage, publish, approve, or create tags or releases.
