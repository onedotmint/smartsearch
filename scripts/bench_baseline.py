#!/usr/bin/env python3
"""Reproducible performance baseline for Smart Search (measurement only).

Runs commands as fresh subprocesses (cold process, the real user pattern) and
reports wall-clock distributions. No production code is touched; live
benchmarks are never part of CI.

Usage:
    python3 scripts/bench_baseline.py local [--samples N]
    python3 scripts/bench_baseline.py live  [--samples N]

Local (deterministic, no network, no keys):
    `--help`, `setup`, invalid `read`, invalid `research`

Live (requires network; uses whatever providers are configured):
    read (Jina or another configured reader), search, research

The v1 envelope exposes operation status, safe attempts, warnings, and error.
Timing is measured around each fresh subprocess; per-attempt elapsed_ms is
reported when the provider returns it.
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
JINA_READ_URL = "https://example.com"


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


def _valid_v1_envelope(stdout: str, operation: str) -> bool:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    return (
        set(payload) == {"version", "operation", "status", "data", "attempts", "warnings", "error"}
        and payload["version"] == 1
        and payload["operation"] == operation
        and payload["status"] in {"complete", "degraded", "failed"}
    )


def run_local(samples: int) -> None:
    env = dict(os.environ)
    env["SMART_SEARCH_CONFIG_DIR"] = tempfile.mkdtemp(prefix="ss-bench-")
    scenarios = [
        ("help", ["--help"], lambda out, rc: rc == 0 and "{search,setup,read,research}" in out),
        ("setup", ["setup", "--format", "json"], lambda out, rc: _valid_v1_envelope(out, "setup")),
        ("read validation", ["read", "not-a-url", "--format", "json"], lambda out, rc: _valid_v1_envelope(out, "read")),
        ("research validation", ["research", "--format", "json"], lambda out, rc: _valid_v1_envelope(out, "research")),
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
    """Run one live command and return stable envelope facts."""
    t0 = time.perf_counter()
    proc = _invoke(argv, env)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    result = {"wall_ms": wall_ms, "exit": proc.returncode, "raw": proc.stdout}
    try:
        payload = json.loads(proc.stdout)
        result["status"] = payload.get("status")
        result["ok"] = payload.get("status") in {"complete", "degraded"} and payload.get("error") is None
        result["attempts"] = [
            (a.get("provider"), a.get("elapsed_ms")) for a in (payload.get("attempts") or [])
        ]
    except (json.JSONDecodeError, AttributeError):
        result["status"] = "unparseable"
    return result


def _report_live(name: str, samples: list[dict], samples_count: int) -> None:
    ok = sum(1 for sample in samples if sample.get("ok") is True)
    durations = [sample["wall_ms"] for sample in samples]
    p = _percentiles(durations)
    attempts = samples[0].get("attempts", []) if samples else []
    print(f"{name}\n  p50: {p['p50_ms']} ms   p95: {p['p95_ms']} ms   "
          f"success: {ok}/{samples_count}   first-sample attempts: {attempts}")


def run_live(samples: int) -> None:
    env = dict(os.environ)
    env["SMART_SEARCH_CONFIG_DIR"] = tempfile.mkdtemp(prefix="ss-bench-live-")
    print("Live\n----")

    cases = [
        ("Read", ["read", JINA_READ_URL, "--format", "json"]),
        ("Search", ["search", "latest python release", "--format", "json"]),
        ("Research", ["research", "latest Python release", "--format", "json"]),
    ]
    for name, argv in cases:
        case_samples = [_run_network_sample(argv, env) for _ in range(samples)]
        _report_live(name, case_samples, samples)


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
