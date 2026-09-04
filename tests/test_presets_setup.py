from __future__ import annotations

import asyncio
import json

import pytest

from smart_search.config import Config
from smart_search.core.retrieval import RetrievalOutcome


def test_default_mode_is_balanced_and_is_securely_persisted(monkeypatch, tmp_path):
    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    assert config.default_mode == "balanced"
    config.set_config_value("SMART_SEARCH_DEFAULT_MODE", "FAST")
    assert config.default_mode == "fast"
    assert config.get_saved_config(masked=False)["SMART_SEARCH_DEFAULT_MODE"] == "fast"
    with pytest.raises(ValueError):
        config.set_config_value("SMART_SEARCH_DEFAULT_MODE", "slow")


def test_search_mode_maps_to_fixed_policy(monkeypatch):
    from smart_search import cli

    seen = []

    async def fake_search(query, policy, *, registry=None):
        seen.append(policy)
        return RetrievalOutcome(providers=("brave",))

    monkeypatch.setattr(cli, "core_search", fake_search)
    for mode, expected in (("fast", (3, False)), ("balanced", (5, True)), ("research", (10, True))):
        payload = asyncio.run(cli.run_search("query", mode=mode))
        assert payload["status"] == "complete"
        assert (seen[-1].max_results, seen[-1].rerank) == expected


def test_setup_saves_multiple_selected_keys_without_transport(monkeypatch, tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    answers = iter(("1,3", "brave-secret", "tavily-secret"))
    payload = cli.run_setup(
        mode="balanced",
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: next(answers),
        config_obj=config,
    )
    rendered = json.dumps(payload)
    assert payload["status"] == "complete"
    assert payload["data"]["providers"] == ["brave", "tavily"]
    assert "secret" not in rendered
    saved = config.get_saved_config(masked=False)
    assert saved["BRAVE_API_KEY"] == "brave-secret"
    assert saved["TAVILY_API_KEY"] == "tavily-secret"
    assert saved["SMART_SEARCH_DEFAULT_MODE"] == "balanced"


def test_setup_does_not_prompt_for_environment_owned_key(monkeypatch, tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    monkeypatch.setenv("BRAVE_API_KEY", "environment-secret")
    prompted = []
    payload = cli.run_setup(
        input_fn=lambda _prompt: "1",
        secret_fn=lambda _prompt: prompted.append(True),
        config_obj=config,
    )
    assert payload["status"] == "complete"
    assert not prompted
    assert "BRAVE_API_KEY" not in config.get_saved_config(masked=False)
    assert payload["data"]["readiness"]["discovery"]["brave"]["source"] == "environment"


def test_setup_invalid_selection_does_not_write_mode(tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    payload = cli.run_setup(
        mode="fast", input_fn=lambda _prompt: "not-a-provider", config_obj=config
    )
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert not config.get_saved_config(masked=False)


def test_parser_exposes_modes_not_retired_policy_flags():
    from smart_search.cli import build_parser

    search = build_parser().parse_args(["search", "query", "--mode", "research"])
    assert search.mode == "research"
    with pytest.raises(ValueError):
        build_parser().parse_args(["search", "query", "--max-results", "2"])
    setup = build_parser().parse_args(["setup", "--mode", "fast"])
    assert setup.mode == "fast"


def test_setup_unavailable_input_returns_stable_invalid_argument(tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None

    def unavailable(_prompt):
        raise OSError("stdin unavailable")

    payload = cli.run_setup(input_fn=unavailable, config_obj=config)
    assert payload["status"] == "failed"
    assert payload["error"] == {
        "code": "INVALID_ARGUMENT",
        "message": "setup input was cancelled or invalid",
    }
    assert not config.config_file.exists()


def test_setup_selection_shows_non_secret_existing_readiness(tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    config._save_config_file({
        "BRAVE_API_KEY": "saved-brave-secret",
        "TAVILY_API_KEY": "saved-tavily-secret",
        "TAVILY_ENABLED": "false",
    })
    prompts = []

    payload = cli.run_setup(
        mode="balanced",
        input_fn=lambda prompt: prompts.append(prompt) or "",
        config_obj=config,
    )

    assert payload["status"] == "complete"
    assert len(prompts) == 1
    prompt = prompts[0]
    assert "Brave (configured, ready)" in prompt
    assert "Exa (not configured)" in prompt
    assert "Tavily (configured, disabled)" in prompt
    assert "saved-brave-secret" not in prompt
    assert "saved-tavily-secret" not in prompt


def test_setup_cancellation_during_secret_collection_does_not_partially_persist(tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    secret_calls = 0

    def secret(_prompt):
        nonlocal secret_calls
        secret_calls += 1
        if secret_calls == 1:
            return "new-brave-secret"
        raise KeyboardInterrupt

    payload = cli.run_setup(
        mode="fast",
        input_fn=lambda _prompt: "1,2",
        secret_fn=secret,
        config_obj=config,
    )

    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert not config.config_file.exists()


def test_setup_persistence_failure_preserves_previous_file(monkeypatch, tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    config._save_config_file({
        "BRAVE_API_KEY": "old-brave-secret",
        "SMART_SEARCH_DEFAULT_MODE": "fast",
        "EXA_BASE_URL": "https://exa.example",
    })
    previous = config.config_file.read_bytes()
    monkeypatch.setattr("smart_search.config.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))

    payload = cli.run_setup(
        mode="balanced",
        input_fn=lambda _prompt: "",
        config_obj=config,
    )

    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert config.config_file.read_bytes() == previous


def test_setup_uses_saved_mode_unless_explicit_mode_is_given(tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    config.set_config_value("SMART_SEARCH_DEFAULT_MODE", "fast")

    saved = cli.run_setup(input_fn=lambda _prompt: "", config_obj=config)
    explicit = cli.run_setup(mode="research", input_fn=lambda _prompt: "", config_obj=config)

    assert saved["data"]["mode"] == "fast"
    assert explicit["data"]["mode"] == "research"
    assert config.get_saved_config(masked=False)["SMART_SEARCH_DEFAULT_MODE"] == "research"


def test_setup_does_not_overwrite_environment_owned_default_mode(monkeypatch, tmp_path):
    from smart_search import cli

    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None
    config._save_config_file({"SMART_SEARCH_DEFAULT_MODE": "balanced"})
    monkeypatch.setenv("SMART_SEARCH_DEFAULT_MODE", "fast")

    payload = cli.run_setup(mode="research", input_fn=lambda _prompt: "", config_obj=config)

    assert payload["status"] == "complete"
    assert payload["data"]["mode"] == "research"
    assert config.get_saved_config(masked=False)["SMART_SEARCH_DEFAULT_MODE"] == "balanced"
    assert config.default_mode == "fast"


def test_setup_constructs_no_registry_or_http_transport(monkeypatch, tmp_path):
    from smart_search import cli
    from smart_search.providers import registry
    import httpx

    def unexpected(*_args, **_kwargs):
        raise AssertionError("setup must not construct providers or transport")

    monkeypatch.setattr(registry, "Registry", unexpected)
    monkeypatch.setattr(httpx, "AsyncClient", unexpected)
    config = Config()
    config._config_file = tmp_path / "config.json"
    config._config_dir_source = "override"
    config._config_snapshot = None

    payload = cli.run_setup(input_fn=lambda _prompt: "", config_obj=config)

    assert payload["status"] == "complete"
