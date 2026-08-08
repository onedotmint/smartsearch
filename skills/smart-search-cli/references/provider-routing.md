# Provider Routing

## Table of Contents

- Source provenance
- Intent routing diagnostics
- Provider boundaries
- Provider output details
- Routing heuristics
- Maintenance guardrails

## Source Provenance

- The V2 evidence envelope separates discovery candidates from admitted evidence: `evidence.candidates` (`{id, resource, provider, title, snippet}`) are unverified discovery results, and `evidence.items` (`{id, resource, provider, title, content}`) are fetched/read evidence.
- Discovery candidates are never automatic evidence for a claim; fetch the URL before citing it.
- The V2 result summary is `result.total` and `result.items` (ids only); all provenance lives in the `evidence`, `routing`, `attempts`, and `degradation` collections.

## Intent Routing Diagnostics

`smart-search dev route-explain "query"` explains which capabilities the unified `IntentRouter` selected without running providers. It is the right command when the user asks why a prompt triggered docs/current/fetch/vertical routing.

The router output includes `query`, `executed_search`, `provider_selection`, `intent_router_mode`, `required_capabilities`, `intent_signals`, `confidence`, `router_engines_used`, `reasons`, `validation_level`, `missing_capabilities`, and `supplemental_paths` inside the V3 `result`.

Intent router rules:

- `SMART_SEARCH_INTENT_ROUTER=hybrid|rules|off`, default `hybrid`. `SMART_SEARCH_INTENT_ROUTER` accepts `hybrid`, `rules`, and `off`.
- Optional semantic routing uses `INTENT_EMBEDDING_API_URL`, `INTENT_EMBEDDING_API_KEY`, `INTENT_EMBEDDING_MODEL`, `INTENT_EMBEDDING_THRESHOLD`, and `INTENT_EMBEDDING_MARGIN`.
- Normal users should use the Qwen3-Embedding-8B preset: SiliconFlow endpoint `https://api.siliconflow.cn/v1/embeddings`, model `Qwen/Qwen3-Embedding-8B`, threshold `0.475`, and margin `0.053`.
- Setting the Qwen3-Embedding-8B preset via `config set` auto-fills threshold/margin when no explicit values are already configured.
- Embedding thresholds are model-specific. Run `smart-search dev route-calibrate --models "Qwen/Qwen3-Embedding-8B" --format json` after changing model/endpoint or refreshing the real-query calibration set; use semantic-only Macro-F1 as the primary selector and full-route Macro-F1 as validation.
- Optional model classification uses `INTENT_CLASSIFIER_API_URL`, `INTENT_CLASSIFIER_API_KEY`, and `INTENT_CLASSIFIER_MODEL`.
- `INTENT_ROUTER_TIMEOUT_SECONDS` defaults to `8`.
- Missing or failing embeddings/classifier degrade to rules and should not fail ordinary `search`.
- Semantic matches add a capability only when the top score reaches `INTENT_EMBEDDING_THRESHOLD` and the top-vs-second gap reaches `INTENT_EMBEDDING_MARGIN`; ambiguous semantic matches are recorded as signals only.
- The router returns capabilities only: `docs_search`, `web_search`, `web_fetch`, and `vertical_search`.
- Classifier output cannot select providers. Unknown capability names and provider names are ignored.
- `search` and `research run` use the unified router. `research plan` remains an offline planner and must not call embeddings or classifier components.

## Provider Boundaries

- `search` builds `main_search` from configured peer providers: `XAI_API_KEY` for xAI Responses and `OPENAI_COMPATIBLE_API_URL` + `OPENAI_COMPATIBLE_API_KEY` for OpenAI-compatible Chat Completions.
- `search` uses unified `IntentRouter` output to populate `required_capabilities` and `supplemental_paths`; provider execution still follows capability-first fallback.
- `research run` reuses the same `IntentRouter` before provider-advantage ordering.
- `research plan` uses offline rules/local signals only and must not call remote embeddings or classifier components.
- Official xAI uses the Responses API `/responses` route through `XAI_*`. Compatible relays/gateways use Chat Completions `/chat/completions` through `OPENAI_COMPATIBLE_*`.
- `OPENAI_COMPATIBLE_STREAM=true` sets `stream=true` only for OpenAI-compatible main search and provider-side `fetch`; it is a relay compatibility switch and does not affect xAI Responses, URL description, or source ranking. There is no `--stream`/`--no-stream` CLI option on the canonical V2 surface.
- Legacy `SMART_SEARCH_API_URL`, `SMART_SEARCH_API_KEY`, `SMART_SEARCH_API_MODE`, `SMART_SEARCH_MODEL`, and `SMART_SEARCH_XAI_TOOLS` are unsupported config keys.
- xAI Responses mode may use only `XAI_TOOLS=web_search,x_search` and a subset of those tools.
- Chat Completions mode must not send xAI `web_search` / `x_search` tools or legacy `search_parameters`; xAI Chat Completions Live Search is deprecated.
- The standard minimum profile requires one configured provider in each of `main_search`, `docs_search`, and fetch capability for profile diagnostics and `doctor`. Command execution is capability-scoped: `search` requires source/discovery providers, `fetch` requires `web_fetch`, `map` requires `site_map`, and `research run` requires `web_fetch` while discovery capabilities remain optional. `doctor status` exposes `minimum_profile_ok`, `minimum_profile_missing`, `core_evidence_path`, and per-capability provider eligibility; it never probes providers.
- `CapabilityPlan` is the internal command boundary for required/optional capabilities, provider-attempt limits, fetch limits, budget, and synthesis permission. `EvidenceBundle` is the shared evidence boundary: discovery candidates stay unverified, fetched/read content becomes evidence and citations, and synthesis receives only that fetched/read content.
- AnySearch is reported only as optional experimental `vertical_search`; it is not part of the `web_search` fallback and is not required by the `standard` minimum profile.
- Jina Reader is `web_fetch` only, not a general search provider. `JINA_API_KEY` is required before Jina satisfies the standard minimum profile; anonymous `r.jina.ai` is explicit/experimental fetch behavior.
- Same-capability fallback is allowed; cross-capability fallback is not. Context7 is not used for unrelated broad web queries, and page extraction providers are not used as docs search providers.
- `main_search`: xAI Responses first for Grok/xAI, then OpenAI-compatible answer fallback when that peer provider is separately configured.
- `web_search`: Zhipu Web Search API first when routed in, then Zhipu Coding Plan MCP `web_search_prime`, then Tavily / Firecrawl source search when configured.
- `docs_search`: Context7 first for library/API/docs intent, then Exa for official-domain, paper, product-page, trusted-site, or low-noise supplemental discovery.
- Fetch capability: Tavily first, then Jina Reader with `JINA_API_KEY`, then Zhipu Coding Plan MCP `webReader`, then Firecrawl.
- Discovery breadth is bounded by the typed plan/request budgets (for example `max_results`), not by a CLI `--extra-sources` option; the canonical V2 surface rejects V1 options before any provider work.
- `fetch` and known-URL `search "https://..."` use the same fetch fallback chain.
- `fetch` tries Tavily first, then Jina with `JINA_API_KEY`, then Zhipu Coding Plan MCP Reader, then Firecrawl.
- `map` currently uses Tavily only.
- `TAVILY_ENABLED=false` keeps a configured Tavily key visible as disabled in diagnostics, but removes Tavily from eligible search, fetch, and map calls; skipped fallback attempts include the disabled reason.
- Provider selection is internal: `search` routes to Exa only for Exa-qualified docs intent, Context7 only for Context7-qualified docs intent, Zhipu only for Zhipu-qualified web intent, and AnySearch only for explicit vertical intent. Exact provider leaves and aliases are removed; their spellings fail at parse time.
- Runtime config priority is environment variables first, then local config file, then defaults.
- `config` reads/writes the local Smart Search config file and does not call providers.
- `SMART_SEARCH_MODEL_ROUTES` stores an ordered JSON array of independent main-search routes. Each route has its own `id`, `provider`, `api_url`, `api_key`, and `model`; `provider routes add`, `provider routes list`, `provider routes current`, and `provider routes remove` manage the same list; the legacy `model *` spellings are removed. Route keys are masked in inspection output, and existing `XAI_*` / `OPENAI_COMPATIBLE_*` settings remain compatible when the array is absent.
- `model current` reports the first route as current when model routes are configured. Use `model add` to append a route, or `config set XAI_MODEL ...` / `config set OPENAI_COMPATIBLE_MODEL ...` for legacy single-provider settings.

Zhipu Web Search API:

- The Zhipu Web Search API route uses `ZHIPU_API_URL` plus `ZHIPU_SEARCH_ENGINE` through the generic `search` command; it is not Zhipu Chat Completions `tools=[web_search]`, not Search Agent, and not the MCP Server.
- `ZHIPU_SEARCH_ENGINE` defaults to `search_std`. Official Web Search API service values include `search_std`, `search_pro`, `search_pro_sogou`, and `search_pro_quark`; keep custom values possible because official services may change.
- `TAVILY_API_URL` only affects Tavily REST calls and does not proxy Zhipu.

Zhipu Coding Plan Remote MCP:

- `ZHIPU_MCP_API_KEY` configures the Coding Plan MCP auth token and must be sent as `Authorization: Bearer ...`; it must never be logged unmasked.
- `ZHIPU_MCP_SEARCH_API_URL` defaults to `https://open.bigmodel.cn/api/mcp/web_search_prime/mcp` and calls `web_search_prime` for `web_search`.
- `ZHIPU_MCP_READER_API_URL` defaults to `https://open.bigmodel.cn/api/mcp/web_reader/mcp` and calls `webReader` for `web_fetch`.
- `ZHIPU_MCP_ZREAD_API_URL` defaults to `https://open.bigmodel.cn/api/mcp/zread/mcp` and calls `search_doc`, `get_repo_structure`, and `read_file` internally for repo/docs operations.
- Zhipu Coding Plan MCP must be implemented as a separate Remote MCP-over-HTTP provider layer. Do not route it through the existing `/paas/v4/web_search` Zhipu REST provider.
- A normal Zhipu Web Search API key is not sufficient evidence of Coding Plan entitlement. If `ZHIPU_MCP_API_KEY` is missing or returns auth/provider errors, MCP providers are skipped or fall through within the same capability; zread remains explicit and does not affect the standard minimum profile.
- Provider failures must appear in `provider_attempts` and fallback must remain same-capability.
- `doctor` should report configured/not-configured, auth, rate-limit, provider, timeout, and network status without exposing the MCP token.

Jina Reader:

- `JINA_READER_API_URL` defaults to `https://r.jina.ai`.
- `JINA_API_KEY` is required before Jina satisfies `SMART_SEARCH_MINIMUM_PROFILE=standard`.
- Anonymous Jina Reader calls may be used only as explicit/experimental degraded fetch behavior; they must not make standard setup pass.
- `JINA_RESPOND_WITH=readerlm-v2` requires `JINA_API_KEY` and should report a configuration error without a network request when the key is missing.
- Jina Reader is `web_fetch` only, not `web_search`.
- Jina 401/403, 422, 429, timeout, network errors, and low-quality challenge pages such as `Title: Just a moment...` must be reported as failed provider attempts and allow same-capability fallback.

AnySearch:

- AnySearch uses JSON-RPC 2.0 `tools/call` at `ANYSEARCH_API_URL`, default `https://api.anysearch.com/mcp`.
- `ANYSEARCH_API_KEY` is optional. If configured, requests include `Authorization: Bearer ...`; if missing, anonymous requests are allowed.
- `ANYSEARCH_TIMEOUT_SECONDS` defaults to `30`.
- HTTP 200 responses with `result.isError=true` must return `ok=false`, `error_type=provider_error`, and no successful source results.
- Markdown URL/title/snippet candidates should be parsed into `results`, while raw text remains in `content` and `raw_content`.
- Structured results without URLs must be preserved as raw/structured evidence, not dropped.
- Dotted vertical domain shorthand such as `security.cve` must be normalized to `domain=security` plus `sub_domain=cve` before calling AnySearch.

OpenAI-compatible streaming:

- `OPENAI_COMPATIBLE_STREAM` defaults to `false` and accepts `true`, `1`, or `yes` as true.
- Streaming preference is configuration-controlled: `OPENAI_COMPATIBLE_STREAM=true` means "prefer stream first"; stream empty/timeout/retryable protocol failures fall back to the same provider/model with `stream=false`.
- `OPENAI_COMPATIBLE_FALLBACK_MODELS` is an optional comma-separated ordered list. It is tried only after the primary OpenAI-compatible model fails; `SMART_SEARCH_MODEL_ROUTES` ordering or removing the route disables the fallback for the invocation.
- OpenAI-compatible attempts may include `model`, `transport`, `fallback_from_transport`, `fallback_from_model`, and `breaker_state` inside the V2 `attempts` collection. `transport_fallback_used` records stream-to-non-stream recovery separately from provider/model fallback.
- Streaming applies only to OpenAI-compatible `search()` and provider-side `fetch()` calls. `describe_url()` and `rank_sources()` stay non-streaming. xAI Responses behavior is unchanged.

Exa domain filters:

- `--include-domains` and `--exclude-domains` accept comma-separated or whitespace-separated domains.
- Both `--include-domains docs.python.org,developer.mozilla.org` and `--include-domains docs.python.org developer.mozilla.org` normalize to the same Exa domain list.
- This normalization is intentional for Windows PowerShell, where an unquoted comma expression can be forwarded through `.ps1` wrappers as a space-separated value.

## Provider Output Details

- Exa HTTP `400` or `422` failures are returned as `ok=false` with `error_type=parameter_error`; use this to distinguish bad CLI/domain/date/category arguments from upstream network failures.
- AnySearch experimental output should preserve structured results without URLs as raw/structured evidence.
- Diagnostic output should report Firecrawl status as whether `FIRECRAWL_API_KEY` is configured; it is not currently a live Firecrawl request.

## Routing Heuristics

- Use `smart-search dev route-explain "query" --format markdown` when you need to explain why a query maps to `docs_search`, `web_search`, `web_fetch`, or `vertical_search` without executing providers.
- Use `search` with intent-matched routing for official/domain-constrained discovery; capability selection is internal.
- Use `search` for docs/API/SDK/library/framework intent when Context7/Exa are configured.
- Use `search` for Chinese, domestic, current, or domain-filtered source discovery when Zhipu is configured.
- Use `search --format content` when a human wants the compact content view.
- Use `fetch --format markdown` or `fetch --format content` for user-supplied URLs or when exact page text matters.
- Use `map` before fetching many pages from a documentation site.
- For current news or high-risk claims, prefer source discovery plus `fetch`; do not treat discovery candidates as claim-level verification.

## Maintenance Guardrails

- Provider architecture changes must be verified as distributable CLI behavior, not as behavior that only works because one developer machine has a specific wrapper, shell profile, or local config file.
- Register providers by capability first, then route by intent. Fallback is allowed only within the same capability.
- Keep xAI Responses and OpenAI-compatible as peer `main_search` providers. A failed xAI Responses request may fall back to OpenAI-compatible only when `OPENAI_COMPATIBLE_API_URL` and `OPENAI_COMPATIBLE_API_KEY` are separately configured.
- Do not use Context7 for broad news or generic web facts; do not use Tavily or Firecrawl as documentation semantic-search replacements.
- Standard installs must fail closed unless `main_search`, `docs_search`, and fetch capability each have at least one configured provider.
- After provider-routing changes, run source-checkout regression plus `smart-search dev smoke --mock --format json`. If live keys were used, run a targeted secret scan for exact key substrings before committing.
