# Search vs Deep Research vs Research

These commands share the same CLI and provider registry, but they have different execution boundaries.

| Command | Role | Provider behavior | Output |
| --- | --- | --- | --- |
| `search` | Fast live answer | Calls the selected live capabilities | Answer, sources, routing, and provider attempts |
| `research plan` | Offline planner | Does not call providers, fetch pages, or run `doctor` | Workflow plan-only result with typed `plan` |
| `research run` | Live executor | Runs discovery, fetch/read, and gap check | Strict workflow result: plan, evidence, citations, gaps, attempts, artifacts |

## Offline planner

```sh
smart-search research plan "Compare Responses API web_search with Chat Completions search" --budget deep --format json
```

`smart-search research plan` is the public offline planner command. It is not an executor and does not change default `smart-search search` behavior. Deep Research is not a fixed topic recipe system: product comparison, technical docs, news, policy, market research, claim verification, and URL-first prompts are user language, not required schema enums.

The plan-only workflow result carries a typed `plan` with exactly two stable
members:

- `schema_version`: the typed plan contract version;
- `operations`: an ordered executable list; each operation has `id` (unique
  within the plan), `operation` (one of `source_discovery`, `docs_discovery`,
  `content_fetch`, or `site_discovery`), `input`, `constraints`, and
  `depends_on`.

The plan never contains shell commands, output paths, evidence directories, or
answer fields. Allowed operations map to the canonical generic commands
`search`, `fetch`, and `map` only. `doctor status` is a preflight action, not a
planned operation. Plans must not require fixed topic recipe ids. Even
`--budget quick` plans retain at least one `content_fetch` operation when
evidence policy requires it.

## Live executor

```sh
smart-search research run "Compare Responses API web_search with Chat Completions search" --budget deep --format json
```

`smart-search research run` is the public live executor command. It runs:

```text
plan -> discover -> fetch/read -> gap check
```

The executor uses capability-based orchestration and provider advantage routing:

- Context7 is preferred for library/API/framework docs; Exa remains the official-domain, paper, product, and trusted-site path.
- Zhipu Web Search API is preferred for Chinese, domestic, current, policy, and announcement searches.
- Zhipu Coding Plan MCP remains a separate quota route through `web_search_prime` and `webReader`.
- Jina is favored for known public URLs, PDFs, and arXiv extraction when its key is configured.
- Firecrawl is favored for JS-heavy, dynamic, browser-like, OCR/PDF, or robust fallback extraction.
- AnySearch is an experimental vertical capability with no generic Evidence owner and does not participate in the research executor.

Research provider overrides can reorder or disable providers only within their declared capabilities. The workflow family rejects `--fallback`, answer-generation flags, `--evidence-dir`, `--output`, and `--force` before any owner work.

Research JSON uses the workflow envelope: `schema_version`, `ok`, `status`, `command`, `operation=research.run`, `plan`, `stages`, `evidence`, `citations`, `gaps`, `attempts`, `artifacts`, `error`, and `meta`. It contains no answer fields; the host agent writes the final prose from admitted evidence. `research run` leaves final writing to the host. Bare `research`, `rs`, `deep`, and `dr` are removed spellings and fail with the workflow family's strict error; the executor never silently searches or fetches again.

Good smoke prompts include:

```sh
smart-search research plan "深度搜索一下最近的比特币行情" --format json
smart-search research plan "OpenAI Responses API web_search 和 Chat Completions 联网搜索怎么选" --budget deep --format json
smart-search research plan "帮我核验这个说法是真是假：某某工具已经完全替代 Tavily 做 AI 搜索了" --format json
smart-search research plan "https://example.com/source" --format json
```

## Related contracts

- [Evidence policy](evidence.md) defines when a candidate becomes usable evidence.
- [Routing](routing.md) defines `intent_signals`, capability selection, and degraded routing.
- [Command reference](../commands.md) lists output flags and skill lifecycle commands such as `smart-search dev skills status` and `smart-search dev skills update`.
