from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import PublishRecord
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    PlaywrightTimeoutError = Exception
    sync_playwright = None

try:
    import msvcrt
except ModuleNotFoundError:  # pragma: no cover - fallback for non-Windows environments
    msvcrt = None


class PublishService:
    NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    SUCCESS_HISTORY_STATUSES = {"published", "submitted_for_review", "submitted", "success"}
    META_NOISE_PREFIXES = (
        "账号定位：",
        "账号：",
        "账号名：",
        "以下是今日自动选中的热点选题",
        "写作要求：",
        "固定写法：",
        "推荐主结论：",
        "跟踪变量：",
        "入选理由：",
        "请围绕这条产业线索",
        "请重点分析",
        "主赛道：",
        "主关键词：",
        "匹配赛道：",
        "匹配关键词：",
        "可确认数字：",
        "标题：",
        "来源：",
        "发布日期：",
        "原文链接：",
        "补充片段：",
        "摘要：",
    )

    DEFAULT_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
    DEFAULT_TITLE_SELECTORS = [
        ".left-column .publish-editor-title-wrapper textarea",
        ".publish-editor-title-wrapper textarea",
        ".assistant-title .title-wrapper textarea",
        ".publish-editor-title textarea",
        ".editor-title textarea",
        "textarea[autocomplete='off'][placeholder]",
        "textarea[placeholder*='标题']",
        "textarea",
        "input[placeholder*='标题']",
        "input[type='text']",
    ]
    DEFAULT_EDITOR_SELECTORS = [
        ".left-column div.ProseMirror",
        ".syl-editor-wrap div.ProseMirror",
        ".syl-editor .ProseMirror",
        "div.ProseMirror",
        "div[contenteditable='true'][role='textbox']",
        "div[contenteditable='true'][data-placeholder*='正文']",
        "div[contenteditable='true']",
    ]
    DEFAULT_DRAFT_STATUS_SELECTORS = [
        ".publish-footer .footer-tip-save",
        ".footer-draft-save .footer-tip-save",
        ".footer-tip-save",
    ]
    DEFAULT_ARTICLE_IMAGE_TRIGGER_SELECTORS = [
        ".publish-editor .syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button.syl-toolbar-button",
        ".publish-editor .syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button",
        ".publish-editor .syl-editor-toolbar.visible.inline .syl-toolbar-tool.image",
        ".syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button.syl-toolbar-button",
        ".syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button",
        ".syl-editor-toolbar.visible.inline .syl-toolbar-tool.image",
        ".syl-editor-toolbar.visible .syl-toolbar-tool.image button.syl-toolbar-button",
        ".syl-editor-toolbar.visible .syl-toolbar-tool.image button",
        ".syl-toolbar-tool.image .syl-toolbar-button",
        ".syl-toolbar-tool.image button",
        ".syl-toolbar-tool.image",
        "[data-e2e='insertImage-btn']",
        "[data-e2e='editorInsertImage-btn']",
        ".syl-toolbar button[aria-label*='Image']",
        ".syl-toolbar button[aria-label*='image']",
        ".syl-toolbar button[aria-label*='图片']",
        ".editor-toolbar button[aria-label*='Image']",
        ".editor-toolbar button[aria-label*='image']",
        ".editor-toolbar button[aria-label*='图片']",
        "button[aria-label*='Image']",
        "button[aria-label*='image']",
        "button[aria-label*='图片']",
        "button:has-text('图片')",
    ]
    INLINE_IMAGE_DATA_TOKENS = (
        "数据",
        "装机",
        "订单",
        "出货",
        "并网",
        "项目",
        "GWh",
        "MWh",
        "MW",
        "亿元",
        "同比",
    )
    INLINE_IMAGE_ANALYSIS_TOKENS = (
        "产业链",
        "交付",
        "利润",
        "毛利",
        "验证",
        "节奏",
        "风险",
        "客户",
        "电站",
        "储能",
        "逆变器",
        "电池",
    )
    AI_DRAWER_MASK_SELECTORS = [
        "div.byte-drawer-mask",
        "div.byte-drawer-wrapper.ai-assistant-drawer div.byte-drawer-mask",
    ]
    AI_DRAWER_CLOSE_SELECTORS = [
        ".ai-assistant-drawer button[aria-label='关闭']",
        ".ai-assistant-drawer .close-btn",
        ".ai-assistant-drawer .byte-drawer-close",
        ".ai-assistant-drawer button:has-text('关闭')",
        ".ai-assistant-drawer [class*='close']",
    ]
    PUBLISH_EVENT_PATTERNS = (
        "/mp/agw/article/publish",
        "/mp/agw/draft/save_ugc_draft",
        "/mp/agw/draft/",
    )
    DIAGNOSTIC_EVENT_PATTERNS = (
        "/mp/agw/article/new",
        "/mp/agw/creator_center/user_info",
        "/mp/agw/media/get_user_base_info",
        "/mp/agw/media/check_user_auth",
    )

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.history_path = settings.output_dir / settings.publishing.history_filename
        self.growth_ops_service = GrowthOpsService(settings, logger)

    @contextmanager
    def _publish_session_lock(self, purpose: str):
        lock_path = self.settings.output_dir / ".publish_session.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            try:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                    handle.seek(0)
            except Exception:
                pass

            if msvcrt is None:
                yield
                return

            self.logger.info("Waiting for publish session lock (%s)...", purpose)
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(1)

            try:
                yield
            finally:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass

    def publish_run(self, run_dir: Path) -> dict[str, Path]:
        with self._publish_session_lock("publish run"):
            return self._publish_run_locked(run_dir)

    def _publish_run_locked(self, run_dir: Path) -> dict[str, Path]:
        if not self.settings.publishing.enabled:
            raise RuntimeError("Publishing is disabled. Set TOUTIAO_PUBLISH_ENABLED=1 first.")

        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        review_status = self._load_json(run_dir / "review_status.json")
        if not publish_meta:
            raise FileNotFoundError("publish_meta.json is missing.")
        if str(review_status.get("status", "")) != "approved":
            raise RuntimeError("Run is not approved yet. Approve review before publishing.")

        topic = publish_meta.get("selected_topic") or {}
        topic_title = str(topic.get("title", ""))
        topic_url = str(topic.get("url", ""))
        if not self.settings.publishing.allow_duplicate_topics and self._was_topic_published(topic_title, topic_url):
            raise RuntimeError("This topic has already been published today. Duplicate publish blocked.")

        title = self._clip_title(str(publish_meta.get("selected_title", "")).strip())
        article = self._sanitize_article_for_publish(self._read_text(publish_dir / "article.md").strip())
        preflight_summary = self._collect_preflight_summary_locked()
        blocking_flags = list(preflight_summary.get("blocking_flags", []))
        should_bypass_preflight = self._should_bypass_preflight_blockers(preflight_summary)
        if blocking_flags and not should_bypass_preflight:
            raise RuntimeError("Publish preflight blocked: " + " | ".join(str(item) for item in blocking_flags))
        if blocking_flags and should_bypass_preflight:
            self.logger.warning(
                "Publish preflight reported blocking flags, but the run is allowed to continue: %s",
                " | ".join(str(item) for item in blocking_flags),
            )

        try:
            result_payload = self._publish_to_toutiao(
                title=title,
                article=article,
            )
        except Exception as exc:
            self._write_failed_publish_result(
                publish_dir=publish_dir,
                run_dir=run_dir,
                title=title,
                topic_title=topic_title,
                topic_url=topic_url,
                error=str(exc),
                preflight_summary=preflight_summary,
            )
            raise

        record = PublishRecord(
            published_at=datetime.now().isoformat(timespec="seconds"),
            platform=self.settings.publishing.platform,
            title=title,
            run_dir=str(run_dir),
            topic_title=topic_title,
            topic_url=topic_url,
            status=str(result_payload.get("status", "published")),
            notes=[str(item) for item in result_payload.get("notes", [])],
            external_url=str(result_payload.get("external_url", "")),
        )
        history = self._load_history()
        history.append(record)
        self._save_history(history)

        publish_result_path = publish_dir / "publish_result.json"
        result_payload = self._normalize_publish_result_payload(result_payload)
        publish_result_path.write_text(
            json.dumps(
                {
                    **result_payload,
                    "preflight_summary": preflight_summary,
                    "record": asdict(record),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        growth_exports = self.growth_ops_service.register_publish_event(
            platform=self.settings.publishing.platform,
            run_dir=run_dir,
            result_path=publish_result_path,
        )
        self.logger.info("Publish finished for run: %s", run_dir)
        return {
            "publish_result": publish_result_path,
            "publish_history": self.history_path,
            **growth_exports,
        }

    def initialize_login_session(self) -> dict[str, str]:
        with self._publish_session_lock("login initialization"):
            return self._initialize_login_session_locked()

    def _initialize_login_session_locked(self) -> dict[str, str]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        target_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            try:
                page = browser.new_page()
                diagnostic_events: list[dict[str, Any]] = []
                self._install_publish_diagnostic_capture(page, diagnostic_events)
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                page.wait_for_timeout(2500)
                print("")
                print("Browser launched for Toutiao login session initialization.")
                print(f"Profile dir: {profile_dir}")
                print("Log in to your Toutiao creator account in the opened browser window.")
                print("Make sure the account can actually open the article publish page.")
                print("This window will keep polling automatically for up to 10 minutes.")
                print("If login succeeds, navigate to the Toutiao article publish page and wait for the preflight summary.")

                deadline = datetime.now().timestamp() + 600
                next_status_print_at = 0.0
                preflight_summary: dict[str, Any] | None = None

                while datetime.now().timestamp() < deadline:
                    if page.is_closed():
                        raise RuntimeError("The browser window was closed before login initialization finished.")

                    try:
                        diagnostics = self._build_publish_diagnostics(diagnostic_events)
                        preflight_summary = self._build_preflight_summary_from_diagnostics(page.url, diagnostics)
                    except Exception:
                        preflight_summary = None

                    if preflight_summary and not preflight_summary.get("blocking_flags"):
                        break

                    now_ts = datetime.now().timestamp()
                    if now_ts >= next_status_print_at:
                        current_url = page.url if not page.is_closed() else ""
                        blockers = []
                        if preflight_summary:
                            blockers = [str(item) for item in preflight_summary.get("blocking_flags", [])]
                        status_line = "Waiting for a publish-capable Toutiao session"
                        if current_url:
                            status_line += f". Current URL: {current_url}"
                        if blockers:
                            status_line += f" | blockers: {'; '.join(blockers[:3])}"
                        print(status_line)
                        next_status_print_at = now_ts + 15

                    page.wait_for_timeout(3000)

                if not preflight_summary or preflight_summary.get("blocking_flags"):
                    export_paths = self._write_preflight_summary(preflight_summary or {})
                    raise RuntimeError(
                        "Login session initialization timed out before the account reached a publish-capable state. "
                        f"Current URL: {page.url}. Preflight summary saved to: {export_paths.get('publish_preflight')}"
                    )

                export_paths = self._write_preflight_summary(preflight_summary)
                print("")
                print("Publish preflight summary:")
                print(json.dumps(preflight_summary, ensure_ascii=False, indent=2))
                return {
                    "profile_dir": str(profile_dir),
                    "current_url": page.url,
                    **{name: str(path) for name, path in export_paths.items()},
                }
            finally:
                browser.close()

    def collect_preflight_summary(self) -> dict[str, Any]:
        with self._publish_session_lock("preflight collection"):
            return self._collect_preflight_summary_locked()

    def _collect_preflight_summary_locked(self) -> dict[str, Any]:
        if sync_playwright is None:
            return {
                "blocking_flags": ["Playwright is not installed."],
                "warnings": [],
                "diagnostics": {},
            }

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        publish_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.settings.publishing.headless,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            try:
                page = browser.new_page()
                diagnostic_events: list[dict[str, Any]] = []
                self._install_publish_diagnostic_capture(page, diagnostic_events)
                page.goto(
                    publish_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                page.wait_for_timeout(2500)
                diagnostics = self._build_publish_diagnostics(diagnostic_events)
                return self._build_preflight_summary_from_diagnostics(page.url, diagnostics)
            except Exception as exc:
                return {
                    "blocking_flags": [f"Publish preflight failed: {self._clean_visible_text(exc)}"],
                    "warnings": [],
                    "diagnostics": {},
                }
            finally:
                browser.close()

    def export_preflight_summary(self) -> dict[str, Path]:
        summary = self.collect_preflight_summary()
        return self._write_preflight_summary(summary)

    def _publish_to_toutiao(self, title: str, article: str) -> dict[str, Any]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        publish_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL
        selectors = self.settings.publishing.selectors
        notes: list[str] = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.settings.publishing.headless,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            try:
                page = browser.new_page()
                publish_events: list[dict[str, Any]] = []
                diagnostic_events: list[dict[str, Any]] = []
                self._install_publish_event_capture(page, publish_events)
                self._install_publish_diagnostic_capture(page, diagnostic_events)

                page.goto(
                    publish_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                page.wait_for_timeout(1500)

                if self.settings.publishing.base_url not in page.url:
                    notes.append(f"Current page redirected to: {page.url}")

                publish_editor_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL
                if "/profile_v4/graphic/publish" not in page.url:
                    if not self._maybe_click(page, selectors.get("new_post_button", [])):
                        notes.append("New post button not clicked. Falling back to direct publish URL.")
                        page.goto(
                            publish_editor_url,
                            wait_until="domcontentloaded",
                            timeout=self.settings.publishing.wait_timeout_ms,
                        )
                        page.wait_for_timeout(1500)

                self._dismiss_ai_assistant_drawer(page, notes)
                self._wait_for_publish_editor_ready(page)
                title_selector = self._resolve_editor_selector(
                    page,
                    selectors.get("title_input", []),
                    self.DEFAULT_TITLE_SELECTORS,
                )
                editor_selector = self._resolve_editor_selector(
                    page,
                    selectors.get("article_editor", []),
                    self.DEFAULT_EDITOR_SELECTORS,
                )

                if (not title_selector or not editor_selector) and page.url != publish_editor_url:
                    notes.append("Editor selectors not found on current page. Retrying publish URL directly.")
                    page.goto(
                        publish_editor_url,
                        wait_until="domcontentloaded",
                        timeout=self.settings.publishing.wait_timeout_ms,
                    )
                    page.wait_for_timeout(1500)
                    self._dismiss_ai_assistant_drawer(page, notes)
                    self._wait_for_publish_editor_ready(page)
                    title_selector = self._resolve_editor_selector(
                        page,
                        selectors.get("title_input", []),
                        self.DEFAULT_TITLE_SELECTORS,
                    )
                    editor_selector = self._resolve_editor_selector(
                        page,
                        selectors.get("article_editor", []),
                        self.DEFAULT_EDITOR_SELECTORS,
                    )

                if not title_selector or not editor_selector:
                    diagnostics = self._build_publish_diagnostics(diagnostic_events)
                    debug_dir = self._capture_debug_artifacts(
                        page,
                        extra_payload={
                            "publish_events": publish_events,
                            "publish_diagnostics": diagnostics,
                        },
                    )
                    raise RuntimeError(
                        "Could not locate title/editor selectors. "
                        f"Current URL: {page.url}. Debug files saved to: {debug_dir}"
                    )

                draft_watch_start = len(publish_events)
                self._dismiss_ai_assistant_drawer(page, notes)
                title_locator = page.locator(title_selector).first
                editor_locator = page.locator(editor_selector).first
                self._set_title_content(page, title_locator, title, notes)
                self._dismiss_ai_assistant_drawer(page, notes)
                self._set_editor_content(page, editor_selector, article, notes)
                self._apply_default_work_declarations(page, notes)
                self._apply_safe_publish_options(page, notes)
                self._finalize_editor_state(page, title_locator, editor_selector, title, notes)
                self._dismiss_ai_assistant_drawer(page, notes)
                cover_mode = "none"
                self._ensure_no_cover_mode(page, notes)
                self._stabilize_publish_form(page, notes)

                diagnostics = self._build_publish_diagnostics(diagnostic_events)
                preflight_blockers = self._format_publish_diagnostic_blockers(diagnostics)
                if preflight_blockers:
                    notes.append(f"Publish preflight: {preflight_blockers.strip()}")

                if not self._wait_for_draft_save(
                    page,
                    publish_events,
                    notes,
                    start_index=draft_watch_start,
                ):
                    notes.append("Draft autosave was not confirmed. Continuing with a direct publish attempt.")

                self._dismiss_ai_assistant_drawer(page, notes)
                page.wait_for_timeout(700)
                notes.append("Waited briefly before clicking the primary publish action.")
                preview_start_index = len(publish_events)
                if not self._click_immediate_publish_button(
                    page,
                    selectors.get("publish_button", []),
                    notes,
                ):
                    raise RuntimeError("Publish button not found. Update selectors in config.yaml.")

                page.wait_for_timeout(600)
                publish_start_index = self._complete_publish_confirmation_flow(
                    page,
                    publish_events,
                    notes,
                    fallback_start_index=preview_start_index,
                )

                publish_event = self._wait_for_publish_submit(page, publish_events, publish_start_index)
                response_payload = publish_event.get("response_json")
                if not self._publish_response_is_success(response_payload):
                    diagnostics = self._build_publish_diagnostics(diagnostic_events)
                    debug_dir = self._capture_debug_artifacts(
                        page,
                        extra_payload={
                            "publish_events": publish_events,
                            "failed_publish_event": publish_event,
                            "publish_diagnostics": diagnostics,
                        },
                    )
                    code, message = self._extract_publish_error(response_payload)
                    blocker_text = self._format_publish_diagnostic_blockers(diagnostics)
                    raise RuntimeError(
                        f"Toutiao publish rejected the submission. code={code}, message={message}. "
                        f"{blocker_text}"
                        f"Debug files saved to: {debug_dir}"
                    )

                success_selector = self._wait_for_any(page, selectors.get("success_indicator", []))
                if success_selector:
                    notes.append(f"Detected success indicator: {success_selector}")
                else:
                    notes.append("Backend accepted the publish request, but no explicit success toast was detected.")

                return {
                    "status": "submitted_for_review",
                    "published_at": datetime.now().isoformat(timespec="seconds"),
                    "external_url": page.url,
                    "notes": self._finalize_notes(notes),
                    "cover_mode": cover_mode,
                    "publish_diagnostics": self._compact_publish_diagnostics(diagnostics),
                    "backend_response_summary": self._compact_backend_response(response_payload),
                    "publish_request_summary": {
                        "url": str(publish_event.get("url", "")),
                        "save_mode": publish_event.get("save_mode"),
                    },
                }
            finally:
                browser.close()

    def _resolve_profile_dir(self) -> Path:
        path = Path(self.settings.publishing.user_data_dir)
        if not path.is_absolute():
            path = self.settings.project_root / path
        return path

    def _clip_title(self, title: str) -> str:
        title = " ".join(self._sanitize_publish_text(title).split()).strip()
        limit = max(1, int(self.settings.publishing.title_max_length))
        if len(title) <= limit:
            return title
        return title[:limit].rstrip(" \t\r\n,，。！？、:：;；-")

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_preflight_summary(self, summary: dict[str, Any]) -> dict[str, Path]:
        automation_dir = self.settings.automation_dir
        automation_dir.mkdir(parents=True, exist_ok=True)
        summary_path = automation_dir / "publish_preflight.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"publish_preflight": summary_path}

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _sanitize_article_for_publish(self, article: str) -> str:
        cleaned = clean_article_text(article)
        sanitized_lines: list[str] = []
        for raw_line in cleaned.splitlines():
            line = self._sanitize_publish_text(raw_line.strip())
            if not line and sanitized_lines and sanitized_lines[-1] != "":
                sanitized_lines.append("")
                continue
            if line:
                sanitized_lines.append(line)
        return "\n".join(sanitized_lines).strip()

    def _sanitize_publish_text(self, text: str) -> str:
        cleaned = str(text or "")
        brand = str(self.settings.brand_name or "").strip()
        if brand:
            cleaned = cleaned.replace(brand, "")
        cleaned = re.sub(r"(我们账号|本账号|本号|本栏目|本文作者|笔者认为|作者认为)[:：]?", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"^[，。；：:、\-\s]+", "", cleaned)
        cleaned = re.sub(r"[，。；：:、\-\s]+$", "", cleaned)
        return cleaned.strip()

    def _resolve_editor_selector(
        self,
        page,
        configured_selectors: list[str],
        fallback_selectors: list[str],
    ) -> str | None:
        ordered: list[str] = []
        for selector in [*configured_selectors, *fallback_selectors]:
            if selector and selector not in ordered:
                ordered.append(selector)
        for _ in range(8):
            resolved = self._first_visible_selector(page, ordered)
            if resolved:
                return resolved
            page.wait_for_timeout(350)
        return None

    def _wait_for_publish_editor_ready(self, page) -> None:
        editor_shell_selectors = [
            ".left-column .publish-editor-title-wrapper",
            ".publish-editor-title-wrapper",
            ".left-column .syl-editor-wrap",
            ".syl-editor-wrap",
            ".left-column div.ProseMirror",
            "div.ProseMirror",
        ]
        deadline = datetime.now().timestamp() + (max(self.settings.publishing.wait_timeout_ms, 12000) / 1000)
        while datetime.now().timestamp() < deadline:
            if self._first_visible_selector(page, editor_shell_selectors, strict=False):
                return
            page.wait_for_timeout(300)

    def _dismiss_ai_assistant_drawer(self, page, notes: list[str]) -> None:
        dismissed = False

        for selector in self.AI_DRAWER_CLOSE_SELECTORS:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=500)
                locator.click(force=True, timeout=1000)
                page.wait_for_timeout(500)
                dismissed = True
                break
            except Exception:
                continue

        if not dismissed:
            for selector in self.AI_DRAWER_MASK_SELECTORS:
                try:
                    locator = page.locator(selector).first
                    locator.wait_for(state="visible", timeout=500)
                    locator.click(force=True, timeout=1000)
                    page.wait_for_timeout(500)
                    dismissed = True
                    break
                except Exception:
                    continue

        if not dismissed:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass

        overlay_still_visible = False
        for selector in self.AI_DRAWER_MASK_SELECTORS:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=500)
                overlay_still_visible = True
                break
            except Exception:
                continue

        if overlay_still_visible:
            try:
                page.evaluate(
                    """() => {
                        const selectors = [
                            '.byte-drawer-mask',
                            '.byte-drawer-wrapper.ai-assistant-drawer',
                        ];
                        for (const selector of selectors) {
                            document.querySelectorAll(selector).forEach((node) => node.remove());
                        }
                    }"""
                )
                page.wait_for_timeout(300)
                notes.append("Dismissed AI assistant overlay via DOM fallback.")
            except Exception:
                pass
        elif dismissed:
            notes.append("Dismissed AI assistant overlay before editor interaction.")

    def _set_editor_content(self, page, selector: str, article: str, notes: list[str]) -> None:
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)

        try:
            locator.click(force=True, timeout=1000)
        except Exception:
            notes.append("Editor click was intercepted once. Continuing with keyboard input.")

        paragraphs = self._split_article_paragraphs(article)
        if not paragraphs:
            raise RuntimeError("Article content is empty after sanitization.")

        attempt_notes: list[str] = []

        try:
            self._clear_editor_content(page, locator)
            self._paste_editor_content(page, locator, paragraphs)
            page.wait_for_timeout(500)
            if self._editor_content_matches(locator, paragraphs):
                return
            attempt_notes.append("clipboard paste verification failed")
        except Exception as exc:
            attempt_notes.append(f"clipboard paste failed: {exc}")

        try:
            self._clear_editor_content(page, locator)
            self._inject_editor_content_via_dom(page, locator, paragraphs)
            page.wait_for_timeout(500)
            if self._editor_content_matches(locator, paragraphs):
                notes.append("Editor content required DOM injection fallback.")
                return
            attempt_notes.append("dom injection verification failed")
        except Exception as exc:
            attempt_notes.append(f"dom injection failed: {exc}")

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={
                "editor_write_attempts": attempt_notes,
                "expected_paragraph_count": len(paragraphs),
                "expected_preview": paragraphs[0][:120],
            },
        )
        raise RuntimeError(
            "Failed to write article content into editor. "
            f"Attempts: {'; '.join(attempt_notes) or 'unknown'}. "
            f"Current URL: {page.url}. Debug files saved to: {debug_dir}"
        )

    def _clear_editor_content(self, page, locator) -> None:
        try:
            locator.click(force=True, timeout=1000)
        except Exception:
            pass

        try:
            locator.focus(timeout=1000)
        except Exception:
            pass

        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(150)
        except Exception:
            pass

        try:
            locator.evaluate(
                """(node) => {
                    node.innerHTML = "";
                    node.dispatchEvent(new InputEvent("input", {
                        bubbles: true,
                        cancelable: true,
                        inputType: "deleteContentBackward",
                        data: null,
                    }));
                    node.dispatchEvent(new Event("change", { bubbles: true }));
                }"""
            )
            page.wait_for_timeout(120)
        except Exception:
            pass

    def _paste_editor_content(self, page, locator, paragraphs: list[str]) -> None:
        content = "\n\n".join(paragraphs)
        try:
            page.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=self.settings.publishing.base_url,
            )
        except Exception:
            pass

        locator.focus(timeout=1000)
        page.evaluate("(text) => navigator.clipboard.writeText(text)", content)
        page.keyboard.press("Control+V")

    def _inject_editor_content_via_dom(self, page, locator, paragraphs: list[str]) -> None:
        locator.evaluate(
            """(node, payload) => {
                const paragraphs = payload.paragraphs || [];
                node.focus();
                node.innerHTML = "";
                for (const text of paragraphs) {
                    const p = document.createElement("p");
                    p.className = "pgc-p";
                    p.textContent = text;
                    node.appendChild(p);
                }
                const tail = document.createElement("p");
                tail.appendChild(document.createElement("br"));
                node.appendChild(tail);
                node.dispatchEvent(new InputEvent("input", {
                    bubbles: true,
                    cancelable: true,
                    inputType: "insertFromPaste",
                    data: paragraphs.join("\\n\\n"),
                }));
                node.dispatchEvent(new Event("change", { bubbles: true }));
            }""",
            {"paragraphs": paragraphs},
        )

    def _editor_content_matches(self, locator, paragraphs: list[str]) -> bool:
        try:
            editor_text = locator.inner_text(timeout=1200)
        except Exception:
            return False

        expected = re.sub(r"\s+", "", "".join(paragraphs))
        actual = re.sub(r"\s+", "", editor_text)
        if not actual:
            return False

        preview = expected[:40]
        if preview and preview not in actual:
            return False

        return len(actual) >= min(len(expected), 80)

    def _try_apply_article_images(
        self,
        page,
        editor_selector: str,
        article: str,
        image_paths: list[Path],
        notes: list[str],
        *,
        first_image_as_cover: bool = False,
    ) -> int:
        available_images = self._dedupe_article_image_paths(image_paths, notes)
        if not available_images:
            notes.append("Article images not found. Skipping inline image upload.")
            return 0

        target_paragraphs = self._resolve_inline_image_targets(
            article,
            len(available_images),
            first_image_as_cover=first_image_as_cover,
        )
        inserted = 0
        for image_path, target_paragraph in zip(available_images, target_paragraphs):
            if self._try_apply_article_image(page, editor_selector, image_path, notes, target_paragraph=target_paragraph):
                inserted += 1
        return inserted

    def _dedupe_article_image_paths(self, image_paths: list[Path], notes: list[str]) -> list[Path]:
        unique_paths: list[Path] = []
        seen_keys: set[str] = set()
        for path in image_paths:
            if not path.exists():
                continue
            key = self._build_image_dedupe_key(path)
            if key in seen_keys:
                notes.append(f"Skipped duplicate article image asset: {path.name}")
                continue
            seen_keys.add(key)
            unique_paths.append(path)
        return unique_paths

    def _build_image_dedupe_key(self, path: Path) -> str:
        try:
            return hashlib.sha1(path.read_bytes()).hexdigest()
        except Exception:
            return str(path.resolve())

    def _resolve_inline_image_targets(
        self,
        article: str,
        image_count: int,
        *,
        first_image_as_cover: bool = False,
    ) -> list[int]:
        paragraphs = [block.strip() for block in article.split("\n\n") if block.strip()]
        paragraph_count = len(paragraphs)
        if image_count <= 0:
            return []
        if first_image_as_cover:
            if image_count == 1:
                return [0]
            analysis_target = self._best_inline_image_target(
                paragraphs,
                self.INLINE_IMAGE_ANALYSIS_TOKENS,
                fallback=min(max(3, 2), max(paragraph_count, 1)),
            )
            return [0, analysis_target][:image_count]
        if paragraph_count <= 2:
            return [1] * image_count
        if image_count == 1:
            return [self._best_inline_image_target(paragraphs, self.INLINE_IMAGE_DATA_TOKENS, fallback=2)]

        first_target = self._best_inline_image_target(paragraphs, self.INLINE_IMAGE_DATA_TOKENS, fallback=2)
        second_target = self._best_inline_image_target(
            paragraphs,
            self.INLINE_IMAGE_ANALYSIS_TOKENS,
            fallback=min(max(4, first_target + 2), paragraph_count),
            exclude={first_target},
        )
        return [first_target, second_target][:image_count]

    def _best_inline_image_target(
        self,
        paragraphs: list[str],
        preferred_tokens: tuple[str, ...],
        *,
        fallback: int,
        exclude: set[int] | None = None,
    ) -> int:
        if not paragraphs:
            return 1

        excluded = exclude or set()
        best_index = min(max(fallback, 1), len(paragraphs))
        best_score = float("-inf")

        for index, paragraph in enumerate(paragraphs, start=1):
            if index in excluded:
                continue

            score = 0
            length = len(paragraph)
            if 40 <= length <= 150:
                score += 8
            elif length < 28:
                score -= 6
            elif length > 190:
                score -= 4

            if re.search(r"\d", paragraph):
                score += 10

            score += sum(4 for token in preferred_tokens if token and token in paragraph)

            if index == 1:
                score -= 5
            if paragraph.endswith(("？", "?")):
                score -= 4

            distance_penalty = abs(index - fallback)
            score -= distance_penalty

            if score > best_score:
                best_score = score
                best_index = index

        return best_index

    def _try_apply_article_image(
        self,
        page,
        editor_selector: str,
        image_path: Path,
        notes: list[str],
        *,
        target_paragraph: int,
    ) -> bool:
        if not image_path.exists():
            notes.append("Article image not found. Skipping inline image upload.")
            return False

        try:
            self._apply_article_image(page, editor_selector, image_path, notes, target_paragraph=target_paragraph)
            return True
        except Exception as exc:
            debug_dir = self._capture_debug_artifacts(
                page,
                extra_payload={
                    "inline_image_path": str(image_path),
                    "target_paragraph": target_paragraph,
                },
            )
            notes.append(
                "Inline article image upload failed and was skipped: "
                f"{self._clean_visible_text(exc)}. Debug files saved to: {debug_dir}"
            )
            return False

    def _apply_article_image(
        self,
        page,
        editor_selector: str,
        image_path: Path,
        notes: list[str],
        *,
        target_paragraph: int,
    ) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        editor_locator = page.locator(editor_selector).first
        editor_locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)
        previous_image_count = self._count_editor_images(
            page,
            editor_selector=editor_selector,
            editor_locator=editor_locator,
        )

        try:
            editor_locator.click(force=True, timeout=1200)
        except Exception:
            pass

        try:
            editor_locator.focus(timeout=1000)
        except Exception:
            pass

        insertion_paragraph = self._position_editor_caret_for_inline_image(
            page,
            editor_locator,
            target_paragraph=target_paragraph,
        )

        self._prime_article_image_toolbar(page, editor_locator)

        trigger_locator = self._resolve_article_image_trigger(page)
        if trigger_locator is None:
            raise RuntimeError("Could not locate the article image upload trigger.")

        existing_input_count = page.locator("input[type='file']").count()
        file_chooser = self._open_article_image_file_chooser(page, trigger_locator)
        if file_chooser is not None:
            file_chooser.set_files(str(image_path))
        else:
            upload_input = self._wait_for_article_upload_input(page, existing_input_count)
            if upload_input is None:
                self._click_article_image_trigger(trigger_locator)
                upload_input = self._wait_for_article_upload_input(page, existing_input_count)
            if upload_input is None:
                raise RuntimeError("Article image upload input did not appear.")
            upload_input.set_input_files(str(image_path), timeout=max(self.settings.publishing.wait_timeout_ms, 60000))

        self._confirm_article_image_selection(
            page,
            editor_selector,
            editor_locator,
            previous_image_count,
        )
        self._wait_for_article_image_inserted(
            page,
            editor_selector,
            editor_locator,
            previous_image_count,
        )

        try:
            editor_locator.click(force=True, timeout=1200)
            page.keyboard.press("ArrowRight")
            page.keyboard.press("Enter")
        except Exception:
            pass

        notes.append(
            f"Inserted inline article image after paragraph {insertion_paragraph}: {image_path.name}"
        )

    def _prime_article_image_toolbar(self, page, editor_locator) -> None:
        self._dismiss_ai_assistant_drawer(page, [])
        try:
            editor_locator.evaluate(
                """(node) => {
                    node.focus();
                    const selection = window.getSelection();
                    if (!selection || selection.rangeCount === 0) {
                        return;
                    }
                    const anchor = selection.anchorNode;
                    const element = anchor && anchor.nodeType === Node.TEXT_NODE ? anchor.parentElement : anchor;
                    if (element && element.scrollIntoView) {
                        element.scrollIntoView({ block: 'center', inline: 'nearest' });
                    } else if (node.scrollIntoView) {
                        node.scrollIntoView({ block: 'center', inline: 'nearest' });
                    }
                }"""
            )
        except Exception:
            pass

        for _ in range(2):
            try:
                page.locator(".syl-editor-toolbar.visible.inline").first.wait_for(
                    state="visible",
                    timeout=800,
                )
                return
            except Exception:
                try:
                    editor_locator.focus(timeout=800)
                except Exception:
                    pass
                page.wait_for_timeout(180)

    def _position_editor_caret_for_inline_image(self, page, editor_locator, *, target_paragraph: int) -> int:
        try:
            paragraph_count = int(
                editor_locator.evaluate(
                    """(node) => {
                        const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'));
                        return blocks.filter((item) => item.textContent && item.textContent.trim()).length;
                    }"""
                )
            )
        except Exception:
            paragraph_count = 0

        if target_paragraph <= 0:
            if self._place_editor_caret_via_click(
                page,
                editor_locator,
                target_index=1,
                insert_before=True,
            ):
                return 1

            try:
                actual_index = int(
                    editor_locator.evaluate(
                        """(node) => {
                            const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'))
                                .filter((item) => item.textContent && item.textContent.trim());
                            const target = blocks[0];
                            if (!target) {
                                node.focus();
                                return 0;
                            }

                            if (target.scrollIntoView) {
                                target.scrollIntoView({ block: 'center', inline: 'nearest' });
                            }

                            const range = document.createRange();
                            range.selectNodeContents(target);
                            range.collapse(true);

                            const selection = window.getSelection();
                            if (selection) {
                                selection.removeAllRanges();
                                selection.addRange(range);
                            }

                            node.focus();
                            return 1;
                        }"""
                    )
                )
            except Exception:
                actual_index = 0

            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(120)
            except Exception:
                pass
            return actual_index or 1

        target_index = min(max(target_paragraph, 1), max(paragraph_count, 1))
        if self._place_editor_caret_via_click(
            page,
            editor_locator,
            target_index=target_index,
            insert_before=False,
        ):
            return target_index

        try:
            actual_index = int(
                editor_locator.evaluate(
                    """(node, payload) => {
                        const desired = payload.targetIndex || 1;
                        const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'))
                            .filter((item) => item.textContent && item.textContent.trim());
                        const target = blocks[Math.max(0, Math.min(blocks.length, desired) - 1)];
                        if (!target) {
                            node.focus();
                            return 0;
                        }

                        if (target.scrollIntoView) {
                            target.scrollIntoView({ block: 'center', inline: 'nearest' });
                        }

                        const range = document.createRange();
                        range.selectNodeContents(target);
                        range.collapse(false);

                        const selection = window.getSelection();
                        if (selection) {
                            selection.removeAllRanges();
                            selection.addRange(range);
                        }

                        node.focus();
                        return blocks.indexOf(target) + 1;
                    }""",
                    {"targetIndex": target_index},
                )
            )
        except Exception:
            actual_index = 0

        if actual_index <= 0:
            for key in ("Control+End", "End"):
                try:
                    page.keyboard.press(key)
                    page.wait_for_timeout(120)
                    break
                except Exception:
                    continue
            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(120)
            except Exception:
                pass
            return max(1, paragraph_count)

        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(120)
        except Exception:
            pass
        return actual_index

    def _place_editor_caret_via_click(
        self,
        page,
        editor_locator,
        *,
        target_index: int,
        insert_before: bool,
    ) -> bool:
        try:
            rect = editor_locator.evaluate(
                """(node, payload) => {
                    const desired = payload.targetIndex || 1;
                    const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'))
                        .filter((item) => item.textContent && item.textContent.trim());
                    const target = blocks[Math.max(0, Math.min(blocks.length - 1, desired - 1))];
                    if (!target) {
                        return null;
                    }

                    if (target.scrollIntoView) {
                        target.scrollIntoView({ block: 'center', inline: 'nearest' });
                    }

                    const box = target.getBoundingClientRect();
                    return {
                        left: box.left,
                        top: box.top,
                        width: box.width,
                        height: box.height,
                    };
                }""",
                {"targetIndex": target_index},
            )
        except Exception:
            return False

        if not isinstance(rect, dict):
            return False

        try:
            width = max(float(rect.get("width", 0.0)), 1.0)
            height = max(float(rect.get("height", 0.0)), 1.0)
            left = float(rect.get("left", 0.0))
            top = float(rect.get("top", 0.0))
        except Exception:
            return False

        click_x = left + min(28.0, width * 0.2) if insert_before else left + max(36.0, min(width - 20.0, width * 0.82))
        click_y = top + min(max(height / 2.0, 8.0), max(height - 4.0, 8.0))

        try:
            page.mouse.click(click_x, click_y)
            page.wait_for_timeout(120)
            page.keyboard.press("Home" if insert_before else "End")
            page.wait_for_timeout(80)
            page.keyboard.press("Enter")
            page.wait_for_timeout(140)
            return True
        except Exception:
            return False

    def _resolve_article_image_trigger(self, page):
        for toolbar_selector in [
            ".publish-editor .syl-editor-toolbar.visible.inline",
            ".syl-editor-toolbar.visible.inline",
            ".publish-editor .syl-editor-toolbar.visible",
            ".syl-editor-toolbar.visible",
        ]:
            try:
                toolbar = page.locator(toolbar_selector).first
                toolbar.wait_for(state="visible", timeout=700)
            except Exception:
                continue

            for button_selector in [
                ".syl-toolbar-tool.image button.syl-toolbar-button",
                ".syl-toolbar-tool.image button",
                ".syl-toolbar-tool.image",
            ]:
                try:
                    locator = toolbar.locator(button_selector).first
                    locator.wait_for(state="visible", timeout=500)
                    return locator
                except Exception:
                    continue

        for selector in self.DEFAULT_ARTICLE_IMAGE_TRIGGER_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=600)
                return locator
            except Exception:
                continue
        return None

    def _open_article_image_file_chooser(self, page, trigger_locator):
        try:
            with page.expect_file_chooser(timeout=1800) as chooser_info:
                self._click_article_image_trigger(trigger_locator)
            return chooser_info.value
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    def _click_article_image_trigger(self, trigger_locator) -> None:
        click_errors: list[str] = []
        for strategy in ("click", "dispatch", "dom"):
            try:
                if strategy == "click":
                    trigger_locator.click(force=True, timeout=1500)
                elif strategy == "dispatch":
                    trigger_locator.dispatch_event("click")
                else:
                    trigger_locator.evaluate("(node) => node.click()")
                return
            except Exception as exc:
                click_errors.append(str(exc))
                continue
        raise RuntimeError(
            "Failed to activate the article image trigger. "
            + " | ".join(item for item in click_errors if item)
        )

    def _wait_for_article_upload_input(self, page, existing_count: int):
        scoped_selectors = [
            ".upload-image-panel .btn-upload-handle input[type='file']",
            ".upload-image-panel .upload-handler input[type='file']",
            ".upload-image-panel .image-item-upload input[type='file']",
            ".upload-image-panel input#upload-drag-input",
            ".upload-image-panel input[type='file']",
            ".byte-modal-wrapper .upload-image-panel input[type='file']",
        ]
        fallback_selectors = [
            ".btn-upload-handle input[type='file']",
            "input#upload-drag-input",
            "input[type='file']",
        ]
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 15000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        while datetime.now().timestamp() < deadline:
            panel_visible = False
            try:
                panel_visible = page.locator(".upload-image-panel").first.is_visible(timeout=300)
            except Exception:
                panel_visible = False

            selectors = scoped_selectors if panel_visible else fallback_selectors
            for selector in selectors:
                try:
                    file_inputs = page.locator(selector)
                    count = file_inputs.count()
                except Exception:
                    continue

                for index in range(count - 1, -1, -1):
                    if not panel_visible and selector == "input[type='file']" and count <= existing_count and index < existing_count:
                        continue
                    locator = file_inputs.nth(index)
                    try:
                        accept_value = (locator.get_attribute("accept") or "").lower()
                    except Exception:
                        accept_value = ""
                    if accept_value and "image" not in accept_value and ".png" not in accept_value:
                        continue
                    try:
                        locator.wait_for(state="attached", timeout=400)
                        return locator
                    except Exception:
                        continue

            page.wait_for_timeout(200)
        return None

    def _wait_for_article_image_inserted(
        self,
        page,
        editor_selector: str,
        editor_locator,
        previous_count: int,
    ) -> None:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 30000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_count = previous_count
        while datetime.now().timestamp() < deadline:
            current_count = self._count_editor_images(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            )
            if current_count > previous_count:
                page.wait_for_timeout(500)
                return
            last_count = current_count
            page.wait_for_timeout(300)

        raise RuntimeError(
            "Article image upload did not result in an inline image appearing in the editor. "
            f"Previous image count: {previous_count}, current image count: {last_count}."
        )

    def _confirm_article_image_selection(
        self,
        page,
        editor_selector: str,
        editor_locator,
        previous_count: int,
    ) -> None:
        panel_visible = False
        try:
            page.locator(".upload-image-panel").first.wait_for(
                state="visible",
                timeout=max(4000, min(self.settings.publishing.wait_timeout_ms, 12000)),
            )
            panel_visible = True
        except Exception:
            panel_visible = False

        if not panel_visible:
            return

        selection_visible = False
        for selector in [
            ".pic-select-image-item",
            ".pic-select-image-item-wrap",
            ".upload-image-wrapper img",
            ".upload-image-wrapper li",
            ".upload-image-panel li.image-item",
            ".upload-image-panel .image-list li",
        ]:
            try:
                page.locator(selector).first.wait_for(
                    state="visible",
                    timeout=max(3000, min(self.settings.publishing.wait_timeout_ms, 15000)),
                )
                selection_visible = True
                break
            except Exception:
                continue

        if selection_visible:
            selected = False
            for selector in [
                ".pic-select-image-item",
                ".pic-select-image-item-wrap",
                ".upload-image-wrapper img",
                ".upload-image-wrapper li",
                ".upload-image-panel li.image-item",
                ".upload-image-panel .image-list li",
            ]:
                try:
                    candidates = page.locator(selector)
                    count = candidates.count()
                    for index in range(count - 1, -1, -1):
                        candidate = candidates.nth(index)
                        try:
                            if not candidate.is_visible(timeout=300):
                                continue
                        except Exception:
                            continue

                        try:
                            candidate_text = self._clean_visible_text(candidate.text_content(timeout=300) or "")
                        except Exception:
                            candidate_text = ""
                        if "更多" in candidate_text:
                            continue

                        candidate.click(force=True, timeout=1500)
                        selected = True
                        page.wait_for_timeout(500)
                        break
                    if selected:
                        break
                except Exception:
                    continue
            if not selected:
                raise RuntimeError("Article image upload panel opened, but no uploaded image could be selected.")

        confirm_clicked = self._maybe_click(
            page,
            [
                "[data-e2e='imageUploadConfirm-btn']",
                ".upload-image-panel .footer .byte-btn-primary",
                ".upload-image-panel button:has-text('确定')",
            ],
            required=False,
        )

        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 15000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        panel_closed = False
        current_count = previous_count

        while datetime.now().timestamp() < deadline:
            current_count = self._count_editor_images(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            )
            if current_count > previous_count:
                return

            try:
                panel_closed = not page.locator(".upload-image-panel").first.is_visible(timeout=300)
            except Exception:
                panel_closed = True

            if panel_closed and confirm_clicked:
                break
            page.wait_for_timeout(250)

        if current_count > previous_count:
            return

        if panel_closed and confirm_clicked:
            return

    def _count_editor_images(self, page, *, editor_selector: str | None = None, editor_locator=None) -> int:
        script = """(root) => {
            const seen = new Set();
            const register = (value) => {
                if (!value) {
                    return;
                }
                const normalized = String(value).trim();
                if (!normalized) {
                    return;
                }
                seen.add(normalized);
            };
            const imageSelector = [
                "img[web_uri]",
                "img[mime_type^='image/']",
                "img[src*='image_upload']",
                "img[src*='tplv-obj.image']",
                "img[src*='image-tt-private']",
            ].join(",");
            const blockSelector = [
                "div[__syl_tag='true'][contenteditable='false']",
                "div[__syl_tag='true']",
                ".pgc-image",
                ".pgc-img",
                ".editor-image",
            ].join(",");

            for (const block of root.querySelectorAll(blockSelector)) {
                const img = block.querySelector(imageSelector);
                register(
                    (img && (img.getAttribute("web_uri") || img.getAttribute("src"))) ||
                    block.getAttribute("data-node-key") ||
                    block.outerHTML.slice(0, 200)
                );
            }

            if (seen.size > 0) {
                return seen.size;
            }

            for (const img of root.querySelectorAll(imageSelector)) {
                register(
                    img.getAttribute("web_uri") ||
                    img.getAttribute("src") ||
                    img.outerHTML.slice(0, 200)
                );
            }

            return seen.size;
        }"""

        if editor_locator is not None:
            try:
                count = int(editor_locator.evaluate(script))
                if count > 0:
                    return count
            except Exception:
                pass

        try:
            return int(
                page.evaluate(
                    """(payload) => {
                        const selector = payload.selector;
                        const roots = selector
                            ? Array.from(document.querySelectorAll(selector))
                            : Array.from(document.querySelectorAll(".ProseMirror"));
                        if (!roots.length) {
                            return 0;
                        }

                        const seen = new Set();
                        const register = (value) => {
                            if (!value) {
                                return;
                            }
                            const normalized = String(value).trim();
                            if (!normalized) {
                                return;
                            }
                            seen.add(normalized);
                        };
                        const imageSelector = [
                            "img[web_uri]",
                            "img[mime_type^='image/']",
                            "img[src*='image_upload']",
                            "img[src*='tplv-obj.image']",
                            "img[src*='image-tt-private']",
                        ].join(",");
                        const blockSelector = [
                            "div[__syl_tag='true'][contenteditable='false']",
                            "div[__syl_tag='true']",
                            ".pgc-image",
                            ".pgc-img",
                            ".editor-image",
                        ].join(",");

                        for (const root of roots) {
                            for (const block of root.querySelectorAll(blockSelector)) {
                                const img = block.querySelector(imageSelector);
                                register(
                                    (img && (img.getAttribute("web_uri") || img.getAttribute("src"))) ||
                                    block.getAttribute("data-node-key") ||
                                    block.outerHTML.slice(0, 200)
                                );
                            }

                            for (const img of root.querySelectorAll(imageSelector)) {
                                register(
                                    img.getAttribute("web_uri") ||
                                    img.getAttribute("src") ||
                                    img.outerHTML.slice(0, 200)
                                );
                            }
                        }

                        return seen.size;
                    }""",
                    {"selector": editor_selector},
                )
            )
        except Exception:
            return 0

    def _verify_publish_media_state(
        self,
        page,
        editor_selector: str,
        *,
        cover_mode: str,
        expected_inline_images: int,
    ) -> None:
        if cover_mode == "single":
            try:
                page.locator(".article-cover img").first.wait_for(
                    state="visible",
                    timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
                )
            except Exception as exc:
                raise RuntimeError("Cover verification failed before publish.") from exc

        if expected_inline_images <= 0:
            return

        editor_locator = page.locator(editor_selector).first
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        while datetime.now().timestamp() < deadline:
            if self._count_editor_images(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            ) >= expected_inline_images:
                return
            page.wait_for_timeout(250)
        self.logger.warning(
            "Inline article image verification did not observe the expected count before publish. Continuing without blocking publish."
        )

    def _set_title_content(self, page, locator, title: str, notes: list[str]) -> None:
        locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)
        observed = ""

        for strategy in ("type", "fill", "dom"):
            self._clear_title_input(page, locator)
            try:
                if strategy == "type":
                    locator.type(title, delay=35)
                elif strategy == "fill":
                    locator.fill(title)
                else:
                    self._set_title_input_via_dom(locator, title)
            except Exception:
                continue

            page.wait_for_timeout(300)
            observed = self._read_title_input(locator)
            if self._title_matches(observed, title):
                if strategy != "fill":
                    notes.append(f"Title content required {strategy} fallback.")
                return

        try:
            self._clear_title_input(page, locator)
            page.keyboard.insert_text(title)
            page.wait_for_timeout(300)
            observed = self._read_title_input(locator)
            if self._title_matches(observed, title):
                notes.append("Title content required keyboard fallback.")
                return
        except Exception:
            pass

        raise RuntimeError(
            "Failed to write title content into the Toutiao title field. "
            f"Expected: {title!r}, observed: {observed!r}"
        )

    def _split_article_paragraphs(self, article: str) -> list[str]:
        return [line.strip() for line in article.splitlines() if line.strip()]

    def _clear_title_input(self, page, locator) -> None:
        try:
            locator.click(force=True, timeout=1000)
        except Exception:
            pass

        try:
            locator.focus(timeout=1000)
        except Exception:
            pass

        try:
            locator.fill("")
        except Exception:
            pass

        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(100)
        except Exception:
            pass

    def _set_title_input_via_dom(self, locator, title: str) -> None:
        locator.evaluate(
            """(node, value) => {
                const prototype = node instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                if (descriptor && descriptor.set) {
                    descriptor.set.call(node, value);
                } else {
                    node.value = value;
                }
                node.dispatchEvent(new Event('input', { bubbles: true }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            title,
        )

    def _read_title_input(self, locator) -> str:
        try:
            return locator.input_value(timeout=1000)
        except Exception:
            return ""

    def _title_matches(self, actual: str, expected: str) -> bool:
        return re.sub(r"\s+", "", actual or "") == re.sub(r"\s+", "", expected or "")

    def _apply_default_work_declarations(self, page, notes: list[str]) -> None:
        selected_labels: list[str] = []
        cleared_labels: list[str] = []

        desired_checked_groups = [
            [
                "label.byte-checkbox:has-text('取材网络')",
                "label.byte-checkbox:has(input[value='4'])",
            ],
            [
                "label.byte-checkbox:has-text('个人观点')",
                "label.byte-checkbox:has-text('仅供参考')",
                "label.byte-checkbox:has(input[value='2'])",
            ],
        ]
        desired_unchecked_groups = [
            [
                "label.byte-checkbox:has-text('引用AI')",
                "label.byte-checkbox:has(input[value='3'])",
            ],
        ]

        for selectors in desired_checked_groups:
            label = self._set_named_checkbox_state(page, selectors, checked=True)
            if label:
                selected_labels.append(label)

        for selectors in desired_unchecked_groups:
            label = self._set_named_checkbox_state(page, selectors, checked=False)
            if label:
                cleared_labels.append(label)

        if selected_labels:
            notes.append(f"Applied work declarations: {', '.join(selected_labels)}")
        if cleared_labels:
            notes.append(f"Cleared work declarations: {', '.join(cleared_labels)}")

    def _apply_safe_publish_options(self, page, notes: list[str]) -> None:
        applied_labels: list[str] = []
        label = self._set_named_checkbox_state(
            page,
            [
                "label.byte-checkbox:has-text('头条首发')",
                "label.byte-checkbox:has-text('声明首发')",
                "label.byte-checkbox:has(input[value='1'])",
            ],
            checked=True,
        )
        if label:
            applied_labels.append(label)

        if applied_labels:
            notes.append(f"Applied publish options: {', '.join(applied_labels)}")
        else:
            notes.append("Publish option selectors for first-release were not found.")

    def _set_named_checkbox_state(self, page, selectors: list[str], checked: bool) -> str:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=800)
                current_state = self._is_custom_control_selected(locator)
                label = self._clean_visible_text(locator.inner_text(timeout=500).strip()) or selector
                if current_state != checked:
                    self._click_custom_control(locator)
                    page.wait_for_timeout(250)
                    current_state = self._is_custom_control_selected(locator)
                if current_state == checked:
                    return label
            except Exception:
                continue
        return ""

    def _ensure_checkbox_state(self, page, selectors: list[str], checked: bool) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=800)
                current_state = self._is_custom_control_selected(locator)
                if current_state == checked:
                    return False
                self._click_custom_control(locator)
                page.wait_for_timeout(200)
                return self._is_custom_control_selected(locator) == checked
            except Exception:
                continue
        return False

    def _ensure_radio_state(self, page, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=800)
                if self._is_custom_control_selected(locator):
                    return False
                self._click_custom_control(locator)
                page.wait_for_timeout(200)
                return self._is_custom_control_selected(locator)
            except Exception:
                continue
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
                        if (node.classList.contains('byte-checkbox-checked')) {
                            return true;
                        }
                        if (node.getAttribute('aria-checked') === 'true') {
                            return true;
                        }
                        return !!node.querySelector('.byte-radio-inner.checked');
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
                        node.querySelector('.byte-checkbox-wrapper') ||
                        node.querySelector('.byte-radio-inner') ||
                        node.querySelector('input') ||
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

    def _apply_cover_image(self, page, cover_path: Path, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        self._ensure_single_cover_mode(page)
        self._clear_existing_cover(page, notes)

        trigger_locator = self._resolve_cover_trigger(page)
        if trigger_locator is None:
            raise RuntimeError("Could not locate the Toutiao cover upload trigger.")

        try:
            existing_input_count = page.locator("input[type='file']").count()
        except Exception:
            existing_input_count = 0

        file_chooser = self._open_cover_file_chooser(page, trigger_locator)
        if file_chooser is not None:
            file_chooser.set_files(str(cover_path))
        else:
            self._click_cover_trigger(trigger_locator)
            upload_input = self._wait_for_cover_upload_input(page, existing_input_count)
            if upload_input is None:
                page.wait_for_timeout(600)
                self._click_cover_trigger(trigger_locator)
                upload_input = self._wait_for_cover_upload_input(page, existing_input_count)
            if upload_input is None:
                debug_dir = self._capture_debug_artifacts(
                    page,
                    extra_payload={"cover_path": str(cover_path)},
                )
                raise RuntimeError(
                    "Cover upload panel did not open or no file input was exposed. "
                    f"Debug files saved to: {debug_dir}"
                )

            upload_input.set_input_files(str(cover_path), timeout=max(self.settings.publishing.wait_timeout_ms, 60000))

        wrapper = page.locator(".upload-image-wrapper").first
        wrapper.wait_for(state="visible", timeout=max(self.settings.publishing.wait_timeout_ms, 20000))
        try:
            page.locator(".pic-select-image-item").first.wait_for(
                state="visible",
                timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
            )
        except Exception as exc:
            debug_dir = self._capture_debug_artifacts(page)
            raise RuntimeError(
                "Cover upload succeeded but the uploaded image list never became selectable. "
                f"Debug files saved to: {debug_dir}"
            ) from exc

        selected = False
        for selector in [
            ".pic-select-image-item",
            ".pic-select-image-item-wrap",
            ".upload-image-wrapper img",
            ".upload-image-wrapper li",
        ]:
            try:
                candidate = page.locator(selector).first
                candidate.click(force=True, timeout=1500)
                selected = True
                page.wait_for_timeout(600)
                break
            except Exception:
                continue

        if not selected:
            raise RuntimeError("Uploaded cover image could not be selected in the Toutiao picker.")

        confirm_selectors = [
            "[data-e2e='imageUploadConfirm-btn']",
            ".upload-image-panel .footer .byte-btn-primary",
            ".upload-image-panel button:has-text('确定')",
        ]
        if not self._maybe_click(page, confirm_selectors, required=False):
            raise RuntimeError("Cover upload confirm button was not available.")

        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        panel_closed = False
        cover_applied = False

        while datetime.now().timestamp() < deadline:
            panel_closed = self._is_cover_upload_panel_hidden(page)
            cover_applied = self._is_cover_applied(page)
            if cover_applied:
                break
            if panel_closed:
                page.wait_for_timeout(400)
            else:
                self._try_close_cover_upload_panel(page, notes)
                page.wait_for_timeout(800)

        if not cover_applied:
            debug_dir = self._capture_debug_artifacts(page)
            raise RuntimeError(
                "Cover image was uploaded but never appeared in the article cover area. "
                f"Debug files saved to: {debug_dir}"
            )

        if not panel_closed:
            self._try_close_cover_upload_panel(page, notes)
            page.wait_for_timeout(500)

        notes.append(f"Applied cover image: {cover_path.name}")

    def _clear_existing_cover(self, page, notes: list[str]) -> None:
        if not self._is_cover_applied(page):
            return

        hovered = False
        for selector in [
            ".article-cover .article-cover-img-wrap",
            ".article-cover .article-cover-images",
            ".article-cover .article-cover-add",
        ]:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.hover(timeout=1000)
                    page.wait_for_timeout(250)
                    hovered = True
                    break
            except Exception:
                continue

        deleted = False
        for selector in [
            ".article-cover .article-cover-delete",
            ".article-cover-delete",
        ]:
            try:
                locator = page.locator(selector).first
                if self._click_button_candidate(locator):
                    deleted = True
                    notes.append(f"Removed existing cover via `{selector}` before uploading a new one.")
                    break
            except Exception:
                continue

        if not deleted:
            try:
                deleted = bool(
                    page.evaluate(
                        """() => {
                            const root = document.querySelector('.article-cover');
                            if (!root) {
                                return false;
                            }
                            const button = root.querySelector('.article-cover-delete');
                            if (!button) {
                                return false;
                            }
                            button.dispatchEvent(new MouseEvent('click', {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                            }));
                            return true;
                        }"""
                    )
                )
                if deleted:
                    notes.append("Removed existing cover via DOM fallback before uploading a new one.")
            except Exception:
                deleted = False

        if not deleted:
            if hovered:
                notes.append("Existing cover was detected, but the remove control was not clickable.")
            return

        deadline = datetime.now().timestamp() + 8
        while datetime.now().timestamp() < deadline:
            if not self._is_cover_applied(page):
                return
            page.wait_for_timeout(250)

    def _is_cover_upload_panel_hidden(self, page) -> bool:
        try:
            panel = page.locator(".upload-image-panel").first
            return not panel.is_visible(timeout=500)
        except Exception:
            return True

    def _is_cover_applied(self, page) -> bool:
        for selector in [
            ".article-cover img",
            ".article-cover-images img",
            ".article-cover .byte-spin img",
            ".article-cover .article-cover-img-wrap img",
        ]:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    return True
            except Exception:
                continue

        try:
            add_button = page.locator(".article-cover-add").first
            if add_button.count() and not add_button.is_visible(timeout=500):
                return True
        except Exception:
            pass

        try:
            preview_button = page.locator(".article-cover-preview .show-dialog-btn").first
            if preview_button.is_visible(timeout=500):
                return True
        except Exception:
            pass

        return False

    def _try_close_cover_upload_panel(self, page, notes: list[str]) -> None:
        for selector in [
            ".upload-image-panel .footer .byte-btn-primary",
            "[data-e2e='imageUploadConfirm-btn']",
            ".upload-image-panel button:has-text('确定')",
            ".upload-image-panel button:has-text('取消')",
            ".upload-image-panel .byte-modal-close-x",
            ".upload-image-panel .byte-modal-close-icon",
        ]:
            try:
                locator = page.locator(selector).first
                if not locator.is_visible(timeout=400):
                    continue
                if self._click_button_candidate(locator):
                    notes.append(f"Attempted to close cover upload panel via `{selector}`.")
                    return
            except Exception:
                continue

    def _resolve_cover_mode(self, cover_path: Path) -> str:
        configured = (self.settings.publishing.cover_mode or "auto").strip().lower()
        if configured in {"none", "single"}:
            return configured
        if cover_path.exists():
            return "single"
        return "none"

    def _ensure_no_cover_mode(self, page, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        changed = self._ensure_radio_state(
            page,
            [
                ".article-cover-radio-group label.byte-radio:has(input[value='1'])",
                "label.byte-radio:has-text('无封面')",
                "label.byte-radio:has-text('无图')",
            ],
        )
        if changed:
            notes.append("Switched publish cover mode to no-cover.")
        else:
            notes.append("No-cover mode was already active or selector was not changeable.")

    def _ensure_single_cover_mode(self, page) -> None:
        self._ensure_radio_state(
            page,
            [
                ".article-cover-radio-group label.byte-radio:has(input[value='2'])",
                "label.byte-radio:has-text('单图')",
            ],
        )

    def _resolve_cover_trigger(self, page):
        selectors = [
            ".article-cover-add",
            ".article-cover-add .article-cover-add-icon",
            ".article-cover .article-cover-add",
            ".article-cover .article-cover-img-wrap",
            ".article-cover-img-replace",
            ".article-cover-img-modify",
            ".article-cover .article-cover-img-replace",
            ".article-cover .article-cover-img-modify",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=1500)
                return locator
            except Exception:
                continue
        return None

    def _open_cover_file_chooser(self, page, trigger_locator):
        try:
            with page.expect_file_chooser(timeout=1800) as chooser_info:
                self._click_cover_trigger(trigger_locator)
            return chooser_info.value
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    def _click_cover_trigger(self, trigger_locator) -> None:
        try:
            trigger_locator.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass

        if self._click_button_candidate(trigger_locator):
            return

        try:
            trigger_locator.hover(timeout=800)
        except Exception:
            pass

        if self._click_button_candidate(trigger_locator):
            return

        try:
            trigger_locator.evaluate(
                """(node) => {
                    const target =
                        node.querySelector('button') ||
                        node.querySelector('.article-cover-add-icon') ||
                        node.querySelector('.article-cover-img-replace') ||
                        node.querySelector('.article-cover-img-modify') ||
                        node;
                    target.dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }"""
            )
            return
        except Exception as exc:
            raise RuntimeError(f"Failed to activate the cover upload trigger: {exc}") from exc

    def _wait_for_cover_upload_input(self, page, existing_count: int = 0):
        selectors = [
            ".upload-image-panel .btn-upload-handle input[type='file']",
            ".upload-image-panel .upload-handler input[type='file']",
            ".upload-image-panel .image-item-upload input[type='file']",
            ".upload-image-panel input#upload-drag-input",
            ".upload-image-panel input[type='file']",
            ".byte-modal-wrapper .upload-image-panel input[type='file']",
            ".upload-handler input[type='file']",
            ".image-item-upload input[type='file']",
            ".btn-upload-handle input[type='file']",
            "input#upload-drag-input",
            "input[type='file']",
        ]
        deadline = datetime.now().timestamp() + (max(self.settings.publishing.wait_timeout_ms, 20000) / 1000)
        while datetime.now().timestamp() < deadline:
            for selector in selectors:
                try:
                    locator_group = page.locator(selector)
                    count = locator_group.count()
                except Exception:
                    continue

                for index in range(count - 1, -1, -1):
                    if selector == "input[type='file']" and count <= existing_count and index < existing_count:
                        continue
                    locator = locator_group.nth(index)
                    try:
                        accept_value = (locator.get_attribute("accept") or "").lower()
                    except Exception:
                        accept_value = ""
                    if accept_value and "image" not in accept_value and ".png" not in accept_value and ".jpg" not in accept_value and ".jpeg" not in accept_value:
                        continue
                    try:
                        locator.wait_for(state="attached", timeout=500)
                        return locator
                    except Exception:
                        continue
            page.wait_for_timeout(250)
        return None

    def _confirm_publish_modal(self, page, notes: list[str]) -> None:
        clicked_texts: list[str] = []

        for _ in range(6):
            timed_publish_selector = self._first_visible_selector(
                page,
                [
                    "div.byte-modal-wrapper button:has-text('预览并定时发布')",
                ],
                strict=False,
            )
            if timed_publish_selector:
                if self._maybe_click(
                    page,
                    [
                        "div.byte-modal-wrapper button:has-text('取消')",
                        "div.byte-modal-wrapper .byte-modal-close-icon",
                    ],
                    required=False,
                ):
                    notes.append("Closed timed publish modal and kept immediate publish flow.")
                    page.wait_for_timeout(900)
                    continue

            selector = self._first_visible_selector(
                page,
                [
                    "div.byte-modal-wrapper button:has-text('继续发布')",
                    "div.byte-modal-wrapper button:has-text('确认')",
                    "div.byte-modal-wrapper button:has-text('发布')",
                    "div.byte-modal-wrapper button:has-text('确定')",
                    "div.byte-modal-wrapper button.byte-btn-primary",
                ],
                strict=False,
            )
            if not selector:
                break

            try:
                locator = page.locator(selector).first
                button_text = locator.inner_text(timeout=500).strip()
                if "定时发布" in button_text:
                    break
                locator.click(force=True, timeout=1500)
                clicked_texts.append(button_text or selector)
                page.wait_for_timeout(900)
            except Exception:
                break

        if clicked_texts:
            notes.append(f"Confirmed publish modal via: {' -> '.join(clicked_texts)}")

    def _blur_active_element(self, page) -> None:
        try:
            page.evaluate(
                """() => {
                    const active = document.activeElement;
                    if (active && typeof active.blur === 'function') {
                        active.blur();
                    }
                }"""
            )
        except Exception:
            pass

    def _click_publish_surface(self, page) -> bool:
        for selector in [
            ".publish-editor-title-wrapper",
            ".publish-editor",
            ".article-cover",
            ".left-column",
            "body",
        ]:
            try:
                page.locator(selector).first.click(position={"x": 12, "y": 12}, force=True, timeout=800)
                return True
            except Exception:
                continue
        return False

    def _finalize_editor_state(self, page, title_locator, editor_selector: str, expected_title: str, notes: list[str]) -> None:
        try:
            title_locator.press("Tab", timeout=1000)
        except Exception:
            pass

        page.wait_for_timeout(300)

        try:
            page.locator(editor_selector).first.press("Control+End", timeout=1000)
        except Exception:
            pass

        self._blur_active_element(page)

        clicked = self._click_publish_surface(page)

        if not clicked:
            try:
                page.mouse.click(20, 20)
            except Exception:
                pass

        page.wait_for_timeout(1200)
        observed_title = self._read_title_input(title_locator)
        if not self._title_matches(observed_title, expected_title):
            notes.append(f"Title changed after blur. Reapplying expected title. Observed: {observed_title!r}")
            self._set_title_content(page, title_locator, expected_title, notes)
            page.wait_for_timeout(500)
            observed_title = self._read_title_input(title_locator)
            if not self._title_matches(observed_title, expected_title):
                raise RuntimeError(
                    "Title field did not remain stable after blur. "
                    f"Expected: {expected_title!r}, observed: {observed_title!r}"
                )
        notes.append("Triggered blur on title and editor to encourage draft autosave.")

    def _stabilize_publish_form(self, page, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        self._blur_active_element(page)

        clicked = self._click_publish_surface(page)
        if not clicked:
            try:
                page.mouse.click(20, 20)
            except Exception:
                pass

        page.wait_for_timeout(3500)
        footer_text = self._read_draft_status_text(page)
        if footer_text:
            notes.append(f"Draft footer after no-cover selection: {self._clean_visible_text(footer_text)}")
        else:
            notes.append("Waited for Toutiao autosave after no-cover selection.")

    def _install_publish_event_capture(self, page, bucket: list[dict[str, Any]]) -> None:
        def handle_response(response) -> None:
            if not any(pattern in response.url for pattern in self.PUBLISH_EVENT_PATTERNS):
                return

            request = response.request
            post_data = request.post_data or ""
            payload: dict[str, Any] = {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "url": response.url,
                "method": request.method,
                "status": response.status,
                "post_data": post_data,
                "save_mode": self._extract_save_mode(post_data),
            }
            try:
                text = response.text()
            except Exception as exc:
                text = f"<<unavailable: {exc}>>"
            payload["response_text"] = text
            try:
                payload["response_json"] = json.loads(text)
            except Exception:
                payload["response_json"] = None
            bucket.append(payload)

        def handle_request_failed(request) -> None:
            if not any(pattern in request.url for pattern in self.PUBLISH_EVENT_PATTERNS):
                return
            failure = request.failure or {}
            bucket.append(
                {
                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                    "url": request.url,
                    "method": request.method,
                    "status": "requestfailed",
                    "post_data": request.post_data or "",
                    "save_mode": self._extract_save_mode(request.post_data or ""),
                    "response_text": str(failure),
                    "response_json": None,
                }
            )

        page.on("response", handle_response)
        page.on("requestfailed", handle_request_failed)

    def _install_publish_diagnostic_capture(self, page, bucket: list[dict[str, Any]]) -> None:
        def handle_response(response) -> None:
            if not any(pattern in response.url for pattern in self.DIAGNOSTIC_EVENT_PATTERNS):
                return

            payload: dict[str, Any] = {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "url": response.url,
                "status": response.status,
            }
            try:
                text = response.text()
            except Exception as exc:
                text = f"<<unavailable: {exc}>>"
            payload["response_text"] = text
            try:
                payload["response_json"] = json.loads(text)
            except Exception:
                payload["response_json"] = None
            bucket.append(payload)

        page.on("response", handle_response)

    def _wait_for_draft_save(self, page, publish_events: list[dict[str, Any]], notes: list[str]) -> bool:
        start_index = len(publish_events)
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_footer_text = ""

        while datetime.now().timestamp() < deadline:
            successful_autosave = self._latest_publish_event(
                publish_events,
                start_index=start_index,
                save_mode=1,
                success_only=True,
            )
            footer_text = self._read_draft_status_text(page)
            footer_saving = self._draft_status_indicates_saving(footer_text)
            footer_saved = self._draft_status_indicates_saved(footer_text)

            if successful_autosave is not None and not footer_saving:
                if footer_text:
                    notes.append(f"Draft autosave completed: {footer_text}")
                else:
                    notes.append("Draft autosave completed and no footer status text remained visible.")
                return True

            if footer_saved:
                notes.append(f"Draft footer reported saved state: {footer_text}")
                return True

            last_footer_text = footer_text
            page.wait_for_timeout(500)

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={
                "publish_events": publish_events,
                "last_footer_text": last_footer_text,
            },
        )
        notes.append(
            "Draft autosave did not complete before publish. "
            f"Last footer state: {last_footer_text or 'unavailable'}. "
            f"Continuing with publish attempt. Debug files saved to: {debug_dir}"
        )
        return False

    def _latest_publish_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
        save_mode: int,
        success_only: bool,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            if event.get("save_mode") != save_mode:
                continue
            if success_only and not self._publish_response_is_success(event.get("response_json")):
                continue
            return event
        return None

    def _read_draft_status_text(self, page) -> str:
        selector = self._first_visible_selector(page, self.DEFAULT_DRAFT_STATUS_SELECTORS, strict=False)
        if not selector:
            return ""
        try:
            return page.locator(selector).first.inner_text(timeout=500).strip()
        except Exception:
            return ""

    def _draft_status_indicates_saving(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(marker in normalized for marker in ["保存中", "草稿保存中", "draft saving"])

    def _draft_status_indicates_saved(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(marker in normalized for marker in ["已保存", "草稿已保存", "draft saved"])

    def _wait_for_publish_submit(
        self,
        page,
        publish_events: list[dict[str, Any]],
        start_index: int,
    ) -> dict[str, Any]:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 30000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)

        while datetime.now().timestamp() < deadline:
            event = self._latest_submit_event(
                publish_events,
                start_index=start_index,
            )
            if event is not None:
                return event
            page.wait_for_timeout(300)

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={"publish_events": publish_events},
        )
        raise RuntimeError(
            "No publish submission response was captured from Toutiao. "
            f"Debug files saved to: {debug_dir}"
        )

    def _latest_submit_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            if not self._is_final_submit_event(event):
                continue
            return event
        return None

    def _latest_preview_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            url = str(event.get("url", ""))
            if "/mp/agw/article/publish" not in url:
                continue
            post_data = str(event.get("post_data", ""))
            if "is_app_preview=1" in post_data:
                return event
        return None

    def _is_final_submit_event(self, event: dict[str, Any]) -> bool:
        url = str(event.get("url", ""))
        if "/mp/agw/article/publish" not in url:
            return False

        post_data = str(event.get("post_data", ""))
        if "is_app_preview=1" in post_data:
            return False

        return True

    def _extract_save_mode(self, post_data: str) -> int | None:
        if not post_data:
            return None

        stripped = post_data.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except Exception:
                payload = {}
            value = payload.get("save")
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

        try:
            parsed = parse_qs(post_data, keep_blank_values=True)
        except Exception:
            parsed = {}
        if "save" in parsed and parsed["save"]:
            try:
                return int(parsed["save"][0])
            except (TypeError, ValueError):
                return None

        match = re.search(r"(?:^|[&,{\"'])save(?:[\"']?\s*:\s*|=)(\d+)", post_data)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None
        return None

    def _publish_response_is_success(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False

        code = payload.get("code")
        err_no = payload.get("err_no")
        if code is None and err_no is None:
            return False

        try:
            if code is not None and int(code) != 0:
                return False
        except (TypeError, ValueError):
            return False

        try:
            if err_no is not None and int(err_no) != 0:
                return False
        except (TypeError, ValueError):
            return False

        return True

    def _extract_publish_error(self, payload: Any) -> tuple[str, str]:
        if not isinstance(payload, dict):
            return ("unknown", "response body was not valid JSON")
        code = str(payload.get("code", payload.get("err_no", "unknown")))
        message = self._clean_visible_text(str(payload.get("message", payload.get("reason", "unknown error"))))
        return code, message

    def _build_publish_diagnostics(self, diagnostic_events: list[dict[str, Any]]) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        for event in diagnostic_events:
            url = str(event.get("url", ""))
            payload = event.get("response_json")
            if "/mp/agw/media/get_user_base_info" in url:
                raw["user_base_info"] = payload
            elif "/mp/agw/creator_center/user_info" in url:
                raw["creator_user_info"] = payload
            elif "/mp/agw/article/new" in url:
                raw["article_new"] = payload
            elif "/mp/agw/media/check_user_auth" in url:
                raw["check_user_auth"] = payload

        creator_user_info = raw.get("creator_user_info") if isinstance(raw.get("creator_user_info"), dict) else {}
        article_new = raw.get("article_new") if isinstance(raw.get("article_new"), dict) else {}
        article_data = article_new.get("data") if isinstance(article_new.get("data"), dict) else {}
        article_media = article_data.get("media") if isinstance(article_data.get("media"), dict) else {}
        content_cache = (
            article_media.get("content_cache")
            if isinstance(article_media.get("content_cache"), dict)
            else {}
        )
        check_user_auth = raw.get("check_user_auth") if isinstance(raw.get("check_user_auth"), dict) else {}
        user_base_info = raw.get("user_base_info") if isinstance(raw.get("user_base_info"), dict) else {}

        summary = {
            "creator_name": self._clean_visible_text(creator_user_info.get("name")),
            "is_creator": creator_user_info.get("is_creator"),
            "auth_type": creator_user_info.get("auth_type"),
            "media_status": user_base_info.get("media_status"),
            "is_auth": user_base_info.get("is_auth"),
            "has_user_auth": check_user_auth.get("has_auth"),
            "default_article_ad_type": article_data.get("article_ad_type"),
            "article_can_change_ad": article_data.get("article_can_change_ad"),
            "claim_origin_permission": article_data.get("claim_origin_permission"),
            "ads_permission": article_media.get("ads_permission"),
            "manual_publish_ad": content_cache.get("manual_publish_ad"),
            "content_cache_source": content_cache.get("source"),
            "modify_source": content_cache.get("modify_source"),
            "not_claim_origin_cnt": content_cache.get("not_claim_origin_cnt"),
        }
        return {
            "summary": summary,
            "suspicious_flags": self._summarize_publish_blockers(summary),
            "raw": raw,
        }

    def _build_preflight_summary_from_diagnostics(self, current_url: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
        summary = diagnostics.get("summary", {}) if isinstance(diagnostics, dict) else {}
        suspicious_flags = list(diagnostics.get("suspicious_flags", [])) if isinstance(diagnostics, dict) else []
        blocking_flags: list[str] = []
        warnings: list[str] = []

        if "/profile_v4/graphic/publish" not in current_url:
            warnings.append(f"Current page is not the direct publish page: {current_url}")
        if not summary:
            blocking_flags.append("No publish diagnostics were captured from the current session.")
        elif not self._diagnostics_indicate_publish_capable_session(summary, current_url):
            blocking_flags.append("Current browser session does not appear to have an accessible Toutiao publish page.")
        if summary.get("media_status") not in {None, 1, "1"}:
            warnings.append(f"Unexpected media status: {summary.get('media_status')}")
        if suspicious_flags:
            warnings.extend(f"Diagnostic flag: {item}" for item in suspicious_flags[:6])

        return {
            "current_url": current_url,
            "blocking_flags": self._unique_texts(blocking_flags),
            "warnings": self._unique_texts(warnings),
            "diagnostics": self._compact_publish_diagnostics(diagnostics),
        }

    def _should_bypass_preflight_blockers(self, preflight_summary: dict[str, Any]) -> bool:
        diagnostics = preflight_summary.get("diagnostics", {}) if isinstance(preflight_summary, dict) else {}
        summary = diagnostics.get("summary", {}) if isinstance(diagnostics, dict) else {}
        if not isinstance(summary, dict) or not summary:
            return False
        return self._diagnostics_indicate_publish_capable_session(
            summary,
            str(preflight_summary.get("current_url", "")),
        )

    def _diagnostics_indicate_publish_capable_session(self, summary: dict[str, Any], current_url: str) -> bool:
        creator_name = self._clean_visible_text(summary.get("creator_name"))
        has_any_identity = bool(creator_name) or summary.get("is_auth") is True or summary.get("media_status") is not None
        page_is_publish_like = "/profile_v4/graphic/publish" in current_url if current_url else True
        return has_any_identity and page_is_publish_like

    def _summarize_publish_blockers(self, summary: dict[str, Any]) -> list[str]:
        blockers: list[str] = []
        if summary.get("is_creator") is False:
            blockers.append("creator_center/user_info.is_creator=false")
        if summary.get("has_user_auth") is False:
            blockers.append("media/check_user_auth.has_auth=false")
        if summary.get("ads_permission") == 0:
            blockers.append("article/new.media.ads_permission=0")
        if summary.get("manual_publish_ad") not in {None, 2}:
            blockers.append(f"article/new.media.content_cache.manual_publish_ad={summary.get('manual_publish_ad')}")
        if summary.get("content_cache_source"):
            blockers.append(f"article/new.media.content_cache.source={summary.get('content_cache_source')}")
        if summary.get("not_claim_origin_cnt"):
            blockers.append(
                f"article/new.media.content_cache.not_claim_origin_cnt={summary.get('not_claim_origin_cnt')}"
            )
        return blockers

    def _format_publish_diagnostic_blockers(self, diagnostics: dict[str, Any]) -> str:
        blockers = diagnostics.get("suspicious_flags")
        if not isinstance(blockers, list) or not blockers:
            return ""
        return f"Suspicious account flags: {', '.join(str(item) for item in blockers)}. "

    def _compact_publish_diagnostics(self, diagnostics: dict[str, Any]) -> dict[str, Any]:
        summary = dict(diagnostics.get("summary", {})) if isinstance(diagnostics.get("summary"), dict) else {}
        if "creator_name" in summary:
            summary["creator_name"] = self._clean_visible_text(summary.get("creator_name"))
        return {
            "summary": summary,
            "suspicious_flags": list(diagnostics.get("suspicious_flags", [])),
        }

    def _compact_backend_response(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return {
            "code": payload.get("code"),
            "err_no": payload.get("err_no"),
            "message": self._clean_visible_text(str(payload.get("message", ""))),
            "reason": self._clean_visible_text(str(payload.get("reason", ""))),
            "pgc_id": str(data.get("pgc_id", "")),
        }

    def _normalize_publish_result_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["notes"] = self._finalize_notes(payload.get("notes", []))
        if "backend_response_summary" in normalized:
            summary = dict(normalized.get("backend_response_summary", {}))
            for key in ("message", "reason", "pgc_id"):
                if key in summary:
                    summary[key] = self._clean_visible_text(summary.get(key))
            normalized["backend_response_summary"] = summary
        if "publish_diagnostics" in normalized:
            normalized["publish_diagnostics"] = self._compact_publish_diagnostics(
                normalized.get("publish_diagnostics", {})
            )
        return normalized

    def _finalize_notes(self, notes: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw_item in notes:
            text = self._clean_visible_text(str(raw_item)).strip()
            if not text:
                continue
            if "Debug files saved to:" in text and "Draft autosave did not complete before publish." in text:
                text = re.sub(r"\s*Debug files saved to: .+$", "", text).strip()
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _clean_visible_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.replace("\u00a0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _unique_texts(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = self._clean_visible_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _write_failed_publish_result(
        self,
        publish_dir: Path,
        run_dir: Path,
        title: str,
        topic_title: str,
        topic_url: str,
        error: str,
        preflight_summary: dict[str, Any] | None = None,
    ) -> None:
        failure_payload = {
            "status": "failed",
            "failed_at": datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
            "title": title,
            "topic_title": topic_title,
            "topic_url": topic_url,
            "error": self._clean_visible_text(error),
            "preflight_summary": preflight_summary or {},
        }
        (publish_dir / "publish_result.json").write_text(
            json.dumps(failure_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_history(self) -> list[PublishRecord]:
        if not self.history_path.exists():
            return []
        payload = json.loads(self.history_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [PublishRecord.from_dict(item) for item in payload if isinstance(item, dict)]

    def _save_history(self, records: list[PublishRecord]) -> None:
        self.history_path.write_text(
            json.dumps([asdict(item) for item in records], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _was_topic_published(self, topic_title: str, topic_url: str) -> bool:
        normalized_title = self._normalize(topic_title)
        today = datetime.now().date().isoformat()
        for record in self._load_history():
            if record.status not in self.SUCCESS_HISTORY_STATUSES:
                continue
            if not record.published_at.startswith(today):
                continue
            if topic_url and record.topic_url and topic_url == record.topic_url:
                return True
            if normalized_title and self._normalize(record.topic_title) == normalized_title:
                return True
        return False

    def _normalize(self, text: str) -> str:
        return self.NORMALIZE_PATTERN.sub("", text.lower())

    def _first_visible_selector(self, page, selectors: list[str], strict: bool = True) -> str | None:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 8)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=500)
                    if index == 0:
                        return selector
                    return f"{selector} >> nth={index}"
                except Exception:
                    continue
        if strict:
            return None
        return None

    def _maybe_click(self, page, selectors: list[str], required: bool = True) -> bool:
        selector = self._first_visible_selector(page, selectors, strict=False)
        if not selector:
            return False if not required else False

        locator = page.locator(selector).first
        try:
            locator.click(timeout=1500)
        except Exception:
            locator.click(force=True, timeout=1500)
        return True

    def _click_immediate_publish_button(self, page, configured_selectors: list[str], notes: list[str]) -> bool:
        preferred_selectors: list[str] = [
            ".publish-footer-content button.byte-btn-primary.publish-btn-last",
            ".publish-footer-content button.publish-btn-last.byte-btn-primary",
            ".publish-footer-content button.publish-btn-last",
            ".publish-footer button.byte-btn-primary.publish-btn-last",
            ".publish-footer button.publish-btn-last",
            "button.byte-btn-primary.publish-btn-last",
            "button.publish-btn-last",
        ]
        selector_candidates: list[str] = []
        seen: set[str] = set()
        for selector in [*preferred_selectors, *configured_selectors]:
            if selector and selector not in seen:
                selector_candidates.append(selector)
                seen.add(selector)

        for selector in selector_candidates:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 6)
            except Exception:
                count = 0

            for index in range(count):
                candidate = locator.nth(index)
                if not self._locator_is_visible(candidate):
                    continue
                button_text = self._read_button_text(candidate)
                normalized_text = self._normalize_button_text(button_text)
                if self._is_timed_publish_button_text(normalized_text):
                    continue
                if not self._is_immediate_publish_button_text(normalized_text) and "publish-btn-last" not in selector:
                    continue
                if self._click_button_candidate(candidate):
                    notes.append(
                        f"Clicked primary publish button via selector `{selector}`"
                        + (f" with text `{button_text}`." if button_text else ".")
                    )
                    return True

        footer_locator = page.locator(".publish-footer-content button, .publish-footer button")
        best_candidate = None
        best_text = ""
        best_score = -10_000
        try:
            footer_count = min(footer_locator.count(), 12)
        except Exception:
            footer_count = 0

        for index in range(footer_count):
            candidate = footer_locator.nth(index)
            if not self._locator_is_visible(candidate):
                continue
            button_text = self._read_button_text(candidate)
            score = self._score_publish_button_candidate(candidate, button_text)
            if score > best_score:
                best_candidate = candidate
                best_text = button_text
                best_score = score

        if best_candidate is not None and best_score > 0 and self._click_button_candidate(best_candidate):
            notes.append(
                "Clicked primary publish button via footer scan"
                + (f" with text `{best_text}`." if best_text else ".")
            )
            return True

        return False

    def _locator_is_visible(self, locator) -> bool:
        try:
            return locator.is_visible(timeout=500)
        except Exception:
            return False

    def _read_button_text(self, locator) -> str:
        try:
            return locator.inner_text(timeout=500).strip()
        except Exception:
            return ""

    def _normalize_button_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _is_timed_publish_button_text(self, normalized_text: str) -> bool:
        return "\u5b9a\u65f6\u53d1\u5e03" in normalized_text

    def _is_immediate_publish_button_text(self, normalized_text: str) -> bool:
        if not normalized_text:
            return False
        if self._is_timed_publish_button_text(normalized_text):
            return False
        if normalized_text in {"\u9884\u89c8", "\u53d6\u6d88"}:
            return False
        return "\u53d1\u5e03" in normalized_text

    def _score_publish_button_candidate(self, locator, button_text: str) -> int:
        normalized_text = self._normalize_button_text(button_text)
        if not self._is_immediate_publish_button_text(normalized_text):
            return -10_000

        score = 0
        if normalized_text == "\u9884\u89c8\u5e76\u53d1\u5e03":
            score += 300
        elif normalized_text.endswith("\u53d1\u5e03"):
            score += 180
        else:
            score += 120

        try:
            class_name = str(locator.get_attribute("class", timeout=500) or "")
        except Exception:
            class_name = ""

        if "publish-btn-last" in class_name:
            score += 80
        if "byte-btn-primary" in class_name:
            score += 60
        return score

    def _click_button_candidate(self, locator) -> bool:
        try:
            locator.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass

        try:
            locator.click(timeout=1500)
            return True
        except Exception:
            pass

        try:
            locator.click(force=True, timeout=1500)
            return True
        except Exception:
            pass

        try:
            locator.evaluate(
                """(node) => {
                    node.dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }"""
            )
            return True
        except Exception:
            return False

    def _complete_publish_confirmation_flow(
        self,
        page,
        publish_events: list[dict[str, Any]],
        notes: list[str],
        fallback_start_index: int,
    ) -> int:
        preview_detected = False
        preview_note_added = False
        deadline = datetime.now().timestamp() + 20

        while datetime.now().timestamp() < deadline:
            self._confirm_publish_modal(page, notes)

            preview_event = self._latest_preview_event(
                publish_events,
                start_index=fallback_start_index,
            )
            if preview_event is not None:
                preview_detected = True
                if not preview_note_added:
                    notes.append("Detected the preview publish step. Waiting for the final confirm button.")
                    preview_note_added = True

            confirm_locator = self._find_confirm_publish_button(page, preview_seen=preview_detected)
            if confirm_locator is not None:
                page.wait_for_timeout(1500)
                submit_start_index = len(publish_events)
                button_text = self._read_button_text(confirm_locator)
                if not self._click_button_candidate(confirm_locator):
                    raise RuntimeError("Found the confirm publish button, but clicking it failed.")
                notes.append(
                    "Clicked the final confirm publish button"
                    + (f" with text `{button_text}`." if button_text else ".")
                )
                page.wait_for_timeout(1500)
                return submit_start_index

            page.wait_for_timeout(800)

        if preview_detected:
            notes.append("Preview publish request was detected, but no final confirm button appeared.")
        else:
            notes.append("No in-page confirm publish button appeared; proceeding with the current submission flow.")
        return fallback_start_index

    def _find_confirm_publish_button(self, page, preview_seen: bool = False):
        candidates = [
            "div.byte-modal-wrapper button:has-text('确认发布')",
            "div.byte-modal-wrapper button:has-text('继续发布')",
            "div.byte-modal-wrapper button:has-text('发布')",
            "div.byte-modal-wrapper .byte-btn-primary",
            ".publish-footer-content button.byte-btn-primary.publish-btn-last",
            ".publish-footer button.byte-btn-primary.publish-btn-last",
            "button.byte-btn-primary.publish-btn-last",
            ".publish-footer-content button",
            ".publish-footer button",
        ]

        for selector in candidates:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 8)
            except Exception:
                count = 0

            for index in range(count):
                candidate = locator.nth(index)
                if not self._locator_is_visible(candidate):
                    continue
                button_text = self._read_button_text(candidate)
                normalized_text = self._normalize_button_text(button_text)
                if normalized_text == "\u786e\u8ba4\u53d1\u5e03":
                    return candidate
                if preview_seen and normalized_text in {"\u7ee7\u7eed\u53d1\u5e03", "\u786e\u8ba4", "\u53d1\u5e03"}:
                    return candidate
        return None

    def _wait_for_any(self, page, selectors: list[str]) -> str | None:
        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(
                    state="visible",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                return selector
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return None

    def _capture_debug_artifacts(self, page, extra_payload: dict[str, Any] | None = None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        debug_dir = self.settings.output_dir / "publish_debug" / timestamp
        debug_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = debug_dir / "publish_page.png"
        html_path = debug_dir / "publish_page.html"
        url_path = debug_dir / "current_url.txt"

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as exc:
            self.logger.warning("Failed to save publish debug screenshot: %s", exc)

        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            self.logger.warning("Failed to save publish debug html: %s", exc)

        try:
            url_path.write_text(page.url, encoding="utf-8")
        except Exception as exc:
            self.logger.warning("Failed to save publish debug url: %s", exc)

        if extra_payload is not None:
            try:
                (debug_dir / "publish_context.json").write_text(
                    json.dumps(extra_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                self.logger.warning("Failed to save publish debug context: %s", exc)

        return debug_dir

    def _confirm_publish_modal(self, page, notes: list[str]) -> None:
        clicked_texts: list[str] = []

        for _ in range(6):
            timed_publish_selector = self._first_visible_selector(
                page,
                [
                    ".common-timing-picker .byte-modal-footer .byte-btn-default",
                    ".common-timing-picker .byte-modal-close-icon",
                ],
                strict=False,
            )
            if timed_publish_selector:
                if self._maybe_click(page, [timed_publish_selector], required=False):
                    notes.append("Closed timed publish modal and kept immediate publish flow.")
                    page.wait_for_timeout(900)
                    continue

            selector = self._first_visible_selector(
                page,
                [
                    "div.byte-modal-wrapper[aria-hidden='false'] .byte-modal-footer button.byte-btn-primary",
                    "div.byte-modal-wrapper:not(.byte-modal-wrapper-hide) .byte-modal-footer button.byte-btn-primary",
                    "div.byte-modal-wrapper button:has-text('继续发布')",
                    "div.byte-modal-wrapper button:has-text('确认')",
                    "div.byte-modal-wrapper button:has-text('发布')",
                    "div.byte-modal-wrapper button:has-text('确定')",
                ],
                strict=False,
            )
            if not selector:
                break

            try:
                locator = page.locator(selector).first
                button_text = locator.inner_text(timeout=500).strip()
                if "定时发布" in button_text:
                    break
                locator.click(force=True, timeout=1500)
                clicked_texts.append(button_text or selector)
                page.wait_for_timeout(900)
            except Exception:
                break

        if clicked_texts:
            notes.append(f"Confirmed publish modal via: {' -> '.join(clicked_texts)}")

    def _wait_for_draft_save(
        self,
        page,
        publish_events: list[dict[str, Any]],
        notes: list[str],
        start_index: int = 0,
    ) -> bool:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 18000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_footer_text = ""
        idle_streak = 0
        saw_footer_text = False
        saw_saving_state = False
        last_nudge_at = 0.0

        while datetime.now().timestamp() < deadline:
            latest_autosave = self._latest_save_event(
                publish_events,
                start_index=start_index,
                success_only=None,
            )
            footer_text = self._clean_visible_text(self._read_draft_status_text(page))
            footer_saving = self._draft_status_indicates_saving(footer_text)
            footer_saved = self._draft_status_indicates_saved(footer_text)
            footer_failed = self._draft_status_indicates_failed(footer_text)

            if footer_text:
                saw_footer_text = True
            if footer_saving:
                saw_saving_state = True
                idle_streak = 0
            elif footer_text == last_footer_text:
                idle_streak += 1
            else:
                idle_streak = 1 if (footer_text or saw_footer_text) else 0

            if latest_autosave is not None and self._publish_response_is_success(latest_autosave.get("response_json")):
                if footer_saving:
                    last_footer_text = footer_text
                    page.wait_for_timeout(600)
                    continue
                if footer_text:
                    notes.append(f"Draft autosave completed: {footer_text}")
                    return True
                if idle_streak >= 3:
                    notes.append("Draft autosave completed and the publish form stayed idle.")
                    return True

            if footer_saved:
                notes.append(f"Draft footer reported saved state: {footer_text}")
                return True

            if latest_autosave is not None and not self._publish_response_is_success(latest_autosave.get("response_json")):
                code, message = self._extract_publish_error(latest_autosave.get("response_json"))
                debug_dir = self._capture_debug_artifacts(
                    page,
                    extra_payload={
                        "publish_events": publish_events,
                        "failed_draft_event": latest_autosave,
                        "last_footer_text": footer_text,
                    },
                )
                notes.append(
                    "Draft save request was rejected by Toutiao. "
                    f"code={code}, message={message}. "
                    f"Last footer state: {footer_text or 'unavailable'}. "
                    f"Debug files saved to: {debug_dir}"
                )
                return False

            if footer_failed:
                debug_dir = self._capture_debug_artifacts(
                    page,
                    extra_payload={
                        "publish_events": publish_events,
                        "last_footer_text": footer_text,
                    },
                )
                notes.append(
                    "Draft footer reported a save failure. "
                    f"Last footer state: {footer_text}. Debug files saved to: {debug_dir}"
                )
                return False

            now_ts = datetime.now().timestamp()
            if now_ts - last_nudge_at >= 4 and (footer_saving or (not saw_footer_text and latest_autosave is None)):
                self._nudge_draft_autosave(page, notes)
                last_nudge_at = now_ts

            if not saw_saving_state and not saw_footer_text and latest_autosave is None and idle_streak >= 6:
                notes.append("Draft footer never exposed a saving indicator, but the publish form stayed idle long enough.")
                return True

            last_footer_text = footer_text
            page.wait_for_timeout(700)

        latest_autosave = self._latest_save_event(
            publish_events,
            start_index=start_index,
            success_only=True,
        )
        if latest_autosave is not None:
            if self._draft_status_indicates_saving(last_footer_text):
                notes.append("Draft autosave request succeeded, but the footer still showed a saving state.")
                return False
            notes.append("Draft autosave completed before publish.")
            return True

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={
                "publish_events": publish_events,
                "last_footer_text": last_footer_text,
                "saw_footer_text": saw_footer_text,
                "saw_saving_state": saw_saving_state,
                "idle_streak": idle_streak,
            },
        )
        notes.append(
            "Draft autosave did not complete before publish. "
            f"Last footer state: {last_footer_text or 'unavailable'}. "
            f"Debug files saved to: {debug_dir}"
        )
        return False

    def _nudge_draft_autosave(self, page, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        self._blur_active_element(page)
        clicked = self._click_publish_surface(page)
        if not clicked:
            try:
                page.mouse.click(20, 20)
            except Exception:
                pass
        page.wait_for_timeout(900)

    def _latest_save_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
        success_only: bool | None,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            if not self._is_draft_save_event(event):
                continue
            if success_only is True and not self._publish_response_is_success(event.get("response_json")):
                continue
            if success_only is False and self._publish_response_is_success(event.get("response_json")):
                continue
            return event
        return None

    def _is_draft_save_event(self, event: dict[str, Any]) -> bool:
        save_mode = event.get("save_mode")
        if save_mode == 1:
            return True
        url = str(event.get("url", ""))
        return "/mp/agw/draft/" in url and "/mp/agw/article/publish" not in url

    def _draft_status_indicates_saving(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in [
                "保存中",
                "草稿保存中",
                "draft saving",
            ]
        )

    def _draft_status_indicates_saved(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in [
                "已保存",
                "草稿已保存",
                "draft saved",
            ]
        )

    def _draft_status_indicates_failed(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in [
                "保存失败",
                "草稿保存失败",
                "draft save failed",
            ]
        )

