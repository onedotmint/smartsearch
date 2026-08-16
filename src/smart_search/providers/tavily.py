"""Tavily -> DiscoveryCandidate normalization (v0.3.0 retrieval gateway).

Pure mapping only. Tavily transport stays in
``provider_search_commands.call_tavily_search``; this module never performs
network I/O and must not be imported by the transport path.
"""

from __future__ import annotations

from typing import Any, Mapping


def to_discovery_candidates(payload: Any) -> list["DiscoveryCandidate"]:
    """Map a Tavily result payload to ``DiscoveryCandidate`` values.

    Accepts the normalized list produced by ``call_tavily_search``
    (``{title, url, content, score}``) or a payload mapping with a
    ``results`` list (both shapes appear in tests and cached values). The
    provider-native ``score`` is retained in ``metadata["tavily_score"]`` for
    diagnostics only and never becomes a shared ranking signal.

    ``DiscoveryCandidate`` is imported lazily to keep this adapter module free
    of an import cycle with the retrieval core.
    """
    from ..retrieval import DiscoveryCandidate

    results = payload.get("results") if isinstance(payload, Mapping) else payload
    candidates: list[DiscoveryCandidate] = []
    for index, item in enumerate(results or []):
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        snippet = str(item.get("content") or item.get("description") or "").strip()
        score = item.get("score")
        metadata: dict[str, Any] = {}
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            metadata["tavily_score"] = float(score)
        candidates.append(
            DiscoveryCandidate(
                url=url,
                title=title,
                provider="tavily",
                snippet=snippet,
                provider_rank=index,
                metadata=metadata,
            )
        )
    return candidates
