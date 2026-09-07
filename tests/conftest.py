import sys
from pathlib import Path

import pytest

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_FILE_MARKERS = {
    "test_v1_core_cli.py": ("integration",),
    "test_brave_provider.py": ("contract",),
    "test_tavily_provider.py": ("contract",),
    "test_providers_new.py": ("contract",),
    "test_jina_provider.py": ("contract",),
    "test_reader_provider_security.py": ("contract",),
    "test_retrieval_replay.py": ("evaluation",),
    "test_retrieval_benchmark.py": ("evaluation",),
    "test_evaluation_v2.py": ("evaluation",),
    "test_provider_role_contracts.py": ("contract",),
    "test_ranking_and_selection.py": ("unit",),
    "test_config_dir_override.py": ("regression",),
    "test_presets_setup.py": ("regression",),
    "test_security.py": ("regression",),
    "test_regression.py": ("regression",),
    "test_release_workflow.py": ("regression",),
    "test_skill_installer.py": ("regression",),
    "test_sources.py": ("unit",),
}


def pytest_collection_modifyitems(items):
    for item in items:
        names = _FILE_MARKERS.get(Path(str(item.fspath)).name)
        if not names:
            continue
        existing = {marker.name for marker in item.iter_markers()}
        for name in names:
            if name not in existing:
                item.add_marker(getattr(pytest.mark, name))


@pytest.fixture(autouse=True)
def isolate_smart_search_config(monkeypatch, tmp_path):
    from smart_search.config import Config

    config = Config()
    monkeypatch.setattr(config, "_config_file", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config_dir_source", "override")
    monkeypatch.setattr(config, "_config_snapshot", None)
    for key in config._CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SMART_SEARCH_CONFIG_DIR", raising=False)
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
