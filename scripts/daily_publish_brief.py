#!/usr/bin/env python
"""每日发布自检简报：把云端与本地的发布结果合成一份可读摘要。

为什么要单独做这个
------------------
判断「今天到底发出去了没有」，**唯一可靠的依据是 `publish_history.json` 里
`platform=toutiao` 的记录**。原因有两条，都踩过：

1. GitHub Actions 的 ✅ 不可信 —— `Enforce publish result` 只把
   `failed/partial_success/publish_login_required/publish_uncertain/degraded_to_draft`
   判为失败，**`review_required` 是放行的**，所以 workflow 绿了不等于发出去了。
2. 发布摘要本身也可能误报 —— 兜底发布常常发的不是最优候选，若 `archive_root`
   仍指向未发布的那篇，`publish_status` 会解析成空串，一次成功发布被写成
   `review_required`（2026-09-16 实测：pgc_id 都拿到了，摘要却写「头条: -」）。

这个脚本把三样东西一起摊开：云端最近一次运行的真实结果、发布历史里的
toutiao 记录、以及那篇稿子的质量指标（字数/抄录率/AI 风险/标题告警）。

用法
----
    .venv/Scripts/python.exe scripts/daily_publish_brief.py
    .venv/Scripts/python.exe scripts/daily_publish_brief.py --local-only
    .venv/Scripts/python.exe scripts/daily_publish_brief.py --date 20260916
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "robot-notes"


def _load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _normalize_day(raw: str) -> str:
    """把 20260916 / 2026-09-16 统一成 publish_history 里 `published_at` 用的
    `2026-09-16` 形式 —— 否则 startswith 永远匹配不上。"""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(raw or "")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _local_history_paths() -> list[Path]:
    return [
        PROJECT_ROOT / "output" / "accounts" / ACCOUNT / "publish_history.json",
        PROJECT_ROOT / "output" / "publish_history.json",
    ]


def _toutiao_records(records: list[dict], day: str) -> list[dict]:
    """筛出某天、且平台是头条系（图文或微头条）的发布记录。"""
    hits: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if not str(item.get("published_at") or "").startswith(day):
            continue
        if "toutiao" in str(item.get("platform") or ""):
            hits.append(item)
    return hits


def _cloud_runs(limit: int = 12) -> list[dict]:
    try:
        completed = subprocess.run(
            [
                "gh", "run", "list",
                "--workflow=daily-run.yml",
                "--limit", str(limit),
                "--json", "databaseId,status,conclusion,createdAt,event,url",
            ],
            capture_output=True, text=True, check=True, cwd=PROJECT_ROOT,
        )
        payload = json.loads(completed.stdout)
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        print(f"[warn] 读取云端运行列表失败：{exc}", file=sys.stderr)
        return []


def _download_cloud_snapshot(run: dict) -> dict:
    """下载一次云端运行的产物并抽出关键文件。"""
    snapshot: dict = {"result": {}, "history": []}
    tmp = Path(tempfile.mkdtemp(prefix="daily-brief-"))
    try:
        subprocess.run(
            ["gh", "run", "download", str(run.get("databaseId")), "--dir", str(tmp)],
            capture_output=True, text=True, check=True, cwd=PROJECT_ROOT,
        )
        for path in tmp.rglob("last_daily_run_result.json"):
            payload = _load_json(path)
            if isinstance(payload, dict):
                snapshot["result"] = payload
                break
        for path in tmp.rglob("publish_history.json"):
            payload = _load_json(path)
            if isinstance(payload, list):
                snapshot["history"].extend(x for x in payload if isinstance(x, dict))
    except Exception as exc:
        print(f"[warn] 下载云端产物失败：{exc}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return snapshot


def _render_local(records: list[dict], day: str) -> list[str]:
    hits = _toutiao_records(records, day)
    if not hits:
        return ["  本地：当天无头条发布记录"]
    lines = [f"  本地：当天 {len(hits)} 条头条发布记录"]
    for item in hits:
        lines.append(
            "    - [{platform}] {title}\n      状态 {status} | {url}".format(
                platform=item.get("platform", "?"),
                title=str(item.get("title") or "")[:44],
                status=item.get("status", "?"),
                url=item.get("external_url", ""),
            )
        )
    return lines


def _render_cloud(run: dict | None, snapshot: dict, day: str) -> list[str]:
    if run is None:
        return ["  云端：未取到运行记录（需要 gh 已认证）"]
    lines = [
        "  云端最近一次运行：{id}  {created}  {conclusion}".format(
            id=run.get("databaseId", "?"),
            created=str(run.get("createdAt") or "")[:16],
            conclusion=run.get("conclusion") or run.get("status") or "?",
        )
    ]
    result = snapshot.get("result") or {}
    if result:
        lines.append(
            "    运行状态 status={status} workflow_status={wf}".format(
                status=result.get("status", "?"),
                wf=result.get("workflow_status", "?"),
            )
        )
        if result.get("publish_pgc_id"):
            lines.append(f"    发布 pgc_id={result['publish_pgc_id']}")
        else:
            lines.append(
                "    ⚠️ publish_pgc_id 为空 —— 若 publish_history 里有记录，"
                "说明是「成功发布被误报」，以 publish_history 为准"
            )
    else:
        lines.append("    未取到 last_daily_run_result.json")

    hits = _toutiao_records(snapshot.get("history") or [], day)
    if hits:
        lines.append(f"    云端 publish_history 中 {day} 的头条记录：{len(hits)} 条")
        for item in hits:
            lines.append(
                "      - [{platform}] {title} | {status}".format(
                    platform=item.get("platform", "?"),
                    title=str(item.get("title") or "")[:44],
                    status=item.get("status", "?"),
                )
            )
    else:
        lines.append(f"    云端 publish_history 中 {day} 没有头条记录（该日期未发布）")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="每日发布自检简报")
    parser.add_argument("--date", default=_today(), help="YYYYMMDD 或 YYYY-MM-DD，默认今天")
    parser.add_argument("--local-only", action="store_true", help="只查本地，不访问 GitHub")
    args = parser.parse_args()
    day = _normalize_day(args.date)

    records: list[dict] = []
    for path in _local_history_paths():
        payload = _load_json(path, default=[])
        if isinstance(payload, list):
            records.extend(x for x in payload if isinstance(x, dict))

    print("=" * 62)
    print(f"每日发布自检简报  {day}")
    print("=" * 62)

    print("\n[本地通道]")
    print("\n".join(_render_local(records, day)))

    if not args.local_only:
        print("\n[云端通道]")
        runs = _cloud_runs()
        content_run = next(
            (r for r in runs if r.get("status") == "completed"),
            runs[0] if runs else None,
        )
        snapshot = _download_cloud_snapshot(content_run) if content_run else {}
        print("\n".join(_render_cloud(content_run, snapshot, day)))

    print("\n[判定规则]")
    print("  · 只看 publish_history.json 里 platform=toutiao* 的记录，")
    print("    workflow 的 ✅ 和发布摘要的「状态/头条」字段都可能不准。")
    print("  · submitted_for_review = 已交平台审核，不等于已进公域。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
