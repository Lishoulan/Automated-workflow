"""知乎专栏文章发布器 — 基于 Playwright 浏览器自动化"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.multi_platform_publisher import (
    PlatformPublisher,
    PublishResult,
)
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class ZhihuPublisher(PlatformPublisher):
    """知乎专栏文章发布器，通过 Playwright 自动化浏览器完成发布流程。"""

    PUBLISH_URL = "https://zhuanlan.zhihu.com/write"

    # ── CSS 选择器 ──────────────────────────────────────────────
    _TITLE_SELECTORS: list[str] = [
        "textarea[placeholder*='标题']",
        "input[placeholder*='标题']",
        "textarea.WriteIndex-titleInput",
        "textarea.WriteTitle",
    ]
    _EDITOR_SELECTORS: list[str] = [
        "div.ProseMirror",
        "div[contenteditable='true'][data-placeholder*='正文']",
        "div[contenteditable='true'].public-DraftEditor-content",
        "div[contenteditable='true'].editable",
        "div[contenteditable='true']",
    ]
    _COVER_UPLOAD_SELECTORS: list[str] = [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]
    _TOPIC_INPUT_SELECTORS: list[str] = [
        "input[placeholder*='搜索话题']",
        "input[placeholder*='添加话题']",
        "input[placeholder*='话题']",
        "input.TopicSelector-input",
    ]
    _PUBLISH_BUTTON_SELECTORS: list[str] = [
        "button:has-text('发布')",
        "button:has-text('发表文章')",
        "button.WriteIndex-publishButton",
        "button.PublishButton",
    ]
    _SUCCESS_INDICATORS: list[str] = [
        "text=发布成功",
        "text=文章已发布",
        "text=提交成功",
    ]
    _LOGIN_MARKERS: list[str] = [
        "登录知乎",
        "注册",
        "手机号登录",
        "密码登录",
        "扫码登录",
    ]

    def __init__(self, settings: AppSettings, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

    # ── 公开接口 ────────────────────────────────────────────────

    def publish_article(
        self,
        title: str,
        content: str,
        images: list[str],
        *,
        tags: list[str] | None = None,
        category: str | None = None,
    ) -> PublishResult:
        """发布文章到知乎专栏。

        Args:
            title: 文章标题。
            content: 文章正文（纯文本或 Markdown）。
            images: 图片路径列表，第一张作为封面图。
            tags: 话题标签列表。
            category: 专栏分类（知乎暂不使用）。

        Returns:
            PublishResult 发布结果。
        """
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="zhihu:publish",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run():
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=self.settings.publishing.headless,
                    channel=self.settings.publishing.channel or None,
                    executable_path=self.settings.publishing.executable_path or None,
                )
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    timeout = max(self.settings.publishing.wait_timeout_ms, 15000)

                    # 1. 导航到写作页面
                    page.goto(self.PUBLISH_URL, wait_until="domcontentloaded", timeout=timeout)
                    page.wait_for_timeout(2000)

                    # 检查登录状态
                    if self._looks_like_login_page(page):
                        return PublishResult(
                            success=False,
                            platform="zhihu",
                            error="知乎未登录，请先运行交互式登录完成认证。",
                        )

                    # 2. 填写标题
                    self._fill_title(page, title)

                    # 3. 填写正文
                    self._fill_content(page, content)

                    # 4. 上传封面图
                    cover_image = images[0] if images else None
                    if cover_image:
                        uploaded = self._upload_cover_image(page, cover_image)
                        if uploaded:
                            self.logger.info("知乎封面图上传成功")
                        else:
                            self.logger.warning("知乎封面图上传失败，继续发布流程")

                    # 5. 添加话题/标签
                    if tags:
                        self._add_topics(page, tags)

                    # 6. 点击发布
                    self._click_publish(page)

                    # 7. 等待发布成功
                    if self._wait_for_success(page):
                        permalink = page.url
                        post_id = self._extract_post_id(permalink)
                        self.logger.info("知乎文章发布成功: %s", permalink)
                        return PublishResult(
                            success=True,
                            post_id=post_id,
                            permalink=permalink,
                            platform="zhihu",
                        )

                    return PublishResult(
                        success=False,
                        platform="zhihu",
                        error="知乎文章发布未检测到成功标识，请手动确认。",
                    )

                except Exception as exc:
                    self.logger.exception("知乎文章发布异常")
                    return PublishResult(
                        success=False,
                        platform="zhihu",
                        error=str(exc),
                    )
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=300)

    def check_login_status(self) -> bool:
        """检查知乎登录状态，返回 True 表示已登录。"""
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        if not profile_dir.exists():
            return False

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="zhihu:check-login",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run():
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=True,
                    channel=self.settings.publishing.channel or None,
                    executable_path=self.settings.publishing.executable_path or None,
                )
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    page.goto("https://www.zhihu.com/", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)

                    # 检查是否存在登录按钮（未登录时出现）
                    with self._suppress_timeout():
                        login_button = page.locator(
                            "button:has-text('登录'), "
                            "a:has-text('登录'), "
                            "button.SignContainer-accountLogin"
                        )
                        if login_button.first.count() > 0:
                            return False

                    # 检查是否有用户头像（已登录时出现）
                    with self._suppress_timeout():
                        avatar = page.locator(
                            "img.AppHeader-userAvatar, "
                            "img.UserAvatar, "
                            "button.AppHeader-profile"
                        )
                        if avatar.first.count() > 0:
                            return True

                    # 尝试访问专栏写作页面
                    page.goto(self.PUBLISH_URL, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)
                    return not self._looks_like_login_page(page)

                except Exception:
                    self.logger.debug("知乎登录状态检查异常", exc_info=True)
                    return False
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=300)

    def login_interactive(self) -> dict:
        """交互式登录知乎，打开浏览器等待用户手动完成登录。

        Returns:
            包含登录结果的字典。
        """
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="zhihu:interactive-login",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run():
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    channel=self.settings.publishing.channel or None,
                    executable_path=self.settings.publishing.executable_path or None,
                )
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded", timeout=15000)

                    print("")
                    print("请在打开的浏览器窗口中完成知乎登录。")
                    print("登录成功后此窗口将自动检测，最长等待 10 分钟。")

                    deadline = time.time() + 600
                    while time.time() < deadline:
                        page.wait_for_timeout(2000)
                        if not self._looks_like_login_page(page):
                            # 再确认一次：访问首页检查用户头像
                            page.goto("https://www.zhihu.com/", wait_until="domcontentloaded", timeout=10000)
                            page.wait_for_timeout(2000)
                            if not self._looks_like_login_page(page):
                                return {
                                    "success": True,
                                    "profile_dir": str(profile_dir),
                                    "current_url": page.url,
                                }
                        print(f"等待知乎登录完成... 当前 URL: {page.url}")

                    return {
                        "success": False,
                        "error": "知乎登录超时（10 分钟），请重试。",
                        "current_url": page.url,
                    }

                except Exception as exc:
                    return {
                        "success": False,
                        "error": str(exc),
                    }
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=600)

    # ── 内部方法 ────────────────────────────────────────────────

    def _resolve_browser_profile_dir(self) -> Path:
        configured = (self.settings.publishing.user_data_dir or "").strip()
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = self.settings.project_root / candidate
            return candidate / "zhihu_profile"
        return self.settings.account_runtime_dir / "zhihu_profile"

    def _ensure_playwright(self) -> None:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright 未安装。请运行 `pip install playwright` 和 `playwright install chromium`。"
            )

    def _looks_like_login_page(self, page) -> bool:
        body_text = self._extract_visible_text(page)
        if any(marker in body_text for marker in self._LOGIN_MARKERS):
            url_lower = page.url.lower()
            if "signin" in url_lower or "login" in url_lower:
                return True
            # 在写作页面出现登录标记也视为未登录
            if "zhuanlan.zhihu.com/write" not in url_lower:
                return True
        return False

    def _fill_title(self, page, title: str) -> None:
        clipped = self._clip_text(title, 100)
        for selector in self._TITLE_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=5000)
                locator.click()
                locator.fill(clipped)
                page.wait_for_timeout(500)
                return
        raise RuntimeError("无法定位知乎文章标题输入框。")

    def _fill_content(self, page, content: str) -> None:
        cleaned = self._clean_content(content)
        if not cleaned:
            raise RuntimeError("知乎文章正文为空。")

        for selector in self._EDITOR_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=5000)
                locator.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(cleaned, delay=6)
                page.wait_for_timeout(800)
                return
        raise RuntimeError("无法定位知乎文章正文编辑器。")

    def _upload_cover_image(self, page, image_path: str) -> bool:
        resolved = Path(image_path).resolve()
        if not resolved.exists():
            self.logger.warning("知乎封面图文件不存在: %s", resolved)
            return False

        staged = self._stage_upload_file(resolved)
        for selector in self._COVER_UPLOAD_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                try:
                    with page.expect_file_chooser(timeout=5000) as chooser_info:
                        locator.click()
                    chooser_info.value.set_files(str(staged))
                    page.wait_for_timeout(3000)
                    return True
                except Exception:
                    continue
        return False

    def _add_topics(self, page, tags: list[str]) -> None:
        for tag in tags[:5]:  # 知乎最多推荐 5 个话题
            clean_tag = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", tag).strip()
            if not clean_tag:
                continue
            for selector in self._TOPIC_INPUT_SELECTORS:
                with self._suppress_timeout():
                    locator = page.locator(selector).first
                    if locator.count() == 0:
                        continue
                    locator.wait_for(state="visible", timeout=3000)
                    locator.click()
                    locator.fill(clean_tag)
                    page.wait_for_timeout(1500)

                    # 从下拉列表中选择第一个匹配话题
                    with self._suppress_timeout():
                        suggestion = page.locator(
                            "div.TopicSelector-item, "
                            "li[role='option'], "
                            "div[role='option']"
                        ).first
                        if suggestion.count() > 0:
                            suggestion.click()
                            page.wait_for_timeout(800)
                            break
                    # 如果没有下拉建议，按回车确认
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(500)
                    break

    def _click_publish(self, page) -> None:
        for selector in self._PUBLISH_BUTTON_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector)
                count = locator.count()
                if count == 0:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        candidate.wait_for(state="visible", timeout=3000)
                        candidate.click(timeout=4000)
                        page.wait_for_timeout(800)
                        return
                    except Exception:
                        continue

        # 兜底：通过 JS 查找发布按钮
        clicked = page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const nodes = Array.from(document.querySelectorAll('button'));
              for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '');
                if (!visible(node) || text !== '发布') continue;
                node.scrollIntoView({ block: 'center' });
                node.click();
                return true;
              }
              return false;
            }
            """
        )
        if clicked:
            page.wait_for_timeout(800)
            return
        raise RuntimeError("无法定位知乎文章发布按钮。")

    def _wait_for_success(self, page) -> bool:
        deadline = time.time() + 20
        before_url = page.url
        while time.time() < deadline:
            # URL 变化且不再是写作页面
            if page.url != before_url and "/write" not in page.url:
                return True
            for selector in self._SUCCESS_INDICATORS:
                with self._suppress_timeout():
                    if page.locator(selector).first.count() > 0:
                        return True
            page.wait_for_timeout(800)
        return False

    def _extract_post_id(self, url: str) -> str:
        match = re.search(r"/p/(\d+)", url)
        return match.group(1) if match else ""

    def _clean_content(self, content: str) -> str:
        text = content.strip()
        # 移除 Markdown 标题标记
        text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
        # 清理多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _clip_text(self, value: str, max_length: int) -> str:
        normalized = self._normalize_text(value)
        if len(normalized) <= max_length:
            return normalized
        return normalized[:max_length].rstrip()

    def _normalize_text(self, value: str) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _stage_upload_file(self, source: Path) -> Path:
        staging_dir = Path(tempfile.gettempdir()) / "toutiao_content_factory_zhihu"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / ("cover" + source.suffix.lower())
        shutil.copy2(source, staged)
        return staged

    def _extract_visible_text(self, page) -> str:
        with self._suppress_timeout():
            try:
                return self._normalize_text(page.locator("body").inner_text(timeout=3000))
            except Exception:
                return ""

    class _suppress_timeout:
        """上下文管理器：抑制 Playwright 超时异常。"""

        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> bool:
            return bool(exc and isinstance(exc, PlaywrightTimeoutError))
