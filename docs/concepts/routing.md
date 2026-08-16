# Intent routing

Intent routing decides which capabilities a request needs before provider selection. It does not let a model choose arbitrary providers.

## Inspect a route

```sh
smart-search dev route-explain "React useEffect API docs" --format markdown
smart-search dev route-explain "请核验这个链接里的说法 https://example.com/source" --format json
```

`smart-search dev route-explain QUERY` never runs search, docs, fetch, or vertical provider APIs. The default `hybrid` mode may call configured remote embeddings and classifier endpoints to supplement local rules. `SMART_SEARCH_INTENT_ROUTER=rules` gives a local-only check and `off` disables routing.

## Routing stages

```text
query
  -> local rules: URL, docs/current/fetch/vertical signals, validation
  -> optional semantic route: embeddings threshold and margin
  -> optional classifier route: structured capabilities
  -> merged required_capabilities
  -> capability-first provider selection
```

The result contains `intent_router_mode`, `required_capabilities`, `intent_signals`, `confidence`, `router_engines_used`, and `degraded_reason`. It can retain compatibility booleans such as `docs_intent`, `zh_current_intent`, `web_current_intent`, `fetch_intent`, and `supplemental_paths`.

Classifier output cannot select providers. Unknown capabilities and provider names are ignored, and the registry still selects only providers that declare the selected capability. Fallback is same-capability only.

## Retrieval policy (v0.3.0) vs intent router

The intent router answers one question: *which capability does this request need?* (`source_discovery`, `docs_discovery`, `web_fetch`, ...). It never chooses providers.

The v0.3.0 retrieval policy answers a second, narrower question: *when `source_discovery` runs through the multi-source gateway, which providers execute?* It is a thin deterministic table, not a router and not a model:

| Intent | Providers |
| --- | --- |
| GENERAL | Brave + Exa |
| FRESH | Brave |
| SEMANTIC | Exa |
| TECHNICAL | Brave + Exa |
| RESEARCH | Brave + Exa + Tavily |

`search`/`source_discovery` auto-detects FRESH/TECHNICAL/GENERAL from the same local rules (`web_current_intent` / `docs_intent` signals); SEMANTIC/RESEARCH are explicit caller parameters (for example the benchmark harness). Provider-native scores never become a shared ranking signal: candidates are deduplicated by canonical URL and ranked with reciprocal-rank fusion (RRF, default `k=60`), optionally reranked by the Jina Reranker without ever making RRF a fallback requirement. The gateway lane runs only when the intent-resolved policy contains a configured provider; setups without Brave/Exa/Tavily keep the exact pre-v0.3.0 source-discovery path.

## Modes and remote calls

| Mode | Local rules | Embeddings | Classifier |
| --- | --- | --- | --- |
| `hybrid` | Yes | Optional | Optional |
| `rules` | Yes | No | No |
| `off` | Minimal command behavior | No | No |

`hybrid` is fail-open. Missing or failing remote router settings are recorded in `degraded_reason`, then local rules continue. A remote router failure does not become a provider error for the search command.

## Evidence-first projection

Schema-v2 composite `search` uses a deterministic rules-only projection of the same local rule source. It always requests `source_discovery` and adds `docs_discovery` only when local rules select `docs_search`. This projection does not invoke embeddings, classifiers, Providers, implicit fetch, vertical search, or answer synthesis. Its output is capability routing metadata, not a reachability check or a replacement for `dev route-explain`.

Relevant settings:

| Key | Meaning |
| --- | --- |
| `SMART_SEARCH_INTENT_ROUTER` | `hybrid`, `rules`, or `off`; default `hybrid` |
| `INTENT_EMBEDDING_API_URL` | OpenAI-compatible embeddings endpoint |
| `INTENT_EMBEDDING_API_KEY` | Embeddings credential |
| `INTENT_EMBEDDING_MODEL` | Embeddings model |
| `INTENT_EMBEDDING_THRESHOLD` | Top similarity threshold; default `0.74` |
| `INTENT_EMBEDDING_MARGIN` | Top-vs-second margin; default `0.05` |
| `INTENT_CLASSIFIER_API_URL` | OpenAI-compatible classifier endpoint |
| `INTENT_CLASSIFIER_API_KEY` | Classifier credential |
| `INTENT_CLASSIFIER_MODEL` | Classifier model |
| `INTENT_ROUTER_TIMEOUT_SECONDS` | Remote router timeout; default `8` |

Semantic routing adds a capability only when the top score reaches the threshold and the top-vs-second gap reaches the margin. Ambiguous matches remain signals only. The classifier may add capabilities, but it cannot bypass the provider registry.

## Calibration

Embedding scores are model-specific. Run calibration after changing the model, endpoint, or real query set:

```sh
smart-search dev route-calibrate --models "Qwen/Qwen3-Embedding-8B" --format markdown
```

Use the report's recommended `INTENT_EMBEDDING_THRESHOLD` and `INTENT_EMBEDDING_MARGIN`. Semantic-only Macro-F1 is the primary calibration metric; full-route Macro-F1 checks rules and classifier fallback behavior.

## Runtime observability

`dev route-explain`, `search`, `fetch`, and `research` expose routing metadata without exposing router keys. `search`, `fetch`, and `research` also report command-scoped `request_count`, `cache_hit`, `inflight_joined`, `remote_router_calls`, `retry_count`, `budget_exhausted`, and `stage_elapsed_ms` where applicable.

The cache is process-local and opt-in. See [Providers](../providers.md#runtime-cache) for its limits and configuration.
