"""叙事体裁账号的审核团提示词必须带「社会故事契约」（2026-10-21）。

云端实测（run 39577216468）：robot_notes 是 ``content_domain=narrative``、
``family_story`` 风格的账号，但审核团给出的 8 条意见全是产业分析标准：

* "明确说明市值变化的市场预期变化原因，并补充数据支撑"
* "补充智慧教育、智慧医疗增长背后的潜在风险或市场环境变化分析"
* "保留风险边界和跟踪变量"

审核团提示词里只有**公众号**分支有体裁契约，头条叙事号没有——于是 850 字的
社会故事稿被按产业长文标准审，``verdict`` 永远是 ``needs_revision``，发布闸门
永远 ``manual_review``。这不是文章质量问题，是"尺子用错了"。

本文件锁住：叙事账号的审核提示词里必须带叙事契约（不要求产业链/订单/利润/市值，
不适用 RULE_006/RULE_009，正文下限 700），产业账号不能被塞进这套契约。
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from toutiao_content_factory.agents.quality_review_council_agent import (
    QualityReviewCouncilAgent,
)
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent
from toutiao_content_factory.services.prompt_repository import PromptRepository

NARRATIVE_MARKER = "不要求补写产业链"
INDUSTRY_MARKER = "不适用本体裁"


def _agent(account_key: str) -> tuple[QualityReviewCouncilAgent, object]:
    settings = load_settings(Path.cwd(), account_key=account_key)
    agent = QualityReviewCouncilAgent(
        settings,
        PromptRepository(Path.cwd() / "prompts"),
        MagicMock(available=False, model="stub"),
        logging.getLogger(f"test.council.{account_key}"),
    )
    agent.request_json = MagicMock(return_value=None)
    return agent, settings


def _report() -> DataReport:
    return DataReport(
        topic_title="科大讯飞的AI预期，兑现了多少",
        core_data=["科大讯飞2026年上半年营收同比增长约18%"],
        growth_numbers=["18%"],
        companies=["科大讯飞"],
        evidence_notes=["公开财报显示营收同比增长约18%"],
        key_points=["营收增长需要利润兑现验证"],
        risk_flags=["估值预期与实际兑现存在差距"],
        focus_industry="人工智能",
        is_hot_topic=True,
    )


def _prompt_from(agent) -> str:
    article = "科大讯飞发布财报，营收同比增长约18%。" * 40
    agent.run(
        GeneratedContent(
            article=article,
            polished_article=article,
            recommended_titles=["科大讯飞AI预期兑现了多少"],
        ),
        _report(),
        agent.settings.get_style(),
        CacheContext(),
        editorial_council=None,
        revision_round=0,
    )
    assert agent.request_json.call_args, "council must call the model with a prompt"
    system_prompt, prompt = agent.request_json.call_args.args
    return f"{system_prompt}\n{prompt}"


def test_narrative_council_prompt_carries_story_contract() -> None:
    agent, settings = _agent("robot_notes")
    assert settings.content_domain == "narrative"

    prompt = _prompt_from(agent)

    assert NARRATIVE_MARKER in prompt, "叙事号必须被告知不要要求补写产业链分析"
    assert "市值" in prompt and "订单" in prompt
    assert "700" in prompt, "叙事体裁的正文下限 700 字必须写进契约"
    assert "RULE_006" in prompt and "RULE_009" in prompt


def test_industry_account_prompt_has_no_story_contract() -> None:
    settings = load_settings(Path.cwd())
    if settings.content_domain == "narrative":
        # Local config default account is industry; if that ever changes this
        # test would be meaningless, so fail loudly instead of silently passing.
        raise AssertionError("default account is narrative; pick an industry account for this test")

    agent, _ = _agent("default")
    prompt = _prompt_from(agent)

    assert NARRATIVE_MARKER not in prompt
    assert INDUSTRY_MARKER not in prompt
