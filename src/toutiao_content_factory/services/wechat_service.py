from __future__ import annotations

import html
import json
import logging
import re
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.services.md2wechat_service import Md2WechatService

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class WechatPublishService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.history_path = settings.output_dir / settings.wechat.history_filename
        self.md2wechat_service = Md2WechatService(settings, logger)
        self.growth_ops_service = GrowthOpsService(settings, logger)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def publish_run(self, run_dir: Path, submit: bool = False) -> dict[str, Path]:
        self._ensure_enabled()
        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        review_status = self._load_json(run_dir / "review_status.json")
        if not publish_meta:
            raise FileNotFoundError("publish_meta.json is missing.")
        if str(review_status.get("status", "")) != "approved":
            raise RuntimeError("Run is not approved yet. Approve review before publishing.")

        title = self._clip_title(str(publish_meta.get("selected_title", "")).strip())
        summary = str(publish_meta.get("summary", "")).strip()
        article = self._read_text(publish_dir / "article.md")
        cover_path, inline_image_paths = self._resolve_image_assets(publish_dir)

        html_dir = publish_dir / "wechat"
        html_dir.mkdir(parents=True, exist_ok=True)
        payload_path = html_dir / "wechat_payload.json"
        article_markdown_path = html_dir / "article.md"
        article_html_path = html_dir / "article.html"
        result_path = html_dir / "wechat_publish_result.json"

        access_token = self._get_access_token()
        thumb_media_id = self._upload_cover_material(access_token, cover_path)
        inline_image_urls = [self._upload_inline_image(access_token, path) for path in inline_image_paths]
        article_markdown = self._compose_article_markdown(article=article, inline_image_urls=inline_image_urls)
        article_markdown_path.write_text(article_markdown, encoding="utf-8")
        article_html, layout_meta = self._render_article_html_via_backend(
            title=title,
            article=article,
            article_markdown=article_markdown,
            inline_image_urls=inline_image_urls,
        )
        article_html_path.write_text(article_html, encoding="utf-8")

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
            raise RuntimeError(f"Draft creation did not return media_id: {draft_response}")

        publish_response: dict[str, Any] = {}
        if submit or self.settings.wechat.auto_publish:
            publish_response = self._submit_draft_via_browser(
                draft_media_id=draft_media_id,
                title=title,
            )

        result_payload = {
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "platform": "wechat",
            "run_dir": str(run_dir),
            "status": "submitted" if publish_response else "draft_created",
            "draft_status": "created",
            "submit_status": str(publish_response.get("status", "")).strip() if publish_response else "",
            "draft_media_id": draft_media_id,
            "thumb_media_id": thumb_media_id,
            "inline_image_urls": inline_image_urls,
            "layout_backend": layout_meta.get("provider", "builtin"),
            "layout_request": layout_meta.get("request", {}),
            "submit_requested": submit or self.settings.wechat.auto_publish,
            "publish_response": publish_response,
            "external_url": str(publish_response.get("publish_url", "")).strip() if publish_response else "",
            "title": title,
        }
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_history(result_payload)
        growth_exports = self.growth_ops_service.register_publish_event(
            platform="wechat",
            run_dir=run_dir,
            result_path=result_path,
        )

        self.logger.info("Wechat draft prepared for run: %s", run_dir)
        return {
            "wechat_payload": payload_path,
            "wechat_article_markdown": article_markdown_path,
            "wechat_article_html": article_html_path,
            "wechat_publish_result": result_path,
            "wechat_history": self.history_path,
            **growth_exports,
        }

    def initialize_login_session(self) -> dict[str, str]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.settings.publishing.headless,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    self.settings.wechat.base_url,
                    wait_until="domcontentloaded",
                    timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
                )
                print("")
                print("Log in to your WeChat Official Account in the opened browser window.")
                print("When login is ready, make sure the 草稿箱 or 公众号后台 page is reachable.")
                deadline = time.time() + 600
                while time.time() < deadline:
                    if self._browser_session_ready(page):
                        summary = {
                            "profile_dir": str(profile_dir),
                            "current_url": page.url,
                            "state": "ready",
                        }
                        self.settings.automation_dir.mkdir(parents=True, exist_ok=True)
                        (self.settings.automation_dir / "wechat_publish_preflight.json").write_text(
                            json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        return summary
                    page.wait_for_timeout(1500)
                raise RuntimeError("WeChat login session was not confirmed within 10 minutes.")
            finally:
                browser.close()

    def _ensure_enabled(self) -> None:
        wechat = self.settings.wechat
        if not wechat.enabled:
            raise RuntimeError("WeChat publishing is disabled. Set WECHAT_ENABLED=1 first.")
        if not wechat.app_id or not wechat.app_secret:
            raise RuntimeError("WeChat AppID/AppSecret are missing. Set WECHAT_APP_ID and WECHAT_APP_SECRET.")

    def _resolve_browser_profile_dir(self) -> Path:
        return self.settings.account_runtime_dir / "wechat_profile"

    def _resolve_image_assets(self, publish_dir: Path) -> tuple[Path, list[Path]]:
        publish_images_dir = publish_dir / "images"
        run_images_dir = publish_dir.parent / "images"
        image_roots = [publish_images_dir, run_images_dir]

        cover_path: Path | None = None
        for root in image_roots:
            candidate = root / "cover.png"
            if candidate.exists():
                cover_path = candidate
                break
        if cover_path is None:
            raise FileNotFoundError(
                f"Cover image not found under: {publish_images_dir} or {run_images_dir}"
            )

        inline_image_paths: list[Path] = []
        for image_name in ("infographic_1.png", "infographic_2.png"):
            for root in image_roots:
                candidate = root / image_name
                if candidate.exists():
                    inline_image_paths.append(candidate)
                    break
        return cover_path, inline_image_paths

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
        if self.md2wechat_service.available:
            try:
                payload = self.md2wechat_service.convert(article_markdown)
                return str(payload.get("html", "")).strip(), payload
            except Exception as exc:
                if not self.settings.wechat.md2wechat_fallback_to_builtin:
                    raise
                self.logger.warning("md2wechat conversion failed, falling back to builtin renderer: %s", exc)
        elif self.md2wechat_service.enabled:
            self.logger.warning(
                "md2wechat backend enabled but neither API key nor local CLI is available; falling back to builtin."
            )

        return (
            self._render_article_html(title=title, article=article, inline_image_urls=inline_image_urls),
            {"provider": "builtin"},
        )

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
        response = self._wechat_post_multipart(
            "/cgi-bin/material/add_material",
            access_token=access_token,
            fields={"type": "image"},
            files={"media": cover_path},
        )
        media_id = str(response.get("media_id", "")).strip()
        if not media_id:
            raise RuntimeError(f"Cover material upload failed: {response}")
        return media_id

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
        try:
            with urlopen(request, timeout=60) as response:
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
        url = f"{wechat.api_base_url.rstrip('/')}/cgi-bin/token?{query}"
        request = Request(url)
        payload = self._read_json_response(request)
        token = str(payload.get("access_token", "")).strip()
        expires_in = int(payload.get("expires_in", 7200) or 7200)
        if not token:
            raise RuntimeError(f"WeChat access_token request returned no token: {payload}")
        self._access_token = token
        self._access_token_expires_at = now + max(300, expires_in - 120)
        return token

    def _build_api_url(self, endpoint: str, *, access_token: str) -> str:
        base = self.settings.wechat.api_base_url.rstrip("/")
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

    def _submit_draft_via_browser(self, *, draft_media_id: str, title: str) -> dict[str, Any]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        debug_dir: Path | None = None

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel=self.settings.publishing.channel or None,
                executable_path=self.settings.publishing.executable_path or None,
            )
            page = None
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                self._open_wechat_backend(page)
                self._open_wechat_draft_box(page)
                self._open_wechat_draft_by_title(page, title)
                self._click_publish_button_on_wechat_page(page)
                self._confirm_wechat_publish_dialog(page)
                self._wait_for_wechat_publish_success(page)
                result = {
                    "status": "submitted",
                    "method": "browser",
                    "draft_media_id": draft_media_id,
                    "title": title,
                    "publish_url": page.url,
                }
                return result
            except Exception as exc:
                debug_dir = self._capture_browser_debug_artifacts(page, {"draft_media_id": draft_media_id, "title": title})
                raise RuntimeError(
                    f"WeChat browser publish failed: {self._clean_visible_text(exc)}. Debug files saved to: {debug_dir}"
                ) from exc
            finally:
                browser.close()

    def _open_wechat_backend(self, page) -> None:
        page.goto(
            self.settings.wechat.base_url,
            wait_until="domcontentloaded",
            timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
        )
        page.wait_for_timeout(2500)

    def _open_wechat_draft_box(self, page) -> None:
        candidate_texts = ("草稿箱", "Drafts", "草稿")
        deadline = time.time() + 60
        while time.time() < deadline:
            for text in candidate_texts:
                with suppress(Exception):
                    locator = page.get_by_text(text, exact=False).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(3000)
                        return
            page.wait_for_timeout(1000)
        raise RuntimeError("Could not open WeChat draft box from the current session.")

    def _open_wechat_draft_by_title(self, page, title: str) -> None:
        locator = page.get_by_text(title, exact=False).first
        deadline = time.time() + 60
        while time.time() < deadline:
            with suppress(Exception):
                if locator.is_visible(timeout=800):
                    locator.click(force=True, timeout=2000)
                    page.wait_for_timeout(2500)
                    return
            search_inputs = [
                "input[placeholder*='搜索']",
                "input[placeholder*='查找']",
                "input[type='search']",
            ]
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

    def _click_publish_button_on_wechat_page(self, page) -> None:
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

    def _confirm_wechat_publish_dialog(self, page) -> None:
        confirm_texts = (
            "发表",
            "确认",
            "确定",
            "群发",
            "继续",
            "我知道了",
        )
        page.wait_for_timeout(1500)
        for _ in range(20):
            for text in confirm_texts:
                with suppress(Exception):
                    locator = page.get_by_role("button", name=text).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(2000)
                        return
            for text in confirm_texts:
                with suppress(Exception):
                    locator = page.get_by_text(text, exact=False).first
                    if locator.is_visible(timeout=500):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(2000)
                        return
            page.wait_for_timeout(1000)

    def _wait_for_wechat_publish_success(self, page) -> None:
        success_markers = ("发布成功", "提交成功", "群发成功", "已群发", "发布完成")
        deadline = time.time() + 90
        while time.time() < deadline:
            current_text = ""
            with suppress(Exception):
                current_text = page.locator("body").inner_text(timeout=1000)
            if any(marker in current_text for marker in success_markers):
                return
            if any(marker in page.url for marker in ("publish_done", "success", "msg")):
                return
            page.wait_for_timeout(1500)
        raise RuntimeError("WeChat publish did not reach a success state within the timeout.")

    def _browser_session_ready(self, page) -> bool:
        ready_markers = ("草稿箱", "公众号后台", "新建群发", "发表", "发布")
        with suppress(Exception):
            body_text = page.locator("body").inner_text(timeout=1000)
            if any(marker in body_text for marker in ready_markers):
                return True
        return any(marker in page.url for marker in ("mp.weixin.qq.com", "appmsg"))

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
