"""多平台发布基类模块"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from toutiao_content_factory.config import AppSettings

PLATFORM_RULES: dict[str, dict] = {
    "toutiao": {
        "max_length": 50000,
        "tag_prefix": "#",
        "tag_suffix": "#",
    },
    "wechat": {
        "max_length": 20000,
    },
    "douyin": {
        "max_length": 3000,
        "tag_prefix": "#",
        "tag_suffix": " ",
    },
    "bilibili": {
        "max_length": 25000,
        "tag_suffix": ",",
    },
    "zhihu": {
        "max_length": 50000,
    },
    "weibo": {
        "max_length": 2000,
        "tag_prefix": "#",
        "tag_suffix": "#",
    },
    "xiaohongshu": {
        "max_length": 1000,
        "tag_prefix": "#",
        "tag_suffix": " ",
        "max_images": 9,
    },
}


@dataclass
class PublishResult:
    """发布结果数据类"""

    success: bool
    post_id: str = ""
    permalink: str = ""
    platform: str = ""
    error: str = ""


class PlatformPublisher(ABC):
    """平台发布抽象基类"""

    @abstractmethod
    def publish_article(
        self,
        title: str,
        content: str,
        images: list[str],
        *,
        tags: list[str] | None = None,
        category: str | None = None,
    ) -> PublishResult:
        """发布文章到平台"""

    @abstractmethod
    def check_login_status(self) -> bool:
        """检查登录状态"""

    @abstractmethod
    def login_interactive(self) -> dict:
        """交互式登录"""

    def adapt_content(self, content: str, platform: str) -> str:
        """根据平台规则适配内容（默认实现）"""
        rules = PLATFORM_RULES.get(platform, {})
        max_length = rules.get("max_length", len(content))
        if len(content) > max_length:
            content = content[:max_length]
        return content


class ContentAdapter:
    """内容适配器，根据平台规则调整标题、内容和标签"""

    def adapt_for_platform(
        self,
        content: str,
        title: str,
        platform: str,
        tags: list[str] | None = None,
    ) -> dict:
        """为指定平台适配内容，返回适配后的 {title, content, tags}"""
        rules = PLATFORM_RULES.get(platform, {})
        max_length = rules.get("max_length", len(content))

        adapted_content = self._truncate_content(content, max_length)
        formatted_tags = self._format_tags(tags or [], platform)

        if formatted_tags:
            adapted_content = f"{adapted_content}\n\n{formatted_tags}"

        return {
            "title": title,
            "content": adapted_content,
            "tags": tags or [],
        }

    def _truncate_content(self, content: str, max_length: int) -> str:
        """智能截断内容，在句子边界处截断"""
        if len(content) <= max_length:
            return content

        truncated = content[:max_length]
        # 在句子结束标点处截断
        sentence_endings = re.compile(r"[。！？.!?\n]")
        matches = list(sentence_endings.finditer(truncated))
        if matches:
            last_match = matches[-1]
            truncated = truncated[: last_match.end()]

        return truncated

    def _format_tags(self, tags: list[str], platform: str) -> str:
        """根据平台规则格式化标签"""
        rules = PLATFORM_RULES.get(platform, {})

        # 不支持标签的平台直接返回空
        if "tag_prefix" not in rules and "tag_suffix" not in rules:
            return ""

        prefix = rules.get("tag_prefix", "")
        suffix = rules.get("tag_suffix", "")

        formatted = [f"{prefix}{tag}{suffix}" for tag in tags]
        return " ".join(formatted).strip()


class MultiPlatformPublisher:
    """多平台发布管理器"""

    def __init__(self, settings: AppSettings, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self._publishers: dict[str, PlatformPublisher] = {}
        self._content_adapter = ContentAdapter()

    def register_publisher(self, platform: str, publisher: PlatformPublisher) -> None:
        """注册平台发布器"""
        self._publishers[platform] = publisher
        self.logger.info("已注册平台发布器: %s", platform)

    def publish_to_platforms(
        self,
        title: str,
        content: str,
        images: list[str],
        platforms: list[str],
        *,
        tags: list[str] | None = None,
    ) -> dict[str, PublishResult]:
        """发布内容到多个平台，返回各平台发布结果"""
        results: dict[str, PublishResult] = {}

        for platform in platforms:
            publisher = self._publishers.get(platform)
            if publisher is None:
                self.logger.warning("未注册的平台: %s，跳过", platform)
                results[platform] = PublishResult(
                    success=False,
                    platform=platform,
                    error=f"未注册的平台: {platform}",
                )
                continue

            try:
                adapted = self._content_adapter.adapt_for_platform(
                    content=content,
                    title=title,
                    platform=platform,
                    tags=tags,
                )

                # 处理图片数量限制
                rules = PLATFORM_RULES.get(platform, {})
                max_images = rules.get("max_images")
                adapted_images = images[:max_images] if max_images else images

                result = publisher.publish_article(
                    title=adapted["title"],
                    content=adapted["content"],
                    images=adapted_images,
                    tags=adapted["tags"],
                )
                results[platform] = result
                self.logger.info(
                    "平台 %s 发布%s", platform, "成功" if result.success else "失败"
                )
            except Exception as e:
                self.logger.exception("平台 %s 发布异常", platform)
                results[platform] = PublishResult(
                    success=False,
                    platform=platform,
                    error=str(e),
                )

        return results

    def get_available_platforms(self) -> list[str]:
        """获取已注册的可用平台列表"""
        return list(self._publishers.keys())
