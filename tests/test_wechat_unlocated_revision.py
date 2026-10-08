from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, QualityReviewReport, TrendReport
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.wechat_revision import (
    begin_revision, bind_issues, bind_patch_locations, finish_revision, verified_patch_locator,
)


def enable(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


@pytest.mark.parametrize("requirement,paragraph,quote,valid", [
    ("存在AI套话：值得注意的是。", "值得注意的是，具体推出时间尚未披露。", "值得注意的是", True),
    ("说明RTX Spark推出时间", "RTX Spark的推出时间尚未披露。", "RTX Spark", True),
    ("说明RTX Spark推出时间", "RTX Spark的推出时间尚未披露。", "RTX  Spark", True),
    ("说明RTX Spark推出时间", "其他段落。", "RTX Spark", False),
    ("说明RTX Spark推出时间", "RTX Spark的推出时间尚未披露。", "RTX", False),
    ("说明RTX Spark推出时间", "RTX Spark的推出时间尚未披露。", "。。。。", False),
    ("说明RTX Spark推出时间", "RTX Spark的推出时间尚未披露。", None, False),
])
def test_unlocated_issue_requires_literal_anchor(requirement, paragraph, quote, valid):
    assert verified_patch_locator(requirement, paragraph, quote) is valid


def patch_case(app_settings, logger, **overrides):
    enable(app_settings)
    before = "值得注意的是，具体推出时间尚未披露。"
    after = "具体推出时间尚未披露。"
    report = QualityReviewReport(verdict="needs_revision", originality_issues=["存在AI套话：值得注意的是。"])
    bind_issues(report, before, [])
    issue = report.issues[0]
    patch = {"issue_id": issue["issue_id"], "locator_quote": "值得注意的是", "paragraph_id": 0,
             "before": before, "after": after, **overrides}
    model = MagicMock(available=True)
    model.create_json_completion.return_value = {"patches": [patch]}
    focus = [f"{issue['issue_id']}：{issue['requirement']}；原段落：未能定位，勿猜测；证据：未知"]
    result = PolishAgent(app_settings, MagicMock(), model, logger).run(
        GeneratedContent(article=before + "\n\n其余合格段落原样保留。"), TrendReport(), DataReport(),
        app_settings.get_style(), CacheContext(), focus)
    return before, after, report, result


def test_missing_review_location_can_be_recovered_without_full_rewrite(app_settings, logger):
    before, after, report, result = patch_case(app_settings, logger)
    assert "值得注意的是" not in result["polished_article"]
    assert after in result["polished_article"] and "其余合格段落原样保留。" in result["polished_article"]
    assert result["grounding_diagnostics"][0]["revision_patch_accepted"] == 1
    assert not report.issues[0]["location_verified"]
    bind_patch_locations(report, result["revision_issue_locations"], before, [])
    assert report.issues[0]["location_verified"] and report.issues[0]["paragraph"] == before
    assert report.verdict == "needs_revision"
    trace = begin_revision(report, before, after, 1)
    finish_revision(trace, QualityReviewReport(verdict="approved"), after, [])
    assert trace["issues"][0]["status"] == "unverifiable"  # Approval/omission still does not prove resolution.


@pytest.mark.parametrize("overrides", [
    {"issue_id": "unknown"}, {"issue_id": []}, {"locator_quote": "不存在的短语"},
    {"locator_quote": "的是"}, {"before": "伪造原段落"},
])
def test_invalid_unlocated_patch_is_rejected(app_settings, logger, overrides):
    before, _, _, result = patch_case(app_settings, logger, **overrides)
    assert before in result["polished_article"]
    assert result["grounding_diagnostics"][0]["revision_patch_accepted"] == 0
    assert result["revision_issue_locations"] == []


def test_recovered_location_cannot_replace_existing_location_or_use_footer():
    before = "正文段落中存在值得注意的是。"
    report = QualityReviewReport(originality_issues=["存在AI套话：值得注意的是。"])
    bind_issues(report, before, [])
    issue = report.issues[0]
    location = {"issue_id": issue["issue_id"], "locator_quote": "值得注意的是", "paragraph": before}
    bind_patch_locations(report, [location], "正文。\n\n参考来源：\n" + before, [])
    assert not issue["location_verified"]
    bind_patch_locations(report, [location], before, [])
    assert issue["location_verified"]
    different = "另一个段落有值得注意的是。"
    bind_patch_locations(report, [{**location, "paragraph": different}], before + "\n\n" + different, [])
    assert issue["paragraph"] == before


def test_recovered_patch_refreshes_title_before_full_reaudit(app_settings, mock_openai_service, logger):
    before, after, first, pack = patch_case(app_settings, logger)
    first.audit_integrity = {"status": "complete"}
    object.__setattr__(app_settings.quality_review, "max_auto_revisions", 2)
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    content = GeneratedContent(article=before, polished_article=before, recommended_titles=["原标题"])
    events = []
    reports = iter([first, QualityReviewReport(verdict="approved", overall_score=95)])
    def review(content, *args, **kwargs):
        events.append(("review", content.article, list(content.recommended_titles)))
        return next(reports)
    def refresh(content, *args):
        events.append(("title", content.article, []))
        content.recommended_titles = ["同步后的标题"]
    service.quality_review_council_agent.run = review
    service.polish_agent.run = MagicMock(return_value=pack)
    service._apply_polish_pack = lambda content, pack, data: (
        setattr(content, "article", pack["polished_article"]), setattr(content, "polished_article", pack["polished_article"]))
    service._refresh_titles = refresh
    result = service._run_quality_review_council(content=content, trend_report=TrendReport(), data_report=DataReport(),
        style=app_settings.get_style(), cache_context=CacheContext(), editorial_council=None, fast_mode=False)
    assert result.verdict == "approved"
    assert [event[0] for event in events] == ["review", "title", "review"]
    assert events[-1][2] == ["同步后的标题"] and after in events[-1][1]
    assert content.revision_trace[0]["issues"][0]["before"] == before
    assert content.revision_trace[0]["issues"][0]["status"] == "unverifiable"
