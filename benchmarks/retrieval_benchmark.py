#!/usr/bin/env python3
"""v0.3.0 retrieval benchmark runner.

LIVE PROVIDER CALLS — NOT A CI GATE.
=============================

This script issues real network requests to Brave / Exa / Tavily / Jina and
consumes real API quota. It is a manual visibility and evidence-collection
tool, never part of CI, never a correctness gate, and never a substitute for
the deterministic pytest suite.

Modes (each is a valid retrieval-policy expression):

    brave-only             intent=fresh    providers=[brave]
    exa-only               intent=semantic providers=[exa]
    tavily-only            intent=research providers=[tavily]
    brave+exa+RRF          intent=general  providers=[brave, exa]
    brave+exa+tavily+RRF   intent=research providers=[brave, exa, tavily]
    RRF+Jina               intent=general  providers=[brave, exa] (+ rerank)

A mode is skipped (with a note) when its providers are not configured.

Usage:

    python benchmarks/retrieval_benchmark.py --help
    python benchmarks/retrieval_benchmark.py --max-queries 10
    python benchmarks/retrieval_benchmark.py --modes brave-only,exa-only
    python benchmarks/retrieval_benchmark.py --output /tmp/retrieval-bench
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smart_search.config import config  # noqa: E402
from smart_search.retrieval import retrieve  # noqa: E402

QUERIES_FILE = Path(__file__).resolve().parent / "retrieval_queries.jsonl"

MODES: dict[str, dict[str, Any]] = {
    "brave-only": {"providers": ["brave"], "intent": "fresh"},
    "exa-only": {"providers": ["exa"], "intent": "semantic"},
    "tavily-only": {"providers": ["tavily"], "intent": "research"},
    "brave+exa+RRF": {"providers": ["brave", "exa"], "intent": "general"},
    "brave+exa+tavily+RRF": {"providers": ["brave", "exa", "tavily"], "intent": "research"},
    "RRF+Jina": {"providers": ["brave", "exa"], "intent": "general"},
}

_PROVIDER_KEY = {
    "brave": "BRAVE_API_KEY",
    "exa": "EXA_API_KEY",
    "tavily": "TAVILY_API_KEY",
}


def _mode_configured(providers: list[str], *, rerank: bool) -> tuple[bool, str]:
    missing = [p for p in providers if not getattr(config, f"{p}_api_key", None)]
    if missing:
        return False, f"missing {', '.join(_PROVIDER_KEY[p] for p in missing)}"
    if rerank and not config.jina_api_key:
        return False, "missing JINA_API_KEY"
    return True, ""


def load_queries(path: Path) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        queries.append(
            {
                "query": str(item["query"]),
                "category": str(item.get("category", "general")),
                "intent": str(item.get("intent", "general")),
            }
        )
    return queries


async def run_mode(
    mode: str,
    spec: dict[str, Any],
    queries: list[dict[str, str]],
    *,
    limit: int,
    rerank: bool,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for item in queries:
        started = time.monotonic()
        outcome = await retrieve(
            item["query"],
            spec["providers"],
            limit,
            intent=spec["intent"],
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        runs.append(
            {
                "mode": mode,
                "query": item["query"],
                "category": item["category"],
                "intent": item["intent"],
                "policy": list(outcome.policy),
                "candidates": [
                    {"url": ranked.candidate.url, "providers": list(ranked.candidate.providers)}
                    for ranked in outcome.ranked
                ],
                "attempts": [
                    {
                        "provider": attempt.provider,
                        "status": attempt.status.value,
                        "error_type": attempt.error.type if attempt.error else None,
                        "result_count": attempt.result_count,
                    }
                    for attempt in outcome.attempts
                ],
                "warnings": list(outcome.warnings),
                "elapsed_ms": elapsed_ms,
            }
        )
    return runs


def markdown_table(runs_by_mode: dict[str, list[dict[str, Any]]], skipped: dict[str, str]) -> str:
    lines = [
        "# Retrieval benchmark (live provider calls)",
        "",
        "| Mode | Queries | Candidates | OK attempts | Failed attempts | Avg elapsed (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in MODES:
        if mode in skipped:
            lines.append(f"| {mode} | _skipped: {skipped[mode]}_ | | | | |")
            continue
        runs = runs_by_mode[mode]
        candidates = sum(len(run["candidates"]) for run in runs)
        ok = sum(1 for run in runs for a in run["attempts"] if a["status"] == "ok")
        failed = sum(1 for run in runs for a in run["attempts"] if a["status"] == "error")
        avg = round(sum(run["elapsed_ms"] for run in runs) / len(runs), 1) if runs else 0.0
        lines.append(f"| {mode} | {len(runs)} | {candidates} | {ok} | {failed} | {avg} |")
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="v0.3.0 retrieval benchmark — LIVE PROVIDER CALLS, not a CI gate.",
    )
    parser.add_argument("--max-queries", type=int, default=0, help="limit the number of queries per mode (0 = all)")
    parser.add_argument(
        "--modes",
        default="",
        help="comma-separated modes to run (default: all)",
    )
    parser.add_argument("--limit", type=int, default=5, help="per-provider result count and final top-k")
    parser.add_argument("--queries", default=str(QUERIES_FILE), help="path to the queries JSONL")
    parser.add_argument("--output", default="", help="directory for detail JSONL + markdown output")
    parser.add_argument("--no-jina", action="store_true", help="disable the RRF+Jina mode")
    args = parser.parse_args(argv)

    selected_modes = [m.strip() for m in args.modes.split(",") if m.strip()] if args.modes else list(MODES)
    unknown = [m for m in selected_modes if m not in MODES]
    if unknown:
        print(f"unknown modes: {', '.join(unknown)} (valid: {', '.join(MODES)})", file=sys.stderr)
        return 2

    queries = load_queries(Path(args.queries))
    if args.max_queries:
        queries = queries[: args.max_queries]
    if not queries:
        print(f"no queries found in {args.queries}", file=sys.stderr)
        return 2

    print(
        "WARNING: this benchmark makes LIVE provider calls and consumes real API quota. "
        "It is NOT a CI gate.",
        file=sys.stderr,
    )

    runs_by_mode: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, str] = {}
    for mode in selected_modes:
        spec = MODES[mode]
        rerank = mode == "RRF+Jina" and not args.no_jina
        if mode == "RRF+Jina" and args.no_jina:
            skipped[mode] = "disabled via --no-jina"
            continue
        configured, reason = _mode_configured(spec["providers"], rerank=rerank)
        if not configured:
            skipped[mode] = reason
            print(f"[skip] {mode}: {reason}", file=sys.stderr)
            continue
        print(f"[run ] {mode}: {len(queries)} queries (providers={spec['providers']}, intent={spec['intent']})", file=sys.stderr)
        runs_by_mode[mode] = await run_mode(mode, spec, queries, limit=args.limit, rerank=rerank)

    table = markdown_table(runs_by_mode, skipped)
    print(table)

    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        detail_path = out_dir / "retrieval_benchmark.jsonl"
        with detail_path.open("w", encoding="utf-8") as handle:
            for mode in selected_modes:
                for run in runs_by_mode.get(mode, []):
                    handle.write(json.dumps(run, ensure_ascii=False) + "\n")
        (out_dir / "retrieval_benchmark.md").write_text(table + "\n", encoding="utf-8")
        print(f"detail JSONL written to {detail_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
