"""Shared capability planning and evidence collection boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .logger import logger


def _copy_items(items: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in (items or []) if isinstance(item, Mapping)]


@dataclass
class CapabilityPlan:
    """
    /*
     * ==============================================================================
     * 步骤1：描述命令能力计划
     * ==============================================================================
     * 目标：把命令依赖、provider/fetch 预算和 synthesis 开关收敛到一个对象。
     * 数据源：命令能力矩阵、minimum profile、RequestContext 预算。
     * 操作：
     * 1) 保存必需和可选 capability，禁止调用方自行拼接依赖。
     * 2) 保存 provider attempt、fetch、budget 和 synthesis 边界。
     * ==============================================================================
     */
    """

    command: str
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    max_provider_attempts: int = 0
    max_fetches: int = 0
    budget: str = ""
    allow_synthesis: bool = False
    source_only: bool = False
    response_mode: str = ""

    def __post_init__(self) -> None:
        self.command = str(self.command or "")
        self.required_capabilities = tuple(dict.fromkeys(str(item) for item in self.required_capabilities if item))
        self.optional_capabilities = tuple(
            item
            for item in dict.fromkeys(str(value) for value in self.optional_capabilities if value)
            if item not in self.required_capabilities
        )
        self.max_provider_attempts = max(0, int(self.max_provider_attempts or 0))
        self.max_fetches = max(0, int(self.max_fetches or 0))
        self.budget = str(self.budget or "")
        self.allow_synthesis = bool(self.allow_synthesis)
        self.source_only = bool(self.source_only)
        self.response_mode = str(self.response_mode or "")

    @property
    def provider_attempt_limit(self) -> int:
        return self.max_provider_attempts

    @property
    def fetch_limit(self) -> int:
        return self.max_fetches

    def to_dict(self) -> dict[str, Any]:
        """
        /*
         * ==============================================================================
         * 步骤2：输出兼容能力计划
         * ==============================================================================
         * 目标：为内部调用和增量观测字段提供稳定字典。
         * 数据源：CapabilityPlan 当前字段。
         * 操作：
         * 1) 输出列表而不是 tuple，保证 JSON 序列化稳定。
         * 2) 同时保留 limit 别名，兼容不同调用方的语义命名。
         * ==============================================================================
         */
        """
        logger.info("开始输出 capability plan: command=%s", self.command)
        result = {
            "command": self.command,
            "required_capabilities": list(self.required_capabilities),
            "optional_capabilities": list(self.optional_capabilities),
            "max_provider_attempts": self.max_provider_attempts,
            "provider_attempt_limit": self.provider_attempt_limit,
            "max_fetches": self.max_fetches,
            "fetch_limit": self.fetch_limit,
            "budget": self.budget,
            "allow_synthesis": self.allow_synthesis,
            "source_only": self.source_only,
            "response_mode": self.response_mode,
        }
        logger.info("capability plan 输出完成: command=%s", self.command)
        return result


@dataclass
class EvidenceBundle:
    """
    /*
     * ==============================================================================
     * 步骤3：维护统一证据包
     * ==============================================================================
     * 目标：隔离 discovery candidate、fetched evidence 和 synthesis 输入。
     * 数据源：搜索候选、fetch/read 内容、provider attempts 和 gap check。
     * 操作：
     * 1) 候选来源只能进入 discovery_candidates，不得直接生成 citation。
     * 2) 只有含正文的 fetched evidence 才能生成 verified citation。
     * 3) 统一保存 sources、gaps、degraded 和 provider attempts，供 flat JSON 适配。
     * ==============================================================================
     */
    """

    discovery_candidates: list[dict[str, Any]] = field(default_factory=list)
    fetched_evidence: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    provider_attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def evidence_items(self) -> list[dict[str, Any]]:
        return self.fetched_evidence

    def add_discovery_candidates(self, items: Iterable[Mapping[str, Any]] | None) -> None:
        """
        /*
         * ==============================================================================
         * 步骤4：记录 discovery 候选
         * ==============================================================================
         * 目标：保留候选来源，但阻断候选到 claim-level evidence 的隐式升级。
         * 数据源：search、docs、web 和 vertical provider 的来源列表。
         * 操作：
         * 1) 复制来源字段，避免后续 stage 修改 provider 原始结果。
         * 2) 标记 candidate/unverified 并按 URL 或稳定内容去重。
         * ==============================================================================
         */
        """
        logger.info("开始记录 discovery candidates")
        for item in _copy_items(items):
            candidate = dict(item)
            candidate["verified"] = False
            candidate["evidence_status"] = "candidate"
            if self._append_unique(self.discovery_candidates, candidate):
                self._append_source(candidate)
        logger.info("discovery candidates 记录完成: count=%s", len(self.discovery_candidates))

    def add_fetched_evidence(self, items: Iterable[Mapping[str, Any]] | None) -> None:
        """
        /*
         * ==============================================================================
         * 步骤5：记录已抓取证据
         * ==============================================================================
         * 目标：只让真实 fetch/read 正文进入 synthesis 和 citation。
         * 数据源：web_fetch、Context7 docs 或其他明确的 read 内容。
         * 操作：
         * 1) 过滤空正文，避免 provider 空结果伪装成证据。
         * 2) 标记 fetched/verified 并同步 source metadata。
         * ==============================================================================
         */
        """
        logger.info("开始记录 fetched evidence")
        for item in _copy_items(items):
            content = str(item.get("content") or item.get("raw_content") or "")
            if not content.strip():
                continue
            evidence = dict(item)
            evidence["content"] = content
            evidence["content_len"] = len(content)
            evidence["verified"] = True
            evidence["evidence_status"] = "fetched"
            if self._append_unique(self.fetched_evidence, evidence):
                self._append_source(evidence)
        self.citations = self._build_citations()
        logger.info(
            "fetched evidence 记录完成: evidence=%s citations=%s",
            len(self.fetched_evidence),
            len(self.citations),
        )

    def add_provider_attempts(self, attempts: Iterable[Mapping[str, Any]] | None) -> None:
        """
        /*
         * ==============================================================================
         * 步骤6：归并 provider attempts
         * ==============================================================================
         * 目标：让所有 stage 的调用观测进入同一个证据边界。
         * 数据源：同 capability fallback 和 provider stage 的 attempts。
         * 操作：复制字典并保持原有顺序，避免改变 fallback 诊断语义。
         * ==============================================================================
         */
        """
        logger.info("开始归并 provider attempts")
        self.provider_attempts.extend(_copy_items(attempts))
        logger.info("provider attempts 归并完成: count=%s", len(self.provider_attempts))

    def add_gap(self, gap: Mapping[str, Any] | str) -> None:
        """
        /*
         * ==============================================================================
         * 步骤8：记录未解决证据缺口
         * ==============================================================================
         * 目标：保留缺口明细，并让 bundle 进入 degraded 状态。
         * 数据源：gap check 的结构化缺口或错误文本。
         * 操作：
         * 1) 复制缺口字段，避免调用方后续修改内部状态。
         * 2) 记录缺口原因并标记当前证据包降级。
         * ==============================================================================
         */
        """
        logger.info("开始记录证据缺口")
        if isinstance(gap, Mapping):
            item = dict(gap)
        else:
            item = {"subquestion_id": "", "reason": str(gap)}
        self.gaps.append(item)
        self.mark_degraded(str(item.get("reason") or "unresolved evidence gap"))
        logger.info("证据缺口记录完成: count=%s", len(self.gaps))

    def mark_degraded(self, reason: str = "") -> None:
        """
        /*
         * ==============================================================================
         * 步骤9：标记证据包降级
         * ==============================================================================
         * 目标：统一保存 degraded 标志和可观察的降级原因。
         * 数据源：source-only 路径、预算耗尽和 gap check 原因。
         * 操作：
         * 1) 设置 degraded 标志。
         * 2) 去重保存非空原因，保持返回顺序稳定。
         * ==============================================================================
         */
        """
        logger.info("开始标记证据包降级")
        self.degraded = True
        normalized = str(reason or "").strip()
        if normalized and normalized not in self.degraded_reasons:
            self.degraded_reasons.append(normalized)
        logger.info("证据包降级标记完成: reasons=%s", len(self.degraded_reasons))

    def to_dict(self) -> dict[str, Any]:
        """
        /*
         * ==============================================================================
         * 步骤7：生成证据包快照
         * ==============================================================================
         * 目标：为 search、fetch、research 提供同一份增量 JSON 数据源。
         * 数据源：当前 bundle 的候选、正文、引用、gap 和观测字段。
         * 操作：重新生成 citations，复制列表，禁止调用方通过结果修改内部状态。
         * ==============================================================================
         */
        """
        logger.info("开始生成 evidence bundle 快照")
        self.citations = self._build_citations()
        result = {
            "discovery_candidates": [dict(item) for item in self.discovery_candidates],
            "fetched_evidence": [dict(item) for item in self.fetched_evidence],
            "sources": [dict(item) for item in self.sources],
            "citations": [dict(item) for item in self.citations],
            "gaps": [dict(item) for item in self.gaps],
            "degraded": bool(self.degraded),
            "degraded_reason": "; ".join(self.degraded_reasons),
            "provider_attempts": [dict(item) for item in self.provider_attempts],
        }
        logger.info(
            "evidence bundle 快照生成完成: candidates=%s evidence=%s gaps=%s",
            len(self.discovery_candidates),
            len(self.fetched_evidence),
            len(self.gaps),
        )
        return result

    def _append_source(self, item: Mapping[str, Any]) -> None:
        source = {
            key: value
            for key, value in dict(item).items()
            if key in {"url", "title", "description", "provider", "source", "published_date", "source_type"}
            and value not in (None, "")
        }
        if source:
            self._append_unique(self.sources, source)

    @staticmethod
    def _append_unique(target: list[dict[str, Any]], item: Mapping[str, Any]) -> bool:
        identity = EvidenceBundle._item_identity(item)
        if any(EvidenceBundle._item_identity(existing) == identity for existing in target):
            return False
        target.append(dict(item))
        return True

    @staticmethod
    def _item_identity(item: Mapping[str, Any]) -> tuple[str, str | tuple[str, ...]]:
        url = str(item.get("url") or "").strip()
        if url:
            return "url", url
        return "fields", tuple(
            str(item.get(key) or "")
            for key in ("id", "provider", "title", "content")
        )

    def _build_citations(self) -> list[dict[str, str]]:
        citations: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in self.fetched_evidence:
            url = str(item.get("url") or "").strip()
            if not url or url in seen or not item.get("verified"):
                continue
            seen.add(url)
            citations.append(
                {
                    "url": url,
                    "title": str(item.get("title") or url),
                    "provider": str(item.get("provider") or ""),
                }
            )
        return citations
