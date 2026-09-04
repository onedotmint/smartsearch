# Providers and configuration

Provider selection is internal to the v1 commands. Direct discovery and fetch providers are selected by role, with same-role fallback; provider-branded CLI commands do not
exist.

## Roles

| Role | Purpose |
| --- | --- |
| Discovery | Supplies candidates for `search` and `research` |
| Fetch | Reads a known URL for `read` and evidence stages |
| Optional model | Supports configured provider-specific enhancements; not required for the evidence core |

`setup` prompts only for the supported discovery keys `BRAVE_API_KEY`,
`EXA_API_KEY`, and `TAVILY_API_KEY`. Reader keys, model keys, and other
provider-specific settings (including `JINA_API_KEY`, `ZHIPU_API_KEY`, and
`FIRECRAWL_API_KEY`) must be supplied through environment variables or supported
local configuration; they are not setup prompts. Exact keys and provider
availability vary by installation. Missing credentials produce a structured
result, not a silent provider substitution.

Jina Reader is a fetch provider, not search. Anonymous Reader may support a
known public URL; a key enables keyed features. Discovery snippets remain
unverified until `read` obtains page evidence.

## Safety and diagnostics

No provider call is made by package installation, help, or offline tests.
Credentials are redacted at output boundaries and must never be copied into
logs, documentation, or bug reports. Real provider checks require explicit
network access and credentials. Use mocked smoke checks for deterministic CI.

The v1 runtime has no public provider-management command tree. Configure
discovery keys through `setup`, configure reader/model/provider-specific values
through environment variables or supported local configuration, and invoke only
`search`, `read`, and `research`.

See [getting started](getting-started.md) for the first setup flow and [the
migration guide](migration.md) for removed pre-v1 surfaces.
