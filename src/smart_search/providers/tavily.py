"""Tavily raw-result normalizer for the v1 retrieval core.

This module performs no network I/O. The callable accepts the provider's
original ``results`` list shape so captured responses can be replayed offline.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..core.models import Candidate


def to_discovery_candidates(payload: Any) -> list[Candidate]:
    """Convert a raw Tavily results list (or a ``{"results": [...]}`` payload)."""
    results = payload.get("results") if isinstance(payload, Mapping) else payload
    candidates: list[Candidate] = []
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
            Candidate(
                url=url,
                title=title,
                provider="tavily",
                snippet=snippet,
                provider_rank=index,
                metadata=metadata,
            )
        )
    return candidates
