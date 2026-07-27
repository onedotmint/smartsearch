# Providers and configuration

Provider selection is capability-first. A provider can be selected only for a capability it declares, and fallback stays inside that capability. Provider credentials are read from local configuration or environment overrides; they are never included in result metadata.

## Capability map

| Capability | Providers and order | Intended use |
| --- | --- | --- |
| `main_search` | xAI Responses -> OpenAI-compatible | Broad live answer generation and primary synthesis |
| `docs_search` | Context7 -> Exa | Libraries, frameworks, APIs, official docs, papers, and trusted discovery |
| `web_search` | Zhipu Web Search API -> Zhipu Coding Plan MCP -> Tavily -> Firecrawl | General, Chinese, domestic, current, or supplemental discovery |
| `web_fetch` | Tavily -> Jina with key -> Zhipu Coding Plan MCP Reader -> Firecrawl | Known URL extraction and claim-level page evidence |
| `vertical_search` | AnySearch | Experimental structured search such as CVE, finance, legal, academic, or code/docs domains |
| `site_map` | Tavily | Site and documentation structure discovery |

Fallback is same-capability only. AnySearch is not part of the `web_search` fallback and is not required by the `standard` minimum profile.

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
| Jina Reader | Known URL extraction for `web_fetch` | `JINA_API_KEY`, `JINA_READER_API_URL`, `JINA_RESPOND_WITH`, `JINA_TIMEOUT_SECONDS` | [Jina Reader](https://jina.ai/reader/), [Jina AI](https://jina.ai/) |
| Firecrawl | Fetch fallback and supplementary web sources | `FIRECRAWL_API_URL`, `FIRECRAWL_API_KEY` | [Firecrawl docs](https://docs.firecrawl.dev/), [API keys](https://www.firecrawl.dev/app/api-keys) |
| AnySearch | Experimental vertical search | `ANYSEARCH_API_URL`, `ANYSEARCH_API_KEY`, `ANYSEARCH_TIMEOUT_SECONDS` | [AnySearch docs](https://www.anysearch.com/docs), [API keys](https://www.anysearch.com/console/api-keys) |

## Minimum profiles

The default `SMART_SEARCH_MINIMUM_PROFILE=standard` is fail-closed for profile diagnostics and `doctor`. It expects:

- one `main_search` provider: xAI Responses or OpenAI-compatible;
- one `docs_search` provider: Exa or Context7;
- one `web_fetch` provider: Tavily, Jina with `JINA_API_KEY`, Zhipu Coding Plan MCP Reader, or Firecrawl.

The explicit modes are:

- `standard`: require `main_search`, `docs_search`, and `web_fetch`;
- `lite`: allow source-only `search --response-mode evidence` when a main, web, or docs search provider exists;
- `full`: require the standard three capabilities plus `site_map`;
- `off`: disable the minimum-profile gate for local experiments.

Normal commands validate only the capabilities they need. `fetch` needs `web_fetch`, `map` needs `site_map`, and `research` needs `web_fetch` while discovery capabilities depend on the query. Missing command capabilities return `config_error` with `required_capabilities` and `missing_capabilities`.

## Important provider boundaries

### xAI and OpenAI-compatible

xAI uses the Responses API `/responses` route through `XAI_*`. OpenAI-compatible relays use Chat Completions `/chat/completions` through `OPENAI_COMPATIBLE_*`. Do not send xAI `web_search` or `x_search` tools, or legacy `search_parameters`, to the compatible route.

`OPENAI_COMPATIBLE_STREAM=true`, `--stream`, and `--no-stream` are relay compatibility controls. They do not change xAI behavior, URL descriptions, or source ranking.

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

Use `smart-search model add` to append the same structure, `smart-search model list` or `smart-search model current` to inspect it, and `smart-search model remove ROUTE_ID` to delete an entry. Inspection output masks API keys and credentials embedded in API URLs, while retaining the endpoint host and path for diagnosis. Existing `XAI_*` and `OPENAI_COMPATIBLE_*` settings remain valid when `SMART_SEARCH_MODEL_ROUTES` is absent. The first local `model add` preserves saved legacy provider settings as `legacy-xai-responses` and `legacy-openai-compatible` routes before appending the new route. It does not copy environment-controlled legacy settings into the local config file; define `SMART_SEARCH_MODEL_ROUTES` in the environment for that setup.

### Zhipu REST and Coding Plan MCP

`zhipu-search` is the Zhipu Web Search API route. It is not Zhipu Chat Completions or Search Agent, and it is not the MCP Server. `TAVILY_API_URL` affects Tavily only; it does not proxy Zhipu. `ZHIPU_SEARCH_ENGINE` accepts values such as `search_std`, `search_pro`, `search_pro_sogou`, and `search_pro_quark`.

Zhipu Coding Plan is a separate Remote MCP route:

- `web_search_prime` maps to `web_search`;
- `webReader` maps to `web_fetch`;
- `search_doc`, `get_repo_structure`, and `read_file` are explicit zread repository/document commands.

Do not route it through the existing `/paas/v4/web_search` REST path. A normal `ZHIPU_API_KEY` for Web Search API does not prove Coding Plan access. The route requires a separate Coding Plan entitlement and `ZHIPU_MCP_API_KEY`; an unavailable MCP provider is skipped while same-capability fallback remains available. Its absence does not affect the standard minimum profile when another provider satisfies the role.

### Jina Reader

Jina Reader is `web_fetch` only. It is not a general search provider. `JINA_API_KEY` is required before Jina satisfies the standard minimum profile. Anonymous Jina Reader calls are explicit or experimental fetch behavior and must not weaken the fail-closed profile. `JINA_RESPOND_WITH=readerlm-v2` also requires a key.

### AnySearch

AnySearch exposes `vertical_search` commands for explicit experiments:

```sh
smart-search setup --non-interactive --anysearch-api-url "https://api.anysearch.com/mcp" --anysearch-key "your-anysearch-key"
smart-search anysearch-domains security --format json
smart-search anysearch-search "CVE-2024-3094" --domain security.cve --max-results 3 --format json
smart-search anysearch-extract "https://example.com/source" --format json
smart-search anysearch-batch "AAPL" "RAG papers" --max-results 2 --format json
```

At the adapter/API layer, a missing key means no `Authorization` header; the CLI preflight still requires `ANYSEARCH_API_KEY` for every `anysearch-*` command. A JSON-RPC 200 response with `result.isError=true` is a provider error, not successful evidence.

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

Use `smart-search route` to inspect the result. Use `route-calibrate` after changing embedding models or endpoints. Details are in [Routing](concepts/routing.md).

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
smart-search setup
smart-search setup --non-interactive --zhipu-api-url "https://open.bigmodel.cn/api" --zhipu-search-engine "search_pro_sogou"
smart-search capabilities --format json
smart-search config list --format json
smart-search doctor --format markdown
```

Use `setup` for normal configuration. Use environment variables for CI and advanced deployments. Do not copy provider keys into issue reports or documentation.
