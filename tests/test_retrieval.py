"""Deterministic tests for the v0.3.0 retrieval core (pure, network-free).

Covers URL canonicalization, cross-provider deduplication with provenance,
reciprocal rank fusion, the thin retrieval policy, and the brave/exa/tavily
DiscoveryCandidate normalizers.
"""

from __future__ import annotations

import pytest

from smart_search.retrieval import (
    DEFAULT_RRF_K,
    RETRIEVAL_POLICIES,
    DiscoveryCandidate,
    FusedCandidate,
    RankedCandidate,
    canonicalize_url,
    deduplicate_candidates,
    reciprocal_rank_fusion,
    resolve_retrieval_policy,
)
from smart_search.providers.brave import to_discovery_candidates as brave_to_candidates
from smart_search.providers.exa import to_discovery_candidates as exa_to_candidates
from smart_search.providers.tavily import to_discovery_candidates as tavily_to_candidates


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------


class TestCanonicalizeUrl:
    def test_lowercases_scheme_and_hostname(self):
        assert canonicalize_url("HTTP://ExAmPlE.COM/Path") == "http://example.com/Path"

    def test_drops_fragment(self):
        assert canonicalize_url("https://example.com/a?b=1#section") == "https://example.com/a?b=1"

    def test_drops_utm_and_known_tracking_params_preserves_meaningful(self):
        assert (
            canonicalize_url(
                "https://example.com/p?utm_source=x&q=hello&fbclid=abc&gclid=def&page=2&ref_src=tw"
            )
            == "https://example.com/p?page=2&q=hello"
        )

    def test_tracking_param_match_is_case_insensitive(self):
        assert canonicalize_url("https://example.com/?UTM_CAMPAIGN=x&keep=1") == "https://example.com/?keep=1"
        assert canonicalize_url("https://example.com/?FBCLID=abc") == "https://example.com/"

    def test_drops_other_known_tracking_params(self):
        assert (
            canonicalize_url("https://example.com/p?mc_cid=1&mc_eid=2&igshid=3&ref_url=4")
            == "https://example.com/p"
        )

    def test_sorts_remaining_query_params(self):
        assert (
            canonicalize_url("https://example.com/p?z=1&a=2&m=3")
            == "https://example.com/p?a=2&m=3&z=1"
        )

    def test_trailing_slash_and_root_path_equivalence(self):
        assert canonicalize_url("https://example.com/") == canonicalize_url("https://example.com")
        assert canonicalize_url("http://example.com/") == "http://example.com/"

    def test_default_ports_removed(self):
        assert canonicalize_url("https://example.com:443/a") == "https://example.com/a"
        assert canonicalize_url("http://example.com:80/a") == "http://example.com/a"
        # Non-default ports are preserved.
        assert canonicalize_url("https://example.com:8443/a") == "https://example.com:8443/a"

    def test_meaningful_query_params_preserved(self):
        assert (
            canonicalize_url("https://example.com/docs?v=2&lang=en&id=42")
            == "https://example.com/docs?id=42&lang=en&v=2"
        )

    def test_www_is_not_normalized(self):
        # v0.3.0 explicitly defers www normalization; these must NOT merge.
        assert canonicalize_url("https://www.example.com/a") == "https://www.example.com/a"
        assert canonicalize_url("https://example.com/a") == "https://example.com/a"
        assert canonicalize_url("https://www.example.com/a") != canonicalize_url("https://example.com/a")

    def test_idempotent(self):
        for url in (
            "HTTP://ExAmPlE.COM:80/a?utm_source=x&b=2&a=1#frag",
            "https://example.com/p?z=1&a=2",
            "http://example.com/",
        ):
            once = canonicalize_url(url)
            assert canonicalize_url(once) == once

    def test_userinfo_preserved(self):
        assert (
            canonicalize_url("https://user:pass@Example.com/a")
            == "https://user:pass@example.com/a"
        )

    def test_unparseable_and_non_hierarchical_return_unchanged(self):
        assert canonicalize_url("not a url") == "not a url"
        assert canonicalize_url("mailto:someone@example.com") == "mailto:someone@example.com"

    def test_empty_and_blank_input(self):
        assert canonicalize_url("") == ""
        assert canonicalize_url("   ") == ""


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _candidate(url, title="Title", provider="brave", rank=0, snippet="", metadata=None):
    return DiscoveryCandidate(
        url=url,
        title=title,
        provider=provider,
        snippet=snippet,
        provider_rank=rank,
        metadata=metadata or {},
    )


class TestDeduplicateCandidates:
    def test_same_canonical_url_across_providers_merges_with_provenance(self):
        candidates = [
            _candidate("https://example.com/a?utm_source=x", "Brave title", provider="brave", rank=2),
            _candidate("https://example.com/a", "Exa title", provider="exa", rank=4),
        ]
        fused = deduplicate_candidates(candidates)
        assert len(fused) == 1
        item = fused[0]
        assert item.url == "https://example.com/a"
        assert item.providers == ("brave", "exa")
        assert item.provider_ranks == {"brave": 2, "exa": 4}
        # First-seen display values.
        assert item.display_url == "https://example.com/a?utm_source=x"
        assert item.title == "Brave title"

    def test_distinct_urls_never_merge(self):
        candidates = [
            _candidate("https://www.example.com/a", provider="brave"),
            _candidate("https://example.com/a", provider="exa"),
            _candidate("https://example.com/b", provider="brave"),
        ]
        fused = deduplicate_candidates(candidates)
        assert [item.url for item in fused] == [
            "https://www.example.com/a",
            "https://example.com/a",
            "https://example.com/b",
        ]

    def test_order_is_deterministic_first_seen(self):
        base = [
            _candidate("https://example.com/a", provider="brave"),
            _candidate("https://example.com/b", provider="exa"),
            _candidate("https://example.com/a", provider="tavily"),
        ]
        assert [item.url for item in deduplicate_candidates(base)] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        # First-seen order follows the input order exactly.
        other = [
            _candidate("https://example.com/b", provider="exa"),
            _candidate("https://example.com/a", provider="tavily"),
            _candidate("https://example.com/b", provider="brave"),
        ]
        assert [item.url for item in deduplicate_candidates(other)] == [
            "https://example.com/b",
            "https://example.com/a",
        ]

    def test_metadata_merged_diagnostics_only(self):
        candidates = [
            _candidate("https://example.com/a", provider="brave", metadata={"age": "2d"}),
            _candidate("https://example.com/a", provider="exa", metadata={"exa_score": 0.9}),
        ]
        item = deduplicate_candidates(candidates)[0]
        assert item.metadata == {"age": "2d", "exa_score": 0.9}


# ---------------------------------------------------------------------------
# Reciprocal rank fusion
# ---------------------------------------------------------------------------


def _fused(url, providers, ranks):
    return FusedCandidate(
        url=url,
        display_url=url,
        title=url,
        snippet="",
        providers=tuple(providers),
        provider_ranks=dict(ranks),
        metadata={},
    )


class TestReciprocalRankFusion:
    def test_exact_score_single_provider(self):
        candidates = [_fused("https://example.com/a", ("brave",), {"brave": 0})]
        ranked = reciprocal_rank_fusion(candidates)
        assert ranked[0].rrf_score == pytest.approx(1.0 / (DEFAULT_RRF_K + 0 + 1))
        assert ranked[0].rank == 0

    def test_exact_score_multi_provider_agreement(self):
        candidates = [
            _fused("https://example.com/a", ("brave", "exa"), {"brave": 2, "exa": 4}),
        ]
        ranked = reciprocal_rank_fusion(candidates)
        expected = 1.0 / (DEFAULT_RRF_K + 2 + 1) + 1.0 / (DEFAULT_RRF_K + 4 + 1)
        assert ranked[0].rrf_score == pytest.approx(expected)

    def test_multi_provider_agreement_outranks_single_provider_first(self):
        candidates = [
            _fused("https://example.com/first", ("brave",), {"brave": 0}),
            _fused("https://example.com/shared", ("brave", "exa"), {"brave": 0, "exa": 0}),
        ]
        ranked = reciprocal_rank_fusion(candidates)
        # 1/61 + 1/61 > 1/61
        assert ranked[0].candidate.url == "https://example.com/shared"
        assert ranked[1].candidate.url == "https://example.com/first"

    def test_deterministic_ordering_and_ranks(self):
        candidates = [
            _fused("https://example.com/c", ("brave",), {"brave": 1}),
            _fused("https://example.com/a", ("brave",), {"brave": 0}),
            _fused("https://example.com/b", ("exa",), {"exa": 0}),
        ]
        ranked = reciprocal_rank_fusion(candidates)
        assert [item.candidate.url for item in ranked] == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]
        assert [item.rank for item in ranked] == [0, 1, 2]

    def test_tie_break_is_stable(self):
        candidates = [
            _fused("https://example.com/b", ("exa",), {"exa": 0}),
            _fused("https://example.com/a", ("brave",), {"brave": 0}),
        ]
        ranked = reciprocal_rank_fusion(candidates)
        assert [item.candidate.url for item in ranked] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        # Reversing input order must not change the deterministic output.
        reversed_ranked = reciprocal_rank_fusion(list(reversed(candidates)))
        assert [item.candidate.url for item in reversed_ranked] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        assert [item.rrf_score for item in ranked] == [item.rrf_score for item in reversed_ranked]

    def test_custom_k(self):
        candidates = [_fused("https://example.com/a", ("brave",), {"brave": 0})]
        ranked = reciprocal_rank_fusion(candidates, k=10)
        assert ranked[0].rrf_score == pytest.approx(1.0 / 11.0)

    def test_never_reads_provider_native_scores(self):
        # The same provider rank must yield the same score regardless of the
        # diagnostic metadata.
        plain = _fused("https://example.com/a", ("exa",), {"exa": 0})
        scored = FusedCandidate(
            url="https://example.com/a",
            display_url="https://example.com/a",
            title="a",
            snippet="",
            providers=("exa",),
            provider_ranks={"exa": 0},
            metadata={"exa_score": 0.99},
        )
        assert reciprocal_rank_fusion([plain])[0].rrf_score == reciprocal_rank_fusion([scored])[0].rrf_score


# ---------------------------------------------------------------------------
# Retrieval policy
# ---------------------------------------------------------------------------


class TestResolveRetrievalPolicy:
    def test_every_intent_row(self):
        assert resolve_retrieval_policy("general", ["brave", "exa", "tavily"]) == ["brave", "exa"]
        assert resolve_retrieval_policy("fresh", ["brave", "exa", "tavily"]) == ["brave"]
        assert resolve_retrieval_policy("semantic", ["brave", "exa", "tavily"]) == ["exa"]
        assert resolve_retrieval_policy("technical", ["brave", "exa", "tavily"]) == ["brave", "exa"]
        assert resolve_retrieval_policy("research", ["brave", "exa", "tavily"]) == [
            "brave",
            "exa",
            "tavily",
        ]

    def test_unavailable_providers_filtered(self):
        assert resolve_retrieval_policy("general", ["exa"]) == ["exa"]
        assert resolve_retrieval_policy("general", ["tavily"]) == []
        assert resolve_retrieval_policy("research", ["tavily"]) == ["tavily"]
        assert resolve_retrieval_policy("fresh", []) == []

    def test_unknown_intent_falls_back_to_general(self):
        assert resolve_retrieval_policy("unknown-intent", ["brave", "exa"]) == ["brave", "exa"]
        assert resolve_retrieval_policy("", ["brave", "exa"]) == ["brave", "exa"]

    def test_empty_intersection(self):
        assert resolve_retrieval_policy("general", []) == []
        assert resolve_retrieval_policy("semantic", ["brave"]) == []

    def test_case_insensitive_intent(self):
        assert resolve_retrieval_policy("RESEARCH", ["tavily"]) == ["tavily"]
        assert resolve_retrieval_policy("General", ["brave"]) == ["brave"]

    def test_policy_table_shape(self):
        assert RETRIEVAL_POLICIES == {
            "general": ("brave", "exa"),
            "fresh": ("brave",),
            "semantic": ("exa",),
            "technical": ("brave", "exa"),
            "research": ("brave", "exa", "tavily"),
        }


# ---------------------------------------------------------------------------
# Normalizers (fixture payloads -> DiscoveryCandidate)
# ---------------------------------------------------------------------------


class TestProviderNormalizers:
    def test_brave_payload(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "title": "Brave result",
                    "url": "https://example.com/a",
                    "description": "snippet",
                    "provider": "brave",
                    "age": "2d",
                    "language": "en",
                    "family_friendly": True,
                },
                {"title": "", "url": "https://example.com/empty"},
            ],
        }
        candidates = brave_to_candidates(payload)
        assert len(candidates) == 1
        item = candidates[0]
        assert item.url == "https://example.com/a"
        assert item.title == "Brave result"
        assert item.provider == "brave"
        assert item.snippet == "snippet"
        assert item.provider_rank == 0
        assert item.metadata == {"age": "2d", "language": "en", "family_friendly": True}

    def test_brave_list_shape(self):
        candidates = brave_to_candidates([{"title": "T", "url": "https://example.com/x"}])
        assert len(candidates) == 1
        assert candidates[0].provider_rank == 0

    def test_brave_empty_and_failed(self):
        assert brave_to_candidates({"ok": False, "results": []}) == []
        assert brave_to_candidates({"ok": True, "results": []}) == []
        assert brave_to_candidates(None) == []

    def test_exa_payload(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "title": "Exa result",
                    "url": "https://example.com/b",
                    "publishedDate": "2025-01-02T00:00:00Z",
                    "author": "Ada",
                    "score": 0.87,
                    "id": "exa-id-1",
                    "highlights": ["first highlight"],
                }
            ],
        }
        candidates = exa_to_candidates(payload)
        assert len(candidates) == 1
        item = candidates[0]
        assert item.url == "https://example.com/b"
        assert item.provider == "exa"
        assert item.published_at == "2025-01-02T00:00:00Z"
        assert item.provider_rank == 0
        assert item.snippet == "first highlight"
        assert item.metadata == {"exa_score": 0.87, "author": "Ada", "id": "exa-id-1"}

    def test_exa_url_falls_back_to_id(self):
        candidates = exa_to_candidates(
            {"ok": True, "results": [{"title": "T", "id": "exa://resource", "score": 0.5}]}
        )
        assert candidates[0].url == "exa://resource"

    def test_tavily_payload(self):
        payload = [
            {"title": "Tavily result", "url": "https://example.com/c", "content": "snippet", "score": 0.92},
            {"title": "No score", "url": "https://example.com/d", "content": "x"},
        ]
        candidates = tavily_to_candidates(payload)
        assert len(candidates) == 2
        assert candidates[0].provider == "tavily"
        assert candidates[0].snippet == "snippet"
        assert candidates[0].metadata == {"tavily_score": 0.92}
        assert candidates[1].metadata == {}
        assert candidates[1].provider_rank == 1

    def test_tavily_dict_shape_and_description_fallback(self):
        candidates = tavily_to_candidates(
            {"ok": True, "results": [{"title": "T", "url": "https://example.com/e", "description": "d"}]}
        )
        assert candidates[0].snippet == "d"

    def test_normalizers_skip_blank_url_or_title(self):
        assert brave_to_candidates([{"title": "no url", "url": ""}]) == []
        assert exa_to_candidates([{"url": "https://example.com", "title": " "}]) == []
        assert tavily_to_candidates([{"title": "x", "url": "  "}]) == []

    def test_full_pipeline_fusion(self):
        candidates = brave_to_candidates(
            {"ok": True, "results": [{"title": "Shared", "url": "https://example.com/a?utm_source=z"}]}
        ) + exa_to_candidates(
            {"ok": True, "results": [{"title": "Shared exa", "url": "https://example.com/a", "score": 0.9}]}
        )
        fused = deduplicate_candidates(candidates)
        assert len(fused) == 1
        assert fused[0].providers == ("brave", "exa")
        ranked = reciprocal_rank_fusion(fused)
        assert ranked[0].rrf_score == pytest.approx(2.0 / (DEFAULT_RRF_K + 1))
