from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

try:
    from urllib.request import ProxyHandler, build_opener
except ImportError:
    ProxyHandler = None
    build_opener = None

from toutiao_content_factory.config import AppSettings, RPAResilienceSettings
from toutiao_content_factory.constants.selectors import (
    WECHAT_LOGIN_PAGE,
    WECHAT_EDITOR_PAGE,
    WECHAT_COVER_DIALOG,
    WECHAT_PUBLISH_DIALOG,
    WECHAT_DISMISS_DIALOG,
    WECHAT_ORIGINAL_DECLARATION,
    WECHAT_SESSION_READY_MARKERS,
    WECHAT_LOGIN_URL_MARKERS,
    WECHAT_DISMISS_KEYWORDS,
    WECHAT_DISMISS_ACTIONS,
    WECHAT_PUBLISH_CONFIRM_TEXTS,
    WECHAT_PUBLISH_BUTTON_TEXTS,
    WECHAT_SUCCESS_MARKERS,
    WECHAT_DRAFT_LIST_PAGE,
)
from toutiao_content_factory.exceptions import SessionExpiredException
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.browser_launch_guard import (
    backup_browser_storage_state,
    ensure_browser_session_ready,
    prepare_browser_profile,
)
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.services.md2wechat_service import Md2WechatService
from toutiao_content_factory.services.html_anything_service import HtmlAnythingService
from toutiao_content_factory.services.monetization_service import MonetizationService
from toutiao_content_factory.services.notification_service import NotificationService
from toutiao_content_factory.services.wechat_layout_engine import MarkdownToWechatConverter, append_wechat_footer
from toutiao_content_factory.services.anti_ai_flavor import AntiAIFlavorPipeline
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe
from toutiao_content_factory.utils.browser_utils import apply_stealth, build_persistent_context_options

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PlaywrightTimeoutError = Exception
    PlaywrightError = Exception
    sync_playwright = None

# 模块 A 合并新增：tenacity 动态重试装饰器
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class WechatIpWhitelistBlockedException(RuntimeError):
    def __init__(self, message: str, *, ip_address: str = "", payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.ip_address = ip_address
        self.payload = payload or {}


WECHAT_EDITOR_135_DISABLED_MESSAGE = (
    "135 Editor publishing is disabled. Use the WeChat Official Account API "
    "or the WeChat backend browser workflow instead."
)


class WechatPublishService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.history_path = settings.output_dir / settings.wechat.history_filename
        self.md2wechat_service = Md2WechatService(settings, logger)
        self.html_anything_service = HtmlAnythingService(settings, logger)
        self.monetization_service = MonetizationService(settings, logger)
        self.notification_service = NotificationService(
            dict(os.environ),
            fallback_log_dir=str(self.settings.output_dir / "automation"),
        )
        self.growth_ops_service = GrowthOpsService(settings, logger)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        # 模块 A 合并新增：读取 rpa_resilience 配置，若无则使用默认值（向下兼容）
        self.rpa_config = getattr(settings, "rpa_resilience", RPAResilienceSettings())

    def _launch_persistent_browser(self, playwright, *, profile_dir: Path, headless: bool | None = None):
        launch_options, fingerprint = build_persistent_context_options(
            account_key=f"{self.settings.account_key}-wechat",
            profile_dir=profile_dir,
            headless=self.settings.publishing.headless if headless is None else bool(headless),
            channel=self.settings.publishing.channel or None,
            executable_path=self.settings.publishing.executable_path or None,
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
        )
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_options,
        )
        apply_stealth(browser, fingerprint=fingerprint)
        return browser

    def _ensure_browser_session_ready(self, browser, page, probe_url: str) -> dict[str, Any]:
        summary = ensure_browser_session_ready(
            browser_context=browser,
            page=page,
            platform="WeChat",
            profile_dir=self._resolve_browser_profile_dir(),
            account_runtime_dir=self.settings.account_runtime_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            probe_url=probe_url,
            login_detected_fn=self._is_login_page,
            session_ready_fn=self._browser_session_ready,
            notification_service=self.notification_service,
            goto_timeout_ms=max(self.settings.publishing.wait_timeout_ms, 20000),
        )
        if summary.get("login_detected") or not summary.get("session_ready"):
            raise RuntimeError(
                "WeChat browser session is invalid or expired. "
                f"Current URL: {summary.get('current_url', '')}. "
                "Operator has been notified to refresh the QR login."
            )
        return summary

    def _get_retry_decorator(self):
        """模块 A 合并新增：动态生成基于配置的 tenacity 重试装饰器。"""
        return retry(
            stop=stop_after_attempt(self.rpa_config.max_retries),
            wait=wait_exponential(
                multiplier=self.rpa_config.retry_backoff_seconds,
                min=2,
                max=10,
            ),
            retry=retry_if_exception_type((PlaywrightError, TimeoutError, PlaywrightTimeoutError)),
            reraise=True,
            before_sleep=lambda retry_state: self.logger.warning(
                f"RPA 操作失败，正在进行第 {retry_state.attempt_number} 次重试..."
            ),
        )

    def _defend_popups(self, page) -> int:
        """模块 A 合并新增：主动拦截并关闭微信后台的干扰弹窗。

        返回关闭的弹窗数量。
        """
        if not self.rpa_config.popup_defense_enabled:
            return 0

        closed_count = 0
        popup_selectors = [
            "text='我知道了'",
            "text='确定'",
            ".modal-close",
            ".dialog-close-btn",
            "text='关闭'",
            ".editor-guide-close",
            "text='下次再说'",
        ]

        for selector in popup_selectors:
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=500):
                    element.click()
                    closed_count += 1
                    self.logger.info(f"弹窗防御机制：成功关闭弹窗 [{selector}]")
            except Exception:
                continue
        return closed_count

    def publish_run(self, run_dir: Path, submit: bool = False, force_editor_135: bool = False) -> dict[str, Path]:
        if force_editor_135:
            raise RuntimeError(WECHAT_EDITOR_135_DISABLED_MESSAGE)
        self._ensure_enabled()
        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        review_status = self._load_json(run_dir / "review_status.json")
        if not publish_meta:
            raise FileNotFoundError("publish_meta.json is missing.")
        draft_only = self.settings.wechat.draft_only
        if not draft_only and str(review_status.get("status", "")) != "approved":
            raise RuntimeError("Run is not approved yet. Approve review before publishing.")

        title = self._clip_title(str(publish_meta.get("selected_title", "")).strip())
        summary = str(publish_meta.get("summary", "")).strip()
        article = self._read_text(publish_dir / "article.md")
        if self.settings.wechat_auto_publish_mode and not draft_only and publish_meta.get("approval_mode") != "manual":
            from toutiao_content_factory.services.wechat_revision import assert_run_automatic_approval
            assert_run_automatic_approval(run_dir, article, title)
        if self.settings.wechat_auto_publish_mode and not draft_only:
            from toutiao_content_factory.services.wechat_execution import require_submission_budget
            require_submission_budget()

        try:
            pipeline = AntiAIFlavorPipeline()
            result = pipeline.process(article)
            density = result["density"]
            if not self.settings.wechat_auto_publish_mode and not draft_only:
                article = result["processed_text"]
            self.logger.info(
                "WeChat compliance: density_score=%d, noun_ratio=%.3f, verb_ratio=%.3f, avg_sent_len=%.1f",
                density["score"], density["noun_ratio"], density["verb_ratio"], density["avg_sentence_length"],
            )
            if density["score"] < 50:
                self.logger.warning(
                    "WeChat info density borderline (%d/100). Article may be flagged as low-value AIGC.",
                    density["score"],
                )
        except Exception as exc:
            self.logger.warning("WeChat compliance check error (non-blocking): %s", exc)
        cover_path, inline_image_paths = self._resolve_image_assets(publish_dir, title=title)

        html_dir = publish_dir / "wechat"
        html_dir.mkdir(parents=True, exist_ok=True)
        payload_path = html_dir / "wechat_payload.json"
        article_markdown_path = html_dir / "article.md"
        article_html_path = html_dir / "article.html"
        result_path = html_dir / "wechat_publish_result.json"

        api_available = False
        access_token = ""
        thumb_media_id = ""
        inline_image_urls: list[str] = []
        draft_media_id = ""

        try:
            access_token = self._get_access_token()
            thumb_media_id = self._upload_cover_material(access_token, cover_path)
            inline_image_urls = [self._upload_inline_image(access_token, path) for path in inline_image_paths]
            api_available = True
            self.logger.info("WeChat API available, using API + browser hybrid mode.")
        except WechatIpWhitelistBlockedException as exc:
            self.logger.warning(
                "WeChat API blocked by IP whitelist, falling back to browser-only mode: %s", exc
            )
        except Exception as exc:
            self.logger.warning("WeChat API unavailable, falling back to browser-only mode: %s", exc)

        article_markdown = self._compose_article_markdown(article=article, inline_image_urls=inline_image_urls)
        article_markdown_path.write_text(article_markdown, encoding="utf-8")

        if api_available:
            article_html, layout_meta = self._render_article_html_via_backend(
                title=title,
                article=article,
                article_markdown=article_markdown,
                inline_image_urls=inline_image_urls,
            )
        else:
            article_html, layout_meta = self._render_article_html_via_backend(
                title=title,
                article=article,
                article_markdown=article_markdown,
                inline_image_urls=[],
            )
        article_html_path.write_text(article_html, encoding="utf-8")
        expected_publication = {}
        if self.settings.wechat_auto_publish_mode and not draft_only:
            from toutiao_content_factory.services.wechat_metrics import html_body_hash, assert_reviewed_body_preserved
            assert_reviewed_body_preserved(article, article_html)
            expected_publication = {"account": self.settings.wechat.account_name,
                                    "title": title, "body_hash": html_body_hash(article_html),
                                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

        if draft_only:
            from toutiao_content_factory.services.wechat_execution import write_phase
            write_phase(self.settings, "saving_draft")
        if api_available:
            payload = self._build_article_payload(
                title=title,
                summary=summary,
                article_html=article_html,
                thumb_media_id=thumb_media_id,
            )
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            draft_response = self._wechat_post_json(
                "/cgi-bin/draft/add",
                {"articles": [payload]},
                access_token=access_token,
            )
            draft_media_id = str(draft_response.get("media_id", "")).strip()
            if not draft_media_id:
                err_code = str(draft_response.get("ret", ""))
                if err_code == "200002" or not thumb_media_id:
                    self.logger.warning(
                        "Draft creation failed (ret=%s, thumb_media_id=%s). Falling back to browser-only mode.",
                        err_code,
                        thumb_media_id[:20] if thumb_media_id else "(empty)",
                    )
                    api_available = False
                    draft_media_id = ""
                else:
                    raise RuntimeError(f"Draft creation did not return media_id: {draft_response}")

        publish_response: dict[str, Any] = {}
        should_submit = not draft_only and (submit or self.settings.wechat.auto_publish)
        if should_submit:
            if self.settings.wechat_auto_publish_mode:
                from toutiao_content_factory.services.wechat_execution import write_phase, require_submission_budget
                if publish_meta.get("approval_mode") != "manual":
                    assert_run_automatic_approval(run_dir, article, title)
                require_submission_budget()
                integrity = self._load_json(run_dir / "quality_review_council.json").get("audit_integrity", {})
                write_phase(self.settings, "submitting", fingerprint=integrity.get("fingerprint", ""))
            if api_available and draft_media_id:
                publish_response = self._submit_draft_via_browser(
                    draft_media_id=draft_media_id,
                    title=title,
                )
        if not api_available:
            publish_response = self._publish_via_browser_only(
                title=title,
                summary=summary,
                article_html=article_html,
                cover_path=cover_path,
                submit=should_submit,
            )
        result_payload = {
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "platform": "wechat",
            "run_dir": str(run_dir),
            "status": str(publish_response.get("status", "")).strip() if publish_response else ("draft_saved" if draft_only else "draft_created"),
            "draft_status": "created" if api_available else "browser_only",
            "submit_status": str(publish_response.get("status", "")).strip() if publish_response else "",
            "draft_media_id": draft_media_id,
            "thumb_media_id": thumb_media_id,
            "inline_image_urls": inline_image_urls,
            "layout_backend": layout_meta.get("provider", "builtin"),
            "layout_request": layout_meta.get("request", {}),
            "submit_requested": should_submit,
            "publish_response": publish_response,
            "external_url": str(publish_response.get("publish_url", "")).strip() if publish_response else "",
            "original_declared": bool(publish_response.get("original_declared", False)) if publish_response else False,
            "title": title,
            "api_available": api_available,
            **({"draft_only": True, "manual_publication_required": True, "publication_attempted": False,
                "review_status": str(review_status.get("status", "pending")),
                "app_msg_id": str(publish_response.get("app_msg_id", "")),
                "saved_at": datetime.now(timezone.utc).isoformat()} if draft_only else {}),
            **({"expected_publication": expected_publication} if expected_publication else {}),
        }
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_history(result_payload)
        growth_exports = self.growth_ops_service.register_publish_event(
            platform="wechat",
            run_dir=run_dir,
            result_path=result_path,
        )
        try:
            from toutiao_content_factory.services.revenue_attribution_service import (
                RevenueAttributionService,
            )

            attribution_service = RevenueAttributionService(self.settings.output_dir, self.logger)
            identity = attribution_service.register_published_run(
                account_key=self.settings.account_key,
                platform="wechat",
                run_dir=run_dir,
                publish_dirname=self.settings.review.publish_dirname,
                title=title,
                published_at=str(result_payload.get("published_at", "")),
                topic=publish_meta.get("selected_topic") or {},
                result_payload=result_payload,
            )
            growth_exports["content_id"] = identity.content_id
            growth_exports["content_index"] = str(attribution_service.content_index_path)
            growth_exports["cost_ledger"] = str(attribution_service.cost_ledger_path)
        except Exception as exc:  # pragma: no cover - attribution best-effort
            self.logger.warning("WeChat revenue attribution registration failed (non-fatal): %s", exc)

        self.logger.info("Wechat publish completed for run: %s (api=%s)", run_dir, api_available)
        return {
            "wechat_payload": payload_path,
            "wechat_article_markdown": article_markdown_path,
            "wechat_article_html": article_html_path,
            "wechat_publish_result": result_path,
            "wechat_history": self.history_path,
            **growth_exports,
        }

    def publish_run_via_135(self, run_dir: Path, submit: bool = True) -> dict[str, Path]:
        raise RuntimeError(WECHAT_EDITOR_135_DISABLED_MESSAGE)

    def initialize_login_session(self, *, headless: bool | None = None) -> dict[str, str]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="wechat:init-session",
            extra_paths=[self.settings.account_runtime_dir],
        )

        with sync_playwright() as playwright:
            browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir, headless=headless)
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                session_summary = ensure_browser_session_ready(
                    browser_context=browser,
                    page=page,
                    platform="WeChat",
                    profile_dir=self._resolve_browser_profile_dir(),
                    account_runtime_dir=self.settings.account_runtime_dir,
                    project_root=self.settings.project_root,
                    logger=self.logger,
                    probe_url=self.settings.wechat.base_url,
                    login_detected_fn=self._is_login_page,
                    session_ready_fn=self._browser_session_ready,
                    notification_service=self.notification_service,
                    goto_timeout_ms=max(self.settings.publishing.wait_timeout_ms, 20000),
                )
                if not session_summary.get("session_ready") or session_summary.get("login_detected"):
                    print("")
                    print("WeChat session is not ready. Notification has been sent to the operator.")
                    print("Please scan the QR code in the opened browser window within 10 minutes.")
                    deadline = time.time() + 600
                    while time.time() < deadline:
                        if self._browser_session_ready(page):
                            break
                        page.wait_for_timeout(1500)
                    if not self._browser_session_ready(page):
                        raise RuntimeError("WeChat login session was not confirmed within 10 minutes.")
                    backup = backup_browser_storage_state(
                        browser,
                        platform="WeChat",
                        profile_dir=profile_dir,
                        account_runtime_dir=self.settings.account_runtime_dir,
                        logger=self.logger,
                    )
                    if not backup.get("saved"):
                        raise RuntimeError("WeChat QR login succeeded, but its session backup could not be saved.")
                summary = {
                    "profile_dir": str(profile_dir),
                    "current_url": page.url,
                    "state": "ready",
                    "healed": bool(session_summary.get("healed", False)),
                }
                self.settings.automation_dir.mkdir(parents=True, exist_ok=True)
                (self.settings.automation_dir / "wechat_publish_preflight.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return summary
            finally:
                browser.close()

    def _ensure_enabled(self) -> None:
        wechat = self.settings.wechat
        if not wechat.enabled:
            raise RuntimeError("WeChat publishing is disabled. Set WECHAT_ENABLED=1 first.")
        if not wechat.app_id or not wechat.app_secret:
            self.logger.warning("WeChat AppID/AppSecret are not configured. API features will be unavailable; falling back to browser-only mode.")

    def _resolve_browser_profile_dir(self) -> Path:
        return self.settings.account_runtime_dir / "wechat_profile"

    def _resolve_image_assets(self, publish_dir: Path, title: str = "") -> tuple[Path, list[Path]]:
        publish_images_dir = publish_dir / "images"
        run_images_dir = publish_dir.parent / "images"
        image_roots = [publish_images_dir, run_images_dir]

        inline_image_paths: list[Path] = []
        for image_name in ("infographic_1.png", "infographic_2.png"):
            for root in image_roots:
                candidate = root / image_name
                if candidate.exists():
                    inline_image_paths.append(candidate)
                    break

        strategy = self.settings.wechat.cover_strategy
        cover_path: Path | None = None

        if strategy == "first_inline" and inline_image_paths:
            cover_path = inline_image_paths[0]
            self.logger.info("WeChat cover strategy=first_inline, using: %s", cover_path.name)
        else:
            for root in image_roots:
                candidate = root / "cover.png"
                if candidate.exists():
                    cover_path = candidate
                    break
            if cover_path and strategy == "first_inline":
                self.logger.info(
                    "WeChat cover strategy=first_inline but no inline images, falling back to: %s",
                    cover_path.name,
                )

        if cover_path is None:
            cover_path = self._generate_text_cover(publish_dir, title=title)
            if cover_path is None:
                raise FileNotFoundError(
                    f"Cover image not found under: {publish_images_dir} or {run_images_dir}"
                )

        return cover_path, inline_image_paths

    def _generate_text_cover(self, publish_dir: Path, title: str = "") -> Path | None:
        """当没有封面图时，自动生成一个简单的纯色文字封面。"""
        try:
            from PIL import Image, ImageDraw, ImageFont

            cover_dir = publish_dir / "images"
            cover_dir.mkdir(parents=True, exist_ok=True)
            cover_path = cover_dir / "cover.png"

            width, height = 900, 500
            img = Image.new("RGB", (width, height), color=(30, 60, 120))
            draw = ImageDraw.Draw(img)

            font_size = 40
            try:
                font = ImageFont.truetype("msyh.ttc", font_size)
            except Exception:
                font = ImageFont.load_default()

            display_title = title or "AI科技观察"
            max_chars = 14
            lines = [display_title[i:i + max_chars] for i in range(0, len(display_title), max_chars)]
            line_height = font_size + 12
            total_height = len(lines) * line_height
            y_start = (height - total_height) // 2

            for idx, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=font)
                tw = bbox[2] - bbox[0]
                x = (width - tw) // 2
                y = y_start + idx * line_height
                draw.text((x, y), line, fill=(255, 255, 255), font=font)

            img.save(str(cover_path), "PNG")
            self.logger.info("Generated text cover image: %s", cover_path)
            return cover_path
        except Exception as exc:
            self.logger.warning("Failed to generate text cover image: %s", exc)
            return None

    def _build_article_payload(
        self,
        *,
        title: str,
        summary: str,
        article_html: str,
        thumb_media_id: str,
    ) -> dict[str, Any]:
        wechat = self.settings.wechat
        return {
            "title": title,
            "author": wechat.author_name or self.settings.account_label,
            "digest": self._clip_digest(summary or article_html),
            "content": article_html,
            "content_source_url": "",
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
            "show_cover_pic": 1,
        }

    def _render_article_html(self, *, title: str, article: str, inline_image_urls: list[str]) -> str:
        blocks = self._build_article_blocks(title=title, article=article)
        text_blocks = [block for block in blocks if block["type"] in {"lead", "paragraph", "closing"}]
        image_markers = self._resolve_inline_image_targets(len(text_blocks), len(inline_image_urls))
        html_parts: list[str] = [
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;"
            "line-height:1.9;color:#1f2329;font-size:16px;letter-spacing:0.02em;\">"
        ]
        image_index = 0
        text_index = 0
        for block in blocks:
            block_type = block["type"]
            if block_type == "heading":
                html_parts.append(self._render_heading_block(block["text"]))
                continue
            if block_type == "divider":
                html_parts.append(self._render_divider_block())
                continue

            html_parts.append(self._render_text_block(block["text"], block_type))
            text_index += 1
            if text_index in image_markers and image_index < len(inline_image_urls):
                html_parts.append(self._render_image_block(inline_image_urls[image_index]))
                image_index += 1
        while image_index < len(inline_image_urls):
            html_parts.append(self._render_image_block(inline_image_urls[image_index]))
            image_index += 1
        html_parts.append("</div>")
        return "\n".join(html_parts)

    def _render_article_html_via_backend(
        self,
        *,
        title: str,
        article: str,
        article_markdown: str,
        inline_image_urls: list[str],
    ) -> tuple[str, dict[str, Any]]:
        clean_markdown = self._strip_wechat_cta_markdown(article_markdown)
        cta_payload = self._build_wechat_cta_payload()
        theme = self._resolve_builtin_wechat_theme()
        brand_name = (
            self.settings.wechat.author_name
            or self.settings.wechat.account_name
            or self.settings.brand_name
        )

        # 优先级1: md2wechat（外部 API/CLI 服务）
        if self.md2wechat_service.available:
            try:
                payload = self.md2wechat_service.convert(clean_markdown)
                html = append_wechat_footer(
                    str(payload.get("html", "")).strip(),
                    theme=theme,
                    brand_name=brand_name,
                    cta_payload=cta_payload,
                )
                payload = dict(payload)
                payload["html"] = html
                return html, payload
            except Exception as exc:
                if not self.settings.wechat.md2wechat_fallback_to_builtin:
                    raise
                self.logger.warning("md2wechat conversion failed, falling back: %s", exc)
        elif self.md2wechat_service.enabled:
            self.logger.warning(
                "md2wechat backend enabled but neither API key nor local CLI is available; falling back."
            )

        # 优先级2: html-anything（本地 Agent 排版服务，75 套模板）
        if self.html_anything_service.enabled:
            try:
                style = getattr(self.settings.writing, "default_style", "")
                payload = self.html_anything_service.convert(clean_markdown, style=style)
                html = append_wechat_footer(
                    str(payload.get("html", "")).strip(),
                    theme=theme,
                    brand_name=brand_name,
                    cta_payload=cta_payload,
                )
                payload = dict(payload)
                payload["html"] = html
                return html, payload
            except Exception as exc:
                self.logger.warning("html-anything conversion failed, falling back to builtin: %s", exc)

        # 优先级3: 内置 WeChat layout engine
        try:
            theme_name = self.settings.wechat.builtin_theme
            converter = MarkdownToWechatConverter(
                theme=theme,
                brand_name=brand_name,
                cta_payload=cta_payload,
            )
            html = converter.convert(clean_markdown)
            return html, {"provider": "wechat_layout_engine", "theme": theme_name}
        except Exception as exc:
            self.logger.warning("WeChat layout engine failed, falling back to builtin renderer: %s", exc)

        # 优先级4: 最基础 fallback
        html = self._render_article_html(title=title, article=article, inline_image_urls=inline_image_urls)
        html = append_wechat_footer(
            html,
            theme=theme,
            brand_name=brand_name,
            cta_payload=cta_payload,
        )
        return html, {"provider": "builtin_fallback"}

    def _compose_article_markdown(self, *, article: str, inline_image_urls: list[str]) -> str:
        blocks = self._build_article_blocks(title="", article=article)
        text_blocks = [block for block in blocks if block["type"] in {"lead", "paragraph", "closing"}]
        image_markers = self._resolve_inline_image_targets(len(text_blocks), len(inline_image_urls))
        markdown_parts: list[str] = []
        image_index = 0
        text_index = 0
        for block in blocks:
            block_type = block["type"]
            if block_type == "heading":
                markdown_parts.append(f"## {block['text']}")
                continue
            if block_type == "divider":
                markdown_parts.append("---")
                continue

            markdown_parts.append(block["text"])
            text_index += 1
            if text_index in image_markers and image_index < len(inline_image_urls):
                markdown_parts.append(f"![]({inline_image_urls[image_index]})")
                image_index += 1
        while image_index < len(inline_image_urls):
            markdown_parts.append(f"![]({inline_image_urls[image_index]})")
            image_index += 1
        return "\n\n".join(part for part in markdown_parts if part.strip()).strip()

    def _resolve_builtin_wechat_theme(self):
        from toutiao_content_factory.services.wechat_layout_engine import THEME_PRESETS

        theme_name = self.settings.wechat.builtin_theme
        theme = THEME_PRESETS.get(theme_name)
        if theme is None:
            self.logger.warning("Theme '%s' not found, using default theme", theme_name)
            theme = THEME_PRESETS.get("tech_blue")
        return theme

    def _strip_wechat_cta_markdown(self, markdown: str) -> str:
        marker = MonetizationService.CTA_MARKER
        normalized = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized or marker not in normalized:
            return normalized
        return normalized.split(marker, 1)[0].rstrip()

    def _build_wechat_cta_payload(self) -> dict[str, Any]:
        try:
            return self.monetization_service.get_cta_payload(platform_key="wechat")
        except Exception as exc:
            self.logger.warning("Failed to load WeChat CTA payload, continuing without CTA card: %s", exc)
            return {}

    def _render_image_block(self, image_url: str) -> str:
        escaped = html.escape(image_url, quote=True)
        return (
            "<section style=\"margin:24px 0 26px;\">"
            "<p style=\"margin:0;text-align:center;\">"
            f"<img src=\"{escaped}\" style=\"width:100%;max-width:100%;height:auto;border-radius:14px;display:block;margin:0 auto;box-shadow:0 8px 24px rgba(15,46,91,0.12);\" />"
            "</p>"
            "</section>"
        )

    def _build_article_blocks(self, *, title: str, article: str) -> list[dict[str, str]]:
        normalized = str(article or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            fallback = clean_article_text(article, [title]).strip() or title.strip()
            return [{"type": "lead", "text": fallback}] if fallback else []

        raw_blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
        blocks: list[dict[str, str]] = []
        title_norm = self._normalize_compare_text(title)
        seen_body = False
        for raw_block in raw_blocks:
            if re.fullmatch(r"[-=*_]{3,}", raw_block):
                if blocks and blocks[-1]["type"] != "divider":
                    blocks.append({"type": "divider", "text": ""})
                continue

            heading_with_body_match = re.match(r"^\s*#{1,6}\s*(.+?)\s*(?:\n+)(.+)$", raw_block, flags=re.S)
            if heading_with_body_match:
                heading_text = self._clean_block_text(heading_with_body_match.group(1))
                body_text = self._clean_block_text(heading_with_body_match.group(2))
                if heading_text and self._normalize_compare_text(heading_text) != title_norm:
                    blocks.append({"type": "heading", "text": heading_text})
                if body_text:
                    paragraph_type = "lead" if not seen_body else "paragraph"
                    for idx, paragraph in enumerate(self._split_paragraph_for_wechat(body_text)):
                        current_type = paragraph_type if idx == 0 else "paragraph"
                        blocks.append({"type": current_type, "text": paragraph})
                        if current_type in {"lead", "paragraph"}:
                            seen_body = True
                continue

            heading_match = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", raw_block)
            if heading_match:
                heading_text = self._clean_block_text(heading_match.group(1))
                if heading_text and self._normalize_compare_text(heading_text) != title_norm:
                    blocks.append({"type": "heading", "text": heading_text})
                continue

            block_text = self._clean_block_text(raw_block)
            if not block_text:
                continue
            if not blocks and title_norm and self._normalize_compare_text(block_text) == title_norm:
                continue

            heading_text, body_text = self._extract_heading_from_paragraph(block_text)
            if heading_text:
                blocks.append({"type": "heading", "text": heading_text})
                if body_text:
                    paragraph_type = "lead" if not seen_body else "paragraph"
                    for idx, paragraph in enumerate(self._split_paragraph_for_wechat(body_text)):
                        current_type = paragraph_type if idx == 0 else "paragraph"
                        blocks.append({"type": current_type, "text": paragraph})
                        if current_type in {"lead", "paragraph"}:
                            seen_body = True
                continue

            paragraph_type = "lead" if not seen_body else "paragraph"
            if self._looks_like_closing_cta(block_text):
                paragraph_type = "closing"
            if paragraph_type == "closing":
                blocks.append({"type": "closing", "text": block_text})
                continue
            segments = self._split_paragraph_for_wechat(block_text)
            for idx, paragraph in enumerate(segments):
                current_type = paragraph_type if idx == 0 else ("paragraph" if paragraph_type != "closing" else "closing")
                blocks.append({"type": current_type, "text": paragraph})
                if current_type in {"lead", "paragraph"}:
                    seen_body = True

        if not blocks:
            fallback = clean_article_text(article, [title]).strip() or title.strip()
            return [{"type": "lead", "text": fallback}] if fallback else []
        return blocks

    def _render_heading_block(self, text: str) -> str:
        return (
            "<section style=\"margin:28px 0 14px;\">"
            "<p style=\"margin:0;\">"
            "<span style=\"display:inline-block;padding:6px 12px 6px 11px;border-left:4px solid #0f4c81;"
            "background:linear-gradient(90deg,rgba(15,76,129,0.10),rgba(15,76,129,0.03));"
            "color:#0f3d73;font-size:17px;font-weight:700;line-height:1.55;\">"
            f"{self._format_inline_rich_text(text)}"
            "</span>"
            "</p>"
            "</section>"
        )

    def _render_divider_block(self) -> str:
        return (
            "<section style=\"margin:26px 0 22px;text-align:center;\">"
            "<span style=\"display:inline-block;width:42px;height:2px;background:#d3dce6;border-radius:999px;\"></span>"
            "</section>"
        )

    def _render_text_block(self, text: str, block_type: str) -> str:
        content = self._format_inline_rich_text(text)
        if block_type == "lead":
            return (
                "<section style=\"margin:0 0 22px;\">"
                "<div style=\"padding:18px 18px 16px;border-radius:16px;background:linear-gradient(180deg,#f5f9ff 0%,#eef4fb 100%);"
                "border:1px solid rgba(15,76,129,0.10);box-shadow:0 6px 18px rgba(15,46,91,0.06);\">"
                f"<p style=\"margin:0;color:#17324d;font-size:17px;line-height:1.95;font-weight:500;\">{content}</p>"
                "</div>"
                "</section>"
            )
        if block_type == "closing":
            return (
                "<section style=\"margin:26px 0 0;\">"
                "<div style=\"padding:16px 18px;border-radius:14px;background:#fafbfd;border:1px solid #e4eaf1;\">"
                f"<p style=\"margin:0;color:#2b3a49;font-size:16px;line-height:1.9;\">{content}</p>"
                "</div>"
                "</section>"
            )
        return f"<p style=\"margin:0 0 18px;color:#222;line-height:1.92;text-align:justify;\">{content}</p>"

    def _clean_block_text(self, block: str) -> str:
        lines: list[str] = []
        for raw_line in str(block or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^\s*#{1,6}\s*", "", line)
            line = re.sub(r"^\s*[-*+]\s+", "", line)
            line = re.sub(r"^\s*\d+\.\s+", "", line)
            line = re.sub(r"^\s*>\s*", "", line)
            if re.fullmatch(r"[-=*_]{3,}", line):
                continue
            lines.append(line)
        joined = " ".join(lines).strip()
        joined = re.sub(r"\s+", " ", joined)
        joined = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", joined)
        joined = re.sub(r"`([^`]+)`", r"\1", joined)
        return joined.strip()

    def _extract_heading_from_paragraph(self, text: str) -> tuple[str, str]:
        compact = str(text or "").strip()
        if not compact:
            return "", ""
        question_match = re.match(r"^((?:为什么|为何|怎么|如何|哪些|谁在|什么是)[^。！？!?]{0,20}[？?])\s*(.+)$", compact)
        if question_match:
            return question_match.group(1).strip(), question_match.group(2).strip()

        sentence_parts = re.split(r"(?<=[。！？!?])", compact, maxsplit=1)
        if len(sentence_parts) != 2:
            return "", compact
        first_sentence = sentence_parts[0].strip()
        remainder = sentence_parts[1].strip()
        if not remainder:
            return "", compact
        heading_like = (
            6 <= len(first_sentence) <= 22
            and any(
                token in first_sentence
                for token in (
                    "真正",
                    "核心",
                    "关键",
                    "风险",
                    "结论",
                    "先看",
                    "最后",
                    "重点",
                    "订单",
                    "融资",
                    "供应链",
                    "海外",
                    "估值",
                    "场景",
                    "交付",
                    "变化",
                    "方面",
                    "加速",
                    "转向",
                )
            )
        )
        if heading_like:
            return first_sentence.rstrip("。"), remainder
        return "", compact

    def _split_paragraph_for_wechat(self, text: str) -> list[str]:
        compact = str(text or "").strip()
        if not compact:
            return []
        if len(compact) <= 88:
            return [compact]

        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", compact) if part.strip()]
        if len(sentences) <= 1:
            fragments = [part.strip() for part in re.split(r"(?<=[，；：])", compact) if part.strip()]
            if len(fragments) <= 1:
                return [compact]
            sentences = fragments

        segments: list[str] = []
        current = ""
        current_count = 0
        for sentence in sentences:
            candidate = f"{current}{sentence}" if current else sentence
            if current and (len(candidate) > 82 or current_count >= 2):
                segments.append(current.strip())
                current = sentence
                current_count = 1
            else:
                current = candidate
                current_count += 1
        if current.strip():
            segments.append(current.strip())
        return segments or [compact]

    def _looks_like_closing_cta(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in (
                "欢迎留言",
                "欢迎评论",
                "你怎么看",
                "你更看好",
                "欢迎分享",
                "你可以回复关键词",
                "回复关键词",
                "后续系列中持续跟踪",
            )
        )

    def _format_inline_rich_text(self, text: str) -> str:
        raw = str(text or "").strip()
        placeholders: dict[str, str] = {}

        def store(fragment: str) -> str:
            token = f"@@WXHTML{len(placeholders)}@@"
            placeholders[token] = fragment
            return token

        prepared = raw
        prepared = re.sub(
            r"\*\*(.+?)\*\*",
            lambda match: store(
                f"<strong style=\"color:#0f3d73;font-weight:700;\">{html.escape(match.group(1).strip())}</strong>"
            ),
            prepared,
        )
        prepared = re.sub(
            r"__(.+?)__",
            lambda match: store(
                f"<strong style=\"color:#0f3d73;font-weight:700;\">{html.escape(match.group(1).strip())}</strong>"
            ),
            prepared,
        )
        prepared = re.sub(
            r"(?<![\w])(?:Q[1-4]|[0-9]+(?:\.[0-9]+)?(?:%|％|万|亿|倍|年|个月|月|天|小时|分钟|台|家|次|亿元|亿美元))(?![\w])",
            lambda match: store(
                f"<strong style=\"color:#8a2e1c;font-weight:700;\">{html.escape(match.group(0))}</strong>"
            ),
            prepared,
        )

        escaped = html.escape(prepared)
        for token, fragment in placeholders.items():
            escaped = escaped.replace(html.escape(token), fragment)
        return escaped

    def _normalize_compare_text(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip().lstrip("#").strip())

    def _resolve_inline_image_targets(self, paragraph_count: int, image_count: int) -> list[int]:
        if paragraph_count <= 0 or image_count <= 0:
            return []
        if image_count == 1:
            return [min(1, paragraph_count)]
        first = min(1, paragraph_count)
        second = min(3, paragraph_count)
        if second <= first:
            second = min(paragraph_count, first + 1)
        targets = [first, second]
        if image_count > 2:
            targets.extend([paragraph_count] * (image_count - 2))
        return targets[:image_count]

    def _upload_cover_material(self, access_token: str, cover_path: Path) -> str:
        upload_path = self._ensure_rgb_image(cover_path)
        try:
            response = self._wechat_post_multipart(
                "/cgi-bin/material/add_material",
                access_token=access_token,
                fields={"type": "image"},
                files={"media": upload_path},
            )
        finally:
            if upload_path is not cover_path and upload_path.exists():
                with suppress(Exception):
                    upload_path.unlink()
        media_id = str(response.get("media_id", "")).strip()
        if not media_id:
            raise RuntimeError(f"Cover material upload failed: {response}")
        return media_id

    def _ensure_rgb_image(self, image_path: Path) -> Path:
        try:
            from PIL import Image as PILImage

            img = PILImage.open(image_path)
            if img.mode != "RGBA":
                return image_path
            rgb = PILImage.new("RGB", img.size, (255, 255, 255))
            rgb.paste(img, mask=img.split()[3])
            tmp = image_path.parent / f"_rgb_{image_path.name}"
            rgb.save(tmp, format="PNG")
            self.logger.info("Converted cover image RGBA→RGB for WeChat upload: %s", tmp.name)
            return tmp
        except ImportError:
            self.logger.warning("Pillow not available, uploading cover as-is (may fail if RGBA).")
            return image_path
        except Exception as exc:
            self.logger.warning("Cover RGBA→RGB conversion skipped: %s", exc)
            return image_path

    def _upload_inline_image(self, access_token: str, image_path: Path) -> str:
        response = self._wechat_post_multipart(
            "/cgi-bin/media/uploadimg",
            access_token=access_token,
            files={"media": image_path},
        )
        url = str(response.get("url", "")).strip()
        if not url:
            raise RuntimeError(f"Inline image upload failed: {response}")
        return url

    def _wechat_post_json(self, endpoint: str, payload: dict[str, Any], *, access_token: str) -> dict[str, Any]:
        url = self._build_api_url(endpoint, access_token=access_token)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
        return self._read_json_response(request)

    def _wechat_post_multipart(
        self,
        endpoint: str,
        *,
        access_token: str,
        fields: dict[str, str] | None = None,
        files: dict[str, Path],
    ) -> dict[str, Any]:
        boundary = f"----WechatBoundary{uuid.uuid4().hex}"
        body = bytearray()
        for name, value in (fields or {}).items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for name, path in files.items():
            mime = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode("utf-8")
            )
            body.extend(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
            body.extend(path.read_bytes())
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        url = self._build_api_url(endpoint, access_token=access_token)
        request = Request(url, data=bytes(body), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return self._read_json_response(request)

    def _read_json_response(self, request: Request) -> dict[str, Any]:
        # Cloudflare Worker 代理需要 User-Agent，否则被 Bot 检测拦截
        if not request.has_header("User-Agent"):
            request.add_header("User-Agent", "WechatProxy/1.0")
        # Serv00 代理密钥
        wechat = self.settings.wechat
        if wechat.serv00_proxy_enabled and wechat.serv00_proxy_secret:
            request.add_header("X-Proxy-Secret", wechat.serv00_proxy_secret)

        opener = self._build_opener()
        try:
            with opener.open(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"WeChat API request failed: {exc.code} {raw}") from exc
        except URLError as exc:
            raise RuntimeError(f"WeChat API request failed: {exc.reason}") from exc

        payload = json.loads(raw or "{}")
        if int(payload.get("errcode", 0) or 0) != 0:
            raise RuntimeError(f"WeChat API error: {payload}")
        return payload

    def _build_opener(self):
        proxy_url = getattr(self.settings.wechat, "api_proxy", "") or ""
        if proxy_url and ProxyHandler is not None and build_opener is not None:
            handler = ProxyHandler({"http": proxy_url, "https": proxy_url})
            self.logger.info("WeChat API using proxy: %s", proxy_url[:30])
            return build_opener(handler)
        from urllib.request import build_opener as _build_opener
        return _build_opener()

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        wechat = self.settings.wechat
        query = urlencode(
            {
                "grant_type": "client_credential",
                "appid": wechat.app_id,
                "secret": wechat.app_secret,
            }
        )
        # Serv00 代理：token 请求也走代理
        if wechat.serv00_proxy_enabled and wechat.serv00_proxy_url:
            base = wechat.serv00_proxy_url.rstrip("/")
        else:
            base = wechat.api_base_url.rstrip("/")
        url = f"{base}/cgi-bin/token?{query}"

        # Cloudflare Worker 代理出口 IP 不固定，遇到 40164 时自动重试
        max_retries = 5 if wechat.serv00_proxy_enabled else 1
        last_exc = None
        for attempt in range(1, max_retries + 1):
            request = Request(url)
            try:
                payload = self._read_json_response(request)
            except RuntimeError as exc:
                payload_text = str(exc)
                if "40164" in payload_text and "invalid ip" in payload_text.lower():
                    ip_address = self._extract_invalid_ip(payload_text)
                    last_exc = exc
                    if attempt < max_retries:
                        self.logger.warning(
                            "[WeChat] IP 白名单拦截 (attempt %d/%d), 出口 IP: %s, 自动重试...",
                            attempt, max_retries, ip_address or "unknown",
                        )
                        time.sleep(1)
                        continue
                    message = (
                        f"[WeChat] 微信公众号 API 调用被白名单拦截（已重试 {max_retries} 次），"
                        f"当前出口 IP: {ip_address or 'unknown'}。"
                        "请前往微信公众平台后台更新接口白名单。"
                    )
                    self.logger.warning(message)
                    raise WechatIpWhitelistBlockedException(message, ip_address=ip_address) from exc
                if "40125" in payload_text and "invalid appsecret" in payload_text.lower():
                    from toutiao_content_factory.exceptions import WechatAuthError
                    message = (
                        "[WeChat] 微信公众号 AppSecret 验证失败 (errcode=40125)。"
                        "请检查: 1) WECHAT_APP_ID 和 WECHAT_APP_SECRET 是否正确; "
                        "2) 微信后台是否已重置 AppSecret; "
                        "3) .env 文件中是否有拼写错误或多余空格。"
                    )
                    self.logger.error(message)
                    raise WechatAuthError(message) from exc
                raise
            break
        token = str(payload.get("access_token", "")).strip()
        expires_in = int(payload.get("expires_in", 7200) or 7200)
        if not token:
            raise RuntimeError(f"WeChat access_token request returned no token: {payload}")
        self._access_token = token
        self._access_token_expires_at = now + max(300, expires_in - 120)
        return token

    def _extract_invalid_ip(self, message: str) -> str:
        match = re.search(r"invalid ip\s+([0-9\.]+)", str(message), flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _build_api_url(self, endpoint: str, *, access_token: str) -> str:
        wechat = self.settings.wechat
        if wechat.serv00_proxy_enabled and wechat.serv00_proxy_url:
            base = wechat.serv00_proxy_url.rstrip("/")
            self.logger.debug("Using Serv00 proxy: %s", base)
        else:
            base = wechat.api_base_url.rstrip("/")
        return f"{base}{endpoint}?access_token={access_token}"

    def _clip_title(self, title: str) -> str:
        max_length = max(1, int(self.settings.wechat.title_max_length))
        cleaned = re.sub(r"\s+", " ", str(title or "").strip())
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[: max_length - 1].rstrip("，,。！？!?；;：:") + "…"

    def _clip_digest(self, digest: str) -> str:
        max_length = max(1, int(self.settings.wechat.digest_max_length))
        cleaned = re.sub(r"\s+", " ", str(digest or "").strip())
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[: max_length - 1].rstrip("，,。！？!?；;：:") + "…"

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def _append_history(self, payload: dict[str, Any]) -> None:
        history: list[dict[str, Any]] = []
        if self.history_path.exists():
            try:
                history = json.loads(self.history_path.read_text(encoding="utf-8"))
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []
        history.append(payload)
        self.history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    def initialize_135editor_login_session(self) -> dict[str, str]:
        """初始化135编辑器登录会话。"""
        raise RuntimeError(WECHAT_EDITOR_135_DISABLED_MESSAGE)

    def _publish_via_browser_only(
        self,
        *,
        title: str,
        summary: str,
        article_html: str,
        cover_path: Path,
        submit: bool = True,
    ) -> dict[str, Any]:
        submit = submit and not self.settings.wechat.draft_only
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed.")

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="wechat:browser-only",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run() -> dict[str, Any]:
            with sync_playwright() as playwright:
                browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir)
                page = None
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    self._ensure_browser_session_ready(browser, page, self.settings.wechat.base_url)
                    self._raise_if_session_expired(page)
                    token = self._extract_token(page)

                    app_msg_id = self._browser_create_draft(
                        page, token, title, summary, article_html, cover_path,
                    )

                    if not app_msg_id:
                        self.logger.info("Falling back to editor-based draft creation.")
                        app_msg_id = self._browser_create_draft_via_editor(
                            page, token, title, summary, article_html, cover_path,
                        )

                    if not app_msg_id:
                        raise RuntimeError("WeChat draft creation did not return an appMsgId.")

                    original_declared = False
                    if submit:
                        # 用已保存的草稿 ID 打开编辑器，避免按标题选错文章。
                        # Open the exact saved draft instead of selecting a duplicate title.
                        editor_url = (
                            f"{self.settings.wechat.base_url.rstrip('/')}/cgi-bin/appmsg"
                            f"?t=media/appmsg_edit&action=edit&type=77&appmsgid={app_msg_id}"
                            f"&token={token}&lang=zh_CN"
                        )
                        page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)
                        self._raise_if_session_expired(page)
                        self._click_publish_button_on_wechat_page(page)
                        original_declared = self._confirm_wechat_publish_dialog(page)
                        self._wait_for_wechat_publish_success(page)

                    result = {
                        "status": "submitted" if submit else "draft_saved",
                        "method": "browser_only",
                        "title": title,
                        "publish_url": page.url,
                        "app_msg_id": app_msg_id or "",
                        "original_declared": original_declared,
                    }
                    return result
                except SessionExpiredException:
                    self.logger.error("微信公众号 SessionExpiredException: 登录态已失效，需要重新登录")
                    raise
                except Exception as exc:
                    debug_dir = self._capture_browser_debug_artifacts(page, {"title": title, "method": "browser_only"})
                    raise RuntimeError(
                        f"WeChat browser-only publish failed: {self._clean_visible_text(exc)}. Debug files saved to: {debug_dir}"
                    ) from exc
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=300)

    def _extract_token(self, page) -> str:
        token_match = re.search(r"token=(\d+)", page.url)
        return token_match.group(1) if token_match else ""

    def _browser_create_draft(
        self,
        page,
        token: str,
        title: str,
        summary: str,
        article_html: str,
        cover_path: Path,
    ) -> str | None:
        self.logger.info("Creating draft via operate_appmsg API in browser context...")

        # Navigate to editor page to ensure we're on mp.weixin.qq.com domain
        # and can access the fingerprint from the page context
        base = self.settings.wechat.base_url.rstrip("/")
        editor_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&token={token}&lang=zh_CN"
        page.goto(editor_url, wait_until="commit", timeout=30000)
        page.wait_for_function(
            "() => !!document.querySelector('#title') && "
            "!!document.querySelector('.ProseMirror, [contenteditable=\"true\"]')",
            timeout=30000,
        )

        cover_file_id, cover_cdn_url = self._browser_upload_cover(page, token, cover_path)
        if not cover_file_id:
            self.logger.warning("Cover upload failed, draft may not be publishable.")
            cover_file_id = ""
            cover_cdn_url = ""

        sq_file_id, sq_cdn_url = "", ""
        if cover_file_id:
            sq_file_id, sq_cdn_url = self._browser_upload_square_cover(page, token, cover_path)

        # cdn_url 为空时 API 草稿创建会返回 200002 参数错误，直接走编辑器模式
        if not cover_cdn_url:
            self.logger.info("cdn_url is empty, skipping API draft creation, using editor mode directly.")
            return ""

        # Get fingerprint from the page (needed for operate_appmsg)
        fingerprint = page.evaluate("""() => {
            return window.cgiData?.fingerprint || window.pageController?.fingerprint || '';
        }""")
        if not fingerprint:
            import hashlib as _hashlib
            fingerprint = _hashlib.md5(f"{token}{time.time()}".encode()).hexdigest()

        create_result = page.evaluate("""async (args) => {
            const formData = new FormData();
            formData.append('token', args.token);
            formData.append('lang', 'zh_CN');
            formData.append('f', 'json');
            formData.append('ajax', '1');
            formData.append('fingerprint', args.fingerprint);
            formData.append('random', String(Math.random()));
            formData.append('AppMsgId', '');
            formData.append('count', '1');
            formData.append('data_seq', '0');
            formData.append('operate_from', 'Chrome');
            formData.append('isnew', '0');
            formData.append('save_type', '1');
            formData.append('articlenum', '1');
            formData.append('pre_timesend_set', '0');
            formData.append('remind_flag', 'null');
            formData.append('sub', 'create');
            formData.append('type', '77');
            formData.append('title0', args.title);
            formData.append('content0', args.content);
            formData.append('author0', args.author);
            formData.append('fileid0', args.fileId);
            formData.append('digest0', '');
            formData.append('sourceurl0', '');
            formData.append('need_open_comment0', '1');
            formData.append('music_id0', '');
            formData.append('video_id0', '');
            formData.append('voteid0', '');
            formData.append('voteismlt0', '');
            formData.append('voteid20', '');
            formData.append('show_cover_pic0', '1');
            formData.append('shortvideofileid0', '');
            formData.append('copyright_type0', '0');
            formData.append('releasefirst0', '');
            formData.append('pay_fee0', '0');
            formData.append('fee0', '0');
            formData.append('fee_type0', '');
            formData.append('pay_album_info0', '');
            formData.append('appmsg_album_info0', JSON.stringify({appmsg_album_infos:[]}));
            formData.append('ori_white_list0', '');
            formData.append('free_content0', '');
            formData.append('free_content_length0', '');
            formData.append('can_reward0', '0');
            formData.append('related_video0', '');
            formData.append('is_set_sync_to_finder0', '0');
            formData.append('mediaapi_publish_status0', '0');
            formData.append('cdns0', '');
            formData.append('cdn_235_1_url0', args.cdnUrl);
            formData.append('cdn_1_1_url0', args.sqCdnUrl);
            formData.append('cdn_url0', args.cdnUrl);
            formData.append('cover_check_info', JSON.stringify({err_format: '', err_idx: 0}));
            formData.append('auto_gen_digest0', '1');
            formData.append('auto_elect_comment0', '1');
            formData.append('auto_elect_reply0', '1');
            formData.append('option_version0', '5');
            formData.append('style_type0', '3');
            formData.append('is_auto_type_setting', '3');
            formData.append('can_insert_ad0', '1');
            formData.append('reply_flag0', '2');
            formData.append('open_comment_ad0', '1');
            formData.append('danmu_pub_type0', '0');
            formData.append('app_cover_auto0', '0');
            formData.append('multi_picture_cover0', '0');
            formData.append('title_gen_type0', '0');
            formData.append('writerid0', '0');
            formData.append('finder_draft_id0', '0');
            formData.append('import_to_finder0', '0');
            formData.append('is_finder_video0', '0');
            formData.append('is_video_recommend0', '-1');
            formData.append('allow_fast_reprint0', '0');
            formData.append('applyori0', '0');
            formData.append('disable_recommend0', '0');
            formData.append('not_pay_can_comment0', '0');
            formData.append('only_fans_can_comment0', '0');
            formData.append('only_fans_days_can_comment0', '0');
            formData.append('open_fansmsg0', '0');
            formData.append('open_keyword_ad0', '0');
            formData.append('incontent_ad_count0', '0');
            formData.append('new_pic_process0', '0');
            formData.append('is_cartoon_copyright0', '0');
            formData.append('is_pay_subscribe0', '0');
            formData.append('is_share_copyright0', '0');
            formData.append('is_user_no_claim_source0', '0');
            formData.append('share_page_type0', '0');
            formData.append('sticker_info0', JSON.stringify({is_stickers:0,common_stickers_num:0,union_stickers_num:0,sticker_id_list:[],has_invalid_sticker:0}));
            formData.append('audio_info0', JSON.stringify({audio_infos:[]}));
            formData.append('mp_video_info0', JSON.stringify({list:[]}));
            formData.append('copyright_img_list0', JSON.stringify({max_width:586,img_list:[]}));
            formData.append('compose_info0', JSON.stringify({list:[{blockIdx:1,imgList:[],text:""}]}));
            formData.append('share_imageinfo0', JSON.stringify({list:[]}));
            formData.append('categories_list0', '[]');
            formData.append('dot0', '{}');
            formData.append('crop_list0', '');
            formData.append('last_choose_cover_from0', '0');
            formData.append('claim_source_type0', '');
            formData.append('original_article_type0', '');
            formData.append('source_article_type0', '');
            formData.append('reprint_permit_type0', '');
            formData.append('guide_words0', '');
            formData.append('hit_nickname0', '');
            formData.append('is_user_title0', '');
            formData.append('pay_desc0', '');
            formData.append('pay_gifts_count0', '0');
            formData.append('pay_preview_percent0', '');
            formData.append('reward_reply_id0', '');
            formData.append('super_vote_id0', '');
            formData.append('supervoteid0', '');
            formData.append('ad_id0', '');
            formData.append('ad_video_transition0', '');
            formData.append('platform0', '');
            formData.append('appmsg_danmu_pub_type0', '');
            formData.append('cdn_16_9_url0', '');
            formData.append('cdn_3_4_url0', '');
            formData.append('cdn_finder_url0', '');
            formData.append('cdn_url_back0', '');
            formData.append('cdn_video_url0', '');
            formData.append('sync_to_finder_cover0', '');
            formData.append('sync_to_finder_cover_source0', '');
            formData.append('share_copyright_url0', '');
            formData.append('share_finder_audio_exportid0', '');
            formData.append('share_finder_audio_username0', '');
            formData.append('share_video_id0', '');
            formData.append('share_voice_id0', '');
            formData.append('mmlistenitem_json_buf0', '');
            formData.append('msg_index_id0', '');
            formData.append('vid_type0', '');
            formData.append('video_ori_status0', '');
            formData.append('reprint_recommend_content0', '');
            formData.append('reprint_recommend_title0', '');
            formData.append('allow_reprint0', '');
            formData.append('allow_reprint_modify0', '');
            formData.append('convert_to_image_share_page0', '');
            formData.append('convert_from_image_share_page0', '');
            formData.append('isneedsave', '0');
            try {
                const resp = await fetch('/cgi-bin/operate_appmsg?t=ajax-response&sub=create&type=77&token=' + args.token, {
                    method: 'POST',
                    body: formData,
                });
                return await resp.text();
            } catch(e) {
                return 'ERROR: ' + e.message;
            }
        }""", {
            "token": token,
            "fingerprint": fingerprint,
            "title": title,
            "content": article_html,
            "author": self.settings.wechat.author_name or self.settings.account_label,
            "fileId": str(cover_file_id),
            "cdnUrl": str(cover_cdn_url),
            "sqCdnUrl": str(sq_cdn_url),
        })

        self.logger.info("Draft create result: %s", str(create_result)[:500])

        app_msg_id = None
        try:
            data = json.loads(create_result)
            app_msg_id = str(data.get("appMsgId", data.get("appmsgid", ""))).strip()
            if not app_msg_id:
                self.logger.warning("Draft create returned no appMsgId: %s", create_result[:300])
        except (json.JSONDecodeError, TypeError):
            self.logger.warning("Failed to parse draft create result: %s", str(create_result)[:300])

        if app_msg_id and cover_file_id:
            update_result = page.evaluate("""async (args) => {
                const [token, appMsgId, fileId, cdnUrl, sqFileId, sqCdnUrl] = args;
                const formData = new FormData();
                formData.append('token', token);
                formData.append('lang', 'zh_CN');
                formData.append('f', 'json');
                formData.append('ajax', '1');
                formData.append('sub', 'update');
                formData.append('type', '77');
                formData.append('id', appMsgId);
                formData.append('fileid0', fileId);
                formData.append('cdn_235_1_url0', cdnUrl);
                formData.append('cdn_1_1_url0', sqCdnUrl);
                formData.append('cdn_url0', cdnUrl);
                formData.append('cover_check_info', JSON.stringify({err_format: '', err_idx: 0}));
                formData.append('save_type', '1');
                try {
                    const resp = await fetch('/cgi-bin/operate_appmsg?t=ajax-response&sub=update&token=' + token, {
                        method: 'POST',
                        body: formData,
                    });
                    return await resp.text();
                } catch(e) {
                    return 'ERROR: ' + e.message;
                }
            }""", [token, app_msg_id, str(cover_file_id), str(cover_cdn_url), str(sq_file_id), str(sq_cdn_url)])
            self.logger.info("Draft cover update result: %s", str(update_result)[:300])

        return app_msg_id

    def _upload_image_via_http(self, page, token: str, image_path: Path, filename: str = "cover.png") -> tuple[str, str]:
        """Upload bytes through the authenticated browser's existing transport."""
        origin = urlsplit(page.url)
        if (origin.scheme != "https" or origin.hostname != "mp.weixin.qq.com"
                or origin.port not in (None, 443) or not token):
            raise RuntimeError("WeChat image upload requires an authenticated backend page")
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        try:
            result = page.evaluate("""async ({token, encoded, filename}) => {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 30000);
                try {
                    const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
                    const body = new FormData();
                    body.append('file', new Blob([bytes], {type: 'image/png'}), filename);
                    const query = new URLSearchParams({action: 'upload_material', scene: '1',
                        f: 'json', writetype: 'doublewrite', groupid: '1', token});
                    const response = await fetch('/cgi-bin/filetransfer?' + query, {
                        method: 'POST', body, credentials: 'same-origin', signal: controller.signal,
                    });
                    if (!response.ok) throw new Error('Upload HTTP status ' + response.status);
                    return await response.json();
                } finally {
                    clearTimeout(timeout);
                }
            }""", {"token": token, "encoded": encoded, "filename": filename})
        except Exception as exc:
            self.logger.warning("Browser image upload failed (%s)", type(exc).__name__)
            return "", ""

        if not isinstance(result, dict):
            self.logger.warning("Upload returned non-dict (%s)", type(result).__name__)
            return "", ""

        try:
            ret = int((result.get("base_resp") or {}).get("ret", -1))
        except (AttributeError, TypeError, ValueError):
            return "", ""
        if ret != 0:
            self.logger.warning("Upload API returned error code: %s", ret)
            return "", ""

        file_id = str(result.get("content") or "").strip()
        cdn_url = str(result.get("cdn_url") or result.get("cdn_mid_url") or "").strip()
        if not file_id or not cdn_url:
            return "", ""
        self.logger.info("Image uploaded through authenticated browser: file_id=%s", file_id)
        return file_id, cdn_url

    def _browser_upload_cover(self, page, token: str, cover_path: Path) -> tuple[str, str]:
        upload_path = self._ensure_rgb_image(cover_path)
        upload_path = Path(os.path.abspath(str(upload_path)))
        if not upload_path.exists():
            self.logger.error("Cover image file does not exist: %s", upload_path)
            return "", ""
        return self._upload_image_via_http(page, token, upload_path, "cover.png")

    def _browser_upload_square_cover(self, page, token: str, cover_path: Path) -> tuple[str, str]:
        try:
            from PIL import Image as PILImage
            img = PILImage.open(cover_path)
            w, h = img.size
            crop_size = min(w, h)
            left = (w - crop_size) // 2
            top = (h - crop_size) // 2
            square_img = img.crop((left, top, left + crop_size, top + crop_size))
            if square_img.mode == "RGBA":
                bg = PILImage.new("RGB", square_img.size, (255, 255, 255))
                bg.paste(square_img, mask=square_img.split()[3])
                square_img = bg
            elif square_img.mode != "RGB":
                square_img = square_img.convert("RGB")
            square_path = cover_path.parent / f"_sq_{cover_path.name}"
            square_img.save(str(square_path), format="PNG")

            result = self._upload_image_via_http(page, token, square_path, "cover_square.png")

            with suppress(Exception):
                if square_path is not cover_path and square_path.exists():
                    square_path.unlink()

            return result
        except ImportError:
            self.logger.warning("Pillow not available, skipping square cover upload.")
            return "", ""
        except Exception as exc:
            self.logger.warning("Square cover upload failed: %s", exc)
            return "", ""

    def _browser_create_draft_via_editor(
        self,
        page,
        token: str,
        title: str,
        summary: str,
        article_html: str,
        cover_path: Path,
    ) -> str | None:
        base = self.settings.wechat.base_url.rstrip("/")
        new_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&token={token}&lang=zh_CN"
        page.goto(new_url, wait_until="commit", timeout=30000)
        page.wait_for_function(
            "() => !!document.querySelector('#title') && "
            "!!document.querySelector('.ProseMirror, [contenteditable=\"true\"]')",
            timeout=30000,
        )

        for _ in range(10):
            self._dismiss_irrelevant_wechat_dialogs(page)
            page.wait_for_timeout(500)

        # Fill title — #title may be visibility:hidden (height:0) in the new editor,
        # so Playwright fill() silently fails.  Use JS native setter instead.
        title_filled = False

        # Method 1: JS native value setter (works even when element is hidden)
        with suppress(Exception):
            js_title_result = page.evaluate("""(title) => {
                const input = document.querySelector('#title');
                if (!input) return 'no_input';
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                nativeSetter.call(input, title);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return 'ok:' + input.value.substring(0, 30);
            }""", title)
            if js_title_result and js_title_result.startswith("ok:"):
                title_filled = True
                self.logger.info("Filled title via JS native setter: %s", js_title_result)

        # Method 2: Playwright fill with force (fallback)
        if not title_filled:
            with suppress(Exception):
                title_input = page.locator("#title")
                title_input.fill(title, force=True, timeout=5000)
                page.wait_for_timeout(500)
                title_value = page.evaluate('() => document.querySelector("#title")?.value || ""')
                if title_value.strip():
                    title_filled = True
                    self.logger.info("Filled title via Playwright force fill.")

        if not title_filled:
            self.logger.warning("Failed to fill title in editor.")

        paste_result = page.evaluate("""(html) => {
            const editor = document.querySelector('.ProseMirror') ||
                          document.querySelector('[contenteditable="true"]');
            if (!editor) return 'no editor';
            editor.focus();
            const dt = new DataTransfer();
            dt.setData('text/html', html);
            const pasteEvent = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true,
            });
            editor.dispatchEvent(pasteEvent);
            return 'pasted';
        }""", article_html)
        self.logger.info("Content paste result: %s", paste_result)
        page.wait_for_timeout(2000)

        cover_uploaded = False
        upload_path = self._ensure_rgb_image(cover_path)
        try:
            cover_uploaded = self._upload_cover_via_image_library(page, upload_path)
            if not cover_uploaded:
                self.logger.warning("Cover upload via image library failed, trying direct file input.")
                cover_result = page.evaluate("""() => {
                    const selectors = [
                        '.js_cover_btn_area input[type="file"]',
                        '#js_cover_area input[type="file"]',
                        '.select-cover__btn input[type="file"]',
                        'input[type="file"][accept*="image"]',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) return sel;
                    }
                    return null;
                }""")
                if cover_result:
                    locator = page.locator(cover_result).first
                    locator.set_input_files(str(upload_path))
                    page.wait_for_timeout(3000)
                    self.logger.info("Uploaded cover image via direct selector: %s", cover_result)

                    # After direct upload, a crop dialog may appear — confirm it
                    self._confirm_cover_crop_dialog(page)
                    page.wait_for_timeout(2000)

                    # Verify cover was set
                    has_cover = page.evaluate("""() => {
                        const coverArea = document.querySelector('.js_cover_btn_area');
                        return !!coverArea?.querySelector('img');
                    }""")
                    if has_cover:
                        cover_uploaded = True
                        self.logger.info("Cover image verified after direct upload + crop confirm.")
                    else:
                        self.logger.warning("Cover image still not visible after direct upload.")
        finally:
            if upload_path is not cover_path and upload_path.exists():
                with suppress(Exception):
                    upload_path.unlink()

        page.wait_for_timeout(2000)
        for _ in range(5):
            self._dismiss_irrelevant_wechat_dialogs(page)
            page.wait_for_timeout(500)

        # If cover is still not uploaded, try one more approach: click the cover
        # area to open the upload dialog, then use file chooser
        if not cover_uploaded:
            self.logger.info("Cover not yet set, trying file-chooser approach.")
            cover_uploaded = self._upload_cover_via_file_chooser(page, upload_path)

        if not cover_uploaded:
            self.logger.warning("Cover image could not be set. Draft save may fail with '请选择封面' error.")

        save_result = page.evaluate("""() => {
            const btn = document.querySelector('#js_submit button');
            if (btn) { btn.click(); return 'clicked'; }
            return null;
        }""")
        if save_result:
            self.logger.info("Saved draft via #js_submit.")
            page.wait_for_timeout(8000)

        app_msg_id = None
        with suppress(Exception):
            url_match = re.search(r"appmsgid=(\d+)", page.url)
            if url_match:
                app_msg_id = url_match.group(1)

        if not app_msg_id:
            app_msg_id = page.evaluate("""() => {
                const app = document.querySelector('[data-v-app]') || document.querySelector('#vue_container');
                if (app && app.__vue_app__) {
                    const root = app.__vue_app__._instance;
                    if (root && root.proxy && root.proxy.$data) {
                        return String(root.proxy.$data.appMsgId || '');
                    }
                }
                return '';
            }""")

        self.logger.info("Draft created via editor, appMsgId: %s", app_msg_id)
        return app_msg_id

    def _browser_click_send_and_confirm(self, page) -> None:
        self.logger.info("Clicking #js_send (发表) button...")
        page.locator("#js_send").click(timeout=10000)
        page.wait_for_timeout(2000)

        first_dialog = page.evaluate("""() => {
            const allDialogs = document.querySelectorAll('.weui-desktop-dialog__wrp, div[role="dialog"], .weui-dialog');
            for (const d of allDialogs) {
                const style = window.getComputedStyle(d);
                if (style.display !== 'none') {
                    const dialog = d.querySelector('.weui-desktop-dialog') || d;
                    const btns = dialog ? dialog.querySelectorAll('button') : [];
                    const btnTexts = [];
                    btns.forEach(b => {
                        const t = (b.textContent || '').trim();
                        if (t) btnTexts.push(t);
                    });
                    if ((btnTexts.includes('发表') && btnTexts.includes('取消')) || btnTexts.includes('确认群发')) {
                        return {found: true, buttons: btnTexts};
                    }
                }
            }
            return {found: false};
        }""")

        if not first_dialog.get("found"):
            self.logger.warning("First publish dialog not found after #js_send click, trying extended selectors.")
            page.wait_for_timeout(2000)
            first_dialog = page.evaluate("""() => {
                const allBtns = document.querySelectorAll('button, [role="button"], a.btn');
                for (const btn of allBtns) {
                    const t = (btn.textContent || '').trim();
                    if (t === '发表' || t === '确认群发' || t === '确认发表') {
                        const style = window.getComputedStyle(btn);
                        const rect = btn.getBoundingClientRect();
                        if (style.display !== 'none' && rect.width > 0 && rect.height > 0) {
                            return {found: true, buttons: [t], directClick: true};
                        }
                    }
                }
                return {found: false};
            }""")
            if first_dialog.get("directClick"):
                self.logger.info("Found publish confirm button via extended search, clicking directly.")
                page.evaluate("""() => {
                    const allBtns = document.querySelectorAll('button, [role="button"], a.btn');
                    for (const btn of allBtns) {
                        const t = (btn.textContent || '').trim();
                        if (t === '发表' || t === '确认群发' || t === '确认发表') {
                            btn.click();
                            return;
                        }
                    }
                }""")
                page.wait_for_timeout(5000)
                return
            return

        self.logger.info("First dialog found with buttons: %s", first_dialog["buttons"])
        publish_btn = page.locator(
            '.weui-desktop-dialog__wrp .weui-desktop-dialog button:has-text("发表")'
        ).first
        publish_btn.click(timeout=10000)
        self.logger.info("Clicked '发表' in first dialog.")
        page.wait_for_timeout(8000)

        second_dialog = page.evaluate("""() => {
            const allDialogs = document.querySelectorAll('.weui-desktop-dialog__wrp');
            for (const d of allDialogs) {
                const style = window.getComputedStyle(d);
                if (style.display !== 'none') {
                    const dialog = d.querySelector('.weui-desktop-dialog');
                    const btns = dialog ? dialog.querySelectorAll('button') : [];
                    const btnTexts = [];
                    btns.forEach(b => {
                        const t = (b.textContent || '').trim();
                        if (t) btnTexts.push(t);
                    });
                    if (btnTexts.includes('继续发表')) {
                        return {found: true, buttons: btnTexts};
                    }
                }
            }
            return {found: false};
        }""")

        if second_dialog.get("found"):
            self.logger.info("Second dialog found with buttons: %s", second_dialog["buttons"])
            continue_btn = page.locator(
                '.weui-desktop-dialog__wrp .weui-desktop-dialog button:has-text("继续发表")'
            ).first
            continue_btn.click(timeout=10000)
            self.logger.info("Clicked '继续发表' in second dialog.")
            page.wait_for_timeout(10000)
        else:
            self.logger.info("No second dialog with '继续发表', publish may have completed from first dialog.")
            page.wait_for_timeout(5000)

        self._verify_wechat_publish_success(page)

    def _verify_wechat_publish_success(self, page) -> None:
        try:
            page.wait_for_timeout(3000)
            current_url = str(page.url or "")
            success_indicators = page.evaluate("""() => {
                const indicators = [];
                const allText = document.body ? document.body.innerText : '';
                if (allText.includes('发表成功') || allText.includes('发布成功')) {
                    indicators.push('success_text');
                }
                const toasts = document.querySelectorAll('.weui-desktop-toast, [class*="toast"], [class*="success"]');
                for (const t of toasts) {
                    const style = window.getComputedStyle(t);
                    if (style.display !== 'none' && t.textContent.includes('成功')) {
                        indicators.push('success_toast');
                        break;
                    }
                }
                return indicators;
            }""")
            if success_indicators:
                self.logger.info("WeChat publish verified: %s", success_indicators)
            elif "mp.weixin.qq.com" in current_url and "/cgi-bin/appmsg" not in current_url:
                self.logger.info("WeChat publish likely succeeded: page navigated away from editor.")
            else:
                self.logger.warning("WeChat publish verification: no explicit success indicator detected.")
        except Exception as exc:
            self.logger.debug("WeChat publish verification failed: %s", exc)

    def _submit_draft_via_browser(self, *, draft_media_id: str, title: str) -> dict[str, Any]:
        return self._submit_draft_via_browser_original(draft_media_id=draft_media_id, title=title)

    def _submit_draft_via_browser_original(self, *, draft_media_id: str, title: str) -> dict[str, Any]:
        if self.settings.wechat.draft_only:
            raise RuntimeError("WeChat draft-only mode prohibits publication")
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        debug_dir: Path | None = None

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="wechat:publish",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run() -> dict[str, Any]:
            with sync_playwright() as playwright:
                browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir)
                page = None
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    self._ensure_browser_session_ready(browser, page, self.settings.wechat.base_url)
                    self._open_wechat_draft_box(page)
                    self._open_wechat_draft_by_title(page, title)
                    self._click_publish_button_on_wechat_page(page)
                    original_declared = self._confirm_wechat_publish_dialog(page)
                    self._wait_for_wechat_publish_success(page)
                    result = {
                        "status": "submitted",
                        "method": "browser",
                        "draft_media_id": draft_media_id,
                        "title": title,
                        "publish_url": page.url,
                        "original_declared": original_declared,
                    }
                    return result
                except SessionExpiredException:
                    self.logger.error("微信公众号 SessionExpiredException: 登录态已失效，需要重新登录")
                    raise
                except Exception as exc:
                    debug_dir = self._capture_browser_debug_artifacts(page, {"draft_media_id": draft_media_id, "title": title})
                    raise RuntimeError(
                        f"WeChat browser publish failed: {self._clean_visible_text(exc)}. Debug files saved to: {debug_dir}"
                    ) from exc
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=300)

    def _open_wechat_backend(self, page) -> None:
        page.goto(
            self.settings.wechat.base_url,
            wait_until="domcontentloaded",
            timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
        )
        page.wait_for_timeout(2500)
        self._raise_if_session_expired(page)

    def _open_wechat_draft_box(self, page) -> None:
        draft_url_paths = [
            "/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card",
            "/cgi-bin/appmsg?t=media/appmsg_draft",
        ]
        current_url = page.url
        base = self.settings.wechat.base_url.rstrip("/")
        for path in draft_url_paths:
            token_match = re.search(r"token=(\d+)", current_url)
            sep = "&" if "?" in path else "?"
            full_url = f"{base}{path}"
            if token_match:
                full_url += f"{sep}token={token_match.group(1)}"
            try:
                page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                self._raise_if_session_expired(page)
                self.logger.info("Navigated directly to WeChat draft box: %s", path[:60])
                return
            except Exception as exc:
                self.logger.warning("Direct navigation to draft box failed (%s), trying text click.", exc)
                break

        candidate_texts = ("草稿箱", "Drafts", "草稿")
        deadline = time.time() + 60
        while time.time() < deadline:
            self._raise_if_session_expired(page)
            for text in candidate_texts:
                with suppress(Exception):
                    locator = page.get_by_text(text, exact=False).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(3000)
                        self._raise_if_session_expired(page)
                        return
            page.wait_for_timeout(1000)
        debug_dir = self._capture_browser_debug_artifacts(page, {"step": "open_draft_box"})
        self.logger.error(
            "Could not open WeChat draft box from the current session. Debug files saved to: %s. "
            "Please inspect the screenshot/HTML to confirm whether the backend DOM changed or the session expired.",
            debug_dir,
        )
        raise RuntimeError(f"Could not open WeChat draft box from the current session. Debug files saved to: {debug_dir}")

    def _open_wechat_draft_by_title(self, page, title: str) -> None:
        edit_url = self._find_draft_edit_url(title)
        if edit_url:
            base = self.settings.wechat.base_url.rstrip("/")
            current_url = page.url
            token_match = re.search(r"token=(\d+)", current_url)
            sep = "&" if "?" in edit_url else "?"
            full_url = f"{base}{edit_url}"
            if token_match:
                full_url += f"{sep}token={token_match.group(1)}"
            try:
                page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                self.logger.info("Navigated directly to draft edit page: %s", edit_url[:80])
                return
            except Exception as exc:
                self.logger.warning("Direct navigation to draft edit page failed: %s", exc)

        short_title = title[:15] if len(title) > 15 else title
        locators = [
            page.get_by_text(title, exact=False).first,
            page.locator(f".weui-desktop-publish__cover__title:has-text('{short_title}')").first,
            page.locator(WECHAT_DRAFT_LIST_PAGE.first("draft_title_link")).first,
        ]
        deadline = time.time() + 60
        while time.time() < deadline:
            for locator in locators:
                with suppress(Exception):
                    if locator.is_visible(timeout=800):
                        locator.click(force=True, timeout=2000)
                        page.wait_for_timeout(2500)
                        self.logger.info("Opened WeChat draft by title: %s", title[:40])
                        return
            search_inputs = WECHAT_DRAFT_LIST_PAGE.get("search_input")
            for selector in search_inputs:
                with suppress(Exception):
                    field = page.locator(selector).first
                    if field.is_visible(timeout=500):
                        field.fill(title)
                        field.press("Enter")
                        page.wait_for_timeout(1500)
                        break
            page.wait_for_timeout(1000)
        raise RuntimeError(f"Could not locate draft titled: {title}")

    def _find_draft_edit_url(self, title: str) -> str | None:
        try:
            access_token = self._get_access_token()
            url = self._build_api_url("/cgi-bin/draft/batchget", access_token=access_token)
            payload = {"offset": 0, "count": 20, "no_content": 1}
            response = self._wechat_post_json("/cgi-bin/draft/batchget", payload, access_token=access_token)
            items = response.get("item", [])
            for item in items:
                content = item.get("content", {})
                news_items = content.get("news_item", [])
                for news in news_items:
                    news_title = str(news.get("title", "")).strip()
                    if news_title == title or title in news_title:
                        media_id = str(item.get("media_id", "")).strip()
                        if media_id:
                            edit_url = f"/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&appmsgid={media_id}"
                            self.logger.info("Found draft edit URL for title '%s': %s", title[:30], edit_url[:80])
                            return edit_url
            self.logger.warning("Draft with title '%s' not found in API draft list.", title[:30])
        except Exception as exc:
            self.logger.warning("Failed to find draft edit URL via API: %s", exc)
        return None

    def _click_publish_button_on_wechat_page(self, page) -> None:
        if self.settings.wechat.draft_only:
            raise RuntimeError("WeChat draft-only mode prohibits publication")
        js_result = page.evaluate(
            """(selectors) => {
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        el.click();
                        return sel;
                    }
                }
                return null;
            }""",
            WECHAT_EDITOR_PAGE.get("publish_button"),
        )
        if js_result:
            page.wait_for_timeout(2000)
            self.logger.info("Clicked publish button via JS on selector: %s", js_result)
            return

        button_texts = (
            "发表",
            "发布",
            "群发",
            "提交",
            "确认发布",
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            for text in button_texts:
                with suppress(Exception):
                    locator = page.get_by_role("button", name=text).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(2000)
                        return
            for text in button_texts:
                with suppress(Exception):
                    locator = page.get_by_text(text, exact=False).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(2000)
                        return
            page.wait_for_timeout(1000)
        raise RuntimeError("Could not find a publish button on the WeChat draft page.")

    def _confirm_wechat_publish_dialog(self, page) -> bool:
        if self.settings.wechat.draft_only:
            raise RuntimeError("WeChat draft-only mode prohibits publication")
        confirm_texts = WECHAT_PUBLISH_CONFIRM_TEXTS
        page.wait_for_timeout(1500)
        original_declared = self._ensure_original_declaration(page)

        self._dismiss_irrelevant_wechat_dialogs(page)

        for attempt in range(30):
            if self._ensure_original_declaration(page):
                original_declared = True

            self._dismiss_irrelevant_wechat_dialogs(page)

            js_clicked = page.evaluate("""(args) => {
                const [confirmTexts, confirmSelectors] = args;
                for (const sel of confirmSelectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const style = window.getComputedStyle(el);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            el.click();
                            return sel;
                        }
                    }
                }
                for (const text of confirmTexts) {
                    const allBtns = document.querySelectorAll('button, a.btn, [role="button"]');
                    for (const btn of allBtns) {
                        const btnText = (btn.textContent || '').trim();
                        if (btnText === text) {
                            const style = window.getComputedStyle(btn);
                            const rect = btn.getBoundingClientRect();
                            if (style.display !== 'none' && rect.width > 0 && rect.height > 0) {
                                const parentDialog = btn.closest('.weui-desktop-dialog');
                                if (parentDialog) {
                                    const dialogText = parentDialog.textContent || '';
                                    if (dialogText.includes('退出') || dialogText.includes('实名') || dialogText.includes('切换账号')) {
                                        continue;
                                    }
                                }
                                btn.click();
                                return 'text:' + text;
                            }
                        }
                    }
                }
                return null;
            }""", [list(confirm_texts), WECHAT_PUBLISH_DIALOG.get("confirm_publish_button")])
            if js_clicked:
                page.wait_for_timeout(2000)
                self.logger.info("Confirmed publish dialog via JS: %s (attempt %d)", js_clicked, attempt)
                return original_declared

            for text in confirm_texts:
                with suppress(Exception):
                    locator = page.get_by_role("button", name=text).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(2000)
                        self.logger.info("Confirmed publish dialog via Playwright button: %s", text)
                        return original_declared
            page.wait_for_timeout(1000)
        return original_declared

    def _upload_cover_via_image_library(self, page, cover_path: Path) -> bool:
        try:
            for _ in range(3):
                self._dismiss_irrelevant_wechat_dialogs(page)
                page.wait_for_timeout(500)

            page.evaluate("""() => {
                const coverBtn = document.querySelector('.js_cover_btn_area');
                if (coverBtn) coverBtn.click();
            }""")
            page.wait_for_timeout(1500)

            self._dismiss_irrelevant_wechat_dialogs(page)
            page.wait_for_timeout(500)

            page.evaluate("""() => {
                const link = document.querySelector('.js_imagedialog');
                if (link) link.click();
            }""")
            page.wait_for_timeout(3000)

            self._dismiss_irrelevant_wechat_dialogs(page)
            page.wait_for_timeout(500)

            has_dialog = page.evaluate("""() => {
                const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                for (const d of dialogs) {
                    const wrp = d.closest('.weui-desktop-dialog__wrp');
                    const style = wrp ? window.getComputedStyle(wrp) : window.getComputedStyle(d);
                    if (style.display !== 'none' && d.textContent.includes('选择图片')) {
                        return true;
                    }
                }
                return false;
            }""")
            if not has_dialog:
                self.logger.warning("Image library dialog did not appear.")
                return False

            with page.expect_file_chooser(timeout=10000) as fc_info:
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('a, button, span');
                    for (const btn of btns) {
                        const text = (btn.textContent || '').trim();
                        if (text === '上传文件' || text === '上传图片' || text === '本地上传') {
                            const style = window.getComputedStyle(btn);
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && style.display !== 'none') {
                                btn.click();
                                return;
                            }
                        }
                    }
                }""")
            file_chooser = fc_info.value
            file_chooser.set_files(str(cover_path))
            self.logger.info("Cover image uploaded via file chooser in image library.")
            page.wait_for_timeout(5000)

            page.evaluate("""() => {
                const images = document.querySelectorAll('.img_item, .image_item, .pic_item, [class*="img_item"]');
                for (const img of images) {
                    if (!img.classList.contains('selected')) {
                        img.click();
                        return;
                    }
                }
            }""")
            page.wait_for_timeout(1000)

            page.evaluate("""() => {
                const btns = document.querySelectorAll('button, a.btn');
                for (const btn of btns) {
                    if (btn.textContent.trim() === '确定') {
                        btn.click();
                        return;
                    }
                }
            }""")
            page.wait_for_timeout(3000)

            has_cover = page.evaluate("""() => {
                const coverArea = document.querySelector('.js_cover_btn_area');
                return !!coverArea?.querySelector('img');
            }""")
            if has_cover:
                self.logger.info("Cover image successfully set via image library.")
                return True
            else:
                self.logger.warning("Cover image was not set after image library upload.")
                return False
        except Exception as exc:
            self.logger.warning("Cover upload via image library failed: %s", exc)
            return False

    def _confirm_cover_crop_dialog(self, page) -> None:
        """After uploading a cover image, a crop/confirm dialog may appear. Confirm it."""
        for _ in range(5):
            self._dismiss_irrelevant_wechat_dialogs(page)
            page.wait_for_timeout(500)

            clicked = page.evaluate("""() => {
                // Look for crop confirm button
                const selectors = [
                    '.js_crop_confirm',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const style = window.getComputedStyle(el);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            el.click();
                            return sel;
                        }
                    }
                }
                // Search buttons by text content
                const allBtns = document.querySelectorAll('button');
                const targetTexts = ['完成裁剪', '完成', '确定'];
                for (const btn of allBtns) {
                    const t = (btn.textContent || '').trim();
                    if (targetTexts.includes(t)) {
                        const style = window.getComputedStyle(btn);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            btn.click();
                            return 'btn_text:' + t;
                        }
                    }
                }
                // Also check dialog buttons
                const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                for (const d of dialogs) {
                    const wrp = d.closest('.weui-desktop-dialog__wrp');
                    const style = wrp ? window.getComputedStyle(wrp) : window.getComputedStyle(d);
                    if (style.display === 'none') continue;
                    const btns = d.querySelectorAll('button');
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        if (t === '完成' || t === '确定' || t === '完成裁剪') {
                            btn.click();
                            return 'dialog_btn:' + t;
                        }
                    }
                }
                return null;
            }""")
            if clicked:
                self.logger.info("Confirmed cover crop dialog via: %s", clicked)
                page.wait_for_timeout(2000)
                return
            page.wait_for_timeout(1000)

    def _upload_cover_via_file_chooser(self, page, cover_path: Path) -> bool:
        """Upload cover by clicking the cover area to trigger a file chooser dialog."""
        try:
            # Click the cover area to trigger file chooser
            with page.expect_file_chooser(timeout=10000) as fc_info:
                # Try clicking the cover upload button/area
                cover_click_result = page.evaluate("""() => {
                    // Try the cover button area first
                    const coverBtn = document.querySelector('.js_cover_btn_area');
                    if (coverBtn) { coverBtn.click(); return 'cover_btn_area'; }
                    // Try the "选择封面" text
                    const els = document.querySelectorAll('span, a, div');
                    for (const el of els) {
                        if ((el.textContent || '').trim() === '选择封面' || (el.textContent || '').trim() === '拖拽或选择封面') {
                            el.click();
                            return 'cover_text:' + (el.textContent || '').trim();
                        }
                    }
                    return null;
                }""")
                if not cover_click_result:
                    self.logger.warning("Could not click cover area for file chooser.")
                    return False

            file_chooser = fc_info.value
            file_chooser.set_files(str(cover_path))
            self.logger.info("Cover uploaded via file chooser.")
            page.wait_for_timeout(5000)

            # Confirm any crop dialog
            self._confirm_cover_crop_dialog(page)
            page.wait_for_timeout(2000)

            # Verify cover
            has_cover = page.evaluate("""() => {
                const coverArea = document.querySelector('.js_cover_btn_area');
                return !!coverArea?.querySelector('img');
            }""")
            if has_cover:
                self.logger.info("Cover image verified after file-chooser upload.")
                return True
            else:
                self.logger.warning("Cover image not visible after file-chooser upload.")
                return False
        except Exception as exc:
            self.logger.warning("Cover upload via file chooser failed: %s", exc)
            return False

    def _dismiss_irrelevant_wechat_dialogs(self, page) -> None:
        irrelevant_keywords_js = json.dumps(list(WECHAT_DISMISS_KEYWORDS))
        dismiss_actions_js = json.dumps(list(WECHAT_DISMISS_ACTIONS))
        page.evaluate("""(args) => {
            const [irrelevantKeywords, dismissActions] = args;
            const dialogs = document.querySelectorAll('.weui-desktop-dialog');
            dialogs.forEach(dialog => {
                const text = (dialog.textContent || '').trim();
                const isIrrelevant = irrelevantKeywords.some(kw => text.includes(kw));
                if (!isIrrelevant) return;
                for (const dismissText of dismissActions) {
                    const btns = dialog.querySelectorAll('button, a, [role="button"]');
                    for (const btn of btns) {
                        if ((btn.textContent || '').trim() === dismissText) {
                            btn.click();
                            return;
                        }
                    }
                }
                const closeBtn = dialog.querySelector('.weui-desktop-dialog__close-btn');
                if (closeBtn) closeBtn.click();
            });
            document.querySelectorAll('.weui-desktop-dialog__wrp').forEach(wrp => {
                const dialog = wrp.querySelector('.weui-desktop-dialog');
                if (dialog) {
                    const text = (dialog.textContent || '').trim();
                    const isIrrelevant = irrelevantKeywords.some(kw => text.includes(kw));
                    if (isIrrelevant) {
                        wrp.style.display = 'none';
                    }
                }
                const picker = wrp.querySelector('.weui-desktop-dialog_img-picker');
                if (picker) {
                    const closeBtn = picker.querySelector('.weui-desktop-dialog__close-btn');
                    if (closeBtn) closeBtn.click();
                    wrp.style.display = 'none';
                }
            });
            document.querySelectorAll('.weui-desktop-dialog_img-picker').forEach(picker => {
                const closeBtn = picker.querySelector('.weui-desktop-dialog__close-btn');
                if (closeBtn) closeBtn.click();
                picker.style.display = 'none';
            });
        }""", [list(WECHAT_DISMISS_KEYWORDS), list(WECHAT_DISMISS_ACTIONS)])

    def _ensure_original_declaration(self, page) -> bool:
        selectors = WECHAT_ORIGINAL_DECLARATION.get("original_checkbox")
        for selector in selectors:
            with suppress(Exception):
                locator = page.locator(selector).first
                if not locator.is_visible(timeout=400):
                    continue
                if not self._is_custom_control_selected(locator):
                    self._click_custom_control(locator)
                    page.wait_for_timeout(300)
                if self._is_custom_control_selected(locator):
                    self.logger.info("WeChat original declaration enabled via selector: %s", selector)
                    return True
        return False

    def _is_custom_control_selected(self, locator) -> bool:
        try:
            return bool(
                locator.evaluate(
                    """(node) => {
                        const input = node.querySelector('input');
                        if (input && typeof input.checked === 'boolean' && input.checked) {
                            return true;
                        }
                        if (node.classList.contains('selected')) {
                            return true;
                        }
                        if (node.classList.contains('checked')) {
                            return true;
                        }
                        if (node.classList.contains('is-checked')) {
                            return true;
                        }
                        if (node.classList.contains('weui-desktop-form__check-label_selected')) {
                            return true;
                        }
                        if (node.classList.contains('byte-checkbox-checked')) {
                            return true;
                        }
                        if (node.getAttribute('aria-checked') === 'true') {
                            return true;
                        }
                        return !!node.querySelector('.checked, .is-checked, .byte-checkbox-checked, .weui-desktop-icon-checkbox-selected');
                    }"""
                )
            )
        except Exception:
            try:
                input = locator.locator("input").first
                return bool(input.count() and input.is_checked())
            except Exception:
                return False

    def _click_custom_control(self, locator) -> bool:
        try:
            locator.click(force=True, timeout=1200)
            return True
        except Exception:
            pass

        try:
            locator.evaluate(
                """(node) => {
                    const target =
                        node.querySelector('input') ||
                        node.querySelector('[role="checkbox"]') ||
                        node.querySelector('.weui-desktop-form__check-label') ||
                        node;
                    target.dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }"""
            )
            return True
        except Exception:
            return False

    def _wait_for_wechat_publish_success(self, page) -> None:
        success_markers = WECHAT_SUCCESS_MARKERS
        deadline = time.time() + 90
        continued = False
        while time.time() < deadline:
            self._raise_if_session_expired(page)
            if not continued:
                continue_button = page.get_by_role("button", name="继续发表", exact=True).first
                if continue_button.is_visible(timeout=300):
                    continue_button.click(timeout=1500)
                    continued = True
            tip_result = page.evaluate("""() => {
                const tips = document.querySelectorAll('.page_tips, .global_error, .toast');
                for (const tip of tips) {
                    const style = window.getComputedStyle(tip);
                    if (style.display !== 'none' && tip.textContent.trim()) {
                        return tip.textContent.trim().substring(0, 100);
                    }
                }
                return null;
            }""")
            if tip_result:
                if any(marker in tip_result for marker in success_markers):
                    self.logger.info("WeChat publish success detected: %s", tip_result)
                    return
                if "失败" in tip_result or "错误" in tip_result or "error" in tip_result.lower():
                    raise RuntimeError(f"WeChat publish failed with message: {tip_result}")
            current_url = page.url
            if "appmsgpublish" in current_url and "sub=list" in current_url:
                self.logger.info("WeChat publish success detected via URL: %s", current_url[:80])
                return
            page.wait_for_timeout(1500)
        raise RuntimeError("WeChat publish did not reach a success state within the timeout.")

    def _browser_session_ready(self, page) -> bool:
        if self._is_login_page(page):
            return False
        current_url = urlsplit(str(page.url or ""))
        expected_host = urlsplit(self.settings.wechat.base_url).hostname
        if current_url.hostname != expected_host or not self._extract_token(page):
            return False
        with suppress(Exception):
            body_text = page.locator("body").inner_text(timeout=1000)
            if any(marker in body_text for marker in WECHAT_SESSION_READY_MARKERS):
                return True
        return current_url.path.startswith("/cgi-bin/")

    def _is_login_page(self, page) -> bool:
        url = str(getattr(page, "url", "") or "")
        if any(marker in url for marker in WECHAT_LOGIN_URL_MARKERS):
            return True
        for selector in WECHAT_LOGIN_PAGE.get("qrcode_image") + WECHAT_LOGIN_PAGE.get("account_input") + WECHAT_LOGIN_PAGE.get("password_input_direct") + WECHAT_LOGIN_PAGE.get("login_page_text"):
            with suppress(Exception):
                if page.locator(selector).first.is_visible(timeout=400):
                    return True
        return False

    def _raise_if_session_expired(self, page) -> None:
        if self._is_login_page(page):
            # TODO: 发布前登录态健康检查 — 在此处加入主动探测逻辑：
            #   1. 访问微信公众平台后台首页，检查是否被重定向到登录页
            #   2. 尝试调用 /cgi-bin/searchbiz 接口验证 token 有效性
            #   3. 若检测到即将过期，主动刷新 cookie 或通知用户续期
            #   4. 将检查结果写入 preflight summary 供上层决策
            debug_dir = self._capture_browser_debug_artifacts(page, {"step": "session_check"})
            self.logger.error(
                "微信公众号登录态已失效，请重新运行初始化登录脚本。"
                " 调试文件已保存到: %s",
                debug_dir,
            )
            raise SessionExpiredException(
                "微信公众号登录态已失效，请重新运行初始化登录脚本。"
                f" 调试文件已保存到: {debug_dir}"
            )

    def _capture_browser_debug_artifacts(self, page, extra_payload: dict[str, Any] | None = None) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        debug_dir = self.settings.output_dir / "wechat_publish_debug" / timestamp
        debug_dir.mkdir(parents=True, exist_ok=True)
        with suppress(Exception):
            page.screenshot(path=str(debug_dir / "wechat_page.png"), full_page=True)
        with suppress(Exception):
            (debug_dir / "wechat_page.html").write_text(page.content(), encoding="utf-8")
        with suppress(Exception):
            (debug_dir / "current_url.txt").write_text(page.url, encoding="utf-8")
        if extra_payload is not None:
            with suppress(Exception):
                (debug_dir / "publish_context.json").write_text(
                    json.dumps(extra_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        return debug_dir

    def _clean_visible_text(self, value: Any) -> str:
        text = str(value or "")
        return re.sub(r"\s+", " ", text).strip()

