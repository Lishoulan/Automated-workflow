"""135编辑器浏览器自动化服务 — 通过135编辑器完成微信公众号文章排版与同步。

核心流程：
1. 登录135编辑器（cookie持久化，比微信后台稳定）
2. 打开编辑器页面
3. 切换到HTML模式，粘贴排版好的HTML
4. 点击「保存同步」→ 填写标题、上传封面、选择公众号
5. 等待同步完成，确认成功

优势：
- 135编辑器通过微信OAuth授权公众号，不需要直接登录微信后台
- HTML模式可以精确控制排版效果
- 「保存同步」直接将文章推到公众号草稿箱
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.constants.selectors import (
    EDITOR_135_LOGIN_PAGE,
    EDITOR_135_EDITOR_PAGE,
    EDITOR_135_SYNC_DIALOG,
    EDITOR_135_SESSION_READY_MARKERS,
    EDITOR_135_LOGIN_URL_MARKERS,
    EDITOR_135_SYNC_SUCCESS_MARKERS,
)
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class Editor135Service:
    """135编辑器浏览器自动化服务。"""

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._base_url = settings.wechat.editor_135_base_url.rstrip("/")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def publish_via_135editor(
        self,
        *,
        title: str,
        article_html: str,
        cover_path: Path | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        """通过135编辑器发布文章到微信公众号草稿箱。

        Args:
            title: 文章标题
            article_html: 排版好的HTML内容
            cover_path: 封面图路径（可选）
            summary: 文章摘要

        Returns:
            包含发布状态信息的字典
        """
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed. Run `pip install playwright` and `playwright install chromium`.")

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="wechat:editor_135",
            extra_paths=[self.settings.account_runtime_dir],
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.settings.publishing.headless,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            page = None
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()

                # Step 1: 打开135编辑器
                self._open_editor(page)

                # Step 2: 检查登录状态
                if self._is_login_required(page):
                    raise RuntimeError(
                        "135编辑器未登录或登录已过期。请先运行 --init-135editor-login 初始化登录。"
                    )

                # Step 3: 在标题输入框中填写标题
                self._fill_title(page, title)

                # Step 4: 通过UEditor API设置文章内容
                self._paste_html_content(page, article_html)

                # Step 5: 点击「保存同步」打开保存图文对话框
                self._click_save_sync(page)

                # Step 6: 在保存图文对话框中上传封面、勾选同步、保存
                sync_result = self._save_and_sync(page, cover_path=cover_path)

                result = {
                    "status": "draft_saved" if sync_result else "sync_failed",
                    "method": "editor_135",
                    "title": title,
                    "editor_url": page.url,
                }
                self.logger.info("135编辑器同步结果: %s", result["status"])
                return result

            except Exception as exc:
                self._capture_debug_artifacts(page, {"title": title, "method": "editor_135"})
                raise RuntimeError(
                    f"135编辑器发布失败: {exc}"
                ) from exc
            finally:
                browser.close()

    def initialize_login_session(self) -> dict[str, str]:
        """初始化135编辑器登录会话。打开浏览器让用户手动登录。"""
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed.")

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="wechat:editor_135:init",
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
                # 直接打开编辑器页面，这样能检测到真实的登录状态
                editor_url = f"{self._base_url}/beautify_editor.html"
                page.goto(
                    editor_url,
                    wait_until="domcontentloaded",
                    timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
                )
                page.wait_for_timeout(5000)
                print("")
                print("请在打开的浏览器窗口中登录135编辑器。")
                print("登录完成后，确保能看到编辑器页面（有编辑区域和工具栏）。")
                print("如果弹出登录对话框，请完成登录后关闭对话框。")
                deadline = time.time() + 1200
                while time.time() < deadline:
                    if not self._is_login_required(page):
                        summary = {
                            "profile_dir": str(profile_dir),
                            "current_url": page.url,
                            "state": "ready",
                        }
                        self.settings.automation_dir.mkdir(parents=True, exist_ok=True)
                        (self.settings.automation_dir / "editor_135_login_preflight.json").write_text(
                            json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        return summary
                    page.wait_for_timeout(1500)
                raise RuntimeError("135编辑器登录未在20分钟内完成。")
            finally:
                browser.close()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_profile_dir(self) -> Path:
        """获取135编辑器的浏览器配置文件目录。"""
        profile_value = self.settings.wechat.editor_135_profile_dir
        if Path(profile_value).is_absolute():
            return Path(profile_value)
        return self.settings.account_runtime_dir / profile_value

    def _open_editor(self, page) -> None:
        """打开135编辑器编辑页面。"""
        editor_url = f"{self._base_url}/beautify_editor.html"
        self.logger.info("正在打开135编辑器: %s", editor_url)
        page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

    def _is_login_required(self, page) -> bool:
        """检查是否需要登录。"""
        # 检查是否有登录对话框
        try:
            login_dialog = page.query_selector("#user-login-dialog")
            if login_dialog and login_dialog.is_visible():
                return True
        except Exception:
            pass

        # 检查是否有登录链接（未登录时显示"登录/注册"）
        try:
            login_link = page.query_selector("#login-link")
            if login_link and login_link.is_visible():
                text = login_link.inner_text().strip()
                if "登录" in text and "注册" in text:
                    return True
        except Exception:
            pass

        # 检查URL是否被重定向到登录页
        current_url = page.url.lower()
        for marker in EDITOR_135_LOGIN_URL_MARKERS:
            if marker.lower() in current_url:
                return True

        return False

    def _is_session_ready(self, page) -> bool:
        """检查135编辑器会话是否就绪。"""
        try:
            page_text = page.inner_text("body", timeout=5000)
        except Exception:
            return False

        for marker in EDITOR_135_SESSION_READY_MARKERS:
            if marker in page_text:
                return True
        return False

    def _paste_html_content(self, page, article_html: str) -> None:
        """切换到HTML模式并粘贴HTML内容。"""
        self.logger.info("正在切换到HTML模式并粘贴内容...")

        # 135编辑器使用UEditor，编辑区域在iframe #ueditor_0 中
        # 方法1: 通过UEditor API直接设置内容
        js_result = self._set_editor_content_via_js(page, article_html)

        if js_result == "failed":
            # 方法2: 尝试点击HTML模式按钮
            html_mode_clicked = False
            for selector in EDITOR_135_EDITOR_PAGE.get("html_mode_button"):
                try:
                    btn = page.wait_for_selector(selector, timeout=5000)
                    if btn and btn.is_visible():
                        btn.click()
                        html_mode_clicked = True
                        self.logger.info("已点击HTML模式按钮: %s", selector)
                        break
                except Exception:
                    continue

            if html_mode_clicked:
                page.wait_for_timeout(1000)
                for selector in EDITOR_135_EDITOR_PAGE.get("html_source_textarea"):
                    try:
                        textarea = page.wait_for_selector(selector, timeout=5000)
                        if textarea:
                            textarea.click()
                            textarea.fill("")
                            textarea.fill(article_html)
                            self.logger.info("已在HTML源码编辑器中粘贴内容 (长度: %d)", len(article_html))
                            break
                    except Exception:
                        continue

                # 切回可视化模式
                page.wait_for_timeout(500)
                for selector in EDITOR_135_EDITOR_PAGE.get("html_mode_button"):
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click()
                            self.logger.info("已切回可视化模式")
                            break
                    except Exception:
                        continue

        page.wait_for_timeout(2000)

    def _set_editor_content_via_js(self, page, article_html: str) -> str:
        """通过JavaScript直接设置135编辑器内容。"""
        # 135编辑器基于UEditor，编辑区域在iframe #ueditor_0 中
        js_code = """(html) => {
            // 尝试UEditor API（135编辑器的UEditor实例）
            try {
                // 方法1: 通过UEditor全局对象
                if (window.UE && window.UE.instants) {
                    const keys = Object.keys(window.UE.instants);
                    for (const key of keys) {
                        const editor = window.UE.instants[key];
                        if (editor && typeof editor.setContent === 'function') {
                            editor.setContent(html);
                            return 'ueditor_api:' + key;
                        }
                    }
                }
                // 方法2: 通过iframe中的编辑区域
                const ueditorIframe = document.getElementById('ueditor_0');
                if (ueditorIframe && ueditorIframe.contentDocument) {
                    const body = ueditorIframe.contentDocument.body;
                    if (body) {
                        body.innerHTML = html;
                        return 'iframe_innerHTML';
                    }
                }
                // 方法3: 查找所有iframe中的编辑区域
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    try {
                        if (iframe.contentDocument && iframe.contentDocument.body) {
                            const body = iframe.contentDocument.body;
                            if (body.contentEditable === 'true' || body.getAttribute('contenteditable') === 'true') {
                                body.innerHTML = html;
                                return 'iframe_fallback';
                            }
                        }
                    } catch(e) { /* cross-origin */ }
                }
            } catch(e) {
                return 'error:' + e.message;
            }
            return 'failed';
        }"""
        try:
            result = page.evaluate(js_code, article_html)
            self.logger.info("JS设置编辑器内容结果: %s", result)
            return str(result)
        except Exception as exc:
            self.logger.warning("JS设置编辑器内容失败: %s", exc)
            return "failed"

    def _fill_title(self, page, title: str) -> None:
        """在135编辑器标题输入框中填写标题。"""
        self.logger.info("正在填写标题: %s", title[:30])

        # 135编辑器标题输入框ID为 edui52_wx_input 或类似
        for selector in ["#edui52_wx_input", "input[id*='wx_input']", "input.edui-wx-input"]:
            try:
                input_el = page.wait_for_selector(selector, timeout=5000)
                if input_el:
                    input_el.click()
                    input_el.fill("")
                    input_el.fill(title)
                    self.logger.info("已填写标题: %s", title[:30])
                    return
            except Exception:
                continue

        # 尝试JS方式填充
        try:
            page.evaluate("""(title) => {
                const inputs = document.querySelectorAll('input[id*="wx_input"], input.edui-wx-input');
                for (const input of inputs) {
                    if (input.offsetParent !== null) {
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        nativeSetter.call(input, title);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""", title)
            self.logger.info("已通过JS填写标题")
        except Exception as exc:
            self.logger.warning("填写标题失败: %s", exc)

    def _click_save_sync(self, page) -> None:
        """点击「保存同步」按钮，打开保存图文对话框。"""
        self.logger.info("正在点击「保存同步」按钮...")

        for selector in ["#save-as-template"]:
            try:
                btn = page.wait_for_selector(selector, timeout=10000)
                if btn and btn.is_visible():
                    btn.click()
                    self.logger.info("已点击「保存同步」按钮")
                    page.wait_for_timeout(3000)
                    return
            except Exception:
                continue

        try:
            page.click("text=保存同步", timeout=10000)
            self.logger.info("已通过文本匹配点击「保存同步」")
            page.wait_for_timeout(3000)
            return
        except Exception:
            pass

        raise RuntimeError("未找到「保存同步」按钮。")

    def _save_and_sync(self, page, *, cover_path: Path | None = None) -> bool:
        """在保存图文对话框中上传封面、勾选同步公众号、保存。"""
        self.logger.info("正在保存图文对话框中操作...")

        # 等待保存图文对话框出现
        dialog = None
        for _ in range(10):
            try:
                dialog = page.query_selector("#save-wx-msg-dialog")
                if dialog and dialog.is_visible():
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)

        if not dialog:
            self.logger.error("保存图文对话框未出现")
            return False

        # 上传封面图
        if cover_path and cover_path.exists():
            self._upload_cover_in_save_dialog(page, dialog, cover_path)

        # 确保同步公众号的复选框已勾选
        try:
            sync_checkbox = dialog.query_selector("input[type='checkbox']:not([id*='comment'])")
            if sync_checkbox and not sync_checkbox.is_checked():
                sync_checkbox.click(force=True)
                self.logger.info("已勾选同步公众号")
        except Exception as exc:
            self.logger.warning("检查同步复选框: %s", exc)

        # 选择「保存为新的图文」而非覆盖
        try:
            new_radio = dialog.query_selector("text=保存为新的图文")
            if new_radio:
                new_radio.click(force=True)
                self.logger.info("已选择「保存为新的图文」")
        except Exception:
            pass

        page.wait_for_timeout(1000)

        # 点击「保存文章」按钮
        try:
            save_btn = dialog.query_selector("text=保存文章")
            if not save_btn:
                # 尝试在页面上查找
                save_btn = page.query_selector(".modal.show .btn-primary, .modal.show button:has-text('保存')")
            if save_btn and save_btn.is_visible():
                save_btn.click(force=True)
                self.logger.info("已点击「保存文章」")
                page.wait_for_timeout(5000)
            else:
                self.logger.warning("未找到「保存文章」按钮")
                return False
        except Exception as exc:
            self.logger.error("点击保存按钮失败: %s", exc)
            return False

        # 等待保存和同步完成
        max_wait = 60
        start_time = time.time()
        while time.time() - start_time < max_wait:
            # 检查对话框是否已关闭
            try:
                dialog_check = page.query_selector("#save-wx-msg-dialog")
                if dialog_check is None or not dialog_check.is_visible():
                    self.logger.info("保存图文对话框已关闭，保存成功")
                    return True
            except Exception:
                # query_selector 返回 None 或 is_visible 抛异常都说明对话框已关闭
                self.logger.info("保存图文对话框已关闭（异常检测），保存成功")
                return True

            # 检查成功标记
            try:
                page_text = page.inner_text("body", timeout=5000)
                for marker in EDITOR_135_SYNC_SUCCESS_MARKERS:
                    if marker in page_text:
                        self.logger.info("同步成功！检测到标记: %s", marker)
                        return True
                # 检查常见的保存成功提示
                for marker in ["保存成功", "已保存", "操作成功"]:
                    if marker in page_text:
                        self.logger.info("保存成功！检测到标记: %s", marker)
                        return True
            except Exception:
                pass

            page.wait_for_timeout(2000)

        self.logger.warning("保存同步等待超时（%d秒）", max_wait)
        # 即使超时，也可能保存成功了，只是检测不到标记
        # 检查对话框是否还存在
        try:
            dialog_check = page.query_selector("#save-wx-msg-dialog")
            if dialog_check and dialog_check.is_visible():
                self.logger.warning("保存图文对话框仍然可见，可能保存失败")
                return False
        except Exception:
            pass
        # 对话框已关闭，可能保存成功
        self.logger.info("超时但对话框已关闭，假定保存成功")
        return True

    def _upload_cover_in_save_dialog(self, page, dialog, cover_path: Path) -> None:
        """在保存图文对话框中上传封面图。"""
        self.logger.info("正在上传封面图: %s", cover_path.name)

        abs_path = Path(os.path.abspath(str(cover_path)))

        # 方法1: 点击「上传封面图」按钮
        try:
            upload_btn = dialog.query_selector("text=上传封面图")
            if upload_btn and upload_btn.is_visible():
                upload_btn.click()
                self.logger.info("已点击「上传封面图」")
                page.wait_for_timeout(2000)

                # 查找文件上传input
                file_inputs = page.query_selector_all("input[type='file'][accept*='image']")
                if not file_inputs:
                    file_inputs = page.query_selector_all("input[type='file']")
                for fi in file_inputs:
                    try:
                        fi.set_input_files(str(abs_path))
                        self.logger.info("已上传封面图文件")
                        page.wait_for_timeout(3000)

                        # 处理可能弹出的图片裁剪对话框
                        self._handle_image_cropper(page)
                        return
                    except Exception:
                        continue
        except Exception as exc:
            self.logger.warning("点击上传封面图按钮失败: %s", exc)

        # 方法2: 直接查找file input
        try:
            file_inputs = page.query_selector_all("input[type='file']")
            for fi in file_inputs:
                try:
                    fi.set_input_files(str(abs_path))
                    self.logger.info("已通过file input上传封面图")
                    page.wait_for_timeout(3000)
                    self._handle_image_cropper(page)
                    return
                except Exception:
                    continue
        except Exception:
            pass

        self.logger.warning("封面图上传可能失败，继续执行...")

    def _handle_image_cropper(self, page) -> None:
        """处理上传封面图后弹出的裁剪对话框。"""
        try:
            cropper = page.query_selector("#save-image-cropper")
            if cropper and cropper.is_visible():
                self.logger.info("检测到图片裁剪对话框，尝试确认裁剪...")

                # 点击裁剪确认按钮
                for selector in [
                    "#save-image-cropper .btn-primary",
                    "#save-image-cropper button:has-text('确定')",
                    "#save-image-cropper button:has-text('确认')",
                    "#save-image-cropper .cropper-confirm",
                ]:
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click(force=True)
                            self.logger.info("已点击裁剪确认按钮: %s", selector)
                            page.wait_for_timeout(2000)
                            break
                    except Exception:
                        continue

                # 尝试通过JS点击
                try:
                    page.evaluate("""() => {
                        const dialog = document.getElementById('save-image-cropper');
                        if (dialog) {
                            const btn = dialog.querySelector('.btn-primary, button');
                            if (btn) btn.click();
                        }
                    }""")
                    self.logger.info("已通过JS确认裁剪")
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
        except Exception:
            pass

        # 处理可能弹出的版权声明对话框
        self._dismiss_copyright_popup(page)

    def _dismiss_copyright_popup(self, page) -> None:
        """关闭上传图片后弹出的版权声明对话框。"""
        try:
            # 查找版权声明对话框
            popup = page.query_selector("[id*='copyright_popup']")
            if popup and popup.is_visible():
                self.logger.info("检测到版权声明对话框，尝试关闭...")

                # 点击确认/关闭按钮
                for selector in [
                    ".btn-primary",
                    "button:has-text('确认')",
                    "button:has-text('确定')",
                    "button:has-text('同意')",
                    ".close",
                    "button.close",
                ]:
                    try:
                        btn = popup.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click(force=True)
                            self.logger.info("已关闭版权声明对话框")
                            page.wait_for_timeout(1000)
                            return
                    except Exception:
                        continue

                # 尝试JS关闭
                try:
                    page.evaluate("""() => {
                        const popups = document.querySelectorAll('[id*="copyright_popup"]');
                        for (const popup of popups) {
                            if (popup.classList.contains('show') || popup.style.display !== 'none') {
                                const btn = popup.querySelector('.btn-primary, button, .close');
                                if (btn) btn.click();
                                else popup.style.display = 'none';
                            }
                        }
                    }""")
                    self.logger.info("已通过JS关闭版权声明对话框")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
        except Exception:
            pass

    def _capture_debug_artifacts(self, page, context: dict[str, Any]) -> None:
        """捕获调试信息。"""
        if page is None:
            return
        debug_dir = self.settings.account_runtime_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        try:
            screenshot_path = debug_dir / f"editor_135_{timestamp}.png"
            page.screenshot(path=str(screenshot_path))
            self.logger.info("调试截图已保存: %s", screenshot_path)
        except Exception:
            pass

        try:
            html_path = debug_dir / f"editor_135_{timestamp}.html"
            html_path.write_text(page.content(), encoding="utf-8")
            self.logger.info("调试HTML已保存: %s", html_path)
        except Exception:
            pass
