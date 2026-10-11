from __future__ import annotations

import base64
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import publication_watchdog as watchdog
from toutiao_content_factory.services import notification_service as notifications

NOW = datetime(2026, 10, 11, 10, tzinfo=watchdog.NOTIFICATION_TIMEZONE)


@pytest.mark.parametrize("result,title", [
    ({"status": "publish_login_required"}, "登录失效"),
    ({"status": "no_topics"}, "未完成交付"),
    ({"status": "review_required"}, "未完成交付"),
    ({"status": "success", "draft_delivery": {"status": "uncertain"}}, "提交结果待核"),
    ({"status": "daily_retry_stopped", "retry_status": "daily_budget_exhausted"}, "额度已用完"),
    ({"status": "success", "metrics_error": "RuntimeError"}, "任务异常"),
    ({"status": "failed"}, "任务异常"),
])
def test_actual_outcomes_trigger_actionable_alerts(result, title):
    assert title in watchdog.outcome_alert("公众号", result)[0]


@pytest.mark.parametrize("result", [
    {"status": "ready"},
    {"status": "already_drafted", "draft_delivery": {"status": "saved"}},
    {"status": "success", "wechat_status": "draft_saved", "draft_delivery": {"status": "saved"}},
    {"status": "success", "publish_status": "submitted_for_review"},
])
def test_valid_checks_drafts_and_pending_review_are_not_failures(result):
    assert watchdog.outcome_alert("公众号", result) is None


def test_daily_missing_outputs_detected_even_without_any_workflow_run():
    alerts = watchdog.daily_alerts(NOW, {}, {"runs": []}, {})
    assert len(alerts) == 2
    assert "今日尚无提交记录" in alerts[0][0]
    assert "今日尚无新草稿" in alerts[1][0]


def test_previous_day_draft_and_other_account_publish_do_not_mask_today():
    state = {"entries": {"2026-10-11::other-account": {"toutiao": {"status": "published"}}}}
    metrics = {"runs": [{"mode": "draft", "started_at": "2026-10-10T10:00:00+08:00", "draft_delivery": {"status": "saved"}}]}
    assert len(watchdog.daily_alerts(NOW, state, metrics, {})) == 2


def test_current_saved_draft_and_valid_toutiao_submission_suppress_missing_alerts():
    state = {"entries": {"2026-10-11::robot-notes": {"toutiao_weitoutiao": {"status": "submitted_for_review"}}}}
    retry = {"days": {"2026-10-11": {"runs": {"one": {"draft_delivery": {"status": "saved"}}}}}}
    assert watchdog.daily_alerts(NOW, state, {}, retry) == []


def test_uncertain_submission_never_becomes_missing_output_or_republish():
    state = {"entries": {"2026-10-11::robot-notes": {"toutiao": {"status": "publish_uncertain"}}}}
    retry = {"days": {"2026-10-11": {"runs": {"one": {"draft_delivery": {"status": "uncertain"}}}}}}
    alerts = watchdog.daily_alerts(NOW, state, {}, retry)
    assert len(alerts) == 2
    assert all("待核" in title for title, _ in alerts)


def test_grace_period_does_not_alert_before_morning_window():
    assert watchdog.daily_alerts(NOW.replace(hour=8, minute=0), {}, {}, {}) == []


def run(**updates):
    return {"id": 1, "name": "WeChat Daily Run", "head_branch": "main", "created_at": "2026-10-11T01:00:00Z",
            "run_started_at": "2026-10-11T01:01:00Z", "updated_at": "2026-10-11T01:30:00Z",
            "status": "completed", "conclusion": "success", "html_url": "https://github.com/example/actions/runs/1", **updates}


def test_green_workflow_with_no_topics_is_alerted():
    api = MagicMock()
    api.results.return_value = [{"status": "no_topics"}]
    assert "未完成交付" in watchdog.inspect_run(api, run(), NOW)[0][0]


def test_failure_before_any_result_or_during_readonly_collection_is_alerted():
    api = MagicMock()
    api.results.return_value = []
    assert "任务异常" in watchdog.inspect_run(api, run(conclusion="failure"), NOW)[0][0]
    assert watchdog.inspect_run(api, run(name="WeChat Content Metrics", conclusion="timed_out"), NOW)


def test_old_run_and_non_main_completion_are_ignored():
    api = MagicMock()
    assert watchdog.inspect_run(api, run(created_at="2026-10-09T01:00:00Z"), NOW) == []
    assert watchdog.inspect_run(api, run(head_branch="development"), NOW) == []
    api.results.assert_not_called()


def test_long_queued_job_is_alerted_without_reading_results():
    api = MagicMock()
    assert "积压" in watchdog.inspect_run(api, run(status="queued", created_at="2026-10-11T00:00:00Z"), NOW)[0][0]
    api.results.assert_not_called()


def test_ledger_merge_preserves_old_quota_and_deduplicates_same_record(tmp_path, monkeypatch):
    entry = {"timestamp": NOW.isoformat(), "title": "old", "pushed": True}
    destination = tmp_path / "notification_log.jsonl"
    destination.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    api = MagicMock()
    api.file.return_value = (json.dumps(entry) + "\n").encode()
    monkeypatch.setattr(watchdog, "ROOT", tmp_path)
    watchdog.restore_ledger(api, destination)
    assert len(destination.read_text().splitlines()) == 1
    monkeypatch.setattr(notifications, "_notification_now", lambda: NOW)
    service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path))
    assert service._count_pushed_today(NOW.date()) == 1


def test_missing_established_ledger_fails_without_resetting_quota(tmp_path):
    api = MagicMock()
    api.file.return_value = None
    api.request.return_value = {"object": {"sha": "old"}}
    with pytest.raises(ValueError, match="ledger is missing"):
        watchdog.restore_ledger(api, tmp_path / "notification_log.jsonl")


def test_two_cloud_instances_do_not_send_duplicate_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "_notification_now", lambda: NOW)
    channel = MagicMock()
    channel.send.return_value = True
    for _ in range(2):
        service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path))
        service.channels = [channel]
        service.notify_urgent("【公众号】登录失效", "renew")
    assert channel.send.call_count == 1


def test_content_job_queues_urgent_events_without_spending_shared_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_URGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(notifications, "_notification_now", lambda: NOW)
    service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path))
    channel = MagicMock()
    service.channels = [channel]
    service.notify_urgent("[URGENT] toutiao session expired", "login needed")
    channel.send.assert_not_called()
    entry = service._read_log_entries()[0]
    assert entry["metadata"]["queued_for_cloud_monitor"] is True
    assert entry["pushed"] is False
    assert service._count_pushed_today(NOW.date()) == 0


def test_login_page_capture_uses_the_existing_urgent_notification_api(tmp_path, monkeypatch):
    from toutiao_content_factory.services.platform_health_service import PlatformHealthService
    monkeypatch.setenv("NOTIFY_URGENT_QUEUE_ONLY", "1")
    service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path / "logs"))
    health = PlatformHealthService.__new__(PlatformHealthService)
    health.settings = SimpleNamespace(output_dir=tmp_path, publishing=SimpleNamespace(base_url="https://mp.weixin.qq.com"))
    health.logger = MagicMock()
    health.notification_service = service
    health._capture_and_notify_qrcode(MagicMock(), "toutiao")
    assert service._read_log_entries()[0]["title"] == "[URGENT] toutiao session expired"


def test_artifact_reader_ignores_cached_results_and_does_not_forward_token(monkeypatch):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("output/old/last_daily_run_result.json", json.dumps({"updated_at": "2026-10-10T09:20:00", "status": "success"}))
        archive.writestr("output/new/last_daily_run_result.json", json.dumps({"updated_at": "2026-10-11T09:20:00", "status": "no_topics"}))
        archive.writestr("output/automation/notification_log.jsonl", json.dumps({"timestamp": "2026-10-11T09:20:00+08:00",
            "title": "[URGENT] toutiao session expired", "metadata": {"queued_for_cloud_monitor": True}}) + "\n")
    api = watchdog.GitHub.__new__(watchdog.GitHub)
    api.repo = "example/repo"
    api.client = MagicMock()
    api.client.get.return_value = httpx.Response(302, headers={"location": "https://signed.example/archive"})
    api.request = MagicMock(return_value={"artifacts": [{"id": 1, "name": "daily-output-1", "size_in_bytes": 500}]})
    stream = MagicMock()
    stream.__enter__.return_value = httpx.Response(200, content=archive_bytes.getvalue())
    get = MagicMock(return_value=stream)
    monkeypatch.setattr(watchdog.httpx, "stream", get)
    assert api.results(run()) == [{"updated_at": "2026-10-11T09:20:00", "status": "no_topics"}, {"status": "publish_login_required"}]
    assert "headers" not in get.call_args.kwargs


@pytest.mark.parametrize("payload", [None, {}, {"code": 40001}])
def test_serverchan_requires_explicit_provider_success(payload, monkeypatch):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    client = MagicMock()
    client.__enter__.return_value.post.return_value = response
    monkeypatch.setattr(notifications.httpx, "Client", lambda: client)
    assert notifications.ServerChanNotifier("test-key").send("title", "message") is False


def test_watchdog_and_digest_share_serialized_durable_ledger():
    load = lambda name: yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    monitor = load("publication-watchdog.yml")
    digest = load("daily-run.yml")["jobs"]["daily-report"]
    assert monitor["jobs"]["monitor"]["runs-on"] == "ubuntu-latest"
    assert monitor["jobs"]["monitor"]["concurrency"] == digest["concurrency"]
    assert monitor["on"]["workflow_run"]["types"] == ["completed"]
    assert monitor["on"]["schedule"]
    steps = digest["steps"]
    restore = next(s for s in steps if s.get("id") == "notification-ledger")
    save = next(s for s in steps if s.get("name") == "Persist shared notification quota and report receipt")
    assert "--ledger restore" in restore["run"]
    assert "notification-ledger.outcome == 'success'" in save["if"]
    assert "--ledger save" in save["run"]
    for name in ("daily-run", "daily-run-fallback"):
        if name in load("daily-run.yml")["jobs"]:
            assert load("daily-run.yml")["jobs"][name]["env"]["NOTIFY_URGENT_QUEUE_ONLY"] == "1"
