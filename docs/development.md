# Development and release

Smart Search v1 is a Python CLI with a root npm wrapper and an independent Pi
package. Public commands are `setup`, `search`, `read`, and `research`; keep
README, docs, Skills, and package metadata aligned.

## Offline verification

```sh
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest tests -q
npm test
npm pack --dry-run
(cd integrations/pi && npm run typecheck && npm test && npm pack --dry-run)
git diff --check
```

These checks must not call live providers or publish packages. Inspect tarball
contents in a temporary directory and keep generated archives out of commits.

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
