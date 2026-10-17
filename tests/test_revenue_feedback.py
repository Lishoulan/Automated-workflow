from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from dataclasses import asdict

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.revenue_feedback import (
    SELECTION_PROTOCOL, adaptive_selection_enabled, build_revenue_feedback, rank_revenue_topics,
)

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone(timedelta(hours=8)))


def cohort(category="energy", *, reads=100, count=15, policy=SELECTION_PROTOCOL, variant="result_first", kind="weitoutiao"):
    works, entries = [], {}
    for i in range(count):
        gid = f"{category}-{policy}-{variant}-{kind}-{i}"
        created = (NOW - timedelta(hours=96)).isoformat()
        captured = (NOW - timedelta(hours=24)).isoformat()
        experiment = {"id": "story_opening_v1" if kind == "weitoutiao" else "article_title_v1", "variable": "opening_order", "variant": variant,
                      "protocol_version": 4 if kind == "weitoutiao" else 1, "selection_protocol": policy, "category": category}
        work = {"gid": gid, "title": gid, "created_at": created, "type": kind, "content_experiment": experiment}
        works.append(work)
        entries[gid] = {**work, "checkpoints": {"72h": {"captured_at": captured, "capture_delay_hours": 0,
            "impressions": 1000, "reads": reads, "comments": 1}}}
    return works, entries


def report(*cohorts):
    works, entries = [], {}
    for w, e in cohorts:
        works.extend(w)
        entries.update(e)
    return build_revenue_feedback({"observed_at": NOW.isoformat(), "account": "robot_notes", "works": works}, {"works": entries})


def enabled(settings):
    object.__setattr__(settings.revenue_optimization, "adaptive_topic_selection", True)
    object.__setattr__(settings, "account_key", "robot_notes")
    return settings


def topic(category, title="有原始来源的产品实测公布具体条件"):
    return TopicCandidate(title, "https://source.example/report/1", "原始报道", "news", topic_bucket=category,
                          published_at=NOW.isoformat(), quality_score=80, evidence_strength=80)


def test_unknown_income_and_small_samples_do_not_become_profitable_niche(app_settings):
    feedback = report(cohort(count=2))
    group = feedback["groups"][0]
    assert group["samples"] == 2 and group["settled_revenue"] is None
    assert group["revenue_per_1000_reads"] is None
    assert group["priority_delta"] == 0 and group["status"] == "collecting"
    ranked = rank_revenue_topics([topic("energy")], enabled(app_settings), {"revenue_feedback": feedback}, content_type="weitoutiao", now=NOW)
    assert ranked[0].revenue_selection["competition"] is None
    assert ranked[0].revenue_selection["historical_delta"] == 0


def test_compatible_conversion_can_influence_ranking_but_never_stop_a_niche(app_settings):
    feedback = report(cohort("energy", reads=100), cohort("technology", reads=10))
    ranked = rank_revenue_topics([topic("technology"), topic("energy")], enabled(app_settings), {"revenue_feedback": feedback}, content_type="weitoutiao", now=NOW)
    assert ranked[0].topic_bucket == "energy"
    assert len(ranked) == 2
    assert ranked[0].revenue_selection["historical_delta"] > 0
    # A stale or wrong-account report cannot influence production.
    stale = deepcopy(feedback)
    stale["observed_at"] = (NOW - timedelta(days=4)).isoformat()
    wrong = {**feedback, "account": "another_account"}
    for invalid in (stale, wrong):
        row = rank_revenue_topics([topic("energy")], app_settings, {"revenue_feedback": invalid}, content_type="weitoutiao", now=NOW)[0]
        assert row.revenue_selection["historical_delta"] == 0


@pytest.mark.parametrize("change", ["late", "negative", "fake_time", "old_protocol", "other_policy", "unassigned", "wrong_identity"])
def test_invalid_or_incompatible_samples_cannot_tune(change):
    works, entries = cohort()
    for work in works:
        entry = entries[work["gid"]]
        sample = entry["checkpoints"]["72h"]
        if change == "late": sample["capture_delay_hours"] = 25
        if change == "negative": sample["reads"] = -1
        if change == "fake_time": sample["captured_at"] = NOW.isoformat()
        if change == "old_protocol": entry["content_experiment"] = {**entry["content_experiment"], "protocol_version": 3}
        if change == "other_policy": entry["content_experiment"] = {**entry["content_experiment"], "selection_protocol": "legacy"}
        if change == "unassigned": entry.pop("content_experiment")
        if change == "wrong_identity": entry["title"] = "old title"
    feedback = report((works, entries), cohort("technology", reads=10))
    assert all(g["priority_delta"] == 0 for g in feedback["groups"])


def test_money_needs_exact_identity_scope_and_cutoff_not_just_same_day():
    works, entries = cohort()
    for work in works:
        sample = entries[work["gid"]]["checkpoints"]["72h"]
        sample.update(platform_revenue=.1, revenue_amount_known=True, platform_revenue_status="available",
                      revenue_gid=work["gid"], revenue_settled=True, revenue_scope="checkpoint_cumulative",
                      revenue_counters_captured_at=sample["captured_at"],
                      revenue_source="/mp/agw/statistic/v2/toutiao_income/article_income")
    exact = report((works, entries))["groups"][0]
    assert exact["settled_revenue"] == 1.5 and exact["revenue_per_1000_reads"] == 1
    assert exact["priority_delta"] == 0  # no compatible comparator
    for entry in entries.values():
        entry["checkpoints"]["72h"]["revenue_counters_captured_at"] = NOW.isoformat()
    assert report((works, entries))["groups"][0]["revenue_per_1000_reads"] is None


def test_windows_variants_and_formats_are_separate():
    feedback = report(cohort("energy", count=8), cohort("energy", count=8, variant="situation_first"),
                      cohort("energy", count=8, kind="article"), cohort("technology", reads=10))
    assert len(feedback["groups"]) == 4
    assert all(g["priority_delta"] == 0 for g in feedback["groups"])


def test_settled_return_changes_priority_only_with_aligned_complete_money(app_settings):
    high, low = cohort("energy"), cohort("technology")
    for works, entries in (high, low):
        for i, work in enumerate(works):
            sample = entries[work["gid"]]["checkpoints"]["72h"]
            sample.update(platform_revenue=.1 if works is high[0] else 0,
                          revenue_amount_known=True, revenue_settled=True, revenue_gid=work["gid"],
                          revenue_scope="checkpoint_cumulative", revenue_counters_captured_at=sample["captured_at"],
                          revenue_source="/mp/agw/statistic/v2/toutiao_income/article_income")
    feedback = report(high, low)
    assert [g["priority_delta"] for g in feedback["groups"]] == [8, -8]
    rows = rank_revenue_topics([topic("technology"), topic("energy")], enabled(app_settings), {"revenue_feedback": feedback}, content_type="weitoutiao", now=NOW)
    assert rows[0].topic_bucket == "energy"
    restored = TopicCandidate.from_dict(asdict(rows[0]))
    assert restored.revenue_selection["historical_delta"] == 8


def test_reader_rate_alone_cannot_win_by_losing_exposure():
    high = cohort("energy", reads=100)
    for entry in high[1].values():
        entry["checkpoints"]["72h"]["impressions"] = 200
    feedback = report(high, cohort("technology", reads=50))
    assert all(g["priority_delta"] == 0 for g in feedback["groups"])


def test_actual_submission_join_retains_selection_and_category():
    from scripts.collect_growth_snapshot import attach_opening_experiments, update_checkpoints
    from toutiao_content_factory.services.growth_feedback_service import opening_experiment_summary
    works, entries = cohort(count=1)
    work = works[0]
    experiment = work.pop("content_experiment")
    record = {**experiment, "title": "clipped", "submitted_opening": work["title"],
              "submitted_at": work["created_at"], "status": "published"}
    snapshot = {"observed_at": NOW.isoformat(), "works": works}
    attach_opening_experiments(snapshot, {"records": {"run": record}})
    assert work["content_experiment"] == experiment
    work.update(age_hours=96, impressions=1000, reads=100, comments=1)
    ledger, _ = update_checkpoints(snapshot, {}, NOW)
    assert ledger["works"][work["gid"]]["content_experiment"] == experiment
    assert sum(g["samples"] for g in opening_experiment_summary(ledger)["groups"]) == 0


def test_article_uses_existing_title_variant_separately_and_requires_exact_platform_id():
    from scripts.collect_growth_snapshot import attach_article_experiments
    from toutiao_content_factory.models import PublishRecord
    work = {"gid": "123456789", "title": "实测条件已公布", "type": "article", "created_at": NOW.isoformat()}
    experiment = {"id": "article_title_v1", "protocol_version": 1, "variable": "title_pattern",
                  "variant": "condition_first", "selection_protocol": SELECTION_PROTOCOL, "category": "technology"}
    record = PublishRecord(NOW.isoformat(), "toutiao", work["title"], "run", "topic", "url", "published",
                           external_url="https://www.toutiao.com/article/123456789/", content_experiment=experiment)
    restored = PublishRecord.from_dict(asdict(record))
    assert restored.content_experiment == experiment
    snapshot = {"works": [work]}
    attach_article_experiments(snapshot, [asdict(record)])
    assert work["content_experiment"] == experiment
    work.pop("content_experiment")
    attach_article_experiments(snapshot, [{**asdict(record), "external_url": ""}])
    assert "content_experiment" not in work


def test_production_history_loader_preserves_article_metadata_through_checkpoint(tmp_path):
    import json
    from scripts.collect_growth_snapshot import _load_json, attach_article_experiments, update_checkpoints
    works, entries = cohort("technology", count=1, kind="article")
    work = works[0]
    work["gid"] = "123456789"
    exp = work.pop("content_experiment")
    record = {"platform": "toutiao", "status": "published", "title": work["title"],
              "external_url": "https://www.toutiao.com/article/123456789/", "published_at": work["created_at"],
              "content_experiment": exp}
    path = tmp_path / "publish_history.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    snapshot = {"works": [work], "observed_at": NOW.isoformat(), "account": "robot_notes"}
    history = _load_json(path, expected_type=list)
    assert _load_json(path) == {}  # existing dictionary consumers stay strict
    attach_article_experiments(snapshot, history)
    work.update(age_hours=96, impressions=1000, reads=20, comments=0)
    ledger, _ = update_checkpoints(snapshot, {}, NOW)
    assert ledger["works"][work["gid"]]["content_experiment"] == exp
    assert build_revenue_feedback(snapshot, ledger)["groups"][0]["format"] == "article"


@pytest.mark.parametrize("old", ["old_protocol", "other_category", "other_variant", "unassigned"])
def test_collector_cannot_reclassify_captured_experimental_identity(old):
    from scripts.collect_growth_snapshot import update_checkpoints
    works, entries = cohort(count=1)
    work = works[0]
    entry = entries[work["gid"]]
    entry["content_experiment"] = dict(entry["content_experiment"])
    if old == "old_protocol": entry["content_experiment"]["protocol_version"] = 3
    if old == "other_category": entry["content_experiment"]["category"] = "other"
    if old == "other_variant": entry["content_experiment"]["variant"] = "situation_first"
    if old == "unassigned": entry.pop("content_experiment")
    before = deepcopy(entry)
    work.update(age_hours=97, impressions=1100, reads=100, comments=1)
    observed = NOW + timedelta(hours=1)
    snapshot = {"account": "robot_notes", "observed_at": observed.isoformat(), "works": works}
    ledger, _ = update_checkpoints(snapshot, {"works": entries}, observed)
    saved = ledger["works"][work["gid"]]
    assert saved.get("content_experiment") == before.get("content_experiment")
    assert all(saved["checkpoints"]["72h"][key] == value for key, value in before["checkpoints"]["72h"].items())
    assert build_revenue_feedback(snapshot, ledger)["groups"] == []


def test_adaptive_editorial_scope_keeps_quality_and_wechat_gates(app_settings, logger):
    from toutiao_content_factory.services.topic_collection import TopicCollectionService
    from toutiao_content_factory.services.automation import TopicAutomationService
    from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator
    enabled(app_settings)
    object.__setattr__(app_settings.topic_collection, "focus_industries", ["social_story"])
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    new = topic("open_new_category")
    collection = TopicCollectionService(app_settings, logger)
    counts = {"low_quality": 0, "weak_focus": 0, "industry_mismatch": 0}
    assert collection._filter_to_focus_industries([new], counts) == [new]
    assert collection._passes_topic_focus(new, counts)
    assert TopicAutomationService(app_settings, logger)._topic_matches_account_boundary(new)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logger)
    generator._load_growth_feedback = lambda: {"topic_feedback": {"stop_titles": [new.title]}}
    assert generator._rank_topics([new]) == [new]
    bad = topic("open_new_category", "买入稳赚翻倍，保证收益")
    bad.quality_flags = ["insufficient_evidence"]
    collection._has_blocking_quality_issue = lambda value: value is bad
    assert not collection._passes_topic_focus(bad, counts)
    object.__setattr__(app_settings, "platform", "wechat")
    assert not adaptive_selection_enabled(app_settings)
    assert not TopicAutomationService(app_settings, logger)._topic_matches_account_boundary(new)


def test_new_category_writing_answers_source_question_without_forcing_a_family_case(app_settings, logger):
    from unittest.mock import MagicMock
    from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator
    enabled(app_settings)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logger)
    row = topic("technology")
    row.snippet = "厂商公开了测试条件，设备只能在指定场景完成任务。相关报告同时列明了测试范围，不代表可以用于所有环境。"
    row._verified_source_material = True
    generator.openai_service.create_text_completion = MagicMock(return_value="公开材料列出了条件。实际适用范围仍有限定。\n\n" + "依据原文分析条件的区别。" * 20)
    generator.generate_content(row, "story_comment")
    assert generator.openai_service.create_text_completion.call_count == 2
    draft = generator.openai_service.create_text_completion.call_args_list[0].kwargs
    assert "不套用家庭纠纷或法院判决" in draft["user_prompt"]
    assert row._reader_value_brief["profile"] == "source_practical"
    assert row._content_experiment["selection_protocol"] == SELECTION_PROTOCOL


def test_adaptive_title_gate_does_not_consume_legacy_mixed_window_calibration(app_settings, logger):
    import json
    from toutiao_content_factory.services.review_service import ReviewWorkflowService
    app_settings.output_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.output_dir / "creative_score_calibration.json").write_text(
        json.dumps({"signal_adjustments": {"number": 10, "question_gap": 10}}), encoding="utf-8")
    enabled(app_settings)
    title = "3家工厂公开设备测试条件，能用于哪些场景？"
    review = ReviewWorkflowService(app_settings, logger)
    assert review.creative_scoring_service.score_title(title)["calibration_adjustment"] == 0
    object.__setattr__(app_settings.revenue_optimization, "adaptive_topic_selection", False)
    legacy = ReviewWorkflowService(app_settings, logger)
    assert legacy.creative_scoring_service.score_title(title)["calibration_adjustment"] == 10
