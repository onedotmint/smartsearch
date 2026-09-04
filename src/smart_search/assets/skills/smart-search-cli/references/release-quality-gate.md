# Quality Gate And Release

## Offline quality gate

Run these checks from a source checkout before release:

```sh
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest tests -q
npm test
npm pack --dry-run
(cd integrations/pi && npm run typecheck && npm test && npm pack --dry-run)
git diff --check
```

They must not call live providers, publish packages, push tags, or create a
GitHub release. Inspect package contents from a temporary dry-run result and do
not retain generated archives.

## Stable release

A release starts as a single reviewed release-intent commit on `main` that
synchronizes the root `package.json`, `package-lock.json`, the Pi
`integrations/pi/package.json` and its lockfile, and `pyproject.toml` to one
stable `x.y.z` version and adds a non-empty `.github/releases/vX.Y.Z.md` notes
file. Pushing that commit to `main` triggers the guarded publisher
(`.github/workflows/publish-npm.yml`): it proves both manifest version fields
changed together from the pushed commit's parent, validates the synchronized
metadata and notes, runs the offline quality gate, checks fail-closed npm
registry state, and only then publishes the root package, then the Pi package,
with provenance to `latest`. The `vX.Y.Z` Git tag and stable GitHub release are
created only after both exact versions verify on `latest`, bound to the
triggering commit SHA.

Ordinary documentation or code pushes without a synchronized version change are
safe no-ops. One-sided version bumps, metadata drift, missing or empty notes,
prerelease versions, and mismatched existing tags stop before any publication.

Manual dispatch remains available only as recovery: it accepts a full commit
SHA reachable from `origin/main`, derives the version from that commit, and
follows the same validation and publication gates. Branch names, short SHAs,
and mutable refs are rejected.

npm versions are immutable. If publication is partial, verify registry state
before deciding whether the unpublished package can use the same exact version;
otherwise fix forward with a new patch release. Create or update GitHub release
notes only after both package versions are confirmed. Never publish tags or
releases as a side effect of deterministic tests.
