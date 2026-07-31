# Intent routing

Intent routing decides which capabilities a request needs before provider selection. It does not let a model choose arbitrary providers.

## Inspect a route

```sh
smart-search route "React useEffect API docs" --format markdown
smart-search route "请核验这个链接里的说法 https://example.com/source" --router-mode rules --format json
```

`smart-search route QUERY` never runs search, docs, fetch, or vertical provider APIs. The default `hybrid` mode may call configured remote embeddings and classifier endpoints to supplement local rules. Use `--router-mode rules` for a local-only check or `--router-mode off` to disable routing.

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

## Modes and remote calls

| Mode | Local rules | Embeddings | Classifier |
| --- | --- | --- | --- |
| `hybrid` | Yes | Optional | Optional |
| `rules` | Yes | No | No |
| `off` | Minimal command behavior | No | No |

`hybrid` is fail-open. Missing or failing remote router settings are recorded in `degraded_reason`, then local rules continue. A remote router failure does not become a provider error for the search command.

## Evidence-first projection

Schema-v2 composite `search` uses a deterministic rules-only projection of the same local rule source. It always requests `source_discovery` and adds `docs_discovery` only when local rules select `docs_search`. This projection does not invoke embeddings, classifiers, Providers, implicit fetch, vertical search, or answer synthesis. Its output is capability routing metadata, not a reachability check or a replacement for the full `route` command.

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
smart-search route-calibrate --models "Qwen/Qwen3-Embedding-8B" --format markdown
```

Use the report's recommended `INTENT_EMBEDDING_THRESHOLD` and `INTENT_EMBEDDING_MARGIN`. Semantic-only Macro-F1 is the primary calibration metric; full-route Macro-F1 checks rules and classifier fallback behavior.

## Runtime observability

`route`, `search`, `fetch`, and `research` expose routing metadata without exposing router keys. `search`, `fetch`, and `research` also report command-scoped `request_count`, `cache_hit`, `inflight_joined`, `remote_router_calls`, `retry_count`, `budget_exhausted`, and `stage_elapsed_ms` where applicable.

The cache is process-local and opt-in. See [Providers](../providers.md#runtime-cache) for its limits and configuration.
