# Search, read, and research

v1 separates discovery from evidence:

| Command | Boundary | Output |
| --- | --- | --- |
| `search QUERY` | Find candidate sources | v1 envelope with candidates |
| `read URL` | Fetch known page content | v1 envelope with evidence |
| `research QUERY` | Compose discovery, reads, and gap checks | v1 envelope with evidence and citations |

```sh
smart-search search "current API changes" --format json
smart-search read "https://example.com/source" --format json
smart-search research "Compare two current API designs" --format json
```

Candidates are not claim-level proof. Read authoritative sources before making
important claims. Research does not generate or persist the final answer; the
host agent owns synthesis, permissions, and citation presentation.

All three commands use the same stable v1 JSON contract. There is no selector,
legacy envelope, answer-generation switch, or compatibility alias.

For provider keys and local setup, see [the provider guide](../providers.md) and
[getting started](../getting-started.md).
