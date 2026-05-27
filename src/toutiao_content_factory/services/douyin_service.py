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
from toutiao_content_factory.services.article_sanitizer import clean_article_text

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class DouyinPublishService:
    ARTICLE_TAB_LABEL = "发布文章"
    ARTICLE_EDITOR_URL = (
        "https://creator.douyin.com/creator-micro/content/post/article"
        "?default-tab=5&enter_from=publish_page&media_type=article&type=new"
    )
    ARTICLE_START_BUTTON_CLASS = "button.container-drag-btn-hcUuRC"
    ARTICLE_TITLE_SELECTORS = [
        "input[placeholder*='文章标题']",
        "input[placeholder*='请输入文章标题']",
    ]
    ARTICLE_SUMMARY_SELECTORS = [
        "input[placeholder*='内容摘要']",
        "input[placeholder*='精彩部分']",
    ]
    ARTICLE_EDITOR_SELECTORS = [
        "div.tiptap.ProseMirror[contenteditable='true']",
        "div[contenteditable='true'][role='textbox']",
    ]
    ARTICLE_HEAD_IMAGE_UPLOAD_SELECTORS = [
        "div.uploadButton-B4xMQ2",
        "div.emptyHeadImage-YXPBGr",
    ]
    ARTICLE_NO_HEAD_IMAGE_SELECTORS = [
        "label:has-text('无文章头图')",
        "span:has-text('无文章头图')",
    ]
    ARTICLE_PUBLISH_SELECTORS = [
        "button.button-dhlUZE.primary-cECiOJ",
        "button:has-text('发布')",
    ]
    ARTICLE_SUCCESS_TEXTS = [
        "text=发布成功",
        "text=已发布",
        "text=发布完成",
    ]

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.history_path = settings.output_dir / settings.douyin.history_filename

    def initialize_login_session(self) -> dict[str, str]:
        self._ensure_playwright()
        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.settings.douyin.headless,
                channel=self.settings.douyin.channel or None,
                executable_path=self.settings.douyin.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    self.settings.douyin.publish_url,
                    wait_until="domcontentloaded",
                    timeout=max(self.settings.douyin.wait_timeout_ms, 20000),
                )
                print("")
                print("Log in to your Douyin creator account in the opened browser window.")
                print("Make sure the account can actually open the article publish page.")
                print("This window will keep polling automatically for up to 10 minutes.")
                deadline = time.time() + 600
                while time.time() < deadline:
                    summary = self._collect_preflight_summary(page)
                    blockers = summary.get("blocking_flags", [])
                    if not blockers:
                        result = {
                            "profile_dir": str(profile_dir),
                            "current_url": page.url,
                            "publish_preflight": str(self._write_publish_preflight(summary)),
                        }
                        return result
                    print(
                        "Waiting for a Douyin publish-capable session. "
                        f"Current URL: {page.url} | blockers: {' | '.join(str(item) for item in blockers)}"
                    )
                    page.wait_for_timeout(1500)
                raise RuntimeError("Douyin login session was not confirmed within 10 minutes.")
            finally:
                browser.close()

    def prepare_run(self, run_dir: Path) -> dict[str, Path]:
        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        review_status = self._load_json(run_dir / "review_status.json")
        if str(review_status.get("status", "")).strip() != "approved":
            raise RuntimeError("Run is not approved yet. Approve review before publishing to Douyin.")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        title = self._clip_title(str(publish_meta.get("selected_title", "")).strip())
        article = self._read_text(publish_dir / "article.md")
        cleaned_article = self._prepare_article_body(title=title, article=article)
        summary = self._build_summary(cleaned_article)
        hashtags = self._build_hashtags(title=title, publish_meta=publish_meta)
        notes = self._build_notes(title=title, summary=summary, hashtags=hashtags, article=cleaned_article)
        cover_image_path = self._resolve_cover_image_path(run_dir)

        douyin_dir = publish_dir / "douyin"
        douyin_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "platform": "douyin",
            "publish_type": "article",
            "account_key": self.settings.account_key,
            "niche": self._normalize_plain_text(self.settings.douyin.niche) or "科技",
            "publish_mode": self.settings.douyin.publish_mode,
            "daily_post_target": int(self.settings.douyin.daily_post_target or 0),
            "title": title,
            "summary": summary,
            "article": cleaned_article,
            "hashtags": hashtags,
            "notes": notes,
            "cover_image_path": str(cover_image_path) if cover_image_path else "",
            "source_run_dir": str(run_dir),
        }

        payload_path = douyin_dir / "douyin_payload.json"
        title_path = douyin_dir / "title.txt"
        summary_path = douyin_dir / "summary.txt"
        article_path = douyin_dir / "article.txt"
        notes_path = douyin_dir / "publish_notes.txt"

        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        title_path.write_text(title + "\n", encoding="utf-8")
        summary_path.write_text(summary + "\n", encoding="utf-8")
        article_path.write_text(cleaned_article + "\n", encoding="utf-8")
        notes_path.write_text(notes.strip() + "\n", encoding="utf-8")

        return {
            "douyin_payload": payload_path,
            "douyin_title": title_path,
            "douyin_summary": summary_path,
            "douyin_article": article_path,
            "douyin_publish_notes": notes_path,
        }

    def publish_run(self, run_dir: Path) -> dict[str, Path]:
        self._ensure_enabled()
        prepared = self.prepare_run(run_dir)
        payload_path = prepared["douyin_payload"]
        payload = self._load_json(payload_path)

        self._ensure_playwright()
        result_dir = run_dir / self.settings.review.publish_dirname / "douyin"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "douyin_publish_result.json"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._resolve_browser_profile_dir()),
                headless=self.settings.douyin.headless,
                channel=self.settings.douyin.channel or None,
                executable_path=self.settings.douyin.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    self.settings.douyin.publish_url,
                    wait_until="domcontentloaded",
                    timeout=max(self.settings.douyin.wait_timeout_ms, 20000),
                )
                notes: list[str] = [f"Opened publish page: {page.url}"]
                self._open_article_editor(page)
                notes.append(f"Entered article editor: {page.url}")
                self._fill_article_title(page, str(payload.get("title", "")).strip())
                self._fill_article_summary(page, str(payload.get("summary", "")).strip())
                self._fill_article_body(page, str(payload.get("article", "")).strip())

                cover_path = self._coerce_existing_path(payload.get("cover_image_path"))
                if cover_path is not None:
                    self._apply_article_head_image(page, cover_path)
                    notes.append(f"Uploaded article head image: {cover_path.name}")
                elif self._set_no_head_image(page):
                    notes.append("Switched to no-head-image mode because no cover image was found.")
                else:
                    notes.append("No cover image was found and no-head-image toggle was not changed.")

                before_url = page.url
                self._click_article_publish(page)
                if not self._wait_for_publish_success(page, before_url):
                    raise RuntimeError(self._build_publish_failure_message(page))

                result_payload = {
                    "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "platform": "douyin",
                    "publish_type": "article",
                    "run_dir": str(run_dir),
                    "status": "published",
                    "title": str(payload.get("title", "")).strip(),
                    "summary": str(payload.get("summary", "")).strip(),
                    "body_length": len(str(payload.get("article", "")).strip()),
                    "external_url": page.url,
                    "notes": notes,
                }
                result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._append_history(result_payload)
                self.logger.info("Douyin article publish finished for run: %s", run_dir)
                return {
                    **prepared,
                    "douyin_publish_result": result_path,
                    "douyin_history": self.history_path,
                }
            except Exception as exc:
                debug_dir = self._capture_debug_artifacts(page, {"payload_path": str(payload_path), "payload": payload})
                failure_payload = {
                    "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "platform": "douyin",
                    "publish_type": "article",
                    "run_dir": str(run_dir),
                    "status": "failed",
                    "title": str(payload.get("title", "")).strip(),
                    "summary": str(payload.get("summary", "")).strip(),
                    "body_length": len(str(payload.get("article", "")).strip()),
                    "external_url": page.url,
                    "error": str(exc),
                    "debug_bundle_dir": str(debug_dir.resolve(strict=False)),
                    "debug_artifacts": self._serialize_debug_artifacts(debug_dir),
                }
                debug_artifacts = failure_payload.get("debug_artifacts", {}) if isinstance(failure_payload.get("debug_artifacts"), dict) else {}
                if debug_artifacts.get("screenshot"):
                    failure_payload["screenshot_path"] = debug_artifacts["screenshot"]
                if debug_artifacts.get("page_html"):
                    failure_payload["dom_path"] = debug_artifacts["page_html"]
                result_path.write_text(json.dumps(failure_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                raise RuntimeError(f"Douyin publish failed: {exc}. Debug files saved to: {debug_dir}") from exc
            finally:
                browser.close()

    def _ensure_enabled(self) -> None:
        if not self.settings.douyin.enabled:
            raise RuntimeError("Douyin publishing is disabled. Set DOUYIN_ENABLED=1 first.")

    def _ensure_playwright(self) -> None:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

    def _resolve_browser_profile_dir(self) -> Path:
        configured = (self.settings.douyin.user_data_dir or "").strip()
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = self.settings.project_root / candidate
            return candidate
        return self.settings.account_runtime_dir / "douyin_profile"

    def _write_publish_preflight(self, summary: dict[str, Any]) -> Path:
        self.settings.automation_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.automation_dir / "douyin_publish_preflight.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _collect_preflight_summary(self, page) -> dict[str, Any]:
        blocking_flags: list[str] = []
        body_text = self._extract_visible_body_text(page)
        login_required = self._looks_like_login_shell(page, body_text)
        if "creator.douyin.com" not in page.url:
            blocking_flags.append("Current browser session is not on a Douyin creator page.")
        if "/creator-micro/content/" not in page.url:
            blocking_flags.append("Current browser session does not appear to have an accessible Douyin publish page.")
        if login_required:
            blocking_flags.append("Current browser session requires a Douyin creator login.")

        summary = {
            "current_url": page.url,
            "blocking_flags": blocking_flags,
            "warnings": [],
            "diagnostics": {
                "title": page.title(),
                "login_required": login_required,
                "body_excerpt": body_text[:300],
            },
        }
        return summary

    def _prepare_article_body(self, *, title: str, article: str) -> str:
        cleaned = clean_article_text(article, [title]).strip()
        cleaned = re.sub(r"(?m)^\s*#{1,6}\s*", "", cleaned)
        cleaned = re.sub(r"(?m)^\s*[-*]\s+", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _build_summary(self, article: str) -> str:
        paragraphs = [self._normalize_plain_text(part) for part in article.split("\n\n") if self._normalize_plain_text(part)]
        if not paragraphs:
            return ""
        return self._clip_text(paragraphs[0], 30)

    def _build_hashtags(self, *, title: str, publish_meta: dict[str, Any]) -> list[str]:
        result: list[str] = []
        niche = self._normalize_plain_text(self.settings.douyin.niche)
        if niche and niche != "??":
            result.append(self._format_hashtag(niche))
        else:
            industry_focus = self._normalize_plain_text(self.settings.industry_focus)
            for focus in re.split(r"[、，, ]+", industry_focus):
                normalized_focus = self._normalize_plain_text(focus)
                if normalized_focus:
                    result.append(self._format_hashtag(normalized_focus))
                    break

        for raw_tag in list(publish_meta.get("tags", []) or []):
            normalized = self._normalize_plain_text(str(raw_tag))
            if normalized:
                result.append(self._format_hashtag(normalized))

        keyword_candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,10}", title)
        for keyword in keyword_candidates[:4]:
            if any(char.isdigit() for char in keyword):
                continue
            result.append(self._format_hashtag(keyword))

        deduped: list[str] = []
        seen: set[str] = set()
        for item in result:
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= 3:
                break
        return deduped or ["#科技", "#推荐"]

    def _build_notes(self, *, title: str, summary: str, hashtags: list[str], article: str) -> str:
        return "\n".join(
            [
                f"账号: {self.settings.account_label}",
                f"赛道: {self._normalize_plain_text(self.settings.douyin.niche) or '科技'}",
                f"标题: {title}",
                f"摘要: {summary}",
                f"话题: {' '.join(hashtags)}",
                "正文:",
                article,
            ]
        ).strip()

    def _resolve_cover_image_path(self, run_dir: Path) -> Path | None:
        candidates = [
            run_dir / self.settings.review.publish_dirname / "images" / "cover.png",
            run_dir / "images" / "cover.png",
        ]
        publish_meta_path = run_dir / self.settings.review.publish_dirname / "publish_meta.json"
        if publish_meta_path.exists():
            try:
                publish_meta = self._load_json(publish_meta_path)
            except Exception:
                publish_meta = {}
            image_paths = publish_meta.get("image_paths", {}) if isinstance(publish_meta, dict) else {}
            if isinstance(image_paths, dict):
                for key in ("cover.png", "cover"):
                    value = image_paths.get(key)
                    if value:
                        candidates.append(Path(str(value)))

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _open_article_editor(self, page) -> None:
        self._ensure_logged_in(page)
        page.wait_for_timeout(800)
        if "/content/post/article" not in page.url:
            self._open_publish_menu(page)
            clicked = self._click_menu_item(page, self.ARTICLE_TAB_LABEL)
            if clicked:
                page.wait_for_timeout(2000)
            else:
                page.goto(
                    self.ARTICLE_EDITOR_URL,
                    wait_until="domcontentloaded",
                    timeout=max(self.settings.douyin.wait_timeout_ms, 20000),
                )
                page.wait_for_timeout(2000)
        self._ensure_logged_in(page)

        with self._suppress_timeout():
            start_button = page.locator(self.ARTICLE_START_BUTTON_CLASS).first
            if start_button.count() > 0:
                page.mouse.click(1100, 150)
                page.wait_for_timeout(500)
                start_button.wait_for(state="visible", timeout=max(self.settings.douyin.wait_timeout_ms, 10000))
                start_button.click()
                page.wait_for_timeout(2500)
        self._wait_for_article_editor_ready(page)

    def _wait_for_article_editor_ready(self, page) -> None:
        attempts = 0
        while attempts < 3:
            attempts += 1
            self._ensure_logged_in(page)
            with self._suppress_timeout():
                page.wait_for_function(
                    """
                    () => {
                      const titleInput = document.querySelector("input[placeholder*='文章标题']");
                      const summaryInput = document.querySelector("input[placeholder*='内容摘要']");
                      const editor = document.querySelector("div.tiptap.ProseMirror[contenteditable='true']");
                      return Boolean(titleInput && editor) || Boolean(summaryInput && editor);
                    }
                    """,
                    timeout=max(self.settings.douyin.wait_timeout_ms, 20000),
                )
            if self._article_editor_ready(page):
                page.wait_for_timeout(1200)
                return
            if attempts < 3:
                if self._looks_like_loading_shell(page):
                    page.goto(
                        self.ARTICLE_EDITOR_URL,
                        wait_until="domcontentloaded",
                        timeout=max(self.settings.douyin.wait_timeout_ms, 20000),
                    )
                else:
                    page.reload(wait_until="domcontentloaded", timeout=max(self.settings.douyin.wait_timeout_ms, 20000))
                page.wait_for_timeout(2500)
        self._ensure_logged_in(page)
        raise RuntimeError("Douyin article editor did not finish loading.")

    def _article_editor_ready(self, page) -> bool:
        title_ready = False
        summary_ready = False
        editor_ready = False
        with self._suppress_timeout():
            title_ready = page.locator("input[placeholder*='文章标题']").count() > 0
        with self._suppress_timeout():
            summary_ready = page.locator("input[placeholder*='内容摘要']").count() > 0
        for selector in self.ARTICLE_EDITOR_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() > 0:
                    editor_ready = True
                    break
        return title_ready and summary_ready and editor_ready

    def _ensure_logged_in(self, page) -> None:
        body_text = self._extract_visible_body_text(page)
        if self._looks_like_login_shell(page, body_text):
            raise RuntimeError(
                "Douyin creator login is required for article publishing. "
                "Please run `python .\\main.py --init-douyin-publish-login`, complete the login, then retry."
            )

    def _looks_like_login_shell(self, page, body_text: str | None = None) -> bool:
        text = body_text if body_text is not None else self._extract_visible_body_text(page)
        normalized_url = page.url.lower()
        login_markers = [
            "创作者登录",
            "登录或注册即代表同意",
            "用户协议",
            "隐私政策",
            "千万创作者邀你同行",
            "创作者登录 / 机构登录",
            "创作者登录/机构登录",
        ]
        if any(marker in text for marker in login_markers):
            if "creator-micro/content/post/article" not in normalized_url:
                return True
            if not self._article_editor_ready(page):
                return True
        with self._suppress_timeout():
            if page.locator("div[class*='agreement']").count() > 0 and not self._article_editor_ready(page):
                return True
        return False

    def _looks_like_loading_shell(self, page) -> bool:
        body_text = self._extract_visible_body_text(page)
        return "加载中" in body_text and not self._article_editor_ready(page)

    def _open_publish_menu(self, page) -> None:
        for selector in [
            "#douyin-creator-master-side-upload-wrap button",
            "#douyin-creator-master-side-upload",
        ]:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.click()
                page.wait_for_timeout(600)
                return

    def _click_menu_item(self, page, label: str) -> bool:
        escaped = label.replace("'", "\\'")
        clicked = page.evaluate(
            f"""
            () => {{
              const visible = (el) => {{
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              }};
              const nodes = Array.from(document.querySelectorAll('.douyin-creator-master-dropdown-item, li, div, span'));
              for (const node of nodes) {{
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '');
                if (!visible(node) || text !== '{escaped}') continue;
                node.click();
                return true;
              }}
              return false;
            }}
            """
        )
        return bool(clicked)

    def _fill_article_title(self, page, title: str) -> None:
        title = self._clip_title(title)
        if self._fill_text_input(page, self.ARTICLE_TITLE_SELECTORS, title):
            return
        raise RuntimeError("Could not locate Douyin article title input.")

    def _fill_article_summary(self, page, summary: str) -> None:
        if not summary:
            return
        self._fill_text_input(page, self.ARTICLE_SUMMARY_SELECTORS, self._clip_text(summary, 30))

    def _fill_article_body(self, page, article: str) -> None:
        content = article.strip()
        if not content:
            raise RuntimeError("Douyin article body is empty.")
        for selector in self.ARTICLE_EDITOR_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=4000)
                locator.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(content, delay=8)
                page.wait_for_timeout(800)
                return
        raise RuntimeError("Could not locate Douyin article editor.")

    def _apply_article_head_image(self, page, image_path: Path) -> None:
        resolved = image_path.resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Douyin article head image not found: {resolved}")
        staged_upload = self._stage_upload_file(resolved)

        for selector in self.ARTICLE_HEAD_IMAGE_UPLOAD_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=4000)
                with page.expect_file_chooser(timeout=5000) as chooser_info:
                    locator.click()
                chooser_info.value.set_files(str(staged_upload))
                page.wait_for_timeout(2500)
                self._confirm_pending_image_dialog(page)
                if self._head_image_present(page):
                    return
        raise RuntimeError("Could not upload the Douyin article head image.")

    def _head_image_present(self, page) -> bool:
        with self._suppress_timeout():
            return bool(
                page.evaluate(
                    """
                    () => {
                      const head = document.querySelector('[data-article-head-image="true"]');
                      if (!head) return false;
                      const uploadButton = head.querySelector('.uploadButton-B4xMQ2');
                      const media = head.querySelectorAll('img:not(.buttonIcon-Auih8m):not(.preview-card-Mbwnmy):not(.avater-class-dG34yz):not(.music-cover-QgdOWo), canvas, video');
                      const styledMedia = Array.from(head.querySelectorAll('div')).some((node) => {
                        const style = node.getAttribute('style') || '';
                        return style.includes('background-image');
                      });
                      return media.length > 0 || styledMedia || !uploadButton;
                    }
                    """
                )
            )

    def _confirm_pending_image_dialog(self, page) -> None:
        for label in ("确定", "完成", "确认", "保存"):
            clicked = page.evaluate(
                """
                (targetText) => {
                  const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
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

    def _stage_upload_file(self, source: Path) -> Path:
        staging_dir = Path(tempfile.gettempdir()) / "toutiao_content_factory_douyin"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / ("cover" + source.suffix.lower())
        shutil.copy2(source, staged)
        return staged

    def _set_no_head_image(self, page) -> bool:
        for selector in self.ARTICLE_NO_HEAD_IMAGE_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.wait_for(state="visible", timeout=2000)
                locator.click()
                page.wait_for_timeout(500)
                return True
        return False

    def _click_article_publish(self, page) -> None:
        for selector in self.ARTICLE_PUBLISH_SELECTORS:
            with self._suppress_timeout():
                locator = page.locator(selector)
                count = locator.count()
                if count == 0:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        candidate.wait_for(state="visible", timeout=2000)
                    except Exception:
                        continue
                    button_text = self._normalize_plain_text(candidate.inner_text() or "")
                    if "发布" not in button_text or "高清发布" in button_text:
                        continue
                    try:
                        candidate.click(timeout=4000)
                    except Exception:
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
        clicked = page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const nodes = Array.from(document.querySelectorAll('button'));
              for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '');
                if (!visible(node)) continue;
                if (text === '发布') {
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
        raise RuntimeError("Could not locate the Douyin article publish button.")

    def _wait_for_publish_success(self, page, before_url: str) -> bool:
        success_text = list(self.settings.douyin.selectors.get("success_indicator", [])) or self.ARTICLE_SUCCESS_TEXTS
        deadline = time.time() + 20
        while time.time() < deadline:
            if page.url != before_url and "/content/post/article" not in page.url:
                return True
            for selector in success_text:
                with self._suppress_timeout():
                    if page.locator(selector).first.count() > 0:
                        return True
            page.wait_for_timeout(800)
        return False

    def _build_publish_failure_message(self, page) -> str:
        visible_text = self._extract_visible_body_text(page)
        hints: list[str] = []
        for phrase in [
            "请上传封面图",
            "请上传图片",
            "请填写文章标题",
            "请填写文章正文",
            "请完善",
            "不能为空",
            "发布失败",
        ]:
            if phrase in visible_text:
                hints.append(phrase)
        if hints:
            return "Douyin article publish did not complete: " + " | ".join(hints)
        return "Douyin article publish did not reach a success page or success toast."

    def _fill_text_input(self, page, selectors: list[str], value: str) -> bool:
        for _ in range(3):
            for selector in selectors:
                with self._suppress_timeout():
                    locator = page.locator(selector).first
                    if locator.count() == 0:
                        continue
                    locator.wait_for(state="visible", timeout=3000)
                    locator.fill(value)
                    return True
            page.wait_for_timeout(1000)
        return False

    def _extract_visible_body_text(self, page) -> str:
        with self._suppress_timeout():
            try:
                return self._normalize_plain_text(page.locator("body").inner_text(timeout=3000))
            except Exception:
                return ""

    def _capture_debug_artifacts(self, page, extra_payload: dict[str, Any] | None = None) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        debug_dir = self.settings.output_dir / "douyin_publish_debug" / timestamp
        debug_dir.mkdir(parents=True, exist_ok=True)
        html_path = debug_dir / "douyin_page.html"
        png_path = debug_dir / "douyin_page.png"
        url_path = debug_dir / "current_url.txt"
        context_path = debug_dir / "publish_context.json"

        with self._suppress_timeout():
            html_path.write_text(page.content(), encoding="utf-8")
        with self._suppress_timeout():
            page.screenshot(path=str(png_path), full_page=True)
        url_path.write_text(page.url, encoding="utf-8")
        context_path.write_text(json.dumps(extra_payload or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_latest_debug_bundle(debug_dir)
        self.logger.warning("Douyin debug files saved to: %s", debug_dir)
        return debug_dir

    def _serialize_debug_artifacts(self, debug_dir: Path) -> dict[str, str]:
        mapping = {
            "screenshot": debug_dir / "douyin_page.png",
            "page_html": debug_dir / "douyin_page.html",
            "current_url": debug_dir / "current_url.txt",
            "context": debug_dir / "publish_context.json",
        }
        artifacts: dict[str, str] = {}
        for key, candidate in mapping.items():
            if candidate.exists():
                artifacts[key] = str(candidate.resolve(strict=False))
        return artifacts

    def _refresh_latest_debug_bundle(self, debug_dir: Path) -> None:
        latest_dir = self.settings.output_dir / "debug"
        latest_dir.mkdir(parents=True, exist_ok=True)
        mapping = {
            "douyin_page.png": "latest_error.png",
            "douyin_page.html": "latest_error_dom.html",
            "current_url.txt": "latest_error_url.txt",
            "publish_context.json": "latest_error_context.json",
        }
        copied: dict[str, str] = {}
        for source_name, target_name in mapping.items():
            source = debug_dir / source_name
            if not source.exists():
                continue
            target = latest_dir / target_name
            shutil.copyfile(source, target)
            copied[source_name] = str(target.resolve(strict=False))
        (latest_dir / "latest_error_manifest.json").write_text(
            json.dumps(
                {
                    "platform": "douyin",
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "debug_dir": str(debug_dir.resolve(strict=False)),
                    "artifacts": copied,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _append_history(self, payload: dict[str, Any]) -> None:
        history: list[dict[str, Any]] = []
        if self.history_path.exists():
            try:
                loaded = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    history = [item for item in loaded if isinstance(item, dict)]
            except Exception:
                history = []
        history.append(payload)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    def _clip_title(self, title: str) -> str:
        return self._clip_text(self._normalize_plain_text(title), int(self.settings.douyin.title_max_length or 30))

    def _clip_text(self, value: str, max_length: int) -> str:
        normalized = self._normalize_plain_text(value)
        if len(normalized) <= max_length:
            return normalized
        return normalized[:max_length].rstrip()

    def _normalize_plain_text(self, value: str) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _format_hashtag(self, value: str) -> str:
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)
        if not cleaned:
            return ""
        return "#" + cleaned[:12]

    def _coerce_existing_path(self, value: Any) -> Path | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.settings.project_root / candidate
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Expected JSON object from {path}")
        return payload

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    class _suppress_timeout:
        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> bool:
            return bool(exc and isinstance(exc, PlaywrightTimeoutError))
