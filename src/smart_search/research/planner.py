"""Offline v1 research planning data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResearchPlan:
    query: str
    stages: tuple[dict[str, Any], ...]
    budget: str = "balanced"

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "budget": self.budget, "stages": [dict(stage) for stage in self.stages]}


def plan(query: str, *, budget: str = "balanced") -> ResearchPlan:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    budget = str(budget or "balanced").strip().lower()
    if budget not in {"balanced", "quick", "deep"}:
        budget = "balanced"
    return ResearchPlan(query, (
        {"name": "search", "order": 0, "operation": "search", "query": query},
        {"name": "read", "order": 1, "operation": "read", "depends_on": ["search"]},
    ), budget)


build_plan = plan
__all__ = ["ResearchPlan", "build_plan", "plan"]
