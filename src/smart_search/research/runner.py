"""Evidence-only research composition for v1."""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.models import Evidence, ResearchRun
from ..core.ranking import canonicalize_url
from ..core.retrieval import RetrievalOutcome, search
from ..evidence.fetch import FetchOutcome, read
from ..evidence.select import select_candidates
from .planner import ResearchPlan, plan

MAX_EVIDENCE = 5
FETCH_CONCURRENCY = 4


def _candidate_value(item: Any) -> Any:
    return getattr(item, "candidate", item)


async def run(
    query: str,
    *,
    plan_data: ResearchPlan | None = None,
    search_fn: Callable[..., Awaitable[RetrievalOutcome]] | None = None,
    read_fn: Callable[..., Awaitable[FetchOutcome]] | None = None,
    max_evidence: int = MAX_EVIDENCE,
    concurrency: int = FETCH_CONCURRENCY,
) -> ResearchRun:
    """Run search then bounded reads; never synthesize or add an answer."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    max_evidence = min(MAX_EVIDENCE, int(max_evidence))
    if max_evidence < 1 or concurrency < 1:
        raise ValueError("evidence and concurrency limits must be positive")
    plan_data = plan_data or plan(query)
    search_fn = search_fn or search
    read_fn = read_fn or read
    found = search_fn(query)
    found = await found if inspect.isawaitable(found) else found
    if not isinstance(found, RetrievalOutcome):
        raise TypeError("search_fn must return RetrievalOutcome")
    selected = select_candidates(found.ranked, max_evidence)
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(item: Any) -> FetchOutcome:
        candidate = _candidate_value(item)
        async with semaphore:
            value = read_fn(candidate.url)
            return await value if inspect.isawaitable(value) else value

    fetched = await asyncio.gather(*(fetch_one(item) for item in selected), return_exceptions=True)
    evidence: list[Evidence] = []
    search_attempts = [attempt.to_dict() for attempt in found.attempts]
    search_failed = found.failed or any(item.get("status") in {"failed", "error"} for item in search_attempts)
    attempts = list(search_attempts)
    gaps: list[dict[str, Any]] = []
    if search_failed:
        gaps.append({"reason": "search_failed", "stage": "search"})
    elif not selected:
        gaps.append({"reason": "no_candidates", "stage": "search"})
    stages: list[dict[str, Any]] = [{
        "name": "search", "order": 0,
        "status": "failed" if search_failed else "complete",
        "count": len(found.ranked),
    }]
    seen_urls: set[str] = set()
    read_degraded = False
    for item, outcome in zip(selected, fetched):
        candidate = _candidate_value(item)
        if isinstance(outcome, BaseException):
            gaps.append({"reason": "read_failed", "url": candidate.url})
            continue
        if not isinstance(outcome, FetchOutcome):
            gaps.append({"reason": "read_invalid_result", "url": candidate.url})
            continue
        attempts.extend(attempt.to_dict() for attempt in outcome.attempts)
        if outcome.degraded:
            read_degraded = True
            gaps.append({"reason": "read_degraded", "url": candidate.url})
        if outcome.evidence is None:
            gaps.append({"reason": "read_failed", "url": candidate.url})
            continue
        key = canonicalize_url(outcome.evidence.url)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        evidence.append(outcome.evidence)
    if len(selected) > len(evidence):
        gaps.append({"reason": "insufficient_evidence", "requested": len(selected), "received": len(evidence)})
    read_status = "complete" if evidence and not read_degraded and not any(
        gap.get("reason") in {"read_failed", "read_invalid_result"} for gap in gaps
    ) else ("degraded" if evidence else "failed")
    stages.append({"name": "read", "order": 1, "status": read_status, "count": len(evidence)})
    citations = tuple({"evidence_id": item.id, "url": item.url, "title": item.title or item.url, "provider": item.provider} for item in evidence)
    return ResearchRun(query, tuple(evidence), citations, tuple(gaps), tuple(attempts), tuple(stages), tuple(selected))


research = run
run_research = run
__all__ = ["FETCH_CONCURRENCY", "MAX_EVIDENCE", "research", "run", "run_research"]
