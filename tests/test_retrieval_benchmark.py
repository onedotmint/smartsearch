"""Offline smoke tests for the live retrieval benchmark runner."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.evaluation

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "retrieval_benchmark.py"


def test_benchmark_help_exits_zero_without_credentials():
    env = {key: value for key, value in os.environ.items() if key not in {
        "BRAVE_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "JINA_API_KEY", "FIRECRAWL_API_KEY",
    }}
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
    assert "live provider" in completed.stdout.lower()


def test_benchmark_import_does_not_construct_registry_or_read_config():
    child = r"""
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1])
mod = runpy.run_path(str(root / "benchmarks" / "retrieval_benchmark.py"), run_name="benchmark_import")
assert "MODES" in mod
loaded = [name for name in sys.modules if name == "httpx" or name.startswith("smart_search")]
assert loaded == [], loaded
"""
    completed = subprocess.run(
        [sys.executable, "-c", child, str(ROOT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
