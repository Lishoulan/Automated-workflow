"""变现引擎 - CPS、CPE 与积分体系"""

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
class CreditsAccount:
    user_id: str
    balance: int = 0
    total_earned: int = 0
    total_spent: int = 0
    pending: int = 0


@dataclass
class CreditsTransaction:
    id: str
    user_id: str
    type: str  # earn, spend, refund, settlement
    amount: int
    source: str  # cps, cpe, cpm, ai_service, refund
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    settlement_status: str = "pending"  # pending, settled, failed, refunded


@dataclass
class CPSOffer:
    id: str
    title: str
    product_url: str
    commission_rate: float
    category: str = ""
    platform: str = ""  # taobao, jd, pdd


@dataclass
class CPETask:
    id: str
    platform: str
    task_type: str  # like, comment, follow, share
    target_url: str
    reward: int
    status: str = "pending"


class MonetizationEngine:
    """变现引擎，支持 CPS 分销、CPE 互动任务与积分体系"""

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self._settings = settings
        self._logger = logger
        self._db_path = Path(settings.project_root) / "credits.db"
        self._init_db()

    # ── 数据库初始化 ──────────────────────────────────────────

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS credits_accounts (
                    user_id TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0,
                    total_earned INTEGER NOT NULL DEFAULT 0,
                    total_spent INTEGER NOT NULL DEFAULT 0,
                    pending INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS credits_transactions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    settlement_status TEXT NOT NULL DEFAULT 'pending'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cps_offers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    product_url TEXT NOT NULL,
                    commission_rate REAL NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cps_clicks (
                    id TEXT PRIMARY KEY,
                    offer_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    clicked_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cpe_tasks (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    reward INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                )
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── 积分体系 ──────────────────────────────────────────────

    def get_credits_balance(self, user_id: str) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT balance FROM credits_accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["balance"] if row else 0

    def _ensure_account(self, conn: sqlite3.Connection, user_id: str) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO credits_accounts
               (user_id, balance, total_earned, total_spent, pending)
               VALUES (?, 0, 0, 0, 0)""",
            (user_id,),
        )

    def add_credits(self, user_id: str, amount: int, source: str, description: str = "") -> str:
        txn_id = uuid.uuid4().hex[:16]
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            self._ensure_account(conn, user_id)
            conn.execute(
                """INSERT INTO credits_transactions
                   (id, user_id, type, amount, source, description, created_at, settlement_status)
                   VALUES (?, ?, 'earn', ?, ?, ?, ?, 'pending')""",
                (txn_id, user_id, amount, source, description, now),
            )
            conn.execute(
                """UPDATE credits_accounts
                   SET balance = balance + ?,
                       total_earned = total_earned + ?,
                       pending = pending + ?
                   WHERE user_id = ?""",
                (amount, amount, amount, user_id),
            )
            conn.commit()
        self._logger.info("积分入账: user=%s, amount=%d, source=%s, txn=%s", user_id, amount, source, txn_id)
        return txn_id

    def deduct_credits(self, user_id: str, amount: int, source: str, description: str = "") -> str:
        txn_id = uuid.uuid4().hex[:16]
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            self._ensure_account(conn, user_id)
            row = conn.execute(
                "SELECT balance FROM credits_accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None or row["balance"] < amount:
                raise ValueError(f"余额不足: 当前 {row['balance'] if row else 0}, 需要 {amount}")
            conn.execute(
                """INSERT INTO credits_transactions
                   (id, user_id, type, amount, source, description, created_at, settlement_status)
                   VALUES (?, ?, 'spend', ?, ?, ?, ?, 'settled')""",
                (txn_id, user_id, amount, source, description, now),
            )
            conn.execute(
                """UPDATE credits_accounts
                   SET balance = balance - ?,
                       total_spent = total_spent + ?
                   WHERE user_id = ?""",
                (amount, amount, user_id),
            )
            conn.commit()
        self._logger.info("积分扣除: user=%s, amount=%d, source=%s, txn=%s", user_id, amount, source, txn_id)
        return txn_id

    # ── 结算 ──────────────────────────────────────────────────

    def create_settlement(self, user_id: str, prepaid: int, source: str) -> str:
        txn_id = uuid.uuid4().hex[:16]
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            self._ensure_account(conn, user_id)
            conn.execute(
                """INSERT INTO credits_transactions
                   (id, user_id, type, amount, source, description, created_at, settlement_status)
                   VALUES (?, ?, 'settlement', ?, ?, ?, ?, 'pending')""",
                (txn_id, user_id, prepaid, source, f"预结算 {prepaid} 积分", now),
            )
            conn.execute(
                """UPDATE credits_accounts
                   SET pending = pending + ?
                   WHERE user_id = ?""",
                (prepaid, user_id),
            )
            conn.commit()
        self._logger.info("创建结算: user=%s, prepaid=%d, source=%s, txn=%s", user_id, prepaid, source, txn_id)
        return txn_id

    def settle_success(self, transaction_id: str, actual_amount: int) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, amount FROM credits_transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                self._logger.warning("结算交易不存在: %s", transaction_id)
                return False

            user_id = row["user_id"]
            prepaid = row["amount"]
            diff = prepaid - actual_amount  # 差额退回

            conn.execute(
                "UPDATE credits_transactions SET settlement_status = 'settled' WHERE id = ?",
                (transaction_id,),
            )
            # 将预结算金额从 pending 移入 balance（已实际到账）
            conn.execute(
                """UPDATE credits_accounts
                   SET balance = balance + actual_amount,
                       pending = pending - prepaid,
                       total_earned = total_earned + actual_amount
                   WHERE user_id = ?""",
                (user_id,),
            )
            # 如有差额，退还
            if diff > 0:
                refund_txn_id = uuid.uuid4().hex[:16]
                conn.execute(
                    """INSERT INTO credits_transactions
                       (id, user_id, type, amount, source, description, created_at, settlement_status)
                       VALUES (?, ?, 'refund', ?, 'refund', ?, ?, 'settled')""",
                    (
                        refund_txn_id,
                        user_id,
                        diff,
                        f"结算差额退回 {diff} 积分",
                        datetime.now().isoformat(),
                    ),
                )
            conn.commit()
        self._logger.info(
            "结算成功: txn=%s, prepaid=%d, actual=%d", transaction_id, prepaid, actual_amount
        )
        return True

    def refund_failed(self, transaction_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, amount FROM credits_transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                self._logger.warning("退款交易不存在: %s", transaction_id)
                return False

            user_id = row["user_id"]
            amount = row["amount"]

            conn.execute(
                "UPDATE credits_transactions SET settlement_status = 'refunded' WHERE id = ?",
                (transaction_id,),
            )
            conn.execute(
                """UPDATE credits_accounts
                   SET pending = pending - ?
                   WHERE user_id = ?""",
                (amount, user_id),
            )
            conn.commit()
        self._logger.info("结算失败退款: txn=%s, amount=%d", transaction_id, amount)
        return True

    # ── CPS 分销 ──────────────────────────────────────────────

    def add_cps_offer(self, offer: CPSOffer) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO cps_offers
                   (id, title, product_url, commission_rate, category, platform)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    offer.id,
                    offer.title,
                    offer.product_url,
                    offer.commission_rate,
                    offer.category,
                    offer.platform,
                ),
            )
            conn.commit()
        self._logger.info("添加 CPS 商品: %s (%s)", offer.title, offer.id)
        return offer.id

    def list_cps_offers(self, category: str | None = None) -> list[CPSOffer]:
        with self._get_conn() as conn:
            query = "SELECT * FROM cps_offers WHERE 1=1"
            params: list[str] = []
            if category:
                query += " AND category = ?"
                params.append(category)
            rows = conn.execute(query, params).fetchall()
        return [
            CPSOffer(
                id=r["id"],
                title=r["title"],
                product_url=r["product_url"],
                commission_rate=r["commission_rate"],
                category=r["category"],
                platform=r["platform"],
            )
            for r in rows
        ]

    def track_cps_click(self, offer_id: str, article_id: str) -> None:
        click_id = uuid.uuid4().hex[:16]
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO cps_clicks (id, offer_id, article_id, clicked_at) VALUES (?, ?, ?, ?)",
                (click_id, offer_id, article_id, datetime.now().isoformat()),
            )
            conn.commit()
        self._logger.info("CPS 点击追踪: offer=%s, article=%s", offer_id, article_id)

    # ── CPE 互动任务 ──────────────────────────────────────────

    def add_cpe_task(self, task: CPETask) -> str:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO cpe_tasks
                   (id, platform, task_type, target_url, reward, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task.id, task.platform, task.task_type, task.target_url, task.reward, task.status),
            )
            conn.commit()
        self._logger.info("添加 CPE 任务: %s (%s)", task.task_type, task.id)
        return task.id

    def complete_cpe_task(self, task_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM cpe_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                self._logger.warning("CPE 任务不存在: %s", task_id)
                return False
            if row["status"] != "pending":
                self._logger.warning("CPE 任务状态非 pending: %s, 当前: %s", task_id, row["status"])
                return False
            conn.execute("UPDATE cpe_tasks SET status = 'completed' WHERE id = ?", (task_id,))
            conn.commit()
        self._logger.info("完成 CPE 任务: %s, 奖励 %d", task_id, row["reward"])
        return True

    # ── 收入报表 ──────────────────────────────────────────────

    def get_revenue_report(self, period: str = "monthly") -> dict:
        with self._get_conn() as conn:
            # 积分汇总
            row = conn.execute(
                "SELECT SUM(balance) as total_balance, SUM(total_earned) as total_earned, "
                "SUM(total_spent) as total_spent, SUM(pending) as total_pending "
                "FROM credits_accounts"
            ).fetchone()

            # CPS 点击统计
            cps_click_count = conn.execute("SELECT COUNT(*) as cnt FROM cps_clicks").fetchone()["cnt"]

            # CPE 任务统计
            cpe_completed = conn.execute(
                "SELECT COUNT(*) as cnt FROM cpe_tasks WHERE status = 'completed'"
            ).fetchone()["cnt"]
            cpe_pending = conn.execute(
                "SELECT COUNT(*) as cnt FROM cpe_tasks WHERE status = 'pending'"
            ).fetchone()["cnt"]

            # 按来源统计积分
            source_rows = conn.execute(
                "SELECT source, SUM(amount) as total FROM credits_transactions "
                "WHERE type = 'earn' GROUP BY source"
            ).fetchall()

        return {
            "period": period,
            "credits": {
                "total_balance": row["total_balance"] or 0,
                "total_earned": row["total_earned"] or 0,
                "total_spent": row["total_spent"] or 0,
                "total_pending": row["total_pending"] or 0,
            },
            "cps": {
                "total_clicks": cps_click_count,
            },
            "cpe": {
                "completed": cpe_completed,
                "pending": cpe_pending,
            },
            "earnings_by_source": {r["source"]: r["total"] for r in source_rows},
        }
