from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from toutiao_content_factory.agents.monthly_review_agent import MonthlyReviewAgent


def test_monthly_review_uses_account_growth_checkpoints(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    output_dir = tmp_path / "output" / "accounts" / "robot-notes"
    output_dir.mkdir(parents=True)
    now = datetime.now(timezone(timedelta(hours=8)))
    works = {}
    for index in range(5):
        created = now - timedelta(days=index + 1)
        gid = str(index + 1)
        works[gid] = {
            "gid": gid,
            "type": "weitoutiao",
            "title": f"社会故事 {index + 1}",
            "created_at": created.isoformat(timespec="seconds"),
            "checkpoints": {
                "24h": {
                    "impressions": 1000 + index * 100,
                    "reads": 10 + index,
                    "comments": index,
                }
            },
        }
    works["new"] = {
        "gid": "new",
        "type": "article",
        "title": "刚发布、尚未成熟的作品",
        "created_at": now.isoformat(timespec="seconds"),
        "latest": {"impressions": 0, "reads": 0, "comments": 0},
        "checkpoints": {},
    }
    (output_dir / "growth_checkpoints.json").write_text(
        json.dumps({"schema": 2, "works": works}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = MonthlyReviewAgent(account="robot_notes").run()

    assert result["status"] == "ok"
    assert result["record_count"] == 5
    assert result["ledger_path"].endswith(
        "output\\accounts\\robot-notes\\growth_checkpoints.json"
    ) or result["ledger_path"].endswith(
        "output/accounts/robot-notes/growth_checkpoints.json"
    )
    assert Path(result["report_path"]).exists()
