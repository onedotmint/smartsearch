#!/usr/bin/env python3
"""Replay sanitized raw provider results through the offline retrieval pipeline.

This is maintainer tooling only. It reads captured normalizer inputs and never
constructs a provider registry, makes a request, or runs a reranker.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smart_search.core.models import thaw  # noqa: E402
from smart_search.core.ranking import deduplicate_candidates, reciprocal_rank_fusion  # noqa: E402
from smart_search.core.retrieval import normalize_provider_results  # noqa: E402

SUPPORTED_PROVIDERS = ("brave", "exa", "tavily")
_SCHEMA_KEYS = {
    "schema_version", "fixture_id", "captured_at", "capture_mode", "query", "intent", "providers",
}
_PROVIDER_KEYS = {"provider", "results"}
_DERIVED_KEYS = {
    "canonical_url", "display_url", "provider_ranks", "provenance", "rrf_score", "rank",
    "fused", "fused_candidate", "normalized_candidates", "ranked", "ranked_candidate",
    "candidate", "discovery_candidate", "fused_candidates", "ranked_candidates",
    "rrf_results", "top_k_results", "provider_contribution_counts",
}
_SECRET_KEYS = {
    "access_token", "api_key", "apikey", "authorization", "client_secret", "clientsecret",
    "cookie", "headers", "password", "refresh_token", "secret", "set_cookie", "signature",
    "signed_url", "token", "id_token", "session_token", "bearer_token",
}
_SECRET_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "credential", "expires", "expires_at", "key",
    "signature", "sig", "token", "x_amz_credential", "x_amz_expires", "x_amz_signature",
}


class FixtureError(ValueError):
    """Raised when a replay fixture does not meet the raw-input contract."""


def _key_name(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _check_safe_value(value: Any, *, field: str = "", root: bool = False) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = _key_name(key)
            if normalized_key in _SECRET_KEYS:
                raise FixtureError(f"fixture contains prohibited secret field: {key}")
            if normalized_key in _DERIVED_KEYS or (normalized_key == "providers" and not root):
                raise FixtureError(f"fixture contains derived-state field: {key}")
            _check_safe_value(child, field=normalized_key)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _check_safe_value(child, field=field)
        return
    if field in {"url", "uri"} and isinstance(value, str):
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise FixtureError("fixture contains an invalid URL") from exc
        if parsed.username or parsed.password:
            raise FixtureError("fixture URL must not contain userinfo")
        query_keys = {
            _key_name(key)
            for key, _ in parse_qsl(parsed.query.replace(";", "&"), keep_blank_values=True)
        }
        fragment_keys = {
            _key_name(key)
            for key, _ in parse_qsl(parsed.fragment.replace(";", "&"), keep_blank_values=True)
        }
        if query_keys & _SECRET_QUERY_KEYS:
            raise FixtureError("fixture URL contains a prohibited signed or secret query parameter")
        if fragment_keys & _SECRET_QUERY_KEYS:
            raise FixtureError("fixture URL contains a prohibited token or secret fragment")


def _required_text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"fixture field {name!r} must be a non-empty string")
    return value.strip()


def validate_fixture(value: Any) -> dict[str, Any]:
    """Validate and return one fixture without transforming its raw results."""
    if not isinstance(value, Mapping):
        raise FixtureError("fixture root must be an object")
    unknown = set(value) - _SCHEMA_KEYS
    if unknown:
        raise FixtureError(f"fixture contains unknown field: {sorted(unknown)[0]}")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise FixtureError("fixture schema_version must be 1")
    for name in ("fixture_id", "captured_at", "capture_mode", "query", "intent"):
        _required_text(value, name)
    providers = value.get("providers")
    if not isinstance(providers, list) or not providers:
        raise FixtureError("fixture providers must be a non-empty array")

    seen: set[str] = set()
    for entry in providers:
        if not isinstance(entry, Mapping):
            raise FixtureError("each fixture provider must be an object")
        unknown = set(entry) - _PROVIDER_KEYS
        if unknown:
            raise FixtureError(f"provider entry contains unknown field: {sorted(unknown)[0]}")
        provider = _required_text(entry, "provider").lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise FixtureError(f"unsupported replay provider: {provider}")
        if provider in seen:
            raise FixtureError(f"duplicate replay provider: {provider}")
        seen.add(provider)
        if not isinstance(entry.get("results"), list):
            raise FixtureError(f"{provider} results must be an array")

    _check_safe_value(value, root=True)
    return dict(value)


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"could not read fixture {path.name}: {exc}") from exc
    return validate_fixture(value)


def load_fixtures(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        return [load_fixture(path)]
    if not path.is_dir():
        raise FixtureError(f"fixture path does not exist: {path}")
    paths = sorted(item for item in path.iterdir() if item.suffix.lower() == ".json")
    if not paths:
        raise FixtureError(f"no JSON fixtures found in {path}")
    return [load_fixture(item) for item in paths]


def _ranked_dict(item: Any) -> dict[str, Any]:
    candidate = item.candidate
    providers = list(candidate.providers)
    provider_ranks = dict(candidate.provider_ranks)
    return {
        "rank": item.rank,
        "canonical_url": candidate.url,
        "display_url": candidate.display_url,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "provenance": {"providers": providers, "provider_ranks": provider_ranks},
        "providers": providers,
        "provider_ranks": provider_ranks,
        "score": item.rrf_score,
        "metadata": thaw(candidate.metadata),
    }


def replay_fixture(fixture: Mapping[str, Any], top_k: int = 5) -> dict[str, Any]:
    """Replay one validated fixture using production normalizer and ranking helpers."""
    fixture = validate_fixture(fixture)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0:
        raise ValueError("top_k must be a non-negative integer")

    candidates = []
    provider_reports: list[dict[str, Any]] = []
    provider_order = []
    for entry in fixture["providers"]:
        provider = entry["provider"].strip().lower()
        raw_results = entry["results"]
        normalized = normalize_provider_results(provider, raw_results)
        provider_order.append(provider)
        candidates.extend(normalized)
        provider_reports.append({
            "provider": provider,
            "raw_count": len(raw_results),
            "normalized_count": len(normalized),
            "invalid_count": len(raw_results) - len(normalized),
        })

    fused = deduplicate_candidates(candidates)
    ranked = reciprocal_rank_fusion(fused)
    contributions = Counter(provider for item in fused for provider in item.providers)
    top_ranked = ranked[:top_k]
    top_contributions = Counter(provider for item in top_ranked for provider in item.candidate.providers)
    contribution_counts = {provider: contributions.get(provider, 0) for provider in provider_order}
    top_contribution_counts = {provider: top_contributions.get(provider, 0) for provider in provider_order}
    normalized_count = len(candidates)
    duplicate_count = normalized_count - len(fused)

    return {
        "fixture_id": fixture["fixture_id"],
        "captured_at": fixture["captured_at"],
        "capture_mode": fixture["capture_mode"],
        "query": fixture["query"],
        "intent": fixture["intent"],
        "provider_order": provider_order,
        "providers": provider_reports,
        "totals": {
            "raw": sum(item["raw_count"] for item in provider_reports),
            "normalized": normalized_count,
            "fused": len(fused),
            "duplicates": duplicate_count,
            "invalid": sum(item["invalid_count"] for item in provider_reports),
        },
        "deduplication_rate": duplicate_count / normalized_count if normalized_count else 0.0,
        "provider_contribution_counts": contribution_counts,
        "top_k": top_k,
        "top_k_provider_contribution_counts": top_contribution_counts,
        "rrf_results": [_ranked_dict(item) for item in ranked],
        "top_k_results": [_ranked_dict(item) for item in top_ranked],
    }


def replay_fixtures(fixtures: list[Mapping[str, Any]], top_k: int = 5) -> dict[str, Any]:
    reports = [replay_fixture(fixture, top_k=top_k) for fixture in fixtures]
    aggregate_counts = {"raw": 0, "normalized": 0, "fused": 0, "duplicates": 0, "invalid": 0}
    contributions: Counter[str] = Counter()
    for report in reports:
        for name, count in report["totals"].items():
            aggregate_counts[name] += count
        contributions.update(report["provider_contribution_counts"])
    normalized_count = aggregate_counts["normalized"]
    return {
        "schema_version": 1,
        "fixtures": reports,
        "aggregate": {
            "fixture_count": len(reports),
            "totals": aggregate_counts,
            "deduplication_rate": aggregate_counts["duplicates"] / normalized_count if normalized_count else 0.0,
            "provider_contribution_counts": dict(sorted(contributions.items())),
        },
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Offline retrieval replay", "", "Historical pre-rerank RRF output; this does not measure current provider quality.", ""]
    for fixture in report["fixtures"]:
        lines.extend([
            f"## {fixture['fixture_id']}",
            "",
            f"Query: `{fixture['query']}`  ",
            f"Captured: `{fixture['captured_at']}` ({fixture['capture_mode']})  ",
            f"Providers: `{', '.join(fixture['provider_order'])}`  ",
            f"Totals: raw {fixture['totals']['raw']}, normalized {fixture['totals']['normalized']}, "
            f"fused {fixture['totals']['fused']}, duplicates {fixture['totals']['duplicates']}, "
            f"invalid {fixture['totals']['invalid']}; deduplication rate {fixture['deduplication_rate']:.3f}",
            "",
            "| Rank | Score | Title | Canonical URL | Providers / ranks |",
            "| ---: | ---: | --- | --- | --- |",
        ])
        for item in fixture["rrf_results"]:
            title = item["title"].replace("|", "\\|")
            providers = ", ".join(
                f"{provider}#{item['provider_ranks'][provider]}" for provider in item["providers"]
            )
            lines.append(
                f"| {item['rank']} | {item['score']:.9f} | {title} | {item['canonical_url']} | {providers} |"
            )
        lines.append("")
    aggregate = report["aggregate"]
    lines.extend([
        "## Aggregate",
        "",
        f"Fixtures: {aggregate['fixture_count']}; deduplication rate: {aggregate['deduplication_rate']:.3f}",
        "",
        "| Raw | Normalized | Fused | Duplicates | Invalid | Provider contributions |",
        "| ---: | ---: | ---: | ---: | ---: | --- |",
        f"| {aggregate['totals']['raw']} | {aggregate['totals']['normalized']} | "
        f"{aggregate['totals']['fused']} | {aggregate['totals']['duplicates']} | "
        f"{aggregate['totals']['invalid']} | {aggregate['provider_contribution_counts']} |",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay sanitized provider results without network access.")
    parser.add_argument("fixture_path", nargs="?", help="a fixture JSON file or directory of JSON fixtures")
    parser.add_argument("--fixtures", dest="fixture_option", help="a fixture JSON file or directory of JSON fixtures")
    parser.add_argument("--top-k", type=int, default=5, help="number of results in the top-k projection")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    fixture_path = args.fixture_option or args.fixture_path
    if not fixture_path:
        parser.error("one fixture path is required (use --fixtures PATH)")
    try:
        report = replay_fixtures(load_fixtures(Path(fixture_path)), top_k=args.top_k)
    except (FixtureError, ValueError) as exc:
        print(f"replay fixture error: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        print(_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
