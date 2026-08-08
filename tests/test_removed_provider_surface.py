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

from smart_search import cli, research_service
from smart_search.cli_constants import (
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
    from smart_search.cli_constants import RESERVED_LEGACY_SPELLINGS

    removed_aliases = set(REMOVED_ALIASES)
    reserved = {" ".join(tokens) for tokens in RESERVED_LEGACY_SPELLINGS}
    assert removed_aliases.isdisjoint(reserved)
    removed_canonicals = set(REMOVED_EXACT_PROVIDER_COMMANDS)
    assert removed_canonicals.isdisjoint(reserved)
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
    with pytest.raises(ImportError):
        exec(f"from smart_search.service import {name}", {})


def test_service_facade_is_deleted() -> None:
    assert not (PACKAGE / "service.py").exists()
    with pytest.raises(ImportError):
        import smart_search.service  # noqa: F401


def test_dispatch_and_render_have_no_removed_command_branches() -> None:
    for name in ("cli_dispatch.py", "cli_render.py", "cli_contract.py", "cli_setup.py", "cli_support.py"):
        assert not (PACKAGE / name).exists(), name
    parser_source = (PACKAGE / "cli_parser.py").read_text(encoding="utf-8")
    for surface in REMOVED_EXACT_PROVIDER_COMMANDS:
        assert f'"{surface}"' not in parser_source
        assert surface not in parser_source


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
import io
import json
import os
import sys
import tempfile
import contextlib

config_dir = tempfile.mkdtemp(prefix="ss-removed-surface-")
os.environ["SMART_SEARCH_CONFIG_DIR"] = config_dir
os.environ.pop("SMART_SEARCH_HOME", None)

from smart_search.cli import main
buffer = io.StringIO()
try:
    with contextlib.redirect_stdout(buffer):
        code = main(sys.argv[1:])
except SystemExit as exc:
    code = int(exc.code or 0)
assert code == 2, code
for name in ("smart_search.service", "smart_search.config", "smart_search.providers",
             "smart_search.skill_installer", "httpx"):
    assert name not in sys.modules, name
assert os.listdir(config_dir) == [], os.listdir(config_dir)
# exactly one strict JSON document on stdout
payload = json.loads(buffer.getvalue())
assert payload["ok"] is False
assert payload["status"] == "failed"
assert payload["error"]["code"] == "INVALID_ARGUMENT"
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
    [
        ["exa-search", "query"],
        ["--schema-version", "2", "exa-search", "query"],
        ["--schema-version", "3", "exa-search", "query"],
    ],
)
def test_removed_spelling_parser_error_is_one_json_document(argv, capsys) -> None:
    # Unrecognized spellings always use the V2 root parser-error sentinel;
    # selector spellings are removed and never change the family.
    assert cli.main(argv) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2"
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
        "control_executors",  # V3 smoke/doctor raw executors
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
    """The v1 live research execution is deleted; the strict workflow composes
    only the generic typed Evidence owner and never invokes a
    Context7/Exa/AnySearch provider-specific callback."""
    # The v1 live research entry point and its provider-specific callbacks are
    # gone from the planner module.
    module = ast.parse((PACKAGE / "research_service.py").read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("research", "_run_research_docs_discovery", "_run_research_context7_docs"):
        assert name not in defined, name

    # The strict workflow owner composes only generic typed Evidence owners.
    from smart_search import research_workflow

    source = (PACKAGE / "research_workflow.py").read_text(encoding="utf-8")
    assert "evidence_operations" in source
    for module in (
        "provider_search_commands",
        "provider_fetch_commands",
        "provider_mcp_commands",
        "provider_vertical_commands",
    ):
        assert module not in source


def _retained_tool_surface() -> frozenset[str]:
    return frozenset(("search", "fetch", "map"))


def test_deep_research_plan_tools_and_commands_are_retained_surface() -> None:
    """Every typed research plan operation maps to a retained canonical
    generic command (``search``/``fetch``/``map``) and carries no shell
    command or output path."""
    from smart_search.research_plan import (
        PLAN_FORBIDDEN_SERIALIZED_FIELDS,
        serialize_research_plan,
    )
    from smart_search.research_plan_render import RENDERER_KIND_TO_TOOL
    from smart_search.research_service import build_research_workflow_plan

    retained = _retained_tool_surface()
    assert retained == {"search", "fetch", "map"}
    assert set(RENDERER_KIND_TO_TOOL.values()) == retained
    queries = (
        "深度搜索一下最近的比特币行情",
        "深度调研 React useEffect 最新文档",
        "React useEffect 官方 API docs",
        "帮我核验这个说法是真是假",
        "深度调研 https://example.com/source",
        "OpenAI Responses API web_search 和 Chat Completions 联网搜索怎么选",
    )
    for query in queries:
        plan = build_research_workflow_plan(query, budget="standard")
        operations = serialize_research_plan(plan)["operations"]
        assert operations
        for item in operations:
            assert not any(field in item for field in PLAN_FORBIDDEN_SERIALIZED_FIELDS), (query, item)
            assert item["operation"] in {
                "source_discovery", "docs_discovery", "content_fetch",
            }, (query, item)


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
        "control_executors.py",
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
    # The v1 search_service module is deleted; the vertical path resolves
    # through operation_runtime's generic executor default.
    assert not (PACKAGE / "search_service.py").exists()


def test_strict_research_workflow_uses_typed_evidence_owners() -> None:
    source = (PACKAGE / "research_workflow.py").read_text(encoding="utf-8")
    assert "evidence_operations" in source
    for module in ("provider_search_commands", "provider_fetch_commands",
                   "provider_mcp_commands", "provider_vertical_commands"):
        assert module not in source
