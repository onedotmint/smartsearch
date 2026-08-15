from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_search.cli import main
from smart_search.cli_constants import classify_command_domain
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


def test_classifier_routes_evidence_commands_to_v2():
    assert classify_command_domain(["search", "q"])["family"] == "v2"
    assert classify_command_domain(["fetch", "https://x"])["family"] == "v2"
    assert classify_command_domain(["map", "https://x"])["family"] == "v2"
    assert classify_command_domain(["capabilities"])["family"] == "v2"
    # selector spellings are removed and never select a family
    removed = classify_command_domain(["--schema-version", "2", "search", "q"])
    assert removed["family"] == "removed"
    assert removed["error_family"] == "v2"
    assert removed["legacy_spelling"] == "--schema-version 2"


def test_v2_parser_error_is_single_json_document():
    code, out, err = _run_main(["search"])
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
        "search", "q", "--response-mode", "synthesized",
    ])
    assert code == 2
    payload = json.loads(out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "response_mode" in payload["error"]["message"]


def test_v2_fetch_rejects_non_http_resource_envelope():
    """Non-http(s) fetch resources fail with the V2 INVALID_ARGUMENT envelope."""
    code, out, err = _run_main(["fetch", "file:///etc/passwd"])
    assert code == 2
    payload = json.loads(out)
    assert payload["schema_version"] == "2"
    assert payload["ok"] is False
    assert payload["command"] == "fetch"
    assert payload["operation"] == "content_fetch"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "http(s)" in payload["error"]["message"]
    assert payload["attempts"] == []
    assert payload["routing"]["reason_codes"] == ["invalid_argument"]
    assert payload["result"] == {"total": 0, "items": []}


def test_v2_map_rejects_non_http_resource_envelope():
    code, out, err = _run_main(["map", "file:///etc/passwd"])
    assert code == 2
    payload = json.loads(out)
    assert payload["operation"] == "site_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["attempts"] == []


def test_v2_fetch_valid_https_resource_reaches_owner(monkeypatch):
    """An https resource passes validation and reaches the owner path."""
    import smart_search.evidence_operations as evidence_operations

    calls = {"fetch": 0}

    async def fake_fetch(url, fallback="auto", preferred_order=None, providers=None):
        calls["fetch"] += 1
        from smart_search.execution_primitives import ExecutionAttempt, ExecutionAttemptStatus, ExecutionOutcome

        return ExecutionOutcome(
            value={"ok": True, "url": url, "provider": "tavily", "content": "body"},
            attempts=(
                ExecutionAttempt(
                    capability="web_fetch",
                    provider="tavily",
                    status=ExecutionAttemptStatus.OK,
                    elapsed_ms=1.0,
                    result_count=1,
                ),
            ),
        )

    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])
    monkeypatch.setattr(evidence_operations, "_execute_web_fetch", fake_fetch)
    code, out, err = _run_main(["fetch", "https://example.com/page"])
    assert calls["fetch"] == 1
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["operation"] == "content_fetch"
    assert len(payload["evidence"]["items"]) == 1


def test_v2_presentation_formats_are_one_stdout_document(monkeypatch):
    """Markdown/content select one human stdout document after validation."""
    from smart_search import api_v2
    from smart_search.v2_contract import (
        V2Candidate,
        V2Envelope,
        V2Evidence,
        V2Meta,
        V2Routing,
        V2Status,
        validate_result,
    )

    async def fake_composite(query, max_results=5):
        return validate_result(
            V2Envelope(
                V2Status.COMPLETE,
                "search",
                "source_discovery",
                {"total": 1, "items": [{"id": "c1"}]},
                V2Evidence(candidates=(V2Candidate("c1", "https://example.com", "tavily", "T", "s"),)),
                V2Routing(("source_discovery",), ("source_discovery",), "v2", ("source_discovery",)),
                (),
                (),
                None,
                V2Meta("fmt-test", 1),
            )
        )

    monkeypatch.setattr(api_v2, "_composite_search", fake_composite)
    code, out, err = _run_main(["search", "q", "--format", "markdown"])
    assert code == 0, err
    assert out.count("# V2 Search") == 1
    assert "Status: COMPLETE" in out
    assert out.count('"schema_version"') == 0

    code, out, err = _run_main(["search", "q", "--format", "content"])
    assert code == 0, err
    assert out.strip() == "s"

    code, out, err = _run_main(["search", "q", "--format", "json"])
    assert code == 0, err
    assert json.loads(out)["status"] == "complete"


def test_v2_output_and_force_remain_rejected(monkeypatch):
    """The typed family never projects an output path; JSON contract rules."""
    code, out, err = _run_main([
        "search", "q", "--output", "out.md",
    ])
    assert code == 2
    payload = json.loads(out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "output" in payload["error"]["message"]
    code, out, err = _run_main(["search", "q", "--force"])
    assert code == 2
    payload = json.loads(out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "force" in payload["error"]["message"]


def test_v2_capabilities_complete_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    code, out, err = _run_main(["capabilities"])
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
code = main(["search"])
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


def test_v2_parser_error_is_stdout_json_not_stderr():
    import io
    import contextlib

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["search"])
    assert code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "2"
    assert payload["operation"] == "source_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert stderr.getvalue() == ""


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
    code, out, err = _run_main(["search", "q"])
    if code != 0:
        raise AssertionError(f"code={code} out={out!r} err={err!r}")
    payload = json.loads(out)
    assert payload["operation"] == facade.operation
    assert payload["status"] == "complete"
    assert payload["result"]["total"] == 1


def test_v2_map_v1_only_options_and_invalid_request_are_json_failures(monkeypatch):
    code, out, err = _run_main([
        "map", "https://example.com", "--timeout", "2",
    ])
    assert code == 2
    payload = json.loads(out)
    assert payload["operation"] == "site_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "timeout" in payload["error"]["message"]

    code, out, err = _run_main(["search", "   "])
    assert code == 2
    payload = json.loads(out)
    assert payload["operation"] == "source_discovery"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_v2_explicit_default_v1_options_are_rejected_by_presence():
    cases = (
        (["search", "q", "--timeout", "90"], "timeout"),
        (["search", "q", "--providers", "auto"], "providers"),
    )
    for argv, option in cases:
        code, out, err = _run_main(argv)
        assert code == 2, (argv, out, err)
        payload = json.loads(out)
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
        assert option in payload["error"]["message"]


def test_v2_map_restored_parameters_reach_owner_and_envelope(monkeypatch):
    from smart_search import evidence_operations
    from smart_search.execution_primitives import ExecutionOutcome, success_attempt

    monkeypatch.setattr(evidence_operations, "_qualified_providers", lambda operation: ["tavily"])
    captured: dict = {}

    async def fake_map(url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150):
        captured.update(
            url=url,
            instructions=instructions,
            max_depth=max_depth,
            max_breadth=max_breadth,
            limit=limit,
            timeout=timeout,
        )
        return ExecutionOutcome(
            value={
                "ok": True,
                "results": [
                    "https://example.com/api",
                    {"url": "https://example.com/guide", "title": "Guide"},
                ],
            },
            attempts=(success_attempt("site_map", "tavily", elapsed_ms=1.0, result_count=2),),
        )

    monkeypatch.setattr(evidence_operations, "_execute_site_map", fake_map)

    code, out, err = _run_main([
        "map", "https://example.com", "--instructions", "deep", "--max-depth", "3",
        "--format", "json",
    ])
    assert code == 0, (out, err)
    payload = json.loads(out)
    assert payload["operation"] == "site_discovery"
    assert payload["status"] == "complete"
    assert payload["result"]["total"] == 2
    assert captured["instructions"] == "deep"
    assert captured["max_depth"] == 3

    # defaults are forwarded when the tuning options are omitted
    code, out, err = _run_main(["map", "https://example.com"])
    assert code == 0, (out, err)
    assert captured["instructions"] == ""
    assert captured["max_depth"] == 1
    assert captured["max_breadth"] == 20
    assert captured["limit"] == 50


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
        "search", "--", "--response-mode",
    ])
    assert code == 0, err
    assert json.loads(out)["status"] == "complete"


def test_v2_internal_handler_failure_has_a_fixed_non_leaking_shape(monkeypatch):
    from smart_search import api_v2

    async def crash(query, max_results=5):
        raise RuntimeError("Bearer private-token")

    monkeypatch.setattr(api_v2, "_composite_search", crash)
    code, out, err = _run_main(["search", "q"])
    assert code == 5
    payload = json.loads(out)
    assert payload["operation"] == "source_discovery"
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "private-token" not in out


def _v2_leaf_help(command: str) -> str:
    import argparse

    from smart_search import cli_parser

    parser = cli_parser.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return subparsers.choices[command].format_help()


# Rejected legacy options stay registered so the deterministic v2 rejection
# envelopes keep working, but canonical V2 help must not advertise them.
_V2_HELP_HIDDEN_OPTIONS = (
    "--platform",
    "--model",
    "--extra-sources",
    "--profile",
    "--response-mode",
    "--validation",
    "--fallback",
    "--providers",
    "--stream",
    "--no-stream",
    "--timeout",
    "--output",
    "--force",
    "--prompt-dir",
    "--search-prompt-file",
    "--fetch-prompt-file",
    "--research-prompt-file",
)


def test_v2_help_hides_rejected_legacy_options():
    for command in ("search", "fetch", "map", "capabilities"):
        help_text = _v2_leaf_help(command)
        for option in _V2_HELP_HIDDEN_OPTIONS:
            assert option not in help_text, (
                f"{command} help must not advertise rejected option {option}"
            )


def test_v2_help_keeps_active_v2_options():
    search_help = _v2_leaf_help("search")
    assert "--format" in search_help
    assert "query" in search_help
    # map parameter options are restored in a later task and stay visible.
    map_help = _v2_leaf_help("map")
    for option in ("--instructions", "--max-depth", "--max-breadth", "--limit"):
        assert option in map_help, f"map help must keep {option}"
    assert "--timeout" not in map_help


def test_v2_fetch_full_flag_reaches_owner_and_default_stays_bounded(monkeypatch):
    from smart_search import evidence_operations
    from smart_search.evidence_operations import (
        ContentFetchRequest,
        DEFAULT_FETCH_CONTENT_LIMIT,
        EvidenceOperationOutcome,
        EvidenceOperationStatus,
        EvidenceRouting,
    )
    from smart_search.execution_primitives import ExecutionEvidenceItem, ExecutionMetadata

    captured: list[ContentFetchRequest] = []

    async def fake_content_fetch(request):
        captured.append(request)
        body = "z" * (DEFAULT_FETCH_CONTENT_LIMIT + 100)
        item = ExecutionEvidenceItem(
            id="ev-full",
            resource=request.resource,
            provider="jina",
            title="Page",
            content=body,
            original_length=len(body),
            returned_length=len(body),
        )
        return EvidenceOperationOutcome(
            operation="content_fetch",
            status=EvidenceOperationStatus.COMPLETE,
            evidence_items=(item,),
            routing=EvidenceRouting(
                ("content_fetch",), ("content_fetch",), "v2", ("content_fetch",)
            ),
            metadata=ExecutionMetadata("req", 1),
        )

    monkeypatch.setattr(evidence_operations, "content_fetch", fake_content_fetch)

    code, out, err = _run_main(["fetch", "https://example.com", "--full"])
    assert code == 0, err
    assert captured and captured[0].full is True
    assert captured[0].effective_content_limit is None
    payload = json.loads(out)
    assert payload["operation"] == "content_fetch"
    assert payload["evidence"]["items"][0]["truncated"] is False
    assert payload["evidence"]["items"][0]["original_length"] == DEFAULT_FETCH_CONTENT_LIMIT + 100

    code, out, err = _run_main(["fetch", "https://example.com"])
    assert code == 0, err
    assert captured[1].full is False
    assert captured[1].effective_content_limit == DEFAULT_FETCH_CONTENT_LIMIT

    # The canonical envelope emits all three content-budget fields.
    payload = json.loads(out)
    item = payload["evidence"]["items"][0]
    for field in ("truncated", "original_length", "returned_length"):
        assert field in item


def test_v2_fetch_full_is_not_a_rejected_legacy_option():
    from smart_search.cli_v2 import _reject_v1_only
    from smart_search import cli_parser

    args = cli_parser.build_parser().parse_args(["fetch", "https://example.com", "--full"])
    assert _reject_v1_only(args, argv=["fetch", "https://example.com", "--full"]) is None
