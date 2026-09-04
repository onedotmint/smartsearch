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

Both `@onedotmint/smart-search` and `@onedotmint/pi-smart-search` release as
`1.0.0` to npm dist-tag `latest`, with Git tag `v1.0.0` and a stable GitHub
release. Validate source and both tarballs, publish the root package, publish
the Pi package, then create the GitHub release. The workflow is a publishing
workflow, not an offline rehearsal; dispatch it only with explicit authorization
and controlled credentials.

npm versions are immutable. If publication is partial, verify registry state
before deciding whether the unpublished package can use the same exact version;
otherwise fix forward with a new patch release. Create GitHub release notes only
after both package versions are confirmed. Never publish tags or releases as a
side effect of deterministic tests.
