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

- `evidence.candidates`: discovery candidates (`id`, `resource`, `provider`, `title`, `snippet`) that are not yet proof;
- `evidence.items`: fetched/read page text used for claims (`id`, `resource`, `provider`, `title`, `content`);
- `evidence.citations`: references generated from fetched evidence;
- `evidence.gaps`: claims or subquestions that remain unsupported;
- `routing`, `attempts`, and `degradation`: provider execution metadata (capability routing, per-attempt provider/status/error, and degradation codes).

Discovery candidates are candidates until their URLs are fetched. A broad answer is not a substitute for fetching the source that supports a high-risk claim.

## Persistent evidence

The strict V2/V3/Workflow families reject `--output`, `--force`, and `--evidence-dir` before any owner work; the research workflow records logical artifacts only. Persist evidence by capturing stdout JSON with shell redirection:

```sh
smart-search search "Reuters Iran Hormuz latest" --format json > ./evidence/01-search.json
smart-search fetch "https://example.com/source" --format markdown > ./evidence/02-fetch.md
```

The runtime cache never stores synthesis answers, credentials, prompts, errors, empty results, or research artifacts. It may cache cleaned successful source/content results when explicitly enabled; caching does not change freshness or evidence requirements.

## Degraded results

When provider or synthesis failures prevent complete coverage, keep the fetched evidence and report the missing support. `research run` may finish with `degraded` status or explicit gaps while intentionally leaving answer fields empty. Bare `research`, `rs`, `deep`, and `dr` are removed spellings that fail with the workflow family's strict error, and `research run --synthesize` is rejected before any owner work. Neither path may invent evidence, treat a discovery snippet as a citation, or silently call another capability as a substitute.
