"""Deterministic evidence/candidate selection helpers."""
from __future__ import annotations

from typing import Iterable, Sequence

from ..core.models import Candidate, RankedCandidate
from ..core.ranking import canonicalize_url


def select_candidates(candidates: Sequence[RankedCandidate | Candidate], limit: int = 5) -> list[RankedCandidate | Candidate]:
    """Select the first bounded unique URLs, preserving ranking order."""
    limit = max(0, int(limit))
    selected = []
    seen: set[str] = set()
    for item in candidates:
        candidate = item.candidate if isinstance(item, RankedCandidate) else item
        key = canonicalize_url(candidate.url)
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


# Name used by callers that select URLs for research fetch stages.
bounded_selection = select_candidates
__all__ = ["bounded_selection", "select_candidates"]
