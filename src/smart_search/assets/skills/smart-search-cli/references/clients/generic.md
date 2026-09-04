# Generic Agent Adapter

Use the shared v1 `smart-search` CLI contract from the parent Skill.

- Resolve `smart-search` from the user's PATH.
- Use `--format json` for machine callers and parse stdout as one JSON value.
- Keep stderr for logs and diagnostics.
- Do not add client-specific provider fallbacks or bypass evidence rules.
