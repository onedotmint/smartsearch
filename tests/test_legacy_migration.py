"""Legacy persisted-data migration contract tests.

Covers the approved five inventory ``data-upgrade-only`` readers for the
published 0.1.0 input state: saved config snapshot readers, the Windows legacy
config-directory fallback, saved ``XAI_*`` / ``OPENAI_COMPATIBLE_*`` main-search
keys, the ``SMART_SEARCH_MODEL_ROUTES`` persisted array, and environment
precedence. Every test enforces the migration invariants: environment values
never land on disk, deterministic route order/ids, atomic writes with source
byte preservation, recursive redaction, reader isolation from runtime
serializers/aliases, and rollback-read without V1 output restoration.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import smart_search.control_operations as co
from smart_search.cli_constants import classify_command_domain
from smart_search.cli_parser import build_parser
from smart_search.config import config

from tests.fixtures import legacy_migration as lm
from tests.fixtures import legacy_surface_inventory as inv

ROOT = Path(__file__).resolve().parents[1]
SRC_CONFIG = ROOT / "src" / "smart_search" / "config.py"


def _write_v010_config(config_file: Path) -> bytes:
    config_file.write_text(lm.V010_CONFIG_JSON, encoding="utf-8")
    return config_file.read_bytes()


def _reset_config_path(monkeypatch, tmp_path):
    """Point the singleton at a clean temporary config root."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "_config_file", config_file)
    monkeypatch.setattr(config, "_config_dir_source", "override")
    monkeypatch.setattr(config, "_config_snapshot", None)
    monkeypatch.setattr(config, "_cached_model", None)
    return config_file


def _all_secrets() -> tuple[str, ...]:
    return (
        "xai-0-1-0-secret",
        "openai-0-1-0-secret",
        "exa-0-1-0-secret",
        "tavily-0-1-0-secret",
        "route-primary-secret",
        "route-backup-secret",
        "env-only-secret",
        "xai-win-legacy-secret",
        "openai-win-legacy-secret",
    )


# ---------------------------------------------------------------------------
# 0.1.0 fixture provenance and clean-root read
# ---------------------------------------------------------------------------


def test_v010_config_key_fixture_matches_published_tag_shape() -> None:
    published = subprocess.run(
        ["git", "show", "v0.1.0:src/smart_search/config.py"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if published.returncode != 0:
        pytest.skip("shallow checkout omits the v0.1.0 tag; skip tag provenance check")
    source = published.stdout
    match = re.search(r"_CONFIG_KEYS = \{(.*?)\n    \}", source, re.S)
    assert match, "v0.1.0 config.py must define _CONFIG_KEYS"
    tag_keys = set(re.findall(r'"([A-Z0-9_]+)"', match.group(1)))
    assert set(lm.V010_CONFIG_KEYS) == tag_keys
    assert "SMART_SEARCH_MODEL_ROUTES" not in lm.V010_CONFIG_KEYS
    live_keys = set(config._CONFIG_KEYS)
    # v0.3.0 adds Brave/Jina-rerank keys; the v0.1.0 key set must remain a
    # strict subset (backward compatible), never removed or renamed.
    assert tag_keys | {"SMART_SEARCH_MODEL_ROUTES"} <= live_keys
    assert tuple(sorted(lm.V010_CONFIG_KEYS)) == lm.V010_CONFIG_KEYS


def test_v010_config_reads_in_clean_config_root(monkeypatch, tmp_path) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    _write_v010_config(config_file)

    snapshot = config.snapshot
    assert snapshot.config_dir_source == "override"
    assert snapshot.file_values["XAI_API_KEY"] == "xai-0-1-0-secret"
    assert config.xai_api_key == "xai-0-1-0-secret"
    assert config.xai_api_url == "https://api.x.ai/v1"
    assert config.xai_model == "grok-4-fast"
    assert config.xai_tools_raw == "web_search,x_search"
    assert config.openai_compatible_api_url == "https://relay-a.example/v1"
    assert config.openai_compatible_api_key == "openai-0-1-0-secret"
    assert config.openai_compatible_model == "qwen3-max"
    assert config.openai_compatible_stream is True
    assert config.openai_compatible_fallback_models == ["qwen3-max-lite"]
    assert config.model_routes_configured is False

    saved = config.get_saved_config(masked=False)
    assert saved["XAI_API_KEY"] == "xai-0-1-0-secret"
    assert saved["OPENAI_COMPATIBLE_FALLBACK_MODELS"] == "qwen3-max-lite"
    assert "SMART_SEARCH_MODEL_ROUTES" not in saved
    masked = config.get_saved_config(masked=True)
    assert "xai-0-1-0-secret" not in json.dumps(masked)

    info = config.get_config_info()
    assert info["primary_api_mode"] == "xai-responses"
    assert info["XAI_API_KEY"].startswith("xai-")
    assert "xai-0-1-0-secret" not in json.dumps(info)


# ---------------------------------------------------------------------------
# Windows legacy config location fallback
# ---------------------------------------------------------------------------


def test_v010_windows_legacy_location_reads_only_under_documented_fallback(
    monkeypatch, tmp_path
) -> None:
    fake_home = tmp_path / "home"
    fake_local_appdata = tmp_path / "local-appdata"
    legacy_config = fake_home / ".config" / "smart-search" / "config.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(lm.V010_WINDOWS_LEGACY_HOME_CONFIG_JSON, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))
    monkeypatch.setattr(config, "_config_file", None)
    monkeypatch.setattr(config, "_config_dir_source", None)
    monkeypatch.setattr(config, "_config_snapshot", None)

    assert config.config_file == legacy_config
    assert config.config_dir_source == "legacy_windows_home"
    assert config.xai_api_key == "xai-win-legacy-secret"
    assert config.openai_compatible_model == "win-model"
    info = config.config_path_info()
    assert info["legacy_windows_config_exists"] is True
    assert info["config_dir_source"] == "legacy_windows_home"

    # The new default file, once present, wins and the legacy file is untouched.
    new_config = fake_local_appdata / "smart-search" / "config.json"
    new_config.parent.mkdir(parents=True)
    new_config.write_text(
        json.dumps({"XAI_API_KEY": "xai-new-default-secret", "XAI_MODEL": "new-model"}),
        encoding="utf-8",
    )
    config.invalidate_snapshot()
    monkeypatch.setattr(config, "_config_file", None)
    monkeypatch.setattr(config, "_config_dir_source", None)
    assert config.config_file == new_config
    assert config.config_dir_source == "default"
    assert config.xai_api_key == "xai-new-default-secret"
    assert legacy_config.read_text(encoding="utf-8") == lm.V010_WINDOWS_LEGACY_HOME_CONFIG_JSON


def test_v010_windows_legacy_fallback_upgrade_keeps_source_values(
    monkeypatch, tmp_path
) -> None:
    """The fallback resolves the legacy location as the active config file:
    the first controlled write upgrades it in place, keeps every source value,
    and never duplicates secrets anywhere else."""
    fake_home = tmp_path / "home"
    fake_local_appdata = tmp_path / "local-appdata"
    legacy_config = fake_home / ".config" / "smart-search" / "config.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(lm.V010_WINDOWS_LEGACY_HOME_CONFIG_JSON, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))
    monkeypatch.setattr(config, "_config_file", None)
    monkeypatch.setattr(config, "_config_dir_source", None)
    monkeypatch.setattr(config, "_config_snapshot", None)

    before = legacy_config.read_bytes()
    result = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))
    assert result.status is co.ControlOperationStatus.COMPLETE
    # The migration writes to the resolved (legacy) location; the legacy file
    # itself must never be modified in place by a second writer and the source
    # secrets remain readable there.
    assert config.config_file == legacy_config
    raw = json.loads(legacy_config.read_text(encoding="utf-8"))
    assert raw["XAI_API_KEY"] == "xai-win-legacy-secret"
    assert [r["id"] for r in raw["SMART_SEARCH_MODEL_ROUTES"]] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "primary",
    ]
    assert before != legacy_config.read_bytes()  # route list added to resolved file
    assert "xai-win-legacy-secret" in legacy_config.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Upgrade: deterministic routes, source preservation, environment non-copy
# ---------------------------------------------------------------------------


def test_first_model_add_upgrades_v010_with_stable_order_and_source_preservation(
    monkeypatch, tmp_path
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    before = _write_v010_config(config_file)

    result = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))

    assert result.status is co.ControlOperationStatus.COMPLETE
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    routes = raw["SMART_SEARCH_MODEL_ROUTES"]
    assert [route["id"] for route in routes] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "primary",
    ]
    assert routes[0] == {
        "id": "legacy-xai-responses",
        "provider": "xai-responses",
        "api_url": "https://api.x.ai/v1",
        "api_key": "xai-0-1-0-secret",
        "model": "grok-4-fast",
        "tools": ["web_search", "x_search"],
    }
    assert routes[1]["provider"] == "openai-compatible"
    assert routes[1]["api_url"] == "https://relay-a.example/v1"
    assert routes[1]["api_key"] == "openai-0-1-0-secret"
    assert routes[1]["model"] == "qwen3-max"
    assert routes[1]["stream"] is True
    assert routes[1]["fallback_models"] == ["qwen3-max-lite"]

    # Source preservation: every pre-existing key keeps its exact value; only
    # the route list key is added. No data is lost or rewritten.
    before_data = json.loads(before)
    assert set(raw) == set(before_data) | {"SMART_SEARCH_MODEL_ROUTES"}
    for key, value in before_data.items():
        assert raw[key] == value, key
    assert "unrelated" not in raw  # fixture has no unrelated key; preservation asserted above


@pytest.mark.parametrize(
    ("file_values", "environment_values", "secret"),
    [
        (
            json.loads(lm.V010_CONFIG_JSON),
            {"XAI_API_KEY": "env-only-secret"},
            "env-only-secret",
        ),
        (
            json.loads(lm.V010_CONFIG_JSON),
            {"OPENAI_COMPATIBLE_API_URL": "https://env-relay.example/v1"},
            "openai-0-1-0-secret",
        ),
    ],
)
def test_environment_owned_legacy_config_rejects_upgrade_and_preserves_bytes(
    monkeypatch, tmp_path, file_values, environment_values, secret
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    config_file.write_text(json.dumps(file_values), encoding="utf-8")
    before = config_file.read_bytes()
    for key, value in environment_values.items():
        monkeypatch.setenv(key, value)

    result = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))

    assert result.status is co.ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert "controlled by the environment" in result.error.message
    assert config_file.read_bytes() == before
    assert "SMART_SEARCH_MODEL_ROUTES" not in json.loads(before)
    assert secret not in json.dumps(result.result_dict, ensure_ascii=False)


def test_environment_owned_model_routes_block_local_management_without_write(
    monkeypatch, tmp_path
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    _write_v010_config(config_file)
    before = config_file.read_bytes()
    monkeypatch.setenv(
        "SMART_SEARCH_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "id": "env-route",
                    "provider": "xai-responses",
                    "api_url": "https://api.x.ai/v1",
                    "api_key": "env-only-secret",
                    "model": "grok-4-fast",
                }
            ]
        ),
    )

    result = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))
    assert result.status is co.ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert "controlled by the environment" in result.error.message
    assert config_file.read_bytes() == before
    assert "env-only-secret" not in config_file.read_text(encoding="utf-8")


def test_successful_migration_never_copies_environment_credentials_to_disk(
    monkeypatch, tmp_path
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    _write_v010_config(config_file)
    monkeypatch.setenv("EXA_API_KEY", "env-only-secret")
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily-secret")

    result = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))
    assert result.status is co.ControlOperationStatus.COMPLETE

    file_text = config_file.read_text(encoding="utf-8")
    assert "env-only-secret" not in file_text
    assert "env-tavily-secret" not in file_text
    # Pre-existing file secrets remain where they were published.
    assert "xai-0-1-0-secret" in file_text
    assert "exa-0-1-0-secret" in file_text
    assert "route-primary-secret" in file_text
    # Outputs never expose any credential.
    serialized = json.dumps(
        {"result": result.result_dict, "config": asyncio.run(co.run_config_list(show_secrets=False)).result_dict, "info": config.get_config_info()},
        ensure_ascii=False,
    )
    for secret in _all_secrets():
        assert secret not in serialized


# ---------------------------------------------------------------------------
# Invalid/conflicting/failed migration leaves source bytes unchanged
# ---------------------------------------------------------------------------


def test_conflicting_legacy_route_id_preserves_source_bytes(monkeypatch, tmp_path) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    _write_v010_config(config_file)
    before = config_file.read_bytes()

    result = asyncio.run(co.run_provider_routes_add(
        "legacy-xai-responses",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))
    assert result.status is co.ControlOperationStatus.FAILED
    assert result.error.type == "parameter_error"
    assert "duplicate id: legacy-xai-responses" in result.error.message
    assert config_file.read_bytes() == before


def test_invalid_saved_routes_are_config_failures_with_byte_preservation(
    monkeypatch, tmp_path
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    config_file.write_text(
        json.dumps(
            {
                **json.loads(lm.V010_CONFIG_JSON),
                "SMART_SEARCH_MODEL_ROUTES": [
                    {"id": "broken", "provider": "openai-compatible"}
                ],
            }
        ),
        encoding="utf-8",
    )
    before = config_file.read_bytes()

    listed = asyncio.run(co.run_provider_routes_list())
    assert listed.status is co.ControlOperationStatus.FAILED
    assert listed.error.type == "config_error"
    assert listed.side_effects.config.write_attempted is False
    assert config_file.read_bytes() == before


def test_atomic_write_failure_preserves_source_bytes_and_reports_uncommitted(
    monkeypatch, tmp_path
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    _write_v010_config(config_file)
    before = config_file.read_bytes()

    real_replace = os.replace

    def failing_replace(src, dst):
        if str(dst).endswith("config.json"):
            raise OSError("simulated atomic replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr("smart_search.config.os.replace", failing_replace)

    result = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))
    assert result.status is co.ControlOperationStatus.FAILED
    assert result.error.type == "config_error"
    assert config_file.read_bytes() == before
    assert not list(config_file.parent.glob("*.tmp"))

    outcome = asyncio.run(
        co.run_provider_routes_add(
            "primary",
            "openai-compatible",
            "https://primary.example/v1",
            "route-primary-secret",
            "primary-model",
        )
    )
    assert outcome.status is co.ControlOperationStatus.FAILED
    assert outcome.error.type == "config_error"
    assert outcome.side_effects.config.read is True
    assert outcome.side_effects.config.write_attempted is True
    assert outcome.side_effects.config.write_committed is False
    assert outcome.network.attempted is False
    envelope_text = json.dumps(outcome.result_dict)
    assert "route-primary-secret" not in envelope_text
    assert "xai-0-1-0-secret" not in envelope_text
    assert "simulated atomic replace failure" not in envelope_text
    assert config_file.read_bytes() == before


# ---------------------------------------------------------------------------
# Redaction and secret non-copy with file-content assertions
# ---------------------------------------------------------------------------


def test_recursive_redaction_masks_nested_and_url_embedded_credentials(
    monkeypatch, tmp_path
) -> None:
    config_file = _reset_config_path(monkeypatch, tmp_path)
    routes = {
        "SMART_SEARCH_MODEL_ROUTES": [
            {
                "id": "primary",
                "provider": "openai-compatible",
                "api_url": "https://user:pass@relay.example/v1?api-key=query-secret&api_key=alt-secret;token=sep-secret#frag-key=1",
                "api_key": "route-primary-secret",
                "model": "primary-model",
                "fallback_models": ["primary-lite"],
            }
        ]
    }
    config_file.write_text(json.dumps(routes), encoding="utf-8")
    monkeypatch.setenv("EXA_API_KEY", "env-only-secret")

    masked = config.get_model_routes(masked=True)
    assert masked[0]["api_key"] != "route-primary-secret"
    text = json.dumps(masked)
    for credential in (
        "route-primary-secret",
        "query-secret",
        "alt-secret",
        "sep-secret",
        "env-only-secret",
    ):
        assert credential not in text

    listed = asyncio.run(co.run_provider_routes_list())
    assert listed.status is co.ControlOperationStatus.COMPLETE
    assert "route-primary-secret" not in json.dumps(listed.result_dict, ensure_ascii=False)
    # The persisted file keeps the raw request URL and key for the provider.
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    assert raw["SMART_SEARCH_MODEL_ROUTES"][0]["api_url"] == routes["SMART_SEARCH_MODEL_ROUTES"][0]["api_url"]
    assert raw["SMART_SEARCH_MODEL_ROUTES"][0]["api_key"] == "route-primary-secret"


# ---------------------------------------------------------------------------
# Removed selectors and old Skill instructions (incompatibility fixtures)
# ---------------------------------------------------------------------------


def test_removed_selector_fixture_is_faithful_to_inventory() -> None:
    inventory_pairs = {
        str(entry["surface"]): str(entry["replacement"])
        for entry in inv.entries_with_kind("schema_selector")
    }
    assert len(inventory_pairs) == len(inv.SCHEMA_SELECTOR_SURFACES) == 11
    fixture_pairs = dict(lm.REMOVED_SCHEMA_SELECTOR_SPELLINGS)
    assert fixture_pairs == inventory_pairs
    for spelling, replacement in lm.REMOVED_SCHEMA_SELECTOR_SPELLINGS:
        assert replacement == lm.SELECTOR_REPLACEMENT
        assert "omit selector" in replacement
        assert "canonical command domain" in replacement
        # Every frozen selector spelling is detected as a removed spelling with
        # the canonical selector replacement by the domain classifier.
        flag, sep, value = spelling.partition("=")
        if not sep:
            flag, _, value = spelling.partition(" ")
        argv = [flag] + ([value] if value else ["1"]) + ["search", "query"]
        classification = classify_command_domain(argv)
        assert classification["family"] == "removed"
        assert classification["error_family"] == "v2"
        assert classification["legacy_spelling"].startswith(flag)
        assert classification["replacement"] == lm.SELECTOR_REPLACEMENT


def test_old_skill_instructions_fixture_is_faithful_to_inventory() -> None:
    rows = inv.entries_by_id()
    seen_row_ids: set[str] = set()
    for spelling, row_id, replacement in lm.OLD_SKILL_INSTRUCTIONS:
        assert row_id not in seen_row_ids, "one inventory row per old spelling"
        seen_row_ids.add(row_id)
        row = rows[row_id]
        assert row["disposition"] == "remove"
        assert str(row["replacement"]) == replacement
        notes = row.get("notes") or ""
        pattern_match = re.search(r"pattern=(.+?);", notes)
        if pattern_match:
            assert re.search(pattern_match.group(1), spelling), (spelling, row_id)
        else:
            assert str(row["surface"]) == spelling, (spelling, row_id)
        # The fixture names a replacement; it never maps the old spelling to a
        # different executable command (no silent reinterpretation). A spelling
        # may share vocabulary with its canonical domain ("diagnose" inside
        # "dev.diagnose.openai-compatible") but the replacement must never
        # begin with the old spelling as its command token.
        assert replacement != spelling
        assert not replacement.startswith(spelling)


def test_old_spellings_fixtures_are_not_runtime_dispatchers() -> None:
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")
    live_leaves = set(command_action.choices)
    for spelling, _replacement in lm.REMOVED_SCHEMA_SELECTOR_SPELLINGS:
        assert spelling not in live_leaves
        assert not spelling.lstrip("-").startswith(("search", "fetch", "map", "capabilities"))
    for spelling, _row_id, replacement in lm.OLD_SKILL_INSTRUCTIONS:
        assert replacement != spelling
        assert not replacement.startswith(spelling)
        assert spelling not in inv.V3_RETAINED_OPERATIONS
    # The fixture module itself must stay import-inert for the runtime: it is
    # data, not a dispatcher.
    module = ast.parse(
        (ROOT / "tests" / "fixtures" / "legacy_migration.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            assert module_name.split(".")[0] in {"typing", "__future__"}, f"runtime import: {module_name}"
        assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)), "dispatcher-like callable in fixture"


# ---------------------------------------------------------------------------
# Reader isolation from runtime serializers/aliases
# ---------------------------------------------------------------------------


def test_data_readers_are_isolated_from_runtime_serializers_and_aliases() -> None:
    """The migration boundary must not import or reference runtime projection."""
    source = SRC_CONFIG.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(SRC_CONFIG))
    forbidden_modules = {
        "service",
        "operations_service",
        "cli",
        "cli_contract",
        "cli_parser",
        "cli_constants",
        "api_v2",
        "v2_contract",
        "control_plane_contract",
        "control_plane_adapters",
        "control_operations",
        "presentation",
        "providers",
        "httpx",
    }
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in forbidden_modules, f"config.py imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root and root != "smart_search":
                continue
            if node.module and any(
                node.module == name or node.module.startswith(f"{name}.")
                for name in forbidden_modules
            ):
                raise AssertionError(f"config.py imports {node.module}")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.attr != "build_json_result", "config.py references build_json_result"
    assert "from smart_search" not in source or "from smart_search.security" in source
    assert "import smart_search" not in source


# ---------------------------------------------------------------------------
# Rollback-read: source and migrated state readable without V1 output
# ---------------------------------------------------------------------------


def test_rollback_read_without_v1_output_restoration(monkeypatch, tmp_path) -> None:
    """Reverting the reader transform keeps source bytes and never adds a
    reverse writer or V1 output projection."""
    config_file = _reset_config_path(monkeypatch, tmp_path)
    before_data = json.loads(_write_v010_config(config_file))

    migrated = asyncio.run(co.run_provider_routes_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "route-primary-secret",
        "primary-model",
        ))
    assert migrated.status is co.ControlOperationStatus.COMPLETE

    # (a) The documented recovery reader (the inventory source-revision reader,
    # which is byte-identical to the current reader) still reads the source.
    recovery = subprocess.run(
        ["git", "diff", "--quiet", "773e77d", "HEAD", "--", "src/smart_search/config.py"],
        check=False,
        cwd=ROOT,
    )
    if recovery.returncode == 0:
        assert config.xai_api_key == before_data["XAI_API_KEY"]
        assert config.openai_compatible_api_key == before_data["OPENAI_COMPATIBLE_API_KEY"]
        assert config.openai_compatible_stream is True
    assert config.get_saved_config(masked=False)["XAI_API_KEY"] == "xai-0-1-0-secret"

    # (b) Migrated state is readable in deterministic order through the same
    # snapshot boundary, and the source values were not overwritten.
    assert [r["id"] for r in config.model_routes] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "primary",
    ]
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    for key, value in before_data.items():
        assert raw[key] == value

    # (c) The typed Control owner consumes the migrated state without a V1
    # output projection or legacy semantic keys.
    outcome = asyncio.run(co.run_provider_routes_list())
    assert outcome.status is co.ControlOperationStatus.COMPLETE
    assert outcome.side_effects.config.read is True
    assert outcome.side_effects.config.write_attempted is False
    result = outcome.result_dict
    for legacy_key in ("ok", "error_type", "error", "network_attempted", "elapsed_ms", "schema_version", "data"):
        assert legacy_key not in result, legacy_key
    assert [r["id"] for r in result["routes"]] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "primary",
    ]
    assert "xai-0-1-0-secret" not in json.dumps(result, ensure_ascii=False)
    assert "route-primary-secret" not in json.dumps(result, ensure_ascii=False)

    # (d) No reverse migration writer exists on the migration boundary: the
    # route write path never references legacy main-search keys. (The legacy
    # reader may reference them for environment detection and property reads.)
    module = ast.parse(SRC_CONFIG.read_text(encoding="utf-8"), filename=str(SRC_CONFIG))
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "set_model_routes",
            "add_model_route",
        }:
            body_source = ast.get_source_segment(
                SRC_CONFIG.read_text(encoding="utf-8"), node
            ) or ""
            assert "XAI_" not in body_source, f"reverse writer in {node.name}"
            assert "OPENAI_COMPATIBLE_" not in body_source, f"reverse writer in {node.name}"
