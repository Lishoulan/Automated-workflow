from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from toutiao_content_factory.services.notification_service import NotificationService


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
    first_channel.send.assert_called_once()

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
    assert entries[0]["pushed"] is True
    assert entries[1]["pushed"] is False
