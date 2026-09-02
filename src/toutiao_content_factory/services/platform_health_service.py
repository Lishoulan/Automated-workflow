from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import datetime
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile, check_cookie_health
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe
from toutiao_content_factory.utils.browser_utils import apply_stealth, BrowserTimeout  # noqa: E501

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    sync_playwright = None


class PlatformHealthService:
    def __init__(
        self,
        settings: AppSettings,
        logger: logging.Logger,
        *,
        toutiao_publish_service,
        wechat_publish_service,
        notification_service=None,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.toutiao_publish_service = toutiao_publish_service
        self.wechat_publish_service = wechat_publish_service
        self.notification_service = notification_service

    def collect(self) -> dict[str, Any]:
        payload = {
            "generated_at": self._now(),
            "account_key": self.settings.account_key,
            "account_slug": self.settings.account_slug,
            "platforms": {
                "toutiao": self._collect_toutiao(),
                "wechat": self._collect_wechat(),
            },
        }
        payload["blocking_flags"] = self._collect_global_blockers(payload["platforms"])
        path = self.settings.automation_dir / "platform_health.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"platform_health": str(path), "blocking_flags": payload["blocking_flags"], "platforms": payload["platforms"]}

    def ensure_publish_ready(self) -> dict[str, Any]:
        health = self.collect()
        blocking_platforms = self._collect_required_auth_failures(health.get("platforms", {}))
        health["blocking_platforms"] = blocking_platforms
        return health

    def quick_health_check(self) -> dict[str, Any]:
        """轻量级健康检查：仅检查 Cookie 和配置状态，不启动浏览器。

        适用于 auto_run 等场景的前置检查，避免因浏览器启动导致延迟。
        返回 dict 包含 blocking_platforms 列表和各平台状态摘要。
        """
        platforms: dict[str, Any] = {
            "toutiao": self._quick_check_toutiao(),
            "wechat": self._quick_check_wechat(),
        }
        blocking_platforms = self._collect_required_auth_failures(platforms)
        blocking_flags = self._collect_global_blockers(platforms)
        return {
            "platforms": platforms,
            "blocking_platforms": blocking_platforms,
            "blocking_flags": blocking_flags,
        }

    def _quick_check_toutiao(self) -> dict[str, Any]:
        if not self.settings.publishing.enabled:
            return {"enabled": False, "required": False, "status": "disabled", "blocking_flags": [], "warnings": []}
        blockers: list[str] = []
        warnings: list[str] = []
        cookie_health = check_cookie_health(self.toutiao_publish_service._resolve_profile_dir())
        if not cookie_health.get("healthy"):
            warnings.append(
                f"Toutiao cookie health check: {cookie_health.get('reason', 'unknown')} "
                f"(age: {cookie_health.get('age_hours', 'N/A')}h)"
            )
            if self.settings.publishing.auto_publish:
                blockers.append("Toutiao cookie 已过期或不存在，自动发布将失败。")
        return {
            "enabled": True,
            "required": bool(self.settings.publishing.auto_publish),
            "status": "blocked" if blockers else "ready",
            "blocking_flags": self._unique_texts(blockers),
            "warnings": self._unique_texts(warnings),
        }

    def _quick_check_wechat(self) -> dict[str, Any]:
        if not self.settings.wechat.enabled:
            return {"enabled": False, "required": False, "status": "disabled", "blocking_flags": [], "warnings": []}
        blockers: list[str] = []
        warnings: list[str] = []

        has_api_credentials = bool(self.settings.wechat.app_id and self.settings.wechat.app_secret)
        if has_api_credentials:
            try:
                token = self.wechat_publish_service._get_access_token()
                if not token:
                    blockers.append("WeChat access token 获取失败。")
            except Exception as exc:
                if exc.__class__.__name__ == "WechatIpWhitelistBlockedException":
                    blockers.append(f"WeChat IP 白名单拦截: {exc}")
                else:
                    blockers.append(f"WeChat access token check failed: {exc}")
        else:
            warnings.append("WeChat API credentials (app_id/app_secret) not configured. Falling back to browser-only mode.")
        cookie_health = check_cookie_health(self.wechat_publish_service._resolve_browser_profile_dir())
        if not cookie_health.get("healthy"):
            warnings.append(
                f"WeChat cookie health check: {cookie_health.get('reason', 'unknown')} "
                f"(age: {cookie_health.get('age_hours', 'N/A')}h)"
            )
        return {
            "enabled": True,
            "required": bool(self.settings.wechat.auto_publish),
            "status": "blocked" if blockers else "ready",
            "blocking_flags": self._unique_texts(blockers),
            "warnings": self._unique_texts(warnings),
        }

    def _collect_toutiao(self) -> dict[str, Any]:
        if not self.settings.publishing.enabled:
            return {"enabled": False, "required": False, "status": "disabled", "blocking_flags": [], "warnings": []}
        summary = self.toutiao_publish_service.collect_preflight_summary()
        session_payload = self._probe_toutiao_login_state()
        blockers = self._unique_texts(
            list(session_payload.get("blocking_flags", [])) + list(summary.get("blocking_flags", []))
        )
        warnings = self._unique_texts(
            list(session_payload.get("warnings", [])) + list(summary.get("warnings", []))
        )
        payload = {
            "enabled": True,
            "required": bool(self.settings.publishing.auto_publish),
            "status": "blocked" if blockers else "ready",
            **summary,
        }
        payload["blocking_flags"] = blockers
        payload["warnings"] = warnings
        payload["diagnostics"] = {
            **dict(session_payload.get("diagnostics", {})),
            **dict(summary.get("diagnostics", {})),
        }
        return payload

    def _collect_wechat(self) -> dict[str, Any]:
        if not self.settings.wechat.enabled:
            return {"enabled": False, "required": False, "status": "disabled", "blocking_flags": [], "warnings": []}

        blockers: list[str] = []
        warnings: list[str] = []
        diagnostics: dict[str, Any] = {}

        has_api_credentials = bool(self.settings.wechat.app_id and self.settings.wechat.app_secret)
        if has_api_credentials:
            try:
                token = self.wechat_publish_service._get_access_token()
                diagnostics["access_token_ready"] = bool(token)
            except Exception as exc:
                if exc.__class__.__name__ == "WechatIpWhitelistBlockedException":
                    diagnostics["blocked_reason"] = "BLOCKED_BY_IP"
                    diagnostics["invalid_ip"] = str(getattr(exc, "ip_address", "")).strip()
                    blockers.append(str(exc))
                else:
                    blockers.append(f"WeChat access token check failed: {exc}")
        else:
            diagnostics["access_token_ready"] = False
            warnings.append("WeChat API credentials (app_id/app_secret) not configured. Falling back to browser-only mode.")

        session_payload = self._probe_wechat_login_state()
        blockers.extend(session_payload.get("blocking_flags", []))
        warnings.extend(session_payload.get("warnings", []))
        diagnostics.update(session_payload.get("diagnostics", {}))

        return {
            "enabled": True,
            "required": bool(self.settings.wechat.auto_publish),
            "status": "blocked" if blockers else "ready",
            "blocking_flags": self._unique_texts(blockers),
            "warnings": self._unique_texts(warnings),
            "diagnostics": diagnostics,
        }

    def _probe_wechat_login_state(self) -> dict[str, Any]:
        profile_dir = self.wechat_publish_service._resolve_browser_profile_dir()
        diagnostics: dict[str, Any] = {
            "profile_dir": str(profile_dir),
        }
        warnings: list[str] = []
        blockers: list[str] = []
        preflight_path = self.settings.automation_dir / "wechat_publish_preflight.json"

        cookie_health = check_cookie_health(profile_dir)
        diagnostics["cookie_health"] = cookie_health
        if not cookie_health.get("healthy"):
            warnings.append(
                f"WeChat cookie health check: {cookie_health.get('reason', 'unknown')} "
                f"(age: {cookie_health.get('age_hours', 'N/A')}h)"
            )

        if not profile_dir.exists():
            warnings.append(f"WeChat browser profile not found: {profile_dir}")
        if sync_playwright is None:
            blockers.append("Playwright is not installed.")
            return {
                "blocking_flags": blockers,
                "warnings": warnings,
                "diagnostics": diagnostics,
            }

        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="platform-health:wechat",
            extra_paths=[self.settings.account_runtime_dir],
        )
        # 云端 headless 环境下必须使用 headless=True，否则无显示器会失败
        headless = bool(self.settings.wechat.headless) if hasattr(self.settings.wechat, 'headless') else bool(self.settings.publishing.headless)

        def _run():
            browser = None
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        channel=self.settings.publishing.channel or None,
                        executable_path=self.settings.publishing.executable_path or None,
                    )
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    self.wechat_publish_service._open_wechat_backend(page)
                    page.wait_for_timeout(1500)

                    diagnostics["current_url"] = str(getattr(page, "url", "") or "")
                    diagnostics["login_page_detected"] = bool(self.wechat_publish_service._is_login_page(page))
                    diagnostics["session_ready"] = bool(self.wechat_publish_service._browser_session_ready(page))
                    diagnostics["visible_markers"] = self._extract_wechat_visible_markers(page)

                    if diagnostics["login_page_detected"]:
                        blockers.append("微信公众号后台需要重新登录，检测到扫码/登录页。")
                        self._capture_and_notify_qrcode(page, "wechat")
                    elif not diagnostics["session_ready"]:
                        blockers.append("微信公众号后台未检测到可用登录态，未发现头像/草稿箱/素材管理等后台标识。")
                        self._capture_and_notify_qrcode(page, "wechat")

                    snapshot = {
                        "generated_at": self._now(),
                        "current_url": diagnostics["current_url"],
                        "state": "ready" if not blockers else "login_required",
                        "blocking_flags": blockers,
                        "warnings": warnings,
                        "diagnostics": diagnostics,
                    }
                    preflight_path.parent.mkdir(parents=True, exist_ok=True)
                    preflight_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                    diagnostics["browser_preflight_path"] = str(preflight_path)
            except Exception as exc:
                blockers.append(f"WeChat browser login preflight failed: {exc}")
            finally:
                with suppress(Exception):
                    if browser is not None:
                        browser.close()

        run_sync_playwright_safe(_run, timeout=300)

        if preflight_path.exists():
            try:
                diagnostics["last_browser_preflight"] = json.loads(preflight_path.read_text(encoding="utf-8"))
            except Exception:
                warnings.append("WeChat browser preflight file exists but could not be parsed.")
        else:
            warnings.append("WeChat browser preflight file not found. Re-run --init-wechat-login if browser publish is needed.")

        return {
            "blocking_flags": blockers,
            "warnings": warnings,
            "diagnostics": diagnostics,
        }

    def _probe_toutiao_login_state(self) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {
            "profile_dir": str(self.toutiao_publish_service._resolve_profile_dir()),
        }
        warnings: list[str] = []
        blockers: list[str] = []

        cookie_health = check_cookie_health(self.toutiao_publish_service._resolve_profile_dir())
        diagnostics["cookie_health"] = cookie_health
        if not cookie_health.get("healthy"):
            warnings.append(
                f"Toutiao cookie health check: {cookie_health.get('reason', 'unknown')} "
                f"(age: {cookie_health.get('age_hours', 'N/A')}h)"
            )

        if sync_playwright is None:
            blockers.append("Playwright is not installed.")
            return {
                "blocking_flags": blockers,
                "warnings": warnings,
                "diagnostics": diagnostics,
            }

        profile_dir = self.toutiao_publish_service._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="platform-health:toutiao",
            extra_paths=[self.settings.account_runtime_dir],
        )
        # 云端 headless 环境下必须使用 headless=True，否则无显示器会失败
        headless = bool(self.settings.publishing.headless)

        def _run():
            browser = None
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        channel=self.settings.publishing.channel or None,
                        executable_path=self.settings.publishing.executable_path or None,
                    )
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    probe_url = (
                        self.settings.publishing.publish_url
                        or self.settings.publishing.base_url
                        or "https://mp.toutiao.com/profile_v4/graphic/publish"
                    )
                    self.toutiao_publish_service._goto_publish_page(page, probe_url)
                    page.wait_for_timeout(2500)

                    diagnostics["current_url"] = str(getattr(page, "url", "") or "")
                    diagnostics["visible_markers"] = self._extract_toutiao_visible_markers(page)
                    diagnostics["login_page_detected"] = self._toutiao_login_page_detected(page)
                    diagnostics["session_ready"] = self._toutiao_session_ready(page)

                    # If we reached the publish page without a login redirect, consider the session valid
                    current_url = diagnostics["current_url"].lower()
                    on_publish_page = "graphic/publish" in current_url or "profile_v4" in current_url
                    if diagnostics["login_page_detected"]:
                        blockers.append("Toutiao creator center requires re-login; login page or timeout dialog detected.")
                        self._capture_and_notify_qrcode(page, "toutiao")
                    elif not diagnostics["session_ready"] and not on_publish_page:
                        blockers.append("Toutiao creator center did not expose expected logged-in markers such as avatar or creator workspace navigation.")
                        self._capture_and_notify_qrcode(page, "toutiao")
            except Exception as exc:
                blockers.append(f"Toutiao browser login preflight failed: {exc}")
            finally:
                with suppress(Exception):
                    if browser is not None:
                        browser.close()

        run_sync_playwright_safe(_run, timeout=300)

        return {
            "blocking_flags": blockers,
            "warnings": warnings,
            "diagnostics": diagnostics,
        }

    def _extract_wechat_visible_markers(self, page) -> list[str]:
        markers = {
            "头像": [
                "img[src*='wx.qlogo.cn']",
                ".weui-desktop-account__thumb img",
                ".weui-desktop-layout__side .avatar img",
            ],
            "草稿箱": ["text=草稿箱"],
            "素材管理": ["text=素材管理"],
            "发表记录": ["text=发表记录"],
            "首页": ["text=首页"],
            "登录超时": ["text=登录超时", "text=重新登录", "text=扫码登录", "text=微信公众平台"],
        }
        visible: list[str] = []
        for label, selectors in markers.items():
            for selector in selectors:
                with suppress(Exception):
                    if page.locator(selector).first.is_visible(timeout=400):
                        visible.append(label)
                        break
        return self._unique_texts(visible)

    def _extract_toutiao_visible_markers(self, page) -> list[str]:
        markers = {
            "avatar": [
                "img[class*='avatar']",
                "img[src*='toutiaoimg.com']",
                "[class*='user-avatar'] img",
            ],
            "creator_center": [
                "text=创作中心",
                "text=工作台",
                "text=数据中心",
            ],
            "publish_entry": [
                "text=发文",
                "text=写文章",
                "text=发布",
            ],
            "login_timeout": [
                "text=登录超时",
                "text=登录失效",
                "text=立即登录",
                "text=重新登录",
            ],
        }
        visible: list[str] = []
        for label, selectors in markers.items():
            for selector in selectors:
                with suppress(Exception):
                    if page.locator(selector).first.is_visible(timeout=400):
                        visible.append(label)
                        break
        return self._unique_texts(visible)

    def _toutiao_login_page_detected(self, page) -> bool:
        current_url = str(getattr(page, "url", "") or "").lower()
        if any(token in current_url for token in ("passport", "login", "sso.toutiao.com")):
            return True
        visible_markers = set(self._extract_toutiao_visible_markers(page))
        return "login_timeout" in visible_markers

    def _toutiao_session_ready(self, page) -> bool:
        visible_markers = set(self._extract_toutiao_visible_markers(page))
        if "avatar" in visible_markers:
            return True
        return "creator_center" in visible_markers and "publish_entry" in visible_markers

    def _capture_and_notify_qrcode(self, page, platform: str) -> None:
        """Capture QR code screenshot and send notification."""
        try:
            debug_dir = self.settings.output_dir / "login_qrcodes"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            screenshot_path = debug_dir / f"{platform}_login_{ts}.png"
            page.screenshot(path=str(screenshot_path), full_page=False)
            self.logger.warning(
                "Login page detected for %s. Screenshot saved: %s. "
                "Please re-login manually.",
                platform, screenshot_path,
            )
            # Try to send notification if available
            if self.notification_service:
                try:
                    self.notification_service.send_notification(
                        title=f"[{platform.upper()}] Login expired - QR code saved",
                        message=f"Login page screenshot saved to: {screenshot_path}. "
                                f"Please re-login at {self.settings.publishing.base_url or 'the platform backend'}.",
                        notification_type="login_expired",
                    )
                except Exception:
                    pass
        except Exception as exc:
            self.logger.warning("Failed to capture QR code for %s: %s", platform, exc)

    def _collect_required_auth_failures(self, platforms: dict[str, Any]) -> list[str]:
        blocking_platforms: list[str] = []
        for name, payload in platforms.items():
            if not isinstance(payload, dict):
                continue
            if not payload.get("enabled") or not payload.get("required"):
                continue
            if payload.get("blocking_flags"):
                blocking_platforms.append(str(name))
        return blocking_platforms

    def _collect_global_blockers(self, platforms: dict[str, Any]) -> list[str]:
        blockers: list[str] = []
        for name, payload in platforms.items():
            if not isinstance(payload, dict) or not payload.get("enabled"):
                continue
            for item in payload.get("blocking_flags", []):
                blockers.append(f"{name}: {item}")
        return self._unique_texts(blockers)

    def _unique_texts(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
