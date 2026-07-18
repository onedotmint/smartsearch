import json

import pytest

from smart_search import cli, service
from smart_search.cli_contract import build_json_result
from smart_search.utils import PromptConfigurationError, get_prompt, prompt_overrides


def test_json_contract_preserves_legacy_error_and_adds_structured_error():
    result = build_json_result(
        "fetch",
        {
            "ok": False,
            "error_type": "network_error",
            "error": "Bearer sk-test-secret upstream timeout",
            "OPENAI_COMPATIBLE_API_KEY": "sk-test-secret",
        },
        secrets=["sk-test-secret"],
    )

    assert result["schema_version"] == "1"
    assert result["command"] == "fetch"
    assert result["error"] == "Bearer [REDACTED] upstream timeout"
    assert result["error_code"] == "PROVIDER_UNAVAILABLE"
    assert result["error_detail"]["code"] == "PROVIDER_UNAVAILABLE"
    assert result["data"]["error"]["message"] == "Bearer [REDACTED] upstream timeout"
    assert "sk-test-secret" not in json.dumps(result)


def test_minimum_profile_modes_keep_standard_fail_closed():
    status = {
        "main_search": {"ok": False},
        "web_search": {"ok": True},
        "docs_search": {"ok": False},
        "web_fetch": {"ok": False},
        "site_map": {"ok": True},
    }

    standard = service._minimum_profile_result("standard", status)
    lite = service._minimum_profile_result("lite", status)
    full = service._minimum_profile_result(
        "full",
        {
            **status,
            "main_search": {"ok": True},
            "docs_search": {"ok": True},
            "web_fetch": {"ok": True},
        },
    )

    assert standard["ok"] is False
    assert standard["missing_required"] == ["main_search", "docs_search", "web_fetch"]
    assert lite["ok"] is True
    assert lite["degraded"] is True
    assert full["ok"] is True


def test_prompt_override_loads_local_utf8_file_and_rejects_remote(tmp_path):
    prompt_file = tmp_path / "search.md"
    prompt_file.write_text("只返回来源候选。", encoding="utf-8")

    with prompt_overrides(search_prompt_file=str(prompt_file)):
        assert get_prompt("search") == "只返回来源候选。"

    with prompt_overrides(search_prompt_file="https://example.com/prompt.md"):
        with pytest.raises(PromptConfigurationError, match="local UTF-8"):
            get_prompt("search")


def test_research_profile_maps_to_executor_budget(monkeypatch, capsys):
    captured: list[str] = []

    async def fake_research(query, budget="deep", evidence_dir="", fallback="auto"):
        captured.append(budget)
        return {"ok": True, "query": query, "budget": budget}

    monkeypatch.setattr(cli.service, "research", fake_research)

    for profile, expected in (("fast", "quick"), ("balanced", "standard"), ("deep", "deep")):
        assert cli.main(["research", "query", "--profile", profile, "--format", "json"]) == cli.EXIT_OK
        json.loads(capsys.readouterr().out)
        assert captured[-1] == expected


def test_output_file_does_not_overwrite_without_force(tmp_path):
    output = tmp_path / "result.json"
    output.write_text("old", encoding="utf-8")

    with pytest.raises(FileExistsError):
        service.write_output(output, "new")

    service.write_output(output, "new", force=True)
    assert output.read_text(encoding="utf-8") == "new"
