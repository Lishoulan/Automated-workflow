from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import DataReport
from toutiao_content_factory.services import material_search_service as material, wechat_evidence as evidence
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker


@pytest.mark.parametrize("title", ["《机器人》设备发布", "设备支持《作品》？测试细节公布", "《文集《技术》》发布"])
def test_repeated_cleanup_never_stacks_nested_title_citations(title):
    body = "机器人公司宣布支持新设备，具体测试条件尚未披露。"
    records = evidence.source_records([material.MaterialSource(title, "https://a.test/report", body, True)])
    report = DataReport(source_records=records, fact_evidence=evidence.build_facts(records), article_kind="product_progress")
    old_stacked = (f"据《{title}》报道，" * 7) + body
    assert evidence.effective_body_chars(old_stacked) == len(body)
    first = evidence.prepare_article(old_stacked, report)
    assert first.split("参考来源：")[0].count("报道，") == 1
    assert evidence.effective_body_chars(first) == len(body)
    assert evidence.unsupported_claims(first, report.fact_evidence) == []
    for _ in range(10):
        first_again = evidence.prepare_article(first, report)
        assert first_again == first
        assert evidence.effective_body_chars(first_again) == len(body)


def test_broken_citations_do_not_silently_remove_claims():
    raw = "据《未闭合来源报道，甲公司利润增长99%。"
    assert evidence._strip_source_citations(raw) == raw
    assert evidence.effective_body_chars(raw) == len(raw)


@pytest.mark.parametrize("action", ["支持", "兼容", "适配", "上线", "搭载", "开源"])
def test_disclosed_technology_actions_use_existing_information_gate(action):
    title = f"英伟达{action}新产品，测试细节尚未披露"
    body = title + "。"
    assert ArticleQualityChecker(wechat_mode=True).check_title(title, body)["issues"] == []
    assert ArticleQualityChecker().check_title(title, body)["issues"]
    assert ArticleQualityChecker(wechat_mode=True).check_title(title, "正文没有该公司。")['issues']
    assert ArticleQualityChecker(wechat_mode=True).check_title("新产品体验很好，玩家迎来福音", body)['issues']


@pytest.mark.parametrize("title,url", [
    ("明日方舟专题页", "https://other.test/topic"),
    ("机器人攻略汇总", "https://other.test/topic"),
    ("游戏目录", "https://cg.163.com/static/game/mrfz/"),
])
def test_aggregation_pages_never_become_fact_evidence(title, url):
    assert not evidence.source_records([material.MaterialSource(title, url, "机器人公司发布新设备。" * 100, True)])
    assert evidence.is_event_article("机器人新品发布", "https://a.test/report")


@pytest.mark.parametrize("wechat", [True, False])
def test_aggregation_rejection_continues_retrieval_only_in_wechat(monkeypatch, wechat):
    directory = "https://cg.163.com/static/game/mrfz/"
    article = "https://a.test/report"
    body = "".join(f"机器人公司在2026年10月7日披露第{i}项测试结果，现场共有{i+10}台设备，公告说明测试的条件和适用范围。" for i in range(1, 24))
    fetch = MagicMock(side_effect=lambda url, **kwargs: ('<p>' + body + '</p>', url, "http"))
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    monkeypatch.setattr(material, "search_web", lambda *args, **kwargs: [
        {"title": "机器人测试专题目录", "url": directory}, {"title": "机器人测试", "url": article}])
    bundle = material.collect_material("机器人测试", preserve_sources=wechat, deadline=time.monotonic() + 60)
    assert fetch.call_count == (2 if wechat else 1)
    if wechat:
        assert bundle.sources[0].evidence_type == "aggregation" and not bundle.sources[0].ok
        assert bundle.usable[0].url == article
        assert material.assess_material_evidence(bundle).passed
