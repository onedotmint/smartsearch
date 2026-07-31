# Search vs Deep Research vs Research

These commands share the same CLI and provider registry, but they have different execution boundaries.

| Command | Role | Provider behavior | Output |
| --- | --- | --- | --- |
| `search` | Fast live answer | Calls the selected live capabilities | Answer, sources, routing, and provider attempts |
| `deep` | Offline planner | Does not call providers, fetch pages, or run `doctor` | `research_plan` with ordered steps |
| `research` | Live executor | Runs discovery, fetch/read, gap check, and synthesis | Evidence bundle, citations, gaps, and final answer |

## Offline planner

```sh
smart-search deep "Compare Responses API web_search with Chat Completions search" --budget deep --format json
```

`smart-search deep` is the public offline planner command. It is not an executor and does not change default `smart-search search` behavior. Deep Research is not a fixed topic recipe system: product comparison, technical docs, news, policy, market research, claim verification, and URL-first prompts are user language, not required schema enums.

The planner emits a `research_plan` with these stable sections:

- `mode`: always `deep_research`;
- `query_mode`: always `deep`;
- `question`: the user's research question;
- `trigger_source`: normally `explicit_cli`;
- `difficulty`: `standard` or `high`;
- `intent_signals`: recency, docs/API intent, known URL, claim risk, source authority, and cross-validation need;
- `decomposition`: subquestions for complex research;
- `capability_plan`: selected capability needs;
- `evidence_policy`: default `fetch_before_claim`;
- `preflight`: `doctor` guidance;
- `steps`: ordered CLI command steps;
- `gap_check`: how the agent verifies missing evidence;
- `final_answer_policy`: how to cite fetched evidence;
- `usage_boundary`: the user-facing distinction between `search`, `deep`, and `research`.

Allowed planned tools are `search`, `exa-search`, `exa-similar`, `zhipu-search`, `context7-library`, `context7-docs`, `fetch`, and `map`. `doctor` is a `preflight` action, not a `steps[]` item. Plans must not require fixed topic recipe ids. Even `--budget quick` plans retain at least one `fetch` step when evidence policy requires it.

The plan's `steps[].command` and `steps[].output_path` are one contract. Prefer PowerShell-safe quoted commands when a plan is intended for Windows. An evidence directory shown in a plan may be a platform temporary directory; explicit persistent examples are user-controlled paths, not the runtime default.

## Live executor

```sh
smart-search research "Compare Responses API web_search with Chat Completions search" --budget deep --fallback auto --format json
```

`smart-search research` is the public live executor command. It runs:

```text
plan -> discover -> fetch/read -> gap check -> evidence-only synthesis
```

The executor uses capability-based orchestration and provider advantage routing:

- Context7 is preferred for library/API/framework docs; Exa remains the official-domain, paper, product, and trusted-site path.
- Zhipu Web Search API is preferred for Chinese, domestic, current, policy, and announcement searches.
- Zhipu Coding Plan MCP remains a separate quota route through `web_search_prime` and `webReader`.
- Jina is favored for known public URLs, PDFs, and arXiv extraction when its key is configured.
- Firecrawl is favored for JS-heavy, dynamic, browser-like, OCR/PDF, or robust fallback extraction.
- AnySearch participates only when vertical intent is explicit.

`research --fallback auto` permits same-capability fallback. `--fallback off` tries only the first eligible provider in each capability. Research provider overrides can reorder or disable providers only within their declared capabilities.

Research JSON includes `final_answer`, `content`, `citations`, `evidence_items`, `gap_check`, `provider_attempts`, `fallback_used`, `degraded`, `response_mode`, `synthesis_enabled`, `route_policy_version`, and `evidence_dir`. The additive `evidence_bundle` groups `discovery_candidates`, `fetched_evidence`, `sources`, `citations`, `gaps`, and provider attempts. Prefer `research run` for Agent workflows: it defaults to evidence-only mode with empty answer fields and leaves final writing to the host. Bare `research` and `research run --synthesize` reuse evidence-only synthesis. If synthesis fails, fetched evidence and citations remain in the result with `synthesis_error` and degraded gaps; the executor does not silently search or fetch again.

Good smoke prompts include:

```sh
smart-search deep "深度搜索一下最近的比特币行情" --format json
smart-search deep "OpenAI Responses API web_search 和 Chat Completions 联网搜索怎么选" --budget deep --format json
smart-search deep "帮我核验这个说法是真是假：某某工具已经完全替代 Tavily 做 AI 搜索了" --format json
smart-search deep "https://example.com/source" --format json
```

## Related contracts

- [Evidence policy](evidence.md) defines when a candidate becomes usable evidence.
- [Routing](routing.md) defines `intent_signals`, capability selection, and degraded routing.
- [Command reference](../commands.md) lists aliases, output flags, and skill lifecycle commands such as `smart-search skills status` and `smart-search skills update`.
