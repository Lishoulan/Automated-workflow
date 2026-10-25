from __future__ import annotations

import html
import json
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.quality_review_council_agent import QualityReviewCouncilAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TopicCandidate, TrendReport
from toutiao_content_factory.services import material_search_service as material, wechat_evidence as evidence
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.prompt_repository import PromptRepository


def enable(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


def page(body, date, container="paragraph"):
    return ('<html><head><meta property="article:published_time" content="' + date + '"></head>'
            '<body><div id="' + container + '"><p>' + html.escape(body) + '</p></div></body></html>')


def detailed_body():
    return "".join(f"机器人公司在明确的测试环境中完成第{i}项设备测试，公告披露设备达到{i+10}台，并介绍操作条件和验证范围。"
                   for i in range(1, 24))


@pytest.mark.parametrize("wechat", [True, False])
def test_collection_does_not_stop_on_historical_background(monkeypatch, wechat):
    primary_url = "https://a.test/current"
    old_url, supplement_url = "https://b.test/old", "https://c.test/current"
    primary = "机器人公司预告发布新设备，公告确认展示地点和计划安排。具体测试结果尚未披露。"
    documents = {
        primary_url: page(primary, "2026-10-06T19:00:00+08:00"),
        old_url: page(detailed_body(), "2026-04-22T18:00:00+08:00"),
        supplement_url: page(detailed_body(), "2026-10-06T18:00:00+08:00"),
    }
    fetch = MagicMock(side_effect=lambda url, **kwargs: (documents[url], url, "http"))
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    monkeypatch.setattr(material, "search_web", lambda *args, **kwargs: [
        {"title": "机器人新设备历史测试报道", "url": old_url},
        {"title": "机器人新设备预告：同期测试报道", "url": supplement_url}])
    bundle = material.collect_material("机器人新设备预告", source_url=primary_url, preserve_sources=wechat,
                                       min_body_chars=400, max_sources=3, deadline=time.monotonic() + 60)
    assert fetch.call_count == (3 if wechat else 2)
    if wechat:
        records = evidence.scope_source_records(evidence.source_records(bundle.usable), primary_url)
        assert [record["source_role"] for record in records] == ["primary", "background", "supporting"]
        assert len(bundle.sources) == 3
        assert primary in bundle.usable[0].body  # Short original retained as event anchor, not approved alone.
        active = material.MaterialBundle(bundle.query, [bundle.usable[0], bundle.usable[2]])
        assert material.assess_material_evidence(active).passed
        assert not material.assess_material_evidence(material.MaterialBundle(bundle.query, [bundle.usable[0]])).passed


@pytest.mark.parametrize("container", ["paragraph", "js_content"])
def test_wechat_extraction_cannot_use_page_recommendations_to_fill_short_body(container):
    primary = "机器人公司预告新设备，具体测试细节尚未披露。"
    raw = ('<body><p>' + detailed_body() + '</p><div id="' + container + '"><div><p>' + primary
           + '</p></div></div><div><p>' + detailed_body() + '</p></div></body>')
    assert material.extract_article_body(raw, min_chars=400, wechat_mode=True) == ""
    assert material.extract_article_body(raw, min_chars=1, wechat_mode=True) == primary
    assert len(material.extract_article_body(raw, min_chars=400)) >= 400
    assert material.extract_article_body('<div id="' + container + '"><p>' + detailed_body(),
                                         wechat_mode=True) == ""


def test_candidate_ranking_uses_current_facts_not_old_financing(app_settings, logger):
    enable(app_settings)
    now = datetime.now().isoformat()
    def topic(title, url, records, score):
        return TopicCandidate(title, url, "rss", "rss", snippet=detailed_body(), source_records=records,
                              published_at=now, evidence_strength=score, quality_score=score)
    rich_records = evidence.source_records([material.MaterialSource("机器人测试", "https://a.test/full",
        detailed_body(), True, published_at=now, date_verified=True)])
    preview_records = evidence.source_records([
        material.MaterialSource("机器人预告", "https://b.test/preview", "机器人公司预告新设备，具体性能和测试结果尚未披露。", True,
                                published_at=now, date_verified=True),
        material.MaterialSource("旧机器人报道", "https://c.test/old", detailed_body() * 3, True,
                                published_at="2026-04-22T10:00:00", date_verified=True)])
    rich = topic("机器人完整测试报道", "https://a.test/full", rich_records, 70)
    preview = topic("机器人产品预告", "https://b.test/preview", preview_records, 95)
    app = ContentFactoryAppService(app_settings, logger)
    assert sorted([preview, rich], key=app._wechat_candidate_sort_key) == [rich, preview]
    app.topic_automation._load_daily_topics = MagicMock(return_value=MagicMock(topics=[preview, rich]))
    app.topic_automation._available_topics = MagicMock(return_value=[preview, rich])
    app._enrich_wechat_candidates = MagicMock()
    candidates = app._build_daily_candidate_specs(topic_rank=None, topic_title=None, refresh_cache=False, dry_run=False)
    assert candidates[0]["title"] == rich.title
    assert app._enrich_wechat_candidates.call_args.args[0] == [rich, preview]
    assert preview.evidence_strength == 95 and rich.evidence_strength == 70  # Ranking never changes gate scores.


@pytest.mark.parametrize("kind,section_index,quote", [
    ("product_progress", 3, "机器人公司披露独立测试结果，验证条件为室内固定环境。"),
    ("policy_explainer", 2, "陕西省通知明确适用于配套新能源项目。"),
    ("company_earnings", 1, "甲公司2026年上半年营业收入为10亿元。"),
])
def test_outline_binds_section_semantics_instead_of_fact_position(kind, section_index, quote):
    records = evidence.source_records([material.MaterialSource("公开报道", "https://a.test/report", quote, True)])
    data = DataReport(source_records=records, fact_evidence=evidence.build_facts(records), article_kind=kind)
    evidence.build_outline(data)
    section = data.writing_outline[section_index]
    assert section["evidence_scope"] == "direct" and section["fact_ids"] == [data.fact_evidence[0]["fact_id"]]
    assert any(section["evidence_scope"] == "boundary_only" for section in data.writing_outline)
    assert sum(section["target_chars"] for section in data.writing_outline) == 2100


def product_data():
    quote = "Mistral AI声称新模型在网安方面优于中国竞品，具体测试方法和比较对象尚未披露。"
    records = evidence.source_records([material.MaterialSource("新模型预告", "https://a.test/model", quote, True)])
    return DataReport(topic_title="Mistral AI新模型预告", article_kind="product_progress",
                      source_records=records, fact_evidence=evidence.build_facts(records))


@pytest.mark.parametrize("wechat", [True, False])
def test_review_uses_article_kind_and_does_not_demand_undisclosed_claims(app_settings, logger, wechat):
    if wechat:
        enable(app_settings)
    repo = MagicMock()
    repo.render.return_value = "review prompt"
    model = MagicMock(available=True)
    model.create_json_completion.return_value = {"verdict": "needs_revision", "overall_score": 60}
    data = product_data()
    data.source_records.append({"source_id": "old", "source_role": "background", "original_text": "OLD_FINANCE"})
    agent = QualityReviewCouncilAgent(app_settings, repo, model, logger)
    agent.run(GeneratedContent(article="Mistral AI声称新模型在网安方面优于中国竞品，具体比较对象尚未披露。"),
              data, app_settings.get_style(), CacheContext())
    rendered = repo.render.call_args.kwargs
    prompt = model.create_json_completion.call_args.args[1]
    if wechat:
        assert "OLD_FINANCE" not in rendered["data_json"]
        assert "突出产业链" not in rendered["style_digest"]
        assert "不要求补造具体对象" in prompt and "仍须阻断" in prompt
    else:
        assert "OLD_FINANCE" in rendered["data_json"]
        assert "突出产业链" in rendered["style_digest"]
        assert "不要求补造具体对象" not in prompt


def test_patch_can_remove_unqualified_claim_without_protecting_it_as_a_fact(app_settings, logger):
    enable(app_settings)
    data = product_data()
    supported = "Mistral AI声称新模型在网安方面优于中国竞品。"
    unsupported = "Mistral AI新模型在网安方面优于中国竞品。"
    paragraph = supported + unsupported
    model = MagicMock(available=True)
    model.create_json_completion.return_value = {"patches": [{"paragraph_id": 0, "before": paragraph,
                                                              "after": supported + "具体比较对象尚未披露。"}]}
    agent = PolishAgent(app_settings, MagicMock(), model, logger)
    result = agent.run(GeneratedContent(article=paragraph + "\n\n其他合格段落保持原样。"), TrendReport(), data,
                       app_settings.get_style(), CacheContext(), ["issue_test：保留声称限定；原段落：" + paragraph])
    assert unsupported not in result["polished_article"]
    assert supported in result["polished_article"]
    assert "其他合格段落保持原样。" in result["polished_article"]
    assert result["grounding_diagnostics"][0]["revision_patch_accepted"] == 1


@pytest.mark.parametrize("wrong_patch,code", [
    ({"paragraph_id": 1, "before": "其他合格段落保持原样。", "after": "把无关段落也改掉。"}, "patch_rejected_unrelated"),
    ({"paragraph_id": 0, "before": "wrong paragraph", "after": "具体比较对象尚未披露。"}, "patch_rejected_identity"),
    ({"paragraph_id": 0, "after": "删除已有的全部模型预告事实。"}, "patch_rejected_fact_loss"),
])
def test_targeted_patches_keep_fact_loss_and_locality_protection(app_settings, logger, wrong_patch, code):
    enable(app_settings)
    data = product_data()
    paragraph = data.fact_evidence[0]["quote"]
    patch = {"before": paragraph, **wrong_patch}
    model = MagicMock(available=True)
    model.create_json_completion.return_value = {"patches": [patch]}
    agent = PolishAgent(app_settings, MagicMock(), model, logger)
    result = agent.run(GeneratedContent(article=paragraph + "\n\n其他合格段落保持原样。"), TrendReport(), data,
                       app_settings.get_style(), CacheContext(), ["issue_test：保留限定；原段落：" + paragraph])
    assert paragraph in result["polished_article"] and "其他合格段落保持原样。" in result["polished_article"]
    assert result["grounding_diagnostics"][0][code] == 1
    assert result["grounding_diagnostics"][0]["revision_patch_accepted"] == 0


def test_bound_writer_states_missing_section_evidence_explicitly(app_settings, logger):
    enable(app_settings)
    data = product_data()
    model = MagicMock(available=True)
    model.create_json_completion.return_value = {"sections": []}
    agent = WriterAgent(app_settings, PromptRepository(app_settings), model, logger)
    agent._write_wechat(TrendReport(), data, app_settings.get_style(), CacheContext())
    prompt = str(model.create_json_completion.call_args)
    assert "boundary_only" in prompt and "不得补造能力" in prompt
