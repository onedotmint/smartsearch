"""Pure deterministic ranking primitives for the v1 retrieval core."""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import Candidate, FusedCandidate, RankedCandidate

_TRACKING_QUERY_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src", "ref_url"})
_DEFAULT_PORTS = {"http": "80", "https": "443"}
DEFAULT_RRF_K = 60


def canonicalize_url(url: str) -> str:
    """Conservative canonicalization; only high-confidence URL changes apply."""
    if not isinstance(url, str):
        return ""
    stripped = url.strip()
    try:
        parts = urlparse(stripped)
    except ValueError:
        return stripped
    scheme = parts.scheme.lower()
    hostname = parts.hostname
    if not scheme or not hostname:
        return stripped
    try:
        port = parts.port
    except ValueError:
        return stripped
    if port is not None and _DEFAULT_PORTS.get(scheme) == str(port):
        port = None
    netloc = hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    if parts.username:
        userinfo = parts.username
        if parts.password is not None:
            userinfo = f"{userinfo}:{parts.password}"
        netloc = f"{userinfo}@{netloc}"
    path = parts.path or ""
    if path in ("", "/"):
        path = "/"
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in _TRACKING_QUERY_PARAMS:
            continue
        kept.append((key, value))
    query = urlencode(sorted(kept)) if kept else ""
    return urlunparse((scheme, netloc, path, parts.params, query, ""))


def deduplicate_candidates(candidates: Sequence[Candidate]) -> list[FusedCandidate]:
    groups: dict[str, list[Candidate]] = {}
    order: list[str] = []
    for candidate in candidates:
        key = canonicalize_url(candidate.url)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candidate)
    fused: list[FusedCandidate] = []
    for key in order:
        group = groups[key]
        first = group[0]
        providers: list[str] = []
        ranks: dict[str, int] = {}
        metadata: dict = {}
        for item in group:
            if item.provider not in providers:
                providers.append(item.provider)
            ranks[item.provider] = item.provider_rank
            for name, value in item.metadata.items():
                metadata.setdefault(name, value)
        fused.append(FusedCandidate(key, first.url, first.title, first.snippet,
                                    tuple(providers), ranks, metadata))
    return fused


def reciprocal_rank_fusion(candidates: Sequence[FusedCandidate], *, k: int = DEFAULT_RRF_K) -> list[RankedCandidate]:
    ranked = []
    for candidate in candidates:
        score = sum(1.0 / (k + candidate.provider_ranks.get(provider, 0) + 1) for provider in candidate.providers)
        ranked.append(RankedCandidate(candidate, score))
    ranked.sort(key=lambda item: (-item.rrf_score, item.candidate.url, item.candidate.providers))
    return [replace(item, rank=index) for index, item in enumerate(ranked)]


__all__ = ["DEFAULT_RRF_K", "canonicalize_url", "deduplicate_candidates", "reciprocal_rank_fusion"]
