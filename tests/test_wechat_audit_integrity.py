import asyncio
import copy
import json
import time
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from openai import AsyncOpenAI
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wechat_run_report import review_diagnostics

from toutiao_content_factory.agents.quality_review_council_agent import QualityReviewCouncilAgent
from toutiao_content_factory.exceptions import AIRequestError
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, QualityReviewReport
from toutiao_content_factory.models import WorkflowResult
from toutiao_content_factory.services.exporter import ExportService
from toutiao_content_factory.services.review_service import ReviewWorkflowService
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.wechat_revision import (
    apply_supplement, audit_fact_material, bind_issues, integrity_reasons,
    manuscript_fingerprint, pending_verifications, valid_automatic_approval,
)
from toutiao_content_factory.services.wechat_service import WechatPublishService


def enable(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


@pytest.fixture
def actual():
    return json.loads((Path(__file__).parent / "fixtures/wechat_audit_20261008.json").read_text(encoding="utf-8"))


def make_agent(settings, logger, model):
    enable(settings)
    prompts = MagicMock()
    prompts.render.return_value = "independent current manuscript audit"
    agent = QualityReviewCouncilAgent(settings, prompts, model, logger)
    # These tests target the model metadata boundary, independently of native hard gates.
    agent._apply_local_hard_gate = MagicMock()
    return agent


def actual_repair(actual):
    paragraph = next(p for p in actual["article"].split("\n\n") if "判断方向是" in p)
    return {"verdict": "approved", "chief_editor_conclusion": "现稿已经给出基于等待记录的判断方向。",
        "finding_evaluations": [{"issue_id": actual["primary_report"]["issues"][0]["issue_id"],
            "status": "not_applicable", "target": "body", "current_quote": paragraph, "evidence_ids": [],
            "verification": "现稿已明确应用条件，并区分高峰覆盖且等待缩短与高峰后开放、没有等待记录两种情况；30分钟是旧建议的假设示例，没有来源。"}],
        "issue_evaluations": []}


def test_real_manuscript_stale_requirement_repaired_without_fabricating_response_time(app_settings, logger, actual):
    model = MagicMock(available=True)
    model.create_json_completion.side_effect = [actual["primary_report"], actual_repair(actual)]
    agent = make_agent(app_settings, logger, model)
    content = GeneratedContent(article=actual["article"], recommended_titles=[actual["title"]])
    data = DataReport.from_dict(actual["data_report"])
    result = agent.run(content, data, app_settings.get_style(), CacheContext())
    assert result.overall_score == 88
    assert result.audit_integrity["status"] == "complete"
    assert result.audit_integrity["primary_report"] == actual["primary_report"]
    assert result.verdict == "approved" and result.required_revisions == []
    assert valid_automatic_approval(result, content.article, actual["title"], data)
    assert "30分钟" not in content.article
    assert content.audit_supplement_count == 1
    assert content.audit_history[0]["primary_report"] == actual["primary_report"]
    restored = GeneratedContent.from_dict(asdict(content))
    assert restored.audit_history == content.audit_history and restored.audit_supplement_count == 1
    assert model.create_json_completion.call_args.kwargs == {"single_attempt": True, "timeout_seconds": 60}
    repair_prompt = model.create_json_completion.call_args.args[1]
    assert "overall_score" not in repair_prompt and "role_reviews" not in repair_prompt


def test_pending_input_excludes_old_scores_status_and_verdict():
    content = GeneratedContent(revision_trace=[{"overall_score": 30, "verdict": "blocked", "issues": [{
        "issue_id": "logic", "category": "logic_issues", "requirement": "解释边界",
        "before": "旧段落", "after": "新段落", "status": "unverifiable", "verification": "旧意见"}]}])
    assert pending_verifications(content, []) == [{"issue_id": "logic", "category": "logic_issues",
        "requirement": "解释边界", "before": "旧段落", "after": "新段落", "evidence": []}]


def test_unverified_older_issue_is_not_lost_in_later_revision():
    content = GeneratedContent(revision_trace=[{"issues": [{"issue_id": "old", "status": "unverifiable"}]},
                                              {"issues": [{"issue_id": "new", "status": "still_present"}]}])
    assert {row["issue_id"] for row in pending_verifications(content, [])} == {"old", "new"}


@pytest.mark.parametrize("paragraph", ["", "旧稿已经删除的段落", "假引文"])
def test_missing_or_stale_current_finding_is_incomplete(paragraph):
    report = QualityReviewReport(overall_score=88, fact_score=90, required_revisions=["补充判断方向"],
        issues=[{"requirement": "补充判断方向", "paragraph": paragraph, "verification": "缺少条件比较"}])
    bind_issues(report, "现稿已经有条件判断。", [])
    assert any(reason.startswith("missing_or_stale_location") for reason in integrity_reasons(
        report, "现稿已经有条件判断。", "标题", [], []))


def revision_case():
    before = "开放设备就会缩短所有车辆的等待。"
    after = "只有在相近来车条件下核对实际等待记录，才能判断补位是否有效。"
    pending = [{"issue_id": "logic", "category": "logic_issues", "requirement": "收窄因果断言",
                "before": before, "after": after}]
    report = QualityReviewReport(overall_score=88, fact_score=90, verdict="approved", chief_editor_conclusion="通过",
        issue_evaluations=[{"issue_id": "logic", "status": "resolved", "after_quote": after,
                            "evidence_ids": [], "verification": "新增可比条件与等待记录，消除无条件因果推断。"}])
    return before, after, pending, report


def test_logic_resolution_allows_empty_fact_ids_with_real_difference_and_reason():
    _, article, pending, report = revision_case()
    assert integrity_reasons(report, article, "标题", [], pending) == []


@pytest.mark.parametrize("defect", ["missing", "empty_reason", "invented_quote", "wrong_id", "unchanged", "wording", "still_present"])
def test_revision_cannot_be_approved_without_proof(defect):
    before, article, pending, report = revision_case()
    row = report.issue_evaluations[0]
    if defect == "missing":
        report.issue_evaluations = []
    elif defect == "empty_reason":
        row["verification"] = ""
    elif defect == "invented_quote":
        row["after_quote"] = "不是现稿的句子"
    elif defect == "wrong_id":
        row["evidence_ids"] = ["unknown"]
    elif defect == "unchanged":
        pending[0]["after"] = article = before
        row["after_quote"] = article
    elif defect == "wording":
        pending[0]["before"] = "开放设备，就会缩短所有车辆的等待。"
        pending[0]["after"] = article = "开放设备就会缩短所有车辆的等待。"
        row["after_quote"] = article
    else:
        row["status"] = "still_present"
    assert integrity_reasons(report, article, "标题", [], pending)


@pytest.mark.parametrize("category", ["fact_issues", "hard_blockers"])
def test_fact_and_hard_blocker_resolution_requires_matching_evidence(category):
    _, article, pending, report = revision_case()
    pending[0]["category"] = category
    assert any(reason.startswith("invalid_evidence") for reason in integrity_reasons(report, article, "标题", [], pending))


def test_title_finding_must_quote_selected_title():
    report = QualityReviewReport(fact_score=90, required_revisions=["标题过于绝对"], issues=[{
        "requirement": "标题过于绝对", "target": "title", "paragraph": "一定缩短等待", "verification": "标题作出确定性承诺"}])
    bind_issues(report, "正文保留事实边界。", [])
    assert not integrity_reasons(report, "正文保留事实边界。", "一定缩短等待", [], [])
    assert integrity_reasons(report, "正文保留事实边界。", "可能缩短等待", [], [])


def test_unverified_hard_blocker_cannot_be_removed_by_supplement():
    report = QualityReviewReport(fact_score=90, hard_blockers=["违规承诺"], issues=[{
        "issue_id": "hard", "category": "hard_blockers", "requirement": "违规承诺", "paragraph": "保证收益"}])
    payload = {"verdict": "approved", "finding_evaluations": [{"issue_id": "hard", "status": "not_applicable",
        "current_quote": "保证收益", "verification": "声称已解决", "evidence_ids": []}], "issue_evaluations": []}
    with pytest.raises(ValueError):
        apply_supplement(report, payload, "保证收益", "标题", [])
    assert report.hard_blockers == ["违规承诺"] and report.verdict == "blocked"


@pytest.mark.parametrize("mode", ["failed", "invalid", "budget", "exhausted"])
def test_one_supplement_per_candidate_and_manual_on_failure(app_settings, logger, actual, monkeypatch, mode):
    model = MagicMock(available=True)
    if mode == "failed":
        model.create_json_completion.side_effect = [actual["primary_report"], AIRequestError("timeout"), actual["primary_report"]]
    else:
        model.create_json_completion.side_effect = [actual["primary_report"], {"verdict": "approved"}, actual["primary_report"]]
    content = GeneratedContent(article=actual["article"], recommended_titles=[actual["title"]],
                               audit_supplement_count=1 if mode == "exhausted" else 0)
    if mode == "budget":
        monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + 59))
    agent = make_agent(app_settings, logger, model)
    data = DataReport.from_dict(actual["data_report"])
    result = agent.run(content, data, app_settings.get_style(), CacheContext())
    assert result.audit_integrity["status"] != "complete"
    assert result.required_revisions and result.overall_score == 88
    assert result.audit_integrity["primary_report"] == actual["primary_report"]
    if mode in {"failed", "invalid"}:
        assert result.audit_integrity["initial_reasons"]
        assert set(result.audit_integrity["initial_reasons"]).issubset(result.audit_integrity["reasons"])
    assert model.create_json_completion.call_count == (1 if mode in {"budget", "exhausted"} else 2)
    if mode in {"failed", "invalid"}:
        agent.run(content, data, app_settings.get_style(), CacheContext(), revision_round=2)
        assert content.audit_supplement_count == 1 and model.create_json_completion.call_count == 3


@pytest.mark.parametrize("change", ["body", "title", "facts", "sources", "legacy"])
def test_old_approval_invalidates_on_any_manuscript_material_change(actual, change):
    report = QualityReviewReport(fact_score=90, verdict="approved", chief_editor_conclusion="通过")
    article, title, data = actual["article"], actual["title"], copy.deepcopy(actual["data_report"])
    report.audit_integrity = {"status": "complete", "fingerprint": manuscript_fingerprint(article, title, audit_fact_material(data)),
                              "primary_report": asdict(report)}
    assert valid_automatic_approval(report, article, title, data)
    if change == "body":
        article += "新增断言。"
    elif change == "title":
        title += "？"
    elif change == "facts":
        data["fact_evidence"][0]["claim"] = "改变的事实"
    elif change == "sources":
        data["source_records"][0]["original_text"] += "变更"
    else:
        report = QualityReviewReport.from_dict({"fact_score": 90, "verdict": "approved"})
        assert report.verdict == "approved" and report.audit_integrity == {}
    assert not valid_automatic_approval(report, article, title, data)


def test_publish_boundary_rejects_historical_approval_before_external_calls(app_settings, logger, tmp_path):
    enable(app_settings)
    publish = tmp_path / app_settings.review.publish_dirname
    publish.mkdir()
    (publish / "publish_meta.json").write_text(json.dumps({"selected_title": "标题", "approval_mode": "automatic"}))
    (publish / "article.md").write_text("正文。", encoding="utf-8")
    (tmp_path / "review_status.json").write_text('{"status":"approved"}')
    (tmp_path / "quality_review_council.json").write_text('{"verdict":"approved","fact_score":90}')
    (tmp_path / "workflow_result.json").write_text('{"data_report":{}}')
    service = WechatPublishService(app_settings, logger)
    service._get_access_token = MagicMock()
    with pytest.raises(ValueError, match="重新审核"):
        service.publish_run(tmp_path, submit=True)
    service._get_access_token.assert_not_called()
    assert not (app_settings.automation_dir / "wechat_publish_phase.json").exists()


def test_exact_real_manuscript_survives_export_and_automatic_approval(app_settings, logger, actual):
    enable(app_settings)
    report = QualityReviewReport(fact_score=90, verdict="approved", chief_editor_conclusion="通过")
    report.audit_integrity = {"status": "complete", "primary_report": asdict(report),
        "fingerprint": manuscript_fingerprint(actual["article"], actual["title"], audit_fact_material(actual["data_report"]))}
    workflow = WorkflowResult.from_dict({"content": {"article": actual["article"], "titles": [actual["title"]],
        "recommended_titles": [actual["title"]], "summary": "解释设备指标与排队体验的证据边界。"}, "data_report": actual["data_report"],
        "quality_review_council": asdict(report), "style_key": app_settings.default_style})
    app_settings.output_dir.mkdir(parents=True)
    ExportService(app_settings, logger).export(workflow)
    assert (app_settings.output_dir / "article.md").read_text(encoding="utf-8") == actual["article"]
    review = ReviewWorkflowService(app_settings, logger)
    review.approve_review(app_settings.output_dir, selected_title=actual["title"], automated=True)
    assert (app_settings.output_dir / app_settings.review.publish_dirname / "article.md").read_text(encoding="utf-8").strip() == actual["article"].strip()
    (app_settings.output_dir / "quality_review_council.json").write_text(json.dumps({"fact_score": 90, "verdict": "approved"}))
    with pytest.raises(ValueError, match="重新审核"):
        review.approve_review(app_settings.output_dir, selected_title=actual["title"], automated=True)


def test_material_change_invalidates_workflow_review_state(actual):
    content = GeneratedContent(article=actual["article"], recommended_titles=[actual["title"]])
    data = DataReport.from_dict(actual["data_report"])
    before = AgentWorkflowService._quality_review_state(content, data)
    data.fact_evidence.append({"fact_id": "new"})
    assert AgentWorkflowService._quality_review_state(content, data) != before


def test_true_new_unsupported_fact_still_hits_native_hard_gate(app_settings, logger, actual):
    enable(app_settings)
    model = MagicMock(available=False)
    prompts = MagicMock()
    prompts.render.return_value = "independent audit"
    agent = QualityReviewCouncilAgent(app_settings, prompts, model, logger)
    article = actual["article"].replace("参考来源：", "设备已经实现30分钟内响应。\n\n参考来源：")
    report = agent.run(GeneratedContent(article=article,
        recommended_titles=[actual["title"]]), DataReport.from_dict(actual["data_report"]), app_settings.get_style(), CacheContext())
    assert report.used_fallback and report.verdict == "blocked"
    assert report.audit_integrity["status"] == "model_unavailable"
    assert any("30" in item for item in report.hard_blockers)


@pytest.mark.parametrize("status,verdict,reason", [
    ("complete", "approved", None), ("complete", "needs_revision", "quality_manuscript_issue"),
    ("incomplete", "approved", "quality_audit_incomplete"),
    ("model_unavailable", "approved", "quality_model_unavailable"),
])
def test_runtime_diagnoses_article_report_and_provider_separately(app_settings, status, verdict, reason):
    run_dir = app_settings.runs_dir / "audit"
    run_dir.mkdir(parents=True)
    (run_dir / "quality_review_council.json").write_text(json.dumps({"verdict": verdict, "audit_integrity": {
        "status": status, "reasons": ["token=private"], "supplement_count": 1}}), encoding="utf-8")
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.automation_dir / "last_daily_workflow_report.json").write_text(json.dumps({
        "account_key": app_settings.account_key, "status": "review_required", "attempts": [{"run_dir": str(run_dir)}]}), encoding="utf-8")
    result = review_diagnostics(app_settings, {"status": "review_required"}, time.time_ns() - 1_000_000_000)
    assert result["attempts"][0]["quality_review"]["audit_integrity_status"] == status
    if reason:
        assert reason in result["reason_codes"]
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["success", "429", "timeout"])
def test_single_call_has_no_sdk_retry_and_total_timeout_cancels_transport(app_settings, logger, monkeypatch, mode):
    requests, cancelled, options, clients = [], [], [], []
    async def handler(request):
        requests.append(request)
        if mode == "timeout":
            try:
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        if mode == "429":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, json={"id": "test", "choices": [{"message": {"content": '{"ok":true}'}}]})
    def factory(**kwargs):
        options.append(kwargs)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return AsyncOpenAI(**kwargs, http_client=client)
    monkeypatch.setattr("toutiao_content_factory.services.openai_service.AsyncOpenAI", factory)
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    service.client = SimpleNamespace(api_key="test", base_url="https://model.invalid/v1")
    start = time.monotonic()
    if mode == "success":
        assert service.create_json_completion("system", "user", single_attempt=True, timeout_seconds=2) == {"ok": True}
    else:
        with pytest.raises(AIRequestError):
            service.create_json_completion("system", "user", single_attempt=True, timeout_seconds=.1)
    assert len(requests) == 1 and options[0]["max_retries"] == 0
    assert time.monotonic() - start < 3
    assert clients[0].is_closed
    assert bool(cancelled) == (mode == "timeout")
