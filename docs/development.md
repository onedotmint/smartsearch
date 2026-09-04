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
`main` triggers `.github/workflows/publish-npm.yml`: it proves both manifest
version fields changed together from the pushed commit's parent, validates the
synchronized metadata and notes, runs the offline checks, checks fail-closed
npm registry state, publishes the root package, publishes the Pi package (both
with provenance to `latest`), verifies both exact versions, and only then
creates or updates the `vX.Y.Z` Git tag and stable GitHub release bound to the
triggering commit SHA.

Ordinary documentation or code pushes without a synchronized version change do
not publish. One-sided version bumps, metadata drift, missing or empty notes,
prerelease versions, registry uncertainty, and mismatched existing tags stop
before any side effect. Manual dispatch is recovery-only: a full commit SHA
reachable from `origin/main`, with the version derived from that commit; branch
names, short SHAs, and mutable refs are rejected.

npm versions are immutable: if publication is partial, verify registry state
and fix forward; use a new patch version when the immutable version already
exists. Deterministic tests never publish or create tags or releases.
