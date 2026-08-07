"""Runtime-inert freeze of the legacy surface disposition inventory.

This module loads the machine-readable inventory produced by
``08-02-legacy-surface-inventory`` and exposes tables used by integrity tests
and later negative fixtures. It does not participate in CLI dispatch.
"""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# Tracked freeze copies under tests/fixtures so CI/npm checks do not depend on
# the gitignored .trellis task tree. Task artifacts remain the human review
# copies and must stay byte-identical to these fixtures.
REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ARTIFACT = Path(__file__).resolve().with_name("legacy_surface_inventory.json")
SCAN_REPORT_ARTIFACT = Path(__file__).resolve().with_name("legacy_surface_scan_report.json")
TRELLIS_TASK_ARTIFACT = (
    REPO_ROOT
    / ".trellis"
    / "tasks"
    / "08-02-legacy-surface-inventory"
    / "artifacts"
    / "legacy-surface-inventory.json"
)

ALLOWED_DISPOSITIONS = frozenset({"remove", "data-upgrade-only"})
ALLOWED_TARGET_CONTRACTS = frozenset({"v2", "v3", "workflow", "persisted-data", "none"})

# Frozen selector spellings that must all be disposition=remove. The single-
# dash forms are prescan-recognized legacy spellings even though argparse rejects
# them later.
SCHEMA_SELECTOR_SURFACES: tuple[str, ...] = (
    "--schema-version",
    "--schema-version 1",
    "--schema-version 2",
    "--schema-version 3",
    "--schema-version=1",
    "--schema-version=2",
    "--schema-version=3",
    "-schema-version",
    "-schema-version 1",
    "-schema-version 2",
    "-schema-version 3",
)

# Current research() result keys are extracted from source by
# research_result_keys(). These spellings cover the separate parser and deep
# projection paths that do not appear in its final result dictionary.
RESEARCH_AUXILIARY_REMOVE_SURFACES: tuple[str, ...] = (
    "research run --synthesize",
    "deep step.command",
    "deep step.output_path",
)

# Documentation scans intentionally use precise legacy phrases. Generic
# `content` prose and `--format content` remain presentation vocabulary and are
# not treated as the removed research-result `content` field.
DOC_REFERENCE_PATTERNS: dict[str, str] = {
    "docs.ref.schema-version": r"--schema-version|schema-version",
    "docs.ref.final_answer": r"\bfinal_answer\b",
    "docs.ref.synthesize": r"--synthesize|synthesis_enabled|synthesis_error|response_mode|synthesize",
    "docs.ref.legacy_control": r"\b(?:cfg|mdl|model (?:current|list|add|remove)|route-calibrate|diagnose|regression|skills (?:status|update))\b",
}

# Namespace leaves that become stable operations are retained targets, never
# remove rows. The key is the exact currently accepted namespace spelling.
RETAINED_NAMESPACE_TARGETS: dict[str, tuple[str, str]] = {
    "research plan": ("workflow", "research plan"),
    "research run": ("workflow", "research run"),
    "doctor probe": ("v3", "doctor.probe"),
    "doctor status": ("v3", "doctor.status"),
    "provider list": ("v3", "provider.catalog.list"),
    "provider status": ("v3", "provider.catalog.status"),
    "provider probe PROVIDER": ("v3", "provider.probe"),
    "provider routes current": ("v3", "provider.routes.current"),
    "provider routes list": ("v3", "provider.routes.list"),
    "provider routes add": ("v3", "provider.routes.add"),
    "provider routes remove": ("v3", "provider.routes.remove"),
    "dev route-explain": ("v3", "dev.route.explain"),
    "dev route-calibrate": ("v3", "dev.route.calibrate"),
    "dev diagnose openai-compatible": ("v3", "dev.diagnose.openai-compatible"),
    "dev smoke": ("v3", "dev.smoke"),
    "dev regression": ("v3", "dev.regression"),
    "dev skills status": ("v3", "dev.skills.status"),
    "dev skills update": ("v3", "dev.skills.update"),
}

# Approved persisted-data upgrade reader inventory ids.
DATA_UPGRADE_ONLY_IDS: tuple[str, ...] = (
    "data.windows_legacy_config_dir",
    "data.legacy_xai_openai_keys",
    "data.model_routes_key",
    "data.model_routes_env_precedence",
    "data.saved_config_snapshot_readers",
)

# Final V3 retained operations (must not appear as remove targets).
V3_RETAINED_OPERATIONS: tuple[str, ...] = (
    "config.path",
    "config.list",
    "config.set",
    "config.unset",
    "provider.catalog.list",
    "provider.catalog.status",
    "provider.probe",
    "provider.routes.current",
    "provider.routes.list",
    "provider.routes.add",
    "provider.routes.remove",
    "doctor.status",
    "doctor.probe",
    "dev.route.explain",
    "dev.route.calibrate",
    "dev.diagnose.openai-compatible",
    "dev.smoke",
    "dev.regression",
    "dev.skills.status",
    "dev.skills.update",
)


def repo_path(relative_path: str) -> Path:
    """Return a repository-local path without relying on the current directory."""
    return REPO_ROOT / relative_path


def documentation_files() -> tuple[Path, ...]:
    roots = (
        repo_path("skills/smart-search-cli"),
        repo_path("src/smart_search/assets/skills/smart-search-cli"),
        repo_path("docs"),
    )
    files = [repo_path("README.md"), repo_path("README.zh-CN.md")]
    for root in roots:
        files.extend(sorted(path for path in root.rglob("*.md") if path.is_file()))
    return tuple(files)


def documentation_hits(pattern: str) -> tuple[str, ...]:
    matcher = re.compile(pattern, re.IGNORECASE)
    return tuple(
        path.relative_to(REPO_ROOT).as_posix()
        for path in documentation_files()
        if matcher.search(path.read_text(encoding="utf-8"))
    )


def research_result_keys() -> set[str]:
    """Extract every literal public result key returned by ``research()``."""
    source = repo_path("src/smart_search/research_service.py")
    module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    function = next(
        node for node in module.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "research"
    )
    result_keys: set[str] = set()
    for node in ast.walk(function):
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "result" for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.Return):
            value = node.value
        if not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                result_keys.add(key.value)
    return result_keys


def namespace_path_without_placeholder(path: str) -> str:
    return path.removesuffix(" QUERY")


@lru_cache(maxsize=1)
def load_inventory() -> dict[str, Any]:
    """Load the task inventory JSON once."""
    payload = json.loads(TASK_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@lru_cache(maxsize=1)
def load_scan_report() -> dict[str, Any]:
    """Load the scan reconciliation JSON once."""
    payload = json.loads(SCAN_REPORT_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def inventory_entries() -> list[dict[str, Any]]:
    entries = load_inventory().get("entries")
    assert isinstance(entries, list)
    return entries


def entries_by_id() -> dict[str, dict[str, Any]]:
    return {str(entry["id"]): entry for entry in inventory_entries()}


def entries_with_kind(kind: str) -> list[dict[str, Any]]:
    return [entry for entry in inventory_entries() if entry.get("kind") == kind]


def removed_spellings() -> tuple[str, ...]:
    """Surfaces marked remove that later negative tests can freeze against."""
    values = [
        str(entry["surface"])
        for entry in inventory_entries()
        if entry.get("disposition") == "remove"
    ]
    return tuple(sorted(set(values)))


def data_upgrade_only_ids() -> tuple[str, ...]:
    return tuple(
        sorted(
            str(entry["id"])
            for entry in inventory_entries()
            if entry.get("disposition") == "data-upgrade-only"
        )
    )


def alias_surfaces() -> set[str]:
    return {
        str(entry["surface"])
        for entry in inventory_entries()
        if entry.get("kind") == "command_alias"
    }


def service_export_names() -> set[str]:
    names: set[str] = set()
    prefix = "smart_search.service."
    for entry in inventory_entries():
        if entry.get("kind") != "python_export":
            continue
        surface = str(entry["surface"])
        if surface.startswith(prefix):
            names.add(surface[len(prefix) :])
    return names
