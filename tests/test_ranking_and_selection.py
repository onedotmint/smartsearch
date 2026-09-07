"""Direct unit tests for pure RRF ranking and default evidence selection."""

from __future__ import annotations

import pytest

from smart_search.core.models import Candidate, FusedCandidate
from smart_search.core.ranking import (
    DEFAULT_RRF_K,
    canonicalize_url,
    deduplicate_candidates,
    reciprocal_rank_fusion,
)
from smart_search.evidence.select import select_candidates

pytestmark = pytest.mark.unit


def _fused(url: str, providers: tuple[str, ...], ranks: dict[str, int]) -> FusedCandidate:
    return FusedCandidate(url, url, "title", "", providers, ranks)


def test_canonicalize_url_drops_tracking_and_default_https_port():
    assert canonicalize_url("https://Example.COM:443/path?utm_source=x&q=1#frag") == (
        "https://example.com/path?q=1"
    )


def test_deduplicate_candidates_merges_canonical_urls_and_keeps_first_display():
    fused = deduplicate_candidates(
        [
            Candidate("https://example.com/a?utm_source=brave", "A", "brave", provider_rank=0),
            Candidate("https://example.com/a", "A2", "exa", provider_rank=1),
            Candidate("https://example.com/b", "B", "brave", provider_rank=2),
        ]
    )
    assert [item.url for item in fused] == ["https://example.com/a", "https://example.com/b"]
    assert fused[0].providers == ("brave", "exa")
    assert fused[0].provider_ranks == {"brave": 0, "exa": 1}
    assert fused[0].display_url.endswith("utm_source=brave")


def test_reciprocal_rank_fusion_scores_and_tie_breaks_deterministically():
    shared = _fused("https://example.com/z", ("brave", "exa"), {"brave": 0, "exa": 0})
    only = _fused("https://example.com/a", ("brave",), {"brave": 0})
    ranked = reciprocal_rank_fusion([only, shared])
    assert [item.candidate.url for item in ranked] == [
        "https://example.com/z",
        "https://example.com/a",
    ]
    assert ranked[0].rrf_score == pytest.approx(2.0 / (DEFAULT_RRF_K + 1))
    assert ranked[1].rrf_score == pytest.approx(1.0 / (DEFAULT_RRF_K + 1))
    assert [item.rank for item in ranked] == [0, 1]

    tied = reciprocal_rank_fusion(
        [
            _fused("https://example.com/z", ("brave",), {"brave": 0}),
            _fused("https://example.com/a", ("exa",), {"exa": 0}),
        ]
    )
    assert [item.candidate.url for item in tied] == [
        "https://example.com/a",
        "https://example.com/z",
    ]


def test_select_candidates_keeps_first_unique_canonical_urls():
    items = [
        Candidate("https://example.com/a?utm_campaign=x", "A", "brave"),
        Candidate("https://example.com/a", "A duplicate", "exa"),
        Candidate("https://example.com/b", "B", "brave"),
        Candidate("https://example.com/c", "C", "exa"),
    ]
    selected = select_candidates(items, limit=2)
    assert [item.url for item in selected] == [
        "https://example.com/a?utm_campaign=x",
        "https://example.com/b",
    ]
    ranked = reciprocal_rank_fusion(deduplicate_candidates(items))
    assert [item.candidate.url for item in select_candidates(ranked, limit=1)] == [ranked[0].candidate.url]
