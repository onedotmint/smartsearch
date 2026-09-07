#!/usr/bin/env python3
"""Evaluation v2: dataset validation, IR/web metrics, and deterministic reports.

Standard Recall/MRR/nDCG/MAP calculations import ir-measures only inside
``standard_metrics()``. Production ``src/`` must never import this module.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from smart_search.core.ranking import canonicalize_url  # noqa: E402

SCHEMA_VERSION = 1
DATASET_ID = "evaluation-v1"
DATASET_REVISION = "v1"
PRIMARY_K = 5
MIN_JUDGED_QUERY_FRACTION = 0.5
DEFAULT_DATASET_DIR = Path(__file__).resolve().parent / "fixtures" / "evaluation" / "v1"
CATEGORIES = (
    "general",
    "fresh",
    "technical",
    "academic",
    "company/entity",
    "long-tail",
    "comparison",
    "research",
    "navigational",
    "official-document",
    "ambiguous",
)
EVAL_MODES: dict[str, dict[str, Any]] = {
    "brave": {"providers": ("brave",), "intent": "general", "rerank": False},
    "exa": {"providers": ("exa",), "intent": "semantic", "rerank": False},
    "tavily": {"providers": ("tavily",), "intent": "research", "rerank": False},
    "rrf": {"providers": ("brave", "exa"), "intent": "general", "rerank": False},
    "rrf_jina": {"providers": ("brave", "exa"), "intent": "general", "rerank": True},
}
_PROVIDER_KEY = {"brave": "BRAVE_API_KEY", "exa": "EXA_API_KEY", "tavily": "TAVILY_API_KEY"}
_SECRET_KEYS = {
    "access_token", "api_key", "apikey", "authorization", "client_secret",
    "cookie", "headers", "password", "refresh_token", "secret", "token",
}
_FAILED = {"failed", "error"}
_METRIC_NAMES = {
    "R": "Recall",
    "RR": "MRR",
    "nDCG": "nDCG",
    "AP": "MAP",
}


class DatasetError(ValueError):
    """Raised when an evaluation dataset or run fixture is invalid."""


def _round(value: Any) -> float:
    return round(float(value), 6)


def _key_name(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _check_secrets(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _key_name(key) in _SECRET_KEYS:
                raise DatasetError(f"prohibited secret field: {key}")
            _check_secrets(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _check_secrets(child)


def _required_text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{name} must be a non-empty string")
    return value.strip()


def document_id(url: str) -> str:
    canonical = canonicalize_url(str(url or "").strip())
    if not canonical:
        raise DatasetError("url must canonicalize to a non-empty document id")
    return canonical


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def domain_matches(url: str, domains: Sequence[str]) -> bool:
    host = _host(url)
    if not host:
        return False
    for domain in domains:
        needle = str(domain).strip().lower().lstrip(".")
        if not needle:
            continue
        if host == needle or host.endswith("." + needle):
            return True
    return False


def validate_query(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasetError("query record must be an object")
    _check_secrets(value)
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise DatasetError("query schema_version must be 1")
    query_id = _required_text(value, "query_id")
    query = _required_text(value, "query")
    category = _required_text(value, "category")
    if category not in CATEGORIES:
        raise DatasetError(f"unknown query category: {category}")
    intent = str(value.get("intent") or category).strip()
    captured_at = _required_text(value, "captured_at")
    authoritative = value.get("authoritative_domains") or []
    preferred = value.get("preferred_domains") or []
    if not isinstance(authoritative, list) or not all(isinstance(item, str) and item.strip() for item in authoritative):
        raise DatasetError("authoritative_domains must be a list of strings")
    if not isinstance(preferred, list) or not all(isinstance(item, str) and item.strip() for item in preferred):
        raise DatasetError("preferred_domains must be a list of strings")
    freshness_expectation = value.get("freshness_expectation")
    if freshness_expectation is not None and (not isinstance(freshness_expectation, str) or not freshness_expectation.strip()):
        raise DatasetError("freshness_expectation must be a non-empty string when present")
    if category == "fresh" and not freshness_expectation:
        raise DatasetError("fresh queries require freshness_expectation")
    record = {
        "schema_version": SCHEMA_VERSION,
        "query_id": query_id,
        "query": query,
        "category": category,
        "intent": intent,
        "captured_at": captured_at,
        "authoritative_domains": [item.strip().lower() for item in authoritative],
        "preferred_domains": [item.strip().lower() for item in preferred],
    }
    if freshness_expectation:
        record["freshness_expectation"] = freshness_expectation.strip()
    return record


def validate_qrel(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasetError("qrel record must be an object")
    _check_secrets(value)
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise DatasetError("qrel schema_version must be 1")
    query_id = _required_text(value, "query_id")
    url = document_id(_required_text(value, "url"))
    relevance = value.get("relevance")
    if type(relevance) is not int or relevance < 0 or relevance > 3:
        raise DatasetError("relevance must be an integer in 0..3")
    source_type = _required_text(value, "source_type")
    judged_at = _required_text(value, "judged_at")
    record = {
        "schema_version": SCHEMA_VERSION,
        "query_id": query_id,
        "url": url,
        "relevance": relevance,
        "source_type": source_type,
        "judged_at": judged_at,
    }
    freshness = value.get("freshness")
    if freshness is not None:
        if freshness not in {"current", "stale"}:
            raise DatasetError("freshness must be 'current' or 'stale'")
        record["freshness"] = freshness
    return record


def validate_run(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasetError("run record must be an object")
    _check_secrets(value)
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise DatasetError("run schema_version must be 1")
    query_id = _required_text(value, "query_id")
    urls = value.get("urls")
    if not isinstance(urls, list) or not all(isinstance(item, str) and item.strip() for item in urls):
        raise DatasetError("run urls must be a list of strings")
    canonical_urls: list[str] = []
    seen: set[str] = set()
    for item in urls:
        doc_id = document_id(item)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        canonical_urls.append(doc_id)
    providers = value.get("providers") or [[] for _ in canonical_urls]
    if not isinstance(providers, list) or len(providers) < len(canonical_urls):
        raise DatasetError("run providers must align with urls")
    normalized_providers = []
    for item in providers[: len(canonical_urls)]:
        if not isinstance(item, list) or not all(isinstance(name, str) and name.strip() for name in item):
            raise DatasetError("run providers must be lists of provider ids")
        normalized_providers.append([name.strip().lower() for name in item])
    attempts = value.get("attempts") or []
    if not isinstance(attempts, list):
        raise DatasetError("run attempts must be a list")
    elapsed = value.get("elapsed_ms", 0)
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise DatasetError("elapsed_ms is invalid")
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "query_id": query_id,
        "urls": canonical_urls,
        "providers": normalized_providers,
        "attempts": attempts,
        "elapsed_ms": float(elapsed),
    }
    if value.get("mode"):
        record["mode"] = str(value["mode"]).strip()
    if value.get("evidence_urls"):
        record["evidence_urls"] = [document_id(item) for item in value["evidence_urls"]]
    if value.get("fetch_attempts"):
        record["fetch_attempts"] = list(value["fetch_attempts"])
    if value.get("warnings"):
        record["warnings"] = [str(item) for item in value["warnings"]]
    return record


def _read_jsonl(path: Path) -> list[Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DatasetError(f"could not read {path.name}: {exc}") from exc
    rows: list[Any] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path.name} line {index} is not JSON") from exc
    if not rows:
        raise DatasetError(f"{path.name} is empty")
    return rows


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    revision: str
    queries: tuple[dict[str, Any], ...]
    qrels: tuple[dict[str, Any], ...]

    @property
    def query_ids(self) -> tuple[str, ...]:
        return tuple(item["query_id"] for item in self.queries)


def load_dataset(path: Path | None = None) -> Dataset:
    directory = Path(path or DEFAULT_DATASET_DIR)
    queries = [validate_query(item) for item in _read_jsonl(directory / "queries.jsonl")]
    qrels = [validate_qrel(item) for item in _read_jsonl(directory / "qrels.jsonl")]
    seen: set[str] = set()
    for item in queries:
        if item["query_id"] in seen:
            raise DatasetError(f"duplicate query_id: {item['query_id']}")
        seen.add(item["query_id"])
    present = {item["category"] for item in queries}
    missing = [name for name in CATEGORIES if name not in present]
    if missing:
        raise DatasetError(f"dataset missing categories: {', '.join(missing)}")
    qrel_keys: set[tuple[str, str]] = set()
    query_ids = {item["query_id"] for item in queries}
    by_query: dict[str, list[dict[str, Any]]] = {item["query_id"]: [] for item in queries}
    for item in qrels:
        if item["query_id"] not in query_ids:
            raise DatasetError(f"qrel for unknown query_id: {item['query_id']}")
        key = (item["query_id"], item["url"])
        if key in qrel_keys:
            raise DatasetError(f"duplicate qrel: {item['query_id']} {item['url']}")
        qrel_keys.add(key)
        by_query[item["query_id"]].append(item)
    for item in queries:
        grades = [qrel["relevance"] for qrel in by_query[item["query_id"]]]
        if not any(grade >= 1 for grade in grades):
            raise DatasetError(f"query {item['query_id']} has no positive judgement")
        if item["category"] == "fresh" and not any(qrel.get("freshness") for qrel in by_query[item["query_id"]]):
            raise DatasetError(f"fresh query {item['query_id']} needs a freshness judgement")
    return Dataset(DATASET_ID, DATASET_REVISION, tuple(queries), tuple(qrels))


def load_runs(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    file_path = Path(path or (DEFAULT_DATASET_DIR / "runs.jsonl"))
    grouped: dict[str, list[dict[str, Any]]] = {mode: [] for mode in EVAL_MODES}
    for item in _read_jsonl(file_path):
        record = validate_run(item)
        mode = record.get("mode")
        if mode not in EVAL_MODES:
            raise DatasetError(f"unknown run mode: {mode}")
        grouped[mode].append(record)
    for mode, rows in grouped.items():
        if not rows:
            raise DatasetError(f"missing runs for mode: {mode}")
    return grouped


def to_qrels_mapping(qrels: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    mapping: dict[str, dict[str, int]] = {}
    for item in qrels:
        record = item if "url" in item and "relevance" in item else validate_qrel(item)
        mapping.setdefault(record["query_id"], {})[record["url"]] = int(record["relevance"])
    return mapping


def to_run_mapping(runs: Sequence[Mapping[str, Any]], k: int = PRIMARY_K) -> dict[str, dict[str, float]]:
    mapping: dict[str, dict[str, float]] = {}
    for item in runs:
        record = item if "urls" in item else validate_run(item)
        ranked: dict[str, float] = {}
        for rank, url in enumerate(record["urls"][:k]):
            ranked.setdefault(url, 1.0 / (rank + 1))
        mapping[record["query_id"]] = ranked
    return mapping


def _ir_measures():
    try:
        import ir_measures
        from ir_measures import AP, RR, R, nDCG
    except ImportError as exc:
        raise RuntimeError(
            "ir-measures is required for standard IR metrics; install with pip install '.[eval]'"
        ) from exc
    return ir_measures, nDCG, AP, RR, R


def _metric_name(measure: Any, k: int) -> str:
    raw = str(measure)
    prefix = raw.split("@", 1)[0]
    if prefix not in _METRIC_NAMES:
        raise DatasetError(f"unexpected ir-measures name: {raw}")
    return f"{_METRIC_NAMES[prefix]}@{k}"


def standard_metrics(
    qrels_map: Mapping[str, Mapping[str, int]],
    run_map: Mapping[str, Mapping[str, float]],
    k: int = PRIMARY_K,
) -> dict[str, Any]:
    ir_measures, nDCG, AP, RR, R = _ir_measures()
    measures = [R @ k, RR @ k, nDCG @ k, AP @ k]
    run = {query_id: dict(run_map.get(query_id) or {}) for query_id in qrels_map}
    aggregate = {
        _metric_name(measure, k): _round(value)
        for measure, value in ir_measures.calc_aggregate(measures, qrels_map, run).items()
    }
    per_query = {query_id: {} for query_id in qrels_map}
    for metric in ir_measures.iter_calc(measures, qrels_map, run):
        per_query[metric.query_id][_metric_name(metric.measure, k)] = _round(metric.value)
    return {"aggregate": aggregate, "per_query": per_query}


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return _round(ordered[0])
    index = min(len(ordered) - 1, max(0, math.ceil((p / 100) * len(ordered)) - 1))
    return _round(ordered[index])


def _qrel_index(qrels: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Mapping[str, Any]]]:
    index: dict[str, dict[str, Mapping[str, Any]]] = {}
    for item in qrels:
        index.setdefault(item["query_id"], {})[item["url"]] = item
    return index


def web_metrics(
    queries: Sequence[Mapping[str, Any]],
    qrels: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    k: int = PRIMARY_K,
) -> dict[str, Any]:
    by_query = {item["query_id"]: item for item in runs}
    qrel_index = _qrel_index(qrels)
    official_hits = 0
    preferred_hits = 0
    judged_queries = 0
    judged_returned = 0
    returned = 0
    diversity_scores: list[float] = []
    diversity_counts: list[int] = []
    contribution: dict[str, int] = {}
    attempt_total = 0
    attempt_failed = 0
    attempt_elapsed: list[float] = []
    run_elapsed: list[float] = []
    evidence_total = 0
    evidence_hits = 0
    fetch_total = 0
    fetch_ok = 0
    fresh_total = 0
    fresh_hits = 0
    for query in queries:
        query_id = query["query_id"]
        run = by_query.get(query_id) or {"urls": [], "providers": [], "attempts": []}
        urls = list(run.get("urls") or [])[:k]
        providers = list(run.get("providers") or [])[: len(urls)]
        judged = qrel_index.get(query_id, {})
        returned += len(urls)
        judged_in_run = [url for url in urls if url in judged]
        judged_returned += len(judged_in_run)
        if judged_in_run:
            judged_queries += 1
        if any(domain_matches(url, query.get("authoritative_domains") or []) for url in urls):
            official_hits += 1
        if any(domain_matches(url, query.get("preferred_domains") or []) for url in urls):
            preferred_hits += 1
        hosts = [host for host in (_host(url) for url in urls) if host]
        unique_hosts = len(set(hosts))
        diversity_counts.append(unique_hosts)
        diversity_scores.append((unique_hosts / k) if k else 0.0)
        for names in providers:
            for name in names:
                contribution[name] = contribution.get(name, 0) + 1
        for attempt in run.get("attempts") or []:
            attempt_total += 1
            status = str(attempt.get("status") or "")
            if status in _FAILED:
                attempt_failed += 1
            elapsed = attempt.get("elapsed_ms")
            if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
                attempt_elapsed.append(float(elapsed))
        elapsed = run.get("elapsed_ms")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            run_elapsed.append(float(elapsed))
        evidence_urls = [document_id(item) for item in run.get("evidence_urls") or []]
        if evidence_urls or run.get("fetch_attempts"):
            evidence_total += 1
            if any(int(judged.get(url, {}).get("relevance") or 0) >= 1 for url in evidence_urls):
                evidence_hits += 1
        for attempt in run.get("fetch_attempts") or []:
            fetch_total += 1
            if str(attempt.get("status") or "") not in _FAILED:
                fetch_ok += 1
        if query.get("freshness_expectation"):
            fresh_total += 1
            if any(judged.get(url, {}).get("freshness") == "current" and int(judged.get(url, {}).get("relevance") or 0) >= 1 for url in urls):
                fresh_hits += 1
    n = len(queries) or 1
    coverage = (judged_returned / returned) if returned else 0.0
    judged_fraction = judged_queries / n
    result: dict[str, Any] = {
        "official_source_hit_rate": _round(official_hits / n),
        "preferred_domain_hit_rate": _round(preferred_hits / n),
        "domain_diversity_mean": _round(statistics.fmean(diversity_counts) if diversity_counts else 0.0),
        "domain_diversity_ratio": _round(statistics.fmean(diversity_scores) if diversity_scores else 0.0),
        "provider_contribution": dict(sorted(contribution.items())),
        "failure_rate": _round((attempt_failed / attempt_total) if attempt_total else 0.0),
        "latency_ms": {
            "median": _round(statistics.median(run_elapsed)) if run_elapsed else None,
            "p95": _percentile(run_elapsed, 95),
            "attempt_median": _round(statistics.median(attempt_elapsed)) if attempt_elapsed else None,
        },
        "judgement_coverage": _round(coverage),
        "judged_query_fraction": _round(judged_fraction),
        "query_count": len(queries),
        "judged_query_count": judged_queries,
    }
    if evidence_total:
        result["evidence_coverage"] = _round(evidence_hits / evidence_total)
    if fetch_total:
        result["fetch_success_rate"] = _round(fetch_ok / fetch_total)
    if fresh_total:
        result["freshness_correctness"] = _round(fresh_hits / fresh_total)
    return result


def mode_configured(registry: Any, spec: Mapping[str, Any]) -> tuple[bool, str]:
    missing = [name for name in spec["providers"] if registry.search_provider(name) is None]
    if missing:
        return False, "missing " + ", ".join(_PROVIDER_KEY[name] for name in missing)
    if spec.get("rerank") and registry.reranker() is None:
        return False, "missing JINA_API_KEY"
    return True, ""


def runs_from_replay(report: Mapping[str, Any], *, query_id: str, mode: str = "rrf") -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for fixture in report.get("fixtures") or ():
        top = list(fixture.get("top_k_results") or fixture.get("rrf_results") or ())
        record = validate_run(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": mode,
                "query_id": query_id,
                "urls": [item["canonical_url"] for item in top],
                "providers": [list(item.get("providers") or []) for item in top],
                "attempts": [
                    {
                        "provider": item["provider"],
                        "status": "complete",
                        "result_count": item.get("normalized_count", 0),
                        "elapsed_ms": 0.0,
                    }
                    for item in fixture.get("providers") or ()
                ],
                "elapsed_ms": 0.0,
            }
        )
        runs.append(record)
    return runs


def evaluate_mode(
    queries: Sequence[Mapping[str, Any]],
    qrels: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    *,
    k: int = PRIMARY_K,
) -> dict[str, Any]:
    qrels_map = to_qrels_mapping(qrels)
    ir = standard_metrics(qrels_map, to_run_mapping(runs, k=k), k=k)
    web = web_metrics(queries, qrels, runs, k=k)
    judged_fraction = web["judged_query_fraction"]
    status = "ok" if judged_fraction >= MIN_JUDGED_QUERY_FRACTION else "inconclusive"
    return {
        "status": status,
        "k": k,
        "standard": ir["aggregate"],
        "per_query": ir["per_query"],
        "web": web,
    }


def evaluate_report(
    *,
    queries: Sequence[Mapping[str, Any]],
    qrels: Sequence[Mapping[str, Any]],
    runs_by_mode: Mapping[str, Sequence[Mapping[str, Any]]],
    skipped: Mapping[str, str] | None = None,
    k: int = PRIMARY_K,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    skipped = dict(skipped or {})
    modes: dict[str, Any] = {}
    for mode in EVAL_MODES:
        if mode in skipped:
            modes[mode] = {"status": "skipped", "reason": skipped[mode]}
            continue
        if mode not in runs_by_mode:
            modes[mode] = {"status": "skipped", "reason": "no runs"}
            continue
        modes[mode] = evaluate_mode(queries, qrels, runs_by_mode[mode], k=k)
    report = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "k": k,
        "modes": modes,
        "metadata": dict(metadata or {}),
    }
    return json.loads(json.dumps(report, sort_keys=True, ensure_ascii=False))


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_jsonl(report: Mapping[str, Any]) -> str:
    rows: list[dict[str, Any]] = []
    for mode, result in report["modes"].items():
        if result.get("status") == "skipped":
            rows.append(
                {
                    "mode": mode,
                    "status": "skipped",
                    "reason": result.get("reason", ""),
                    "dataset_id": report["dataset_id"],
                    "dataset_revision": report["dataset_revision"],
                    "k": report["k"],
                }
            )
            continue
        per_query = result.get("per_query") or {}
        for query_id, metrics in per_query.items():
            row = {
                "mode": mode,
                "status": result.get("status"),
                "query_id": query_id,
                "dataset_id": report["dataset_id"],
                "dataset_revision": report["dataset_revision"],
                "k": report["k"],
            }
            row.update(metrics)
            rows.append(row)
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Retrieval evaluation v2",
        "",
        f"Dataset `{report['dataset_id']}` revision `{report['dataset_revision']}` at K={report['k']}.",
        "Live evaluation is not a CI or correctness gate.",
        "",
        "## Modes",
        "",
        "| Mode | Status | Recall@5 | MRR@5 | nDCG@5 | MAP@5 | Official@5 | Coverage | Note |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for mode, result in report["modes"].items():
        if result.get("status") == "skipped":
            lines.append(f"| {mode} | skipped | | | | | | | {result.get('reason', '')} |")
            continue
        standard = result.get("standard") or {}
        web = result.get("web") or {}
        k = result.get("k", report["k"])
        lines.append(
            "| {mode} | {status} | {recall} | {mrr} | {ndcg} | {map_} | {official} | {coverage} | {note} |".format(
                mode=mode,
                status=result.get("status"),
                recall=standard.get(f"Recall@{k}", ""),
                mrr=standard.get(f"MRR@{k}", ""),
                ndcg=standard.get(f"nDCG@{k}", ""),
                map_=standard.get(f"MAP@{k}", ""),
                official=web.get("official_source_hit_rate", ""),
                coverage=web.get("judgement_coverage", ""),
                note="inconclusive" if result.get("status") == "inconclusive" else "",
            )
        )
    lines.extend(["", "## Web metrics", ""])
    for mode, result in report["modes"].items():
        if result.get("status") == "skipped":
            continue
        web = result.get("web") or {}
        lines.append(f"### {mode}")
        lines.append("")
        lines.append(f"- provider contribution: `{web.get('provider_contribution', {})}`")
        lines.append(f"- failure rate: {web.get('failure_rate')}")
        latency = web.get("latency_ms") or {}
        lines.append(f"- latency ms median/p95: {latency.get('median')}/{latency.get('p95')}")
        if "evidence_coverage" in web:
            lines.append(f"- evidence coverage: {web['evidence_coverage']}")
        if "fetch_success_rate" in web:
            lines.append(f"- fetch success rate: {web['fetch_success_rate']}")
        if "freshness_correctness" in web:
            lines.append(f"- freshness correctness: {web['freshness_correctness']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_artifacts(report: Mapping[str, Any], out_dir: Path, *, runs_by_mode: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(render_json(report), encoding="utf-8")
    (out_dir / "results.jsonl").write_text(render_jsonl(report), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    if runs_by_mode is not None:
        lines = []
        for mode in EVAL_MODES:
            for row in runs_by_mode.get(mode, ()):
                payload = dict(row)
                payload.setdefault("mode", mode)
                lines.append(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        (out_dir / "runs.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
