"""小红书笔记发布器 — 基于 Playwright 浏览器自动化"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
from pathlib import Path

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.multi_platform_publisher import (
    PlatformPublisher,
    PublishResult,
)

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class XiaohongshuPublisher(PlatformPublisher):
    """小红书笔记发布器，通过 Playwright 自动化浏览器完成发布流程。

    小红书以图文笔记为主，正文限1000字，最多9张图，标签用 #话题 格式。
    """

    CREATOR_URL = "https://creator.xiaohongshu.com/publish/publish"
    HOME_URL = "https://www.xiaohongshu.com"

    # ── CSS 选择器 ──────────────────────────────────────────────
    _TITLE_SELECTORS: list[str] = [
        "input[placeholder*='标题']",
        "input[placeholder*='填写标题']",
        "input.title-input",
        "input.d-title",
    ]
    _EDITOR_SELECTORS: list[str] = [
        "div[contenteditable='true'][data-placeholder*='正文']",
        "div[contenteditable='true'].ql-editor",
        "div[contenteditable='true'].editor",
        "div[contenteditable='true']",
    ]
    _IMAGE_UPLOAD_SELECTORS: list[str] = [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]
    _TOPIC_INPUT_SELECTORS: list[str] = [
        "input[placeholder*='搜索话题']",
        "input[placeholder*='添加话题']",
        "input[placeholder*='话题']",
    ]
    _PUBLISH_BUTTON_SELECTORS: list[str] = [
        "button:has-text('发布')",
        "button:has-text('发布笔记')",
        "button.publishBtn",
        "button:has-text('提交')",
    ]
    _SUCCESS_INDICATORS: list[str] = [
        "text=发布成功",
        "text=笔记发布成功",
        "text=提交成功",
    ]
    _LOGIN_MARKERS: list[str] = [
        "登录",
        "扫码登录",
        "手机号登录",
        "密码登录",
        "注册",
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
        """发布笔记到小红书。"""
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="xiaohongshu:publish",
            extra_paths=[self.settings.account_runtime_dir],
        )

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

                page.goto(self.CREATOR_URL, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(2000)

                if self._looks_like_login_page(page):
                    return PublishResult(
                        success=False,
                        platform="xiaohongshu",
                        error="小红书未登录，请先运行交互式登录完成认证。",
                    )

                # 上传图片（小红书必须先上传图片）
                if images:
                    self._upload_images(page, images[:9], timeout)

                # 填写标题
                self._fill_title(page, title, timeout)

                # 填写正文 + 标签
                formatted_tags = self._format_xhs_tags(tags or [])
                body = self._clip_text(content, 1000)
                if formatted_tags:
                    body = f"{body}\n\n{formatted_tags}"
                self._fill_content(page, body, timeout)

                # 点击发布
                self._click_publish(page)

                # 等待成功
                if self._wait_for_success(page):
                    self.logger.info("小红书笔记发布成功")
                    return PublishResult(
                        success=True,
                        platform="xiaohongshu",
                        permalink=page.url,
                    )

                return PublishResult(
                    success=False,
                    platform="xiaohongshu",
                    error="小红书笔记发布未检测到成功标识，请手动确认。",
                )

            except Exception as exc:
                self.logger.exception("小红书笔记发布异常")
                return PublishResult(
                    success=False,
                    platform="xiaohongshu",
                    error=str(exc),
                )
            finally:
                browser.close()

    def check_login_status(self) -> bool:
        """检查小红书登录状态。"""
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        if not profile_dir.exists():
            return False

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="xiaohongshu:check-login",
            extra_paths=[self.settings.account_runtime_dir],
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(self.HOME_URL, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                return not self._looks_like_login_page(page)
            except Exception:
                self.logger.debug("小红书登录状态检查异常", exc_info=True)
                return False
            finally:
                browser.close()

    def login_interactive(self) -> dict:
        """交互式登录小红书，打开浏览器等待用户手动完成登录。"""
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="xiaohongshu:interactive-login",
            extra_paths=[self.settings.account_runtime_dir],
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(self.HOME_URL, wait_until="domcontentloaded", timeout=15000)

                print("")
                print("请在打开的浏览器窗口中完成小红书登录。")
                print("登录成功后此窗口将自动检测，最长等待 10 分钟。")

                deadline = time.time() + 600
                while time.time() < deadline:
                    page.wait_for_timeout(2000)
                    if not self._looks_like_login_page(page):
                        # 二次确认：访问创作者页面
                        page.goto(self.CREATOR_URL, wait_until="domcontentloaded", timeout=10000)
                        page.wait_for_timeout(2000)
                        if not self._looks_like_login_page(page):
                            return {
                                "success": True,
                                "profile_dir": str(profile_dir),
                                "current_url": page.url,
                            }
                    print(f"等待小红书登录完成... 当前 URL: {page.url}")

                return {
                    "success": False,
                    "error": "小红书登录超时（10 分钟），请重试。",
                    "current_url": page.url,
                }

            except Exception as exc:
                return {
                    "success": False,
                    "error": str(exc),
                }
            finally:
                browser.close()

    # ── 内部方法 ────────────────────────────────────────────────

    def _resolve_browser_profile_dir(self) -> Path:
        configured = (self.settings.publishing.user_data_dir or "").strip()
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = self.settings.project_root / candidate
            return candidate / "xiaohongshu_profile"
        return self.settings.account_runtime_dir / "xiaohongshu_profile"

    def _ensure_playwright(self) -> None:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright 未安装。请运行 `pip install playwright` 和 `playwright install chromium`。"
            )

    def _looks_like_login_page(self, page) -> bool:
        body_text = self._extract_visible_text(page)
        url_lower = page.url.lower()
        if any(marker in body_text for marker in self._LOGIN_MARKERS):
            if "login" in url_lower or "signin" in url_lower:
                return True
            # 首页出现登录弹窗
            if "xiaohongshu.com" in url_lower and "creator" not in url_lower:
                return True
        return False

    def _format_xhs_tags(self, tags: list[str]) -> str:
        """将标签格式化为小红书话题格式 #话题 。"""
        formatted: list[str] = []
        for tag in tags[:8]:
            clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", tag).strip()
            if clean:
                formatted.append(f"#{clean}")
        return " ".join(formatted)

    def _fill_title(self, page, title: str, timeout: int) -> None:
        clipped = self._clip_text(title, 20)  # 小红书标题限20字
        for selector in self._TITLE_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=timeout)
                locator.click()
                locator.fill(clipped)
                page.wait_for_timeout(500)
                return
        raise RuntimeError("无法定位小红书标题输入框。")

    def _fill_content(self, page, content: str, timeout: int) -> None:
        for selector in self._EDITOR_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=timeout)
                locator.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(content, delay=6)
                page.wait_for_timeout(500)
                return
        raise RuntimeError("无法定位小红书正文编辑器。")

    def _upload_images(self, page, images: list[str], timeout: int) -> None:
        for image_path in images:
            resolved = Path(image_path).resolve()
            if not resolved.exists():
                self.logger.warning("小红书图片文件不存在: %s", resolved)
                continue

            staged = self._stage_upload_file(resolved)
            for selector in self._IMAGE_UPLOAD_SELECTORS:
                with self._suppress_timeout():
                    locator = page.locator(selector).first
                    if locator.count() == 0:
                        continue
                    try:
                        with page.expect_file_chooser(timeout=5000) as chooser_info:
                            locator.click()
                        chooser_info.value.set_files(str(staged))
                        page.wait_for_timeout(2000)
                        break
                    except Exception:
                        continue

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

        # 兜底 JS
        clicked = page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const nodes = Array.from(document.querySelectorAll('button, a'));
              for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '');
                if (!visible(node)) continue;
                if (text === '发布' || text === '发布笔记' || text === '提交') {
                  node.scrollIntoView({ block: 'center' });
                  node.click();
                  return true;
                }
              }
              return false;
            }
            """
        )
        if clicked:
            page.wait_for_timeout(800)
            return
        raise RuntimeError("无法定位小红书发布按钮。")

    def _wait_for_success(self, page) -> bool:
        deadline = time.time() + 20
        before_url = page.url
        while time.time() < deadline:
            if page.url != before_url and "publish" not in page.url:
                return True
            for selector in self._SUCCESS_INDICATORS:
                with self._suppress_timeout():
                    if page.locator(selector).first.count() > 0:
                        return True
            page.wait_for_timeout(800)
        return False

    def _clip_text(self, value: str, max_length: int) -> str:
        text = value.strip()
        if len(text) <= max_length:
            return text
        # 在句子边界截断
        truncated = text[:max_length]
        sentence_endings = re.compile(r"[。！？.!?\n]")
        matches = list(sentence_endings.finditer(truncated))
        if matches:
            truncated = truncated[:matches[-1].end()]
        return truncated.rstrip()

    def _stage_upload_file(self, source: Path) -> Path:
        staging_dir = Path(tempfile.gettempdir()) / "toutiao_content_factory_xhs"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / ("upload" + source.suffix.lower())
        shutil.copy2(source, staged)
        return staged

    def _extract_visible_text(self, page) -> str:
        with self._suppress_timeout():
            try:
                return (page.locator("body").inner_text(timeout=3000) or "").strip()
            except Exception:
                return ""

    class _suppress_timeout:
        """上下文管理器：抑制 Playwright 超时异常。"""

        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> bool:
            return bool(exc and isinstance(exc, PlaywrightTimeoutError))
