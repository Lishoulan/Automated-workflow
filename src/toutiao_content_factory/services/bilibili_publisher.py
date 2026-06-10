"""Bilibili (B站) 专栏文章发布器 — 基于 Playwright 浏览器自动化"""
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
from toutiao_content_factory.services.multi_platform_publisher import PlatformPublisher, PublishResult

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PlaywrightTimeoutError = Exception
    sync_playwright = None


# ============================================================================
# Bilibili 创作中心选择器（内联定义，尚未纳入 selectors.py）
# ============================================================================

_BILIBILI_SELECTORS: dict[str, dict[str, list[str]]] = {
    "editor_page": {
        "title_input": [
            "textarea[placeholder*='请输入标题']",
            "input[placeholder*='请输入标题']",
            "textarea.title-input",
            ".article-title textarea",
            ".title-input textarea",
            "textarea",
        ],
        "content_editor": [
            "div.ProseMirror[contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
            "div.ql-editor[contenteditable='true']",
            "div.public-DraftEditor-content[contenteditable='true']",
            "div[contenteditable='true']",
        ],
        "cover_upload_button": [
            "div.cover-uploader",
            "div.cover-upload",
            "span:has-text('上传封面')",
            "button:has-text('上传封面')",
            ".cover-wrapper .upload-btn",
        ],
        "cover_file_input": [
            "input[type='file'][accept*='image']",
            "input[type='file']",
        ],
        "category_selector": [
            "div.category-select",
            "select.category-select",
            "div:has-text('选择分类')",
            ".select-wrap.category",
        ],
        "tag_input": [
            "input[placeholder*='标签']",
            "input[placeholder*='请输入标签']",
            "input.tag-input",
            ".tag-wrap input",
        ],
        "tag_confirm_button": [
            "button:has-text('确定')",
            "span:has-text('确定')",
        ],
        "publish_button": [
            "button.publish-btn",
            "button:has-text('发布')",
            "button:has-text('提交')",
            ".submit-btn",
        ],
        "success_indicator": [
            "text=发布成功",
            "text=提交成功",
            "text=文章已发布",
            "text=审核中",
        ],
    },
}

_BILIBILI_LOGIN_MARKERS = (
    "扫码登录",
    "账号登录",
    "登录bilibili",
    "密码登录",
    "短信登录",
    "注册",
    "请登录",
)

_BILIBILI_PUBLISH_FAILURE_HINTS = (
    "请填写标题",
    "请填写正文",
    "请上传封面",
    "请选择分类",
    "标题不能为空",
    "内容不能为空",
    "发布失败",
    "参数错误",
)


class BilibiliPublisher(PlatformPublisher):
    """Bilibili 专栏文章发布器，基于 Playwright 浏览器自动化。

    继承自 PlatformPublisher 抽象基类，实现 publish_article / check_login_status /
    login_interactive 三个核心方法。
    """

    PUBLISH_URL = "https://member.bilibili.com/article-text/home"

    # Bilibili 专栏标题最大长度
    TITLE_MAX_LENGTH = 80

    # Bilibili 专栏正文最大长度
    CONTENT_MAX_LENGTH = 25000

    # 默认分类
    DEFAULT_CATEGORY = "科技"

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._selectors = _BILIBILI_SELECTORS["editor_page"]

    # ------------------------------------------------------------------
    # PlatformPublisher 抽象方法实现
    # ------------------------------------------------------------------

    def publish_article(
        self,
        title: str,
        content: str,
        images: list[str],
        *,
        tags: list[str] | None = None,
        category: str | None = None,
    ) -> PublishResult:
        """发布文章到 Bilibili 专栏。

        完整流程：
        1. 启动 Playwright 浏览器（使用持久化 profile）
        2. 导航到 Bilibili 创作中心专栏编辑页
        3. 填写标题
        4. 填写正文（富文本编辑器）
        5. 上传封面图（如有）
        6. 设置分类（默认"科技"）
        7. 添加标签
        8. 点击发布按钮
        9. 等待成功指示
        10. 返回 PublishResult
        """
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="bilibili:publish",
            extra_paths=[self.settings.account_runtime_dir],
        )

        clipped_title = self._clip_title(title)
        adapted_content = self.adapt_content(content, "bilibili")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self._get_headless(),
                channel=self._get_channel(),
                executable_path=self._get_executable_path(),
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    self.PUBLISH_URL,
                    wait_until="domcontentloaded",
                    timeout=max(self._get_timeout_ms(), 20000),
                )
                page.wait_for_timeout(2000)

                # 检查登录态
                self._ensure_logged_in(page)

                # 填写标题
                self._fill_title(page, clipped_title)
                self.logger.info("Bilibili: 标题已填写 — %s", clipped_title)

                # 填写正文
                self._fill_content(page, adapted_content)
                self.logger.info("Bilibili: 正文已填写（%d 字）", len(adapted_content))

                # 上传封面图
                cover_image = self._resolve_cover_image(images)
                if cover_image is not None:
                    self._upload_cover(page, cover_image)
                    self.logger.info("Bilibili: 封面图已上传 — %s", cover_image.name)
                else:
                    self.logger.info("Bilibili: 无封面图，跳过上传")

                # 设置分类
                target_category = category or self.DEFAULT_CATEGORY
                self._set_category(page, target_category)
                self.logger.info("Bilibili: 分类已设置 — %s", target_category)

                # 添加标签
                if tags:
                    self._add_tags(page, tags)
                    self.logger.info("Bilibili: 标签已添加 — %s", ", ".join(tags))

                # 点击发布
                before_url = page.url
                self._click_publish(page)
                self.logger.info("Bilibili: 已点击发布按钮")

                # 等待成功指示
                success = self._wait_for_success(page, before_url)
                if not success:
                    failure_msg = self._build_failure_message(page)
                    return PublishResult(
                        success=False,
                        platform="bilibili",
                        error=failure_msg,
                    )

                result = PublishResult(
                    success=True,
                    platform="bilibili",
                    permalink=page.url,
                )
                self.logger.info("Bilibili: 文章发布成功 — %s", page.url)
                return result

            except Exception as exc:
                self._capture_debug_artifacts(page, {"title": clipped_title, "error": str(exc)})
                return PublishResult(
                    success=False,
                    platform="bilibili",
                    error=str(exc),
                )
            finally:
                browser.close()

    def check_login_status(self) -> bool:
        """导航到 bilibili.com 并检查是否已登录。"""
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                channel=self._get_channel(),
                executable_path=self._get_executable_path(),
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    "https://www.bilibili.com/",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                page.wait_for_timeout(2000)

                # 检查页面中是否有登录态标识
                is_logged_in = page.evaluate(
                    """
                    () => {
                        // 方式1：检查顶部导航栏用户头像
                        const avatar = document.querySelector('.header-avatar-wrap, .bili-avatar, .user-con .avatar, .nav-user-info');
                        if (avatar) return true;

                        // 方式2：检查 cookie 中的 DedeUserID
                        const cookies = document.cookie;
                        if (cookies.includes('DedeUserID=') && !cookies.includes('DedeUserID=deleted')) {
                            return true;
                        }

                        // 方式3：检查页面中是否有登录按钮（有则说明未登录）
                        const loginBtn = document.querySelector('.header-login-entry, .btn-login, a:has-text("登录")');
                        if (loginBtn) return false;

                        return false;
                    }
                    """
                )
                self.logger.info("Bilibili: 登录状态检查结果 — %s", "已登录" if is_logged_in else "未登录")
                return bool(is_logged_in)

            except Exception as exc:
                self.logger.warning("Bilibili: 登录状态检查异常 — %s", exc)
                return False
            finally:
                browser.close()

    def login_interactive(self) -> dict:
        """打开 Bilibili 登录页面，等待用户扫码登录。

        Returns:
            包含登录结果的字典，如 {"success": True, "profile_dir": "..."} 或
            {"success": False, "error": "..."}。
        """
        self._ensure_playwright()

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="bilibili:init-session",
            extra_paths=[self.settings.account_runtime_dir],
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel=self._get_channel(),
                executable_path=self._get_executable_path(),
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    "https://passport.bilibili.com/login",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )

                print("")
                print("请在打开的浏览器窗口中完成 Bilibili 登录（扫码或账号密码）。")
                print("系统将自动检测登录状态，最长等待 10 分钟。")
                print("")

                deadline = time.time() + 600
                while time.time() < deadline:
                    page.wait_for_timeout(3000)

                    # 登录成功后通常会跳转到首页或创作中心
                    current_url = page.url.lower()
                    if "passport.bilibili.com/login" not in current_url:
                        self.logger.info("Bilibili: 登录成功，当前页面 — %s", page.url)
                        return {
                            "success": True,
                            "profile_dir": str(profile_dir),
                            "current_url": page.url,
                        }

                    # 尝试通过 cookie 判断
                    has_user_cookie = page.evaluate(
                        """
                        () => {
                            const cookies = document.cookie;
                            return cookies.includes('DedeUserID=') && !cookies.includes('DedeUserID=deleted');
                        }
                        """
                    )
                    if has_user_cookie:
                        self.logger.info("Bilibili: 检测到登录 Cookie，登录成功")
                        return {
                            "success": True,
                            "profile_dir": str(profile_dir),
                            "current_url": page.url,
                        }

                    if page.is_closed():
                        return {
                            "success": False,
                            "error": "浏览器窗口已关闭",
                        }

                return {
                    "success": False,
                    "error": "登录超时（10 分钟），请重试",
                }

            except Exception as exc:
                self.logger.exception("Bilibili: 交互式登录异常")
                return {
                    "success": False,
                    "error": str(exc),
                }
            finally:
                browser.close()

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _ensure_playwright(self) -> None:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright 未安装。请运行 `pip install playwright` 和 `playwright install chromium`。"
            )

    def _ensure_logged_in(self, page) -> None:
        """检查当前页面是否处于登录态，未登录则抛出异常。"""
        body_text = self._extract_visible_text(page)
        if any(marker in body_text for marker in _BILIBILI_LOGIN_MARKERS):
            # 进一步检查 URL
            if "passport.bilibili.com" in page.url:
                raise RuntimeError(
                    "Bilibili 登录已过期。请先运行交互式登录完成扫码，然后重试发布。"
                )
        # 通过 cookie 二次确认
        has_user_cookie = page.evaluate(
            """
            () => {
                const cookies = document.cookie;
                return cookies.includes('DedeUserID=') && !cookies.includes('DedeUserID=deleted');
            }
            """
        )
        if not has_user_cookie:
            # 检查是否在创作中心（有时 cookie 不可直接读取但页面已登录）
            if "member.bilibili.com" in page.url:
                # 在创作中心页面，检查是否有用户头像
                has_avatar = page.evaluate(
                    """
                    () => {
                        const avatar = document.querySelector('.user-con .avatar, .header-avatar-wrap, .bili-avatar');
                        return Boolean(avatar);
                    }
                    """
                )
                if has_avatar:
                    return
            raise RuntimeError(
                "Bilibili 登录态已失效。请先运行交互式登录完成扫码，然后重试发布。"
            )

    def _fill_title(self, page, title: str) -> None:
        """填写文章标题。"""
        selectors = self._selectors.get("title_input", [])
        for selector in selectors:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=5000)
                locator.click()
                locator.fill(title)
                page.wait_for_timeout(500)
                # 验证标题是否填入
                value = locator.input_value() if locator.evaluate("el => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'") else ""
                if value == title:
                    return
                # 如果 fill 不生效，尝试键盘输入
                locator.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(title, delay=15)
                page.wait_for_timeout(500)
                return
        raise RuntimeError("无法定位 Bilibili 文章标题输入框。")

    def _fill_content(self, page, content: str) -> None:
        """填写文章正文（富文本编辑器）。"""
        selectors = self._selectors.get("content_editor", [])
        for selector in selectors:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=5000)
                locator.click()
                page.wait_for_timeout(300)

                # 清空已有内容
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(200)

                # 逐段输入正文
                page.keyboard.type(content, delay=5)
                page.wait_for_timeout(800)
                return
        raise RuntimeError("无法定位 Bilibili 文章正文编辑器。")

    def _upload_cover(self, page, image_path: Path) -> None:
        """上传封面图。"""
        resolved = image_path.resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Bilibili 封面图不存在: {resolved}")

        staged = self._stage_upload_file(resolved)

        # 先尝试点击上传按钮触发文件选择器
        cover_button_selectors = self._selectors.get("cover_upload_button", [])
        file_input_selectors = self._selectors.get("cover_file_input", [])

        # 方式1：通过 file_chooser 对话框上传
        for selector in cover_button_selectors:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=4000)
                try:
                    with page.expect_file_chooser(timeout=5000) as chooser_info:
                        locator.click()
                    chooser_info.value.set_files(str(staged))
                    page.wait_for_timeout(3000)
                    self._confirm_pending_dialog(page)
                    return
                except Exception:
                    continue

        # 方式2：直接设置 file input
        for selector in file_input_selectors:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.set_input_files(str(staged))
                page.wait_for_timeout(3000)
                return

        self.logger.warning("Bilibili: 无法上传封面图，所有选择器均未匹配")

    def _set_category(self, page, category: str) -> None:
        """设置文章分类。"""
        selectors = self._selectors.get("category_selector", [])
        for selector in selectors:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=4000)
                locator.click()
                page.wait_for_timeout(500)

                # 在弹出的分类列表中查找目标分类
                clicked = page.evaluate(
                    """
                    (targetCategory) => {
                        const visible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0
                                && style.visibility !== 'hidden' && style.display !== 'none';
                        };
                        const nodes = Array.from(document.querySelectorAll(
                            'li, div, span, option, a'
                        ));
                        for (const node of nodes) {
                            const text = (node.innerText || node.textContent || '').trim();
                            if (!visible(node) || text !== targetCategory) continue;
                            node.click();
                            return true;
                        }
                        return false;
                    }
                    """,
                    category,
                )
                if clicked:
                    page.wait_for_timeout(500)
                    return

        self.logger.warning("Bilibili: 无法设置分类 '%s'，可能需要手动设置", category)

    def _add_tags(self, page, tags: list[str]) -> None:
        """添加文章标签。"""
        tag_input_selectors = self._selectors.get("tag_input", [])
        tag_confirm_selectors = self._selectors.get("tag_confirm_button", [])

        for tag in tags[:5]:  # Bilibili 最多约 10 个标签，限制为 5 个
            tag_entered = False
            for selector in tag_input_selectors:
                with self._suppress_timeout():
                    locator = page.locator(selector).first
                    if locator.count() == 0:
                        continue
                    locator.wait_for(state="visible", timeout=3000)
                    locator.click()
                    locator.fill(tag)
                    page.wait_for_timeout(300)

                    # 尝试按回车确认
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(500)

                    # 尝试点击确认按钮
                    for confirm_selector in tag_confirm_selectors:
                        with self._suppress_timeout():
                            confirm_locator = page.locator(confirm_selector).first
                            if confirm_locator.count() > 0:
                                confirm_locator.click()
                                page.wait_for_timeout(300)
                                break

                    tag_entered = True
                    break

            if not tag_entered:
                self.logger.warning("Bilibili: 无法添加标签 '%s'", tag)

    def _click_publish(self, page) -> None:
        """点击发布按钮。"""
        selectors = self._selectors.get("publish_button", [])
        for selector in selectors:
            with self._suppress_timeout():
                locator = page.locator(selector)
                count = locator.count()
                if count == 0:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        candidate.wait_for(state="visible", timeout=3000)
                    except Exception:
                        continue
                    button_text = (candidate.inner_text() or "").strip()
                    if "发布" not in button_text:
                        continue
                    try:
                        candidate.click(timeout=4000)
                    except Exception:
                        # 尝试滚动到元素后点击
                        page.evaluate(
                            """
                            (element) => {
                                element.scrollIntoView({ block: 'center' });
                                element.click();
                            }
                            """,
                            candidate.element_handle(),
                        )
                    page.wait_for_timeout(800)
                    return

        # 降级：通过 JS 查找发布按钮
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
                    const text = (node.innerText || node.textContent || '').trim();
                    if (!visible(node)) continue;
                    if (text === '发布' || text === '提交') {
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

        raise RuntimeError("无法定位 Bilibili 发布按钮。")

    def _wait_for_success(self, page, before_url: str) -> bool:
        """等待发布成功指示。"""
        success_selectors = self._selectors.get("success_indicator", [])
        deadline = time.time() + 25
        while time.time() < deadline:
            # URL 变化通常表示发布成功跳转
            if page.url != before_url and "article-text/home" not in page.url:
                return True

            for selector in success_selectors:
                with self._suppress_timeout():
                    if page.locator(selector).first.count() > 0:
                        return True

            page.wait_for_timeout(800)
        return False

    def _build_failure_message(self, page) -> str:
        """构建发布失败消息。"""
        visible_text = self._extract_visible_text(page)
        hints: list[str] = []
        for phrase in _BILIBILI_PUBLISH_FAILURE_HINTS:
            if phrase in visible_text:
                hints.append(phrase)
        if hints:
            return "Bilibili 文章发布未完成: " + " | ".join(hints)
        return "Bilibili 文章发布未到达成功页面或成功提示。"

    def _resolve_browser_profile_dir(self) -> Path:
        """解析浏览器 profile 目录。"""
        # 优先从 settings 中读取 bilibili 相关配置（如果存在）
        configured = ""
        if hasattr(self.settings, "bilibili_profile_dir"):
            configured = str(getattr(self.settings, "bilibili_profile_dir", "")).strip()
        if not configured:
            configured = str(
                self.settings.publishing.user_data_dir
                if hasattr(self.settings, "publishing")
                else ".runtime/bilibili_profile"
            ).strip()
            # 如果使用的是 toutiao 的 profile，替换为 bilibili 专用
            configured = configured.replace("toutiao_profile", "bilibili_profile")

        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = self.settings.project_root / candidate
        return candidate

    def _get_headless(self) -> bool:
        """获取 headless 配置。"""
        if hasattr(self.settings, "bilibili_headless"):
            return bool(getattr(self.settings, "bilibili_headless", False))
        return self.settings.publishing.headless

    def _get_channel(self) -> str | None:
        """获取浏览器 channel 配置。"""
        if hasattr(self.settings, "bilibili_channel"):
            channel = str(getattr(self.settings, "bilibili_channel", "")).strip()
            return channel or None
        return self.settings.publishing.channel or None

    def _get_executable_path(self) -> str | None:
        """获取浏览器可执行文件路径。"""
        if hasattr(self.settings, "bilibili_executable_path"):
            path = str(getattr(self.settings, "bilibili_executable_path", "")).strip()
            return path or None
        return self.settings.publishing.executable_path or None

    def _get_timeout_ms(self) -> int:
        """获取超时配置。"""
        if hasattr(self.settings, "bilibili_timeout_ms"):
            return int(getattr(self.settings, "bilibili_timeout_ms", 20000))
        return self.settings.publishing.wait_timeout_ms

    def _clip_title(self, title: str) -> str:
        """截断标题到最大长度。"""
        normalized = self._normalize_text(title)
        if len(normalized) <= self.TITLE_MAX_LENGTH:
            return normalized
        return normalized[: self.TITLE_MAX_LENGTH].rstrip()

    def _resolve_cover_image(self, images: list[str]) -> Path | None:
        """从图片列表中找到第一张可用的封面图。"""
        for image_path_str in images:
            if not image_path_str:
                continue
            candidate = Path(image_path_str)
            if not candidate.is_absolute():
                candidate = self.settings.project_root / candidate
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _stage_upload_file(self, source: Path) -> Path:
        """将上传文件暂存到临时目录（避免路径含中文等问题）。"""
        staging_dir = Path(tempfile.gettempdir()) / "toutiao_content_factory_bilibili"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / ("cover" + source.suffix.lower())
        shutil.copy2(source, staged)
        return staged

    def _confirm_pending_dialog(self, page) -> None:
        """确认可能弹出的对话框（如裁剪确认等）。"""
        for label in ("确定", "完成", "确认", "保存", "裁剪"):
            clicked = page.evaluate(
                """
                (targetText) => {
                    const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0
                            && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const nodes = Array.from(document.querySelectorAll('button, div, span'));
                    for (const node of nodes) {
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '');
                        if (!visible(node) || text !== targetText) continue;
                        node.click();
                        return true;
                    }
                    return false;
                }
                """,
                label,
            )
            if clicked:
                page.wait_for_timeout(1500)
                break

    def _extract_visible_text(self, page) -> str:
        """提取页面可见文本。"""
        with self._suppress_timeout():
            try:
                return self._normalize_text(page.locator("body").inner_text(timeout=3000))
            except Exception:
                return ""

    def _capture_debug_artifacts(self, page, extra_payload: dict[str, Any] | None = None) -> Path:
        """捕获调试截图和 DOM 快照。"""
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        debug_dir = self.settings.output_dir / "bilibili_publish_debug" / timestamp
        debug_dir.mkdir(parents=True, exist_ok=True)

        html_path = debug_dir / "bilibili_page.html"
        png_path = debug_dir / "bilibili_page.png"
        url_path = debug_dir / "current_url.txt"
        context_path = debug_dir / "publish_context.json"

        with self._suppress_timeout():
            html_path.write_text(page.content(), encoding="utf-8")
        with self._suppress_timeout():
            page.screenshot(path=str(png_path), full_page=True)
        url_path.write_text(page.url, encoding="utf-8")
        context_path.write_text(
            json.dumps(extra_payload or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.warning("Bilibili 调试文件已保存到: %s", debug_dir)
        return debug_dir

    @staticmethod
    def _normalize_text(value: str) -> str:
        """规范化文本。"""
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    class _suppress_timeout:
        """上下文管理器：抑制 Playwright 超时异常。"""

        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> bool:
            return bool(exc and isinstance(exc, PlaywrightTimeoutError))
