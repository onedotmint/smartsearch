"""Integrity checks for the legacy surface disposition inventory."""

from __future__ import annotations

import json
import re
import subprocess

import pytest

import pytest

from smart_search.cli_constants import (
    NAMESPACE_COMMANDS,
    RESERVED_LEGACY_SPELLINGS,
    SELECTOR_REPLACEMENT,
    classify_command_domain,
)
from smart_search.cli_parser import build_parser

from tests.fixtures import legacy_surface_inventory as inv
from tests.fixtures.v1_cli_inventory import (
    ALIAS_TO_CANONICAL,
    RESEARCH_COMPAT_FIELDS,
    SERVICE_PUBLIC_EXPORTS,
)
from tests.fixtures.v1_json_baselines import (
    CAPABILITIES_SUCCESS_KEYS,
    DOCTOR_CORE_KEYS,
    FETCH_CORE_KEYS,
    MAP_CORE_KEYS,
    SEARCH_CORE_KEYS,
    V1_ENVELOPE_REQUIRED_KEYS,
    V1_META_REQUIRED_KEYS,
)


REQUIRED_ENTRY_FIELDS = (
    "id",
    "kind",
    "surface",
    "current_authority",
    "disposition",
    "replacement",
    "target_contract",
    "deletion_condition",
    "owning_child_tasks",
    "fixture",
    "rollback_point",
    "evidence",
)

PARSER_SPELLING_KINDS = frozenset(
    {
        "schema_selector",
        "command_alias",
        "nested_alias",
        "exact_provider_command",
        "legacy_control_command",
        "legacy_workflow_command",
        "namespace_leaf",
        "research_compat_field",
    }
)


def _entries_for_kind(kind: str) -> dict[str, dict]:
    return {str(entry["surface"]): entry for entry in inv.entries_with_kind(kind)}


def _top_level_canonical_commands() -> set[str]:
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")
    return {
        name
        for name, subparser in command_action.choices.items()
        if subparser.get_default("command") == name
        or subparser.get_default("namespace_operation") is not None
        or name in {"config", "provider", "doctor", "dev"}
    }


def _exact_provider_commands(commands: set[str]) -> set[str]:
    return {
        command
        for command in commands
        if command.startswith(("anysearch-", "context7-", "exa-", "zhipu-"))
    }


def test_inventory_and_scan_artifacts_exist() -> None:
    assert inv.TASK_ARTIFACT.is_file()
    assert inv.SCAN_REPORT_ARTIFACT.is_file()
    payload = inv.load_inventory()
    report = inv.load_scan_report()
    assert payload["schema_version"] == 1
    assert payload["generated_for_task"] == "08-02-legacy-surface-inventory"
    assert payload["parent_task"] == "08-02-structured-contract-migration"
    assert report["source_revision"] == payload["source_revision"]
    assert report["unmatched_scan_hits"] == []
    assert report["unmatched_inventory_rows"] == []


def test_entries_have_unique_ids_and_required_fields() -> None:
    entries = inv.inventory_entries()
    assert len(entries) == 184, "the removal-task inventory has 184 rows"
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    identities = [(entry["kind"], entry["surface"]) for entry in entries]
    assert len(identities) == len(set(identities)), "one current surface must have one authority"
    for entry in entries:
        for field in REQUIRED_ENTRY_FIELDS:
            assert field in entry, f"missing {field} on {entry.get('id')}"
        assert entry["disposition"] in inv.ALLOWED_DISPOSITIONS
        assert entry["target_contract"] in inv.ALLOWED_TARGET_CONTRACTS
        assert entry["replacement"], entry["id"]
        assert entry["evidence"], entry["id"]
        assert isinstance(entry["owning_child_tasks"], list)
        if entry["kind"] in PARSER_SPELLING_KINDS:
            assert entry.get("family_for_error") in {"v2", "v3", "workflow"}, entry["id"]


def test_replacements_are_single_canonical_outcomes() -> None:
    entries = inv.entries_by_id()
    for entry in entries.values():
        replacement = entry["replacement"]
        assert "|" not in replacement, f"multiple replacement alternatives on {entry['id']}"
        assert not replacement.endswith(".*"), f"wildcard replacement on {entry['id']}"
        assert "or removal" not in replacement, f"unresolved replacement on {entry['id']}"

    setup_replacement = "canonical V3 config.set"
    assert entries["alias.top.init"]["replacement"] == setup_replacement
    assert entries["alias.top.init"]["target_contract"] == "v3"
    assert entries["command.legacy_control.setup"]["replacement"] == setup_replacement


def test_schema_version_selectors_are_all_remove() -> None:
    selector_entries = inv.entries_with_kind("schema_selector")
    surfaces = {entry["surface"] for entry in selector_entries}
    parser = build_parser()
    # The parser no longer registers any schema-selector option.
    assert not any(
        "--schema-version" in action.option_strings for action in parser._actions
    )

    expected = set(inv.SCHEMA_SELECTOR_SURFACES)
    assert surfaces == expected == {
        "--schema-version", "-schema-version",
        "--schema-version 1", "--schema-version 2", "--schema-version 3",
        "--schema-version=1", "--schema-version=2", "--schema-version=3",
        "-schema-version 1", "-schema-version 2", "-schema-version 3",
    }
    for entry in selector_entries:
        assert entry["disposition"] == "remove"
        assert "omit selector" in entry["replacement"]
        assert "canonical family" in entry.get("notes", "")
    # Every frozen spelling is detected as removed with the selector
    # replacement by the canonical domain classifier.
    for spelling in expected:
        flag, sep, value = spelling.partition("=")
        if not sep:
            flag, _, value = spelling.partition(" ")
        argv = [flag] + ([value] if value else ["1"]) + ["search", "query"]
        classification = classify_command_domain(argv)
        assert classification["family"] == "removed"
        assert classification["replacement"] == SELECTOR_REPLACEMENT


def test_parser_alias_freeze_subset_of_inventory() -> None:
    """Alias normalization tables are deleted; the reserved legacy spelling
    table is the single live source of removed alias spellings."""
    alias_surfaces = inv.alias_surfaces()
    live_aliases = {
        " ".join(tokens): replacement
        for tokens, (_family, replacement) in RESERVED_LEGACY_SPELLINGS.items()
        if len(tokens) == 1 and tokens[0] in ALIAS_TO_CANONICAL
    }
    assert set(live_aliases) == set(ALIAS_TO_CANONICAL)
    assert alias_surfaces == set(live_aliases)
    for alias in live_aliases:
        assert alias in alias_surfaces, f"missing alias inventory row for {alias}"
    for entry in inv.entries_with_kind("command_alias"):
        assert entry["disposition"] == "remove"
    import smart_search.cli_constants as cli_constants

    for name in ("COMMAND_ALIASES", "CONFIG_COMMAND_ALIASES", "MODEL_COMMAND_ALIASES", "SKILLS_COMMAND_ALIASES"):
        assert not hasattr(cli_constants, name), name


def test_service_export_freeze_subset_of_inventory() -> None:
    """The broad facade is deleted: every inventory python_export row is a
    historical remove record and no live module may re-export them."""
    export_names = inv.service_export_names()
    assert export_names == set(SERVICE_PUBLIC_EXPORTS)
    for name in SERVICE_PUBLIC_EXPORTS:
        assert name in export_names, f"missing export inventory row for {name}"
    for entry in inv.entries_with_kind("python_export"):
        assert entry["disposition"] == "remove"
    with pytest.raises(ImportError):
        import smart_search.service  # noqa: F401
    import smart_search.cli as cli

    for name in SERVICE_PUBLIC_EXPORTS:
        assert not hasattr(cli, name), f"cli re-exports removed facade symbol {name}"


def test_cli_legacy_reexports_are_removed() -> None:
    """Every frozen ``smart_search.cli.*`` re-export row is deleted with the
    v1 lazy proxy; the cli module keeps only the canonical entry point."""
    import smart_search.cli as cli

    surfaces = _entries_for_kind("python_export")
    expected = {
        "smart_search.cli.service",
        "smart_search.cli.Path",
        "smart_search.cli.subprocess",
        "smart_search.cli.argparse",
        "smart_search.cli.json",
        "smart_search.cli.build_json_result",
        "smart_search.cli.configure_cli_logging",
        "smart_search.cli.logger",
        "smart_search.cli.PromptConfigurationError",
        "smart_search.cli.build_parser",
        "smart_search.cli.SmartSearchArgumentParser",
        "smart_search.cli.PUBLIC_COMMANDS",
    }
    assert expected.issubset(surfaces)
    for name in ("service", "Path", "subprocess", "argparse", "json", "build_json_result", "logger"):
        assert not hasattr(cli, name), f"cli still re-exports {name}"
    with pytest.raises(ImportError):
        import smart_search.cli_support  # noqa: F401


def test_research_compat_fields_marked_remove() -> None:
    """The v1 research result projection is deleted; the inventory rows remain
    as the historical remove record and no live authority produces them."""
    surfaces = {entry["surface"] for entry in inv.entries_with_kind("research_compat_field")}
    expected = set(inv.RESEARCH_AUXILIARY_REMOVE_SURFACES)
    assert inv.research_result_keys() == set(), "no live research() result dict may remain"
    assert expected <= surfaces
    for field in RESEARCH_COMPAT_FIELDS:
        assert f"research result {field}" in surfaces
    for entry in inv.entries_with_kind("research_compat_field"):
        assert entry["disposition"] == "remove"
        assert entry["target_contract"] == "workflow"


def test_data_upgrade_only_set_matches_approved_readers() -> None:
    actual = inv.data_upgrade_only_ids()
    assert actual == tuple(sorted(inv.DATA_UPGRADE_ONLY_IDS))
    anchors = {
        "data.windows_legacy_config_dir": "_legacy_windows_config_dir",
        "data.legacy_xai_openai_keys": "_legacy_model_routes_for_migration",
        "data.model_routes_key": "_MODEL_ROUTES_KEY",
        "data.model_routes_env_precedence": "environment_values.get(self._MODEL_ROUTES_KEY)",
        "data.saved_config_snapshot_readers": "get_saved_config",
    }
    source = inv.repo_path("src/smart_search/config.py").read_text(encoding="utf-8")
    for entry_id, anchor in anchors.items():
        entry = inv.entries_by_id()[entry_id]
        assert entry["disposition"] == "data-upgrade-only"
        assert entry["target_contract"] == "persisted-data"
        assert anchor in source, entry_id


def test_inventory_source_revision_is_reachable_baseline() -> None:
    checkout = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
        cwd=inv.REPO_ROOT,
    )
    if checkout.returncode != 0:
        return
    checkout_root = checkout.stdout.strip()
    revision = inv.load_inventory()["source_revision"]
    baseline = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
        cwd=checkout_root,
    )
    if baseline.returncode != 0:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            check=True,
            capture_output=True,
            text=True,
            cwd=checkout_root,
        )
        if shallow.stdout.strip() == "true":
            pytest.skip("shallow checkout omits the recorded baseline; fetch history to verify ancestry")
        baseline.check_returncode()
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=checkout_root,
    )


def test_package_skill_asset_reference_is_retained_not_remove() -> None:
    package = json.loads(inv.repo_path("package.json").read_text(encoding="utf-8"))
    package_paths = package["files"]
    assert "src/smart_search/assets/skills/smart-search-cli/**" in package_paths
    assert not any(path.startswith("skills/") for path in package_paths)
    report = inv.load_scan_report()["scan_sources"]["package_assets"]
    assert report == {
        "retained_package_paths": ["src/smart_search/assets/skills/smart-search-cli/**"],
        "excluded_root_paths": ["skills/"],
    }


def test_v3_retained_operations_are_not_remove_targets() -> None:
    retained = inv.load_inventory()["retained_targets"]["v3"]
    assert tuple(retained) == inv.V3_RETAINED_OPERATIONS
    assert len(retained) == 20
    remove_surfaces = {
        entry["surface"]
        for entry in inv.inventory_entries()
        if entry["disposition"] == "remove"
    }
    for operation in inv.V3_RETAINED_OPERATIONS:
        assert operation not in remove_surfaces


def test_live_parser_and_namespace_scans_reconcile_to_inventory() -> None:
    commands = _top_level_canonical_commands()
    exact = _exact_provider_commands(commands)
    assert set(_entries_for_kind("exact_provider_command")) == exact

    # The parser registers only the final canonical tree: V2 evidence leaves,
    # the research workflow namespace, and the V3 control-plane namespaces.
    # Legacy control commands are removed spellings handled by the domain
    # classifier, not by argparse.
    assert exact == set()
    assert commands == {
        "search", "fetch", "map", "capabilities",
        "research", "config", "provider", "doctor", "dev",
    }
    alias_names = set(ALIAS_TO_CANONICAL)
    assert not any(alias in commands for alias in alias_names)

    nested_control_surfaces = {
        str(entry["surface"])
        for entry in inv.entries_with_kind("legacy_control_command")
        if " " in str(entry["surface"])
    }
    expected_nested_aliases = {
        " ".join(tokens)
        for tokens in RESERVED_LEGACY_SPELLINGS
        if len(tokens) == 2 and " ".join(tokens) not in nested_control_surfaces
    }
    assert set(_entries_for_kind("nested_alias")) == expected_nested_aliases

    namespace_paths = {item["path"] for item in NAMESPACE_COMMANDS}
    removed_namespace_paths = {
        item["path"].removesuffix(" QUERY")
        for item in NAMESPACE_COMMANDS
        if item["path"].startswith(("provider exa", "provider context7", "provider zhipu", "experimental "))
    }
    assert set(_entries_for_kind("namespace_leaf")) == removed_namespace_paths
    retained_paths = {
        item["path"].removesuffix(" QUERY")
        for item in NAMESPACE_COMMANDS
        if item["path"].removesuffix(" QUERY") not in removed_namespace_paths
    }
    assert set(inv.RETAINED_NAMESPACE_TARGETS) == retained_paths

    retained_report = inv.load_scan_report()["scan_sources"]["namespace_retained"]["retained_target_paths"]
    actual_retained = {
        item["path"]: (item["target_contract"], item["target"])
        for item in retained_report
    }
    assert actual_retained == inv.RETAINED_NAMESPACE_TARGETS


def test_v1_envelope_and_field_freezes_are_each_inventory_rows() -> None:
    surfaces = _entries_for_kind("v1_field")
    for field in V1_ENVELOPE_REQUIRED_KEYS:
        assert field in surfaces
    for field in V1_META_REQUIRED_KEYS:
        assert f"meta.{field}" in surfaces
    baseline_fields = set().union(
        CAPABILITIES_SUCCESS_KEYS,
        SEARCH_CORE_KEYS,
        FETCH_CORE_KEYS,
        MAP_CORE_KEYS,
        DOCTOR_CORE_KEYS,
    )
    for field in baseline_fields - {"content", "error_type"}:
        assert field in surfaces
    assert "data.content / top-level content projection" in surfaces
    assert "data.error_type / top-level error_type projection" in surfaces
    assert "top-level error_type" in surfaces
    assert "top-level error" in surfaces


def test_documentation_scans_are_clean_after_removal() -> None:
    """The four docs/Skill scan patterns no longer hit any legacy spelling.

    The inventory rows keep their frozen evidence as the historical remove
    record; the final state asserts zero legacy hits. The legacy-control
    pattern may still match canonical ``dev``-namespace invocations, which
    are the final contract surface, so the legacy scan excludes exactly the
    canonical ``dev <command>`` / ``dev.<command>`` forms.
    """
    entries = inv.entries_by_id()
    legacy_control = re.compile(
        r"(?<![\w.])(?:cfg|mdl)\b|"
        r"\bmodel (?:current|list|add|remove)\b|"
        r"(?<!dev )(?<!dev\.)\b(?:route-calibrate|diagnose|regression)\b|"
        r"(?<!dev )\bskills (?:status|update)\b"
    )
    for entry_id, pattern in inv.DOC_REFERENCE_PATTERNS.items():
        entry = entries[entry_id]
        assert entry["disposition"] == "remove"
        files = inv.documentation_files()
        if entry_id == "docs.ref.legacy_control":
            hits = [
                str(path.relative_to(inv.REPO_ROOT))
                for path in files
                if legacy_control.search(path.read_text(encoding="utf-8"))
            ]
        else:
            hits = list(inv.documentation_hits(pattern))
        assert hits == [], (entry_id, hits)


def test_scan_report_cross_checks_agree_with_entries() -> None:
    report = inv.load_scan_report()
    checks = report["cross_checks"]
    assert checks["alias_freeze_count"] == checks["alias_inventory_count"]
    assert checks["service_export_freeze_count"] == checks["service_export_inventory_count"]
    assert checks["schema_selector_remove_count"] == len(inv.SCHEMA_SELECTOR_SURFACES)
    assert checks["persisted_data_upgrade_only_count"] == len(inv.DATA_UPGRADE_ONLY_IDS)
    assert checks["v3_retained_operation_count"] == 20
    flattened = {
        entry_id
        for source in report["scan_sources"].values()
        for entry_id in source.get("entry_ids", [])
    }
    assert flattened == {entry["id"] for entry in inv.inventory_entries()}
    for source in report["scan_sources"].values():
        if "entry_ids" in source:
            assert source["count"] == len(source["entry_ids"])
    source_kinds = {
        "command_aliases": "command_alias",
        "docs_skill_refs": "docs_skill_ref",
        "exact_provider_commands": "exact_provider_command",
        "legacy_control_commands": "legacy_control_command",
        "legacy_workflow_commands": "legacy_workflow_command",
        "namespace_remove": "namespace_leaf",
        "nested_aliases": "nested_alias",
        "persisted_data": "persisted_data_reader",
        "python_exports": "python_export",
        "research_fields": "research_compat_field",
        "schema_selector": "schema_selector",
        "v1_fields": "v1_field",
    }
    for source_name, kind in source_kinds.items():
        assert set(report["scan_sources"][source_name]["entry_ids"]) == {
            entry["id"] for entry in inv.entries_with_kind(kind)
        }
    assert report["totals"]["entries"] == len(inv.inventory_entries())


def test_fixture_module_stays_synchronized_with_artifacts() -> None:
    assert inv.TASK_ARTIFACT.exists()
    assert inv.SCAN_REPORT_ARTIFACT.exists()
    assert inv.load_inventory()["entries"]
    removed = inv.removed_spellings()
    assert "--schema-version 2" in removed
    assert "research result final_answer" in removed
    if inv.TRELLIS_TASK_ARTIFACT.exists():
        assert inv.TRELLIS_TASK_ARTIFACT.read_bytes() == inv.TASK_ARTIFACT.read_bytes()
        trellis_scan = inv.TRELLIS_TASK_ARTIFACT.with_name("scan-report.json")
        assert trellis_scan.read_bytes() == inv.SCAN_REPORT_ARTIFACT.read_bytes()


def test_no_production_runtime_modules_changed_by_inventory_task() -> None:
    prod_path = inv.repo_path("src/smart_search/legacy_surface_inventory.py")
    assert not prod_path.exists()
