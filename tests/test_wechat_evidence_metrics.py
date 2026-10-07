from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import DataReport, GeneratedContent, QualityReviewReport, TopicCandidate
from toutiao_content_factory.services import material_search_service as material
from toutiao_content_factory.services import wechat_evidence as evidence
from toutiao_content_factory.services import wechat_metrics as metrics
from toutiao_content_factory.services.wechat_revision import bind_issues, begin_revision, finish_revision
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.topic_collection import TopicCollectionService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wechat_cloud_run as cloud


def enable_wechat(settings):
    object.__setattr__(settings, "platform", "wechat")
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


def make_sources():
    return evidence.source_records([
        material.MaterialSource("装机统计", "https://a.test/report?token=private", "2025年光伏装机容量达到10万千瓦，同比增长20%。", True,
                                publisher="能源机构", published_at="2026-01-01", date_verified=True),
        material.MaterialSource("产品披露", "https://b.test/robot", "机器人公司发布新产品，原型已完成实验室测试，尚未进行户外验证。", True),
    ])


def test_sources_map_exact_quotes_and_stable_ids_without_scrape_dates():
    sources = make_sources()
    facts = evidence.build_facts(sources)
    assert len(facts) == 2
    assert facts == evidence.build_facts(sources)
    assert facts[0]["source_id"] == sources[0]["source_id"]
    assert facts[1]["source_id"] == sources[1]["source_id"]
    assert facts[0]["quote"] in sources[0]["original_text"]
    assert facts[0]["statistical_period"] == ["2025年"]
    assert sources[1]["published_at"] == ""
    assert "token" not in sources[0]["url"]
    assert evidence.build_facts([]) == []
    assert DataReport.from_dict({"source_references": ["2026年1月1日某机构"]}).fact_evidence == []


@pytest.mark.parametrize("claim", [
    "2025年风电装机容量达到10万千瓦，同比增长20%。",
    "2025年光伏装机容量达到10万兆瓦，同比增长20%。",
    "2026年光伏装机容量达到10万千瓦，同比增长20%。",
    "光伏装机容量达到10万千瓦，同比增长20%。",
])
def test_same_number_wrong_subject_unit_or_period_is_unsupported(claim):
    facts = evidence.build_facts(make_sources())
    assert not evidence.supporting_facts(claim, facts)
    assert evidence.unsupported_claims(claim, facts)


def test_publication_metadata_is_not_a_statistical_period():
    metadata = evidence.source_metadata('<meta property="article:published_time" content="2026-01-01"><meta property="og:site_name" content="机构">')
    assert metadata["published_at"] == "2026-01-01T00:00:00"
    assert evidence.source_metadata('<body>抓取日期2026年1月1日；截至2025年12月</body>')["published_at"] == ""
    assert evidence.source_metadata('<meta property="article:modified_time" content="2026-01-01">')["published_at"] == ""
    assert not evidence.source_metadata('<meta name="publishdate" content="2026-02-31">')["date_verified"]


def test_two_numbers_in_same_source_cannot_be_swapped_between_financial_metrics():
    source = material.MaterialSource("财报", "https://a.test", "2025年甲公司营业收入达到10亿元，净利润达到20亿元。", True)
    facts = evidence.build_facts(evidence.source_records([source]))
    assert not evidence.supporting_facts("2025年甲公司营业收入达到20亿元。", facts)
    assert evidence.supporting_facts("2025年甲公司营业收入达到10亿元。", facts)
    assert not evidence.supporting_facts("2025年乙公司营业收入达到10亿元。", facts)


def test_same_percentage_is_not_interchangeable_between_growth_and_share():
    facts = evidence.build_facts(make_sources())
    assert not evidence.supporting_facts("2025年光伏装机容量占比20%。", facts)


def test_enumerated_capacities_keep_distinct_objects_units_and_forecast_scope():
    source = material.MaterialSource("移动充电站", "https://a.test/charging",
                                    "设备以单车储电约3000度、可同时为8辆车快充、预计服务80辆车的能力补位。", True)
    sources = evidence.source_records([source])
    facts = evidence.build_facts(sources)
    assert [item['statistical_object'] for item in facts[0]['measurements']] == ["设备以单车储电", "可同时", "预计服务"]
    assert [item['unit'] for item in facts[0]['measurements']] == ["度", "辆", "辆"]
    assert evidence.supporting_facts("设备以单车储电约3000度进行补位。", facts)
    assert evidence.supporting_facts("设备可同时为8辆车快充。", facts)
    assert not evidence.unsupported_claims("预计服务80辆车。", facts)
    assert evidence.unsupported_claims("设备可同时为80辆车快充。", facts)
    assert evidence.unsupported_claims("预计服务80度。", facts)
    assert evidence.unsupported_claims("服务80辆车。", facts)


def test_enumerated_financial_metrics_cannot_exchange_numbers():
    source = material.MaterialSource("财报", "https://a.test/earnings",
                                    "2025年甲公司营业收入达到10亿元、净利润达到20亿元。", True)
    facts = evidence.build_facts(evidence.source_records([source]))
    assert evidence.supporting_facts("2025年甲公司营业收入达到10亿元。", facts)
    assert evidence.supporting_facts("2025年甲公司净利润达到20亿元。", facts)
    assert not evidence.supporting_facts("2025年甲公司营业收入达到20亿元。", facts)


def test_citations_restored_after_polish_before_audit_without_invented_sources():
    sources = make_sources()
    report = DataReport(source_records=sources, fact_evidence=evidence.build_facts(sources))
    raw = sources[0]["original_text"] + "\n\n未来的适用条件仍需观察。\n\n2026年风电装机容量达到99万千瓦。"
    article = evidence.prepare_article(raw, report)
    assert "99" not in article
    assert "据《装机统计》报道" in article
    assert "发布日期未核实" in article
    assert "https://a.test/report" in article
    assert article == evidence.prepare_article(article, report)
    polished = article.split("参考来源：", 1)[0]
    assert "参考来源：" in evidence.prepare_article(polished, report)


def test_numeric_source_title_is_not_a_body_measurement_or_business_claim():
    sources = evidence.source_records([material.MaterialSource(
        "新型储能5年增长45倍，订单与利润仍需核实", "https://a.test/policy",
        "能源机构发布管理办法，明确新能源项目适用条件和实施措施。", True)])
    facts = evidence.build_facts(sources)
    report = DataReport(source_records=sources, fact_evidence=facts)
    article = evidence.prepare_article(sources[0]["original_text"], report)
    assert "据《新型储能5年增长45倍" in article
    assert evidence.unsupported_claims(article, facts) == []
    assert evidence.unsupported_claims("据《新型储能5年增长45倍》报道，2026年装机容量达到99万千瓦。", facts)


def test_generic_source_opinion_is_not_financial_or_order_evidence():
    sources = evidence.source_records([material.MaterialSource(
        "政策", "https://a.test/policy", "利润将向产业链核心环节集中。订单验证速度成为衡量产业链兑现能力的关键指标。", True)])
    facts = evidence.build_facts(sources)
    report = DataReport(source_records=sources, fact_evidence=facts)
    prepared = evidence.prepare_article(sources[0]["original_text"] + "\n\n适用条件仍需进一步明确。", report)
    assert "利润将" not in prepared and "订单验证速度" not in prepared
    assert "适用条件仍需进一步明确" in prepared
    financial = evidence.build_facts(evidence.source_records([material.MaterialSource(
        "财报", "https://a.test/earnings", "2025年甲公司净利润达到10亿元。", True)]))
    assert not evidence.unsupported_claims("2025年甲公司净利润达到10亿元。", financial)


def test_mobile_splitting_preserves_complete_measurements_and_is_idempotent():
    sentence = "2025年光伏装机容量达到10万千瓦，同比增长20%。"
    prose = sentence + "政策适用范围仍需明确，各地实施条件和公开披露进展应持续跟踪。" * 6
    sources = evidence.source_records([material.MaterialSource("装机5年数据", "https://a.test/policy", sentence, True)])
    report = DataReport(source_records=sources, fact_evidence=evidence.build_facts(sources))
    prepared = evidence.prepare_article(prose, report)
    body = prepared.split("参考来源：", 1)[0].strip()
    assert all(len(part) <= 180 for part in body.split("\n\n"))
    assert sentence in body
    assert evidence.unsupported_claims(prepared, report.fact_evidence) == []
    assert evidence.prepare_article(prepared, report) == prepared


def test_type_requirements_override_upstream_financial_and_format_instructions(app_settings):
    from toutiao_content_factory.models import ExtractionResult, TrendReport
    from toutiao_content_factory.agents.polish_agent import PolishAgent
    enable_wechat(app_settings)
    sources = evidence.source_records([material.MaterialSource(
        "政策", "https://a.test/policy", "能源机构发布管理办法，明确新能源项目适用条件和实施措施。", True)])
    extraction = ExtractionResult("", [], [], [], [], [], [], source_records=sources, fact_evidence=evidence.build_facts(sources), article_kind="policy_explainer")
    report = DataReport(format_brief="预测产业链利润归属", paragraph_instruction="讨论订单验证速度")
    trend = TrendReport(recommended_angle="利润将向产业链核心环节集中", hot_trends=["订单验证速度是兑现能力的关键指标"])
    evidence.apply_requirements(report, extraction, trend)
    brief = evidence.requirements("policy_explainer")
    assert trend.recommended_angle == report.suggested_angle == brief["suggested_angle"]
    assert trend.hot_trends == []
    assert report.format_brief == brief["writing_brief"]
    assert report.paragraph_instruction.startswith(brief["instruction"])
    polish = PolishAgent(app_settings, MagicMock(), MagicMock(), MagicMock())
    notes, _ = polish._validate_article("适用条件仍需进一步明确。", report, trend)
    assert "正文主体感仍偏弱，建议明确公司、客户、订单方或交付项目。" not in notes
    assert "正文对产业链位置展开还不够，建议补上环节分工与利润归属。" not in notes


@pytest.mark.parametrize("topic_type,quote,expected", [
    ("policy_market", "能源机构发布管理办法，明确新能源项目适用条件和实施措施。", "policy_explainer"),
    ("product_launch", "机器人公司发布产品，完成室内测试，尚未完成户外验证。", "product_progress"),
    ("earnings_signal", "2025年甲公司营业收入达到10亿元，净利润同比增长20%。", "company_earnings"),
    ("earnings_signal", "行业融资规模达到10亿元，后续验证进展仍需观察。", "industry_update"),
    ("unknown", "机器人产业应用条件仍需等待独立验证与后续公开披露。", "industry_update"),
])
def test_kind_classification_requires_actual_original_facts(topic_type, quote, expected):
    source = material.MaterialSource("披露", "https://a.test", quote, True)
    facts = evidence.build_facts(evidence.source_records([source]))
    assert evidence.classify_kind(topic_type, facts) == expected
    brief = evidence.requirements(expected)
    assert brief["suggested_angle"] in brief["writing_brief"]
    assert brief["follow_up_focus"] in brief["instruction"]
    assert brief["closing"] in brief["instruction"]


def test_revision_omission_or_word_change_never_proves_resolution():
    before = "装机统计必须说明统计对象和统计期。"
    old = QualityReviewReport(fact_issues=["明确统计期"], issues=[{"requirement": "明确统计期", "paragraph": before}])
    bind_issues(old, before, [])
    trace = begin_revision(old, before, "装机数字仍需说明具体对象和期间。", 1)
    new = QualityReviewReport(verdict="approved")
    finish_revision(trace, new, "装机数字仍需说明具体对象和期间。", [])
    assert trace["issues"][0]["status"] == "unverifiable"
    assert trace["issues"][0]["issue_id"] == old.issues[0]["issue_id"]
    assert new.verdict == "approved"  # Tracking does not override the council.


def test_revision_resolution_requires_explicit_reaudit_quote_and_matching_evidence():
    facts = evidence.build_facts(make_sources())
    before, after = "装机容量达到10万千瓦。", facts[0]["quote"]
    old = QualityReviewReport(fact_issues=["明确统计期"], issues=[{"requirement": "明确统计期", "paragraph": before}])
    bind_issues(old, before, facts)
    trace = begin_revision(old, before, after, 1)
    evaluation = {"issue_id": old.issues[0]["issue_id"], "status": "resolved", "after_quote": after,
                  "evidence_ids": [facts[1]["fact_id"]], "verification": "与统计原句逐项核对"}
    new = QualityReviewReport(issue_evaluations=[evaluation])
    finish_revision(trace, new, after, facts)
    assert trace["issues"][0]["status"] == "unverifiable"
    evaluation["evidence_ids"] = [facts[0]["fact_id"]]
    finish_revision(trace, new, after, facts)
    assert trace["issues"][0]["status"] == "resolved"


def test_noop_revision_stays_present_even_if_model_says_resolved():
    body = "段落结构需要调整并说明适用条件。"
    report = QualityReviewReport(structure_issues=["调整段落"], issues=[{"requirement": "调整段落", "paragraph": body}])
    bind_issues(report, body, [])
    trace = begin_revision(report, body, body, 1)
    report.issue_evaluations = [{"issue_id": report.issues[0]["issue_id"], "status": "resolved", "after_quote": body, "verification": "已修改"}]
    finish_revision(trace, report, body, [])
    assert trace["issues"][0]["status"] == "still_present"


def test_weak_candidates_are_rescored_and_enrichment_has_separate_cap(app_settings, logger, monkeypatch):
    enable_wechat(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    topics = [TopicCandidate(f"机器人公司新品发布{i}", f"https://a.test/{i}", "rss", "rss", published_at=datetime.now(metrics.BEIJING).isoformat(),
                             matched_keywords=["机器人"], quality_flags=["title_only"], evidence_strength=20) for i in range(15)]
    def hydrate(topic, **kwargs):
        assert kwargs["deadline"] <= time.monotonic() + 60
        topic.snippet = "机器人公司新产品已完成测试。" * 70
        return True
    hydrator = MagicMock(side_effect=hydrate)
    monkeypatch.setattr(app.topic_automation, "_hydrate_topic_material", hydrator)
    rescored = MagicMock(side_effect=lambda topic: setattr(topic, "evidence_strength", 80))
    monkeypatch.setattr(TopicCollectionService, "_enrich_topic", rescored)
    app._enrich_wechat_candidates(topics)
    assert hydrator.call_count == rescored.call_count == 10
    assert app._wechat_enrichment_count == 10
    assert topics[0].quality_flags == ["title_only"]  # Never blindly delete flags.
    app._enrich_wechat_candidates(topics)
    assert hydrator.call_count == 10


def test_exhausted_budget_starts_no_io(app_settings, logger):
    enable_wechat(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    app._wechat_enrichment_deadline = time.monotonic() - 1
    app._wechat_enrichment_count = 0
    app._wechat_enriched_topics = {}
    app.topic_automation._hydrate_topic_material = MagicMock()
    app._enrich_wechat_candidates([TopicCandidate("机器人发布", "", "", "")])
    app.topic_automation._hydrate_topic_material.assert_not_called()
    with pytest.raises(material.MaterialBudgetExceeded):
        material.fetch_text_smart("https://a.test", deadline=time.monotonic() - 1)


def test_enrichment_system_fault_is_not_a_weak_candidate(app_settings, logger):
    enable_wechat(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    app.topic_automation._hydrate_topic_material = MagicMock(side_effect=material.MaterialSearchError("browser failed"))
    with pytest.raises(material.MaterialSearchError):
        app._enrich_wechat_candidates([TopicCandidate("机器人产品发布", "", "", "", published_at=datetime.now(metrics.BEIJING).isoformat(), matched_keywords=["机器人"])])


def publication_inputs():
    now = datetime.now(metrics.BEIJING).replace(microsecond=0)
    body = "机器人新产品在明确条件下完成测试，后续应用边界需要进一步验证。" * 20
    expected = {"account": "小椰科技", "title": "机器人新品进展", "submitted_at": (now - timedelta(minutes=1)).isoformat(), "body_hash": metrics.body_hash(body)}
    backend = {"account": "小椰科技", "title": expected["title"], "status": "published", "published_at": now.isoformat(), "url": "https://mp.weixin.qq.com/s/new"}
    article = {**backend, "body": body}
    return expected, backend, article


@pytest.mark.parametrize("field,value", [("account", "其他账号"), ("title", "另一篇"), ("body", "旧正文"), ("published_at", "2025-01-01T00:00:00+08:00")])
def test_formal_publication_needs_exact_account_title_date_and_body(field, value):
    expected, backend, article = publication_inputs()
    article[field] = value
    assert metrics.match_publication(expected, backend, article) is None


def test_formal_publication_requires_backend_record_and_full_public_date():
    expected, backend, article = publication_inputs()
    assert metrics.match_publication(expected, backend, article)["confirmed"] is True
    backend["status"] = "submitted"
    assert metrics.match_publication(expected, backend, article) is None
    backend["status"] = "published"
    backend["published_at"] = datetime.now(metrics.BEIJING).strftime("%m月%d日")
    assert metrics.match_publication(expected, backend, article)
    article["published_at"] = ""
    assert metrics.match_publication(expected, backend, article) is None


def test_metrics_dedup_pending_confirmation_and_denominator():
    expected, backend, article = publication_inputs()
    record = {"run_key": "1:1", "started_at": expected["submitted_at"], "mode": "publish", "outcome": "pending_review", "excluded": False}
    state = metrics.merge_run({}, record)
    state = metrics.merge_run(state, record)
    assert len(state["runs"]) == 1
    assert metrics.summary(state)["7"]["confirmed_articles"] == 0
    proof = metrics.match_publication(expected, backend, article)
    state = metrics.merge_run(state, {**record, "outcome": "published_confirmed", "publication": proof})
    state = metrics.merge_run(state, {**record, "run_key": "1:2", "outcome": "published_confirmed", "publication": proof})
    state = metrics.merge_run(state, {**record, "run_key": "2:1", "mode": "check"})
    state = metrics.merge_run(state, {**record, "run_key": "3:1", "excluded": True})
    state = metrics.merge_run(state, record)  # Stale rerun cannot downgrade proof.
    stats = metrics.summary(state)["7"]
    assert stats["actual_publish_runs"] == 2
    assert stats["confirmed_articles"] == 1
    assert stats["publication_rate"] == .5
    state = metrics.merge_run(state, {**record, "run_key": "old:1", "started_at": (datetime.now(metrics.BEIJING) - timedelta(days=91)).isoformat()})
    assert len(state["runs"]) == 4


@pytest.mark.parametrize("status", ["published", "success", "submitted", "submitted_for_review"])
def test_submit_success_is_not_formal_publication(status):
    assert metrics.outcome({"wechat_status": status}) == "pending_review"


def test_metrics_write_failure_preserves_confirmed_result_and_never_submits(app_settings, monkeypatch):
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    expected, backend, article = publication_inputs()
    report = {"status": "success", "wechat_status": "published", "publication": metrics.match_publication(expected, backend, article)}
    monkeypatch.setattr(cloud, "write_state_file", MagicMock(side_effect=OSError("state unavailable")))
    child = MagicMock()
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.complete_run(app_settings, report, 0, metrics_state={}, run_key="4:1", mode="publish", started_at=expected["submitted_at"]) == 1
    saved = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert saved["publication"]["confirmed"] is True
    assert saved["wechat_status"] == "published"
    assert "metrics_persistence_failed" in saved["reason_codes"]
    child.assert_not_called()


def test_old_reports_have_empty_additive_fields_and_do_not_upgrade_attribution():
    report = DataReport.from_dict({"source_references": ["能源部门，2026年发布"]})
    assert report.source_records == report.fact_evidence == []
    assert report.article_kind == ""
    assert GeneratedContent.from_dict({}).revision_trace == []
    assert QualityReviewReport.from_dict({}).issues == []


def test_toutiao_candidate_path_never_enriches(app_settings, logger):
    app = ContentFactoryAppService(app_settings, logger)
    topic = TopicCandidate("机器人公司产品发布", "https://a.test", "rss", "rss", evidence_strength=80, quality_score=80)
    app.topic_automation._load_daily_topics = MagicMock(return_value=MagicMock(topics=[topic]))
    app.topic_automation._available_topics = MagicMock(return_value=[topic])
    app.topic_automation._hydrate_topic_material = MagicMock(side_effect=AssertionError("Cross-channel enrichment"))
    assert app._build_daily_candidate_specs(topic_rank=None, topic_title=None, refresh_cache=False, dry_run=False)
    app.topic_automation._hydrate_topic_material.assert_not_called()


@pytest.mark.parametrize("confirmed", [True, False])
def test_pending_run_is_only_reconciled_without_submitting(app_settings, monkeypatch, confirmed):
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    expected, backend, article = publication_inputs()
    pending = {"run_key": "10:1", "started_at": expected["submitted_at"], "mode": "publish", "outcome": "pending_review", "expected_publication": expected}
    day = datetime.now(metrics.BEIJING).strftime("%Y-%m-%d")
    guard = {"entries": {day + "::default": {"wechat": {"status": "submitted_for_review"}}}}
    monkeypatch.setenv("GITHUB_RUN_ID", "11")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw:
        {"schema": 1, "runs": [pending]} if kw.get("path") == metrics.METRICS_PATH else guard)
    proof = metrics.match_publication(expected, backend, article)
    verifier = MagicMock(return_value={"10:1": proof} if confirmed else {})
    monkeypatch.setattr(cloud, "verify_pending", verifier)
    writer = MagicMock()
    monkeypatch.setattr(cloud, "write_state_file", writer)
    child = MagicMock(side_effect=AssertionError("Pending publication must not be resubmitted"))
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.main(["--mode", "publish"]) == 0
    child.assert_not_called()
    rows = writer.call_args.args[0]["runs"]
    original = next(row for row in rows if row["run_key"] == "10:1")
    assert original["outcome"] == ("published_confirmed" if confirmed else "pending_review")
    assert metrics.summary(writer.call_args.args[0])["7"]["actual_publish_runs"] == 1


def test_collection_retains_only_safe_thin_technology_for_later_enrichment(app_settings, logger):
    enable_wechat(app_settings)
    collector = TopicCollectionService(app_settings, logger)
    robot = TopicCandidate("机器人新品测试", "", "", "", matched_keywords=["机器人"], quality_flags=["title_only"])
    unrelated = TopicCandidate("明星离婚热搜", "", "", "", matched_keywords=["热点"], quality_flags=["title_only"])
    assert collector._wechat_enrichment_candidate(robot)
    assert not collector._wechat_enrichment_candidate(unrelated)
    object.__setattr__(app_settings, "platform", "今日头条")
    assert not collector._wechat_enrichment_candidate(robot)


def test_source_url_keeps_article_identity_and_discards_credentials():
    assert evidence.public_url("https://baijiahao.baidu.com/s?id=123&token=secret&utm_campaign=x") == "https://baijiahao.baidu.com/s?id=123"
    assert evidence.public_url("https://mp.weixin.qq.com/cgi-bin/appmsg?token=secret") == ""


def test_collector_passes_one_deadline_and_collects_no_more_than_three_sources(monkeypatch, tmp_path):
    deadline = time.monotonic() + 60
    hits = [{"title": f"机器人新品发布{i}", "url": f"https://a{i}.test/article"} for i in range(6)]
    searches = MagicMock(return_value=hits)
    monkeypatch.setattr(material, "search_web", searches)
    seen = []
    def fetch(url, **kwargs):
        seen.append(kwargs["deadline"])
        return '<meta property="article:published_time" content="2026-10-06">', url, "http"
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    monkeypatch.setattr(material, "extract_article_body", lambda *a, **kw: "机器人完成测试，验证条件尚未披露。" * 30)
    bundle = material.collect_material("机器人新品发布", max_sources=3, preserve_sources=True, deadline=deadline, cache_dir=tmp_path)
    assert len(bundle.sources) == len(seen) == 3
    assert seen == [deadline] * 3
    assert all(call.kwargs["deadline"] == deadline for call in searches.call_args_list)
    assert all(source.date_verified for source in bundle.sources)


def test_no_original_sources_never_promotes_search_snippets(monkeypatch):
    monkeypatch.setattr(material, "search_web", lambda *a, **kw: [{"title": "机器人新品", "url": "https://a.test", "snippet": "实际产品验证条件尚未披露。" * 40}])
    monkeypatch.setattr(material, "fetch_text_smart", lambda *a, **kw: ("empty", "https://a.test", "http"))
    monkeypatch.setattr(material, "extract_article_body", lambda *a, **kw: "")
    bundle = material.collect_material("机器人新品发布", preserve_sources=True, deadline=time.monotonic() + 60)
    assert not bundle.usable
    assert evidence.source_records(bundle.sources) == []


def test_wechat_hydration_uses_new_cache_and_does_not_upgrade_legacy_snippet(app_settings, logger, monkeypatch):
    enable_wechat(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    original = "\n".join(f"机器人公司完成第{i}项测试，测试产品达到20台，公开披露的验证条件仍需进一步核对。" for i in range(1, 25))
    source = material.MaterialSource("新原文", "https://a.test", original, True)
    collector = MagicMock(return_value=material.MaterialBundle("机器人新品", [source]))
    monkeypatch.setattr(material, "collect_material", collector)
    topic = TopicCandidate("机器人新品", "https://a.test", "rss", "rss", snippet="【来源：匿名旧汇总】" + "机器人此前达到99台。" * 100)
    assert app.topic_automation._hydrate_topic_material(topic)
    assert "wechat_source_material_v7" in str(collector.call_args.kwargs["cache_dir"])
    assert topic.source_records[0]["original_text"] == source.body
    assert "99" not in topic.source_records[0]["original_text"]


def test_layout_must_preserve_reviewed_body_and_fingerprint_ignores_style():
    article = "机器人公司发布产品。\n\n测试条件尚未公开。"
    rendered = "<style>.article{width:500px}</style><p>机器人公司发布产品。</p><p>测试条件尚未公开。</p>"
    metrics.assert_reviewed_body_preserved(article, rendered)
    assert metrics.html_body_hash(rendered) == metrics.body_hash(article)
    with pytest.raises(ValueError, match="reviewed article"):
        metrics.assert_reviewed_body_preserved(article, rendered.replace("测试条件尚未公开", "测试已完成独立验证"))


@pytest.mark.parametrize("kind", list(evidence.KINDS))
def test_writer_polish_and_data_use_same_kind_rules(app_settings, logger, mock_openai_service, kind):
    from toutiao_content_factory.agents.writer_agent import WriterAgent
    from toutiao_content_factory.agents.polish_agent import PolishAgent
    from toutiao_content_factory.models import ExtractionResult
    enable_wechat(app_settings)
    extraction = ExtractionResult("", [], [], [], [], [], [], article_kind=kind)
    report = DataReport(watch_metrics=["利润兑现", "回款"], suggested_angle="订单兑现")
    evidence.apply_requirements(report, extraction)
    writer = WriterAgent(app_settings, MagicMock(), mock_openai_service, logger)
    polish = PolishAgent(app_settings, MagicMock(), mock_openai_service, logger)
    brief = evidence.requirements(kind)
    assert report.suggested_angle == brief["suggested_angle"]
    assert report.follow_up_focus == brief["follow_up_focus"]
    assert brief["instruction"] in writer._dynamic_rules_for(report)
    assert writer._format_brief_for_prompt(report) == brief["writing_brief"]
    assert writer._finalize_comment_prompt("利润何时兑现？", report) == polish._build_closing_question(report) == brief["closing"]
