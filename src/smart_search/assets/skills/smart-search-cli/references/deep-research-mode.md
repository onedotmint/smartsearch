# Deep Research Mode

## Deep Research Skill Contract

This contract keeps Deep Research capability-based and evidence-first:
`smart-search research plan` plans offline, `smart-search research run` executes
the staged evidence workflow, and claim-level conclusions require fetched
evidence. The typed plan binds executable operations and request budgets, while
the workflow keeps discovery candidates separate from fetched/read evidence.
The plan is an offline artifact only: it never embeds shell commands, output
paths, or an evidence directory, and the workflow records logical artifacts
without projecting files.

## Table of Contents

- Trigger and boundary
- Offline planner and live executor
- Planner shape
- Operation contract
- Capability boundaries
- Live executor output
- Closeout lessons
- Smoke coverage

## Trigger And Boundary

Use Deep Research Mode when the user asks for `深度搜索`, `深度调研`, `深入搜索`, `deep search`, `deep research`, multi-source verification, cross-checking, serious review, or selection/comparison research. This is a capability-based orchestration workflow.

Do not select a fixed topic recipe. Market, product, technical docs, news, policy, claim-checking, and URL-first prompts are examples of user language, not schema modes. Deep Research must not require fixed topic recipe ids such as `current_market_research`, `product_comparison_research`, `technical_docs_research`, `news_or_policy_research`, `claim_verification_research`, or `url_first_research`; fixed topic recipe ids are not required schema.

Deep Research does not change default `smart-search search` behavior and does not depend on an MCP session. It must not change default `smart-search search` behavior.

## Offline Planner And Live Executor

- `smart-search research plan "question"` is the public offline planner command and a public planner entrypoint, not an executor. It does not call providers, run `doctor`, or fetch pages by default. It returns the typed plan inside a plan-only workflow result (operation `research.run`, empty execution collections).
- `smart-search research run "question"` is the public live executor command and public live executor entrypoint. It executes plan -> discover -> fetch/read -> gap check and returns the strict workflow result; the host agent writes the final answer.
- Before manual execution, run `smart-search research plan "question" --format json` and use the returned `plan` as your planning artifact.
- Use `smart-search research run "question" --format json` when the user wants the CLI to run live Deep Research end to end instead of only planning.

Default orchestration:

1. Run `smart-search doctor status --format json` as the local preflight when configuration is uncertain. Use `doctor probe` only for an explicit live aggregate check.
2. Call `smart-search research plan "question" --format json` to create the offline typed plan.
3. Inspect the ordered `operations` list (`id`, `operation`, `input`, `constraints`, `depends_on`); do not choose fixed topic recipe ids.
4. Execute the planned discovery operations as `smart-search search "query" --format json` steps (docs/API, Chinese/current, or official-domain routes are selected internally by capability), or planned `map` steps (`smart-search map "url" --format json`) when site structure is needed.
5. Use `smart-search fetch "url" --format json` on key URLs before making claim-level statements.
6. Run `gap_check`: if an important claim lacks fetched evidence, fetch another source or mark the claim/source as unverified.

Default evidence policy is `fetch_before_claim`: key claims in the final answer must be supported by fetched page text. Treat `evidence.candidates` as discovery candidates until the relevant URL has been fetched. Final answers should include fetched evidence, unverified candidate sources, and key commands used.

## Planner Shape

`research plan` returns a plan-only Workflow result whose `plan` member is the
typed plan. It carries exactly:

```json
{
  "schema_version": "research-plan-1",
  "operations": [
    {
      "id": "source-discovery-1",
      "operation": "source_discovery",
      "input": {
        "query": "user question"
      },
      "constraints": {
        "max_results": 3
      },
      "depends_on": []
    }
  ]
}
```

- `operations` is an ordered executable plan. Each operation has `id` (unique within the plan), `operation` (one of the executable workflow operations such as `source_discovery`, `docs_discovery`, `content_fetch`, or `site_discovery`), `input` (the query or candidate references), `constraints` (bounded request budgets such as `max_results` or `max_items`), and `depends_on` (ids of operations that must complete first).
- The plan never contains shell commands, output paths, provider raw payloads, answer fields, or answer-generation flags. `--evidence-dir`, `--output`, `--force`, `--fallback`, and answer-generation flags are rejected by the workflow family before any owner work.
- The workflow result envelope is the same for plan and run: `schema_version`, `ok`, `status`, `command`, `operation`, `plan`, `stages`, `evidence`, `citations`, `gaps`, `attempts`, `artifacts`, `error`, and `meta`. `research plan` returns it with empty `stages`/`evidence`/`citations`/`gaps`/`attempts`/`artifacts`.

## Operation Contract

Planned operations map to existing CLI commands only:

- `source_discovery` / `docs_discovery` -> `smart-search search "query" --format json`
- `content_fetch` -> `smart-search fetch "url" --format json`
- `site_discovery` -> `smart-search map "url" --format json`

`doctor status` is a preflight action, not a planned operation. Simple plans
may have a single discovery plus fetches; complex plans use staged discovery
with dependencies and bounded fetch batches. The `depends_on` links must stay
valid: a fetch must depend on the discovery operation whose candidate
references it feeds. The `depends_on` links must stay valid and each retained
operation must keep its dependencies within the same plan.

## Capability Boundaries

- `search`: broad discovery and synthesis through the generic evidence command; use returned `routing`, `attempts`, and `degradation` as orchestration signals, not as claim proof. Provider selection inside `search` is internal and intent-driven (for example `docs_search` providers for library/API intent and `web_search` providers for Chinese/current topics).
- `fetch`: page-content evidence. Key claims require fetched page text under `fetch_before_claim`.
- `map`: site structure exploration before many fetches from one site; not claim evidence by itself.
- The canonical V2 commands accept only `--format json|markdown|content`; V1 options such as `--extra-sources`, `--timeout`, `--validation`, and `--output` are rejected before any provider work.

## Live Executor Output

Use the Agent-facing workflow command:

```powershell
smart-search research run "question" --budget deep --format json
```

`research run` returns the strict workflow result: plan, stages, admitted
evidence, citations, gaps, attempts, and logical artifact records while no
answer is authored by the tool. The host agent writes the final prose.
Answer-generation flags, `--output`, `--force`, `--fallback`, and
`--evidence-dir` are rejected by the workflow family before any owner work.
Bare `research`, `rs`, `deep`, and `dr` are removed spellings and fail with
the workflow family's strict error.

Dynamic routing may reorder providers only inside the same capability. Every
attempt must record capability, provider, status, error type, latency, and
result count.

`research run` output has the exact workflow shape: `schema_version`, `ok`,
`status`, `command`, `operation`, `plan`, `stages`, `evidence`, `citations`,
`gaps`, `attempts`, `artifacts`, `error`, and `meta`. It never includes
answer fields, shell commands, or output paths. Citations reference admitted fetched evidence only; discovery
candidates are never cited as proof. If providers are exhausted or evidence
cannot close, the workflow returns structured gaps rather than inventing
missing claims.

Research provider advantage routing:

- Context7: library/API/framework docs resolution and docs retrieval.
- Exa: official domains, papers, product/company pages, date/domain-filtered low-noise discovery, and adjacent-source discovery.
- Zhipu REST: Chinese, domestic, current, policy, and announcement searches.
- Zhipu MCP: separate Coding Plan quota route through `web_search_prime` and `webReader`.
- Tavily: broad source discovery and site map.
- Jina: known public URL, PDF, and arXiv clean extraction; ReaderLM-v2 requires `JINA_API_KEY`.
- Firecrawl: robust fetch fallback, JS-heavy/dynamic pages, browser-like extraction, OCR/PDF/structured extraction.
- AnySearch: experimental vertical capability only; it does not join research routes (no generic Evidence owner).

Safe research overrides are `SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS` and `SMART_SEARCH_RESEARCH_DISABLED_PROVIDERS`. They may reorder or disable providers only inside capabilities the provider already supports; they must not move a provider across capability boundaries.

## Closeout Lessons

- Budget limits must not break evidence policy. Even `--budget quick` plans must retain at least one `content_fetch` operation when claim-level conclusions are expected, and retained operations must keep valid `depends_on` links.
- If a smoke issue is found, fix the affected docs/code/tests and rerun the affected smoke until it passes or is proven to be an external provider blocker.
- Final answers assembled from discovery-only output should list unverified candidates rather than presenting them as supported claims.

## Smoke Coverage

Deep Research smoke matrix for workflow maintenance is mock-full plus live-limited. Mock-full coverage should cover trigger phrases, normal search requests that should not trigger Deep Research, required `research plan` fields, the executable operation whitelist, `fetch_before_claim`, capability boundaries, simple current prompts such as `深度搜索一下最近的比特币行情`, docs/API prompts, claim-verification prompts, user-provided URL fetch-first flows, missing-provider failure guidance, research provider advantage routing, same-capability research fallback, and the rule that fixed topic recipe ids are not required schema.

Live-limited coverage should run `doctor status`, one broad `search`, and one `fetch` only when real keys are available and the user expects live checks. Add one small `research run` smoke when configured keys make it stable.

Standard user-facing Deep Research tests:

```powershell
smart-search research plan "深度搜索一下最近的比特币行情" --format json
smart-search research plan "OpenAI Responses API web_search 和 Chat Completions 联网搜索怎么选" --budget deep --format json
smart-search research plan "帮我核验这个说法是真是假：某某工具已经完全替代 Tavily 做 AI 搜索了" --format json
smart-search research plan "https://example.com/source" --format json
```
