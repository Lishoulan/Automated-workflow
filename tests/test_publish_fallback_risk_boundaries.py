"""发布审核兜底路径不得把「正文风险边界」当成「发布阻断项」（2026-10-21）。

云端实测（run 39577216468）：定时运行默认 ``RUN_FAST_MODE=true``，所以
``PublishReviewAgent`` 走的是 ``_fallback``。兜底里把 ``data_report.risk_flags``
（正文里本该交代的风险边界，例如"国产芯片技术成熟度可能影响兑现速度"）
原样塞进 ``publish_risk_flags``，而紧随其后的一条规则是

    if publish_risk_flags or readiness < 78:
        recommendation = "manual_review"

于是**只要选题带有风险边界，发布建议必然是 manual_review**，而
``AppService._qualifies_for_auto_approval`` 只接受 ``ready_to_publish``/``approve``
—— 自动发布被第二条结构性否决锁死（第一条是质量审核团返工死锁，已由 PR #47 修复）。

模型路径不这么干：``risk_flags_json`` 只是作为上下文交给模型判断，是否构成
发布阻断由模型按稿件实际情况决定。兜底路径把"文章讨论了风险"等同成
"文章有发布风险"，是范畴错误。修复方式：风险边界写进 ``publish_notes``
（供人工阅读），不再进 ``publish_risk_flags``；发布与否继续由**分数门槛**
（``TOUTIAO_AUTO_PUBLISH_MIN_*``）决定——那才是配置里真正定义自动发布的地方。

真正的发布阻断项（敏感词、preflight blocking flags、AI 痕迹过重、正文偏短、
正文过短到影响阅读）全部保留。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from toutiao_content_factory.agents.publish_review_agent import PublishReviewAgent
from toutiao_content_factory.models import ContentScore, DataReport, GeneratedContent
from toutiao_content_factory.services.prompt_repository import PromptRepository


def _agent(app_settings, logger, openai_service=None) -> PublishReviewAgent:
    openai_service = openai_service or MagicMock()
    openai_service.available = True
    # 缺少 readiness 字段 → 强制走兜底路径。
    openai_service.create_json_completion.return_value = {"publish_notes": []}
    prompt_repository = MagicMock(spec=PromptRepository)
    prompt_repository.render.return_value = "publish review prompt"
    agent = PublishReviewAgent(app_settings, prompt_repository, openai_service, logger)
    agent.growth_ops_service = MagicMock()
    agent.growth_ops_service.get_self_benchmark_track_context.return_value = {}
    agent.growth_ops_service.build_benchmark_digest.return_value = ""
    return agent


def _content(article_chars: int = 900) -> GeneratedContent:
    article = "这是一段用于测试的正文内容。" * (article_chars // 13 + 1)
    return GeneratedContent(
        article=article[:article_chars],
        summary="一篇可发布的测试稿件。",
        recommended_titles=["科大讯飞AI预期兑现了多少"],
        titles=["科大讯飞AI预期兑现了多少"],
        validation_score=100,
    )


def _data_report(*, risk_flags: list[str] | None = None) -> DataReport:
    return DataReport(
        topic_title="科大讯飞的AI预期，兑现了多少",
        focus_industry="人工智能",
        companies=["科大讯飞"],
        evidence_notes=["公开财报显示营收同比增长约18%"],
        key_points=["营收增长需要利润兑现验证"],
        risk_flags=(
            ["国产芯片技术成熟度可能影响市场预期兑现速度", "AI硬件供应链稳定性面临挑战"]
            if risk_flags is None
            else risk_flags
        ),
    )


def _score(ai_generated_risk: int = 32) -> ContentScore:
    return ContentScore(overall_score=83, ai_generated_risk=ai_generated_risk)


def _run(agent, content, data_report, score, preflight=None):
    return agent.run(
        content,
        data_report,
        agent.settings.style_profiles["deep_industry_analysis"],
        score,
        preflight_summary=preflight or {},
    )


def test_content_risk_boundaries_do_not_force_manual_review(app_settings, logger) -> None:
    agent = _agent(app_settings, logger)
    result = _run(agent, _content(), _data_report(), _score())

    assert result["publish_readiness_score"] >= 78
    assert result["publish_recommendation"] == "ready_to_publish", (
        "风险边界是正文该交代的内容，不该把发布建议钉死在 manual_review"
    )
    assert not any("国产芯片" in item for item in result["publish_risk_flags"])
    assert any("国产芯片" in item for item in result["publish_notes"]), (
        "风险边界仍要作为发布提示保留给人工 / 下游阅读"
    )


def test_ai_flavor_risk_still_blocks_auto_publish(app_settings, logger) -> None:
    agent = _agent(app_settings, logger)
    result = _run(agent, _content(), _data_report(), _score(ai_generated_risk=60))

    assert result["publish_recommendation"] == "manual_review"
    assert result["publish_risk_flags"]


def test_short_article_still_blocks_auto_publish(app_settings, logger) -> None:
    agent = _agent(app_settings, logger)
    result = _run(agent, _content(article_chars=400), _data_report(), _score())

    assert result["publish_recommendation"] == "manual_review"
    assert any("偏短" in item for item in result["publish_risk_flags"])


def test_sensitive_word_preflight_still_zeroes_readiness(app_settings, logger) -> None:
    agent = _agent(app_settings, logger)
    result = _run(
        agent,
        _content(),
        _data_report(),
        _score(),
        preflight={"blocking_flags": ["含敏感词"]},
    )

    assert result["publish_readiness_score"] == 0
    assert result["publish_recommendation"] == "revise_before_publish"


def test_preflight_blocking_flags_still_block(app_settings, logger) -> None:
    agent = _agent(app_settings, logger)
    result = _run(
        agent,
        _content(),
        _data_report(),
        _score(),
        preflight={"blocking_flags": ["标题与正文不一致"]},
    )

    assert result["publish_recommendation"] == "manual_review"
    assert any("标题与正文不一致" in item for item in result["publish_risk_flags"])
