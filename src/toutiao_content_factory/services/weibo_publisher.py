"""微博文章发布器 — 基于 Playwright 浏览器自动化"""

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

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class WeiboPublisher(PlatformPublisher):
    """微博文章发布器，通过 Playwright 自动化浏览器完成发布流程。

    短内容（<2000 字符）使用快速发博框，长内容使用文章编辑器。
    """

    PUBLISH_URL = "https://weibo.com"
    ARTICLE_EDITOR_URL = "https://weibo.com/article"

    # 微博长文与短文分界线
    SHORT_CONTENT_THRESHOLD = 2000

    # ── CSS 选择器：快速发博 ────────────────────────────────────
    _COMPOSE_BOX_SELECTORS: list[str] = [
        "textarea[placeholder*='有什么新鲜事']",
        "textarea.W_input",
        "textarea[placeholder*='分享']",
        "div[contenteditable='true'][role='textbox']",
    ]
    _COMPOSE_PUBLISH_SELECTORS: list[str] = [
        "a:has-text('发布')",
        "button:has-text('发布')",
        "a.W_btn_a:has-text('发布')",
        "button.W_btn_a:has-text('发布')",
    ]
    _COMPOSE_IMAGE_UPLOAD_SELECTORS: list[str] = [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]

    # ── CSS 选择器：文章编辑器 ──────────────────────────────────
    _ARTICLE_TITLE_SELECTORS: list[str] = [
        "input[placeholder*='标题']",
        "input[placeholder*='请输入标题']",
        "input.Article_titleInput",
        "input.titleInput",
    ]
    _ARTICLE_EDITOR_SELECTORS: list[str] = [
        "div.ProseMirror",
        "div[contenteditable='true'][data-placeholder*='正文']",
        "div[contenteditable='true'].ql-editor",
        "div[contenteditable='true'].editor",
        "div[contenteditable='true']",
    ]
    _ARTICLE_COVER_UPLOAD_SELECTORS: list[str] = [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]
    _ARTICLE_PUBLISH_SELECTORS: list[str] = [
        "button:has-text('发布文章')",
        "button:has-text('发布')",
        "a:has-text('发布文章')",
        "button.Article_publishBtn",
    ]

    # ── 通用选择器 ──────────────────────────────────────────────
    _SUCCESS_INDICATORS: list[str] = [
        "text=发布成功",
        "text=文章发布成功",
        "text=微博发布成功",
        "text=提交成功",
    ]
    _LOGIN_MARKERS: list[str] = [
        "登录微博",
        "账号登录",
        "扫码登录",
        "密码登录",
        "手机号登录",
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
        """发布文章到微博。

        短内容（<2000 字符）使用快速发博框，长内容使用文章编辑器。

        Args:
            title: 文章标题（短内容模式下拼入正文开头）。
            content: 文章正文。
            images: 图片路径列表。
            tags: 话题标签列表（以 # 前缀格式添加）。
            category: 分类（微博暂不使用）。

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
            label="weibo:publish",
            extra_paths=[self.settings.account_runtime_dir],
        )

        is_short = len(content) < self.SHORT_CONTENT_THRESHOLD

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

                if is_short:
                    result = self._publish_short_content(page, title, content, images, tags, timeout)
                else:
                    result = self._publish_long_content(page, title, content, images, tags, timeout)

                return result

            except Exception as exc:
                self.logger.exception("微博文章发布异常")
                return PublishResult(
                    success=False,
                    platform="weibo",
                    error=str(exc),
                )
            finally:
                browser.close()

    def check_login_status(self) -> bool:
        """检查微博登录状态，返回 True 表示已登录。"""
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        if not profile_dir.exists():
            return False

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="weibo:check-login",
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
                page.goto(self.PUBLISH_URL, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)

                # 检查是否有登录按钮
                with self._suppress_timeout():
                    login_button = page.locator(
                        "a:has-text('登录'), "
                        "button:has-text('登录'), "
                        "a[node-type='loginBtn']"
                    )
                    if login_button.first.count() > 0:
                        return False

                # 检查是否有用户头像/昵称
                with self._suppress_timeout():
                    user_element = page.locator(
                        "img.W_face, "
                        "a[node-type='userNick'], "
                        "div.WB_miniblog a[nm], "
                        "img[class*='avatar']"
                    )
                    if user_element.first.count() > 0:
                        return True

                return not self._looks_like_login_page(page)

            except Exception:
                self.logger.debug("微博登录状态检查异常", exc_info=True)
                return False
            finally:
                browser.close()

    def login_interactive(self) -> dict:
        """交互式登录微博，打开浏览器等待用户手动完成登录。

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
            label="weibo:interactive-login",
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
                page.goto(
                    "https://weibo.com/login.php",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )

                print("")
                print("请在打开的浏览器窗口中完成微博登录。")
                print("登录成功后此窗口将自动检测，最长等待 10 分钟。")

                deadline = time.time() + 600
                while time.time() < deadline:
                    page.wait_for_timeout(2000)
                    if not self._looks_like_login_page(page):
                        page.goto(self.PUBLISH_URL, wait_until="domcontentloaded", timeout=10000)
                        page.wait_for_timeout(2000)
                        if not self._looks_like_login_page(page):
                            return {
                                "success": True,
                                "profile_dir": str(profile_dir),
                                "current_url": page.url,
                            }
                    print(f"等待微博登录完成... 当前 URL: {page.url}")

                return {
                    "success": False,
                    "error": "微博登录超时（10 分钟），请重试。",
                    "current_url": page.url,
                }

            except Exception as exc:
                return {
                    "success": False,
                    "error": str(exc),
                }
            finally:
                browser.close()

    # ── 短内容发布 ──────────────────────────────────────────────

    def _publish_short_content(
        self,
        page,
        title: str,
        content: str,
        images: list[str],
        tags: list[str] | None,
        timeout: int,
    ) -> PublishResult:
        """使用快速发博框发布短内容。"""
        page.goto(self.PUBLISH_URL, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2000)

        if self._looks_like_login_page(page):
            return PublishResult(
                success=False,
                platform="weibo",
                error="微博未登录，请先运行交互式登录完成认证。",
            )

        # 拼接标题和话题标签
        formatted_tags = self._format_weibo_tags(tags or [])
        full_text = f"【{title}】{content}"
        if formatted_tags:
            full_text = f"{full_text}\n{formatted_tags}"

        # 微博字数限制
        full_text = self._clip_text(full_text, 2000)

        # 填写发博框
        self._fill_compose_box(page, full_text)

        # 上传图片
        if images:
            self._upload_compose_images(page, images)

        # 点击发布
        self._click_compose_publish(page)

        # 等待成功
        if self._wait_for_success(page):
            self.logger.info("微博短内容发布成功")
            return PublishResult(
                success=True,
                platform="weibo",
                permalink=page.url,
            )

        return PublishResult(
            success=False,
            platform="weibo",
            error="微博短内容发布未检测到成功标识，请手动确认。",
        )

    # ── 长内容发布 ──────────────────────────────────────────────

    def _publish_long_content(
        self,
        page,
        title: str,
        content: str,
        images: list[str],
        tags: list[str] | None,
        timeout: int,
    ) -> PublishResult:
        """使用文章编辑器发布长内容。"""
        page.goto(self.ARTICLE_EDITOR_URL, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2000)

        if self._looks_like_login_page(page):
            return PublishResult(
                success=False,
                platform="weibo",
                error="微博未登录，请先运行交互式登录完成认证。",
            )

        # 填写标题
        self._fill_article_title(page, title)

        # 填写正文（附加话题标签）
        formatted_tags = self._format_weibo_tags(tags or [])
        article_body = content
        if formatted_tags:
            article_body = f"{article_body}\n\n{formatted_tags}"

        self._fill_article_content(page, article_body)

        # 上传封面图
        cover_image = images[0] if images else None
        if cover_image:
            uploaded = self._upload_article_cover(page, cover_image)
            if uploaded:
                self.logger.info("微博文章封面图上传成功")
            else:
                self.logger.warning("微博文章封面图上传失败，继续发布流程")

        # 点击发布
        self._click_article_publish(page)

        # 等待成功
        if self._wait_for_success(page):
            permalink = page.url
            post_id = self._extract_article_id(permalink)
            self.logger.info("微博长文章发布成功: %s", permalink)
            return PublishResult(
                success=True,
                post_id=post_id,
                permalink=permalink,
                platform="weibo",
            )

        return PublishResult(
            success=False,
            platform="weibo",
            error="微博长文章发布未检测到成功标识，请手动确认。",
        )

    # ── 内部方法 ────────────────────────────────────────────────

    def _resolve_browser_profile_dir(self) -> Path:
        configured = (self.settings.publishing.user_data_dir or "").strip()
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = self.settings.project_root / candidate
            return candidate / "weibo_profile"
        return self.settings.account_runtime_dir / "weibo_profile"

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
            if "article" not in url_lower and "weibo.com" in url_lower:
                # 首页出现登录标记，可能是弹窗登录
                return True
        return False

    def _format_weibo_tags(self, tags: list[str]) -> str:
        """将标签格式化为微博话题格式 #话题#。"""
        formatted: list[str] = []
        for tag in tags[:5]:  # 微博建议不超过 5 个话题
            clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", tag).strip()
            if clean:
                formatted.append(f"#{clean}#")
        return " ".join(formatted)

    def _fill_compose_box(self, page, text: str) -> None:
        for selector in self._COMPOSE_BOX_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=5000)
                locator.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(text, delay=6)
                page.wait_for_timeout(500)
                return
        raise RuntimeError("无法定位微博快速发博框。")

    def _upload_compose_images(self, page, images: list[str]) -> None:
        for image_path in images[:9]:  # 微博最多 9 张图
            resolved = Path(image_path).resolve()
            if not resolved.exists():
                self.logger.warning("微博图片文件不存在: %s", resolved)
                continue

            staged = self._stage_upload_file(resolved)
            for selector in self._COMPOSE_IMAGE_UPLOAD_SELECTORS:
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

    def _click_compose_publish(self, page) -> None:
        for selector in self._COMPOSE_PUBLISH_SELECTORS:
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

        # 兜底 JS 查找
        clicked = page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const nodes = Array.from(document.querySelectorAll('a, button'));
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
        raise RuntimeError("无法定位微博快速发博发布按钮。")

    def _fill_article_title(self, page, title: str) -> None:
        clipped = self._clip_text(title, 50)
        for selector in self._ARTICLE_TITLE_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=5000)
                locator.click()
                locator.fill(clipped)
                page.wait_for_timeout(500)
                return
        raise RuntimeError("无法定位微博文章标题输入框。")

    def _fill_article_content(self, page, content: str) -> None:
        cleaned = self._clean_content(content)
        if not cleaned:
            raise RuntimeError("微博文章正文为空。")

        for selector in self._ARTICLE_EDITOR_SELECTORS:
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
        raise RuntimeError("无法定位微博文章正文编辑器。")

    def _upload_article_cover(self, page, image_path: str) -> bool:
        resolved = Path(image_path).resolve()
        if not resolved.exists():
            self.logger.warning("微博封面图文件不存在: %s", resolved)
            return False

        staged = self._stage_upload_file(resolved)
        for selector in self._ARTICLE_COVER_UPLOAD_SELECTORS:
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

    def _click_article_publish(self, page) -> None:
        for selector in self._ARTICLE_PUBLISH_SELECTORS:
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

        # 兜底 JS 查找
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
                if (text === '发布文章' || text === '发布') {
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
        raise RuntimeError("无法定位微博文章发布按钮。")

    def _wait_for_success(self, page) -> bool:
        deadline = time.time() + 20
        before_url = page.url
        while time.time() < deadline:
            # URL 变化且不再是编辑页面
            if page.url != before_url and "/article" not in page.url and "/write" not in page.url:
                return True
            for selector in self._SUCCESS_INDICATORS:
                with self._suppress_timeout():
                    if page.locator(selector).first.count() > 0:
                        return True
            page.wait_for_timeout(800)
        return False

    def _extract_article_id(self, url: str) -> str:
        match = re.search(r"/article/(\d+)", url)
        return match.group(1) if match else ""

    def _clean_content(self, content: str) -> str:
        text = content.strip()
        text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
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
        staging_dir = Path(tempfile.gettempdir()) / "toutiao_content_factory_weibo"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / ("upload" + source.suffix.lower())
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
