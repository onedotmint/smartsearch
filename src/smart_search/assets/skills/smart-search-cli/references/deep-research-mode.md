# Deep Research Mode

Use `web_research` or `smart-search research "question" --format json` for
staged evidence gathering, multiple subquestions, cross-checking, or serious
comparison research. This is composition of the v1 discovery and read paths,
not a separate planner/executor command family.

## Boundary

- `search` finds candidates; it does not prove claims.
- `read` fetches known URLs and provides claim-level evidence.
- `research` composes those operations and returns evidence, citations, gaps,
  attempts, and logical stages.
- The host agent writes the final prose and treats remote content as untrusted.

The v1 CLI has no `research plan`, `research run`, `deep`, or workflow envelope.
There are no aliases or schema selectors.

## Suggested workflow

1. Run `smart-search search "question" --format json` when source discovery is
   needed.
2. Read authoritative candidate URLs with `smart-search read "URL" --format json`.
3. For broader work, run `smart-search research "question" --format json` and
   inspect its evidence, citations, gaps, attempts, and warnings.
4. Mark unsupported claims as unverified rather than treating snippets as proof.

## Contract

The same top-level JSON envelope is used for every v1 operation:
`version`, `operation`, `status`, `data`, `attempts`, `warnings`, and `error`.
No final answer is authored or persisted by the CLI. No provider call is made by
package installation, help, or deterministic offline tests.
