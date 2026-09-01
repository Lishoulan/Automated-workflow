"""通知服务 - 支持多种推送渠道"""
import json
import logging
import os
from abc import ABC, abstractmethod
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import httpx
except ImportError:
    httpx = None


logger = logging.getLogger(__name__)
NOTIFICATION_TIMEZONE = ZoneInfo("Asia/Shanghai")


def notification_now() -> datetime:
    return datetime.now(NOTIFICATION_TIMEZONE)


def _notification_now() -> datetime:
    return notification_now()


def _notification_date(timestamp: object) -> date | None:
    try:
        parsed = datetime.fromisoformat(str(timestamp).strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NOTIFICATION_TIMEZONE)
    return parsed.astimezone(NOTIFICATION_TIMEZONE).date()


class NotificationChannel(ABC):
    """通知渠道基类"""

    @abstractmethod
    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        """发送通知"""
        pass


class BarkNotifier(NotificationChannel):
    """Bark iOS推送"""

    def __init__(self, bark_url: str):
        self.bark_url = bark_url.rstrip("/")

    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        if httpx is None:
            logger.warning("httpx未安装，跳过Bark通知")
            return False
        try:
            url = f"{self.bark_url}/{title}/{message}"
            if extra:
                url += "?" + "&".join([f"{k}={v}" for k, v in extra.items()])
            httpx.get(url, timeout=10)
            return True
        except Exception as e:
            logger.error(f"Bark通知失败: {e}")
            return False


class FeishuWebhookNotifier(NotificationChannel):
    """飞书群机器人"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        if httpx is None:
            logger.warning("httpx未安装，跳过飞书通知")
            return False
        try:
            payload = {
                "msg_type": "text",
                "content": {"text": f"{title}\n{message}"}
            }
            with httpx.Client() as client:
                response = client.post(self.webhook_url, json=payload, timeout=10)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"飞书通知失败: {e}")
            return False


class DingtalkWebhookNotifier(NotificationChannel):
    """钉钉群机器人"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        if httpx is None:
            logger.warning("httpx未安装，跳过钉钉通知")
            return False
        try:
            payload = {
                "msgtype": "text",
                "text": {"content": f"{title}\n{message}"}
            }
            with httpx.Client() as client:
                response = client.post(self.webhook_url, json=payload, timeout=10)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"钉钉通知失败: {e}")
            return False


class WeComWebhookNotifier(NotificationChannel):
    """企业微信群机器人"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        if httpx is None:
            logger.warning("httpx未安装，跳过企业微信通知")
            return False
        try:
            payload = {
                "msgtype": "text",
                "text": {"content": f"{title}\n{message}"}
            }
            with httpx.Client() as client:
                response = client.post(self.webhook_url, json=payload, timeout=10)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"企业微信通知失败: {e}")
            return False


class ServerChanNotifier(NotificationChannel):
    """ServerChan（方糖）微信推送：https://sct.ftqq.com

    用法：POST https://sctapi.ftqq.com/<SENDKEY>.send
    """

    def __init__(self, sendkey: str):
        self.sendkey = sendkey.strip()
        self.api_url = "https://sctapi.ftqq.com/{}.send".format(self.sendkey)

    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        if httpx is None:
            logger.warning("httpx未安装，跳过ServerChan通知")
            return False
        try:
            payload = {
                "title": title,
                "desp": f"{message}\n\n> 发送时间：{_notification_now().strftime('%Y-%m-%d %H:%M:%S')}",
            }
            with httpx.Client() as client:
                response = client.post(self.api_url, data=payload, timeout=10)
                ok = response.status_code == 200
                if ok:
                    # ServerChan can return HTTP 200 for a business-level
                    # failure such as code=40001 (daily quota exhausted).
                    # Treat that response as a failed delivery so it is not
                    # recorded as a successful notification.
                    try:
                        result = response.json()
                    except (TypeError, ValueError):
                        result = None
                    if isinstance(result, dict):
                        code = result.get("code")
                        errno = result.get("errno")
                        if code is not None and str(code) != "0":
                            ok = False
                        if errno is not None and str(errno) != "0":
                            ok = False
                if not ok:
                    logger.error("ServerChan通知失败: HTTP %s", response.status_code)
                return ok
        except Exception as e:
            logger.error(f"ServerChan通知失败: {e}")
            return False


class PushPlusNotifier(NotificationChannel):
    """PushPlus 微信推送：https://www.pushplus.plus

    用法：POST https://www.pushplus.plus/send，body 含 token/title/content
    """

    def __init__(self, token: str):
        self.token = token.strip()

    def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> bool:
        if httpx is None:
            logger.warning("httpx未安装，跳过PushPlus通知")
            return False
        try:
            payload = {
                "token": self.token,
                "title": title,
                "content": f"{message}\n\n发送时间：{_notification_now().strftime('%Y-%m-%d %H:%M:%S')}",
                "template": "html",
            }
            with httpx.Client() as client:
                response = client.post("https://www.pushplus.plus/send", json=payload, timeout=10)
                ok = response.status_code == 200
                if not ok:
                    logger.error("PushPlus通知失败: HTTP %s", response.status_code)
                return ok
        except Exception as e:
            logger.error(f"PushPlus通知失败: {e}")
            return False


class NotificationService:
    """统一的通知服务"""

    # ServerChan 免费版每天最多 5 条。日常事件不即时推送，因此正常日
    # 只会消耗 1 条日报额度；其余额度只留给真正的紧急告警。
    MAX_FREE_DAILY_LIMIT = 5
    DEFAULT_DAILY_SEND_LIMIT = MAX_FREE_DAILY_LIMIT
    # Keep the class attribute for backwards compatibility; each instance
    # resolves the environment again after load_dotenv() has run.
    DAILY_SEND_LIMIT = DEFAULT_DAILY_SEND_LIMIT
    DAILY_REPORT_RESERVE = 1

    def __init__(self, config: dict[str, Any], fallback_log_dir: str | None = None):
        self.channels: list[NotificationChannel] = []
        self.fallback_log_path: Path | None = None
        self.DAILY_SEND_LIMIT = self._resolve_daily_send_limit(config)
        if fallback_log_dir:
            self.fallback_log_path = Path(fallback_log_dir) / "notification_log.jsonl"
        self.global_log_path = self._resolve_global_log_path(self.fallback_log_path)
        self._log_paths = self._build_log_paths()
        self._report_log_paths = self._build_report_log_paths()
        inferred_account = self._infer_account_from_log_path()
        self._account = str(
            config.get("account_key")
            or inferred_account
            or config.get("TOUTIAO_ACCOUNT")
            or config.get("ACCOUNT_KEY")
        ).strip()
        self._memory_pushed_by_date: dict[str, int] = {}
        self._memory_pushed_titles: set[tuple[str, str]] = set()
        self._memory_report_dates: set[str] = set()

        if bark_url := config.get("BARK_URL"):
            self.channels.append(BarkNotifier(bark_url))

        if feishu_webhook := config.get("FEISHU_WEBHOOK_URL"):
            self.channels.append(FeishuWebhookNotifier(feishu_webhook))

        if dingtalk_webhook := config.get("DINGTALK_WEBHOOK_URL"):
            self.channels.append(DingtalkWebhookNotifier(dingtalk_webhook))

        if wecom_webhook := config.get("WECOM_WEBHOOK_URL"):
            self.channels.append(WeComWebhookNotifier(wecom_webhook))

        if serverchan_key := config.get("SERVERCHAN_SENDKEY") or config.get("SERVERCHAN_KEY"):
            self.channels.append(ServerChanNotifier(serverchan_key))

        if pushplus_token := config.get("PUSHPLUS_TOKEN"):
            self.channels.append(PushPlusNotifier(pushplus_token))

        if not self.channels:
            logger.debug(
                "No notification channels configured. Set at least one of: "
                "BARK_URL, FEISHU_WEBHOOK_URL, DINGTALK_WEBHOOK_URL, WECOM_WEBHOOK_URL, "
                "SERVERCHAN_SENDKEY, PUSHPLUS_TOKEN. "
                "All notifications will be silently dropped."
            )

    @classmethod
    def _resolve_daily_send_limit(cls, config: dict[str, Any] | None) -> int:
        """Resolve the limit at instance creation, after .env is loaded."""
        raw_value: object | None = None
        if isinstance(config, dict):
            raw_value = config.get("NOTIFY_DAILY_LIMIT")
        if raw_value is None or not str(raw_value).strip():
            raw_value = os.environ.get("NOTIFY_DAILY_LIMIT")
        if raw_value is None or not str(raw_value).strip():
            raw_value = cls.DEFAULT_DAILY_SEND_LIMIT
        try:
            configured = int(str(raw_value).strip())
        except (TypeError, ValueError):
            configured = cls.DEFAULT_DAILY_SEND_LIMIT
        return min(cls.MAX_FREE_DAILY_LIMIT, max(0, configured))

    @property
    def is_configured(self) -> bool:
        return len(self.channels) > 0

    def notify_success(self, platform: str, article_title: str, run_time: str) -> None:
        """发布成功通知：仅记录本地日志（信息并入每日 22:00 日报推送，节省方糖额度）"""
        title = f"【{platform}】文章发布成功"
        message = f"{run_time} | 《{article_title}》已成功发布至{platform}"
        self._write_fallback_log(title, message, pushed=False, kind="success")

    def notify_failure(
        self,
        platform: str,
        stage: str,
        error_msg: str,
        run_time: str,
        debug_url: str | None = None
    ) -> None:
        """发布失败通知：仅记录本地日志（信息并入每日 22:00 日报推送，节省方糖额度）"""
        title = f"【{platform}】发布失败"
        message = f"""{run_time} 任务执行失败！
阶段: {stage}
错误: {error_msg}
"""
        if debug_url:
            message += f"调试: {debug_url}"

        message += "\n请前往后台处理。"
        self._write_fallback_log(title, message, pushed=False, kind="failure")

    def notify_review_required(
        self,
        *,
        account: str,
        selected_title: str,
        run_dir: str,
        reason: str,
        run_time: str,
    ) -> None:
        """记录人工审核事件，留待统一日报发送。

        人工审核门禁是正常业务状态，不应在每个定时批次单独占用方糖额度。
        每次运行仍会保留完整上下文，日报会按日期汇总这些记录。
        """
        title = f"【{account}】定时任务需要人工审核"
        message = (
            f"{run_time} 定时内容未自动发布，需人工审核。\n"
            f"标题: 《{selected_title or '未选择标题'}》\n"
            f"运行目录: {run_dir or 'N/A'}\n"
            f"原因: {reason or '未达到自动发布门槛'}\n"
            "请修改并审核通过后再发布。"
        )
        self._write_fallback_log(title, message, pushed=False, kind="review_required")

    def notify_urgent(self, title: str, message: str) -> None:
        """即时发送紧急通知，但仍受持久化的每日额度和去重保护。"""
        self._broadcast(title, message, force=True)

    @staticmethod
    def _resolve_global_log_path(path: Path | None) -> Path | None:
        """Use one shared ledger when account logs live under output/accounts."""
        if path is None:
            return None
        resolved = path.resolve()
        automation_dir = resolved.parent
        accounts_dir = automation_dir.parent.parent
        output_dir = accounts_dir.parent
        if (
            automation_dir.name == "automation"
            and accounts_dir.name == "accounts"
            and output_dir.name == "output"
        ):
            return output_dir / "automation" / resolved.name
        return resolved

    def _build_log_paths(self) -> list[Path]:
        paths: list[Path] = []
        for path in (self.fallback_log_path, self.global_log_path):
            if path is not None and path not in paths:
                paths.append(path)
        return paths

    def _build_report_log_paths(self) -> list[Path]:
        """Include existing account ledgers when building a shared report."""
        paths = self._build_log_paths()
        if not self.global_log_path:
            return paths
        output_dir = self.global_log_path.parent.parent
        if self.global_log_path.parent.name != "automation" or output_dir.name != "output":
            return paths
        accounts_dir = output_dir / "accounts"
        if not accounts_dir.is_dir():
            return paths
        for path in sorted(accounts_dir.glob(f"*/automation/{self.global_log_path.name}")):
            if path not in paths:
                paths.append(path)
        return paths

    def _infer_account_from_log_path(self) -> str:
        if not self.fallback_log_path:
            return ""
        automation_dir = self.fallback_log_path.parent
        if automation_dir.name == "automation" and automation_dir.parent.parent.name == "accounts":
            return automation_dir.parent.name
        return ""

    @contextmanager
    def _notification_lock(self) -> Iterator[None]:
        """Serialize quota checks, delivery, and ledger writes across processes."""
        lock_target = self.global_log_path or self.fallback_log_path
        if not lock_target:
            yield
            return

        lock_path = lock_target.with_name(f".{lock_target.name}.lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "a+b")
        except OSError as exc:
            logger.warning("无法创建通知锁，继续执行: %s", exc)
            yield
            return

        backend = ""
        acquired = False
        try:
            try:
                lock_file.seek(0, 2)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    backend = "msvcrt"
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    backend = "fcntl"
                acquired = True
            except (ImportError, OSError) as exc:
                logger.warning("无法获取通知锁，继续执行: %s", exc)

            yield
        finally:
            if acquired:
                try:
                    lock_file.seek(0)
                    if backend == "msvcrt":
                        import msvcrt

                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    elif backend == "fcntl":
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError) as exc:
                    logger.debug("释放人工审核通知锁失败: %s", exc)
            lock_file.close()

    @contextmanager
    def _review_notification_lock(self) -> Iterator[None]:
        """Backward-compatible alias for callers using the old lock name."""
        with self._notification_lock():
            yield

    def _append_fallback_log(
        self,
        title: str,
        message: str,
        *,
        pushed: bool = False,
        kind: str = "event",
        report_date: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._log_paths:
            return
        try:
            entry = {
                "timestamp": _notification_now().isoformat(),
                "title": title,
                "message": message,
                "pushed": pushed,
                "kind": kind,
            }
            if self._account:
                entry["account"] = self._account
            if report_date:
                entry["report_date"] = report_date
            if metadata:
                entry["metadata"] = dict(metadata)
            serialized = json.dumps(entry, ensure_ascii=False) + "\n"
            for path in self._log_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as file_handle:
                    file_handle.write(serialized)
        except Exception as e:
            logger.debug(f"通知日志写入失败: {e}")

    def _write_fallback_log(
        self,
        title: str,
        message: str,
        pushed: bool = False,
        *,
        kind: str = "event",
        report_date: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write the event ledger used by the daily report."""
        if not self._log_paths:
            return
        with self._notification_lock():
            self._append_fallback_log(
                title,
                message,
                pushed=pushed,
                kind=kind,
                report_date=report_date,
                metadata=metadata,
            )

    def _read_log_entries(self) -> list[dict[str, Any]]:
        """Read local and shared ledgers, removing copies written to both."""
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in self._report_log_paths:
            if not path.exists():
                continue
            try:
                raw_lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for raw_line in raw_lines:
                try:
                    entry = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                fingerprint = json.dumps(entry, ensure_ascii=False, sort_keys=True)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                entries.append(entry)
        return entries

    def _has_pushed_title_today(self, title: str) -> bool:
        """检查本地日志中今天是否已经成功推送过指定标题。"""
        today = _notification_now().date()
        normalized_account = self._normalize_account(self._account)
        for entry in self._read_log_entries():
            entry_account = self._normalize_account(entry.get("account", ""))
            if (
                entry.get("pushed") is True
                and str(entry.get("title", "")) == title
                and _notification_date(entry.get("timestamp")) == today
                and not (normalized_account and entry_account and normalized_account != entry_account)
            ):
                return True
        if (today.isoformat(), title) in self._memory_pushed_titles:
            return True
        return False

    def _count_pushed_today(self, target_date: date) -> int:
        persisted = sum(
            1
            for entry in self._read_log_entries()
            if entry.get("pushed") is True
            and _notification_date(entry.get("timestamp")) == target_date
        )
        if self._log_paths:
            return persisted
        return persisted + self._memory_pushed_by_date.get(target_date.isoformat(), 0)

    @staticmethod
    def _normalize_account(value: object) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    def _deliver(self, title: str, message: str) -> tuple[bool, bool]:
        """Return (any channel succeeded, quota-bearing delivery succeeded)."""
        sent_any = False
        sent_serverchan = False
        has_serverchan = any(isinstance(channel, ServerChanNotifier) for channel in self.channels)
        for channel in self.channels:
            try:
                if channel.send(title, message):
                    sent_any = True
                    if isinstance(channel, ServerChanNotifier):
                        sent_serverchan = True
            except Exception as exc:
                logger.warning("通知发送失败: %s", exc)
        return sent_any, (sent_serverchan if has_serverchan else sent_any)

    def _broadcast(self, title: str, message: str, force: bool = False) -> None:
        """Queue routine events; deliver only urgent events immediately."""
        if not force:
            self._write_fallback_log(title, message, pushed=False, kind="event")
            logger.info("日常通知已记入日报队列: %s", title)
            return

        today = _notification_now().date()
        with self._notification_lock():
            if self._has_pushed_title_today(title):
                logger.info("紧急通知今日已成功推送，跳过重复推送: %s", title)
                self._append_fallback_log(title, message, pushed=False, kind="urgent")
                return
            used = self._count_pushed_today(today)
            urgent_limit = self.DAILY_SEND_LIMIT
            if not self._has_daily_report_sent(today):
                # Reserve the final free notification for the fixed-time
                # digest, so a burst of urgent alerts cannot suppress it.
                urgent_limit = max(0, urgent_limit - self.DAILY_REPORT_RESERVE)
            if used >= urgent_limit:
                logger.warning(
                    "通知已达到紧急告警额度 %d 条，跳过即时推送并记入日报: %s",
                    urgent_limit,
                    title,
                )
                self._append_fallback_log(
                    title,
                    message,
                    pushed=False,
                    kind="urgent",
                    metadata={
                        "quota_exhausted": True,
                        "quota_used": used,
                        "quota_limit": urgent_limit,
                        "daily_limit": self.DAILY_SEND_LIMIT,
                    },
                )
                return
            sent_any, delivered = self._deliver(title, message)
            self._append_fallback_log(
                title,
                message,
                pushed=delivered,
                kind="urgent",
                metadata={"channel_sent": sent_any, "quota_used": used + int(delivered)},
            )
            if delivered:
                if not self._log_paths:
                    date_key = today.isoformat()
                    self._memory_pushed_by_date[date_key] = (
                        self._memory_pushed_by_date.get(date_key, 0) + 1
                    )
                    self._memory_pushed_titles.add((date_key, title))
                logger.info(
                    "紧急通知已发送（今日第 %d/%d 条）: %s",
                    used + 1,
                    self.DAILY_SEND_LIMIT,
                    title,
                )

    @staticmethod
    def _coerce_report_date(value: date | datetime | str | None) -> date:
        if value is None:
            return _notification_now().date()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=NOTIFICATION_TIMEZONE)
            return value.astimezone(NOTIFICATION_TIMEZONE).date()
        if isinstance(value, date):
            return value
        parsed = _notification_date(value)
        if parsed is not None:
            return parsed
        return date.fromisoformat(str(value).strip())

    @staticmethod
    def _entry_kind(entry: dict[str, Any]) -> str:
        kind = str(entry.get("kind", "")).strip().lower()
        if kind:
            return kind
        title = str(entry.get("title", ""))
        if "需要人工审核" in title:
            return "review_required"
        if "发布成功" in title:
            return "success"
        if "发布失败" in title:
            return "failure"
        if "紧急" in title or title.startswith("[URGENT]"):
            return "urgent"
        return "event"

    @classmethod
    def _is_daily_report_entry(cls, entry: dict[str, Any]) -> bool:
        kind = cls._entry_kind(entry)
        title = str(entry.get("title", ""))
        return kind == "daily_report" or title.startswith(("【头条日报】", "【自动化日报】"))

    def _has_daily_report_sent(self, report_day: date) -> bool:
        report_day_text = report_day.isoformat()
        if not self._log_paths and report_day_text in self._memory_report_dates:
            return True
        for entry in self._read_log_entries():
            if entry.get("pushed") is not True or not self._is_daily_report_entry(entry):
                continue
            if str(entry.get("report_date", "")).strip() == report_day_text:
                return True
            # Compatibility with older report records that had no explicit
            # report_date field: their timestamp is the report's local date.
            if not entry.get("report_date") and _notification_date(entry.get("timestamp")) == report_day:
                return True
        return False

    def _daily_report_entries(self, report_day: date) -> list[dict[str, Any]]:
        return sorted(
            [
                entry
                for entry in self._read_log_entries()
                if _notification_date(entry.get("timestamp")) == report_day
                and not self._is_daily_report_entry(entry)
            ],
            key=lambda entry: str(entry.get("timestamp", "")),
        )

    @staticmethod
    def _shorten(text: object, limit: int) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 3)] + "..."

    def _build_daily_report(self, report_day: date, entries: list[dict[str, Any]]) -> tuple[str, str]:
        counts = Counter(self._entry_kind(entry) for entry in entries)
        title = f"【自动化日报】{report_day.month}月{report_day.day}日 {len(entries)}条事件"
        summary = (
            f"日期：{report_day.isoformat()}（Asia/Shanghai）\n"
            f"事件：{len(entries)} 条；人工审核 {counts.get('review_required', 0)}；"
            f"成功 {counts.get('success', 0)}；失败 {counts.get('failure', 0)}；"
            f"紧急 {counts.get('urgent', 0)}。"
        )
        if not entries:
            return title, summary + "\n今日没有需要处理的自动化通知。"

        lines = [summary, "", "事件明细："]
        for index, entry in enumerate(entries[:20], start=1):
            timestamp = str(entry.get("timestamp", ""))
            try:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=NOTIFICATION_TIMEZONE)
                display_time = parsed.astimezone(NOTIFICATION_TIMEZONE).strftime("%H:%M")
            except (TypeError, ValueError):
                display_time = "??:??"
            status = "已即时推送" if entry.get("pushed") is True else "待处理"
            lines.append(
                f"{index}. [{display_time}][{self._entry_kind(entry)}][{status}] "
                f"{self._shorten(entry.get('title', ''), 100)}"
            )
            lines.append(f"   {self._shorten(entry.get('message', ''), 360)}")
        if len(entries) > 20:
            lines.append(f"其余 {len(entries) - 20} 条事件已保留在 notification_log.jsonl。")
        return title, "\n".join(lines)

    def send_daily_report(self, report_date: date | datetime | str | None = None) -> dict[str, Any]:
        """Send one date-scoped report and atomically record its delivery."""
        report_day = self._coerce_report_date(report_date)
        report_day_text = report_day.isoformat()
        with self._notification_lock():
            if self._has_daily_report_sent(report_day):
                return {
                    "status": "already_sent",
                    "report_date": report_day.isoformat(),
                    "event_count": len(self._daily_report_entries(report_day)),
                }

            entries = self._daily_report_entries(report_day)
            if not entries:
                # An empty heartbeat would spend a real ServerChan quota
                # without adding information. A later invocation can still
                # send the report if an event is recorded the same day.
                return {
                    "status": "no_events",
                    "report_date": report_day.isoformat(),
                    "event_count": 0,
                    "quota_used": self._count_pushed_today(report_day),
                }
            title, message = self._build_daily_report(report_day, entries)
            used = self._count_pushed_today(report_day)
            if not self.channels:
                self._append_fallback_log(
                    title,
                    message,
                    pushed=False,
                    kind="daily_report",
                    report_date=report_day.isoformat(),
                    metadata={"no_channels": True, "quota_used": used},
                )
                return {
                    "status": "no_channels",
                    "report_date": report_day.isoformat(),
                    "event_count": len(entries),
                    "quota_used": used,
                }
            if used >= self.DAILY_SEND_LIMIT:
                self._append_fallback_log(
                    title,
                    message,
                    pushed=False,
                    kind="daily_report",
                    report_date=report_day.isoformat(),
                    metadata={"quota_exhausted": True, "quota_used": used},
                )
                return {
                    "status": "quota_exhausted",
                    "report_date": report_day.isoformat(),
                    "event_count": len(entries),
                    "quota_used": used,
                }

            sent_any, delivered = self._deliver(title, message)
            self._append_fallback_log(
                title,
                message,
                pushed=delivered,
                kind="daily_report",
                report_date=report_day.isoformat(),
                metadata={"channel_sent": sent_any, "quota_used": used + int(delivered)},
            )
            if delivered:
                if not self._log_paths:
                    self._memory_pushed_by_date[report_day_text] = (
                        self._memory_pushed_by_date.get(report_day_text, 0) + 1
                    )
                    self._memory_report_dates.add(report_day_text)
                logger.info(
                    "每日日报已发送（今日第 %d/%d 条）: %s",
                    used + 1,
                    self.DAILY_SEND_LIMIT,
                    report_day.isoformat(),
                )
                status = "sent"
            else:
                logger.warning("每日日报发送失败，将保留并允许下一次重试: %s", report_day.isoformat())
                status = "delivery_failed"
            return {
                "status": status,
                "report_date": report_day.isoformat(),
                "event_count": len(entries),
                "quota_used": used + int(delivered),
            }
