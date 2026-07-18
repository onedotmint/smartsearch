# Generic Agent Adapter

Use the shared `smart-search` CLI contract from the parent skill.

- Resolve `smart-search` from the user's PATH.
- Use `--format json` for machine callers; parse stdout as one JSON value.
- Keep stderr for logs and diagnostics.
- Do not add client-specific provider fallbacks or bypass the shared evidence rules.
