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
    service.quality_review_council_agent.run = MagicMock(return_value=QualityReviewReport(verdict="needs_revision", required_revisions=["改范围"]))
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
    assert retry.can_relay(state["days"][day], payload, 0)
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
