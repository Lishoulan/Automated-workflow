from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.exceptions import LowQualityAbortException, AIRequestError
from toutiao_content_factory.models import DataReport, GeneratedContent, TrendReport, CacheContext, QualityReviewReport
from toutiao_content_factory.services import wechat_evidence as evidence, wechat_retry as retry, wechat_metrics as metrics
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.material_search_service import MaterialSource
from toutiao_content_factory.services.openai_service import OpenAIContentService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wechat_cloud_run as cloud
from wechat_run_report import cloud_exit_code, review_diagnostics


def enable(settings):
    object.__setattr__(settings, "platform", "wechat")
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


def report():
    sources = evidence.source_records([MaterialSource("陕西储能通知", "https://a.test/policy", 
        "陕西发布储能通知，要求相关储能项目与配套新能源项目同期投产。适用条件按通知执行。", True)])
    return DataReport(source_records=sources, fact_evidence=evidence.build_facts(sources), article_kind="policy_explainer",
                      topic_title="陕西储能通知")


def normal():
    return {"status": "review_required", "publication_attempted": False, "native_exit_code": 0}


def reserved():
    now = datetime.now(timezone.utc).isoformat()
    day = retry.day_of(now)
    state, status = retry.reserve({}, day, "101:1", now)
    assert status == "reserved"
    return state, day


def test_effective_body_excludes_footer_urls_and_repeated_source_prefixes():
    body = "据《统计2026》报道，真实正文。\n\n据《统计2026》报道，解释边界。"
    assert evidence.effective_body_chars(body + "\nhttps://a.test/x\n参考来源：" + "来源" * 1000) == len("真实正文。解释边界。")


def test_policy_cleanup_removes_unsupported_global_cost_and_return_claims():
    data = report()
    original = "陕西要求储能项目与配套新能源项目同期投产。\n\n全球趋势正在改变。\n\n成本构成已经改变。\n\n投资收益将增加。"
    grounded = evidence.prepare_article(original, data)
    assert "同期投产" in grounded
    assert all(word not in grounded for word in ("全球趋势", "成本构成", "投资收益"))


@pytest.mark.parametrize("kind", list(evidence.KINDS))
def test_outline_has_fact_bindings_and_common_body_target(kind):
    data = report()
    data.article_kind = kind
    evidence.build_outline(data)
    assert len(data.writing_outline) == 6
    assert all(section["fact_ids"] for section in data.writing_outline)
    assert sum(section["target_chars"] for section in data.writing_outline) == 2100
    assert "1750" in data.writing_brief and data.word_target == 2100


def test_failed_inputs_require_changed_outline_or_requirements():
    data = report()
    evidence.build_outline(data)
    original = data.draft_fingerprint
    with pytest.raises(LowQualityAbortException):
        evidence.build_outline(report(), {"failed_fingerprints": [original]})
    revised = report()
    evidence.build_outline(revised, {"failed_fingerprints": [original], "requirements": ["限定陕西同期投产项目"]})
    assert revised.draft_fingerprint != original
    with pytest.raises(LowQualityAbortException):
        evidence.build_outline(report(), {"failed_fingerprints": [revised.draft_fingerprint], "requirements": ["限定陕西同期投产项目"]})


def test_retry_requirements_only_apply_to_matching_sources():
    data = report()
    evidence.build_outline(data)
    context = {"failed_inputs": [{"source_fingerprint": data.source_fingerprint,
                                 "requirements": ["限定陕西项目"]}]}
    matching = report()
    evidence.build_outline(matching, context)
    assert matching.regeneration_requirements == ["限定陕西项目"]
    unrelated = report()
    unrelated.fact_evidence = [dict(unrelated.fact_evidence[0], quote="机器人产品完成测试。")]
    evidence.build_outline(unrelated, context)
    assert unrelated.regeneration_requirements == []


def test_data_report_positional_compatibility():
    assert DataReport(["原有数据"]).core_data == ["原有数据"]


def test_polish_preserves_unpatched_paragraphs_and_rejects_bad_patch_identity(app_settings, mock_openai_service, logger):
    enable(app_settings)
    agent = PolishAgent(app_settings, MagicMock(), mock_openai_service, logger)
    agent.openai_service.available = True
    article = "合格段落应保持。\n\n需要收窄的段落。\n\n另一个合格段落应保持。"
    agent.openai_service.create_json_completion = MagicMock(return_value={"patches": [
        {"paragraph_id": 1, "before": "需要收窄的段落。", "after": "仅适用于陕西通知明确的项目。"},
        {"paragraph_id": 0, "before": "错误原文", "after": "误删"},
        {"paragraph_id": 20, "before": "未知", "after": "误删"}]})
    result = agent.run(GeneratedContent(article=article), TrendReport(), report(), app_settings.get_style(), CacheContext(), ["收窄范围"])
    assert "合格段落应保持。" in result["polished_article"]
    assert "另一个合格段落应保持。" in result["polished_article"]
    assert "仅适用于陕西" in result["polished_article"]
    assert "误删" not in result["polished_article"]


def test_initial_polish_does_not_rewrite_whole_article(app_settings, mock_openai_service, logger):
    enable(app_settings)
    agent = PolishAgent(app_settings, MagicMock(), mock_openai_service, logger)
    agent.openai_service.create_json_completion = MagicMock(side_effect=AssertionError("No full rewrite"))
    original = "段落事实清楚。\n\n保留条件和边界。"
    assert agent.run(GeneratedContent(article=original), TrendReport(), DataReport(), app_settings.get_style(), CacheContext())["polished_article"] == original


def test_wechat_writer_uses_bound_sections_and_avoids_legacy_shrinking(app_settings, mock_openai_service, logger):
    enable(app_settings)
    data = report()
    evidence.build_outline(data)
    writer = WriterAgent(app_settings, MagicMock(), mock_openai_service, logger)
    valid = data.writing_outline[0]
    writer.request_json = MagicMock(return_value={"sections": [
        {"section": valid["section"], "fact_ids": valid["fact_ids"], "text": "陕西储能措施须核对通知明确的适用条件。"},
        {"section": valid["section"], "fact_ids": valid["fact_ids"], "text": "重复节不应写入。"},
        {"section": "虚构章节", "fact_ids": valid["fact_ids"], "text": "大纲外内容不应写入。"},
        {"section": data.writing_outline[1]["section"], "fact_ids": ["虚构证据"], "text": "未知证据内容不应写入。"}]})
    writer._finalize_article = MagicMock(side_effect=AssertionError("Legacy rewrite must not shrink the draft"))
    content = writer.run(TrendReport(), data, app_settings.get_style(), CacheContext())
    assert "陕西储能措施" in content.article and "不应写入" not in content.article
    assert content.grounding_diagnostics
    assert "2000～2200" in writer.request_json.call_args.args[1]


def test_unusable_wechat_expansions_are_bounded(app_settings, mock_openai_service, logger):
    enable(app_settings)
    object.__setattr__(app_settings.quality_review, "max_auto_revisions", 2)
    writer = WriterAgent(app_settings, MagicMock(), mock_openai_service, logger)
    writer._has_grounded_evidence = MagicMock(return_value=True)
    writer.openai_service.create_json_completion = MagicMock(return_value={"paragraphs": []})
    for _ in range(6):
        assert writer._expand_article_via_llm(["原稿事实。"], report(), min_chars=2100) == ["原稿事实。"]
    assert writer.openai_service.create_json_completion.call_count == 3


def test_final_cleanup_shortfall_gets_evidence_repair_before_title(app_settings, mock_openai_service, logger):
    enable(app_settings)
    workflow = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    data = report()
    content = GeneratedContent(article="全球趋势正在改变。" * 250)
    seen = []
    workflow.writer_agent.ensure_article_min_chars = MagicMock(side_effect=lambda article, *a, **kw:
        seen.append(("repair", evidence.effective_body_chars(article))) or "这里解释通知适用边界。" * 190)
    workflow.title_agent.run = MagicMock(side_effect=lambda content, *a:
        seen.append(("title", evidence.effective_body_chars(content.article))) or {"titles": [], "recommended_titles": []})
    workflow._refresh_titles(content, None, data, None, None)
    assert seen[0] == ("repair", 0)
    assert seen[1][0] == "title" and seen[1][1] >= 1750
    assert content.grounding_diagnostics[-1]["after_cleanup_chars"] == 0


def test_cleanup_undo_restores_audited_titles_without_another_audit(app_settings, mock_openai_service, logger):
    enable(app_settings)
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    content = GeneratedContent(article="原稿。", polished_article="原稿。", titles=["原标题"], recommended_titles=["原标题"])
    service.quality_review_council_agent.run = MagicMock(return_value=QualityReviewReport(verdict="needs_revision", audit_integrity={"status": "complete"}, required_revisions=["改范围"]))
    service.polish_agent.run = MagicMock(return_value={"polished_article": "原稿。\n\n全球趋势。"})
    def cleanup(content, *a):
        content.article = content.polished_article = "原稿。"
        content.titles = content.recommended_titles = ["新标题"]
    service._refresh_titles = cleanup
    service._run_quality_review_council(content=content, trend_report=TrendReport(), data_report=report(),
        style=app_settings.get_style(), cache_context=CacheContext(), editorial_council=None, fast_mode=False)
    service.quality_review_council_agent.run.assert_called_once()
    assert content.titles == content.recommended_titles == ["原标题"]
    assert content.revision_trace and all(item["status"] != "resolved" for item in content.revision_trace[-1]["issues"])


def test_old_models_default_diagnostics_empty():
    assert DataReport.from_dict({}).draft_fingerprint == ""
    assert DataReport.from_dict({}).writing_outline == []
    assert GeneratedContent.from_dict({}).grounding_diagnostics == []


def test_daily_manual_rerun_counts_and_three_run_limit():
    state, day = reserved()
    for key in ("101:1", "101:2", "102:1"):
        if key != "101:1":
            state, result = retry.reserve(state, day, key, datetime.now(timezone.utc).isoformat())
            assert result == "reserved"
        state = retry.complete(state, day, key, 200, normal(), {})
    state, result = retry.reserve(state, day, "103:1", datetime.now(timezone.utc).isoformat())
    assert result == "daily_budget_exhausted"
    assert len(state["days"][day]["runs"]) == 3


def test_ninety_minute_budget_and_unfinished_reservation_fail_closed():
    state, day = reserved()
    with pytest.raises(RuntimeError):
        retry.reserve(state, day, "102:1", datetime.now(timezone.utc).isoformat())
    tomorrow = (datetime.now(retry.BEIJING) + timedelta(days=1)).strftime("%Y-%m-%d")
    with pytest.raises(RuntimeError):
        retry.reserve(state, tomorrow, "102:1", datetime.now(timezone.utc).isoformat())
    state = retry.complete(state, day, "101:1", 5400, normal(), {})
    assert retry.reserve(state, day, "102:1", datetime.now(timezone.utc).isoformat())[1] == "daily_budget_exhausted"


def test_relay_intent_is_once_only_and_cannot_cross_beijing_date():
    state, day = reserved()
    state = retry.complete(state, day, "101:1", 100, normal(), {})
    state = retry.dispatch_intent(state, day, "101:1")
    with pytest.raises(RuntimeError):
        retry.dispatch_intent(state, day, "101:1")
    assert retry.reserve(state, day, "102:1", datetime.now(timezone.utc).isoformat(), parent="101:1", parent_day="2000-01-01")[1] == "stale_relay"
    state, result = retry.reserve(state, day, "102:1", datetime.now(timezone.utc).isoformat(), parent="101:1", parent_day=day)
    assert result == "reserved"
    state = retry.complete(state, day, "102:1", 100, normal(), {})
    assert retry.reserve(state, day, "103:1", datetime.now(timezone.utc).isoformat(), parent="101:1", parent_day=day)[1] == "duplicate_or_invalid_relay"


@pytest.mark.parametrize("changes,code", [({"wechat_status": "submitted_for_review"}, 0),
    ({"wechat_status": "publish_uncertain"}, 1), ({"publication_attempted": True}, 0),
    ({"status": "publish_login_required"}, 1), ({"status": "error"}, 1),
    ({"metrics_error": "OSError"}, 0), ({"retry_state_error": "OSError"}, 0), ({"native_exit_code": 1}, 0)])
def test_true_faults_or_possible_submission_never_relay(changes, code):
    state, day = reserved()
    assert not retry.can_relay(state["days"][day], normal() | changes, code)


@pytest.mark.parametrize("status", ["review_required", "no_topics"])
def test_explicit_normal_no_publication_can_relay(status):
    state, day = reserved()
    payload = normal() | {"status": status, "run_outcome": status}
    assert retry.can_relay(state["days"][day], payload, 0) is (status == "review_required")
    assert cloud_exit_code(payload, 3) == 0
    assert cloud_exit_code(payload | {"publication_attempted": True}, 3) == 3


def test_dispatch_intent_persisted_before_api_and_single_dispatch(app_settings, monkeypatch):
    state, day = reserved()
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    events = []
    def write(state, *a):
        events.append(next(iter(state["days"][day]["dispatches"].values()))["status"] if state["days"][day]["dispatches"] else "complete")
    monkeypatch.setattr(cloud, "write_state_file", write)
    monkeypatch.setattr(cloud.subprocess, "run", lambda *a, **kw: events.append("api"))
    payload = normal()
    assert cloud.finish_retry(app_settings, state, day, "101:1", cloud.time.monotonic(), payload, 0) == 0
    assert events == ["complete", "intent", "api", "dispatched"]
    assert payload["retry_status"] == "dispatched"


@pytest.mark.parametrize("failure", ["persist", "dispatch"])
def test_retry_persistence_or_dispatch_failure_never_sends_again(app_settings, monkeypatch, failure):
    state, day = reserved()
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    writes = []
    def write(value, *a):
        writes.append(value)
        if failure == "persist" and len(writes) == 2:
            raise OSError("state unavailable")
    sender = MagicMock(side_effect=TimeoutError("uncertain") if failure == "dispatch" else None)
    monkeypatch.setattr(cloud, "write_state_file", write)
    monkeypatch.setattr(cloud.subprocess, "run", sender)
    with pytest.raises((OSError, TimeoutError)):
        cloud.finish_retry(app_settings, state, day, "101:1", cloud.time.monotonic(), normal(), 0)
    assert sender.call_count == (1 if failure == "dispatch" else 0)
    assert writes[-1]["days"][day]["dispatches"]["101:1"]["status"] == "intent"


def test_seed_previous_runs_not_checks_or_skips_and_prove_legacy_duration(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"run_key": "11:1", "mode": "publish", "started_at": now},
            {"run_key": "12:1", "mode": "check", "started_at": now},
            {"run_key": "13:1", "mode": "publish", "excluded": True, "started_at": now}]
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/b")
    fetch = MagicMock(return_value=MagicMock(stdout=json.dumps({"jobs": [{"started_at": "2026-10-06T00:00:00Z", "completed_at": "2026-10-06T00:10:00Z"}]})))
    monkeypatch.setattr(cloud.subprocess, "run", fetch)
    state = cloud.seed_retry_day({}, {"runs": rows}, retry.day_of(now))
    entry = state["days"][retry.day_of(now)]
    assert list(entry["runs"]) == ["11:1"] and retry.remaining(entry) == 4800
    fetch.assert_called_once()


def test_rollout_with_three_existing_runs_skips_writer_and_persists_usage(app_settings, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"run_key": f"{i}:1", "mode": "publish", "started_at": now, "elapsed_seconds": 300} for i in range(3)]
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "verify_pending", MagicMock(return_value={}))
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw: {"schema": 1, "runs": rows} if kw.get("path") == metrics.METRICS_PATH else {})
    writer = MagicMock()
    monkeypatch.setattr(cloud, "write_state_file", writer)
    child = MagicMock(side_effect=AssertionError("Daily cap prohibits generation"))
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.main(["--mode", "publish"]) == 0
    child.assert_not_called()
    payload = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert payload["daily_run_count"] == 3 and payload["retry_status"] == "daily_budget_exhausted"
    assert payload["native_exit_code"] is None
    saved_metrics = [call.args[0] for call in writer.call_args_list if call.args[1] == metrics.METRICS_PATH][-1]
    stopped = next(row for row in saved_metrics["runs"] if row["run_key"] == "99:1")
    assert stopped["outcome"] == "not_started" and stopped["excluded"] is True
    assert stopped["native_exit_code"] is None
    assert metrics.summary(saved_metrics)["7"]["actual_publish_runs"] == 3
    assert any(call.args[1] == retry.STATE_PATH for call in writer.call_args_list)


def test_only_wechat_model_deadline_limits_request(app_settings, logger, monkeypatch):
    enable(app_settings)
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(cloud.time.time() - 1))
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    service.client = MagicMock()
    with pytest.raises(AIRequestError, match="deadline"):
        service._call_with_retry(model="test", messages=[])
    service.client.chat.completions.create.assert_not_called()
    object.__setattr__(app_settings, "platform", "toutiao")
    service._call_with_retry(model="test", messages=[])
    assert "timeout" not in service.client.chat.completions.create.call_args.kwargs


def test_workflow_summary_compiles_and_retains_crons_permissions_and_artifact_allowlist():
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/wechat-daily-run.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert [row["cron"] for row in triggers["schedule"]] == ["17 23 * * *", "17 5 * * *"]
    assert workflow["permissions"] == {"contents": "write", "actions": "write"}
    steps = workflow["jobs"]["wechat"]["steps"]
    summary = next(row["run"] for row in steps if row.get("name") == "Summarize actual outcome")
    compile(summary.split("\n", 1)[1].rsplit("\nPY", 1)[0], "workflow-summary", "exec")
    upload = next(row for row in steps if row.get("name") == "Save outcome reports")
    assert "browser_state" not in upload["with"]["path"] and "source_material" not in upload["with"]["path"]


def validation_state():
    now = datetime.now(timezone.utc)
    day = retry.day_of(now.isoformat())
    state = {"schema_version": 1, "days": {day: {"runs": {str(i): {"status": "completed", "elapsed_seconds": 300}
            for i in range(3)}, "dispatches": {}, "failed_inputs": [], "manual_validation_grants": {
            "once": {"status": "authorized", "purpose": "verify_formal_publication", "head_sha": "approved-sha",
                     "expires_at": (now + timedelta(hours=1)).isoformat()}}}}}
    return state, day, now.isoformat()


def test_one_use_validation_preserves_counts_budget_and_normal_limit():
    state, day, now = validation_state()
    assert retry.reserve(state, day, "4:1", now)[1] == "daily_budget_exhausted"
    state, result = retry.reserve(state, day, "4:1", now, validation_id="once", head_sha="approved-sha")
    assert result == "reserved" and len(state["days"][day]["runs"]) == 4
    assert state["days"][day]["manual_validation_grants"]["once"]["run_key"] == "4:1"
    state = retry.complete(state, day, "4:1", 400, normal(), {})
    assert retry.remaining(state["days"][day]) == 4100
    assert state["days"][day]["manual_validation_grants"]["once"]["status"] == "consumed"
    assert retry.reserve(state, day, "5:1", now, validation_id="once", head_sha="approved-sha")[1] == "invalid_validation_grant"
    assert retry.reserve(state, day, "5:1", now)[1] == "daily_budget_exhausted"
    assert not retry.can_relay(state["days"][day], normal(), 0)


@pytest.mark.parametrize("case", ["unknown", "wrong_sha", "expired", "wrong_purpose", "consumed", "budget", "running"])
def test_manual_validation_never_bypasses_other_safety_boundaries(case):
    state, day, now = validation_state()
    grant = state["days"][day]["manual_validation_grants"]["once"]
    validation_id, head_sha = "once", "approved-sha"
    if case == "unknown": validation_id = "unknown"
    if case == "wrong_sha": head_sha = "different-sha"
    if case == "expired": grant["expires_at"] = (datetime.fromisoformat(now) - timedelta(seconds=1)).isoformat()
    if case == "wrong_purpose": grant["purpose"] = "bypass_review"
    if case == "consumed": grant["status"] = "consumed"
    if case == "budget": state["days"][day]["runs"]["0"]["elapsed_seconds"] = 5400
    if case == "running": state["days"][day]["runs"]["0"]["status"] = "running"
    if case == "running":
        with pytest.raises(RuntimeError): retry.reserve(state, day, "4:1", now, validation_id=validation_id, head_sha=head_sha)
    else:
        assert retry.reserve(state, day, "4:1", now, validation_id=validation_id, head_sha=head_sha)[1] != "reserved"


def test_validation_continuation_requires_verified_preparation_failure_and_fresh_authorization():
    state, day, now = validation_state()
    state, _ = retry.reserve(state, day, "4:1", now, validation_id="once", head_sha="approved-sha")
    state = retry.complete(state, day, "4:1", 100, {"status": "failed"}, {})
    grant = state["days"][day]["manual_validation_grants"]["once"]
    grant.update(status="authorized", resume_after_preparation_failure="4:1")
    assert retry.reserve(state, day, "5:1", now, validation_id="once", head_sha="approved-sha")[1] == "validation_already_used"
    state["days"][day]["runs"]["4:1"]["preparation_failure_verified"] = True
    resumed, status = retry.reserve(state, day, "5:1", now, validation_id="once", head_sha="approved-sha")
    assert status == "reserved" and len(resumed["days"][day]["runs"]) == 5
    assert retry.remaining(resumed["days"][day]) == 4400


def test_wechat_refund_consumer_keywords_do_not_enter_technology_enrichment(app_settings, logger):
    from toutiao_content_factory.services.topic_collection import TopicCollectionService
    from toutiao_content_factory.models import TopicCandidate
    enable(app_settings)
    collector = TopicCollectionService(app_settings, logger)
    unrelated = TopicCandidate("“退款成功”四个字不该成为套路", "", "rss", "rss", matched_keywords=["平台", "消费者", "监管"])
    assert not collector._has_concrete_industry_keyword(unrelated)
    assert collector._has_concrete_industry_keyword(TopicCandidate("陕西风光新规让储能重回硬指标", "", "rss", "rss"))
    object.__setattr__(app_settings, "platform", "toutiao")
    assert collector._has_concrete_industry_keyword(unrelated)


@pytest.mark.parametrize("phase,account,expected", [("preparing", "default", False), ("submitting", "default", True), ("preparing", "other", True)])
def test_failed_run_phase_proves_no_submit_without_changing_real_failure(app_settings, phase, account, expected):
    import time
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    started = time.time_ns()
    (app_settings.automation_dir / "wechat_publish_phase.json").write_text(json.dumps({"account_key": account,
        "phase": phase, "publication_attempted": phase != "preparing"}), encoding="utf-8")
    diagnostic = review_diagnostics(app_settings, {"status": "failed"}, started)
    assert diagnostic["publication_attempted"] is expected
    assert cloud_exit_code({"status": "failed", "native_exit_code": 1, "publication_attempted": expected}, 1) == 1


def test_material_budget_is_distinct_from_system_failure(monkeypatch):
    from toutiao_content_factory.services import material_search_service as material
    import time
    monkeypatch.setattr(material, "search_web", lambda *a, **kw: [{"title": "机器人发布", "url": "https://a.test/report", "snippet": ""}])
    monkeypatch.setattr(material, "fetch_text_smart", MagicMock(side_effect=material.MaterialBudgetExceeded("deadline")))
    with pytest.raises(material.MaterialBudgetExceeded):
        material.collect_material("机器人发布", preserve_sources=True, deadline=time.monotonic()+60)
    monkeypatch.setattr(material, "fetch_text_smart", MagicMock(side_effect=RuntimeError("browser crashed")))
    with pytest.raises(material.MaterialSearchError):
        material.collect_material("机器人发布", preserve_sources=True, deadline=time.monotonic()+60)


def test_lazy_enrichment_stops_before_unused_source_failure(app_settings, logger, monkeypatch):
    from toutiao_content_factory.services.app_service import ContentFactoryAppService
    from toutiao_content_factory.services.topic_collection import TopicCollectionService, TopicCandidate
    from toutiao_content_factory.services.material_search_service import MaterialSearchError
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    topics = [TopicCandidate("机器人新产品发布" + str(i), "https://a.test/" + str(i), "rss", "rss",
        published_at=datetime.now(metrics.BEIJING).isoformat(), matched_keywords=["机器人"]) for i in range(2)]
    def hydrate(topic, **kwargs):
        if topic is topics[1]: raise MaterialSearchError("unused source failed")
        topic.snippet = "机器人产品已完成测试。" * 100
        topic.source_records = [{"source_id": "source-1"}]
        return True
    def score(self, topic):
        topic.evidence_strength = topic.quality_score = 80
        topic.quality_flags = []
    hydrator = MagicMock(side_effect=hydrate)
    monkeypatch.setattr(app.topic_automation, "_hydrate_topic_material", hydrator)
    monkeypatch.setattr(TopicCollectionService, "_enrich_topic", score)
    monkeypatch.setattr(TopicCollectionService, "_passes_topic_focus", lambda *args: True)
    app._enrich_wechat_candidates(topics)
    assert hydrator.call_count == 2
    # If the writer rejects the ready article, the next source really is needed.
    # Its system failure must still propagate, not turn into insufficient evidence.
    with pytest.raises(MaterialSearchError): app._enrich_wechat_candidates(topics[1:])


def test_lazy_enrichment_budget_excludes_time_spent_writing(app_settings, logger):
    import time
    from toutiao_content_factory.services.app_service import ContentFactoryAppService
    from toutiao_content_factory.services.topic_collection import TopicCandidate
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    app._wechat_enrichment_deadline = time.monotonic() - 100
    app._wechat_enrichment_remaining_seconds = 40
    app._wechat_enrichment_count = 0
    app._wechat_enriched_topics = {}
    def hydrate(topic, **kwargs):
        assert time.monotonic() < kwargs["deadline"] <= time.monotonic() + 40
        return False
    app.topic_automation._hydrate_topic_material = MagicMock(side_effect=hydrate)
    topic = TopicCandidate("机器人新产品发布", "https://a.test", "rss", "rss", published_at=datetime.now(metrics.BEIJING).isoformat())
    app._enrich_wechat_candidates([topic])
    app.topic_automation._hydrate_topic_material.assert_called_once()
    assert 0 < app._wechat_enrichment_remaining_seconds <= 40


def test_lazy_candidates_exclude_attempted_titles_and_keep_unique_eligible_count(app_settings, logger):
    from toutiao_content_factory.services.app_service import ContentFactoryAppService
    from toutiao_content_factory.services.topic_collection import TopicCandidate
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    topics = [TopicCandidate("机器人产品发布" + str(i), "https://a.test/" + str(i), "rss", "rss",
        snippet="机器人产品已经完成测试。" * 100, source_records=[{"source_id": str(i)}], evidence_strength=80, quality_score=80) for i in range(2)]
    app.topic_automation._load_daily_topics = MagicMock(return_value=MagicMock(topics=topics))
    app.topic_automation._available_topics = MagicMock(return_value=topics)
    initial = app._build_daily_candidate_specs(topic_rank=None, topic_title=None, refresh_cache=False, dry_run=False)
    assert len(initial) == 2
    app._wechat_attempted_candidate_titles = {topics[0].title}
    later = app._build_daily_candidate_specs(topic_rank=None, topic_title=None, refresh_cache=False, dry_run=False)
    assert [row["title"] for row in later] == [topics[1].title]
    assert app._wechat_eligible_candidate_count == 2


def test_writer_receives_actual_outline_and_only_accepts_bound_sections(app_settings, logger):
    enable(app_settings)
    model = MagicMock(available=True)
    data = report()
    evidence.build_outline(data)
    def completion(system_prompt, user_prompt, **kwargs):
        prompt = user_prompt
        for section in data.writing_outline:
            assert section["section"] in prompt
            assert section["fact_ids"][0] in prompt
        return {"sections": [{"section": data.writing_outline[0]["section"],
            "fact_ids": data.writing_outline[0]["fact_ids"], "text": "陕西要求储能项目与配套新能源项目同期投产。"}]}
    model.create_json_completion.side_effect = completion
    writer = WriterAgent(app_settings, MagicMock(), model, logger)
    result = writer.run(TrendReport(), data, app_settings.get_style(), CacheContext())
    assert "同期投产" in result.article


def test_wechat_metadata_uses_platform_publication_not_collection_date():
    page = '<div id="js_content">截至2025年底装机变化</div><a id="js_name">科技账号</a><script>var ct = "1791259200";</script>'
    metadata = evidence.source_metadata(page, source_url="https://mp.weixin.qq.com/s/public")
    assert metadata["publisher"] == "科技账号"
    assert metadata["date_verified"] is True
    assert metadata["published_at"] == datetime.fromtimestamp(1791259200, metrics.BEIJING).isoformat()
    assert not evidence.source_metadata(page, source_url="https://other.test/page")["date_verified"]


def test_structured_article_metadata_and_ambiguity():
    payload = {"@type": ["NewsArticle"], "datePublished": "2026-10-05", "dateModified": "2026-10-06", "publisher": {"name": "公开发布机构"}}
    page = '<script type="application/ld+json">' + json.dumps(payload) + '</script><p>截至2025年底</p>'
    metadata = evidence.source_metadata(page)
    assert metadata["published_at"].startswith("2026-10-05")
    assert metadata["publisher"] == "公开发布机构"
    payload = [payload, {"@type": "Article", "datePublished": "2020-01-01"}]
    assert not evidence.source_metadata('<script type="application/ld+json">' + json.dumps(payload) + '</script>')["date_verified"]


def test_citation_restore_does_not_readd_repeated_prefixes():
    data = report()
    body = "陕西要求储能项目与配套新能源项目同期投产。\n\n相关储能项目与配套新能源项目同期投产，适用条件按通知执行。"
    first = evidence.prepare_article(body, data)
    second = evidence.prepare_article(first, data)
    assert first.count("据《陕西储能通知》报道") == 1
    assert second == first
    assert "参考来源：" in second


def test_single_quoted_review_excerpts_bind_despite_whitespace():
    from toutiao_content_factory.services.wechat_revision import bind_issues
    body = "一个百兆瓦级光伏电站配两成储能，仍需核对具体条件。"
    audit = QualityReviewReport(logic_issues=["核实‘一个百兆瓦级 光伏电站 配两成储能’，不要外推。"])
    bind_issues(audit, body, [])
    assert audit.issues[0]["paragraph"] == body
    assert audit.issues[0]["location_verified"] is True


def test_validation_review_hold_continuation_requires_separate_no_submission_proof():
    state, day, now = validation_state()
    state, _ = retry.reserve(state, day, "4:1", now, validation_id="once", head_sha="approved-sha")
    state = retry.complete(state, day, "4:1", 300, normal(), {})
    entry = state["days"][day]
    grant = entry["manual_validation_grants"]["once"]
    grant.update(status="authorized", resume_after_not_published="4:1")
    assert retry.reserve(state, day, "5:1", now, validation_id="once", head_sha="approved-sha")[1] == "validation_already_used"
    entry["runs"]["4:1"]["no_submission_verified"] = True
    resumed, status = retry.reserve(state, day, "5:1", now, validation_id="once", head_sha="approved-sha")
    assert status == "reserved"
    assert resumed["days"][day]["runs"]["4:1"]["elapsed_seconds"] == 300


def test_ofweek_header_date_excludes_related_article_dates():
    page = '<div class="artical-relative clearfix"><div class="time fl">2026-07-30 15:07</div><a href="https://mp.ofweek.com/account">来源账号\n<span>关注</span></a></div><span class="entries-time">2026-10-06</span>'
    metadata = evidence.source_metadata(page, source_url="https://chuneng.ofweek.com/news/article")
    assert metadata["published_at"] == "2026-07-30T15:07:00"
    assert metadata["publisher"] == "来源账号"
    assert not evidence.source_metadata(page, source_url="https://other.test")["date_verified"]


def test_article_header_date_not_statistics_or_related_link():
    page = '<span id="pubtime_baidu">2026/10/05 12:13</span><span id="source_baidu">IT之家</span><p>截至2025年底统计</p>'
    metadata = evidence.source_metadata(page)
    assert metadata["published_at"] == "2026-10-05T12:13:00"
    assert metadata["publisher"] == "IT之家"


def test_thepaper_header_preserves_real_publication_not_current_hot_topic(app_settings, logger):
    from collections import defaultdict
    from toutiao_content_factory.services.topic_collection import TopicCandidate, TopicCollectionService
    page = '<div class="header"><div id="title">新能源汽车维修为啥又贵又难？</div><div class="info link"><img/><span>媒体：经济日报&nbsp;2026-06-03 17:49</span></div></div><article>截至2025年底保有量4397万辆。</article><div class="info link">媒体：另一账号 2026-10-06 15:00</div>'
    metadata = evidence.source_metadata(page, source_url="https://m.thepaper.cn/baijiahao_33300881")
    assert metadata["publisher"] == "经济日报"
    assert metadata["published_at"] == "2026-06-03T17:49:00"
    assert metadata["page_title"] == "新能源汽车维修为啥又贵又难？"
    enable(app_settings)
    topic = TopicCandidate(metadata["page_title"], "", "rss", "rss", published_at=metadata["published_at"])
    assert not TopicCollectionService(app_settings, logger)._filter_by_freshness([topic], defaultdict(int))
    assert not evidence.source_metadata(page, source_url="https://other.test/article")["date_verified"]


@pytest.mark.parametrize("page", [
    '<article><div class="info link">媒体：经济日报 2026-10-06 15:00</div></article>',
    '<div class="header"><div class="info link">媒体：经济日报 日期未知</div></div><article>截至2025年底。</article>',
])
def test_thepaper_unknown_header_never_uses_prose_or_related_date(page):
    assert not evidence.source_metadata(page, source_url="https://m.thepaper.cn/baijiahao_1")["date_verified"]


def test_grounding_removes_only_exact_repeated_sentences_and_keeps_distinct_facts():
    records = evidence.source_records([MaterialSource("科技测试", "https://a.test/report",
        "机器人公司完成产品测试，实际验证范围仍需进一步核对。芯片公司公布测试结果，实际配置和性能尚待确认。", True)])
    data = DataReport(source_records=records, fact_evidence=evidence.build_facts(records))
    first, second = [fact["quote"] for fact in data.fact_evidence[:2]]
    article = first + "\n\n" + first + second
    grounded = evidence.prepare_article(article, data)
    body = grounded.split("参考来源：", 1)[0]
    assert body.count(first) == 1
    assert second in body
    assert evidence.prepare_article(grounded, data) == grounded


def test_grounding_does_not_turn_expert_opinion_into_implemented_policy():
    source = MaterialSource("维修观察", "https://a.test/report", "专家认为，未来新能源汽车维修行业将逐步开放技术数据。", True)
    records = evidence.source_records([source]);facts = evidence.build_facts(records)
    data = DataReport(topic_title="新能源汽车维修", source_records=records, fact_evidence=facts, article_kind="industry_update")
    assertion = "未来新能源汽车维修行业将逐步开放技术数据。"
    assert evidence.supporting_facts(assertion, facts)
    assert assertion not in evidence.prepare_article(assertion, data).split("参考来源：", 1)[0]
    qualified = "专家认为，未来新能源汽车维修行业将逐步开放技术数据。"
    assert qualified in evidence.prepare_article(qualified, data)


def test_estimate_keeps_original_uncertainty_even_when_numbers_match():
    source = MaterialSource("维修市场", "https://a.test/report", "机构预测，2029年新能源汽车维修市场规模达到3万亿元。", True)
    records = evidence.source_records([source]);facts = evidence.build_facts(records)
    data = DataReport(source_records=records, fact_evidence=facts)
    assertion = "2029年新能源汽车维修市场规模达到3万亿元。"
    assert evidence.supporting_facts(assertion, facts)
    assert assertion not in evidence.prepare_article(assertion, data).split("参考来源：", 1)[0]
    assert source.body in evidence.prepare_article(source.body, data)


def test_later_forecast_does_not_turn_established_data_into_a_forecast():
    source = MaterialSource("机器人测试进展", "https://a.test/report", "机器人公司目前已完成20台设备测试，预计下一批设备将进入验证阶段。", True)
    records = evidence.source_records([source]);facts = evidence.build_facts(records)
    data = DataReport(source_records=records, fact_evidence=facts)
    actual = "机器人公司目前已完成20台设备测试。"
    assert evidence.supporting_facts(actual, facts)
    assert actual in evidence.prepare_article(actual, data)


def test_stable_outline_id_preserves_binding_and_rejects_unknown_evidence(app_settings, mock_openai_service, logger):
    enable(app_settings)
    data = report(); evidence.build_outline(data)
    writer = WriterAgent(app_settings, MagicMock(), mock_openai_service, logger)
    writer.request_json = MagicMock(return_value={"sections": [
        {"section_id": 0, "section": "改写了节名", "fact_ids": data.writing_outline[0]["fact_ids"], "text": "陕西储能措施应核对明确的适用条件。"},
        {"section_id": 1, "fact_ids": ["unknown"], "text": "错误事实不应进入正文。"}]})
    result = writer.run(TrendReport(), data, app_settings.get_style(), CacheContext())
    assert "适用条件" in result.article
    assert "错误事实" not in result.article
    assert result.grounding_diagnostics[0]["accepted_section_count"] == 1
    assert result.grounding_diagnostics[0]["returned_section_count"] == 2
    prompt = writer.request_json.call_args.args[1]
    schema = json.loads(prompt.split("每节text替换为约350字正文，fact_ids只保留该节实际使用的绑定事实编号：", 1)[1].split("\n结尾", 1)[0])
    assert [section["section_id"] for section in schema["sections"]] == list(range(6))
    assert [section["fact_ids"] for section in schema["sections"]] == [section["fact_ids"] for section in data.writing_outline]


@pytest.mark.parametrize("value,expected", [("2026/10/6 11:38:47", "2026-10-06T11:38:47"), ("2026年10月6日 11:38", "2026-10-06T11:38:00"), ("2026/10/6", "2026-10-06")])
def test_publication_timestamp_preserves_explicit_time_and_date_precision(value, expected):
    metadata = evidence.source_metadata('<span id="pubtime_baidu">' + value + '</span>')
    assert metadata["published_at"] == expected
    assert metadata["date_verified"] is True


def test_today_article_not_stale_after_time_preservation(app_settings, logger, monkeypatch):
    from collections import defaultdict
    import toutiao_content_factory.services.topic_collection as topic_module
    from toutiao_content_factory.services.topic_collection import TopicCandidate, TopicCollectionService
    enable(app_settings)
    object.__setattr__(app_settings.topic_collection, "max_topic_age_hours", 12)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = datetime(2026, 10, 6, 5, 12, tzinfo=timezone.utc)
            return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)
    monkeypatch.setattr(topic_module, "datetime", FixedDatetime)
    metadata = evidence.source_metadata('<span id="pubtime_baidu">2026/10/6 11:38:47</span>')
    collector = TopicCollectionService(app_settings, logger)
    # Chinese publication header time is explicitly interpreted in Beijing.
    topic = TopicCandidate("人工智能大模型新品发布", "", "rss", "rss", published_at=metadata["published_at"] + "+08:00")
    assert collector._filter_by_freshness([topic], defaultdict(int)) == [topic]
    topic.published_at = "2026-10-06T00:00:00+08:00"
    assert not collector._filter_by_freshness([topic], defaultdict(int))


@pytest.mark.parametrize("verified,matching,platform,expected", [
    (True, True, "wechat", False), (False, True, "wechat", True),
    (True, False, "wechat", True), (True, True, "toutiao", True),
])
def test_full_original_body_rescored_without_preview_length_heuristics(app_settings, logger, verified, matching, platform, expected):
    from toutiao_content_factory.services.topic_collection import TopicCandidate, TopicCollectionService
    from toutiao_content_factory.services.material_search_service import MaterialBundle
    enable(app_settings)
    object.__setattr__(app_settings, "platform", platform)
    body = "\n".join(f"芯片公司发布第{i}项处理器测试，测试条件由公司披露，结果为{i + 10}台，独立验证情况尚未公开。" for i in range(30))
    bundle = MaterialBundle("芯片进展", [MaterialSource("处理器报道", "https://a.test/report", body, True)])
    topic = TopicCandidate("芯片处理器测试发布", "https://a.test/report", "rss", "rss", snippet=bundle.combined_text,
        source_records=evidence.source_records(bundle.usable))
    topic._verified_source_material = verified
    if not matching:
        topic.snippet += "未在原文披露的额外断言。"
    collector = TopicCollectionService(app_settings, logger)
    collector._enrich_topic(topic)
    assert collector._is_title_only_topic(topic) is expected
    assert ("title_only" in topic.quality_flags) is expected


def test_original_body_does_not_bypass_page_safety_or_material_gate(app_settings, logger):
    from toutiao_content_factory.services.topic_collection import TopicCandidate, TopicCollectionService
    from toutiao_content_factory.services.material_search_service import MaterialBundle
    enable(app_settings)
    collector = TopicCollectionService(app_settings, logger)
    for body in ["芯片发布完成10台测试。", "copyright 当前位置 " + "芯片公司披露20台测试，条件尚未核对。" * 80]:
        bundle = MaterialBundle("芯片进展", [MaterialSource("处理器报道", "https://a.test/report", body, True)])
        topic = TopicCandidate("芯片进展", "https://a.test/report", "rss", "rss", snippet=bundle.combined_text,
            source_records=evidence.source_records(bundle.usable))
        topic._verified_source_material = True
        assert collector._is_title_only_topic(topic)


@pytest.mark.parametrize("keyword", ["处理器", "人工智能", "大模型", "智能体"])
def test_wechat_ai_vocabulary_matches_existing_track_without_mutating_toutiao(app_settings, logger, keyword):
    from copy import deepcopy
    from toutiao_content_factory.services.topic_collection import TopicCollectionService
    object.__setattr__(app_settings.topic_collection, "keyword_map", {"ai_hardware": ["芯片", "GPU"]})
    original = deepcopy(app_settings.topic_collection.keyword_map)
    enable(app_settings)
    collector = TopicCollectionService(app_settings, logger)
    industries, words = collector._match_industries(keyword + "产品更新")
    assert "ai_hardware" in industries and keyword in words
    assert app_settings.topic_collection.keyword_map == original
    object.__setattr__(app_settings, "platform", "toutiao")
    assert keyword not in collector._match_industries(keyword + "产品更新")[1]


@pytest.mark.parametrize("quote,expected", [
    ("英特尔规定新处理器型号处于封闭测试组，首发计划尚未获官方确认。", "product_progress"),
    ("陕西省发布储能通知，规定项目适用条件与配套实施要求。", "policy_explainer"),
    ("公司规定会议次序，具体内容仍需等待核实。", "industry_update"),
])
def test_internal_product_rules_are_not_government_policy(quote, expected):
    facts = evidence.build_facts(evidence.source_records([MaterialSource("公开报道", "https://a.test/report", quote, True)]))
    assert evidence.classify_kind("policy_market", facts) == expected


def test_product_requirements_keep_undisclosed_performance_unknown():
    brief = evidence.requirements("product_progress")
    for key in ["suggested_angle", "writing_brief", "instruction"]:
        assert "配置、性能、基准测试或独立验证未披露时明确保持未知" in brief[key]
        assert "保留报道归因" in brief[key]


def test_sampling_policy_is_manufacturer_product_context():
    facts = evidence.build_facts(evidence.source_records([MaterialSource("处理器进展", "https://a.test/report",
        "英特尔处理器首发型号尚待确认。公开渠道罕见BFC硬件，可能源于抽样政策而非供货问题。", True)]))
    assert evidence.classify_kind("policy_market", facts) == "product_progress"


@pytest.mark.parametrize("after", ["", "配置与性能尚未披露。"])
def test_polish_cannot_discard_bound_original_facts(app_settings, mock_openai_service, logger, after):
    enable(app_settings)
    data = report()
    original = data.fact_evidence[0]["quote"]
    agent = PolishAgent(app_settings, MagicMock(), mock_openai_service, logger)
    agent.openai_service.available = True
    agent.openai_service.create_json_completion = MagicMock(return_value={"patches":[{"paragraph_id":0,"before":original,"after":after}]})
    result = agent.run(GeneratedContent(article=original), TrendReport(), data, app_settings.get_style(), CacheContext(), ["收窄结论"])
    assert original in result["polished_article"]


def test_ascii_quoted_review_issue_binds_only_actual_body():
    from toutiao_content_factory.services.wechat_revision import bind_issues
    from toutiao_content_factory.models import QualityReviewReport
    body = "英特尔公布处理器测试条件尚待核实。"
    audit = QualityReviewReport(fact_issues=["核对 '英特尔公布处理器测试条件尚待核实' 的来源。", "核对 '不存在的另一段原文事实' 的出处。"])
    bind_issues(audit, body, [])
    assert audit.issues[0]["location_verified"] is True
    assert audit.issues[1]["location_verified"] is False


def test_polish_applies_source_preserving_narrowing(app_settings, mock_openai_service, logger):
    enable(app_settings)
    data = report()
    original = data.fact_evidence[0]["quote"]
    revised = original + "适用条件仍需按通知核对，不据此推导其他地区的结论。"
    agent = PolishAgent(app_settings, MagicMock(), mock_openai_service, logger)
    agent.openai_service.available = True
    agent.openai_service.create_json_completion = MagicMock(return_value={"patches":[{"paragraph_id":0,"before":original,"after":revised}]})
    result = agent.run(GeneratedContent(article=original), TrendReport(), data, app_settings.get_style(), CacheContext(), ["收窄结论"])
    assert original in result["polished_article"]
    assert "不据此推导其他地区的结论" in result["polished_article"]


def test_authorized_budget_extension_keeps_history_and_next_day_default():
    state, day = reserved()
    state = retry.complete(state, day, "101:1", 5268.198, normal(), {})
    entry = state["days"][day]
    entry["budget_extension"] = {"day": day, "purpose": "verify_formal_publication", "additional_seconds": 1800,
        "authorized_at": datetime.now(timezone.utc).isoformat(), "authorization": "User explicitly authorized thirty additional minutes today"}
    checked = retry.validate(state)
    assert retry.budget_seconds(checked["days"][day]) == 7200
    assert retry.remaining(checked["days"][day]) == pytest.approx(1931.802)
    assert checked["days"][day]["runs"]["101:1"]["elapsed_seconds"] == 5268.198
    tomorrow = (datetime.now(retry.BEIJING) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert retry.budget_seconds(retry.today_entry(checked, tomorrow)) == 5400
    assert retry.MAX_RUNS == 3


def test_additional_budget_grant_is_idempotent_and_preserves_usage():
    from copy import deepcopy
    state, day = reserved()
    state = retry.complete(state, day, "101:1", 6942.131, normal(), {})
    entry = state["days"][day]
    first = {"day": day, "purpose": "verify_formal_publication", "additional_seconds": 1800,
        "authorized_at": datetime.now(timezone.utc).isoformat(), "authorization": "First explicit thirty-minute authorization"}
    entry["budget_extension"] = first
    original_runs = deepcopy(entry["runs"])
    second = {**first, "authorization": "Second explicit thirty-minute authorization"}
    for _ in range(2):
        entry.setdefault("additional_budget_grants", {})["same-user-authorization"] = second
        checked = retry.validate(state)
        assert retry.budget_seconds(checked["days"][day]) == 9000
        assert retry.remaining(checked["days"][day]) == pytest.approx(2057.869)
    assert entry["budget_extension"] == first and entry["runs"] == original_runs
    tomorrow = (datetime.now(retry.BEIJING) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert retry.budget_seconds(retry.today_entry(checked, tomorrow)) == 5400
    assert retry.MAX_RUNS == 3 and retry.MAX_SECONDS == 5400


@pytest.mark.parametrize("grants", [[], {"": {}}, {" ": {}}, {"authorization": False}])
def test_additional_budget_grant_rejects_invalid_identity_or_shape(grants):
    state, day = reserved()
    state["days"][day]["additional_budget_grants"] = grants
    with pytest.raises(ValueError):
        retry.validate(state)


@pytest.mark.parametrize("change", [{"day": "2000-01-01"}, {"authorization": ""},
    {"additional_seconds": 3600}, {"additional_seconds": True}, {"purpose": "automatic_retry"},
    {"authorized_at": None}, {"authorized_at": "2026-10-06T08:00:00"}])
def test_additional_budget_grant_requires_dated_explicit_authority(change):
    state, day = reserved()
    state["days"][day]["additional_budget_grants"] = {"authorization": {
        "day": day, "purpose": "verify_formal_publication", "additional_seconds": 1800,
        "authorized_at": datetime.now(timezone.utc).isoformat(), "authorization": "Explicit user authorization", **change}}
    with pytest.raises(ValueError):
        retry.validate(state)


@pytest.mark.parametrize("change", [{"status": "error"}, {"publication_attempted": True},
    {"retry_status": "invalid_validation_grant"}, {"native_exit_code": 124},
    {"metrics_error": "OSError"}, {"retry_state_error": "RuntimeError"}])
def test_budget_stop_does_not_mask_real_faults(change):
    stopped = {"status": "daily_retry_stopped", "retry_status": "daily_budget_exhausted",
               "publication_attempted": False, "native_exit_code": None, **change}
    assert metrics.outcome(stopped) == "runtime_error"


def test_budget_stop_preserves_platform_submission_state():
    stopped = {"status": "daily_retry_stopped", "retry_status": "daily_budget_exhausted",
               "publication_attempted": False, "native_exit_code": None}
    assert metrics.outcome(stopped) == "not_started"
    assert metrics.outcome({**stopped, "wechat_status": "submitted_for_review"}) == "pending_review"
    assert metrics.outcome({**stopped, "wechat_status": "publish_uncertain"}) == "submit_uncertain"


@pytest.mark.parametrize("change", [{"additional_seconds": True}, {"additional_seconds": 1801},
    {"authorization": ""}, {"purpose": "automatic_retry"}, {"day": "2000-01-01"}])
def test_budget_extension_rejects_invalid_or_cross_day_authority(change):
    state, day = reserved()
    state["days"][day]["budget_extension"] = {"day": day, "purpose": "verify_formal_publication", "additional_seconds": 1800,
        "authorized_at": datetime.now(timezone.utc).isoformat(), "authorization": "Explicit user authorization", **change}
    with pytest.raises(ValueError):
        retry.validate(state)


def test_wechat_expansion_excludes_unverified_legacy_analysis(app_settings, mock_openai_service, logger):
    enable(app_settings)
    data = report()
    data.article_kind = "product_progress"
    data.source_facts = data.evidence_notes = data.risk_flags = ["未核实利润将增长99%"]
    writer = WriterAgent(app_settings, MagicMock(), mock_openai_service, logger)
    writer._has_grounded_evidence = MagicMock(return_value=True)
    writer.openai_service.available = True
    writer.openai_service.create_json_completion = MagicMock(return_value={"paragraphs": []})
    writer._expand_article_via_llm(["已有正文。"], data, min_chars=2100)
    prompt = writer.openai_service.create_json_completion.call_args.kwargs["user_prompt"]
    assert "未核实利润将增长99%" not in prompt
    assert data.fact_evidence[0]["quote"] in prompt
    assert "Only explain the measures, region, project types" not in prompt
    assert "稿件类型：产品进展" in prompt


@pytest.mark.parametrize("platform", ["wechat", "toutiao"])
def test_review_prompt_fact_authority_is_channel_scoped(app_settings, logger, platform):
    from toutiao_content_factory.agents.quality_review_council_agent import QualityReviewCouncilAgent
    enable(app_settings)
    object.__setattr__(app_settings, "platform", platform)
    repo = MagicMock()
    repo.render.return_value = "review prompt"
    ai = MagicMock()
    ai.available = True
    ai.create_json_completion.return_value = {"verdict": "needs_revision", "overall_score": 60}
    data = report()
    data.key_points = data.source_facts = ["未核实利润将增长99%"]
    agent = QualityReviewCouncilAgent(app_settings, repo, ai, logger)
    agent.run(GeneratedContent(article="原文规定相关项目与新能源项目同期投产。"), data, app_settings.get_style(), CacheContext())
    payload = json.loads(repo.render.call_args.kwargs["data_json"])
    if platform == "wechat":
        assert set(payload) == {"topic_title", "article_kind", "source_records", "fact_evidence"}
        assert "未核实利润将增长99%" not in json.dumps(payload, ensure_ascii=False)
    else:
        assert payload["source_facts"] == data.source_facts
