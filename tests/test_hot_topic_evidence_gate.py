"""回归测试：热榜二创的证据门槛豁免必须是一个**显式开关**。

背景（2026-09-18）：

`_abort_if_low_quality` 原先是

    if "insufficient_source_facts" in quality_flags and (
        toutiao_auto_publish or not (is_hot_topic or is_title_only)
    ):

上面那句注释写着「实时热榜二创：标题型热点没有完整原文/证据链，是正常形态，
不以『缺证据/缺公司/质量分低』拦截」，但 `toutiao_auto_publish or` 这一支
让**开了自动发布时反而更严** —— 生产配置下该变量恒为 True，热榜题照样被拦。

而热榜题只有标题、没有正文，必然命中 insufficient_source_facts
（要求 ≥6 条事实且 ≥320 字）。换到社会故事赛道后 4 个候选全被跳过，
日更直接报「Daily workflow did not produce any runnable candidates」。

关键判断：既有测试 `test_agent_workflow.test_abort_rejects_insufficient_hot_topic_for_auto_publish`
**显式断言了这个严格行为**，说明它是有意策略而不是笔误。所以这次没有直接放宽，
而是加了一个默认关闭的显式开关 `hot_topic_evidence_exempt`：
  - 既有账号（含 sports_notes / default）行为一字不变；
  - 社会故事类账号在账号配置里打开它。

这个文件锁住「默认不变」和「打开后生效」两件事。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import (
    DataReport,
    ExtractionResult,
)
from toutiao_content_factory.services.agent_workflow import (
    AgentWorkflowService,
    LowQualityAbortException,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository

STORY_ACCOUNT = "robot_notes"
VERTICAL_ACCOUNT = "sports_notes"


class _StubOpenAI:
    def __init__(self) -> None:
        self.model = "stub"
        self.available = False
        self.estimated_cost = 0.0


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy",
        "TOUTIAO_HOT_TOPIC_EVIDENCE_EXEMPT",
    ):
        monkeypatch.delenv(name, raising=False)


def _workflow(account_key: str) -> AgentWorkflowService:
    settings = load_settings(Path.cwd(), account_key=account_key)
    return AgentWorkflowService(
        settings, PromptRepository(Path.cwd() / "prompts"), _StubOpenAI(), logging.getLogger("t")
    )


def _hot_case() -> tuple[ExtractionResult, DataReport]:
    extraction = ExtractionResult(
        merged_text="男子30层高空作业绳子被2楼老人剪断",
        core_data=[],
        industry_trends=[],
        growth_numbers=[],
        companies=[],
        supply_chain_keywords=[],
        key_points=[],
        topic_title="男子高空作业绳子被2楼老人剪断",
        is_hot_topic=True,
    )
    data_report = DataReport(
        topic_title="男子高空作业绳子被2楼老人剪断",
        is_hot_topic=True,
        topic_quality_score=90,
        topic_quality_flags=["insufficient_source_facts"],
    )
    return extraction, data_report


# ---------------------------------------------------------------------------
# 开关本身
# ---------------------------------------------------------------------------


def test_switch_defaults_to_off_for_existing_accounts() -> None:
    """默认必须关闭 —— 既有账号的严格行为不能被动到。"""
    for account_key in (VERTICAL_ACCOUNT, "default", "politics_notes"):
        assert load_settings(Path.cwd(), account_key=account_key).hot_topic_evidence_exempt is False


def test_story_account_opts_in() -> None:
    assert load_settings(Path.cwd(), account_key=STORY_ACCOUNT).hot_topic_evidence_exempt is True


# ---------------------------------------------------------------------------
# 行为：关闭时严格（既有语义），打开时豁免
# ---------------------------------------------------------------------------


def test_hot_topic_is_still_blocked_when_the_switch_is_off() -> None:
    """既有严格行为保持不变 —— 这是 test_agent_workflow 里那条断言的语义。"""
    workflow = _workflow(VERTICAL_ACCOUNT)
    extraction, data_report = _hot_case()

    with pytest.raises(LowQualityAbortException, match="insufficient_source_facts"):
        workflow._abort_if_low_quality(extraction, data_report)


def test_hot_topic_is_exempt_when_the_switch_is_on() -> None:
    """社会故事账号打开开关后，热榜题不再被证据门槛拦掉。

    关键是这个账号**确实开着自动发布**（生产配置），也就是既有策略里
    「照拦」的那一支 —— 所以这条断言真正验证的是「开关能强力覆盖」。
    """
    workflow = _workflow(STORY_ACCOUNT)
    assert workflow.settings.publishing.enabled is True
    assert workflow.settings.publishing.auto_publish is True
    assert workflow.settings.publishing.draft_only is False
    extraction, data_report = _hot_case()

    # 不抛异常即为通过
    workflow._abort_if_low_quality(extraction, data_report)


def test_non_hot_topic_is_never_exempt() -> None:
    """开关只对热榜/标题型热点生效；普通题证据不足照样拦。"""
    workflow = _workflow(STORY_ACCOUNT)
    extraction = ExtractionResult(
        merged_text="某行业季度观察",
        core_data=[],
        industry_trends=[],
        growth_numbers=[],
        companies=[],
        supply_chain_keywords=[],
        key_points=[],
        topic_title="某行业季度观察",
        is_hot_topic=False,
    )
    data_report = DataReport(
        topic_title="某行业季度观察",
        is_hot_topic=False,
        topic_quality_score=90,
        topic_quality_flags=["insufficient_source_facts"],
    )

    with pytest.raises(LowQualityAbortException, match="insufficient_source_facts"):
        workflow._abort_if_low_quality(extraction, data_report)
