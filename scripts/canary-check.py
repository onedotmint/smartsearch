#!/usr/bin/env python3
"""Advisory canary result checker.

Reads the JSON envelopes written by the provider-canary workflow steps and
fails (exit 1) when any result reports a failure state. Keeps the workflow
YAML simple: each probe step captures its own JSON, and this script aggregates
them. A missing/unparseable result file also fails so the canary never hides
upstream breakage.

Usage: python3 scripts/canary-check.py canary-results/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _failed(payload: object) -> bool:
    if not isinstance(payload, dict):
        return True
    status = payload.get("status")
    if status in ("failed", "degraded", "error"):
        return True
    if status is None:
        ok = payload.get("ok")
        if ok is False:
            return True
        # A result with no status and no explicit ok is unverifiable.
        if "result" not in payload and "evidence" not in payload:
            return True
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: canary-check.py <results-dir>", file=sys.stderr)
        return 2
    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"missing results dir: {results_dir}", file=sys.stderr)
        return 1
    failed_any = False
    for path in sorted(results_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # unparseable or fallback echo payload
            print(f"failed {path.name}: unparseable ({exc})")
            failed_any = True
            continue
        if _failed(payload):
            print(f"failed {path.name}: status={payload.get('status')!r} ok={payload.get('ok')!r}")
            failed_any = True
        else:
            print(f"ok {path.name}")
    return 1 if failed_any else 0


if __name__ == "__main__":
    sys.exit(main())
