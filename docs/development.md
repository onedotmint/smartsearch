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

## v1.0.0 release

Both `@onedotmint/smart-search` and `@onedotmint/pi-smart-search` are version
`1.0.0`, published publicly to npm `latest`. The Git tag and stable GitHub
release are `v1.0.0` (not prerelease). Validate source and both tarballs, publish
the root package, publish the Pi package, then create the GitHub release.
Publication is an explicit manual operation; the workflow is not an offline
dry run and must only be dispatched with release credentials when publication
is authorized.

Do not run publication, tag pushes, or release creation without explicit user
authorization. npm versions are immutable: if publication is partial, verify
registry state and fix forward; use a new patch version when the immutable
version already exists. Before publication, revert the candidate commit to
return to the previous release.
