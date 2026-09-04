"""Pure raw-result normalizers shared by retrieval and provider adapters.

These functions accept a provider's ordered ``results`` list (or its existing
mapping envelope) and do not import provider transports, configuration, or
networking code.
"""
from __future__ import annotations

from typing import Any, Mapping

from .models import Candidate


def normalize_brave(payload: Any) -> list[Candidate]:
    """Map a Brave result payload to ``Candidate`` values."""
    results = payload.get("results") if isinstance(payload, dict) else payload
    candidates: list[Candidate] = []
    for index, item in enumerate(results or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        metadata: dict[str, Any] = {}
        for key in ("age", "language", "family_friendly", "page_age"):
            if item.get(key) is not None:
                metadata[key] = item[key]
        candidates.append(
            Candidate(
                url=url,
                title=title,
                provider="brave",
                snippet=str(item.get("description") or "").strip(),
                provider_rank=index,
                metadata=metadata,
            )
        )
    return candidates


def normalize_exa(payload: Any) -> list[Candidate]:
    """Map an Exa result payload to ``Candidate`` values."""
    results = payload.get("results") if isinstance(payload, dict) else payload
    candidates: list[Candidate] = []
    for index, item in enumerate(results or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        metadata: dict[str, Any] = {}
        score = item.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            metadata["exa_score"] = float(score)
        if item.get("author"):
            metadata["author"] = str(item["author"])
        if item.get("id"):
            metadata["id"] = str(item["id"])
        snippet = ""
        if isinstance(item.get("text"), str):
            snippet = item["text"]
        elif isinstance(item.get("highlights"), list):
            snippet = " ".join(str(part) for part in item["highlights"])
        candidates.append(
            Candidate(
                url=url,
                title=title,
                provider="exa",
                snippet=snippet.strip(),
                published_at=str(item.get("publishedDate") or ""),
                provider_rank=index,
                metadata=metadata,
            )
        )
    return candidates


def normalize_tavily(payload: Any) -> list[Candidate]:
    """Map a Tavily result payload to ``Candidate`` values."""
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


__all__ = ["normalize_brave", "normalize_exa", "normalize_tavily"]
