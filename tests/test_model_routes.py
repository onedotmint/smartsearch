import json

import pytest

from smart_search import cli, operations_service, service, service_support
from smart_search import search_service
from smart_search.cli_contract import build_json_result
from smart_search.cli_render import _format_doctor_markdown, _format_model_markdown
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
        "XAI_API_URL",
        "XAI_API_KEY",
        "XAI_MODEL",
        "XAI_TOOLS",
        "OPENAI_COMPATIBLE_API_URL",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_MODEL",
        "OPENAI_COMPATIBLE_STREAM",
        "OPENAI_COMPATIBLE_FALLBACK_MODELS",
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


def test_model_add_migrates_local_legacy_main_search_config(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤1：验证本地旧主搜索配置迁移
     * ==============================================================================
     * 目标：首次 model add 保留两类旧 provider 并按 legacy 顺序追加新 route。
     * 数据源：config.json 中的 XAI_* 与 OPENAI_COMPATIBLE_* 配置。
     * 操作：
     * 1) 写入带 provider 专属字段的旧配置。
     * 2) 核对持久化顺序、字段绑定、旧键保留与输出脱敏。
     * ==============================================================================
    */
    """
    logger.info("步骤1开始：验证本地旧主搜索配置迁移")
    config_file = _reset_model_config(monkeypatch, tmp_path)
    config_file.write_text(
        json.dumps(
            {
                "XAI_API_URL": "https://legacy-xai.example/v1",
                "XAI_API_KEY": "legacy-xai-secret",
                "XAI_MODEL": "legacy-xai-model",
                "XAI_TOOLS": "x_search",
                "OPENAI_COMPATIBLE_API_URL": "https://legacy-openai.example/v1",
                "OPENAI_COMPATIBLE_API_KEY": "legacy-openai-secret",
                "OPENAI_COMPATIBLE_MODEL": "legacy-openai-model",
                "OPENAI_COMPATIBLE_STREAM": "true",
                "OPENAI_COMPATIBLE_FALLBACK_MODELS": "legacy-openai-backup",
                "unrelated_setting": "preserved",
            }
        ),
        encoding="utf-8",
    )

    result = service.model_add(
        "new-route",
        "openai-compatible",
        "https://new-route.example/v1",
        "new-route-secret",
        "new-route-model",
    )

    assert result["ok"] is True
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    routes = raw["SMART_SEARCH_MODEL_ROUTES"]
    assert [route["id"] for route in routes] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "new-route",
    ]
    assert routes[0] == {
        "id": "legacy-xai-responses",
        "provider": "xai-responses",
        "api_url": "https://legacy-xai.example/v1",
        "api_key": "legacy-xai-secret",
        "model": "legacy-xai-model",
        "tools": ["x_search"],
    }
    assert routes[1]["stream"] is True
    assert routes[1]["fallback_models"] == ["legacy-openai-backup"]
    assert raw["XAI_API_KEY"] == "legacy-xai-secret"
    assert raw["OPENAI_COMPATIBLE_API_KEY"] == "legacy-openai-secret"
    assert raw["unrelated_setting"] == "preserved"
    serialized = json.dumps({"result": result, "config": service.config_list()}, ensure_ascii=False)
    assert "legacy-xai-secret" not in serialized
    assert "legacy-openai-secret" not in serialized
    assert "new-route-secret" not in serialized
    logger.info("步骤1结束：本地旧主搜索配置迁移验证完成")


@pytest.mark.parametrize(
    ("file_values", "environment_values", "secret"),
    [
        ({}, {"XAI_API_KEY": "environment-xai-secret"}, "environment-xai-secret"),
        ({"XAI_API_KEY": "file-xai-secret"}, {"XAI_MODEL": "environment-xai-model"}, "file-xai-secret"),
        (
            {
                "OPENAI_COMPATIBLE_API_URL": "https://file-openai.example/v1",
                "OPENAI_COMPATIBLE_API_KEY": "file-openai-secret",
            },
            {"OPENAI_COMPATIBLE_STREAM": "true"},
            "file-openai-secret",
        ),
    ],
)
def test_model_add_does_not_persist_environment_controlled_legacy_config(
    monkeypatch,
    tmp_path,
    file_values,
    environment_values,
    secret,
):
    """
    /*
     * ==============================================================================
     * 步骤2：验证环境控制的旧配置不会落盘
     * ==============================================================================
     * 目标：保护环境管理的凭据与 provider 参数，避免 model add 复制到 config.json。
     * 数据源：文件中的 legacy 配置与同 provider 的环境覆盖。
     * 操作：
     * 1) 模拟密钥或运行参数来自环境变量。
     * 2) 核对命令失败、原文件不变且结果不包含秘密。
     * ==============================================================================
    */
    """
    logger.info("步骤2开始：验证环境控制的旧配置不会落盘")
    config_file = _reset_model_config(monkeypatch, tmp_path)
    config_file.write_text(json.dumps({**file_values, "unrelated_setting": "preserved"}), encoding="utf-8")
    for key, value in environment_values.items():
        monkeypatch.setenv(key, value)
    before = config_file.read_text(encoding="utf-8")

    result = service.model_add(
        "new-route",
        "openai-compatible",
        "https://new-route.example/v1",
        "new-route-secret",
        "new-route-model",
    )

    assert result["ok"] is False
    assert result["error_type"] == "parameter_error"
    assert "controlled by the environment" in result["error"]
    assert config_file.read_text(encoding="utf-8") == before
    assert "SMART_SEARCH_MODEL_ROUTES" not in json.loads(before)
    assert secret not in json.dumps(result, ensure_ascii=False)
    logger.info("步骤2结束：环境控制的旧配置未落盘验证完成")


def test_model_add_keeps_config_unchanged_when_legacy_route_id_conflicts(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤3：验证 legacy route ID 冲突的原子性
     * ==============================================================================
     * 目标：新 route 与生成的 legacy ID 冲突时拒绝整次迁移。
     * 数据源：本地 xAI legacy 配置和冲突的 model add 参数。
     * 操作：
     * 1) 触发 route ID 重复校验。
     * 2) 核对原 config.json 未写入 route list。
     * ==============================================================================
    */
    """
    logger.info("步骤3开始：验证 legacy route ID 冲突原子性")
    config_file = _reset_model_config(monkeypatch, tmp_path)
    config_file.write_text(json.dumps({"XAI_API_KEY": "legacy-xai-secret"}), encoding="utf-8")
    before = config_file.read_text(encoding="utf-8")

    result = service.model_add(
        "legacy-xai-responses",
        "openai-compatible",
        "https://new-route.example/v1",
        "new-route-secret",
        "new-route-model",
    )

    assert result["ok"] is False
    assert result["error_type"] == "parameter_error"
    assert "duplicate id: legacy-xai-responses" in result["error"]
    assert config_file.read_text(encoding="utf-8") == before
    logger.info("步骤3结束：legacy route ID 冲突原子性验证完成")


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


def test_model_route_inspection_redacts_url_credentials(monkeypatch, tmp_path):
    """
    /*
     * ================================================================================
     * 步骤1：验证模型路由展示脱敏
     * ================================================================================
     * 目标：确保 model 与 config 检查结果不泄露 URL userinfo 或敏感查询参数。
     * 数据源：两条含 URL 内嵌凭据的本地模型路由。
     * 操作：
     * 1) 分别覆盖 add、list、current、remove 与 config list 输出。
     * 2) 验证持久化配置仍保留原始 URL 供后续请求使用。
     * ================================================================================
    */
    """
    logger.info("步骤1开始：验证模型路由展示脱敏")
    config_file = _reset_model_config(monkeypatch, tmp_path)
    primary_url = "https://primary-user:primary-password@primary.example/v1?api_key=primary-query-secret&region=cn"
    backup_url = "https://backup-user:backup-password@backup.example/v1?token=backup-query-secret&region=us"

    # 1.1 创建两条路由，覆盖各个模型管理结果的 route 输出。
    added = service.model_add("primary", "openai-compatible", primary_url, "primary-api-secret", "primary-model")
    service.model_add("backup", "openai-compatible", backup_url, "backup-api-secret", "backup-model")
    listed = service.model_list()
    current = service.current_model()
    removed = service.model_remove("primary")
    config_result = service.config_list()

    # 1.2 断言所有展示和 Markdown 渲染都不包含 URL 或 API key 凭据。
    rendered = _format_model_markdown(listed)
    serialized = json.dumps(
        {
            "added": added,
            "listed": listed,
            "current": current,
            "removed": removed,
            "config": config_result,
            "markdown": rendered,
        },
        ensure_ascii=False,
    )
    for secret in (
        "primary-user",
        "primary-password",
        "primary-query-secret",
        "primary-api-secret",
        "backup-user",
        "backup-password",
        "backup-query-secret",
        "backup-api-secret",
    ):
        assert secret not in serialized
    assert "[REDACTED]@primary.example" in serialized
    assert "[REDACTED]@backup.example" in serialized

    # 1.3 持久化内容保持原样，避免展示脱敏误伤真实请求配置。
    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["SMART_SEARCH_MODEL_ROUTES"] == [
        {
            "id": "backup",
            "provider": "openai-compatible",
            "api_url": backup_url,
            "api_key": "backup-api-secret",
            "model": "backup-model",
            "stream": False,
            "fallback_models": [],
        }
    ]
    logger.info("步骤1结束：模型路由展示脱敏验证完成")


@pytest.mark.asyncio
async def test_model_route_url_credentials_are_redacted_in_doctor_and_routing_metadata(monkeypatch, tmp_path):
    """
    /*
     * ================================================================================
     * 步骤2：验证诊断与路由元数据脱敏
     * ================================================================================
     * 目标：确保 doctor、搜索结果和 JSON 契约都不泄露 URL 内嵌凭据。
     * 数据源：环境变量中的独立模型路由和模拟的连接探针、模型请求。
     * 操作：
     * 1) 保留探针和 provider 实际收到的原始 URL。
     * 2) 验证所有对外诊断和路由元数据仅包含脱敏 URL。
     * ================================================================================
    */
    """
    logger.info("步骤2开始：验证诊断与路由元数据脱敏")
    _reset_model_config(monkeypatch, tmp_path)
    service_support.reset_runtime_breakers()
    primary_url = "https://primary-user:primary-password@primary.example/v1?api_key=primary-query-secret&region=cn"
    backup_url = "https://backup-user:backup-password@backup.example/v1?token=backup-query-secret&region=us"
    monkeypatch.setenv(
        "SMART_SEARCH_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "id": "primary",
                    "provider": "openai-compatible",
                    "api_url": primary_url,
                    "api_key": "primary-api-secret",
                    "model": "primary-model",
                },
                {
                    "id": "backup",
                    "provider": "openai-compatible",
                    "api_url": backup_url,
                    "api_key": "backup-api-secret",
                    "model": "backup-model",
                },
            ]
        ),
    )
    monkeypatch.setenv("SMART_SEARCH_MINIMUM_PROFILE", "off")
    probed_urls = []
    searched_urls = []

    async def fake_probe(provider_config):
        # 2.1 模拟 provider 将原始 URL 放入连接错误消息。
        probed_urls.append(provider_config["api_url"])
        return {"status": "error", "message": f"unable to reach {provider_config['api_url']}"}

    async def fake_search(self, query, platform="", ctx=None):
        # 2.2 模拟真实请求读取未脱敏的路由 URL。
        searched_urls.append(self.api_url)
        return "Primary answer."

    monkeypatch.setattr(operations_service, "_safe_test_main_provider_connection", fake_probe)
    monkeypatch.setattr(search_service.OpenAICompatibleSearchProvider, "search", fake_search)

    # 2.3 收集 doctor、搜索服务和 CLI JSON 契约三类对外结果。
    doctor_result = await service.doctor()
    search_result = await service.search("route credential test", providers="openai-compatible")
    cli_result = build_json_result("search", search_result)
    serialized = json.dumps(
        {"doctor": doctor_result, "search": search_result, "cli": cli_result},
        ensure_ascii=False,
    )

    # 2.4 对外结果不得泄露凭据，但内部探针和请求仍使用原始完整 URL。
    for secret in (
        "primary-user",
        "primary-password",
        "primary-query-secret",
        "primary-api-secret",
        "backup-user",
        "backup-password",
        "backup-query-secret",
        "backup-api-secret",
    ):
        assert secret not in serialized
    assert probed_urls == [primary_url, backup_url]
    assert searched_urls == [primary_url]
    assert "[REDACTED]@primary.example" in serialized
    assert "[REDACTED]@backup.example" in serialized
    assert "[REDACTED]@primary.example" in _format_doctor_markdown(doctor_result)
    assert search_result["routing_decision"]["main_search_routes"][0]["api_url"].startswith(
        "https://[REDACTED]@primary.example/v1"
    )
    logger.info("步骤2结束：诊断与路由元数据脱敏验证完成")
