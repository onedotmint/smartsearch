# Evidence policy

Smart Search separates discovery from proof. A search result can help find a source, but a snippet or generated answer is not claim-level evidence until the relevant page is fetched or read.

## Evidence flow

```text
schema-v2 search / provider discovery
    -> evidence.candidates
    -> explicit fetch of selected URLs
    -> evidence.items (fetched/read evidence)
    -> host-agent citations and final answer
```

The default Deep Research policy is `fetch_before_claim`. The Agent default uses schema-v2 `search` followed by explicit schema-v2 `fetch`; `evidence.candidates` are discovery only and `evidence.items` are the fetched/read evidence boundary. For staged work, `research run` returns admitted evidence, citations, and gaps without synthesizing by default.

The evidence workflow is:

1. Discover candidate URLs with `search` (intent-matched internal provider routing) or `map`.
2. Fetch exact pages with `fetch` or a capability-owned reader.
3. Cite the fetched text for material claims.
4. If a key claim cannot be fetched, mark it as an unverified candidate or list it in the gap report.

Unsupported key claims must be fetched or downgraded to unverified candidates. This matters most for news, policy, finance, health, security, product selection, and serious reviews.

## Result fields

Search and research results may contain:

- `primary_sources`: the main discovery candidates;
- `extra_sources`: supplementary discovery candidates;
- `fetched_evidence`: page text or reader output used for claims;
- `citations`: references generated from fetched evidence;
- `gaps`: claims or subquestions that remain unsupported;
- `source_warning`: warnings about incomplete or low-confidence source coverage;
- `provider_attempts`, `providers_used`, and `fallback_used`: provider execution metadata.

`primary_sources` and `extra_sources` are candidates until their URLs are fetched. A broad answer is not a substitute for fetching the source that supports a high-risk claim.

## Persistent evidence

The planner may display a platform temporary `evidence_dir`. Runtime artifacts are persisted only when `--evidence-dir` is explicit or `SMART_SEARCH_PERSIST_EVIDENCE=true` is set. Use a stable, user-controlled directory when evidence must survive the process:

```sh
smart-search search "Reuters Iran Hormuz latest" --format json --output ./evidence/01-search.json
smart-search fetch "https://example.com/source" --format markdown --output ./evidence/02-fetch.md
```

The runtime cache never stores synthesis answers, credentials, prompts, errors, empty results, or research artifacts. It may cache cleaned successful source/content results when explicitly enabled; caching does not change freshness or evidence requirements.

## Degraded results

When provider or synthesis failures prevent complete coverage, keep the fetched evidence and report the missing support. `research run` may finish with `degraded=true` or explicit gaps while intentionally leaving answer fields empty. Bare `research` and `research run --synthesize` may also report `synthesis_error`. Neither path may invent evidence, treat a discovery snippet as a citation, or silently call another capability as a substitute.
