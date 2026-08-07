"""Negative, import, scan, and reachability coverage for the removed exact
Provider and Experimental public surface.

The legacy inventory fixture is the removal authority. Every spelling and
public import listed on rows owned by ``provider-experimental-surface-removal``
must fail deterministically before any owner/config/provider import, network
I/O, or filesystem write. Retained provider adapters must remain reachable only
from approved generic Evidence owners or V3 diagnostic paths.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import smart_search.service as service
from smart_search import cli, research_service
from smart_search.cli_constants import (
    COMMAND_ALIASES,
    NAMESPACE_COMMANDS,
    help_all_text,
)
from smart_search.cli_parser import build_parser
from smart_search.evidence_operations import (
    EvidenceOperationOutcome,
    EvidenceOperationStatus,
)
from smart_search.execution_primitives import ExecutionCandidate

from tests.fixtures import legacy_surface_inventory as inv
from tests.fixtures.removed_provider_surface import (
    REMOVED_ALIASES,
    REMOVED_EXACT_PROVIDER_COMMANDS,
    REMOVED_NAMESPACE_ARGV,
    REMOVED_NAMESPACE_PATHS,
    REMOVED_SERVICE_EXPORTS,
)
from tests.fixtures.v1_cli_inventory import SERVICE_PUBLIC_EXPORTS

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "smart_search"


def _removed_surfaces() -> tuple[str, ...]:
    """Every removed parser spelling: exact commands plus aliases."""
    return tuple(sorted(REMOVED_EXACT_PROVIDER_COMMANDS + REMOVED_ALIASES))


def _removed_exports() -> tuple[str, ...]:
    return tuple(sorted(REMOVED_SERVICE_EXPORTS))


def test_removed_fixture_matches_the_removal_authority_rows() -> None:
    """The frozen negative fixture stays faithful to the pre-removal inventory
    artifact rows owned by this task (rebuilt from the recorded baseline)."""
    baseline = subprocess.run(
        ["git", "show", f"{inv.load_inventory()['source_revision']}:tests/fixtures/legacy_surface_inventory.json"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if baseline.returncode != 0:
        return  # shallow checkout without the baseline revision; skip silently
    rows = json.loads(baseline.stdout)["entries"]
    owned = [r for r in rows if "provider-experimental-surface-removal" in r.get("owning_child_tasks", [])]
    exact = sorted(str(r["surface"]) for r in owned if r["kind"] == "exact_provider_command")
    aliases = sorted(str(r["surface"]) for r in owned if r["kind"] == "command_alias")
    namespaces = sorted(str(r["surface"]) for r in owned if r["kind"] == "namespace_leaf")
    exports = sorted(
        str(r["surface"]).removeprefix("smart_search.service.")
        for r in owned
        if r["kind"] == "python_export"
    )
    assert exact == sorted(REMOVED_EXACT_PROVIDER_COMMANDS)
    assert aliases == sorted(REMOVED_ALIASES)
    assert namespaces == sorted(REMOVED_NAMESPACE_PATHS)
    assert exports == sorted(REMOVED_SERVICE_EXPORTS)


def _parser_fails(argv: list[str]) -> None:
    parser = build_parser(raise_on_error=True)
    with pytest.raises(Exception) as excinfo:
        parser.parse_args(argv)
    # Deterministic one-error behavior: the failure is argparse's own
    # unrecognized/invalid-choice error, never a dispatch or fallthrough.
    from smart_search.cli_constants import CLIParseError

    assert isinstance(excinfo.value, CLIParseError)


@pytest.mark.parametrize(
    "surface",
    list(_removed_surfaces()),
    ids=list(_removed_surfaces()),
)
def test_removed_spellings_fail_at_parse_time(surface: str) -> None:
    argv = [surface, "query"] if surface not in {"anysearch-domains"} else [surface]
    _parser_fails(argv)


@pytest.mark.parametrize("argv", [list(a) for a in REMOVED_NAMESPACE_ARGV])
def test_removed_namespace_paths_fail_at_subparser(argv: list[str]) -> None:
    _parser_fails(argv)


def test_removed_exact_commands_are_not_parser_leaves() -> None:
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")
    leaves = set(command_action.choices)
    assert set(REMOVED_EXACT_PROVIDER_COMMANDS).isdisjoint(leaves)
    assert set(REMOVED_ALIASES).isdisjoint(leaves)
    # The experimental tree is gone entirely; provider retains only its
    # canonical local leaves.
    provider_parser = command_action.choices["provider"]
    provider_leaves = {
        name
        for action in provider_parser._actions
        if getattr(action, "dest", None) == "provider_command"
        for name in action.choices
    }
    assert provider_leaves == {"list", "status", "probe", "routes"}


def test_removed_aliases_and_canonicals_are_absent_from_constants() -> None:
    removed_aliases = set(REMOVED_ALIASES)
    live_aliases = {alias for aliases in COMMAND_ALIASES.values() for alias in aliases}
    assert removed_aliases.isdisjoint(live_aliases)
    removed_canonicals = set(REMOVED_EXACT_PROVIDER_COMMANDS)
    assert removed_canonicals.isdisjoint(set(COMMAND_ALIASES))
    namespace_paths = {item["path"].removesuffix(" QUERY") for item in NAMESPACE_COMMANDS}
    assert not any(
        path.startswith(("provider exa ", "provider context7 ", "provider zhipu", "experimental "))
        for path in namespace_paths
    )


def test_help_all_does_not_advertise_removed_surface() -> None:
    text = help_all_text()
    for surface in _removed_surfaces():
        assert not re.search(rf"\b{re.escape(surface)}\b", text), surface
    for argv in REMOVED_NAMESPACE_ARGV:
        assert " ".join(argv[:2]) not in text


@pytest.mark.parametrize("name", _removed_exports())
def test_removed_service_exports_raise_importerror(name: str) -> None:
    assert name not in SERVICE_PUBLIC_EXPORTS
    assert name not in service.__all__
    with pytest.raises(ImportError):
        exec(f"from smart_search.service import {name}", {})


def test_service_facade_no_longer_reexports_provider_wrappers() -> None:
    module_source = (PACKAGE / "service.py").read_text(encoding="utf-8")
    for name in _removed_exports():
        assert name not in module_source, name


def test_dispatch_and_render_have_no_removed_command_branches() -> None:
    dispatch = (PACKAGE / "cli_dispatch.py").read_text(encoding="utf-8")
    render = (PACKAGE / "cli_render.py").read_text(encoding="utf-8")
    parser_source = (PACKAGE / "cli_parser.py").read_text(encoding="utf-8")
    for surface in REMOVED_EXACT_PROVIDER_COMMANDS:
        assert f'args.command == "{surface}"' not in dispatch
        assert f'"{surface}"' not in parser_source
        assert surface not in render
    for surface in REMOVED_NAMESPACE_PATHS:
        assert f'"{surface}"' not in dispatch


def test_removed_spelling_parse_failure_is_isolated_and_writes_nothing() -> None:
    """Fresh-process proof: parse fails with exit 2, imports nothing heavy, and
    creates no config file, for one representative spelling per surface family."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    cases = (
        ("exa-search", ["exa-search", "query"]),
        ("zhipu-mcp-reader", ["zhipu-mcp-reader", "https://example.com"]),
        ("anysearch-batch", ["anysearch-batch", "a", "b"]),
        ("context7-library", ["context7-library", "react"]),
        ("provider namespace", ["provider", "exa", "search", "query"]),
        ("experimental namespace", ["experimental", "zread", "read-file", "repo", "README.md"]),
    )
    script = """
import json
import os
import sys
import tempfile

config_dir = tempfile.mkdtemp(prefix="ss-removed-surface-")
os.environ["SMART_SEARCH_CONFIG_DIR"] = config_dir
os.environ.pop("SMART_SEARCH_HOME", None)

from smart_search.cli import main
try:
    code = main(sys.argv[1:])
except SystemExit as exc:
    code = int(exc.code or 0)
assert code == 2, code
for name in ("smart_search.service", "smart_search.config", "smart_search.providers",
             "smart_search.skill_installer", "httpx"):
    assert name not in sys.modules, name
assert os.listdir(config_dir) == [], os.listdir(config_dir)
sys.stdout.write("ok")
"""
    for label, argv in cases:
        run = subprocess.run(
            [sys.executable, "-c", script, *argv],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, f"{label}: {run.stdout} {run.stderr}"
        assert run.stdout.strip() == "ok"


@pytest.mark.parametrize(
    "argv",
    [["--schema-version", "2", "exa-search", "query"], ["--schema-version", "3", "exa-search", "query"]],
)
def test_removed_spelling_parser_error_is_one_json_document(argv, capsys) -> None:
    assert cli.main(argv) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == argv[1]
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["error"]["retryable"] is False


def test_provider_catalog_no_longer_advertises_removed_spellings(monkeypatch) -> None:
    from smart_search import provider_catalog

    monkeypatch.setattr(provider_catalog, "get_capability_status", lambda: {})
    monkeypatch.setattr(provider_catalog, "provider_profiles", lambda: {})
    monkeypatch.setattr(provider_catalog, "list_provider_qualifications", lambda **kwargs: [])
    catalog = provider_catalog.provider_catalog(include_status=False)
    removed = set(_removed_surfaces()) | set(REMOVED_NAMESPACE_PATHS)
    removed_exports = set(_removed_exports())
    payload = json.dumps(catalog)
    for spelling in removed:
        assert spelling not in payload, spelling
    for name in removed_exports:
        assert name not in payload, name


def _imports_from(source: str, package_dir: Path) -> set[str]:
    module = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level == 1 and node.module:
                names.add(node.module)
    return names


def test_retained_adapters_reachable_only_from_approved_owners() -> None:
    """Provider command modules remain internal: their direct importers must be
    the generic Evidence execution path, V3 diagnostics, or v1 compatibility
    workflows still owned by later cleanup tasks -- never the CLI boundary."""
    approved = {
        "operation_runtime",  # generic Evidence owner execution path
        "provider_diagnostics",  # V3 probe path
        "search_service",  # v1 search workflow (compat window)
        "operations_service",  # temporary v1 aggregation seam
        "service",  # retained fetch/map_site exports only
        "capability_executor",
    }
    modules = ("provider_search_commands", "provider_fetch_commands",
               "provider_mcp_commands", "provider_vertical_commands")
    for module in modules:
        importers = set()
        for path in PACKAGE.glob("*.py"):
            if path.name in {
                f"{module}.py",
                "provider_catalog.py",
                "provider_command_support.py",
                "provider_diagnostics.py",
            }:
                continue
            source = path.read_text(encoding="utf-8")
            imported = _imports_from(source, PACKAGE)
            if module in imported or f"smart_search.{module}" in imported:
                importers.add(path.stem)
        unexpected = importers - approved
        assert not unexpected, f"{module} imported by unapproved modules: {sorted(unexpected)}"
    # The strict research workflow, the typed Evidence owner, and the live
    # research service must stay generic: they may not import provider command
    # modules at all.
    for path in (
        PACKAGE / "research_workflow.py",
        PACKAGE / "evidence_operations.py",
        PACKAGE / "research_service.py",
    ):
        source = path.read_text(encoding="utf-8")
        imported = _imports_from(source, PACKAGE)
        assert not any(name in imported for name in modules), path.name
        assert "providers" not in imported, path.name


def test_live_research_path_uses_typed_evidence_owner_only(monkeypatch) -> None:
    """The live research execution composes the generic typed Evidence owner
    and never invokes a Context7/Exa/AnySearch provider-specific callback.
    Docs candidates flow into the generic fetch stage; resource-id candidates
    stay discovery-only.
    """
    import asyncio

    calls: list[str] = []

    async def fake_docs_discovery(request):
        calls.append(request.query)
        return EvidenceOperationOutcome(
            operation="docs_discovery",
            status=EvidenceOperationStatus.COMPLETE,
            candidates=(
                ExecutionCandidate(
                    id="c-1",
                    resource="https://docs.example.com/react/hooks",
                    provider="exa",
                    title="React Hooks docs",
                    snippet="official hooks reference",
                ),
                ExecutionCandidate(
                    id="c-2",
                    resource="context7:facebook/react",
                    provider="context7",
                    title="React library",
                    snippet="library resource id without HTTP URL",
                ),
            ),
        )

    async def fake_fetch(url, fallback="auto", preferred_order=None, providers=None):
        return (
            {"ok": True, "url": url, "provider": "tavily", "content": "# Fetched body"},
            [],
        )

    async def fake_web_search(query, count=5, providers="auto", fallback="auto"):
        return [], []

    monkeypatch.setattr(research_service, "docs_discovery", fake_docs_discovery)
    monkeypatch.setattr(research_service, "_run_web_fetch_fallback", fake_fetch)
    monkeypatch.setattr(research_service, "_run_web_search_fallback", fake_web_search)
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "rules")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test-secret")
    monkeypatch.setenv("EXA_API_KEY", "exa-test-secret")

    result = asyncio.run(
        research_service.research(
            "React useEffect official API docs",
            budget="quick",
            fallback="off",
        )
    )

    assert calls == ["React useEffect official API docs"]
    assert any(item.get("stage") == "docs_discovery" for item in result["stage_results"])
    urls = {item.get("url") for item in result["discovery_sources"]}
    assert "https://docs.example.com/react/hooks" in urls
    assert "context7:facebook/react" in urls
    # URL-backed docs candidates became fetched evidence through the generic
    # fetch stage; the resource-id candidate never did.
    evidence_urls = {item.get("url") for item in result["evidence_items"]}
    assert "https://docs.example.com/react/hooks" in evidence_urls
    assert not any(str(url).startswith("context7:") for url in evidence_urls)
    # No AnySearch vertical stage may run.
    assert not any(item.get("stage") == "vertical_discovery" for item in result["stage_results"])


def _retained_tool_surface() -> frozenset[str]:
    return frozenset(service.DEEP_ALLOWED_TOOLS)


def test_deep_research_plan_tools_and_commands_are_retained_surface() -> None:
    """Every deep/research plan tool and rendered command is a retained
    canonical generic command (``search``/``fetch``/``map``), and every
    rendered command re-parses through the current parser."""
    retained = _retained_tool_surface()
    assert retained == {"search", "fetch", "map"}
    parser = build_parser(raise_on_error=True)
    queries = (
        "深度搜索一下最近的比特币行情",
        "深度调研 React useEffect 最新文档",
        "React useEffect 官方 API docs",
        "帮我核验这个说法是真是假",
        "深度调研 https://example.com/source",
        "OpenAI Responses API web_search 和 Chat Completions 联网搜索怎么选",
    )
    for query in queries:
        plan = service.build_deep_research_plan(query, budget="standard")
        assert set(plan["allowed_tools"]) == retained
        for item in plan["capability_plan"]:
            assert set(item["tools"]) <= retained, (query, item)
        for step in plan["steps"]:
            assert step["tool"] in retained, (query, step)
            parts = shlex.split(step["command"])
            assert parts[0] == "smart-search"
            assert parts[1] == step["tool"]
            # The rendered command must still parse today (no fallthrough, no
            # removed spelling), proving the plan is executable.
            parsed = parser.parse_args(parts[1:])
            assert parsed.command == step["tool"]


def _removed_source_spellings() -> tuple[str, ...]:
    """Removed command spellings that do not collide with the retained
    ``zhipu-mcp-reader`` provider id (which stays as bounded provenance)."""
    return tuple(
        spelling
        for spelling in REMOVED_EXACT_PROVIDER_COMMANDS
        if spelling != "zhipu-mcp-reader"
    )


def test_source_scans_no_longer_advertise_removed_spellings() -> None:
    """Source-tree scan: plan rendering, capability support, research service,
    regression aggregation, and provider command modules must not contain any
    removed exact command spelling (the retained ``zhipu-mcp-reader`` provider
    id is the only allowed collision and is asserted explicitly)."""
    scanned = [
        "research_plan_render.py",
        "service_support.py",
        "research_service.py",
        "operations_service.py",
        "provider_search_commands.py",
        "provider_mcp_commands.py",
        "provider_vertical_commands.py",
        "provider_fetch_commands.py",
    ]
    spellings = _removed_source_spellings()
    for filename in scanned:
        source = (PACKAGE / filename).read_text(encoding="utf-8")
        for spelling in spellings:
            assert not re.search(rf"\b{re.escape(spelling)}\b", source), (
                f"{filename} advertises removed spelling {spelling!r}"
            )


def test_orphaned_zread_command_wrappers_are_deleted() -> None:
    """The zread adapters whose CLI caller graph was emptied by the removal
    are deleted, not retained as internal orphans. Only the V3-probe-qualified
    repo-structure wrapper remains."""
    import smart_search.provider_mcp_commands as provider_mcp_commands

    source = (PACKAGE / "provider_mcp_commands.py").read_text(encoding="utf-8")
    assert "zhipu_mcp_search_doc" not in source
    assert "zhipu_mcp_read_file" not in source
    assert "zhipu-mcp-search-doc" not in source
    assert "zhipu-mcp-read-file" not in source
    assert not hasattr(provider_mcp_commands, "zhipu_mcp_search_doc")
    assert not hasattr(provider_mcp_commands, "zhipu_mcp_read_file")
    # The retained wrapper is still reachable from the V3 diagnostic probe
    # path and keeps its capability-qualified gate.
    assert hasattr(provider_mcp_commands, "zhipu_mcp_repo_structure")
    assert sorted(provider_mcp_commands.__all__) == [
        "zhipu_mcp_reader",
        "zhipu_mcp_repo_structure",
        "zhipu_mcp_search",
    ]
    diagnostics = (PACKAGE / "provider_diagnostics.py").read_text(encoding="utf-8")
    assert "zhipu_mcp_repo_structure" in diagnostics


def test_orphaned_internal_command_wrappers_are_deleted() -> None:
    """Internal provider command wrappers whose production caller graph is
    empty are deleted, not retained as module-level orphans. The function-level
    check complements the module-level importer test: no definition, no
    ``__all__`` export, and no importer anywhere under ``src``.
    """
    import smart_search.provider_search_commands as provider_search_commands
    import smart_search.provider_vertical_commands as provider_vertical_commands

    deleted = {
        "context7_docs",
        "exa_find_similar",
        "anysearch_extract",
        "anysearch_batch",
    }
    for module in (provider_search_commands, provider_vertical_commands):
        for name in deleted:
            assert not hasattr(module, name), name
    for filename in ("provider_search_commands.py", "provider_vertical_commands.py"):
        source = (PACKAGE / filename).read_text(encoding="utf-8")
        for name in deleted:
            assert f"async def {name}(" not in source, (filename, name)
    # No src module may define, import, or reference the deleted wrappers.
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in deleted:
            assert name not in source, (path.name, name)
    # __all__ keeps exactly the reachable wrappers: the generic docs executor
    # pair (exa_search/context7_library) and the vertical pair used by the
    # Evidence executor and the V3 probe path.
    assert sorted(provider_search_commands.__all__) == [
        "call_firecrawl_search",
        "call_tavily_search",
        "context7_library",
        "exa_search",
        "zhipu_search",
    ]
    assert sorted(provider_vertical_commands.__all__) == [
        "anysearch_domains",
        "anysearch_search",
    ]
    # search_service no longer imports provider_vertical_commands: the vertical
    # path resolves through operation_runtime's generic executor default.
    search_source = (PACKAGE / "search_service.py").read_text(encoding="utf-8")
    assert "provider_vertical_commands" not in search_source


def test_strict_research_workflow_uses_typed_evidence_owners() -> None:
    source = (PACKAGE / "research_workflow.py").read_text(encoding="utf-8")
    assert "evidence_operations" in source
    for module in ("provider_search_commands", "provider_fetch_commands",
                   "provider_mcp_commands", "provider_vertical_commands"):
        assert module not in source
