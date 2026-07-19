import json
import os
import stat
from pathlib import Path

import pytest

from smart_search.config import Config


def _fresh_config_file(monkeypatch, tmp_path=None):
    config = Config()
    monkeypatch.setattr(config, "_config_file", None)
    monkeypatch.setattr(config, "_config_dir_source", None)
    monkeypatch.setattr(config, "_config_snapshot", None)
    if tmp_path is not None:
        monkeypatch.setattr(config, "_config_file", tmp_path / "config.json")
        monkeypatch.setattr(config, "_config_dir_source", "override")
    return config


def test_env_dir_overrides_config_file_path(monkeypatch, tmp_path):
    target = tmp_path / "custom-config-root"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(target))
    config = _fresh_config_file(monkeypatch)
    assert config.config_file == target / "config.json"
    assert config.config_dir_source == "environment"
    info = config.config_path_info()
    assert info["config_dir_override_value"] == str(target)
    assert info["config_dir_override_matches_default"] is False
    assert target.exists() and target.is_dir()


def test_windows_env_override_matching_default_is_reported(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_local_appdata = tmp_path / "local-appdata"
    default_dir = fake_local_appdata / "smart-search"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(default_dir))
    config = _fresh_config_file(monkeypatch)
    info = config.config_path_info()
    assert config.config_file == default_dir / "config.json"
    assert config.config_dir_source == "environment"
    assert info["default_config_file"] == str(default_dir / "config.json")
    assert info["config_dir_override_value"] == str(default_dir)
    assert info["config_dir_override_matches_default"] is True


def test_env_dir_pointing_at_unwritable_does_not_crash(monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    bogus = blocker / "child"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(bogus))
    config = _fresh_config_file(monkeypatch)
    assert config.config_file == bogus / "config.json"
    assert config.config_dir_source == "environment"
    assert config._load_config_file() == {}


def test_no_env_falls_back_to_platform_default(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_local_appdata = tmp_path / "local-appdata"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))
    config = _fresh_config_file(monkeypatch)
    assert config.config_file == fake_local_appdata / "smart-search" / "config.json"
    assert config.config_dir_source == "default"


def test_windows_uses_legacy_home_config_when_new_default_missing(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_local_appdata = tmp_path / "local-appdata"
    legacy_config = fake_home / ".config" / "smart-search" / "config.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))
    config = _fresh_config_file(monkeypatch)
    assert config.config_file == legacy_config
    assert config.config_dir_source == "legacy_windows_home"


def test_windows_prefers_new_default_when_both_new_and_legacy_exist(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_local_appdata = tmp_path / "local-appdata"
    legacy_config = fake_home / ".config" / "smart-search" / "config.json"
    new_config = fake_local_appdata / "smart-search" / "config.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text("{}", encoding="utf-8")
    new_config.parent.mkdir(parents=True)
    new_config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))
    config = _fresh_config_file(monkeypatch)
    assert config.config_file == new_config
    assert config.config_dir_source == "default"


def test_no_env_non_windows_falls_back_to_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "linux")
    config = _fresh_config_file(monkeypatch)
    assert config.config_file == fake_home / ".config" / "smart-search" / "config.json"
    assert config.config_dir_source == "default"


def test_env_dir_also_governs_log_dir_parent(monkeypatch, tmp_path):
    target = tmp_path / "shared-root"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(target))
    config = _fresh_config_file(monkeypatch)
    assert config.log_dir == target / "logs"
    assert config.log_dir_config_value == "logs"
    assert not (target / "logs").exists()


def test_tavily_timeout_defaults_to_thirty_seconds(monkeypatch):
    monkeypatch.delenv("TAVILY_TIMEOUT_SECONDS", raising=False)
    config = _fresh_config_file(monkeypatch)
    assert config.tavily_timeout == 30.0
    info = config.get_config_info()
    assert info["TAVILY_TIMEOUT_SECONDS"] == 30.0
    assert info["config_sources"]["TAVILY_TIMEOUT_SECONDS"] == "default"


def test_tavily_timeout_can_be_configured(monkeypatch):
    monkeypatch.setenv("TAVILY_TIMEOUT_SECONDS", "45")
    config = _fresh_config_file(monkeypatch)
    assert config.tavily_timeout == 45.0
    info = config.get_config_info()
    assert info["TAVILY_TIMEOUT_SECONDS"] == 45.0
    assert info["config_sources"]["TAVILY_TIMEOUT_SECONDS"] == "environment"


def test_config_snapshot_reads_file_once_for_config_info(monkeypatch, tmp_path):
    config = _fresh_config_file(monkeypatch, tmp_path)
    config._save_config_file({"XAI_API_KEY": "file-secret", "XAI_MODEL": "file-model"})
    calls = 0
    original_load = config._load_config_file

    def counted_load():
        nonlocal calls
        calls += 1
        return original_load()

    monkeypatch.setattr(config, "_load_config_file", counted_load)
    info = config.get_config_info()

    assert info["XAI_MODEL"] == "file-model"
    assert info["config_sources"]["XAI_API_KEY"] == "config_file"
    assert calls == 1


def test_config_snapshot_merges_environment_and_is_immutable(monkeypatch, tmp_path):
    config = _fresh_config_file(monkeypatch, tmp_path)
    config._save_config_file({"XAI_API_KEY": "file-secret"})
    monkeypatch.setenv("XAI_API_KEY", "environment-secret")

    snapshot = config.refresh()

    assert snapshot.values["XAI_API_KEY"] == "environment-secret"
    assert snapshot.file_values["XAI_API_KEY"] == "file-secret"
    assert config.xai_api_key == "environment-secret"
    assert config.get_config_source("XAI_API_KEY") == "environment"
    with pytest.raises(TypeError):
        snapshot.values["XAI_API_KEY"] = "changed"


def test_config_snapshot_refresh_reads_external_file_changes(monkeypatch, tmp_path):
    config = _fresh_config_file(monkeypatch, tmp_path)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.config_file.write_text(json.dumps({"XAI_MODEL": "old-model"}), encoding="utf-8")

    first = config.refresh()
    config.config_file.write_text(json.dumps({"XAI_MODEL": "new-model"}), encoding="utf-8")

    assert first.values["XAI_MODEL"] == "old-model"
    assert config.xai_model == "old-model"
    config.refresh()
    assert config.xai_model == "new-model"


def test_config_write_invalidates_snapshot(monkeypatch, tmp_path):
    config = _fresh_config_file(monkeypatch, tmp_path)
    config._save_config_file({"XAI_MODEL": "old-model"})
    assert config.xai_model == "old-model"

    config.set_config_value("XAI_MODEL", "new-model")

    assert config.xai_model == "new-model"
    config.unset_config_value("XAI_MODEL")
    assert config.xai_model == config._DEFAULT_MODEL


def test_absolute_log_dir_is_resolved_without_creation(monkeypatch, tmp_path):
    target = tmp_path / "shared-root"
    log_dir = tmp_path / "explicit-logs"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(target))
    monkeypatch.setenv("SMART_SEARCH_LOG_DIR", str(log_dir))
    config = _fresh_config_file(monkeypatch)
    assert config.log_dir == log_dir
    assert config.log_dir_config_value == str(log_dir)
    assert not log_dir.exists()


def test_save_unwritable_raises_with_hint(monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    bogus = blocker / "child"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(bogus))
    config = _fresh_config_file(monkeypatch)
    with pytest.raises(ValueError) as exc:
        config._save_config_file({"x": 1})
    assert "无法保存" in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable to Windows")
def test_config_storage_uses_owner_only_modes(monkeypatch, tmp_path):
    target = tmp_path / "secure-config"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(target))
    config = _fresh_config_file(monkeypatch)

    config._save_config_file({"XAI_API_KEY": "secret"})

    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.config_file.stat().st_mode) == 0o600

    config.config_file.chmod(0o644)
    config._save_config_file({"XAI_API_KEY": "rotated"})

    assert stat.S_IMODE(config.config_file.stat().st_mode) == 0o600
    assert json.loads(config.config_file.read_text(encoding="utf-8"))["XAI_API_KEY"] == "rotated"


def test_atomic_config_replace_preserves_previous_file_on_failure(monkeypatch, tmp_path):
    target = tmp_path / "secure-config"
    monkeypatch.setenv("SMART_SEARCH_CONFIG_DIR", str(target))
    config = _fresh_config_file(monkeypatch)
    config._save_config_file({"XAI_API_KEY": "old"})

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("smart_search.config.os.replace", fail_replace)
    with pytest.raises(ValueError, match="无法保存"):
        config._save_config_file({"XAI_API_KEY": "new"})

    assert json.loads(config.config_file.read_text(encoding="utf-8"))["XAI_API_KEY"] == "old"
    assert list(target.glob(".config.json.*.tmp")) == []


def test_unavailable_default_dir_does_not_fall_back_to_cwd(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("smart_search.config.sys.platform", "linux")
    monkeypatch.chdir(tmp_path)
    config = _fresh_config_file(monkeypatch)
    monkeypatch.setattr(config, "_safe_mkdir", lambda path: False)

    expected_file = fake_home / ".config" / "smart-search" / "config.json"
    assert config.config_file == expected_file
    assert config.config_dir_source == "default"
    assert not (tmp_path / ".smart-search").exists()

    info = config.config_path_info()
    assert info["ok"] is False
    assert info["error_type"] == "config_error"
    assert "SMART_SEARCH_CONFIG_DIR" in info["error"]
    config_info = config.get_config_info()
    assert config_info["config_storage_ok"] is False
    assert "SMART_SEARCH_CONFIG_DIR" in config_info["config_storage_error"]
    assert config_info["config_status"].startswith("config_error:")
    with pytest.raises(ValueError, match="SMART_SEARCH_CONFIG_DIR"):
        config._save_config_file({"XAI_API_KEY": "secret"})
