# Contributing

Smart Search is a Python CLI distributed through an npm wrapper and an
AI-agent Skill. Contributions should keep the direct Python path, npm path, and
public v1 Skill contract consistent.

## Before changing files

Read the relevant section of [Development](docs/development.md), then inspect
the current command and provider behavior. Treat executable code and tests as
the behavior source of truth.

For user-visible changes, check these boundaries:

- `README.md` and `README.zh-CN.md` for the product entry;
- `docs/` for detailed public usage;
- `skills/smart-search-cli/` for the AI-agent contract;
- `src/smart_search/assets/skills/smart-search-cli/` for the packaged copy;
- `tests/` for deterministic contract coverage;
- `package.json` and npm scripts for package behavior.

## Local setup

```sh
python -m pip install -e ".[dev]"
npm ci
```

Provider keys are optional for unit and mock tests. Keep local configuration
outside the repository and never commit credentials.

## Verification

Run the narrowest relevant checks first, then the complete set:

```sh
python -m compileall -q src tests
python -m pytest tests -q
npm test
npm pack --dry-run
(cd integrations/pi && npm run typecheck && npm test && npm pack --dry-run)
git diff --check
```

Live provider checks are useful only when the change affects a configured
provider path. State the provider, environment, and limitation in the pull
request.

## Documentation changes

Keep the two README files structurally aligned, but write idiomatic English and
Chinese rather than literal translations. Put detailed command flags in
`docs/commands.md`, provider keys and fallback rules in `docs/providers.md`, and
research/evidence semantics in `docs/concepts/`.

## JSON compatibility policy

The v1 JSON envelope is `{version, operation, status, data, attempts, warnings,
error}`. Treat changes to these fields as a major contract change and update
tests and docs together. Provider payloads, credentials, and removed V2/V3/
Workflow envelopes are not public interfaces.

When changing the public Skill source, synchronize the packaged asset tree and
run the parity regression test. When adding a public Markdown file, add a README
link and include it in the npm package whitelist when the link must work from an
installed package.

## Pull requests

A pull request should state the user-visible change, contract source files,
deterministic checks that passed, and any live-provider checks not run.
Keep commits focused and exclude local config, generated evidence, API keys, and
unrelated formatting churn.
