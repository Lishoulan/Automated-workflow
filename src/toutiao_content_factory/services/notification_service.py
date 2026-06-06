"""通知服务 - 支持多种推送渠道"""
import json
import logging
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


class NotificationService:
    """统一的通知服务"""

    def __init__(self, config: dict[str, Any], fallback_log_dir: str | None = None):
        self.channels: list[NotificationChannel] = []
        self.fallback_log_path: Path | None = None
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

        if not self.channels:
            logger.debug(
                "No notification channels configured. Set at least one of: "
                "BARK_URL, FEISHU_WEBHOOK_URL, DINGTALK_WEBHOOK_URL, WECOM_WEBHOOK_URL. "
                "All notifications will be silently dropped."
            )

    @property
    def is_configured(self) -> bool:
        return len(self.channels) > 0

    def notify_success(self, platform: str, article_title: str, run_time: str) -> None:
        """发送成功通知"""
        title = f"【{platform}】文章发布成功"
        message = f"{run_time} | 《{article_title}》已成功发布至{platform}"
        self._broadcast(title, message)

    def notify_failure(
        self,
        platform: str,
        stage: str,
        error_msg: str,
        run_time: str,
        debug_url: str | None = None
    ) -> None:
        """发送失败通知"""
        title = f"【{platform}】发布失败"
        message = f"""{run_time} 任务执行失败！
阶段: {stage}
错误: {error_msg}
"""
        if debug_url:
            message += f"调试: {debug_url}"

        message += "\n请前往后台处理。"
        self._broadcast(title, message)

    def notify_urgent(self, title: str, message: str) -> None:
        """发送紧急通知"""
        self._broadcast(title, message)

    def _broadcast(self, title: str, message: str) -> None:
        """向所有渠道广播"""
        sent = False
        for channel in self.channels:
            try:
                if channel.send(title, message):
                    sent = True
            except Exception as e:
                logger.warning(f"通知发送失败: {e}")
        # 兜底：写入本地日志
        if not sent and self.fallback_log_path:
            try:
                self.fallback_log_path.parent.mkdir(parents=True, exist_ok=True)
                entry = {
                    "timestamp": datetime.now().isoformat(),
                    "title": title,
                    "message": message,
                }
                with open(self.fallback_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.debug(f"通知日志写入失败: {e}")
