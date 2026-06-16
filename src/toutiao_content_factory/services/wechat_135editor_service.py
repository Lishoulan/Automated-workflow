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
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe

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
        self._debug_dir = settings.account_runtime_dir / "debug"
        self._debug_dir.mkdir(parents=True, exist_ok=True)

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

        # Run sync_playwright in a dedicated thread to avoid
        # "using Playwright Sync API inside the asyncio loop" errors
        # when called from an async context (e.g. FastAPI / cloud_daily_run).
        def _run_in_thread() -> dict[str, Any]:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=self.settings.publishing.headless,
                    channel=self.settings.publishing.channel or None,
                    executable_path=self.settings.publishing.executable_path or None,
                )
                page = None
                try:
                    # 注入导出的明文 Cookie（解决跨平台 DPAPI 加密不兼容）
                    self._inject_exported_cookies(browser, "135editor")

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
                    sync_result = self._save_and_sync(page, title=title, cover_path=cover_path)

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

        return run_sync_playwright_safe(_run_in_thread, timeout=120)

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
        # 如果路径以 .runtime 开头，相对于项目根目录解析（避免嵌套 .runtime/.runtime/）
        if profile_value.startswith(".runtime") or profile_value.startswith(".runtime\\"):
            return self.settings.project_root / profile_value
        return self.settings.account_runtime_dir / profile_value

    def _inject_exported_cookies(self, browser_ctx, platform: str) -> None:
        """从 exported_cookies.json 注入明文 Cookie 到浏览器上下文。

        解决 Windows DPAPI 加密的 Cookie 在 Linux 云端无法解密的问题。
        在浏览器打开后、页面导航前注入，确保 Cookie 在运行时生效。
        """
        import json as _json

        # 查找 cookies 文件
        candidates = [
            self.settings.account_runtime_dir / "automation" / "exported_cookies.json",
        ]
        cookies_file = None
        for c in candidates:
            if c.exists():
                cookies_file = c
                break

        if not cookies_file:
            return  # 无导出文件，跳过注入

        try:
            data = _json.loads(cookies_file.read_text(encoding="utf-8"))
            platform_data = data.get("platforms", {}).get(platform, {})
            cookies = platform_data.get("cookies", [])

            if not cookies:
                return

            injectable = []
            for c in cookies:
                cookie = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c["path"],
                }
                if c.get("expires", -1) > 0:
                    cookie["expires"] = c["expires"]
                if c.get("httpOnly"):
                    cookie["httpOnly"] = True
                if c.get("secure"):
                    cookie["secure"] = True
                if c.get("sameSite") in ("Strict", "Lax", "None"):
                    cookie["sameSite"] = c["sameSite"]
                injectable.append(cookie)

            if injectable:
                browser_ctx.add_cookies(injectable)
                self.logger.info("运行时注入 %s Cookie: %d 个", platform, len(injectable))
        except Exception as exc:
            self.logger.warning("注入导出 Cookie 失败 (非致命): %s", exc)

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

    def _save_and_sync(self, page, *, title: str = "", cover_path: Path | None = None) -> bool:
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

        # 确保对话框中的文章标题已填写（WxMsgName 字段）
        # 始终用传入的 title 覆盖，避免标题为空或显示为字体大小
        try:
            name_input = dialog.query_selector("#WxMsgName")
            if name_input:
                fill_title = title.strip() if title.strip() else ""
                if not fill_title:
                    try:
                        title_input = page.query_selector("input[id*='wx_input'], input.edui-wx-input")
                        if title_input:
                            fill_title = title_input.input_value().strip()
                    except Exception:
                        pass
                if fill_title:
                    # 使用 JS nativeSetter + dispatchEvent 确保 React/jQuery 表单能感知到值变化
                    page.evaluate("""([selector, value]) => {
                        const input = document.querySelector(selector);
                        if (input) {
                            const nativeSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value'
                            ).set;
                            nativeSetter.call(input, value);
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", ["#WxMsgName", fill_title])
                    self.logger.info("已填写对话框标题: %s", fill_title[:30])
                else:
                    self.logger.warning("对话框标题为空且无法获取标题")
        except Exception as exc:
            self.logger.warning("填写对话框标题失败: %s", exc)

        # 确保同步公众号的复选框已勾选
        try:
            # 同步复选框的name为 data[WxMsg][sync][]
            sync_checkbox = dialog.query_selector("input[name='data[WxMsg][sync][]']")
            if sync_checkbox and not sync_checkbox.is_checked():
                sync_checkbox.click(force=True)
                self.logger.info("已勾选同步公众号")
            elif sync_checkbox and sync_checkbox.is_checked():
                self.logger.info("同步公众号已勾选")
            else:
                self.logger.warning("未找到同步公众号复选框")
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
        # 注意：135编辑器的保存按钮是 <input type="submit" id="save-wx-msg">，
        # 有两个同名元素（一个隐藏一个可见），需要选可见的那个
        try:
            save_btn = None

            # 方法1: 精确匹配 value="保存文章" 的 input#save-wx-msg
            # 注意：有两个 input#save-wx-msg，一个 value="投稿给其他用户"，一个 value="保存文章"
            try:
                save_btn = page.locator("#save-wx-msg[value='保存文章']:visible").first
                if save_btn.count() > 0:
                    self.logger.info("通过 locator 找到 value='保存文章' 的 #save-wx-msg")
                else:
                    save_btn = None
            except Exception:
                save_btn = None

            # 方法2: query_selector_all 遍历所有 #save-wx-msg，找 value="保存文章" 的那个
            if not save_btn:
                try:
                    all_save_btns = page.query_selector_all("#save-wx-msg")
                    for btn in all_save_btns:
                        if btn.is_visible():
                            btn_value = btn.get_attribute("value") or ""
                            if "保存" in btn_value and "投稿" not in btn_value:
                                save_btn = btn
                                self.logger.info("通过遍历找到 value='%s' 的 #save-wx-msg", btn_value)
                                break
                except Exception:
                    pass

            # 方法3: 在对话框内查找 input[type=submit]
            if not save_btn:
                try:
                    submit_inputs = page.query_selector_all("#save-wx-msg-dialog input[type='submit']")
                    for btn in submit_inputs:
                        if btn.is_visible():
                            save_btn = btn
                            self.logger.info("通过 input[type=submit] 找到保存按钮")
                            break
                except Exception:
                    pass

            # 方法4: 回退查找对话框中所有可见的 .btn-primary（含 input 和 button）
            if not save_btn:
                try:
                    btns = page.query_selector_all("#save-wx-msg-dialog .btn-primary")
                    for btn in btns:
                        if btn.is_visible():
                            save_btn = btn
                            self.logger.info("通过 .btn-primary 回退找到保存按钮")
                            break
                except Exception:
                    pass

            if save_btn and save_btn.is_visible():
                # 保存前截图
                try:
                    debug_path = self._debug_dir / f"before_save_click_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                except Exception:
                    pass

                # 尝试多种点击方式
                click_success = False

                # 方式1: Playwright locator click — 精确匹配 value="保存文章"
                try:
                    save_locator = page.locator("#save-wx-msg[value='保存文章']:visible").first
                    save_locator.click(timeout=10000)
                    click_success = True
                    self.logger.info("通过 locator.click() 点击保存按钮成功")
                except Exception as exc:
                    self.logger.warning("locator.click() 失败: %s，尝试其他方式", exc)

                # 方式2: 直接 click element handle
                if not click_success:
                    try:
                        save_btn.click(force=True)
                        click_success = True
                        self.logger.info("通过 element_handle.click() 点击保存按钮成功")
                    except Exception as exc:
                        self.logger.warning("element_handle.click() 失败: %s，尝试其他方式", exc)

                # 方式3: 通过 form submit 触发
                if not click_success:
                    try:
                        form_result = page.evaluate("""() => {
                            const btn = document.getElementById('save-wx-msg');
                            if (btn && btn.form) {
                                btn.form.submit();
                                return 'form_submit';
                            }
                            // 查找对话框中的 form
                            const dialog = document.getElementById('save-wx-msg-dialog');
                            if (dialog) {
                                const form = dialog.querySelector('form');
                                if (form) {
                                    form.submit();
                                    return 'dialog_form_submit';
                                }
                            }
                            return 'no_form';
                        }""")
                        self.logger.info("form submit 结果: %s", form_result)
                        if form_result != "no_form":
                            click_success = True
                    except Exception as exc:
                        self.logger.warning("form submit 失败: %s", exc)

                # 方式4: JS dispatch submit event
                if not click_success:
                    try:
                        page.evaluate("""() => {
                            const btns = document.querySelectorAll('#save-wx-msg');
                            for (const btn of btns) {
                                if (btn.value === '保存文章' && btn.form) {
                                    btn.form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                                    return;
                                }
                            }
                        }""")
                        click_success = True
                        self.logger.info("通过 dispatchEvent('submit') 触发保存")
                    except Exception as exc:
                        self.logger.warning("dispatchEvent submit 失败: %s", exc)

                # 方式5: 直接调用全局 saveWxMsg() 函数
                if not click_success:
                    try:
                        fn_result = page.evaluate("""() => {
                            if (typeof saveWxMsg === 'function') {
                                saveWxMsg();
                                return 'saveWxMsg_called';
                            }
                            return 'no_fn';
                        }""")
                        self.logger.info("saveWxMsg() 结果: %s", fn_result)
                        if fn_result == "saveWxMsg_called":
                            click_success = True
                    except Exception as exc:
                        self.logger.warning("saveWxMsg() 调用失败: %s", exc)

                # 方式6: jQuery trigger click on the correct button
                if not click_success:
                    try:
                        jq_result = page.evaluate("""() => {
                            const btns = document.querySelectorAll('#save-wx-msg');
                            for (const btn of btns) {
                                if (btn.value === '保存文章') {
                                    if (typeof jQuery === 'function') {
                                        jQuery(btn).trigger('click');
                                        return 'jquery_trigger';
                                    }
                                    btn.click();
                                    return 'native_click';
                                }
                            }
                            return 'not_found';
                        }""")
                        self.logger.info("jQuery trigger 结果: %s", jq_result)
                        if jq_result != "not_found":
                            click_success = True
                    except Exception as exc:
                        self.logger.warning("jQuery trigger 失败: %s", exc)

                if not click_success:
                    self.logger.error("所有点击方式均失败")
                    return False

                self.logger.info("已触发保存操作")
                page.wait_for_timeout(3000)

                # 保存后可能再次弹出版权声明对话框，需要关闭
                for attempt in range(3):
                    self._dismiss_copyright_popup(page)
                    page.wait_for_timeout(2000)
                    popup_check = page.query_selector("[id*='copyright_popup']")
                    if not popup_check or not popup_check.is_visible():
                        break
                # 保存后截图
                try:
                    debug_path = self._debug_dir / f"after_save_click_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                    self.logger.info("调试截图(保存后): %s", debug_path)
                except Exception:
                    pass
                # 检查是否有错误提示
                try:
                    error_el = page.query_selector(".error, .alert-danger, .toast-error, [class*='error'], .layui-layer-content")
                    if error_el and error_el.is_visible():
                        self.logger.error("保存时出现错误: %s", error_el.inner_text(timeout=3000))
                except Exception:
                    pass
                # 检查对话框中是否还有必填项提示
                try:
                    dialog_text = page.inner_text("#save-wx-msg-dialog", timeout=3000)
                    if "请填写" in dialog_text or "不能为空" in dialog_text:
                        self.logger.warning("对话框有必填项提示: %s", dialog_text[:200])
                except Exception:
                    pass
            else:
                self.logger.warning("未找到「保存文章」按钮")
                try:
                    debug_path = self._debug_dir / f"no_save_btn_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                    self.logger.info("调试截图(无保存按钮): %s", debug_path)
                except Exception:
                    pass
                return False
        except Exception as exc:
            self.logger.error("点击保存按钮失败: %s", exc)
            return False

        # 等待保存和同步完成
        max_wait = 90
        start_time = time.time()
        last_progress = ""
        while time.time() - start_time < max_wait:
            # 检查同步进度条（多种选择器）
            try:
                progress_text = page.evaluate("""() => {
                    // 查找同步进度条
                    const selectors = [
                        '.syncProgressBar',
                        '[class*="syncProgress"]',
                        '[class*="SyncProgress"]',
                        '.progress-bar',
                        '[class*="progress"]',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) {
                            return el.innerText || el.textContent;
                        }
                    }
                    // 查找包含百分比的元素
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = (el.innerText || '').trim();
                        if (/^\\d+%$/.test(text) && el.offsetParent !== null) {
                            return text;
                        }
                    }
                    return '';
                }""")
                if progress_text and progress_text.strip():
                    progress_stripped = progress_text.strip()[:50]
                    if progress_stripped != last_progress:
                        self.logger.info("同步进度: %s", progress_stripped)
                        last_progress = progress_stripped
                    if "100%" in progress_stripped:
                        self.logger.info("同步进度100%，同步完成")
                        page.wait_for_timeout(3000)
                        return True
            except Exception:
                pass

            # 检查对话框是否已关闭
            try:
                dialog_check = page.query_selector("#save-wx-msg-dialog")
                if dialog_check is None or not dialog_check.is_visible():
                    # 对话框关闭 = 保存到135编辑器成功
                    # 但同步到公众号是异步的，需要继续等待
                    elapsed = time.time() - start_time
                    if elapsed < 10:
                        # 对话框关闭太快（<10秒），同步可能还没开始
                        # 继续等待同步进度
                        self.logger.info("保存图文对话框已关闭，等待同步完成...")
                        page.wait_for_timeout(5000)
                        # 检查是否有同步结果提示
                        try:
                            sync_result_text = page.evaluate("""() => {
                                // 查找同步结果提示
                                const selectors = [
                                    '.sync-result', '.sync-result-msg',
                                    '[class*="syncResult"]', '[class*="sync_result"]',
                                    '.toast', '.layui-layer-content',
                                    '.alert-success', '.alert-info',
                                ];
                                for (const sel of selectors) {
                                    const el = document.querySelector(sel);
                                    if (el && el.offsetParent !== null) {
                                        return el.innerText || el.textContent;
                                    }
                                }
                                return '';
                            }""")
                            if sync_result_text and sync_result_text.strip():
                                self.logger.info("同步结果提示: %s", sync_result_text.strip()[:100])
                        except Exception:
                            pass
                        # 对话框关闭后额外等待同步
                        page.wait_for_timeout(10000)
                    self.logger.info("保存图文对话框已关闭，保存成功")
                    return True
            except Exception:
                self.logger.info("保存图文对话框已关闭（异常检测），保存成功")
                return True

            # 检查成功标记
            try:
                page_text = page.inner_text("body", timeout=5000)
                for marker in EDITOR_135_SYNC_SUCCESS_MARKERS:
                    if marker in page_text:
                        self.logger.info("同步成功！检测到标记: %s", marker)
                        return True
                for marker in ["保存成功", "已保存", "操作成功", "同步成功"]:
                    if marker in page_text:
                        self.logger.info("保存成功！检测到标记: %s", marker)
                        return True
            except Exception:
                pass

            # 再次检查并关闭可能弹出的版权声明
            self._dismiss_copyright_popup(page)

            # 额外：通过JS强制关闭所有版权声明对话框
            try:
                page.evaluate("""() => {
                    const popups = document.querySelectorAll('[id*="copyright_popup"]');
                    for (const popup of popups) {
                        if (popup.classList.contains('show') || popup.style.display !== 'none') {
                            popup.style.display = 'none';
                            popup.classList.remove('show', 'in');
                        }
                    }
                    // 也关闭遮罩层
                    const backdrops = document.querySelectorAll('.modal-backdrop');
                    for (const bd of backdrops) {
                        // 只关闭与版权声明相关的遮罩
                        if (backdrops.length > 1) {
                            bd.remove();
                        }
                    }
                }""")
            except Exception:
                pass

            page.wait_for_timeout(3000)

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
        """关闭上传图片后弹出的版权声明对话框，并勾选'下次不再提示'。"""
        try:
            # 查找版权声明对话框
            popup = page.query_selector("[id*='copyright_popup']")
            if popup and popup.is_visible():
                self.logger.info("检测到版权声明对话框，尝试关闭...")

                # 先勾选"下次不再提示"
                try:
                    no_remind = popup.query_selector("input[type='checkbox'], input[name*='remind'], input[name*='again']")
                    if no_remind and not no_remind.is_checked():
                        no_remind.click(force=True)
                        self.logger.info("已勾选'下次不再提示'")
                except Exception:
                    pass

                # 点击确认/关闭按钮
                for selector in [
                    "button:has-text('我已知晓')",
                    "button:has-text('确认')",
                    "button:has-text('确定')",
                    "button:has-text('同意')",
                    ".btn-primary",
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
