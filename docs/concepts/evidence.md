# Evidence policy

Smart Search separates discovery from proof. A search result can help find a source, but a snippet or generated answer is not claim-level evidence until the relevant page is fetched or read.

## Evidence flow

```text
search / provider discovery
    -> discovery candidates
    -> fetch known URLs
    -> fetched evidence
    -> citations and final answer
```

The default Deep Research policy is `fetch_before_claim`:

1. Discover candidate URLs with `search`, `exa-search`, `zhipu-search`, `exa-similar`, or an explicit docs command.
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
smart-search exa-search "Reuters Iran Hormuz latest" --format json --output ./evidence/01-exa.json
smart-search fetch "https://example.com/source" --format markdown --output ./evidence/02-fetch.md
```

The runtime cache never stores synthesis answers, credentials, prompts, errors, empty results, or research artifacts. It may cache cleaned successful source/content results when explicitly enabled; caching does not change freshness or evidence requirements.

## Degraded results

When provider or synthesis failures prevent complete coverage, keep the fetched evidence and report the missing support. `research` may finish with `degraded=true`, `synthesis_error`, or explicit gaps. It must not invent evidence, treat a discovery snippet as a citation, or silently call another capability as a substitute.
