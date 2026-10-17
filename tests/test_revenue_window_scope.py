import logging
from datetime import datetime, timedelta

import pytest

from toutiao_content_factory.services.revenue_experiment_service import RevenueExperimentService
from toutiao_content_factory.services.creator_account_service import save_account_snapshot, load_account_snapshot


def daily(days=30):
    end = datetime(2026, 10, 15)
    return [{"day": (end - timedelta(days=i)).date().isoformat(), "pending": False,
             "total": .01, "article": 0, "weitoutiao": .01, "video": 0} for i in range(days)]


def test_partial_income_is_not_full_window_or_zero_last_seven(tmp_path):
    service = RevenueExperimentService(tmp_path, logging.getLogger("scope"))
    report = service._income_summary(daily(), datetime(2026, 10, 17))
    assert report["settled_total"] == .29  # natural window Sep17..Oct16
    assert report["complete_total"] is None
    assert report["latest_7d"] is None
    assert report["missing_days"] == ["2026-10-16"]
    assert report["observed_latest_7d"] == .06


def test_dashboard_reports_7_30_90_coverage_and_never_divides_calendar_income_by_stock(tmp_path):
    service = RevenueExperimentService(tmp_path, logging.getLogger("scope"))
    report = service.build_dashboard(
        account_snapshot={"revenue": {"income": {"daily": daily(37)}}},
        growth_snapshot={"observed_at": "2026-10-17T05:00:00+08:00", "window_days": 30,
                         "summary": {"weitoutiao": {"works": 1, "reads": 20, "impressions": 1000}},
                         "works": []},
        growth_feedback={}, performance={}, readiness={})
    assert report["income_windows"]["7"]["complete_total"] is None
    assert report["latest_settled_windows"]["7"]["complete_total"] == .07
    assert report["latest_settled_windows"]["30"]["complete_total"] == .30
    assert report["latest_settled_windows"]["90"]["settled_days"] == 37
    assert report["latest_settled_windows"]["90"]["complete_total"] is None
    metrics = report["formats"]["weitoutiao"]
    assert metrics["revenue_per_1000_reads"] is None
    assert metrics["revenue_per_1000_impressions"] is None
    assert metrics["window_aligned"] is False
    assert report["formats"]["article"]["reads"] is None
    assert report["revenue_reconciliation"]["settled_income"] is None


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1, True])
def test_invalid_exact_money_is_unknown(tmp_path, amount):
    service = RevenueExperimentService(tmp_path, logging.getLogger("scope"))
    result = service._per_content_attribution({"records": [{"content_id": "x", "platform_revenue": amount,
        "revenue_amount_known": True, "read_count": 20, "impression_count": 100}]})
    assert result["exact_count"] == 0
    assert result["rows"][0]["revenue"] is None


def test_exact_amount_without_counter_cutoff_cannot_be_rpm(tmp_path):
    result = RevenueExperimentService(tmp_path, logging.getLogger("scope"))._per_content_attribution({"records": [
        {"content_id": "x", "platform_revenue": .01, "revenue_amount_known": True,
         "read_count": 2, "impression_count": 100}]})
    assert result["rows"][0]["revenue"] == .01
    assert result["rows"][0]["revenue_per_1000_impressions"] is None


def test_same_date_does_not_align_full_day_income_and_intraday_counters(tmp_path):
    row = {"content_id": "x", "platform_revenue": .1, "revenue_amount_known": True,
           "read_count": 20, "impression_count": 1000,
           "revenue_as_of_date": "2026-10-15", "counters_as_of_date": "2026-10-15"}
    report = RevenueExperimentService(tmp_path, logging.getLogger("scope"))._per_content_attribution({"records": [row]})
    assert report["rows"][0]["revenue_per_1000_impressions"] is None


def test_daily_history_survives_rolling_feed_and_quarantines_conflict(app_settings, logger):
    account = {"account_key": "robot_notes", "observed_date": "2026-10-15", "revenue": {"income": {"daily": daily(37)}}}
    save_account_snapshot(app_settings, logger, account)
    newer = {**account, "observed_date": "2026-10-17", "revenue": {"income": {"daily": daily(30)}}}
    newer["revenue"]["income"]["daily"][0] = {**daily()[0], "total": .02}
    save_account_snapshot(app_settings, logger, newer)
    payload = load_account_snapshot(app_settings)
    assert len(payload["settled_daily_history"]) == 38
    result = RevenueExperimentService(app_settings.output_dir, logger)._income_summary(
        payload["settled_daily_history"], datetime(2026, 10, 15), days=90)
    assert result["settled_days"] == 36
    assert result["conflicting_days"] == ["2026-10-15"]


def test_ninety_settled_days_survive_two_day_lag_but_future_money_is_excluded(app_settings, logger):
    rows = daily(90) + [{**daily()[0], "day": "2026-10-18", "total": 999}]
    account = {"account_key": "robot_notes", "observed_date": "2026-10-17", "revenue": {"income": {"daily": rows}}}
    save_account_snapshot(app_settings, logger, account)
    history = load_account_snapshot(app_settings)["settled_daily_history"]
    assert len(history) == 90
    assert history[0]["day"] == "2026-07-18" and history[-1]["day"] == "2026-10-15"
    summary = RevenueExperimentService(app_settings.output_dir, logger)._income_summary(history, datetime(2026, 10, 15), days=90)
    assert summary["coverage_complete"] is True and summary["complete_total"] == .9


def test_partial_format_money_is_explicit_subtotal_not_complete_revenue(tmp_path):
    service = RevenueExperimentService(tmp_path, logging.getLogger("scope"))
    income = service._income_summary(daily(), datetime(2026, 10, 17))
    metrics = service._format_metrics({"window_days": 30, "summary": {}}, income)
    assert metrics["weitoutiao"]["observed_revenue"] == .29
    assert metrics["weitoutiao"]["revenue"] is None
