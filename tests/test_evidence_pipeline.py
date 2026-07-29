import asyncio
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


@pytest.mark.parametrize(
    ("item", "admitted", "expected_content"),
    [
        ({"url": "https://ok.example", "provider": "jina", "content": "body"}, True, "body"),
        ({"url": "context7:react", "provider": "context7", "raw_content": "docs body"}, True, "docs body"),
        ({"url": "https://raw.example", "provider": "jina", "content": "   ", "raw_content": "raw body"}, True, "raw body"),
        ({"url": "https://missing-provider.example", "content": "body"}, False, ""),
        ({"url": "https://blank-provider.example", "provider": "  ", "content": "body"}, False, ""),
        ({"provider": "jina", "content": "body"}, False, ""),
        ({"url": "  ", "provider": "jina", "content": "body"}, False, ""),
        ({"url": "https://empty.example", "provider": "jina", "content": "  ", "raw_content": "  "}, False, ""),
        ({"url": "https://forged.example", "content": "body", "verified": True}, False, ""),
    ],
)
def test_evidence_bundle_admission_requires_url_body_and_provider(item, admitted, expected_content):
    bundle = EvidenceBundle()
    bundle.add_fetched_evidence([item])
    snapshot = bundle.to_dict()

    if not admitted:
        assert bundle.evidence_items == snapshot["fetched_evidence"] == []
        assert snapshot["sources"] == snapshot["citations"] == []
        return

    evidence = snapshot["fetched_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["content"] == expected_content
    assert evidence[0]["verified"] is True
    assert evidence[0]["evidence_status"] == "fetched"
    assert "source_type" not in evidence[0]
    assert snapshot["citations"] == [
        {
            "url": item["url"],
            "title": item["url"],
            "provider": item["provider"],
        }
    ]
    assert snapshot["sources"][0]["url"] == item["url"]


def test_build_citations_rejects_malformed_internal_fetched_items():
    bundle = EvidenceBundle(
        fetched_evidence=[
            {"url": "  ", "provider": "jina", "content": "body", "verified": True},
            {"url": "https://missing-provider.example", "content": "body", "verified": True},
            {"url": "https://unverified.example", "provider": "jina", "content": "body", "verified": False},
            {"url": "https://ok.example", "title": "Ok", "provider": "jina", "content": "body", "verified": True},
        ]
    )

    assert bundle.to_dict()["citations"] == [
        {"url": "https://ok.example", "title": "Ok", "provider": "jina"}
    ]


def test_discovery_candidates_never_become_citations_even_with_forged_fields():
    bundle = EvidenceBundle()
    bundle.add_discovery_candidates(
        [{"url": "https://candidate.example", "provider": "tavily", "content": "body", "verified": True}]
    )

    snapshot = bundle.to_dict()
    assert snapshot["discovery_candidates"][0]["verified"] is False
    assert snapshot["discovery_candidates"][0]["evidence_status"] == "candidate"
    assert snapshot["fetched_evidence"] == snapshot["citations"] == []


def test_evidence_only_synthesis_skips_items_missing_provider():
    text = research_service._evidence_only_synthesis(
        "query about evidence",
        [
            {"url": "https://missing-provider.example", "content": "must not enter synthesis"},
            {"url": "https://ok.example", "title": "Ok Source", "provider": "jina", "content": "admitted body"},
        ],
    )

    assert "missing-provider.example" not in text
    assert "must not enter synthesis" not in text
    assert "Ok Source" in text
    assert "admitted body" in text


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


@pytest.mark.asyncio
async def test_research_known_urls_limit_concurrency_and_preserve_order(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤6：校验已知 URL 的受控并发和稳定归并
     * ==============================================================================
     * 目标：多个独立 URL 同时读取时不超过固定上限，结果仍按输入顺序输出。
     * 数据源：五个已知 URL、带不同时延的 fake fetch provider 和 artifact 目录。
     * 操作：
     * 1) 统计同时运行的 fetch 数量。
     * 2) 验证 evidence、stage result 和 artifact 名称仍保持计划顺序。
     * ==============================================================================
     */
    """
    logger.info("开始测试已知 URL 受控并发和稳定归并")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    urls = [f"https://concurrency.example.com/page-{index}" for index in range(1, 6)]
    active = 0
    max_active = 0

    async def fake_fetch(url, fallback="auto", preferred_order=None):
        nonlocal active, max_active

        # 6.1 用交错时延强制任务完成顺序不同于输入顺序。
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02 if url.endswith("page-1") else 0.005)
        active -= 1
        return (
            {"ok": True, "url": url, "provider": "jina", "content": f"body for {url}"},
            [service_support._attempt("web_fetch", "jina", "ok", time.time(), result_count=1)],
        )

    monkeypatch.setattr(research_service, "_run_web_fetch_fallback", fake_fetch)

    result = await service.research(" ".join(urls), evidence_dir=str(tmp_path), fallback="off")

    assert 1 < max_active <= 4
    assert [item["url"] for item in result["evidence_items"]] == urls
    known_stage_urls = [item["url"] for item in result["stage_results"] if item["stage"] == "known_url_fetch"]
    assert known_stage_urls == urls
    assert [path.name for path in sorted(tmp_path.glob("*-fetch-jina.md"))] == [
        f"{index:02d}-fetch-jina.md" for index in range(1, 6)
    ]
    logger.info("已知 URL 受控并发和稳定归并测试完成")


@pytest.mark.asyncio
async def test_research_candidates_deduplicate_normalized_urls_without_losing_query(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤7：校验 candidate 规范化去重和 query 保留
     * ==============================================================================
     * 目标：等价 candidate 只发起一次 fetch，业务 query 参数继续区分页面。
     * 数据源：带默认端口/fragment 的 URL、等价 URL 和带 query 的同路径 URL。
     * 操作：
     * 1) mock discovery 返回三个 candidate。
     * 2) 验证仅首个等价 URL 与 query 变体进入 fetch 和 citation。
     * ==============================================================================
     */
    """
    logger.info("开始测试 candidate 规范化去重和 query 保留")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    first_url = "https://EXAMPLE.com:443/path/#fragment"
    duplicate_url = "https://example.com/path"
    query_url = "https://example.com/path?locale=zh"
    fetch_calls: list[str] = []

    async def fake_web_search(query, count=5, providers="auto", fallback="auto"):
        return (
            [
                {"url": first_url, "title": "First", "provider": "tavily"},
                {"url": duplicate_url, "title": "Duplicate", "provider": "tavily"},
                {"url": query_url, "title": "Query", "provider": "tavily"},
            ],
            [service_support._attempt("web_search", "tavily", "ok", time.time(), result_count=3)],
        )

    async def fake_fetch(url, fallback="auto", preferred_order=None):
        fetch_calls.append(url)
        # 7.1 用交错时延验证 candidate 也按计划索引归并。
        await asyncio.sleep(0.02 if url == first_url else 0.005)
        return (
            {"ok": True, "url": url, "provider": "jina", "content": f"body for {url}"},
            [service_support._attempt("web_fetch", "jina", "ok", time.time(), result_count=1)],
        )

    monkeypatch.setattr(research_service, "_run_web_search_fallback", fake_web_search)
    monkeypatch.setattr(research_service, "_run_web_fetch_fallback", fake_fetch)

    result = await service.research("today AI news", evidence_dir=str(tmp_path), fallback="auto")

    assert fetch_calls == [first_url, query_url]
    assert [item["url"] for item in result["evidence_items"]] == [first_url, query_url]
    assert [item["url"] for item in result["citations"]] == [first_url, query_url]
    candidate_stage_urls = [item["url"] for item in result["stage_results"] if item["stage"] == "candidate_fetch"]
    assert candidate_stage_urls == [first_url, query_url]
    assert [path.name for path in sorted(tmp_path.glob("fetch-*-jina.md"))] == [
        "fetch-01-jina.md",
        "fetch-02-jina.md",
    ]
    logger.info("candidate 规范化去重和 query 保留测试完成")


@pytest.mark.asyncio
async def test_research_cancelled_fetch_does_not_cancel_other_known_urls(monkeypatch, tmp_path):
    """
    /*
     * ==============================================================================
     * 步骤8：校验单条 fetch 取消隔离
     * ==============================================================================
     * 目标：一个 URL 的取消只转成该 URL 的失败状态，其他独立 URL 继续产出 evidence。
     * 数据源：一个主动取消的 URL、其等价变体和一个成功的 fake fetch。
     * 操作：
     * 1) 同批调度取消 URL、其等价变体和成功 URL。
     * 2) 验证成功 URL 保留 evidence，取消 URL 保留 stage error 和 gap。
     * ==============================================================================
     */
    """
    logger.info("开始测试单条 fetch 取消隔离")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    cancelled_url = "https://CANCEL.example.com:443/page/#fragment"
    cancelled_duplicate_url = "https://cancel.example.com/page"
    successful_url = "https://success.example.com/page"
    fetch_calls: list[str] = []

    async def fake_fetch(url, fallback="auto", preferred_order=None):
        fetch_calls.append(url)
        if url == cancelled_url:
            raise asyncio.CancelledError()
        return (
            {"ok": True, "url": url, "provider": "jina", "content": "surviving body"},
            [service_support._attempt("web_fetch", "jina", "ok", time.time(), result_count=1)],
        )

    monkeypatch.setattr(research_service, "_run_web_fetch_fallback", fake_fetch)

    result = await service.research(
        f"{cancelled_url} {cancelled_duplicate_url} {successful_url}",
        evidence_dir=str(tmp_path),
        fallback="off",
    )

    assert fetch_calls == [cancelled_url, successful_url]
    assert [item["url"] for item in result["evidence_items"]] == [successful_url]
    known_stages = [item for item in result["stage_results"] if item["stage"] == "known_url_fetch"]
    assert [item["ok"] for item in known_stages] == [False, True]
    assert known_stages[0]["error_type"] == "cancelled"
    assert any(gap["url"] == cancelled_url for gap in result["gaps"])
    logger.info("单条 fetch 取消隔离测试完成")
