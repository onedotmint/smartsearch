from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_search.cli import main
from smart_search.cli_constants import prescan_schema_version
from smart_search.v2_contract import V2_TOP_LEVEL_FIELDS

ROOT = Path(__file__).parents[1]


def _run_main(argv: list[str], monkeypatch=None, env_updates: dict | None = None):
    import io
    import contextlib

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_prescan_root_global_only():
    assert prescan_schema_version(["--schema-version", "2", "search", "q"])["v2"] is True
    assert prescan_schema_version(["search", "q", "--schema-version", "2"])["v2"] is False
    assert prescan_schema_version(["--schema-version=2", "fetch", "https://x"])["operation"] == "content_fetch"
    assert prescan_schema_version(["--schema-version", "2", "capabilities"])["operation"] == "capability_status"


def test_v2_parser_error_is_single_json_document():
    code, out, err = _run_main(["--schema-version", "2", "search"])
    assert code == 2
    payload = json.loads(out)
    assert tuple(payload) == V2_TOP_LEVEL_FIELDS
    assert payload["schema_version"] == "2"
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["operation"] == "source_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    # single JSON document
    assert out.strip().startswith("{")
    assert out.count('"schema_version"') == 1


def test_v2_response_mode_rejected_before_network(monkeypatch):
    code, out, err = _run_main([
        "--schema-version", "2", "search", "q", "--response-mode", "synthesized",
    ])
    assert code == 2
    payload = json.loads(out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "response_mode" in payload["error"]["message"]


def test_v2_markdown_format_rejected():
    code, out, err = _run_main([
        "--schema-version", "2", "search", "q", "--format", "markdown",
    ])
    assert code == 2
    payload = json.loads(out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_v2_capabilities_complete_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    code, out, err = _run_main(["--schema-version", "2", "capabilities"])
    assert code == 0
    payload = json.loads(out)
    assert payload["operation"] == "capability_status"
    assert payload["status"] == "complete"
    assert payload["attempts"] == []
    assert payload["routing"]["requested_capabilities"] == []
    assert payload["evidence"]["candidates"] == []


def test_v2_parser_import_isolation_fresh_process():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["SMART_SEARCH_MINIMUM_PROFILE"] = "off"
    script = r"""
import sys
from smart_search.cli import main
code = main(["--schema-version", "2", "search"])
assert code == 2
for name in (
    "smart_search.service",
    "smart_search.config",
    "httpx",
    "smart_search.providers.openai_compatible",
    "smart_search.providers.xai_responses",
):
    assert name not in sys.modules, name
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_v1_parser_error_still_uses_stderr():
    import io
    import contextlib

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        with pytest.raises(SystemExit) as exc:
            main(["search"])
    assert exc.value.code == 2
    out = stdout.getvalue()
    err = stderr.getvalue()
    assert "schema_version" not in out or out == ""
    assert "search" in err


def test_cli_and_facade_parity(monkeypatch):
    import asyncio

    from smart_search import api_v2
    from smart_search.v2_contract import (
        V2Attempt,
        V2Candidate,
        V2Envelope,
        V2Evidence,
        V2Meta,
        V2Routing,
        V2Status,
        validate_result,
    )

    async def fake_source(request):
        cand = V2Candidate("c1", "https://example.com", "tavily", "T", "s")
        return validate_result(
            V2Envelope(
                V2Status.COMPLETE,
                "search",
                "source_discovery",
                {"total": 1, "items": [{"id": "c1"}]},
                V2Evidence(candidates=(cand,)),
                V2Routing(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
                (V2Attempt("source_discovery", "tavily", "ok", None, 1, 1),),
                (),
                None,
                V2Meta("parity", 1),
            )
        )

    async def fake_composite(query, max_results=5):
        return await api_v2.source_discovery(
            api_v2.SourceDiscoveryRequest(query=query, max_results=max_results)
        )

    monkeypatch.setattr(api_v2, "source_discovery", fake_source)
    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)

    facade = asyncio.run(api_v2.source_discovery(api_v2.SourceDiscoveryRequest("q")))
    code, out, err = _run_main(["--schema-version", "2", "search", "q"])
    if code != 0:
        raise AssertionError(f"code={code} out={out!r} err={err!r}")
    payload = json.loads(out)
    assert payload["operation"] == facade.operation
    assert payload["status"] == "complete"
    assert payload["result"]["total"] == 1


def test_v2_map_v1_only_options_and_invalid_request_are_json_failures(monkeypatch):
    code, out, err = _run_main([
        "--schema-version", "2", "map", "https://example.com", "--max-depth", "2",
    ])
    assert code == 2
    payload = json.loads(out)
    assert payload["operation"] == "site_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "max-depth" in payload["error"]["message"]

    code, out, err = _run_main(["--schema-version", "2", "search", "   "])
    assert code == 2
    payload = json.loads(out)
    assert payload["operation"] == "source_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_v2_explicit_default_v1_options_are_rejected_by_presence():
    cases = (
        (["--schema-version", "2", "map", "https://example.com", "--max-depth", "1"], "max-depth"),
        (["--schema-version", "2", "search", "q", "--timeout", "90"], "timeout"),
        (["--schema-version", "2", "search", "q", "--providers", "auto"], "providers"),
    )
    for argv, option in cases:
        code, out, err = _run_main(argv)
        assert code == 2, (argv, out, err)
        payload = json.loads(out)
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert option in payload["error"]["message"]


def test_v2_option_detection_stops_at_argv_delimiter(monkeypatch):
    from smart_search import api_v2
    from smart_search.cli_v2 import _argv_has_response_mode
    from smart_search.v2_contract import (
        V2Envelope,
        V2Evidence,
        V2Meta,
        V2Routing,
        V2Status,
        validate_result,
    )

    assert _argv_has_response_mode(["--", "--response-mode"]) is False

    async def fake_composite(query, max_results=5):
        assert query == "--response-mode"
        return validate_result(
            V2Envelope(
                V2Status.COMPLETE,
                "search",
                "source_discovery",
                {"total": 0, "items": []},
                V2Evidence(),
                V2Routing(("source_discovery",), (), "v2", ("source_discovery",)),
                (),
                (),
                None,
                V2Meta("delimiter", 0),
            )
        )

    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)
    code, out, err = _run_main([
        "--schema-version", "2", "search", "--", "--response-mode",
    ])
    assert code == 0, err
    assert json.loads(out)["status"] == "complete"


def test_v2_internal_handler_failure_has_a_fixed_non_leaking_shape(monkeypatch):
    from smart_search import api_v2

    async def crash(query, max_results=5):
        raise RuntimeError("Bearer private-token")

    monkeypatch.setattr(api_v2, "_composite_search", crash)
    code, out, err = _run_main(["--schema-version", "2", "search", "q"])
    assert code == 5
    payload = json.loads(out)
    assert payload["operation"] == "source_discovery"
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "private-token" not in out
