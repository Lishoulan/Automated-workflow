from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.automation import TopicAutomationService
import toutiao_content_factory.services.material_search_service as material


def _full_body() -> str:
    return "".join([
        "据公开报道，社区在2026年10月3日组织志愿者为居民提供服务。",
        "工作人员表示，现场共有12名志愿者参与活动。",
        "公告显示，当日上午9时开始登记居民需求。",
        "报道还提到，3位老人获得了现场协助。",
        "截至活动结束，共有80名居民完成登记。",
        "社区确认，下一场活动将在10月8日举行。",
    ] * 8)


def _mock_full_report(monkeypatch) -> MagicMock:
    search = MagicMock(return_value=[{
        "title": "社区志愿者为老人提供服务",
        "url": "https://publisher.example/report",
        "snippet": "",
    }])
    monkeypatch.setattr(material, "search_web", search)
    monkeypatch.setattr(material, "fetch_text_smart", lambda url, **_: (f"<p>{_full_body()}</p>", url, "http"))
    return search


def test_no_matching_query_does_not_disable_search_for_next_topic(monkeypatch):
    monkeypatch.setattr(material, "_DEAD_BACKENDS", set())
    monkeypatch.setattr(material, "SEARCH_BACKENDS", [("test", "https://search.example?q={q}", "js")])
    search = MagicMock(side_effect=[[], [{"title": "有报道的题", "url": "https://publisher.example/a"}]])
    monkeypatch.setattr(material, "_search_with_backend", search)

    assert material.search_web("无报道的题") == []
    assert material.search_web("有报道的题")[0]["title"] == "有报道的题"
    assert search.call_count == 2
    assert material._DEAD_BACKENDS == set()


def test_transport_failure_disables_backend_but_valid_empty_page_does_not(monkeypatch):
    import playwright.sync_api

    monkeypatch.setattr(material, "_DEAD_BACKENDS", set())
    browser = MagicMock()
    page = browser.new_context.return_value.new_page.return_value
    page.goto.return_value = None
    page.content.return_value = "<html><body>No results</body></html>"
    page.url = "https://search.example"
    page.evaluate.return_value = []
    context = MagicMock()
    context.__enter__.return_value.chromium.launch.return_value = browser
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: context)
    backend = ("test", "https://search.example?q={q}", "js")

    assert material._search_with_backend(backend, "empty", retries=1) == []
    assert "test" not in material._DEAD_BACKENDS
    page.goto.side_effect = TimeoutError("transport failed")
    assert material._search_with_backend(backend, "broken", retries=1) == []
    assert "test" in material._DEAD_BACKENDS


def test_snippets_do_not_exhaust_budget_before_full_report(monkeypatch):
    hits = [{"title": "社区现场活动", "url": f"https://publisher.example/{i}",
             "snippet": "据报道，社区活动在10月3日开始，工作人员协助了居民完成登记。" * 2}
            for i in range(4)]
    monkeypatch.setattr(material, "search_web", lambda *_, **__: hits)
    fetch = MagicMock(side_effect=[("<p>短页</p>", item["url"], "http") for item in hits[:3]] +
                     [(f"<p>{_full_body()}</p>", hits[3]["url"], "http")])
    monkeypatch.setattr(material, "fetch_text_smart", fetch)

    bundle = material.collect_material("社区志愿者为老人提供服务", max_sources=3)

    assert fetch.call_count == 4
    assert material.assess_material_evidence(bundle).passed
    assert bundle.usable[-1].evidence_type == "article"


def test_baijiahao_reports_are_fetchable_but_baidu_search_and_lookalikes_are_not(monkeypatch):
    urls = [
        "https://www.baidu.com/s?wd=baijiahao.baidu.com",
        "https://baijiahao.baidu.com.attacker.example/report",
        "https://baijiahao.baidu.com/s?id=123",
    ]
    assert [material._fetch_priority(url) for url in urls] == [0, 0, 2]
    monkeypatch.setattr(material, "search_web", lambda *_, **__: [
        {"title": "社区报道", "url": url, "snippet": ""} for url in urls
    ])
    fetch = MagicMock(return_value=(f"<p>{_full_body()}</p>", urls[2], "http"))
    monkeypatch.setattr(material, "fetch_text_smart", fetch)

    bundle = material.collect_material("社区报道")

    fetch.assert_called_once()
    assert fetch.call_args.args[0] == urls[2]
    assert material.assess_material_evidence(bundle).passed


def test_hot_story_reuses_rechecked_material_in_next_process(app_settings, monkeypatch):
    search = _mock_full_report(monkeypatch)
    def topic():
        return TopicCandidate(title="社区志愿者为老人提供服务", url="", source="百度热榜", source_type="baidu_hot")

    first = topic()
    assert TopicAutomationService(app_settings, logging.getLogger("test"))._hydrate_topic_material(first)
    search.side_effect = AssertionError("temporary source outage should not discard cached reporting")
    second = topic()
    assert TopicAutomationService(app_settings, logging.getLogger("test"))._hydrate_topic_material(second)
    assert second.snippet == first.snippet
    assert second._verified_source_material is True
    assert search.call_count == 1


def test_slow_sources_cannot_consume_unbounded_fetch_time(monkeypatch):
    hits = [{"title": "社区活动", "url": f"https://publisher.example/{i}", "snippet": ""} for i in range(6)]
    monkeypatch.setattr(material, "search_web", lambda *_, **__: hits)
    fetch = MagicMock(return_value=("<p>短页</p>", hits[0]["url"], "http"))
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    monkeypatch.setattr(material.time, "monotonic", MagicMock(side_effect=[0, 0, 121]))

    bundle = material.collect_material("社区活动")

    assert fetch.call_count == 1
    assert not material.assess_material_evidence(bundle).passed


def test_original_report_passes_without_search_and_keeps_case_metadata(app_settings, monkeypatch):
    search = MagicMock(side_effect=AssertionError("qualified original report needs no search"))
    monkeypatch.setattr(material, "search_web", search)
    fetch = MagicMock(return_value=(f"<p>{_full_body()}</p>", "https://www.court.gov.cn/case.html", "http"))
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    topic = TopicCandidate(
        title="奶奶赠房给孙女后被起诉，法院判决撤销本人份额赠与",
        url="https://www.court.gov.cn/case.html", source="最高人民法院公开案例", source_type="evergreen",
        topic_type="evergreen", published_at="2022-07-11T09:33:43",
        summary="2022年案例回顾，仅撤销奶奶本人的赠与份额。",
    )
    service = TopicAutomationService(app_settings, logging.getLogger("test"))

    assert service._hydrate_topic_material(topic)
    assert topic.summary == "2022年案例回顾，仅撤销奶奶本人的赠与份额。"
    assert topic.published_at == "2022-07-11T09:33:43"
    assert "不得写成今日" in service._build_input_text(topic)
    fetch.assert_called_once()
    search.assert_not_called()


def test_failed_original_report_can_recover_from_search(monkeypatch):
    search = _mock_full_report(monkeypatch)
    fetch = MagicMock(side_effect=[OSError("temporarily unavailable"),
                                  (f"<p>{_full_body()}</p>", "https://publisher.example/report", "http")])
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    bundle = material.collect_material("社区报道", source_url="https://www.court.gov.cn/case.html")
    assert material.assess_material_evidence(bundle).passed
    assert fetch.call_count == 2
    search.assert_called_once()


@pytest.mark.parametrize("url", ["evergreen://case/123", "file:///private", "https:missing-host",
                                "https://www.baidu.com/s?wd=case", "https://baijiahao.baidu.com.attacker.example/case"])
def test_non_article_original_url_is_not_fetched(monkeypatch, url):
    monkeypatch.setattr(material, "search_web", lambda *_, **__: [])
    fetch = MagicMock()
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    assert not material.collect_material("社区报道", source_url=url).usable
    fetch.assert_not_called()


@pytest.mark.parametrize("damage", ["old_version", "expired", "future", "wrong_query", "thin", "invalid_type", "invalid_url", "invalid_json"])
def test_invalid_or_stale_cache_cannot_pass_evidence_gate(tmp_path, monkeypatch, damage):
    search = _mock_full_report(monkeypatch)
    monkeypatch.setattr(material.time, "time", lambda: 100_000)
    title = "社区志愿者为老人提供服务"
    material.collect_material(title, cache_dir=tmp_path)
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if damage == "old_version":
        payload["version"] = 1
    elif damage == "expired":
        payload["collected_at"] -= material.MATERIAL_CACHE_TTL_SECONDS + 1
    elif damage == "future":
        payload["collected_at"] += 1
    elif damage == "wrong_query":
        payload["query"] = "另一件事"
    elif damage == "thin":
        payload["sources"][0]["body"] = "据报道，仅有一个短摘要。"
    elif damage == "invalid_type":
        payload["sources"][0]["evidence_type"] = "verified"
    elif damage == "invalid_url":
        payload["sources"][0]["url"] = "https:missing-host"
    path.write_text("{" if damage == "invalid_json" else json.dumps(payload), encoding="utf-8")
    search.return_value = []

    bundle = material.collect_material(title, cache_dir=tmp_path)

    assert not bundle.usable
    assert search.call_count > 1


def test_cache_is_scoped_to_account_directory(tmp_path, monkeypatch):
    search = _mock_full_report(monkeypatch)
    title = "社区志愿者为老人提供服务"
    material.collect_material(title, cache_dir=tmp_path / "account-a")
    material.collect_material(title, cache_dir=tmp_path / "account-b")
    assert search.call_count == 2


def test_failed_material_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(material, "search_web", lambda *_, **__: [])
    cache_dir = tmp_path / "cache"
    assert not material.collect_material("不存在的报道", cache_dir=cache_dir).usable
    assert not cache_dir.exists()


def test_cache_write_failure_does_not_block_qualified_material(tmp_path, monkeypatch):
    _mock_full_report(monkeypatch)
    monkeypatch.setattr(material.os, "replace", MagicMock(side_effect=OSError("disk error")))
    bundle = material.collect_material("社区志愿者为老人提供服务", cache_dir=tmp_path)
    assert material.assess_material_evidence(bundle).passed
    assert not list(tmp_path.glob("*.tmp"))


def test_article_container_excludes_sidebar_and_comments_without_padding_short_reports():
    sidebar = "股市涨跌数据和其他新闻不能作为本案事实。" * 100
    body = _full_body()
    page = f'<body><div id="artibody"><p>{body}</p><div><p>报道补充说明了现场人员的登记安排。</p></div></div><div>{sidebar}</div></body>'
    extracted = material.extract_article_body(page)
    assert body in extracted
    assert "登记安排" in extracted
    assert "股市" not in extracted
    short_page = f'<div id="artibody"><p>仅有一段简短报道，不应靠推荐栏凑足证据字数。</p></div><p>{sidebar}</p>'
    assert material.extract_article_body(short_page) == ""


def test_unclosed_article_container_cannot_spill_into_sidebar():
    assert material.extract_article_body(f'<div id="artibody"><p>{_full_body()}</p>') == ""
