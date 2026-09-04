# Provider Routing

Provider selection is internal to the v1 commands. Providers have two public
roles:

- discovery providers supply candidates for `search` and `research`;
- fetch providers read known URLs for `read` and the evidence stages.

Fallback is allowed only within the same role. Provider-branded commands,
capability/control command trees, route selectors, and provider raw payloads are
not public interfaces.

## Configuration

Use `smart-search setup --format json` for first-time local configuration, or
provide supported keys through the environment for CI. Common keys include
`BRAVE_API_KEY`, `EXA_API_KEY`, `TAVILY_API_KEY`, `JINA_API_KEY`,
`FIRECRAWL_API_KEY`, and the documented Zhipu/OpenAI-compatible settings. Exact
availability depends on the installation. Missing credentials produce
structured errors or degraded results; they are not silently replaced by a
different command.

Jina Reader is fetch-only, not search. Discovery snippets remain unverified
until `read` obtains page evidence.

## Evidence and safety

`search` candidates are not claim-level evidence. `read` bounds returned content
and reports truncation in `data`. `research` keeps candidates, evidence,
citations, gaps, and attempts distinct. Credentials are redacted at output
boundaries and must never appear in logs or documentation.

Package installation, help, and deterministic tests make no provider calls.
Live provider checks require explicit network access and credentials.
