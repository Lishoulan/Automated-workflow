from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.collect_growth_snapshot import attach_content_income, attach_opening_experiments, build_snapshot, merge_recovered_checkpoints, update_checkpoints
from toutiao_content_factory.services.content_income_service import ARTICLE_INCOME_PATH


def _work(created: datetime, impressions: int, reads: int) -> dict:
    return {
        "article_attr": {
            "gid": str(int(created.timestamp())),
            "create_time": int(created.timestamp()),
            "status": 2,
            "article_type": "weitoutiao",
            "title": "老人拒交物业费，法院最后怎么判",
        },
        "article_stat": {
            "counters": [
                {"Name": "展示", "Count": impressions},
                {"Name": "阅读", "Count": reads},
            ]
        },
    }


def test_opening_experiment_requires_unique_exact_title_time_and_type_match():
    created = datetime(2026, 10, 5, 9, tzinfo=timezone(timedelta(hours=8)))
    snapshot = build_snapshot([_work(created, 100, 2)], created + timedelta(hours=25), 30)
    record = {"id": "story_opening_v1", "variable": "opening_order", "variant": "result_first",
              "title": snapshot["works"][0]["title"], "submitted_at": created.isoformat(), "status": "submitted_for_review"}
    attach_opening_experiments(snapshot, {"records": {"one": record}})
    assert snapshot["works"][0]["content_experiment"]["variant"] == "result_first"
    for change in ({"submitted_at": (created - timedelta(days=1)).isoformat()}, {"title": "another story"}, {"status": "publish_uncertain"}):
        trial = build_snapshot([_work(created, 100, 2)], created + timedelta(hours=25), 30)
        attach_opening_experiments(trial, {"records": {"one": {**record, **change}}})
        assert "content_experiment" not in trial["works"][0]
    trial = build_snapshot([_work(created, 100, 2)], created + timedelta(hours=25), 30)
    attach_opening_experiments(trial, {"records": {"one": record, "two": record}})
    assert "content_experiment" not in trial["works"][0]


def test_opening_protocol_survives_collection_and_persistence_without_mixing_legacy():
    from toutiao_content_factory.services.growth_feedback_service import opening_experiment_summary

    created = datetime(2026, 10, 15, 9, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(hours=25)
    snapshot = build_snapshot([_work(created, 100, 2)], observed, 30)
    record = {"id": "story_opening_v1", "variable": "opening_order", "variant": "result_first",
              "protocol_version": 3, "title": snapshot["works"][0]["title"],
              "submitted_at": created.isoformat(), "status": "submitted_for_review"}
    previous = {"works": {"legacy": {"type": "weitoutiao",
        "content_experiment": {"id": "story_opening_v1", "variant": "situation_first"},
        "checkpoints": {"24h": {"impressions": 1000, "reads": 200, "capture_delay_hours": 0}}}}}

    attach_opening_experiments(snapshot, {"records": {"new": record}})
    persisted, _ = update_checkpoints(snapshot, previous, observed)
    report = opening_experiment_summary(persisted)

    assert report["protocol_version"] == 3
    assert report["legacy_works_excluded"] == 1
    first, second = report["groups"][:2]
    assert (first["samples"], first["impressions"], first["reads"]) == (1, 100, 2)
    assert second["samples"] == 0
    assert report["comparisons"] == []


def test_precise_income_joins_by_id_time_title_and_type_and_keeps_zero_known():
    created = datetime(2026, 10, 2, 9, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(days=3)
    snapshot = build_snapshot([_work(created, 100, 2)], observed, 30)
    work = snapshot["works"][0]
    row = {"gid": work["gid"], "title": work["title"], "type": "weitoutiao", "created_at": work["created_at"],
           "amount": 0, "status": "available", "source": ARTICLE_INCOME_PATH,
           "scope": "cumulative_through_ready_date", "revenue_as_of_date": "2026-10-03"}
    report = {"observed_at": observed.isoformat(), "works": [row]}
    attach_content_income(snapshot, report, observed)
    assert work["platform_revenue"] == 0
    for patch in ({"title": "different"}, {"gid": "other"}, {"amount": True}, {"status": "pending_or_unknown"}, {"revenue_as_of_date": "2026-10-01"}):
        trial = build_snapshot([_work(created, 100, 2)], observed, 30)
        attach_content_income(trial, {"observed_at": observed.isoformat(), "works": [{**row, **patch}]}, observed)
        assert "platform_revenue" not in trial["works"][0]


def test_continuous_opening_joins_actual_submitted_first_paragraph_without_fuzzy_titles():
    import copy
    created = datetime(2026, 10, 16, 9, tzinfo=timezone(timedelta(hours=8)))
    lead = "法院支持陆奶奶撤销本人转出的赠房份额。其他共有人转出的份额，未在本案中处理。"
    snapshot = build_snapshot([_work(created, 198, 0)], created + timedelta(hours=25), 30)
    snapshot["works"][0]["title"] = lead.rstrip("。")
    record = {"id": "story_opening_v1", "variable": "opening_order", "variant": "result_first",
              "protocol_version": 4, "title": lead[:20], "submitted_opening": lead,
              "submitted_at": created.isoformat(), "status": "submitted_for_review"}
    attach_opening_experiments(snapshot, {"records": {"one": record}})
    assert snapshot["works"][0]["content_experiment"]["protocol_version"] == 4
    for change in ({"submitted_opening": lead + "不能扩大到全部房产。"}, {"submitted_at": (created - timedelta(hours=1)).isoformat()},
                   {"status": "publish_uncertain"}, {"protocol_version": 3}):
        trial = copy.deepcopy(snapshot)
        trial["works"][0].pop("content_experiment", None)
        attach_opening_experiments(trial, {"records": {"one": {**record, **change}}})
        assert "content_experiment" not in trial["works"][0]
    for records, works in (({"one": record, "two": record}, snapshot["works"]),
                           ({"one": record}, [snapshot["works"][0], {**snapshot["works"][0], "gid": "another"}])):
        trial = {"works": copy.deepcopy(works)}
        for work in trial["works"]:
            work.pop("content_experiment", None)
        attach_opening_experiments(trial, {"records": records})
        assert all("content_experiment" not in work for work in trial["works"])


def test_protocol4_missing_actual_submission_text_is_not_repaired_from_local_title():
    created = datetime(2026, 10, 16, 9, tzinfo=timezone(timedelta(hours=8)))
    record = {"id": "story_opening_v1", "variant": "result_first", "protocol_version": 4,
              "title": "老人拒交物业费，法院最后怎么判", "submitted_at": created.isoformat(), "status": "submitted_for_review"}
    for text in (None, "", "   "):
        trial = build_snapshot([_work(created, 100, 2)], created + timedelta(hours=25), 30)
        candidate = {**record, "submitted_opening": text}
        attach_opening_experiments(trial, {"records": {"one": candidate}})
        assert "content_experiment" not in trial["works"][0]


def test_growth_checkpoints_capture_24h_then_72h_and_compute_delta() -> None:
    created = datetime(2026, 9, 25, 8, tzinfo=timezone(timedelta(hours=8)))
    first_observed = created + timedelta(hours=25)
    first = build_snapshot([_work(created, 1000, 25)], first_observed, 14)

    ledger, first_summary = update_checkpoints(first, {}, first_observed)
    gid = first["works"][0]["gid"]
    assert first_summary["captured_now"] == {"24h": 1, "72h": 0}
    assert ledger["works"][gid]["checkpoints"]["24h"]["capture_delay_hours"] == 1.0

    second_observed = created + timedelta(hours=74)
    second = build_snapshot([_work(created, 1800, 55)], second_observed, 14)
    ledger, second_summary = update_checkpoints(second, ledger, second_observed)

    entry = ledger["works"][gid]
    assert second_summary["captured_now"] == {"24h": 0, "72h": 1}
    assert entry["checkpoints"]["24h"]["impressions"] == 1000
    assert entry["checkpoints"]["72h"]["impressions"] == 1800
    assert entry["change_24h_to_72h"] == {
        "impressions": 800,
        "reads": 30,
        "comments": 0,
    }


def test_first_late_observation_only_captures_72h() -> None:
    created = datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(hours=120)
    snapshot = build_snapshot([_work(created, 900, 20)], observed, 14)

    ledger, summary = update_checkpoints(snapshot, {}, observed)
    entry = ledger["works"][snapshot["works"][0]["gid"]]

    assert summary["captured_now"] == {"24h": 0, "72h": 1}
    assert set(entry["checkpoints"]) == {"72h"}
    assert entry["checkpoints"]["72h"]["capture_quality"] == "late"
    assert "change_24h_to_72h" not in entry


def test_schema_one_duplicate_backfill_is_repaired() -> None:
    created = datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(hours=121)
    captured_at = observed.isoformat(timespec="seconds")
    snapshot = build_snapshot([_work(created, 900, 20)], observed, 14)
    gid = snapshot["works"][0]["gid"]
    sample = {
        "captured_at": captured_at,
        "observed_age_hours": 120,
        "capture_delay_hours": 96,
        "impressions": 900,
        "reads": 20,
        "comments": 0,
    }
    previous = {
        "schema": 1,
        "works": {
            gid: {
                "gid": gid,
                "checkpoints": {"24h": dict(sample), "72h": dict(sample)},
                "change_24h_to_72h": {"impressions": 0, "reads": 0, "comments": 0},
            }
        },
    }

    ledger, _ = update_checkpoints(snapshot, previous, observed)
    entry = ledger["works"][gid]

    assert ledger["schema"] == 2
    assert set(entry["checkpoints"]) == {"72h"}
    assert entry["checkpoints"]["72h"]["capture_quality"] == "late"
    assert "change_24h_to_72h" not in entry


def test_late_24h_and_72h_observations_do_not_claim_an_exact_window_delta() -> None:
    created = datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    first_observed = created + timedelta(hours=60)
    first = build_snapshot([_work(created, 300, 5)], first_observed, 14)
    ledger, _ = update_checkpoints(first, {}, first_observed)

    second_observed = created + timedelta(hours=110)
    second = build_snapshot([_work(created, 900, 20)], second_observed, 14)
    ledger, _ = update_checkpoints(second, ledger, second_observed)
    entry = ledger["works"][second["works"][0]["gid"]]

    assert set(entry["checkpoints"]) == {"24h", "72h"}
    assert entry["checkpoints"]["24h"]["capture_quality"] == "late"
    assert entry["checkpoints"]["72h"]["capture_quality"] == "late"
    assert "change_24h_to_72h" not in entry


def test_recovery_restores_real_early_capture_and_preserves_latest_counters():
    created = datetime(2026, 10, 3, 12, tzinfo=timezone(timedelta(hours=8)))
    first_observed = created + timedelta(hours=25)
    later_observed = created + timedelta(hours=46)
    first, _ = update_checkpoints(build_snapshot([_work(created, 61, 1)], first_observed, 30), {}, first_observed)
    later, _ = update_checkpoints(build_snapshot([_work(created, 100, 2)], later_observed, 30), {}, later_observed)
    merged, count = merge_recovered_checkpoints(later, first)
    gid = next(iter(first['works']))
    assert count == 1
    assert merged['works'][gid]['checkpoints']['24h']['impressions'] == 61
    assert merged['works'][gid]['checkpoints']['24h']['capture_quality'] == 'on_schedule'
    assert merged['works'][gid]['latest'] == later['works'][gid]['latest']
    assert later['works'][gid]['checkpoints']['24h']['impressions'] == 100
    assert merge_recovered_checkpoints(merged, first)[1] == 0
    assert merge_recovered_checkpoints(first, later)[1] == 0


def test_recovery_rejects_changed_work_identity_and_invalid_captures():
    import copy
    created = datetime(2026, 10, 3, 12, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(hours=25)
    source, _ = update_checkpoints(build_snapshot([_work(created, 61, 1)], observed, 30), {}, observed)
    gid = next(iter(source['works']))
    current = copy.deepcopy(source)
    current['works'][gid]['checkpoints'] = {}
    for damage in ['identity', 'before_due', 'wrong_window', 'negative', 'invalid_container', 'unknown_work']:
        corrupted = copy.deepcopy(source)
        entry = corrupted['works'][gid]
        if damage == 'identity': entry['title'] = 'Different work'
        elif damage == 'before_due': entry['checkpoints']['24h']['captured_at'] = (created + timedelta(hours=5)).isoformat()
        elif damage == 'wrong_window': entry['checkpoints']['24h']['captured_at'] = (created + timedelta(hours=90)).isoformat()
        elif damage == 'negative': entry['checkpoints']['24h']['reads'] = -1
        elif damage == 'invalid_container': entry['checkpoints'] = []
        elif damage == 'unknown_work': corrupted['works'] = {'unknown': entry}
        merged, count = merge_recovered_checkpoints(current, corrupted)
        assert count == 0
        assert merged == current
