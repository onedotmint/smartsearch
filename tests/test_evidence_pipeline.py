import time

import pytest

from smart_search import service
from smart_search import research_service, service_support
from smart_search.evidence import CapabilityPlan, EvidenceBundle
from smart_search.logger import logger


def test_capability_plan_keeps_command_dependencies_and_budget_aliases():
    """
    /*
     * ==============================================================================
     * 步骤1：校验 CapabilityPlan 字段
     * ==============================================================================
     * 目标：保证命令依赖、预算和 synthesis 开关使用同一内部对象。
     * 数据源：显式构造的 capability plan。
     * 操作：验证 tuple 输入、重复 capability 和 limit 别名的归一化。
     * ==============================================================================
     */
    """
    logger.info("开始测试 CapabilityPlan 字段")
    plan = CapabilityPlan(
        command="research",
        required_capabilities=("web_fetch", "web_fetch"),
        optional_capabilities=("web_search", "web_fetch"),
        max_provider_attempts=20,
        max_fetches=8,
        budget="standard",
        allow_synthesis=True,
    )

    assert plan.required_capabilities == ("web_fetch",)
    assert plan.optional_capabilities == ("web_search",)
    assert plan.provider_attempt_limit == 20
    assert plan.fetch_limit == 8
    assert plan.to_dict()["allow_synthesis"] is True
    logger.info("CapabilityPlan 字段测试完成")


def test_evidence_bundle_keeps_candidates_out_of_citations():
    """
    /*
     * ==============================================================================
     * 步骤2：校验证据分层
     * ==============================================================================
     * 目标：候选来源只能作为 discovery，正文读取后才能生成 citation。
     * 数据源：一个 search candidate 和一个 fetched page。
     * 操作：分别写入两个 stage，检查 verified、sources 和 citations。
     * ==============================================================================
     */
    """
    logger.info("开始测试证据分层")
    bundle = EvidenceBundle()
    bundle.add_discovery_candidates(
        [{"url": "https://candidate.example", "title": "Candidate", "provider": "search"}]
    )
    bundle.add_fetched_evidence(
        [{"url": "https://fetched.example", "title": "Fetched", "provider": "reader", "content": "body"}]
    )
    snapshot = bundle.to_dict()

    assert snapshot["discovery_candidates"][0]["verified"] is False
    assert snapshot["fetched_evidence"][0]["verified"] is True
    assert snapshot["citations"] == [
        {"url": "https://fetched.example", "title": "Fetched", "provider": "reader"}
    ]
    assert "https://candidate.example" not in {item["url"] for item in snapshot["citations"]}
    logger.info("证据分层测试完成")


def test_evidence_bundle_deduplicates_non_http_urls_and_empty_content():
    """
    /*
     * ==============================================================================
     * 步骤3：校验证据去重和空正文过滤
     * ==============================================================================
     * 目标：伪 URL 和正文缺失结果也遵守统一 evidence 边界。
     * 数据源：重复的 Context7 候选、重复的 fetched item 和空正文 item。
     * 操作：
     * 1) 用任意非空 URL 去重 discovery candidate。
     * 2) 过滤空正文，并按 URL 去重 fetched evidence。
     * ==============================================================================
     */
    """
    logger.info("开始测试证据去重和空正文过滤")
    bundle = EvidenceBundle()
    bundle.add_discovery_candidates(
        [
            {"url": "context7:react", "title": "React", "provider": "context7"},
            {"url": "context7:react", "title": "React duplicate", "provider": "context7"},
        ]
    )
    bundle.add_fetched_evidence(
        [
            {"url": "context7:react", "title": "React", "provider": "context7", "content": "docs"},
            {"url": "context7:react", "title": "React duplicate", "provider": "context7", "content": "docs"},
            {"url": "https://empty.example", "provider": "reader", "content": "  "},
        ]
    )

    assert len(bundle.discovery_candidates) == 1
    assert len(bundle.fetched_evidence) == 1
    assert len(bundle.citations) == 1
    logger.info("证据去重和空正文过滤测试完成")


@pytest.mark.asyncio
async def test_research_synthesis_failure_preserves_evidence(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤4：校验 synthesis 失败保护
     * ==============================================================================
     * 目标：synthesis 异常不能丢失 fetched evidence，也不能重跑 provider。
     * 数据源：mock web discovery、mock fetch 和抛错的 synthesis。
     * 操作：执行 research，检查 evidence、citation、错误字段和 provider 次数。
     * ==============================================================================
     */
    """
    logger.info("开始测试 synthesis 失败保护")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-secret")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    calls = {"search": 0, "fetch": 0}

    async def fake_web_search(query, count=5, providers="auto", fallback="auto"):
        calls["search"] += 1
        return (
            [{"url": "https://evidence.example/source", "title": "Source", "provider": "tavily"}],
            [service_support._attempt("web_search", "tavily", "ok", time.time(), result_count=1)],
        )

    async def fake_fetch(url, fallback="auto", preferred_order=None):
        calls["fetch"] += 1
        return (
            {"ok": True, "url": url, "provider": "jina", "content": "fetched body"},
            [service_support._attempt("web_fetch", "jina", "ok", time.time(), result_count=1)],
        )

    def fail_synthesis(*args, **kwargs):
        raise RuntimeError("synthesis provider unavailable")

    monkeypatch.setattr(research_service, "_run_web_search_fallback", fake_web_search)
    monkeypatch.setattr(research_service, "_run_web_fetch_fallback", fake_fetch)
    monkeypatch.setattr(research_service, "_evidence_only_synthesis", fail_synthesis)

    result = await service.research("today AI news", evidence_dir=str(tmp_path), fallback="auto")

    assert calls == {"search": 1, "fetch": 1}
    assert result["evidence_items"][0]["content"] == "fetched body"
    assert result["citations"] == [
        {"url": "https://evidence.example/source", "title": "Source", "provider": "jina"}
    ]
    assert result["synthesis_error"] == "synthesis provider unavailable"
    assert result["degraded"] is True
    assert "synthesis failed: synthesis provider unavailable" in result["degraded_reason"]
    assert result["evidence_bundle"]["degraded"] is True
    assert result["gap_check"]["status"] == "degraded"
    logger.info("synthesis 失败保护测试完成")


@pytest.mark.asyncio
async def test_research_without_explicit_evidence_dir_does_not_persist_artifacts(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤5：校验默认 artifact 边界
     * ==============================================================================
     * 目标：没有显式 evidence_dir 或持久化开关时，research 只返回内存证据。
     * 数据源：默认 evidence 目录、mock fetch provider。
     * 操作：替换默认目录，执行已知 URL research，确认目录不会被创建。
     * ==============================================================================
     */
    """
    logger.info("开始测试默认 artifact 边界")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    default_root = tmp_path / "generated-evidence"
    monkeypatch.setattr(research_service, "_default_evidence_dir", lambda query: str(default_root))

    async def fake_fetch(url, fallback="auto", preferred_order=None):
        return (
            {"ok": True, "url": url, "provider": "jina", "content": "known URL body"},
            [service_support._attempt("web_fetch", "jina", "ok", time.time(), result_count=1)],
        )

    monkeypatch.setattr(research_service, "_run_web_fetch_fallback", fake_fetch)

    result = await service.research("https://example.com/source", fallback="off")

    assert result["artifacts_persisted"] is False
    assert result["evidence_items"][0]["content"] == "known URL body"
    assert not default_root.exists()
    logger.info("默认 artifact 边界测试完成")
