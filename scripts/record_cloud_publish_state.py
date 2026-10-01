"""云端专用：把「今天发了什么」写进跨通道共享状态文件。

背景见 `toutiao_content_factory/services/cross_channel_state.py`：本地通道与云端
通道的 `publish_history.json` 互相不可见，导致同一天可能各发一篇。云端把结果
写到仓库的 `publish-state` 分支后，本地的「当日已发布」预检就能看见云端。

数据源只用**权威的 publish_history.json**（`platform=toutiao` 记录），
不读 `last_daily_run_result.json` 的 `状态/头条` —— 那两个字段已被证实会误报
（兜底发布时 archive_root 指错，把成功写成 review_required）。

本脚本在 CI 里跑，本身不发布任何内容。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services import cross_channel_state as xstate  # noqa: E402

# An uncertain submission may already be public. Share it as a same-day hold
# so another runner cannot publish a duplicate while evidence is incomplete.
SUCCESS_STATUSES = {"submitted_for_review", "published", "success", "publish_uncertain"}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("records", "items", "history"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def resolve_account_slug(account_key: str, cash: Path) -> str:
    for candidate in (cash / "automation" / "last_daily_run_result.json",):
        payload = _read_json(candidate)
        if isinstance(payload, dict) and payload.get("account_slug"):
            return str(payload["account_slug"]).strip()
    return account_key.replace("_", "-")


def find_history_file(account_key: str, account_output_dir: Path | None) -> Path | None:
    candidates: list[Path] = []
    if account_output_dir:
        candidates.append(account_output_dir / "publish_history.json")
    slug = resolve_account_slug(account_key, ROOT / "output")
    candidates.append(ROOT / "output" / "accounts" / slug / "publish_history.json")
    candidates.append(ROOT / "output" / "publish_history.json")
    for path in candidates:
        if path.exists():
            return path
    return None


def today_records(history_path: Path, platform: str, today: str) -> list[dict[str, Any]]:
    payload = _read_json(history_path)
    matches: list[dict[str, Any]] = []
    for record in _records(payload):
        if str(record.get("platform", "")).strip() != platform:
            continue
        published_at = str(record.get("published_at", "")).strip()
        if not published_at.startswith(today):
            continue
        if str(record.get("status", "")).strip().lower() not in SUCCESS_STATUSES:
            continue
        matches.append(record)
    return matches


def latest_today_record(history_path: Path, platform: str, today: str) -> dict[str, Any] | None:
    records = today_records(history_path, platform, today)
    return records[-1] if records else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="robot_notes")
    parser.add_argument("--account-output-dir", default="")
    parser.add_argument("--show", action="store_true", help="打印当前远端共享状态后退出")
    parser.add_argument("--today", default="", help="覆盖日期 YYYY-MM-DD（默认本地日期）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = logging.getLogger("cross-channel")

    if args.show:
        state = xstate.read_remote_state(ROOT)
        print(json.dumps(state or {"entries": {}}, ensure_ascii=False, indent=2))
        return 0

    today = args.today.strip() or datetime.now().strftime("%Y-%m-%d")
    account_output_dir = Path(args.account_output_dir) if args.account_output_dir.strip() else None
    history_path = find_history_file(args.account, account_output_dir)
    if history_path is None:
        logger.warning("No publish_history.json found; nothing to record.")
        return 0
    logger.info("Using publish history: %s", history_path)

    remote_state = xstate.read_remote_state(ROOT)
    state = remote_state if isinstance(remote_state, dict) else {}
    changed = False

    account_slug = resolve_account_slug(args.account, ROOT / "output")
    for platform in (xstate.PLATFORM_KEY_MAIN, xstate.PLATFORM_KEY_WEITOUTIAO):
        records = today_records(history_path, platform, today)
        if not records:
            logger.info("No %s publish recorded today (%s).", platform, today)
            continue
        record = records[-1]
        previous = xstate.published_on(state, today, platform=platform, account=account_slug)
        previous_count = 0
        if previous is not None:
            try:
                previous_count = max(1, int(previous.get("daily_count", 1) or 1))
            except (TypeError, ValueError):
                previous_count = 1
        state = xstate.merge_entry(
            state,
            day=today,
            platform=platform,
            record={
                "channel": "cloud",
                "status": record.get("status"),
                "pgc_id": record.get("pgc_id", ""),
                "title": record.get("title", ""),
                "publish_url": record.get("publish_url", ""),
                "daily_count": max(len(records), previous_count),
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            },
            updated_at=datetime.now().isoformat(timespec="seconds"),
            # 带上账号，否则两个账号会覆盖同一条 entries[日期][平台]，
            # 跨通道互斥会串号（A 发过导致 B 整天不发）。
            account=account_slug,
        )
        changed = True
        logger.info("Recorded %s publish: %s", platform, record.get("title", ""))

    if not changed:
        logger.info("Cross-channel state unchanged; skipping write.")
        return 0

    target = xstate.write_local_state(ROOT, state)
    logger.info("Cross-channel state written: %s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
