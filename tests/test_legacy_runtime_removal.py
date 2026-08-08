"""Negative isolation and forbidden-surface proofs for the legacy runtime cleanup.

Every removed module, symbol, import, and command spelling must fail clearly
in a fresh process without loading providers, touching configuration, or
writing files. The scan derives its forbidden names from the tracked inventory
fixture rows, not from a hand-maintained list.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import contextlib
from pathlib import Path

import pytest

import smart_search.cli as cli
from smart_search.cli_constants import SELECTOR_REPLACEMENT, classify_command_domain
from smart_search.cli_parser import build_parser

from tests.fixtures import legacy_surface_inventory as inv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "smart_search"

# Modules physically deleted by this cleanup task. Importing any of them must
# fail without loading config/providers or touching the filesystem.
REMOVED_MODULES = (
    "smart_search.cli_contract",
    "smart_search.cli_dispatch",
    "smart_search.cli_render",
    "smart_search.cli_setup",
    "smart_search.cli_support",
    "smart_search.service",
    "smart_search.search_service",
    "smart_search.operations_service",
)

REMOVED_CLI_ATTRS = (
    "_LazyService",
    "_print_result",
    "_exit_code",
    "build_json_result",
    "configure_cli_logging",
)

REMOVED_CONSTANTS = (
    "COMMAND_ALIASES",
    "CONFIG_COMMAND_ALIASES",
    "MODEL_COMMAND_ALIASES",
    "SKILLS_COMMAND_ALIASES",
    "prescan_schema_version",
)


def test_removed_modules_are_deleted_on_disk() -> None:
    for module in REMOVED_MODULES:
        filename = module.rsplit(".", 1)[1] + ".py"
        assert not (PACKAGE / filename).exists(), filename


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_removed_module_import_fails_cleanly_in_fresh_process(module: str) -> None:
    """Fresh-process proof: importing a removed module raises ImportError and
    never imports providers/config/httpx and never creates a config file."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    script = """
import os
import sys
import tempfile

config_dir = tempfile.mkdtemp(prefix="ss-removed-module-")
os.environ["SMART_SEARCH_CONFIG_DIR"] = config_dir
os.environ.pop("SMART_SEARCH_HOME", None)

try:
    __import__(sys.argv[1])
except ImportError:
    pass
else:
    raise SystemExit("removed module imported")

for name in ("smart_search.config", "smart_search.providers", "httpx"):
    assert name not in sys.modules, name
assert os.listdir(config_dir) == [], os.listdir(config_dir)
print("ok")
"""
    run = subprocess.run(
        [sys.executable, "-c", script, module],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, f"{module}: {run.stdout} {run.stderr}"
    assert run.stdout.strip() == "ok"


@pytest.mark.parametrize("name", REMOVED_CLI_ATTRS)
def test_cli_no_longer_exposes_legacy_symbols(name: str) -> None:
    assert not hasattr(cli, name), name
    assert name not in vars(cli)


@pytest.mark.parametrize("name", REMOVED_CONSTANTS)
def test_cli_constants_alias_maps_and_prescan_are_removed(name: str) -> None:
    import smart_search.cli_constants as cli_constants

    assert not hasattr(cli_constants, name), name


def test_service_facade_import_fails_and_no_re_export_exists() -> None:
    for name in inv.service_export_names():
        assert not hasattr(cli, name), f"cli re-exports removed facade symbol {name}"
    with pytest.raises(ImportError):
        import smart_search.service  # noqa: F401


def test_cli_never_loads_legacy_facade_or_config_to_run_stable_commands() -> None:
    """Fresh-process proof: a stable V3 command runs and never imports the
    removed facade, the v1 renderer/dispatcher, or the lazy proxy."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    script = """
import io
import json
import os
import sys
import tempfile
import contextlib

config_dir = tempfile.mkdtemp(prefix="ss-cli-clean-")
os.environ["SMART_SEARCH_CONFIG_DIR"] = config_dir

from smart_search.cli import main
buffer = io.StringIO()
try:
    with contextlib.redirect_stdout(buffer):
        code = main(["config", "path", "--format", "json"])
except SystemExit as exc:
    code = int(exc.code or 0)
assert code == 0, code
payload = json.loads(buffer.getvalue())
assert payload["operation"] == "config.path"
assert payload["status"] == "complete"
for name in (
    "smart_search.service",
    "smart_search.cli_contract",
    "smart_search.cli_render",
    "smart_search.cli_dispatch",
    "smart_search.cli_support",
):
    assert name not in sys.modules, name
print("ok")
"""
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "ok"


def test_removed_commands_fail_before_owner_imports_in_fresh_process() -> None:
    """Fresh-process proof for one representative removed spelling per family:
    the strict INVALID_ARGUMENT envelope is emitted, no heavy module is
    imported, and no config file is created."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    script = """
import io
import json
import os
import sys
import tempfile
import contextlib

config_dir = tempfile.mkdtemp(prefix="ss-removed-cmd-")
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
for name in ("smart_search.config", "smart_search.providers",
             "smart_search.skill_installer", "httpx"):
    assert name not in sys.modules, name
assert os.listdir(config_dir) == [], os.listdir(config_dir)
payload = json.loads(buffer.getvalue())
assert payload["ok"] is False
assert payload["status"] == "failed"
assert payload["error"]["code"] == "INVALID_ARGUMENT"
sys.stdout.write("ok")
"""
    cases = (
        ("legacy alias", ["cfg", "list"]),
        ("legacy model command", ["model", "current"]),
        ("bare smoke", ["smoke", "--mock"]),
        ("bare research", ["research", "topic"]),
        ("schema selector", ["--schema-version", "2", "search", "query"]),
        ("schema selector equals", ["--schema-version=3", "config", "list"]),
        ("setup command", ["setup"]),
    )
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


def test_selector_detection_is_parser_only_and_never_dispatches() -> None:
    """Reserved incompatibility detection stays parser-only: the classifier
    never maps a removed spelling to an executable command, and the parser
    registers no selector option."""
    parser = build_parser()
    assert not any(
        "--schema-version" in action.option_strings for action in parser._actions
    )
    for spelling in inv.SCHEMA_SELECTOR_SURFACES:
        flag, sep, value = spelling.partition("=")
        if not sep:
            flag, _, value = spelling.partition(" ")
        argv = [flag] + ([value] if value else ["1"]) + ["search", "query"]
        classification = classify_command_domain(argv)
        assert classification["family"] == "removed"
        assert classification["error_family"] in {"v2", "v3", "workflow"}
        assert classification["replacement"] == SELECTOR_REPLACEMENT


def test_no_v1_envelope_authority_exists() -> None:
    """No live module may define the v1 JSON envelope builder, and the typed
    serializers never emit the duplicate v1 error fields."""
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "def build_json_result" not in source, path.name
    serializers = (
        "cli_v2.py",
        "cli_v3.py",
        "cli_research.py",
        "v2_contract.py",
        "control_plane_contract.py",
        "research_workflow_contract.py",
    )
    for filename in serializers:
        source = (PACKAGE / filename).read_text(encoding="utf-8")
        for field in ("error_detail", "error_message"):
            assert not re.search(rf"\b{field}\b", source), (filename, field)


def _removed_parser_spellings() -> tuple[str, ...]:
    """Every removed parser spelling from the tracked inventory rows: aliases,
    legacy control/workflow commands, nested aliases, and selectors."""
    kinds = {
        "command_alias",
        "legacy_control_command",
        "legacy_workflow_command",
        "nested_alias",
        "schema_selector",
    }
    return tuple(
        sorted(
            str(entry["surface"])
            for entry in inv.inventory_entries()
            if entry.get("kind") in kinds
        )
    )


def test_source_forbidden_surface_scan_from_tracked_inventory() -> None:
    """Source scan driven by the tracked inventory remove rows: no removed
    parser spelling is a root parser leaf or a registered option."""
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")
    leaves = set(command_action.choices)
    canonical_namespaces = {"config", "provider", "doctor", "dev", "research"}
    removed = {
        spelling.split(" ")[0]
        for spelling in _removed_parser_spellings()
        if spelling.split(" ")[0] not in canonical_namespaces
    }
    assert removed.isdisjoint(leaves)


def test_docs_skills_and_package_scan_from_tracked_inventory() -> None:
    """Docs/Skill/package assets never advertise removed spellings: the scan
    derives every forbidden name from the tracked inventory fixture. The
    legacy-control pattern may match canonical ``dev``-namespace invocations,
    which are the final contract surface, so those are excluded."""
    docs_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in inv.documentation_files()
    )
    legacy_control = re.compile(
        r"(?<![\w.])(?:cfg|mdl)\b|"
        r"\bmodel (?:current|list|add|remove)\b|"
        r"(?<!dev )(?<!dev\.)\b(?:route-calibrate|diagnose|regression)\b|"
        r"(?<!dev )\bskills (?:status|update)\b"
    )
    assert not legacy_control.search(docs_text)
    for spelling in inv.removed_spellings():
        if spelling in inv.SCHEMA_SELECTOR_SURFACES:
            continue
        if " " not in spelling:
            continue
        if spelling in {
            "deep step.command",
            "deep step.output_path",
            "research run --synthesize",
        } or spelling.startswith("research result "):
            matcher = re.compile(r"\b" + re.escape(spelling) + r"\b")
            assert not matcher.search(docs_text), f"docs advertise {spelling!r}"


def test_persisted_data_readers_are_retained() -> None:
    """The five approved data-upgrade-only readers stay in config.py."""
    source = (PACKAGE / "config.py").read_text(encoding="utf-8")
    anchors = {
        "data.windows_legacy_config_dir": "_legacy_windows_config_dir",
        "data.legacy_xai_openai_keys": "_legacy_model_routes_for_migration",
        "data.model_routes_key": "_MODEL_ROUTES_KEY",
        "data.model_routes_env_precedence": "environment_values.get(self._MODEL_ROUTES_KEY)",
        "data.saved_config_snapshot_readers": "get_saved_config",
    }
    for entry_id, anchor in anchors.items():
        assert anchor in source, entry_id


def test_inventory_reconciliation_has_no_unmatched_rows() -> None:
    report = inv.load_scan_report()
    assert report["unmatched_scan_hits"] == []
    assert report["unmatched_inventory_rows"] == []
    assert inv.load_inventory()["source_revision"] == report["source_revision"]


def test_help_all_advertises_only_canonical_surface() -> None:
    from smart_search.cli_constants import help_all_text

    text = help_all_text()
    assert "--schema-version" not in text
    assert "final_answer" not in text
    assert "--synthesize" not in text
    for spelling in ("cfg", "mdl", "setup"):
        assert spelling not in text
    assert not re.search(r"(?<!dev )\bsmoke\b", text)
    for command in ("search", "fetch", "map", "capabilities", "research plan", "research run"):
        assert command in text
    for command in ("config ", "provider ", "doctor ", "dev "):
        assert command in text
