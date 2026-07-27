import json

import pytest

from smart_search import cli, operations_service, service, service_support
from smart_search import search_service
from smart_search.cli_render import _format_doctor_markdown
from smart_search.logger import logger
from smart_search.utils import PromptConfigurationError


def _reset_model_config(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(service.config, "_config_file", config_file)
    monkeypatch.setattr(service.config, "_config_snapshot", None)
    monkeypatch.setattr(service.config, "_cached_model", None)
    for key in (
        "SMART_SEARCH_MODEL_ROUTES",
        "SMART_SEARCH_MINIMUM_PROFILE",
        "XAI_API_KEY",
        "OPENAI_COMPATIBLE_API_URL",
        "OPENAI_COMPATIBLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    return config_file


def test_model_routes_are_ordered_persisted_and_masked(monkeypatch, tmp_path):
    config_file = _reset_model_config(monkeypatch, tmp_path)

    first = service.model_add(
        "primary",
        "openai-compatible",
        "https://primary.example/v1",
        "primary-secret",
        "primary-model",
    )
    second = service.model_add(
        "backup",
        "openai-compatible",
        "https://backup.example/v1",
        "backup-secret",
        "backup-model",
        stream=True,
    )

    assert first["ok"] is True
    assert [route["id"] for route in second["routes"]] == ["primary", "backup"]
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    assert [route["id"] for route in raw["SMART_SEARCH_MODEL_ROUTES"]] == ["primary", "backup"]
    assert raw["SMART_SEARCH_MODEL_ROUTES"][0]["api_key"] == "primary-secret"

    listed = service.model_list()
    assert listed["current_route_id"] == "primary"
    assert listed["routes"][1]["stream"] is True
    assert "primary-secret" not in json.dumps(listed, ensure_ascii=False)
    assert "backup-secret" not in json.dumps(service.config_list(), ensure_ascii=False)

    removed = service.model_remove("primary")
    assert removed["ok"] is True
    assert [route["id"] for route in removed["routes"]] == ["backup"]


def test_direct_json_model_routes_are_discoverable_and_invalid_entries_fail_closed(monkeypatch, tmp_path):
    config_file = _reset_model_config(monkeypatch, tmp_path)
    config_file.write_text(
        json.dumps(
            {
                "SMART_SEARCH_MODEL_ROUTES": [
                    {
                        "id": "json-primary",
                        "provider": "openai-compatible",
                        "api_url": "https://json.example/v1",
                        "api_key": "json-secret",
                        "model": "json-model",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    listed = service.model_list()
    assert listed["routes"][0]["id"] == "json-primary"
    assert "json-secret" not in json.dumps(listed, ensure_ascii=False)

    config_file.write_text(
        json.dumps(
            {
                "SMART_SEARCH_MODEL_ROUTES": [
                    {"id": "invalid", "provider": "openai-compatible", "api_url": "https://invalid.example/v1"}
                ]
            }
        ),
        encoding="utf-8",
    )
    service.config.refresh()
    invalid = service.model_list()
    assert invalid["ok"] is False
    assert invalid["error_type"] == "config_error"


def test_model_command_supports_add_list_current_remove(monkeypatch):
    parser = cli.build_parser()
    add_args = parser.parse_args(
        [
            "model",
            "add",
            "--id",
            "backup",
            "--provider",
            "openai-compatible",
            "--api-url",
            "https://backup.example/v1",
            "--api-key",
            "secret",
            "--model",
            "backup-model",
            "--stream",
        ]
    )
    assert add_args.model_command == "add"
    assert add_args.route_id == "backup"
    assert add_args.stream is True
    assert parser.parse_args(["model", "list"]).model_command == "list"
    assert parser.parse_args(["model", "current"]).model_command == "current"
    assert parser.parse_args(["model", "remove", "backup"]).model_command == "remove"


def test_model_add_dispatches_route_arguments(monkeypatch, capsys):
    captured = {}

    def fake_model_add(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"ok": True, "routes": []}

    monkeypatch.setattr(cli.service, "model_add", fake_model_add)

    assert (
        cli.main(
            [
                "model",
                "add",
                "--id",
                "backup",
                "--api-url",
                "https://backup.example/v1",
                "--api-key",
                "secret",
                "--model",
                "backup-model",
                "--fallback-models",
                "backup-fallback",
                "--stream",
            ]
        )
        == cli.EXIT_OK
    )
    capsys.readouterr()
    assert captured["args"] == (
        "backup",
        "openai-compatible",
        "https://backup.example/v1",
        "secret",
        "backup-model",
    )
    assert captured["kwargs"] == {"tools": "", "stream": True, "fallback_models": "backup-fallback"}


@pytest.mark.asyncio
async def test_doctor_keeps_diagnostics_for_routes_using_the_same_provider(
    monkeypatch,
    tmp_path,
):
    """
    /*
     * ==============================================================================
     * 步骤1：验证同 provider 多路由诊断
     * ==============================================================================
     * 目标：确保 doctor 按 route ID 保留结果，且首选诊断对应第一条路由。
     * 数据源：两条使用 openai-compatible 的独立模型路由。
     * 操作：
     * 1) 模拟第一条超时、第二条成功。
     * 2) 核对诊断顺序、路由身份、首选结果和秘密脱敏。
     * ==============================================================================
    */
    """
    logger.info("步骤1开始：验证同 provider 多路由诊断")
    _reset_model_config(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "SMART_SEARCH_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "id": "primary",
                    "provider": "openai-compatible",
                    "api_url": "https://primary.example/v1",
                    "api_key": "primary-secret",
                    "model": "primary-model",
                },
                {
                    "id": "backup",
                    "provider": "openai-compatible",
                    "api_url": "https://backup.example/v1",
                    "api_key": "backup-secret",
                    "model": "backup-model",
                },
            ]
        ),
    )
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    seen_routes = []

    async def fake_probe(provider_config):
        seen_routes.append(
            (
                provider_config["route_id"],
                provider_config["provider"],
                provider_config["api_url"],
                provider_config["api_key"],
            )
        )
        if provider_config["route_id"] == "primary":
            return {"status": "timeout", "message": "primary timeout"}
        return {"status": "ok", "message": "backup ok"}

    monkeypatch.setattr(operations_service, "_safe_test_main_provider_connection", fake_probe)

    result = await service.doctor()

    assert seen_routes == [
        ("primary", "openai-compatible", "https://primary.example/v1", "primary-secret"),
        ("backup", "openai-compatible", "https://backup.example/v1", "backup-secret"),
    ]
    assert list(result["main_search_connection_tests"]) == ["primary", "backup"]
    assert result["main_search_connection_tests"]["primary"] == {
        "status": "timeout",
        "message": "primary timeout",
        "route_id": "primary",
        "provider": "openai-compatible",
    }
    assert result["main_search_connection_tests"]["backup"]["status"] == "ok"
    assert result["primary_connection_test"] == result["main_search_connection_tests"]["primary"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "primary-secret" not in serialized
    assert "backup-secret" not in serialized
    markdown = _format_doctor_markdown(result)
    assert "| Route | Provider | Status | Latency | Message |" in markdown
    assert "| primary | openai-compatible | TIMEOUT |" in markdown
    assert "| backup | openai-compatible | OK |" in markdown
    logger.info("步骤1结束：同 provider 多路由诊断验证完成")


@pytest.mark.asyncio
async def test_search_uses_ordered_model_routes_and_records_route_fallback(monkeypatch):
    service_support.reset_runtime_breakers()
    monkeypatch.setenv(
        "SMART_SEARCH_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "id": "primary",
                    "provider": "openai-compatible",
                    "api_url": "https://primary.example/v1",
                    "api_key": "primary-secret",
                    "model": "primary-model",
                },
                {
                    "id": "backup",
                    "provider": "openai-compatible",
                    "api_url": "https://backup.example/v1",
                    "api_key": "backup-secret",
                    "model": "backup-model",
                },
            ]
        ),
    )
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    seen_urls = []

    async def fake_search(self, query, platform="", ctx=None):
        seen_urls.append(self.api_url)
        if self.api_url.startswith("https://primary"):
            return ""
        return "Backup answer."

    monkeypatch.setattr(search_service.OpenAICompatibleSearchProvider, "search", fake_search)

    result = await service.search("ordered route test", providers="openai-compatible")

    assert result["ok"] is True
    assert seen_urls == ["https://primary.example/v1", "https://backup.example/v1"]
    assert result["model_route_id"] == "backup"
    assert result["model_route_fallback_used"] is True
    assert result["routing_decision"]["main_search_route_chain"] == ["primary", "backup"]
    assert result["routing_decision"]["selected_route_id"] == "backup"
    attempts = [attempt for attempt in result["provider_attempts"] if attempt["capability"] == "main_search"]
    assert attempts[0]["route_id"] == "primary"
    assert attempts[1]["route_id"] == "backup"
    assert attempts[1]["fallback_from_route"] == "primary"
    assert "primary-secret" not in json.dumps(result, ensure_ascii=False)
    assert "backup-secret" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_search_does_not_mask_prompt_configuration_error_with_backup(monkeypatch):
    service_support.reset_runtime_breakers()
    monkeypatch.setenv(
        "SMART_SEARCH_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "id": "primary",
                    "provider": "openai-compatible",
                    "api_url": "https://primary.example/v1",
                    "api_key": "primary-secret",
                    "model": "primary-model",
                },
                {
                    "id": "backup",
                    "provider": "openai-compatible",
                    "api_url": "https://backup.example/v1",
                    "api_key": "backup-secret",
                    "model": "backup-model",
                },
            ]
        ),
    )
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    calls = []

    async def fail_with_prompt_error(self, query, platform="", ctx=None):
        calls.append(self.api_url)
        raise PromptConfigurationError("invalid search prompt")

    monkeypatch.setattr(search_service.OpenAICompatibleSearchProvider, "search", fail_with_prompt_error)

    result = await service.search("configuration error test", providers="openai-compatible")

    assert result["ok"] is False
    assert result["error_type"] == "config_error"
    assert calls == ["https://primary.example/v1"]
    assert len(result["provider_attempts"]) == 1
    assert result["provider_attempts"][0]["route_id"] == "primary"
