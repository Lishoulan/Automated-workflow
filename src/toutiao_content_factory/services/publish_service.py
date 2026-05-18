from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import PublishRecord

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    PlaywrightTimeoutError = Exception
    sync_playwright = None


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

    def publish_run(self, run_dir: Path) -> dict[str, Path]:
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
        cover_path = publish_dir / "images" / "cover.png"
        body_image_path = publish_dir / "images" / "infographic_1.png"
        preflight_summary = self.collect_preflight_summary()
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
                cover_path=cover_path,
                body_image_path=body_image_path,
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
        self.logger.info("Publish finished for run: %s", run_dir)
        return {"publish_result": publish_result_path, "publish_history": self.history_path}

    def initialize_login_session(self) -> dict[str, str]:
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
                print("After login succeeds, return here and press Enter to run a publish preflight check.")
                input()
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                page.wait_for_timeout(2500)
                diagnostic_events: list[dict[str, Any]] = []
                self._install_publish_diagnostic_capture(page, diagnostic_events)
                page.reload(
                    wait_until="domcontentloaded",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                page.wait_for_timeout(2500)
                diagnostics = self._build_publish_diagnostics(diagnostic_events)
                preflight_summary = self._build_preflight_summary_from_diagnostics(page.url, diagnostics)
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

    def _publish_to_toutiao(self, title: str, article: str, cover_path: Path, body_image_path: Path) -> dict[str, Any]:
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
                self._try_apply_article_image(page, editor_selector, body_image_path, notes)
                self._apply_default_work_declarations(page, notes)
                self._apply_safe_publish_options(page, notes)
                self._finalize_editor_state(page, title_locator, editor_selector, title, notes)
                self._dismiss_ai_assistant_drawer(page, notes)

                cover_mode = self._resolve_cover_mode(cover_path)
                if cover_mode == "none":
                    self._ensure_no_cover_mode(page, notes)
                elif cover_mode == "single":
                    self._apply_cover_image(page, cover_path, notes)
                else:
                    notes.append("Cover image not found. Falling back to no-cover publish mode.")
                    self._ensure_no_cover_mode(page, notes)

                self._verify_publish_media_state(
                    page,
                    editor_selector,
                    cover_mode=cover_mode,
                    expect_inline_image=body_image_path.exists(),
                )
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
        title = self._sanitize_publish_text(title).strip()
        if len(title) <= self.settings.publishing.title_max_length:
            return title
        return title[: self.settings.publishing.title_max_length].rstrip()

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
        lines: list[str] = []
        for raw_line in article.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue

            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^\d+\.\s*", "", line)
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^>\s*", "", line)
            line = re.sub(r"!\[(.*?)\]\((.*?)\)", "", line)
            line = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", line)
            line = re.sub(r"`(.+?)`", r"\1", line)
            line = line.replace("**", "")
            line = line.replace("__", "")
            line = line.replace("*", "")
            line = line.replace("_", "")
            line = line.replace("~", "")
            if line in {"--", "---"}:
                continue
            if any(line.startswith(prefix) for prefix in self.META_NOISE_PREFIXES):
                continue
            line = self._sanitize_publish_text(line)
            line = re.sub(r"\s{2,}", " ", line).strip()
            if not line:
                continue
            lines.append(line)

        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

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

    def _try_apply_article_image(self, page, editor_selector: str, image_path: Path, notes: list[str]) -> None:
        if not image_path.exists():
            notes.append("Article image not found. Skipping inline image upload.")
            return

        try:
            self._apply_article_image(page, editor_selector, image_path, notes)
        except Exception as exc:
            notes.append(
                "Inline article image upload failed and was skipped: "
                f"{self._clean_visible_text(exc)}"
            )

    def _apply_article_image(self, page, editor_selector: str, image_path: Path, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        editor_locator = page.locator(editor_selector).first
        editor_locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)
        previous_image_count = self._count_editor_images(editor_locator)

        try:
            editor_locator.click(force=True, timeout=1200)
        except Exception:
            pass

        try:
            editor_locator.focus(timeout=1000)
        except Exception:
            pass

        insertion_paragraph = self._position_editor_caret_for_inline_image(page, editor_locator)

        trigger_locator = self._resolve_article_image_trigger(page)
        if trigger_locator is None:
            raise RuntimeError("Could not locate the article image upload trigger.")

        existing_input_count = page.locator("input[type='file']").count()
        try:
            trigger_locator.dispatch_event("click")
        except Exception:
            trigger_locator.click(force=True, timeout=1500)

        upload_input = self._wait_for_article_upload_input(page, existing_input_count)
        if upload_input is None:
            raise RuntimeError("Article image upload input did not appear.")

        upload_input.set_input_files(str(image_path), timeout=max(self.settings.publishing.wait_timeout_ms, 60000))
        self._wait_for_article_image_inserted(page, editor_locator, previous_image_count)

        try:
            editor_locator.click(force=True, timeout=1200)
            page.keyboard.press("ArrowRight")
            page.keyboard.press("Enter")
        except Exception:
            pass

        notes.append(
            f"Inserted inline article image after paragraph {insertion_paragraph}: {image_path.name}"
        )

    def _position_editor_caret_for_inline_image(self, page, editor_locator) -> int:
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

        target_index = 2 if paragraph_count >= 2 else max(1, paragraph_count)
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

    def _resolve_article_image_trigger(self, page):
        for selector in self.DEFAULT_ARTICLE_IMAGE_TRIGGER_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=600)
                return locator
            except Exception:
                continue
        return None

    def _wait_for_article_upload_input(self, page, existing_count: int):
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 15000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        while datetime.now().timestamp() < deadline:
            file_inputs = page.locator("input[type='file']")
            try:
                count = file_inputs.count()
            except Exception:
                page.wait_for_timeout(200)
                continue

            for index in range(count - 1, -1, -1):
                if count <= existing_count and index < existing_count:
                    continue
                locator = file_inputs.nth(index)
                try:
                    accept_value = (locator.get_attribute("accept") or "").lower()
                except Exception:
                    accept_value = ""
                if accept_value and "image" not in accept_value and ".png" not in accept_value:
                    continue
                return locator

            page.wait_for_timeout(200)
        return None

    def _wait_for_article_image_inserted(self, page, editor_locator, previous_count: int) -> None:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 30000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_count = previous_count
        while datetime.now().timestamp() < deadline:
            current_count = self._count_editor_images(editor_locator)
            if current_count > previous_count:
                page.wait_for_timeout(500)
                return
            last_count = current_count
            page.wait_for_timeout(300)

        raise RuntimeError(
            "Article image upload did not result in an inline image appearing in the editor. "
            f"Previous image count: {previous_count}, current image count: {last_count}."
        )

    def _count_editor_images(self, editor_locator) -> int:
        for selector in ("figure img", ".editor-image img", "img"):
            try:
                return editor_locator.locator(selector).count()
            except Exception:
                continue
        return 0

    def _verify_publish_media_state(
        self,
        page,
        editor_selector: str,
        *,
        cover_mode: str,
        expect_inline_image: bool,
    ) -> None:
        if cover_mode == "single":
            try:
                page.locator(".article-cover img").first.wait_for(
                    state="visible",
                    timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
                )
            except Exception as exc:
                raise RuntimeError("Cover verification failed before publish.") from exc

        if not expect_inline_image:
            return

        editor_locator = page.locator(editor_selector).first
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        while datetime.now().timestamp() < deadline:
            if self._count_editor_images(editor_locator) > 0:
                return
            page.wait_for_timeout(250)

        raise RuntimeError("Inline article image verification failed before publish.")

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

        trigger_locator = self._resolve_cover_trigger(page)
        if trigger_locator is None:
            raise RuntimeError("Could not locate the Toutiao cover upload trigger.")

        try:
            trigger_locator.dispatch_event("click")
        except Exception:
            trigger_locator.click(force=True, timeout=1500)

        upload_input = self._wait_for_cover_upload_input(page)
        if upload_input is None:
            raise RuntimeError("Cover upload panel did not open or no file input was exposed.")

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

        try:
            page.locator(".upload-image-panel").first.wait_for(
                state="hidden",
                timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
            )
        except Exception:
            debug_dir = self._capture_debug_artifacts(page)
            raise RuntimeError(
                "Cover upload panel did not close after confirming the uploaded image. "
                f"Debug files saved to: {debug_dir}"
            )

        try:
            page.locator(".article-cover img").first.wait_for(
                state="visible",
                timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
            )
        except Exception:
            debug_dir = self._capture_debug_artifacts(page)
            raise RuntimeError(
                "Cover image was uploaded but never appeared in the article cover area. "
                f"Debug files saved to: {debug_dir}"
            )

        notes.append(f"Applied cover image: {cover_path.name}")

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
            ".article-cover-img-replace",
            ".article-cover-img-modify",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=1500)
                return locator
            except Exception:
                continue
        return None

    def _wait_for_cover_upload_input(self, page):
        selectors = [
            ".btn-upload-handle input[type='file']",
            "input#upload-drag-input",
        ]
        deadline = datetime.now().timestamp() + (max(self.settings.publishing.wait_timeout_ms, 20000) / 1000)
        while datetime.now().timestamp() < deadline:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count():
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
        article_data = article_new.get("data") if isinstance(article_new, dict) else {}
        article_media = article_data.get("media") if isinstance(article_data, dict) else {}
        content_cache = article_media.get("content_cache") if isinstance(article_media, dict) else {}
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
