from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.agents.title_agent import TitleAgent
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TopicCandidate
from toutiao_content_factory.services import wechat_evidence as evidence
from toutiao_content_factory.services import material_search_service as material
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.company_entities import extract_wechat_company_names
from toutiao_content_factory.services.concrete_anchors import concrete_hook_status, extract_concrete_anchors
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.review_service import ReviewWorkflowService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wechat_run_report import review_diagnostics


TITLE = "Mistral AI预告新模型，测试结果尚未披露"
BODY = "Mistral AI预告新模型，具体测试方法尚未披露。背景资料提到了OpenAI，未提供双方的独立对比结果。"


def enable(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


@pytest.mark.parametrize("name", ["Mistral AI", "MistralAI", "MISTRAL AI", "mistral ai", "Mistral"])
def test_wechat_real_subject_and_preview_action_are_recognized(name):
    title = TITLE.replace("Mistral AI", name)
    body = BODY.replace("Mistral AI", name)
    result = ArticleQualityChecker(wechat_mode=True).check_title(title, body)
    assert result["issues"] == []
    assert extract_wechat_company_names(body)
    assert concrete_hook_status(title, body, wechat_mode=True)[0] == "PASS"
    assert ArticleQualityChecker().check_title(title, body)["issues"]


@pytest.mark.parametrize("name", ["英特尔", "微软", "三星"])
def test_wechat_reuses_existing_known_company_coverage(name):
    result = ArticleQualityChecker(wechat_mode=True).check_title(f"{name}预告新产品，测试结果尚未披露", f"{name}预告新产品，测试结果尚未披露。")
    assert result["issues"] == []


@pytest.mark.parametrize("title,body", [
    (TITLE, "OpenAI预告新模型。"),
    (TITLE, "尚未披露测试结果。\n\n参考来源：Mistral AI报道"),
    (TITLE, "参考来源：Mistral AI报道"),
    ("Mistral预告新模型，测试结果尚未披露", "相关信息在https://mistral.ai/，OpenAI没有公布测试结果。"),
    ("FictionalBrand预告新模型，测试結果尚未披露", BODY),
    ("产业发展进入新阶段，关键变化值得观察", BODY),
    ("Mistral AI预告新模型，Mistral AI", "名字只在网址https://mistral.ai/出现。"),
])
def test_wechat_keeps_subject_identity_and_information_gates(title, body):
    assert ArticleQualityChecker(wechat_mode=True).check_title(title, body)["issues"]


def test_wechat_alias_extraction_excludes_urls_and_ascii_substrings():
    assert not extract_wechat_company_names("https://mistral.ai/about/ NotMistralAI MistralAIExtra Mistral_foo")
    assert not extract_concrete_anchors("正文没有该公司。\n参考来源：Mistral AI与OpenAI", wechat_mode=True)


@pytest.mark.parametrize("wechat", [True, False])
def test_title_passes_generation_review_and_selection_consistently(app_settings, mock_openai_service, logger, tmp_path, monkeypatch, wechat):
    if wechat:
        enable(app_settings)
    monkeypatch.delenv("OPENAI_TITLE_MODEL", raising=False)
    workflow = AgentWorkflowService(app_settings, PromptRepository(app_settings), mock_openai_service, logger)
    title = workflow.title_agent._sanitize_title(TITLE)
    assert workflow.title_agent.validate_title_candidate(title, BODY).passed is wechat
    content = GeneratedContent(article=BODY, polished_article=BODY, titles=[title], recommended_titles=[title])
    workflow._filter_titles_by_quality(content)
    assert bool(content.titles) is wechat
    council_title = workflow.quality_review_council_agent.quality_checker.check_title(title, BODY)
    assert bool(council_title["issues"]) is not wechat
    service = ReviewWorkflowService(app_settings, logger)
    monkeypatch.setattr(service, "_load_bundle", lambda path: (SimpleNamespace(article=BODY, style_key=app_settings.default_style), None))
    monkeypatch.setattr(service, "_title_candidates", lambda bundle: [title])
    selection = service.select_auto_title(tmp_path)
    assert bool(selection["selected_title"]) is wechat
    assert service.AUTO_TITLE_SCORE_MIN == 70


def test_wechat_prefilter_and_every_pool_keep_per_title_decisions(app_settings, mock_openai_service, logger):
    enable(app_settings)
    agent = TitleAgent(app_settings, PromptRepository(app_settings), mock_openai_service, logger)
    trace = []
    raw = [TITLE, TITLE, "某公司预告新产品，测试结果尚未披露"]
    titles = agent._dedupe_titles(raw, CacheContext(), trace=trace)
    assert [item["prefilter_passed"] for item in trace] == [True, False, False]
    assert trace[1]["reason_codes"] == ["duplicate_title"]
    assert trace[2]["reason_codes"] == ["low_quality_or_placeholder"]
    for source in ["model", "fallback", "emergency", "repair"]:
        pool = agent._build_title_pool(source, titles + ["产业发展进入新阶段，关键变化值得观察"], BODY, DataReport(),
                                      raw_titles=raw, prefilter_trace=trace)
        report = pool.to_dict(include_candidates=True)
        assert report["raw_titles"] == raw
        assert [item["passed"] for item in report["candidates"]] == [True, False]
        assert report["candidates"][1]["reason_codes"]
        assert "candidates" not in pool.to_dict()  # Historical/Toutiao report shape.


@pytest.mark.parametrize("legacy_pool", [{}, {"raw_titles": None, "prefilter": None, "candidates": None},
                                         {"raw_titles": "old title", "prefilter": "old", "candidates": "old"}])
def test_title_diagnostics_are_sanitized_and_do_not_upload_material(app_settings, monkeypatch, legacy_pool):
    started = time.time_ns() - 1_000_000_000
    monkeypatch.setenv("PRIVATE_TITLE_TOKEN", "secret-title-token")
    run_dir = app_settings.runs_dir / "diagnostic-run"
    run_dir.mkdir(parents=True)
    sensitive = "标题 secret-title-token https://mp.weixin.qq.com/cgi-bin/home?token=private"
    pool = {"status": "all_rejected", "raw_titles": [sensitive],
            "prefilter": [{"raw_title": sensitive, "title": sensitive, "prefilter_passed": True, "reason_codes": []}],
            "candidates": [{"title": sensitive, "passed": False, "reason_codes": ["rule_006_abstract_subject"]}]}
    (run_dir / "workflow_result.json").write_text(json.dumps({"content": {"article": "PRIVATE_FULL_ARTICLE", "title_validation": {
        "status": "all_rejected", "pools": {**{name: pool for name in ["model", "fallback", "emergency", "repair"]}, "legacy": legacy_pool}}},
        "data_report": {"source_records": [{"source_id": "src", "original_text": "PRIVATE_FULL_SOURCE", "source_role": "background"}]}}), encoding="utf-8")
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.automation_dir / "last_daily_workflow_report.json").write_text(json.dumps({"account_key": app_settings.account_key,
        "status": "review_required", "attempts": [{"status": "review_required", "run_dir": str(run_dir)}]}), encoding="utf-8")
    report = review_diagnostics(app_settings, {"status": "review_required"}, started)
    text = json.dumps(report)
    assert not any(private in text for private in ["secret-title-token", "token=private", "PRIVATE_FULL_ARTICLE", "PRIVATE_FULL_SOURCE"])
    assert set(report["attempts"][0]["title_validation"]["pools"]) == {"model", "fallback", "emergency", "repair", "legacy"}
    assert all(report["attempts"][0]["title_validation"]["pools"]["legacy"][key] == []
               for key in ["raw_titles", "prefilter", "candidates"])
    assert report["attempts"][0]["title_validation"]["pools"]["model"]["candidates"][0]["reason_codes"] == ["rule_006_abstract_subject"]
    assert report["attempts"][0]["source_records"][0]["source_role"] == "background"


def sources():
    return evidence.source_records([
        material.MaterialSource("模型预告", "https://a.test/current", "Mistral AI声称新模型在网安方面优于中国竞品，具体测试方法和比较对象尚未披露。", True,
                                published_at="2026-10-06T19:24:24", date_verified=True),
        material.MaterialSource("旧财务报道", "https://b.test/background", "2025年甲公司净利润达到10亿元。", True,
                                published_at="2026-04-22T18:47:31", date_verified=True),
        material.MaterialSource("日期未知", "https://c.test/unknown", "尚未披露模型的具体测试方法和独立验证结果。", True),
    ])


def test_background_is_not_current_event_evidence_and_legacy_dates_stay_unknown():
    original = sources()
    scoped = evidence.scope_source_records(original, "https://a.test/current")
    assert [source["source_role"] for source in scoped] == ["primary", "background", "undated"]
    assert all("source_role" not in source for source in original)
    assert scoped[2]["published_at"] == "" and scoped[2]["date_verified"] is False
    active = evidence.build_facts(scoped, include_background=False)
    assert len(active) == 2 and len(evidence.build_facts(scoped)) == 3
    assert all(fact["source_id"] != scoped[1]["source_id"] for fact in active)
    report = DataReport(source_records=scoped, fact_evidence=active, article_kind="product_progress")
    evidence.build_outline(report)
    assert all(set(section["fact_ids"]).issubset({fact["fact_id"] for fact in active}) for section in report.writing_outline)
    article = evidence.prepare_article("2025年甲公司净利润达到10亿元。\n\n" + original[0]["original_text"], report)
    assert "净利润" not in article and "旧财务报道" not in article
    assert "模型预告" in article
    assert evidence.scope_source_records(original, "https://missing.test") == original


@pytest.mark.parametrize("old_date,role", [
    ("2026-10-06T09:00:00", "supporting"),
    ("2026-10-06T07:00:00", "background"),
    ("2026-10-06T11:24:24+00:00", "supporting"),
    ("2026-10-05", "supporting"),
    ("2026-10-03", "background"),
])
def test_event_source_timezones_and_date_precision_are_preserved(old_date, role):
    records = sources();records[1]["published_at"] = old_date
    assert evidence.scope_source_records(records, records[0]["url"])[1]["source_role"] == role
    assert records[1]["published_at"] == old_date


@pytest.mark.parametrize("cached", [True, False])
def test_old_background_cannot_make_a_thin_current_source_qualify(app_settings, logger, monkeypatch, cached):
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    current = material.MaterialSource("机器人预告", "https://a.test/current", "机器人公司预告将发布新模型，测试细节尚未披露。", True,
                                     published_at="2026-10-06T19:00:00", date_verified=True)
    old = material.MaterialSource("旧机器人报道", "https://b.test/old", "\n".join(f"机器人公司完成第{i}项测试，设备达到20台，具体测试条件和适用范围已经公开披露。" for i in range(1, 40)), True,
                                 published_at="2026-04-22T18:00:00", date_verified=True)
    bundle = material.MaterialBundle("机器人预告", [current, old])
    assert material.assess_material_evidence(bundle).passed
    monkeypatch.setattr(material, "collect_material", MagicMock(return_value=bundle))
    topic = TopicCandidate("机器人新模型预告", current.url, "rss", "rss", published_at=current.published_at,
                           source_records=evidence.source_records(bundle.usable) if cached else [])
    assert not app.topic_automation._hydrate_topic_material(topic)
    assert topic.source_records[1]["source_role"] == "background"
    assert topic.source_records[1]["original_text"] == old.body


def test_selected_source_date_is_not_replaced_by_a_newer_related_article(app_settings, logger):
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    primary = material.MaterialSource("原选题", "https://a.test/old", "机器人报道正文。", True,
                                     published_at="2026-04-22T18:00:00", date_verified=True)
    later = material.MaterialSource("新相关报道", "https://b.test/new", "另一个机器人报道。", True,
                                   published_at="2026-10-06T19:00:00", date_verified=True)
    topic = TopicCandidate("原选题", primary.url, "rss", "rss", published_at=later.published_at)
    result = app.topic_automation._scope_wechat_material(topic, material.MaterialBundle("原选题", [primary, later]))
    assert topic.published_at == primary.published_at
    assert result.usable == [primary]


@pytest.mark.parametrize("claim,allowed", [
    ("Mistral AI声称新模型在网安方面优于中国竞品。", True),
    ("Mistral AI新模型在网安方面优于中国竞品。", False),
    ("Mistral AI声称新模型在网安方面优于OpenAI。", False),
    ("Mistral AI声称新模型在网安方面优于所有中国竞品。", False),
    ("是否优于中国竞品仍需独立验证。", True),
    ("不能证明新模型优于中国竞品。", True),
    ("具体测试方法尚未披露，Mistral AI新模型在网安方面优于所有中国竞品。", False),
])
def test_comparisons_need_original_target_and_preview_qualification(claim, allowed):
    facts = evidence.build_facts(sources()[:1])
    assert bool(evidence.unsupported_claims(claim, facts)) is not allowed
