# Providers and configuration

Provider selection is capability-first. A provider can be selected only for a capability it declares, and fallback stays inside that capability. Provider credentials are read from local configuration or environment overrides; they are never included in result metadata.

## Capability map

| Capability | Providers and order | Intended use |
| --- | --- | --- |
| `main_search` | xAI Responses -> OpenAI-compatible | Broad live answer generation and primary synthesis |
| `docs_search` | Context7 -> Exa | Libraries, frameworks, APIs, official docs, papers, and trusted discovery |
| `web_search` | Zhipu Web Search API -> Zhipu Coding Plan MCP -> Tavily -> Firecrawl -> Brave | General, Chinese, domestic, current, or supplemental discovery |
| `web_fetch` | Tavily -> Jina (anonymous or keyed) -> Zhipu Coding Plan MCP Reader -> Firecrawl | Known URL extraction and claim-level page evidence |
| `vertical_search` | AnySearch | Experimental structured search such as CVE, finance, legal, academic, or code/docs domains |
| `site_map` | Tavily | Site and documentation structure discovery |

Fallback is same-capability only. AnySearch is not part of the `web_search` fallback and is not required by the `standard` minimum profile.

## Retrieval policy and fusion (v0.3.0)

Since v0.3.0, `source_discovery` can run through the multi-source retrieval gateway when a retrieval-policy provider (Brave or Exa; Tavily for research intent) is configured. The gateway normalizes every provider into one internal `DiscoveryCandidate` shape, canonicalizes URLs (lowercase scheme/host, fragment and known tracking-parameter removal, default-port and root-path normalization; no www normalization in v0.3.0), deduplicates across providers while retaining provenance, and ranks with reciprocal-rank fusion (RRF, default `k=60`). Optionally, fused candidates are reranked with the Jina Reranker (`JINA_API_KEY`) — RRF remains the final ranking on any rerank failure. Discovery candidates stay candidates; the existing `fetch` path remains the only evidence path.

The retrieval policy answers only *which providers execute source discovery* for an intent; it is not an intent router. Intent routing (which capability a request needs) is unchanged.

| Intent | Providers |
| --- | --- |
| GENERAL | Brave + Exa |
| FRESH | Brave |
| SEMANTIC | Exa |
| TECHNICAL | Brave + Exa |
| RESEARCH | Brave + Exa + Tavily |

The `search`/`source_discovery` default auto-detects FRESH/TECHNICAL/GENERAL from the existing local routing rules. Setups with none of the policy providers configured (for example Tavily-only or Zhipu-only) keep the exact pre-v0.3.0 source-discovery behavior.

## Provider matrix

| Provider | Capability and role | Main configuration | Documentation |
| --- | --- | --- | --- |
| xAI Responses | Primary live search with web search tools | `XAI_API_KEY`, `XAI_API_URL`, `XAI_MODEL`, `XAI_TOOLS` | [xAI docs](https://docs.x.ai/docs), [API keys](https://console.x.ai/team/default/api-keys) |
| OpenAI-compatible | Primary search through OpenAI or a compatible relay | `OPENAI_COMPATIBLE_API_URL`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL`, `OPENAI_COMPATIBLE_FALLBACK_MODELS`, `OPENAI_COMPATIBLE_STREAM` | [OpenAI docs](https://platform.openai.com/docs), [API keys](https://platform.openai.com/api-keys) |
| Exa | Low-noise docs, API, paper, product, and trusted-page discovery | `EXA_API_KEY` | [Exa docs](https://docs.exa.ai/), [API keys](https://dashboard.exa.ai/api-keys) |
| Context7 | SDK, library, framework, and API documentation | `CONTEXT7_API_KEY`, `CONTEXT7_BASE_URL` | [Context7 docs](https://context7.com/docs), [Context7](https://context7.com/) |
| Zhipu Web Search API | Chinese, domestic, current, or domain-filtered discovery | `ZHIPU_API_KEY`, `ZHIPU_API_URL`, `ZHIPU_SEARCH_ENGINE` | [Web Search API](https://docs.bigmodel.cn/cn/guide/tools/web-search), [API keys](https://open.bigmodel.cn/usercenter/apikeys) |
| Zhipu Coding Plan Remote MCP | Coding Plan search, page reading, and repository discovery | `ZHIPU_MCP_API_KEY`, `ZHIPU_MCP_SEARCH_API_URL`, `ZHIPU_MCP_READER_API_URL`, `ZHIPU_MCP_ZREAD_API_URL` | [search MCP](https://docs.bigmodel.cn/cn/coding-plan/mcp/search-mcp-server), [reader MCP](https://docs.bigmodel.cn/cn/coding-plan/mcp/reader-mcp-server), [zread MCP](https://docs.bigmodel.cn/cn/coding-plan/mcp/zread-mcp-server) |
| Tavily | Extra web sources, URL fetch, and site map | `TAVILY_API_URL`, `TAVILY_API_KEY`, `TAVILY_ENABLED` | [Tavily docs](https://docs.tavily.com/), [Tavily app](https://app.tavily.com/home) |
| Brave | Fresh and general web discovery through the retrieval gateway | `BRAVE_API_KEY`, `BRAVE_API_URL`, `BRAVE_ENABLED`, `BRAVE_TIMEOUT_SECONDS` | [Brave Search API](https://brave.com/search/api/), [API keys](https://api.search.brave.com/app/keys) |
| Jina Reader | Known URL extraction for `web_fetch`; anonymous with the default endpoint | `JINA_API_KEY`, `JINA_READER_API_URL`, `JINA_RESPOND_WITH`, `JINA_TIMEOUT_SECONDS` | [Jina Reader](https://jina.ai/reader/), [Jina AI](https://jina.ai/) |
| Jina Reranker | Optional post-fusion reranking of retrieval candidates | `JINA_API_KEY`, `JINA_RERANK_API_URL`, `JINA_RERANK_MODEL` | [Jina Reranker](https://jina.ai/reranker/), [Jina AI](https://jina.ai/) |
| Firecrawl | Fetch fallback and supplementary web sources | `FIRECRAWL_API_URL`, `FIRECRAWL_API_KEY` | [Firecrawl docs](https://docs.firecrawl.dev/), [API keys](https://www.firecrawl.dev/app/api-keys) |
| AnySearch | Experimental vertical search | `ANYSEARCH_API_URL`, `ANYSEARCH_API_KEY`, `ANYSEARCH_TIMEOUT_SECONDS` | [AnySearch docs](https://www.anysearch.com/docs), [API keys](https://www.anysearch.com/console/api-keys) |

## Minimum profiles

The default `SMART_SEARCH_MINIMUM_PROFILE=standard` is fail-closed for profile diagnostics and `doctor`. The Core minimum needs source discovery plus fetch and never requires a model route:

- source discovery: at least one of `web_search` (Zhipu REST/MCP, Tavily, Firecrawl) or `docs_search` (Context7, Exa);
- one `web_fetch` provider: Tavily, Jina (anonymous or with `JINA_API_KEY`), Zhipu Coding Plan MCP Reader, or Firecrawl.

The explicit modes are:

- `standard`: require source discovery (`web_search` OR `docs_search`) and `web_fetch`;
- `lite`: relax the profile gate to source/search availability (any `main_search`, `web_search`, or `docs_search` provider); command preflight stays capability-scoped;
- `full`: require the Core minimum plus `site_map`;
- `off`: disable the minimum-profile gate for local experiments.

Legacy model routes (`main_search`: xAI Responses and OpenAI-compatible) remain configured and probeable as optional `llm_synthesis` state; `llm_plan` is reported as an explicit empty optional capability. An absent model route never makes an evidence-ready Core unavailable, and `doctor` reports it as a warning rather than a Core failure.

Normal commands validate only the capabilities they need. `fetch` needs `web_fetch`, `map` needs `site_map`, and `research` needs `web_fetch` while discovery capabilities depend on the query. Missing command capabilities return `config_error` with `required_capabilities` and `missing_capabilities`.

## Important provider boundaries

### xAI and OpenAI-compatible

xAI uses the Responses API `/responses` route through `XAI_*`. OpenAI-compatible relays use Chat Completions `/chat/completions` through `OPENAI_COMPATIBLE_*`. Do not send xAI `web_search` or `x_search` tools, or legacy `search_parameters`, to the compatible route.

`OPENAI_COMPATIBLE_STREAM=true` is the relay compatibility control for OpenAI-compatible main search and provider-side fetch. There is no `--stream` / `--no-stream` CLI option on the canonical V2 surface; those V1 options are rejected before any provider work. Streaming does not change xAI behavior, URL descriptions, or source ranking.

For multiple independent endpoints, use the ordered `SMART_SEARCH_MODEL_ROUTES` array. Each entry owns its provider, API URL, API key, and model, so the next entry can use a different service or credential. The first entry is primary; later entries are tried only when the main-search request has a switchable upstream failure.

```json
{
  "SMART_SEARCH_MODEL_ROUTES": [
    {
      "id": "primary",
      "provider": "openai-compatible",
      "api_url": "https://relay-a.example/v1",
      "api_key": "key-a",
      "model": "model-a"
    },
    {
      "id": "backup",
      "provider": "xai-responses",
      "api_url": "https://api.x.ai/v1",
      "api_key": "xai-key",
      "model": "grok-4-fast",
      "tools": ["web_search", "x_search"]
    }
  ]
}
```

Use `smart-search provider routes add` to append the same structure, `smart-search provider routes list` or `smart-search provider routes current` to inspect it, and `smart-search provider routes remove ROUTE_ID` to delete an entry (the legacy `model *` spellings are removed). Inspection output masks API keys and credentials embedded in API URLs, while retaining the endpoint host and path for diagnosis. Existing `XAI_*` and `OPENAI_COMPATIBLE_*` settings remain valid when `SMART_SEARCH_MODEL_ROUTES` is absent. The first local `provider routes add` preserves saved legacy provider settings as `legacy-xai-responses` and `legacy-openai-compatible` routes before appending the new route. It does not copy environment-controlled legacy settings into the local config file; define `SMART_SEARCH_MODEL_ROUTES` in the environment for that setup.

### Zhipu REST and Coding Plan MCP

The Zhipu Web Search API route (`ZHIPU_API_URL` plus `ZHIPU_SEARCH_ENGINE`) is selected internally for Chinese/current/domestic `web_search` intent. It is not Zhipu Chat Completions or Search Agent, and it is not the MCP Server. `TAVILY_API_URL` affects Tavily only; it does not proxy Zhipu. `ZHIPU_SEARCH_ENGINE` accepts values such as `search_std`, `search_pro`, `search_pro_sogou`, and `search_pro_quark`.

Zhipu Coding Plan is a separate Remote MCP route:

- `web_search_prime` maps to `web_search`;
- `webReader` maps to `web_fetch`;
- `search_doc`, `get_repo_structure`, and `read_file` are internal zread repository/document operations without public command leaves.

Do not route it through the existing `/paas/v4/web_search` REST path. A normal `ZHIPU_API_KEY` for Web Search API does not prove Coding Plan access. The route requires a separate Coding Plan entitlement and `ZHIPU_MCP_API_KEY`; an unavailable MCP provider is skipped while same-capability fallback remains available. Its absence does not affect the standard minimum profile when another provider satisfies the role.

### Jina Reader

Jina Reader is `web_fetch` only. It is not a general search provider. Anonymous Jina with the default `JINA_READER_API_URL` (`https://r.jina.ai`) is normal eligible `web_fetch`: `provider status`, `provider probe`, and `doctor` distinguish `ready` (keyed) from `anonymous_ready` (anonymous) without exposing the key. A key is still supported and unlocks ReaderLM-v2 quality; `JINA_RESPOND_WITH=readerlm-v2` without a key stays a classified `config_error` and never becomes eligible.

### AnySearch

AnySearch is an experimental `vertical_search` provider selected internally for explicit vertical intent; it has no public command leaves.

```sh
smart-search config set ANYSEARCH_API_URL "https://api.anysearch.com/mcp"
smart-search config set ANYSEARCH_API_KEY "your-anysearch-key"
```

At the adapter/API layer, a missing key means no `Authorization` header. A JSON-RPC 200 response with `result.isError=true` is a provider error, not successful evidence.

## Intent router configuration

| Key | Meaning |
| --- | --- |
| `SMART_SEARCH_INTENT_ROUTER` | `hybrid`, `rules`, or `off`; default `hybrid` |
| `INTENT_EMBEDDING_API_URL` | Optional OpenAI-compatible embeddings endpoint |
| `INTENT_EMBEDDING_API_KEY` | Optional embeddings key, masked by diagnostics |
| `INTENT_EMBEDDING_MODEL` | Embeddings model name |
| `INTENT_EMBEDDING_THRESHOLD` | Semantic route threshold; default `0.74` |
| `INTENT_EMBEDDING_MARGIN` | Top-vs-second margin; default `0.05` |
| `INTENT_CLASSIFIER_API_URL` | Optional structured classifier endpoint |
| `INTENT_CLASSIFIER_API_KEY` | Optional classifier key, masked by diagnostics |
| `INTENT_CLASSIFIER_MODEL` | Classifier model name |
| `INTENT_ROUTER_TIMEOUT_SECONDS` | Remote router timeout; default `8` |

Use `smart-search dev route-explain "query" --format markdown` to inspect the routing decision. Use `smart-search dev route-calibrate --models "..." --format markdown` after changing embedding models or endpoints. Details are in [Routing](concepts/routing.md).

## Runtime cache

The process-local source/content cache is opt-in:

| Key | Default | Meaning |
| --- | --- | --- |
| `SMART_SEARCH_CACHE_ENABLED` | `false` | Enable successful source/content caching |
| `SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS` | `30` | Search/discovery TTL, allowed range `1..604800` |
| `SMART_SEARCH_FETCH_CACHE_TTL_SECONDS` | `300` | Fetch/content TTL, allowed range `1..604800` |
| `SMART_SEARCH_CACHE_MAX_SIZE` | `256` | Per-process LRU capacity, allowed range `1..10000` |
| `SMART_SEARCH_PERSIST_EVIDENCE` | `false` | Persist live research artifacts under an explicit evidence directory |

Only cleaned successful source/content results are cached. Synthesis answers, errors, empty results, credentials, prompts, and research artifacts are excluded. Cache behavior does not change provider capability boundaries or evidence rules.

## Setup and inspection

```sh
smart-search config set ZHIPU_API_URL "https://open.bigmodel.cn/api"
smart-search config set ZHIPU_SEARCH_ENGINE "search_pro_sogou"
smart-search config set BRAVE_API_KEY "your-brave-key"
smart-search capabilities --format json
smart-search config list --format json
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search provider probe exa --format json
```

`doctor status` reports local configuration and evidence-path readiness without creating a Provider client or probe. `doctor` / `doctor probe` are explicit live aggregate diagnostics, while `provider probe PROVIDER` tests exactly one named provider or main-route family without fallback. Use `smart-search config set` for normal configuration. Use environment variables for CI and advanced deployments. Do not copy provider keys into issue reports or documentation.
