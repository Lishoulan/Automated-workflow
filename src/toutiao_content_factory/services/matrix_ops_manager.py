"""矩阵运营管理器 - 多账号管理与批量发布"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from toutiao_content_factory.config import AppSettings


@dataclass
class MatrixAccount:
    id: str
    platform: str
    account_name: str
    group_id: str = "default"
    login_status: str = "never"  # logged_in, expired, never
    profile_dir: str = ""
    fans_count: int = 0
    read_count: int = 0
    income: float = 0.0


@dataclass
class AccountGroup:
    id: str
    name: str
    accounts: list[str] = field(default_factory=list)
    publish_strategy: str = "sync"  # sync, staggered, differentiated


@dataclass
class BatchPublishResult:
    task_id: str
    total: int
    succeeded: int
    failed: int
    results: dict[str, dict]  # {account_id: {success, error}}


class MatrixOpsManager:
    """矩阵运营管理器，支持多账号管理与批量发布"""

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self._settings = settings
        self._logger = logger
        self._db_path = Path(settings.project_root) / "matrix_accounts.db"
        self._init_db()

    # ── 数据库初始化 ──────────────────────────────────────────

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    group_id TEXT NOT NULL DEFAULT 'default',
                    login_status TEXT NOT NULL DEFAULT 'never',
                    profile_dir TEXT NOT NULL DEFAULT '',
                    fans_count INTEGER NOT NULL DEFAULT 0,
                    read_count INTEGER NOT NULL DEFAULT 0,
                    income REAL NOT NULL DEFAULT 0.0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    accounts TEXT NOT NULL DEFAULT '[]',
                    publish_strategy TEXT NOT NULL DEFAULT 'sync'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publish_logs (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    images TEXT NOT NULL DEFAULT '[]',
                    target_accounts TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    total INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    results TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── 账号管理 ──────────────────────────────────────────────

    def add_account(self, account: MatrixAccount) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO accounts
                   (id, platform, account_name, group_id, login_status,
                    profile_dir, fans_count, read_count, income)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account.id,
                    account.platform,
                    account.account_name,
                    account.group_id,
                    account.login_status,
                    account.profile_dir,
                    account.fans_count,
                    account.read_count,
                    account.income,
                ),
            )
            conn.commit()
        self._logger.info("添加矩阵账号: %s (%s)", account.account_name, account.id)
        return account.id

    def remove_account(self, account_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.commit()
            removed = cursor.rowcount > 0
        if removed:
            self._logger.info("移除矩阵账号: %s", account_id)
        else:
            self._logger.warning("未找到账号: %s", account_id)
        return removed

    def list_accounts(
        self, platform: str | None = None, group_id: str | None = None
    ) -> list[MatrixAccount]:
        with self._get_conn() as conn:
            query = "SELECT * FROM accounts WHERE 1=1"
            params: list[str] = []
            if platform:
                query += " AND platform = ?"
                params.append(platform)
            if group_id:
                query += " AND group_id = ?"
                params.append(group_id)
            rows = conn.execute(query, params).fetchall()
        return [
            MatrixAccount(
                id=r["id"],
                platform=r["platform"],
                account_name=r["account_name"],
                group_id=r["group_id"],
                login_status=r["login_status"],
                profile_dir=r["profile_dir"],
                fans_count=r["fans_count"],
                read_count=r["read_count"],
                income=r["income"],
            )
            for r in rows
        ]

    # ── 分组管理 ──────────────────────────────────────────────

    def create_group(self, name: str, account_ids: list[str]) -> str:
        group_id = uuid.uuid4().hex[:12]
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO groups (id, name, accounts, publish_strategy) VALUES (?, ?, ?, ?)",
                (group_id, name, json.dumps(account_ids), "sync"),
            )
            # 将账号归入该分组
            for aid in account_ids:
                conn.execute("UPDATE accounts SET group_id = ? WHERE id = ?", (group_id, aid))
            conn.commit()
        self._logger.info("创建账号分组: %s (%s), 包含 %d 个账号", name, group_id, len(account_ids))
        return group_id

    # ── 批量发布 ──────────────────────────────────────────────

    def batch_publish(
        self,
        title: str,
        content: str,
        images: list[str],
        target_accounts: list[str],
        *,
        tags: list[str] | None = None,
    ) -> BatchPublishResult:
        task_id = uuid.uuid4().hex[:16]
        tags = tags or []
        results: dict[str, dict] = {}
        succeeded = 0
        failed = 0

        accounts = self.list_accounts()
        account_map = {a.id: a for a in accounts}

        for aid in target_accounts:
            account = account_map.get(aid)
            if account is None:
                results[aid] = {"success": False, "error": "账号不存在"}
                failed += 1
                continue
            if account.login_status != "logged_in":
                results[aid] = {"success": False, "error": f"账号未登录，状态: {account.login_status}"}
                failed += 1
                continue
            # 模拟发布（实际发布由各平台 publisher 执行）
            try:
                results[aid] = {"success": True, "error": ""}
                succeeded += 1
            except Exception as exc:
                results[aid] = {"success": False, "error": str(exc)}
                failed += 1

        # 记录发布日志
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO publish_logs
                   (task_id, title, content, images, target_accounts, tags,
                    total, succeeded, failed, results, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    title,
                    content,
                    json.dumps(images),
                    json.dumps(target_accounts),
                    json.dumps(tags),
                    len(target_accounts),
                    succeeded,
                    failed,
                    json.dumps(results),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()

        self._logger.info(
            "批量发布完成: task=%s, 成功=%d, 失败=%d", task_id, succeeded, failed
        )
        return BatchPublishResult(
            task_id=task_id,
            total=len(target_accounts),
            succeeded=succeeded,
            failed=failed,
            results=results,
        )

    # ── 统计与仪表盘 ──────────────────────────────────────────

    def get_account_stats(self, account_id: str) -> dict:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return {}
        return {
            "id": row["id"],
            "platform": row["platform"],
            "account_name": row["account_name"],
            "group_id": row["group_id"],
            "login_status": row["login_status"],
            "fans_count": row["fans_count"],
            "read_count": row["read_count"],
            "income": row["income"],
        }

    def get_dashboard_data(self) -> dict:
        with self._get_conn() as conn:
            accounts = conn.execute("SELECT * FROM accounts").fetchall()
            groups = conn.execute("SELECT * FROM groups").fetchall()
            recent_tasks = conn.execute(
                "SELECT * FROM publish_logs ORDER BY created_at DESC LIMIT 20"
            ).fetchall()

        total_accounts = len(accounts)
        logged_in = sum(1 for a in accounts if a["login_status"] == "logged_in")
        total_fans = sum(a["fans_count"] for a in accounts)
        total_read = sum(a["read_count"] for a in accounts)
        total_income = sum(a["income"] for a in accounts)

        platform_counts: dict[str, int] = {}
        for a in accounts:
            platform_counts[a["platform"]] = platform_counts.get(a["platform"], 0) + 1

        return {
            "total_accounts": total_accounts,
            "logged_in_accounts": logged_in,
            "total_fans": total_fans,
            "total_read": total_read,
            "total_income": total_income,
            "platform_distribution": platform_counts,
            "total_groups": len(groups),
            "recent_publish_tasks": [
                {
                    "task_id": t["task_id"],
                    "title": t["title"],
                    "total": t["total"],
                    "succeeded": t["succeeded"],
                    "failed": t["failed"],
                    "created_at": t["created_at"],
                }
                for t in recent_tasks
            ],
        }
