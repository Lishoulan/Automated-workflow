"""Failures observed in the October16 cloud topic top-up, with real boundaries."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


def topic(title, url="evergreen://case"):
    return TopicCandidate(title=title, summary=title, url=url, source="test", source_type="evergreen")


def generator(settings, logger):
    object.__setattr__(settings.publishing, "weitoutiao_personas", ["story_comment"])
    service = WeiToutiaoStandaloneGenerator(settings, logger)
    service.story_sources = SimpleNamespace(candidates=lambda **_: [], can_retry=lambda _: True, record_result=MagicMock())
    service.topic_automation._available_topics = lambda rows: rows
    service.topic_automation.filter_topics_for_account = lambda rows: rows
    service.topic_automation.topic_pool_service.build_fallback_report = lambda: SimpleNamespace(topics=[])
    return service


def test_complete_primary_headline_pool_does_not_hide_original_source_reserve(app_settings, logger):
    service = generator(app_settings, logger)
    weak = [topic(f"老人房产继承争议第{i}件", f"evergreen://case/{i}") for i in range(4)]
    original = topic("业主物业费调价争议有法院原文", "https://www.hncourt.gov.cn/public/detail.php?id=188830")
    service.topic_automation._load_daily_topics = lambda: SimpleNamespace(topics=weak)
    service.topic_automation.topic_pool_service.build_fallback_report = lambda: SimpleNamespace(topics=[original])
    service.topic_automation._hydrate_topic_material = MagicMock(side_effect=lambda row, **_: row is original)
    assert service.select_topics(1) == [original]
    service.topic_automation._hydrate_topic_material.assert_called_once()


def test_real_court_catalogue_survives_reader_and_account_filters_without_mocking_them(app_settings, logger):
    from toutiao_content_factory.services.story_source_service import StorySourceService, SEED_REPORTS
    object.__setattr__(app_settings.topic_collection, "focus_industries", ["social_story"])
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    service.story_sources = StorySourceService(app_settings.output_dir, logger, live_feeds=False)
    catalogue = service.story_sources.candidates(deadline=999999999)
    assert {item.url for item in catalogue} == {url for _, url in SEED_REPORTS}
    service.topic_automation._load_daily_topics = lambda: SimpleNamespace(topics=[])
    service.topic_automation.topic_pool_service.build_fallback_report = lambda: SimpleNamespace(topics=[])
    service.topic_automation._hydrate_topic_material = MagicMock(side_effect=lambda item, **_: item.url == SEED_REPORTS[1][1])
    assert [item.url for item in service.select_topics(1)] == [SEED_REPORTS[1][1]]


def test_fifth_candidate_can_replace_four_failed_material_fetches(app_settings, logger):
    service = generator(app_settings, logger)
    rows = [topic(f"房产继承案第{i}件", f"https://news.example/case/{i}") for i in range(6)]
    service.topic_automation._load_daily_topics = lambda: SimpleNamespace(topics=rows)
    service.topic_automation._hydrate_topic_material = MagicMock(side_effect=lambda row, **_: row is rows[4])
    assert service.select_topics(1) == [rows[4]]
    assert service.topic_automation._hydrate_topic_material.call_count == 5


def test_shared_retrieval_deadline_stops_instead_of_starting_another_candidate(app_settings, logger, monkeypatch):
    service = generator(app_settings, logger)
    rows = [topic(f"物业费公开争议第{i}件") for i in range(8)]
    service.topic_automation._load_daily_topics = lambda: SimpleNamespace(topics=rows)
    clock = [100.0]
    import toutiao_content_factory.services.weitoutiao_generator as module
    import time
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    def hydrate(row, **kwargs):
        assert kwargs["deadline"] <= 125
        clock[0] = 281
        return False
    service.topic_automation._hydrate_topic_material = MagicMock(side_effect=hydrate)
    assert service.select_topics(1) == []
    service.topic_automation._hydrate_topic_material.assert_called_once()


def test_cooldown_candidates_do_not_consume_the_eight_actual_fetch_slots(app_settings, logger):
    service = generator(app_settings, logger)
    rows = [topic(f"物业费公开争议第{i}件", f"https://news.example/case/{i}") for i in range(9)]
    for index, row in enumerate(rows):
        row.rank = index + 1
    service.topic_automation._load_daily_topics = lambda: SimpleNamespace(topics=rows)
    service.story_sources.can_retry = lambda row: row is rows[-1]
    service.topic_automation._hydrate_topic_material = MagicMock(return_value=True)
    assert service.select_topics(1) == [rows[-1]]
    service.topic_automation._hydrate_topic_material.assert_called_once()


def test_source_link_parser_accepts_only_full_same_host_article_links():
    from toutiao_content_factory.services.story_source_service import parse_source_links
    page = '''<a href="/public/detail.php?id=123" title="员工加班费争议，法院核对考勤记录">缩略标题</a>
      <a href="https://evil.example/public/detail.php?id=9">员工工资被拖欠</a>
      <a href="https://www.hncourt.gov.cn.evil.example/public/detail.php?id=9">物业费纠纷</a>
      <a href="//user:password@www.hncourt.gov.cn/public/detail.php?id=9">物业费纠纷</a>
      <a href="/public/index.php?LocationID=1">物业费纠纷</a>
      <a href="/public/detail.php?id=bad">物业费纠纷</a>
      <a href="/public/detail.php?id=2">明星婚礼晒照片</a>
      <a href="/public/detail.php?id=123">员工加班费纠纷的另一标题</a>'''
    rows = parse_source_links(page, "https://www.hncourt.gov.cn/")
    assert [(row.title, row.url) for row in rows] == [
        ("员工加班费争议，法院核对考勤记录", "https://www.hncourt.gov.cn/public/detail.php?id=123")]
    assert not rows[0].published_at and not rows[0].snippet
    assert not parse_source_links(page, "https://evil.example/")


def test_persisted_source_catalogue_cannot_inject_untrusted_hosts(tmp_path, monkeypatch):
    from toutiao_content_factory.services import story_source_service as module
    service = module.StorySourceService(tmp_path, logging.getLogger("test"))
    service.path.parent.mkdir(parents=True)
    service.path.write_text(json.dumps({"schema": 1, "catalogue_at": 1000,
        "catalogue": [{"title": "员工工资被拖欠", "url": "https://evil.example/public/detail.php?id=2"}], "failures": {}}))
    monkeypatch.setattr(module.time, "time", lambda: 1001)
    monkeypatch.setattr(module, "fetch_text_smart", MagicMock(side_effect=AssertionError("Fresh catalogue should not refetch")))
    rows = service.candidates(deadline=2000)
    assert all("evil.example" not in row.url for row in rows)
    assert rows and not any(getattr(row, "_verified_source_material", False) for row in rows)


def test_failed_material_cooldown_survives_process_restart_but_is_not_approval(tmp_path, monkeypatch):
    from toutiao_content_factory.services import story_source_service as module
    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    item = topic("物业费纠纷", "https://www.hncourt.gov.cn/public/detail.php?id=1")
    service = module.StorySourceService(tmp_path, logging.getLogger("test"))
    service.record_result(item, False)
    fresh = module.StorySourceService(tmp_path, logging.getLogger("test"))
    assert not fresh.can_retry(item)
    item._verified_source_material = True
    item._material_evidence_assessment = {"passed": True}
    assert fresh.can_retry(item)  # Still goes through the caller's normal hydration gate.
    item._verified_source_material = False
    clock[0] += 6 * 3600 + 1
    assert fresh.can_retry(item)
    fresh.record_result(item, True)
    assert not hasattr(item, "_automatic_approval")


@pytest.mark.parametrize("malformed", ["{broken", '{"schema":99}', '{"schema":1,"failures":[]}'])
def test_bad_efficiency_cache_does_not_block_normal_fact_checks(tmp_path, malformed):
    from toutiao_content_factory.services.story_source_service import StorySourceService
    service = StorySourceService(tmp_path, logging.getLogger("test"))
    service.path.parent.mkdir(parents=True)
    service.path.write_text(malformed)
    assert service.can_retry(topic("工资纠纷"))


def court_page(body, *, date="2026-08-31 09:47:10", closing=True):
    return ('<p id="hztitle">婚介服务退款争议</p><span class="article_time">发布时间：' + date + '</span>'
        + '<span class="detail_content">' + body + ('</span>' if closing else '')
        + '<div><p>' + '推荐新闻有900亿元和90人的其他争议。' * 60 + '</p></div>')


def test_court_article_body_excludes_navigation_and_unrelated_factual_anchors():
    from toutiao_content_factory.services.material_search_service import extract_article_body, MaterialBundle, MaterialSource, assess_material_evidence
    original = "法院核对服务合同、收费凭证及双方已确认的实际履行情况，没有把其他争议写进本案。" * 12
    extracted = extract_article_body(court_page('<p>' + original + '</p><p>未披露的信息不能自行补写。</p>'), min_chars=1)
    assert original in extracted and "推荐新闻" not in extracted and "900亿元" not in extracted
    assert not assess_material_evidence(MaterialBundle("婚介服务", [MaterialSource("原文", "https://www.hncourt.gov.cn/public/detail.php?id=1", extracted, True)])).passed
    assert extract_article_body(court_page('<p>' + original + '</p>', closing=False), min_chars=1) == ""


def test_court_source_date_comes_from_header_and_not_case_or_copyright_year():
    from toutiao_content_factory.services.wechat_evidence import source_metadata
    page = court_page('<p>2024年6月发生本案，页面版权2026。</p>', date="2021-10-20 19:02:01")
    result = source_metadata(page, source_url="https://www.hncourt.gov.cn/public/detail.php?id=1")
    assert result["date_verified"] is True and result["published_at"].startswith("2021-10-20")
    assert result["page_title"] == "婚介服务退款争议"
    assert not source_metadata(page.replace('class="article_time"', 'class="footer"'), source_url="https://www.hncourt.gov.cn/public/detail.php?id=1")["date_verified"]
    assert not source_metadata(page, source_url="https://www.hncourt.gov.cn.evil.example/report")["date_verified"]


def test_non_wechat_enrichment_honors_the_callers_deadline(app_settings, monkeypatch):
    from toutiao_content_factory.services.automation import TopicAutomationService
    from toutiao_content_factory.services import material_search_service as material
    collect = MagicMock(return_value=material.MaterialBundle("物业费争议", []))
    monkeypatch.setattr(material, "collect_material", collect)
    TopicAutomationService(app_settings, logging.getLogger("test"))._hydrate_topic_material(topic("物业费争议"), deadline=1234)
    assert collect.call_args.kwargs["deadline"] == 1234


def test_court_date_is_bound_as_metadata_and_supports_only_the_report_year(app_settings, monkeypatch):
    from toutiao_content_factory.services.automation import TopicAutomationService
    from toutiao_content_factory.services import material_search_service as material
    body = "\n".join(f"2020年公司安排员工完成第{i}项工作，当事人提供了{i}份考勤记录。法院核对2019年和2020年工作记录，未将其他案件的材料混入。" for i in range(1, 21))
    item = topic("员工加班费争议", "https://www.hncourt.gov.cn/public/detail.php?id=1")
    item.source_type = "court_case"
    source = material.MaterialSource("员工加班费争议", item.url, body, True,
        published_at="2021-10-20T19:02:01", date_verified=True)
    monkeypatch.setattr(material, "collect_material", MagicMock(return_value=material.MaterialBundle(item.title, [source])))
    assert TopicAutomationService(app_settings, logging.getLogger("test"))._hydrate_topic_material(item, deadline=1234)
    assert item.published_at == "2021-10-20T19:02:01"
    assert "原文发布日期：2021年10月20日" in item.snippet
    assert not hasattr(item, "_automatic_approval")


@pytest.mark.parametrize("title,eligible", [
    ("男子付婚介费后相亲对象失联，法院判返还款项", True),
    ("婚约解除后双方争议彩礼返还比例", True),
    ("明星婚礼曝光，网友讨论彩礼传闻", False),
    ("婚介节目嘉宾晒出合影", False),
    ("许昌某华物业有限公司与赵某蕾物业服务合同纠纷案", True),
    ("物业公司发布智能机器人新品", False),
    ("物业公司年度营收增长", False),
])
def test_real_disputed_family_payments_and_services_stay_in_scope(title, eligible):
    from toutiao_content_factory.services.reader_value_service import story_reader_fit
    assert story_reader_fit(topic(title))["eligible"] is eligible


def test_cooldown_is_invalidated_by_new_material_but_new_material_is_not_approval(app_settings, logger, monkeypatch):
    from toutiao_content_factory.services.story_source_service import StorySourceService
    from toutiao_content_factory.services.automation import TopicAutomationService
    from toutiao_content_factory.services import material_search_service as material
    item = topic("物业费收费争议", "https://news.example/report")
    item.source_type = "rss"
    service = StorySourceService(app_settings.output_dir, logger, live_feeds=False)
    service.record_result(item, False)
    assert not service.can_retry(item)
    item.snippet = "\n".join(f"2020年公司安排员工完成第{i}项工作，当事人提供了{i}份考勤记录。法院核对2019年和2020年工作记录，未将其他案件的材料混入。" for i in range(1, 21))
    assert service.can_retry(item)
    assert not getattr(item, "_verified_source_material", False)
    service.record_result(item, False)
    assert not service.can_retry(item)
    collect = MagicMock(side_effect=AssertionError("Sufficient non-court local material needs no fetch"))
    monkeypatch.setattr(material, "collect_material", collect)
    assert TopicAutomationService(app_settings, logger)._hydrate_topic_material(item)
    collect.assert_not_called()


@pytest.mark.parametrize("other_container", [False, True])
def test_missing_court_primary_container_cannot_use_navigation_or_generic_container(other_container):
    from toutiao_content_factory.services.material_search_service import extract_article_body
    navigation = "".join(f"<p>其他新闻介绍第{i}个案件，涉及{i}人和{i}万元的争议，与本案没有已确认联系。</p>" for i in range(1, 40))
    wrapper = '<div id="main-content">' + navigation + '</div>' if other_container else navigation
    page = '<p id="hztitle">物业服务合同纠纷案</p><span class="article_time">发布时间：2021-10-20 19:02:01</span>' + wrapper
    assert extract_article_body(page, min_chars=1) == ""


def test_known_court_url_requires_body_container_even_when_title_header_is_missing():
    from toutiao_content_factory.services.material_search_service import extract_article_body
    page = '<meta property="article:published_time" content="2021-10-20T19:02:01">' + "".join(
        f"<p>其他新闻介绍第{i}个案件，涉及{i}人和{i}万元的争议，与本案没有已确认联系。</p>" for i in range(1, 40))
    assert extract_article_body(page, min_chars=1, require_court_container=True) == ""


@pytest.mark.parametrize("damage", ["undated", "missing_date", "invalid_date", "future_date", "external_redirect", "different_case", "raw_snippet", "digest"])
def test_court_verification_requires_exact_original_with_valid_header_date(app_settings, logger, monkeypatch, damage):
    from toutiao_content_factory.services.automation import TopicAutomationService
    from toutiao_content_factory.services import material_search_service as material
    body = "\n".join(f"2020年公司安排员工完成第{i}项工作，当事人提供了{i}份考勤记录。法院核对2019年和2020年工作记录，未将其他案件的材料混入。" for i in range(1, 21))
    item = topic("物业服务合同纠纷案", "https://www.hncourt.gov.cn/public/detail.php?id=1")
    item.source_type = "court_case"
    source = material.MaterialSource(item.title, item.url, body, True, published_at="2021-10-20T19:02:01", date_verified=True)
    if damage == "undated": source.date_verified = False
    if damage == "missing_date": source.published_at = ""
    if damage == "invalid_date": source.published_at = "2021-99-99"
    if damage == "future_date": source.published_at = "2099-10-20T19:02:01"
    if damage == "external_redirect": source.url = "https://external.example/unrelated"
    if damage == "different_case": source.url = item.url + "2"
    if damage == "digest": source.evidence_type = "search_digest"
    if damage == "raw_snippet":
        item.snippet = body
        source.date_verified = False
    collect = MagicMock(return_value=material.MaterialBundle(item.title, [source]))
    monkeypatch.setattr(material, "collect_material", collect)
    assert not TopicAutomationService(app_settings, logger)._hydrate_topic_material(item)
    assert not getattr(item, "_verified_source_material", False)
    collect.assert_called_once()


def test_court_material_cache_replay_keeps_required_original_date(app_settings, logger, monkeypatch):
    from toutiao_content_factory.services.automation import TopicAutomationService
    from toutiao_content_factory.services import material_search_service as material
    body = "".join(f"<p>2020年婚介服务合同约定第{i}项服务，当事人提供了{i}份付款记录。法院核对2019年和2020年的服务及退款记录，未将其他案件的材料混入。</p>" for i in range(1, 21))
    url = "https://www.hncourt.gov.cn/public/detail.php?id=1"
    def candidate():
        item = topic("婚介服务退款争议", url)
        item.source_type = "court_case"
        return item
    fetch = MagicMock(return_value=(court_page(body, date="2021-10-20 19:02:01"), url, "mock"))
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    service = TopicAutomationService(app_settings, logger)
    assert service._hydrate_topic_material(candidate())
    fetch.assert_called_once()
    fetch.side_effect = AssertionError("Cache replay must not need the network")
    restored = candidate()
    assert service._hydrate_topic_material(restored)
    assert restored.published_at == "2021-10-20T19:02:01"
    cached = next((app_settings.output_dir / "growth_ops/source_material").glob("*.json"))
    payload = json.loads(cached.read_text(encoding="utf-8"))
    payload["sources"][0]["date_verified"] = False
    cached.write_text(json.dumps(payload), encoding="utf-8")
    assert not service._hydrate_topic_material(candidate())
    assert fetch.call_count == 1
