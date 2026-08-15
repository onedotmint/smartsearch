# Contributing

Smart Search is a Python CLI distributed through an npm wrapper and an AI-agent skill. Contributions should keep the direct Python path, npm path, and public skill contract consistent.

## Before changing files

Read the relevant section of [Development](docs/development.md), then inspect the current command and provider behavior. Treat executable code and tests as the behavior source of truth. Do not infer a provider capability from a README example alone.

For user-visible changes, check all of these boundaries:

- `README.md` and `README.zh-CN.md` for the product entry;
- `docs/` for detailed public usage;
- `skills/smart-search-cli/` for the AI-agent contract;
- `src/smart_search/assets/skills/smart-search-cli/` for the packaged skill copy;
- `tests/` for deterministic contract coverage;
- `package.json` and npm scripts for package behavior.

## Local setup

```sh
python -m pip install -e ".[dev]"
npm ci
```

Provider keys are optional for unit, regression, and mock smoke tests. Keep local configuration outside the repository and never commit credentials.

## Verification

Run the narrowest relevant checks first, then the complete set:

```sh
python -m compileall -q src tests
python -m pytest tests -q
python -m smart_search.cli dev regression
python -m smart_search.cli dev smoke --mock --format json
npm test
npm pack --dry-run
git diff --check
```

Live provider checks are useful only when the change affects a configured provider path. State the provider, environment, and limitation in the pull request.

## Documentation changes

Keep the two README files structurally aligned, but write idiomatic English and Chinese rather than literal translations. Put detailed command flags in `docs/commands.md`, provider keys and fallback rules in `docs/providers.md`, and research/evidence semantics in `docs/concepts/`.

## JSON compatibility policy

The V2 Evidence, V3 Control Plane, and Research Workflow envelopes are stable machine contracts that agents depend on. Treat them accordingly:

- Removing an existing field or changing its type or semantics is a **major breaking change** and must be announced as such.
- **Additive optional fields are compatible**, but the strict serializers emit them, so any additive field updates the frozen golden baselines in `tests/fixtures/json_compat_baselines.py` in the same commit.
- Input unknown fields are rejected by strict schema validation; do not silently ignore them.
- Output shapes change only through a deliberate schema version bump with the golden fixtures, serializers, and docs updated together.
- Run `tests/test_json_compat_baselines.py` (byte-wise serializer snapshots for V2 and Workflow) and the contract tests for any envelope change.

When changing the public skill source, synchronize the packaged asset tree and run the regression test that compares both trees. When adding a public Markdown file, add a README link and include it in the npm package whitelist when the link must work from an installed package.

## Pull requests

A pull request should state:

- what user-visible behavior or documentation changed;
- which files define the new contract;
- deterministic checks that passed;
- any live-provider checks that were not run and why.

Keep commits focused. Do not include local config files, generated evidence, API keys, or unrelated formatting churn.
