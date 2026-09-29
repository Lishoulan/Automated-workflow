"""跨通道「今日是否已发布」共享状态。

问题（2026-09-16 实测）：本地计划任务与云端 GitHub Actions 的
`publish_history.json` **互相不可见**，于是同一天可能各发一篇
（9-16：本地 02:02 + 云端 17:57，共 2 篇），「每日 1 篇」形同虚设。

修法：云端把自己每天的发布结果写进仓库里的一个**共享状态文件**并推到
`publish-state` 分支；本地在「当日已发布」预检时读这个文件，从而看见云端。
不往 `main` 上提交，是为了不干扰本地的正常开发（本地读的是 `origin/publish-state`，
`git show` 不碰工作区，脏工作区也安全）。

为什么选「git 分支」而不是别的通道：
- `actions/cache` 只能在 Actions 内部读；artifact 要让本地下载整个包（含图片，很重）；
  workflow 的 ✅ 与发布摘要都已被证明会误报（见 `scheduled-publish-verification`）。
- 分支里一个纯 JSON 文件，人可读、可 diff、可回溯，且离线时能优雅降级。

写方只有云端一个（本地不写），所以不存在并发写同一分支的冲突。
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import date as date_cls
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_BRANCH = "publish-state"
STATE_PATH = "state/daily_publish_state.json"
STATE_SCHEMA = 1
GIT_TIMEOUT_SECONDS = 60

# 同一天同一平台只保留一条记录；云端重跑会覆盖自己那条。
PLATFORM_KEY_MAIN = "toutiao"
PLATFORM_KEY_WEITOUTIAO = "toutiao_weitoutiao"


def _run_git(project_root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git %s failed: %s", " ".join(args), exc)
        return None


def read_remote_state(
    project_root: Path,
    *,
    remote: str = "origin",
    branch: str = STATE_BRANCH,
    path: str = STATE_PATH,
    refresh: bool = True,
) -> dict[str, Any]:
    """从远端分支读共享状态。任何失败都返回 `{}`（**不要当成「今天没发」的肯定证据**）。"""
    if refresh:
        # ⚠️ 2026-09-21：加 `--depth=1`。
        #
        # 这里原来是无深度限制的 `git fetch origin publish-state`，实测这个仓库
        # 拉完整历史要 **12 秒以上**，而调用方是每次 `daily_run` 开头的同步守卫 ——
        # 网络稍慢就超过 `GIT_TIMEOUT_SECONDS`，于是 `read_remote_state` 返回 `{}`，
        # `_cloud_published_today()` 拿到 None，守卫**静默失效**。
        #
        # 后果实测（2026-09-20）：云端 07:00 已发一篇，本地 21:35 那次守卫没读到
        # 云端状态，又发了一篇 —— 同一天两条图文，正是这个守卫要防的事。
        # 而"守卫失败"与"云端今天没发"在日志里长得几乎一样，很难发现。
        #
        # 我们只要这一个文件的当前版本，浅取一条提交即可，耗时从 12 秒降到亚秒级。
        fetched = _run_git(
            project_root, ["fetch", "--quiet", "--depth=1", remote, branch]
        )
        if fetched is None or fetched.returncode != 0:
            logger.info(
                "Cross-channel state unavailable (fetch %s/%s failed): %s",
                remote,
                branch,
                (fetched.stderr.strip() if fetched else "git unavailable"),
            )
            return {}
    shown = _run_git(project_root, ["show", f"{remote}/{branch}:{path}"])
    if shown is None or shown.returncode != 0:
        logger.info("Cross-channel state file not present yet: %s:%s", f"{remote}/{branch}", path)
        return {}
    try:
        payload = json.loads(shown.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Cross-channel state file is not valid JSON: %s", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _entry_key(day: date_cls | str, account: str | None = None) -> str:
    """状态条目的键。

    ⚠️ 2026-09-18：这里原来**只有日期**（`2026-09-18`），没有账号维度。
    单账号时没问题，但第二个头条账号上线后，两个账号会往同一个
    `entries["2026-09-18"]["toutiao"]` 里互相覆盖，而且跨通道互斥会串号：
    story_notes 发过之后，robot_notes 的本地兜底会以为「云端今天已经发过」，
    于是整天不发——收益直接损失，且没有任何报错。

    现在带上账号：`2026-09-18::story-notes`。
    不带账号时仍然用旧键，保证既有调用方与已写入的线上状态继续可读。
    """
    key = day.isoformat() if isinstance(day, date_cls) else str(day).strip()
    account_key = str(account or "").strip()
    return f"{key}::{account_key}" if account_key else key


def published_on(
    state: dict[str, Any],
    day: date_cls | str,
    platform: str = PLATFORM_KEY_MAIN,
    *,
    account: str | None = None,
) -> dict[str, Any] | None:
    """返回指定日期/平台/账号上已记录的发布条目；没有则 None。

    `account` 为空时只读旧的无账号键（保持既有语义不变）。
    指定账号时先读账号键，读不到再回落到旧键 —— 旧键里的记录是这次改造之前
    写下的，无法事后判定属于哪个账号，宁可当作「两个账号都可能已发」，
    也不要因为读不到而重复发布。
    """
    entries = state.get("entries") if isinstance(state, dict) else None
    if not isinstance(entries, dict):
        return None
    candidates: list[str] = []
    if str(account or "").strip():
        candidates.append(_entry_key(day, account))
    candidates.append(_entry_key(day))
    for key in candidates:
        day_entry = entries.get(key)
        if not isinstance(day_entry, dict):
            continue
        record = day_entry.get(platform)
        if not isinstance(record, dict):
            continue
        # publish_uncertain 可能已到达平台，当天先占用名额以防重复提交。
        status = str(record.get("status", "")).strip().lower()
        if status not in {"submitted_for_review", "published", "success", "publish_uncertain"}:
            continue
        return record
    return None


def read_cloud_published_today(
    project_root: Path,
    *,
    platform: str = PLATFORM_KEY_MAIN,
    today: date_cls | None = None,
    refresh: bool = True,
    account: str | None = None,
) -> dict[str, Any] | None:
    """本地预检用：云端今天是否已发布指定平台内容（可按账号区分）。"""
    state = read_remote_state(project_root, refresh=refresh)
    if not state:
        return None
    return published_on(
        state, today or date_cls.today(), platform=platform, account=account
    )


def merge_entry(
    state: dict[str, Any] | None,
    *,
    day: date_cls | str,
    platform: str,
    record: dict[str, Any],
    updated_at: str,
    account: str | None = None,
) -> dict[str, Any]:
    """把一条发布记录并入状态（同日同平台同账号覆盖），返回新状态。"""
    key = _entry_key(day, account)
    payload = dict(state) if isinstance(state, dict) else {}
    payload["schema"] = STATE_SCHEMA
    payload["updated_at"] = updated_at
    entries = payload.get("entries")
    entries = dict(entries) if isinstance(entries, dict) else {}
    day_entry = entries.get(key)
    day_entry = dict(day_entry) if isinstance(day_entry, dict) else {}
    day_entry[platform] = dict(record)
    entries[key] = day_entry
    # 只保留最近 120 天，避免文件无限增长
    ordered = sorted(entries.items(), key=lambda pair: pair[0], reverse=True)[:120]
    payload["entries"] = dict(sorted(ordered, key=lambda pair: pair[0]))
    return payload


def write_local_state(project_root: Path, state: dict[str, Any], *, path: str = STATE_PATH) -> Path:
    target = project_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
