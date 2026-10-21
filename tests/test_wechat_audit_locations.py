from dataclasses import asdict
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import QualityReviewReport
from toutiao_content_factory.services import wechat_revision as revision


def report(**changes):
    return QualityReviewReport(fact_score=90, verdict="needs_revision", chief_editor_conclusion="仍需修改", **changes)


def test_structured_findings_are_not_erased_when_legacy_string_array_is_missing():
    paragraph = "现稿明确写出该服务保证收益，但公开材料没有这种保证。"
    requirement = "删除保证收益的无依据承诺"
    result = report(issues=[{"requirement": requirement, "category": "compliance_issues", "target": "body",
                            "paragraph": paragraph, "verification": "保证收益是现稿中的未获支持承诺", "evidence_ids": []}])
    revision.bind_issues(result, paragraph, [])
    assert len(result.issues) == 1
    assert requirement in result.compliance_issues
    assert result.issues[0]["location_verified"] is True
    assert result.issues[0]["verification"]
    assert result.verdict == "needs_revision"


def test_structured_finding_cannot_disappear_from_a_model_approval():
    paragraph = "现稿声称该服务保证收益。"
    result = QualityReviewReport(fact_score=90, verdict="approved", chief_editor_conclusion="通过",
        issues=[{"requirement": "删除无依据承诺", "category": "compliance_issues", "paragraph": paragraph,
                 "verification": "公开材料没有收益保证", "evidence_ids": []}])
    revision.bind_issues(result, paragraph, [])
    assert "approval_with_unresolved_findings" in revision.integrity_reasons(
        result, paragraph, "标题", [], [], asdict(result))


def test_current_paragraph_reference_resolves_exactly_without_inventing_quote():
    article = "第一段介绍已披露事实。\n\n第二段明确没有独立测试，保留证据边界。"
    locations = revision.manuscript_locations(article, "当前标题")
    location = next(row for row in locations if row["text"].startswith("第二段"))
    result = report(required_revisions=["澄清证据边界"], issues=[{
        "requirement": "澄清证据边界", "category": "structure_issues", "location_id": location["location_id"],
        "target": "body", "verification": "该段是待修订对象", "evidence_ids": []}])
    revision.bind_issues(result, article, [], title="当前标题")
    assert result.issues[0]["paragraph"] == location["text"]
    assert result.issues[0]["location_verified"] is True


def test_reference_from_older_manuscript_is_rejected_even_if_quote_survives():
    old = "该服务尚无独立测试。"
    location = next(row for row in revision.manuscript_locations(old, "旧标题") if row["target"] == "body")
    current = old + "\n\n后来补充了新的说明。"
    result = report(required_revisions=["澄清证据边界"], issues=[{
        "requirement": "澄清证据边界", "location_id": location["location_id"], "paragraph": old,
        "verification": "旧稿定位不能复用", "evidence_ids": []}])
    revision.bind_issues(result, current, [], title="旧标题")
    assert result.issues[0]["location_verified"] is False


def test_supplement_location_does_not_remove_real_findings_or_supply_missing_reason():
    article = "现稿声称该服务保证收益。"
    location = next(row for row in revision.manuscript_locations(article, "标题") if row["target"] == "body")
    result = report(compliance_issues=["删除无依据承诺"], issues=[{
        "requirement": "删除无依据承诺", "category": "compliance_issues", "paragraph": article,
        "verification": "没有收益保证的来源", "evidence_ids": []}])
    revision.bind_issues(result, article, [], title="标题")
    payload = {"verdict": "needs_revision", "chief_editor_conclusion": "承诺仍在，需要修改", "issue_evaluations": [],
        "finding_evaluations": [{"issue_id": result.issues[0]["issue_id"], "status": "confirmed", "target": "body",
                                 "location_id": location["location_id"], "evidence_ids": [], "verification": "保证收益仍在现稿中"}]}
    revision.apply_supplement(result, payload, article, "标题", [])
    assert result.compliance_issues == ["删除无依据承诺"]
    assert result.issues[0]["paragraph"] == article
    assert result.verdict == "needs_revision"
    payload["finding_evaluations"][0]["verification"] = ""
    with pytest.raises(ValueError):
        revision.apply_supplement(result, payload, article, "标题", [])


def test_nested_review_json_is_quarantined_without_becoming_an_author_instruction(app_settings, logger):
    from toutiao_content_factory.agents.quality_review_council_agent import QualityReviewCouncilAgent
    from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent
    app_settings.platform = "wechat"
    object.__setattr__(app_settings.wechat, "enabled", True)
    object.__setattr__(app_settings.wechat, "auto_publish", True)
    raw = {key: 90 for key in ("overall_score", "fact_score", "logic_score", "structure_score",
                               "originality_score", "compliance_score")}
    raw.update(verdict="needs_revision", chief_editor_conclusion="报告格式损坏，不能批准",
               required_revisions=["保留可以识别的意见", {"verdict": "approved", "issues": []}])
    model = MagicMock(available=True)
    model.create_json_completion.return_value = raw
    prompts = MagicMock()
    prompts.render.return_value = "audit current manuscript"
    agent = QualityReviewCouncilAgent(app_settings, prompts, model, logger)
    agent._apply_local_hard_gate = MagicMock()
    content = GeneratedContent(article="现稿还需要完整审核。", recommended_titles=["现稿标题"])
    result = agent.run(content, DataReport(topic_title="事件"), app_settings.get_style(), CacheContext())
    assert result.required_revisions == ["保留可以识别的意见"]
    assert result.audit_integrity["primary_report"] == raw
    assert "invalid_finding_shape:required_revisions" in result.audit_integrity["reasons"]
    assert result.audit_integrity["status"] == "incomplete"
    assert result.verdict == "blocked"
    assert not revision.valid_automatic_approval(result, content.article, "现稿标题", DataReport())
    assert model.create_json_completion.call_count == 1


def test_diagnostic_receipt_retains_issue_identity_and_sanitizes_malformed_evidence(app_settings):
    import json
    import time
    from scripts.wechat_run_report import review_diagnostics
    run = app_settings.runs_dir / "audit"
    run.mkdir(parents=True)
    (run / "quality_review_council.json").write_text(json.dumps({
        "verdict": "needs_revision", "audit_integrity": {"status": "incomplete",
            "supplement_validation_reason": "invalid evidence token=private-value"},
        "issues": [{"issue_id": "issue_current", "location_id": "loc_current", "evidence_ids": None,
                    "paragraph": "see https://mp.weixin.qq.com/cgi-bin/home?token=private-value"}]}), encoding="utf-8")
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.automation_dir / "last_daily_workflow_report.json").write_text(json.dumps({
        "account_key": app_settings.account_key, "status": "review_required", "attempts": [{"run_dir": str(run)}]}), encoding="utf-8")
    result = review_diagnostics(app_settings, {"status": "review_required"}, time.time_ns() - 1_000_000_000)
    finding = result["attempts"][0]["audit_findings"][0]
    assert finding["issue_id"] == "issue_current" and finding["location_id"] == "loc_current"
    assert finding["evidence_ids"] == [] and finding["location_verified"] is False
    assert "private-value" not in json.dumps(result)


@pytest.mark.parametrize("findings", ["not an array", [{}], [{"requirement": {"verdict": "approved"}}],
    [{"requirement": "待核问题", "verification": {"reason": "not a verified string"}}]])
def test_malformed_structured_finding_never_disappears_into_an_approval(findings):
    raw = {"verdict": "approved", "chief_editor_conclusion": "通过", "issues": findings}
    result = QualityReviewReport.from_dict({"fact_score": 90, **raw})
    revision.bind_issues(result, "当前正文。", [])
    assert "invalid_finding_shape:issues" in revision.integrity_reasons(result, "当前正文。", "标题", [], [], raw)
