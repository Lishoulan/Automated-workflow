from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from toutiao_content_factory.services import notification_service as notification_module
from toutiao_content_factory.services.notification_service import (
    NotificationService,
    ServerChanNotifier,
)


def test_review_required_notification_is_deduplicated_across_service_instances(
    tmp_path: Path,
) -> None:
    first = NotificationService({}, fallback_log_dir=str(tmp_path))
    first_channel = MagicMock()
    first_channel.send.return_value = True
    first.channels = [first_channel]

    kwargs = {
        "account": "robot-notes",
        "selected_title": "需要人工审核的标题",
        "run_dir": str(tmp_path / "run-1"),
        "reason": "未达到自动发布门槛",
        "run_time": "2026-08-30 08:00",
    }
    first.notify_review_required(**kwargs)
    first_channel.send.assert_not_called()

    second = NotificationService({}, fallback_log_dir=str(tmp_path))
    second_channel = MagicMock()
    second_channel.send.return_value = True
    second.channels = [second_channel]
    second.notify_review_required(**{**kwargs, "run_dir": str(tmp_path / "run-2")})

    second_channel.send.assert_not_called()
    entries = [
        json.loads(line)
        for line in (tmp_path / "notification_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 2
    assert all(entry["pushed"] is False for entry in entries)
    assert all(entry["kind"] == "review_required" for entry in entries)


def test_daily_report_batches_review_success_and_failure_events_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 9, 1, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(notification_module, "_notification_now", lambda: fixed_now)
    service = NotificationService({}, fallback_log_dir=str(tmp_path))
    channel = MagicMock()
    channel.send.return_value = True
    service.channels = [channel]

    service.notify_review_required(
        account="robot-notes",
        selected_title="未过门槛的文章",
        run_dir="run-1",
        reason="publish_readiness=0",
        run_time="2026-09-01 18:00",
    )
    service.notify_success("Toutiao", "已发布的文章", "2026-09-01 12:00")
    service.notify_failure("Toutiao", "publish", "session expired", "2026-09-01 15:00")

    result = service.send_daily_report()

    assert result["status"] == "sent"
    assert result["event_count"] == 3
    channel.send.assert_called_once()
    title, message = channel.send.call_args.args[:2]
    assert title == "【自动化日报】9月1日 3条事件"
    assert "人工审核 1" in message
    assert "成功 1" in message
    assert "失败 1" in message
    assert "未过门槛的文章" in message

    repeated = service.send_daily_report()
    assert repeated["status"] == "already_sent"
    channel.send.assert_called_once()


def test_daily_report_and_urgent_events_share_persistent_daily_quota(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 9, 1, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(notification_module, "_notification_now", lambda: fixed_now)
    service = NotificationService({}, fallback_log_dir=str(tmp_path))
    monkeypatch.setattr(service, "DAILY_SEND_LIMIT", 2)
    channel = MagicMock()
    channel.send.return_value = True
    service.channels = [channel]

    service.notify_urgent("[URGENT] first", "first")
    service.notify_urgent("[URGENT] second", "second")
    service.notify_urgent("[URGENT] third", "third")

    result = service.send_daily_report()

    assert channel.send.call_count == 2
    assert result["status"] == "sent"
    assert result["event_count"] == 3
    assert result["quota_used"] == 2


def test_empty_daily_report_does_not_consume_quota(tmp_path: Path, monkeypatch) -> None:
    fixed_now = datetime(2026, 9, 1, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(notification_module, "_notification_now", lambda: fixed_now)
    service = NotificationService({}, fallback_log_dir=str(tmp_path))
    channel = MagicMock()
    channel.send.return_value = True
    service.channels = [channel]

    result = service.send_daily_report()

    assert result["status"] == "no_events"
    assert result["quota_used"] == 0
    channel.send.assert_not_called()


def test_daily_limit_is_resolved_from_environment_at_instance_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NOTIFY_DAILY_LIMIT", "3")

    service = NotificationService({}, fallback_log_dir=str(tmp_path))

    assert service.DAILY_SEND_LIMIT == 3


def test_serverchan_business_quota_error_is_not_treated_as_success(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"code": 40001, "message": "超过当天的发送次数限制[5]"}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def post(*_args, **_kwargs):
            return FakeResponse()

    class FakeHttpx:
        Client = FakeClient

    monkeypatch.setattr(notification_module, "httpx", FakeHttpx)

    assert ServerChanNotifier("test-key").send("title", "message") is False


def test_account_ledgers_are_mirrored_to_shared_daily_report_ledger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 9, 1, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(notification_module, "_notification_now", lambda: fixed_now)
    account_log_dir = tmp_path / "output" / "accounts" / "robot-notes" / "automation"
    service = NotificationService({}, fallback_log_dir=str(account_log_dir))

    service.notify_review_required(
        account="robot-notes",
        selected_title="机器人收入分析",
        run_dir="run-1",
        reason="publish_readiness=0",
        run_time="2026-09-01 18:00",
    )

    shared_log = tmp_path / "output" / "automation" / "notification_log.jsonl"
    assert shared_log.exists()
    reporter = NotificationService({}, fallback_log_dir=str(tmp_path / "output" / "automation"))
    assert len(reporter._daily_report_entries(fixed_now.date())) == 1


def test_multiple_account_ledgers_share_one_daily_report_and_quota(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 9, 1, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(notification_module, "_notification_now", lambda: fixed_now)
    account_root = tmp_path / "output" / "accounts"
    robot = NotificationService(
        {"account_key": "robot-notes"},
        fallback_log_dir=str(account_root / "robot-notes" / "automation"),
    )
    politics = NotificationService(
        {"account_key": "politics-notes"},
        fallback_log_dir=str(account_root / "politics-notes" / "automation"),
    )
    robot_channel = MagicMock()
    robot_channel.send.return_value = True
    politics_channel = MagicMock()
    politics_channel.send.return_value = True
    robot.channels = [robot_channel]
    politics.channels = [politics_channel]

    robot.notify_review_required(
        account="robot-notes",
        selected_title="机器人收入分析",
        run_dir="robot-run",
        reason="publish_readiness=0",
        run_time="2026-09-01 18:00",
    )
    politics.notify_review_required(
        account="politics-notes",
        selected_title="地缘风险分析",
        run_dir="politics-run",
        reason="publish_readiness=0",
        run_time="2026-09-01 18:01",
    )

    reporter = NotificationService({}, fallback_log_dir=str(tmp_path / "output" / "automation"))
    report_channel = MagicMock()
    report_channel.send.return_value = True
    reporter.channels = [report_channel]
    result = reporter.send_daily_report()

    assert result["status"] == "sent"
    assert result["event_count"] == 2
    report_channel.send.assert_called_once()
    _, message = report_channel.send.call_args.args[:2]
    assert "机器人收入分析" in message
    assert "地缘风险分析" in message


def test_deduplication_converts_utc_log_timestamp_to_shanghai_date(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 9, 1, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(notification_module, "_notification_now", lambda: fixed_now)
    title = "【robot-notes】定时任务需要人工审核"
    (tmp_path / "notification_log.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-08-31T17:29:00+00:00",
                "title": title,
                "message": "already sent",
                "pushed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    service = NotificationService({}, fallback_log_dir=str(tmp_path))

    assert service._has_pushed_title_today(title) is True
