#!/usr/bin/env python3
"""Reproducible performance baseline for Smart Search (measurement only).

Runs commands as fresh subprocesses (cold process, the real user pattern) and
reports wall-clock distributions. No production code is touched; live
benchmarks are never part of CI.

Usage:
    python3 scripts/bench_baseline.py local [--samples N]
    python3 scripts/bench_baseline.py live  [--samples N]

Local (deterministic, no network, no keys):
    --version, capabilities, doctor status

Live (requires network; uses whatever providers are configured):
    jina-fetch (zero-config anonymous Jina), search (first eligible provider),
    fallback (dead local Tavily -> anonymous Jina success)

Decomposition: each network envelope already carries meta.duration_ms (total)
and per-attempt elapsed_ms (provider time); local overhead is derived as the
difference when the envelope provides both.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = 25
JINA_FETCH_URL = "https://example.com"


def env_metadata() -> dict:
    node_version = "n/a"
    try:
        node_version = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or "n/a"
    except Exception:
        pass
    commit = "n/a"
    try:
        commit = (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            or "n/a"
        )
    except Exception:
        pass
    return {
        "os": platform.system(),
        "python": sys.version.split()[0],
        "node": node_version,
        "commit": commit,
    }


def _interpreter() -> str:
    """Prefer the repo venv python so the harness works from a bare checkout."""
    candidates = [
        REPO_ROOT / ".venv" / "bin" / "python",
        REPO_ROOT / ".smart-search-python" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _invoke(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_interpreter(), "-m", "smart_search.cli", *argv],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _percentiles(samples: list[float]) -> dict:
    s = sorted(samples)
    return {
        "n": len(s),
        "min_ms": round(s[0], 1),
        "p50_ms": round(statistics.median(s), 1),
        "p95_ms": round(s[int(0.95 * (len(s) - 1))], 1),
        "max_ms": round(s[-1], 1),
    }


def _peak_rss_kb() -> float:
    """Peak RSS of the most recent child (ru_maxrss: KB on Linux, bytes on macOS)."""
    kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return kb / 1024.0 if sys.platform == "darwin" else float(kb)


def _valid_doctor_envelope(stdout: str, returncode: int = 0) -> bool:
    del returncode
    try:
        return json.loads(stdout).get("command") == "doctor"
    except json.JSONDecodeError:
        return False


def run_local(samples: int) -> None:
    env = dict(os.environ)
    env["SMART_SEARCH_CONFIG_DIR"] = tempfile.mkdtemp(prefix="ss-bench-")
    scenarios = [
        ("--version", ["--version"], lambda out, rc: rc == 0),
        ("capabilities", ["capabilities"], lambda out, rc: rc == 0),
        # doctor status is a fail-closed readiness check: with no keys it
        # deterministically returns a failed V3 envelope (exit 3). Success here
        # means a valid deterministic envelope, not exit 0.
        ("doctor status", ["doctor", "status", "--format", "json"], _valid_doctor_envelope),
    ]
    print("Local\n-----")
    for name, argv, is_ok in scenarios:
        durations: list[float] = []
        ok = 0
        for i in range(samples):
            t0 = time.perf_counter()
            proc = _invoke(argv, env)
            durations.append((time.perf_counter() - t0) * 1000.0)
            if is_ok(proc.stdout, proc.returncode):
                ok += 1
            # first sample only: capture representative peak RSS of one child
            if i == 0:
                rss_kb = _peak_rss_kb()
        p = _percentiles(durations)
        print(f"{name}\n  p50: {p['p50_ms']} ms   p95: {p['p95_ms']} ms   "
              f"min: {p['min_ms']}   max: {p['max_ms']}   "
              f"success: {ok}/{samples}   peak_rss: {rss_kb:.0f} KB")


def _run_network_sample(argv: list[str], env: dict) -> dict:
    """Run one live command and return envelope facts (or None-marker)."""
    t0 = time.perf_counter()
    proc = _invoke(argv, env)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    result = {"wall_ms": wall_ms, "exit": proc.returncode, "raw": proc.stdout}
    try:
        payload = json.loads(proc.stdout)
        result["status"] = payload.get("status")
        result["ok"] = payload.get("ok")
        result["duration_ms"] = (payload.get("meta") or {}).get("duration_ms")
        result["attempts"] = [
            (a.get("provider"), a.get("elapsed_ms")) for a in (payload.get("attempts") or [])
        ]
        result["providers_used"] = [a for a in (payload.get("providers_used") or [])]
    except (json.JSONDecodeError, AttributeError):
        result["status"] = "unparseable"
    return result


def run_live(samples: int) -> None:
    env = dict(os.environ)
    env["SMART_SEARCH_CONFIG_DIR"] = tempfile.mkdtemp(prefix="ss-bench-live-")
    print("Live\n----")

    # 1) zero-config Jina fetch of a stable public URL
    samples_jina: list[dict] = []
    for _ in range(samples):
        samples_jina.append(_run_network_sample(["fetch", JINA_FETCH_URL, "--format", "json"], env))
    ok = sum(1 for s in samples_jina if s.get("ok") is True)
    total = [s["wall_ms"] for s in samples_jina if s.get("duration_ms") is not None]
    p = _percentiles(total) if total else {"n": 0, "min_ms": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}
    print(f"Jina fetch (anonymous, {JINA_FETCH_URL})\n  p50: {p['p50_ms']} ms   p95: {p['p95_ms']} ms   "
          f"success: {ok}/{samples}   envelope_duration_ms_p50: "
          f"{round(statistics.median([s['duration_ms'] for s in samples_jina if s.get('duration_ms') is not None]) or 0, 1) if ok else 0}")

    # 2) one representative configured search provider (first eligible)
    probes = _run_network_sample(["capabilities", "--format", "json"], env)
    eligible = []
    try:
        payload = json.loads(probes["raw"])
        for cap in ("source_discovery", "docs_discovery"):
            for c in (payload.get("result") or {}).get("capabilities", []):
                if c.get("operation") == cap and c.get("configured"):
                    eligible.extend([p.get("provider") for p in (c.get("providers") or [])])
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    eligible = list(dict.fromkeys(eligible))
    if eligible:
        provider = eligible[0]
        samples_s: list[dict] = []
        for _ in range(samples):
            samples_s.append(_run_network_sample(["search", "latest python release", "--format", "json"], env))
        ok_s = sum(1 for s in samples_s if s.get("ok") is True)
        totals = [s["wall_ms"] for s in samples_s if s.get("duration_ms") is not None]
        p = _percentiles(totals) if totals else {"n": 0, "p50_ms": 0, "p95_ms": 0, "min_ms": 0, "max_ms": 0}
        print(f"Search: {provider}\n  p50: {p['p50_ms']} ms   p95: {p['p95_ms']} ms   success: {ok_s}/{samples}")
    else:
        print("Search: skipped (no eligible discovery provider configured; add a search provider key to run)")

    # 3) controlled fallback: dead local Tavily endpoint -> anonymous Jina success
    env_fb = dict(env)
    env_fb["TAVILY_API_KEY"] = "benchmark-dead-endpoint-key"
    env_fb["TAVILY_API_URL"] = "http://127.0.0.1:1"
    samples_fb: list[dict] = []
    for _ in range(samples):
        samples_fb.append(_run_network_sample(["fetch", JINA_FETCH_URL, "--format", "json"], env_fb))
    ok_fb = sum(1 for s in samples_fb if s.get("ok") is True)
    totals = [s["wall_ms"] for s in samples_fb if s.get("duration_ms") is not None]
    p = _percentiles(totals) if totals else {"n": 0, "p50_ms": 0, "p95_ms": 0, "min_ms": 0, "max_ms": 0}
    fb_attempts = samples_fb[0].get("attempts", []) if samples_fb else []
    print(f"Fallback: tavily(dead) -> jina\n  p50: {p['p50_ms']} ms   p95: {p['p95_ms']} ms   "
          f"success: {ok_fb}/{samples}   first-sample attempts: {fb_attempts}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart Search performance baseline (measurement only)")
    ap.add_argument("scope", choices=["local", "live"])
    ap.add_argument("--samples", type=int, default=SAMPLES)
    args = ap.parse_args()
    meta = env_metadata()
    print(f"Environment: OS={meta['os']} Python={meta['python']} Node={meta['node']} "
          f"commit={meta['commit']} samples={args.samples}")
    if args.scope == "local":
        run_local(args.samples)
    else:
        run_live(args.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
