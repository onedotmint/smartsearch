# CLI Core

## Entrypoints

- `smart-search` is the primary CLI and should resolve from the user's PATH.
- The public commands are `setup`, `search`, `read`, and `research`.
- `--format json` is the only supported output format and the stable machine
  interface. The CLI does not promise `--version`.
- Private API keys belong in local setup or environment variables, never in
  committed files or logs.

## Commands

- `smart-search setup [--mode fast|balanced|research] --format json` configures
  local discovery providers without making provider requests.
- `smart-search search QUERY [--mode fast|balanced|research] --format json`
  discovers and ranks source candidates.
- `smart-search read URL [--max-chars N] --format json` fetches bounded evidence
  from a known URL.
- `smart-search research QUERY --format json` composes discovery and reads,
  returning evidence, citations, gaps, and logical stages. The host agent writes
  the final answer.

## JSON contract

Every operation returns one v1 envelope with these top-level fields:

```json
{"version":1,"operation":"search","status":"complete","data":{},"attempts":[],"warnings":[],"error":null}
```

`error` is `null` or a structured error object. Attempts and warnings are safe,
redacted summaries. Provider payloads and credentials are not public output.

## Agent and Pi tools

The native Pi package and the bundled Skill expose exactly `web_search`,
`web_read`, and `web_research`. Use `web_search` for discovery, `web_read` for
a known URL, and `web_research` for staged evidence gathering.

## Removed surface

V2/V3/Workflow commands, envelopes, schema selectors, aliases, and Python
facades are removed. Callers must migrate rather than rely on runtime aliases.

## Exit codes

- `0`: complete or degraded operation
- `2`: invalid command or argument
- `3`: configuration error
- `4`: provider or upstream error
- `5`: unexpected internal error

If the CLI is unavailable, report the blocker and recovery steps instead of
silently switching to another web-search route.
