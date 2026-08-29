"""通知服务 - 支持多种推送渠道"""
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None


logger = logging.getLogger(__name__)


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
                "desp": f"{message}\n\n> 发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            }
            with httpx.Client() as client:
                response = client.post(self.api_url, data=payload, timeout=10)
                ok = response.status_code == 200
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
                "content": f"{message}\n\n发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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

    # 每日推送限频（方糖 ServerChan 免费额度有限，默认每天只发 1 条总结/关键通知）
    DAILY_SEND_LIMIT = int(os.environ.get("NOTIFY_DAILY_LIMIT", "1") or "1")

    def __init__(self, config: dict[str, Any], fallback_log_dir: str | None = None):
        self.channels: list[NotificationChannel] = []
        self.fallback_log_path: Path | None = None
        self._send_date: str = ""
        self._daily_sent: int = 0
        if fallback_log_dir:
            self.fallback_log_path = Path(fallback_log_dir) / "notification_log.jsonl"

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

    @property
    def is_configured(self) -> bool:
        return len(self.channels) > 0

    def notify_success(self, platform: str, article_title: str, run_time: str) -> None:
        """发布成功通知：仅记录本地日志（信息并入每日 22:00 日报推送，节省方糖额度）"""
        title = f"【{platform}】文章发布成功"
        message = f"{run_time} | 《{article_title}》已成功发布至{platform}"
        self._write_fallback_log(title, message, pushed=False)

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
        self._write_fallback_log(title, message, pushed=False)

    def notify_urgent(self, title: str, message: str) -> None:
        """发送紧急通知（登录过期/健康检查失败等，始终推送，不受每日限频限制）"""
        self._broadcast(title, message, force=True)

    def _write_fallback_log(self, title: str, message: str, pushed: bool = False) -> None:
        """写入本地通知日志（每日日报的素材来源）"""
        if not self.fallback_log_path:
            return
        try:
            self.fallback_log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "title": title,
                "message": message,
                "pushed": pushed,
            }
            with open(self.fallback_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"通知日志写入失败: {e}")

    def _broadcast(self, title: str, message: str, force: bool = False) -> None:
        """向所有渠道广播。

        每日限频：同一天内最多发送 DAILY_SEND_LIMIT 条非紧急通知（默认 1 条），
        超出的只写入本地 fallback 日志，不消耗方糖免费额度。
        紧急通知（force=True，如登录过期）始终推送。
        """
        sent = False
        today = datetime.now().strftime("%Y-%m-%d")
        if self._send_date != today:
            self._send_date = today
            self._daily_sent = 0
        if force or self._daily_sent < self.DAILY_SEND_LIMIT:
            for channel in self.channels:
                try:
                    if channel.send(title, message):
                        sent = True
                except Exception as e:
                    logger.warning(f"通知发送失败: {e}")
            if sent:
                self._daily_sent += 1
                logger.info(
                    "通知已发送（今日第 %d/%d 条）: %s",
                    self._daily_sent,
                    self.DAILY_SEND_LIMIT,
                    title,
                )
        else:
            logger.info(
                "通知已到每日限频（%d 条），跳过推送仅记录日志: %s",
                self.DAILY_SEND_LIMIT,
                title,
            )
        # 兜底：写入本地日志（无论是否推送，都保留完整记录供日报汇总）
        self._write_fallback_log(title, message, pushed=sent)
