from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import time
import textwrap
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageOps

from toutiao_content_factory.config import AppSettings

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    OpenAI = None

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    sync_playwright = None


class ImageGenerationService:
    MAX_PUBLISH_IMAGE_BYTES = 1_500_000
    COMPRESS_TRIGGER_BYTES = 2_000_000
    MAX_PUBLISH_IMAGE_WIDTH = 1200
    IMAGE_API_RETRY_DELAYS = (0.0, 2.0)
    HTML_FALLBACK_CHANNELS = ("msedge", "chrome")
    DEFAULT_IMAGE_API_TIMEOUT_SECONDS = 15
    BACKEND_TIMEOUTS = {"image2": 12.0, "gpt-image-2": 12.0, "dalle3": 15.0, "dall-e-3": 15.0, "openai": 15.0}

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.client = None
        self._fallback_events: list[dict[str, Any]] = []
        self._successful_remote_backend: str = ""

        image_settings = settings.image_generation
        self._api_timeout = float(
            os.getenv(
                "IMAGE_REQUEST_TIMEOUT_SECONDS",
                str(image_settings.request_timeout_seconds or self.DEFAULT_IMAGE_API_TIMEOUT_SECONDS),
            )
        )
        self._backend_priority = self._parse_backend_priority()
        if image_settings.backend != "renderer" and image_settings.model_api_key and OpenAI is not None:
            client_kwargs: dict[str, Any] = {"api_key": image_settings.model_api_key}
            if image_settings.model_base_url:
                client_kwargs["base_url"] = image_settings.model_base_url
            client_kwargs["timeout"] = self._api_timeout
            self.client = OpenAI(**client_kwargs)

    def generate_for_run(self, run_dir: Path, *, include_infographics: bool = True) -> dict[str, Path]:
        self._fallback_events = []
        self._successful_remote_backend = ""
        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        title = str(publish_meta.get("selected_title", "")).strip() or "科技行业观察"
        summary = str(publish_meta.get("summary", "")).strip()

        cover_prompt = self._read_text(publish_dir / "cover_prompt.txt").strip() or str(
            publish_meta.get("cover_prompt", "")
        ).strip()
        infographic_prompts: list[str] = []
        if include_infographics:
            infographic_prompts = self._load_infographic_prompts(
                publish_dir / "infographic_prompts.txt",
                fallback=publish_meta.get("infographic_prompts", []),
            )
            if not infographic_prompts:
                article = self._read_text(publish_dir / "article.md")
                infographic_prompts = self._build_default_infographic_prompts(
                    title=title,
                    summary=summary,
                    article=article,
                )

        run_images_dir = run_dir / "images"
        publish_images_dir = publish_dir / "images"
        run_images_dir.mkdir(parents=True, exist_ok=True)
        publish_images_dir.mkdir(parents=True, exist_ok=True)

        cover_image = self._generate_cover_image(title=title, summary=summary, prompt=cover_prompt)
        cover_run_path = run_images_dir / "cover.png"
        cover_publish_path = publish_images_dir / "cover.png"
        cover_image.save(cover_run_path, format="PNG")
        cover_image.save(cover_publish_path, format="PNG")
        self._optimize_publish_image(cover_run_path)
        self._optimize_publish_image(cover_publish_path)

        infographic_paths: list[Path] = []
        max_infographics = max(1, int(self.settings.image_generation.max_generated_assets))
        if include_infographics:
            for index, prompt in enumerate(infographic_prompts[:max_infographics], start=1):
                infographic = self._generate_infographic_image(prompt)
                run_path = run_images_dir / f"infographic_{index}.png"
                publish_path = publish_images_dir / f"infographic_{index}.png"
                infographic.save(run_path, format="PNG")
                infographic.save(publish_path, format="PNG")
                self._optimize_publish_image(run_path)
                self._optimize_publish_image(publish_path)
                infographic_paths.append(run_path)
        else:
            for stale_path in sorted(publish_images_dir.glob("infographic_*.png")):
                stale_path.unlink(missing_ok=True)
            for stale_path in sorted(run_images_dir.glob("infographic_*.png")):
                stale_path.unlink(missing_ok=True)

        result_payload = {
            "generated_at": self._now(),
            "backend": self.settings.image_generation.backend,
            "backend_priority": self._backend_priority,
            "model": self.settings.image_generation.model_name,
            "successful_remote_backend": self._successful_remote_backend,
            "used_fallback": bool(self._fallback_events),
            "fallback_events": list(self._fallback_events),
            "cover_prompt": cover_prompt,
            "infographic_prompts": infographic_prompts[:max_infographics] if include_infographics else [],
            "artifacts": {
                "cover": str(cover_run_path),
                "infographics": [str(path) for path in infographic_paths],
            },
            "include_infographics": include_infographics,
        }
        result_path = publish_dir / "image_generation_result.json"
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self._fallback_events:
            self._write_fallback_notification(run_dir=run_dir, result_path=result_path)

        return {
            "cover_image": cover_run_path,
            "image_generation_result": result_path,
            **{f"infographic_{idx}": path for idx, path in enumerate(infographic_paths, start=1)},
        }

    def _generate_cover_image(self, *, title: str, summary: str, prompt: str) -> Image.Image:
        size = self._parse_size(self.settings.image_generation.cover_size, fallback=(1536, 1024))
        cover_prompt = self._build_cover_prompt(title=title, summary=summary, prompt=prompt)
        if self._prefers_remote_backends():
            try:
                return self._generate_via_priority_backends(prompt=cover_prompt, size=size, mode="cover")
            except Exception as exc:
                self.logger.warning("Direct image cover generation failed, falling back to composed cover: %s", exc)
                if not self.settings.image_generation.fallback_to_renderer:
                    raise
                self._record_fallback_event(mode="cover", size=size, prompt=cover_prompt, error=exc)
        background_prompt = (
            f"{prompt} "
            "Do not render any text, letters, watermarks, logos, UI panels or captions. "
            "Leave clean negative space for later Chinese title overlay. "
            "High-end editorial cover, strong lighting contrast, precise composition."
        ).strip()
        background = self._generate_or_fallback(prompt=background_prompt, size=size, mode="cover")
        return self._compose_cover(background, title=title, summary=summary, size=size)

    def _build_cover_prompt(self, *, title: str, summary: str, prompt: str) -> str:
        clipped_title = str(title or "").strip()[:30]
        clipped_summary = str(summary or "").strip()[:60]
        return (
            f"{prompt} "
            "Create a polished WeChat article cover in Chinese business-tech editorial style. "
            "The image must look like a premium public-account feature cover, not a poster ad. "
            "Use strong focal subject, clean composition, restrained colors, subtle information-card feeling. "
            "Reserve a clean title area with high contrast. "
            f"Main Chinese headline: {clipped_title}. "
            f"Optional small supporting line: {clipped_summary}. "
            "Typography must be clean, readable, and natural Chinese, no gibberish, no extra watermarks, no logos."
        ).strip()

    def _build_default_infographic_prompts(self, *, title: str, summary: str, article: str) -> list[str]:
        clipped_title = str(title or "").strip()[:30]
        clipped_summary = str(summary or "").strip()[:80]
        article_excerpt = " ".join(str(article or "").replace("\r", "\n").split())[:260]
        return [
            (
                "Create a vertical Chinese editorial infographic for insertion inside a WeChat article body. "
                "The style should feel like a premium business-tech explainer image, not a marketing poster. "
                f"Topic title: {clipped_title}. "
                f"Core takeaway: {clipped_summary}. "
                f"Article context: {article_excerpt}. "
                "Use one strong central visual metaphor, restrained blue-gray palette, clean card-based composition, "
                "mobile-friendly portrait framing, and only minimal natural Chinese labels if necessary. "
                "No gibberish, no watermark, no logo."
            ).strip()
        ]

    def _generate_infographic_image(self, prompt: str) -> Image.Image:
        size = self._parse_size(self.settings.image_generation.infographic_size, fallback=(1024, 1536))
        infographic_prompt = (
            f"{prompt} "
            "No gibberish text. Prefer clean Chinese-ready layout zones, strong data-viz feel, "
            "dark tech editorial style, mobile-friendly vertical composition."
        ).strip()
        image = self._generate_or_fallback(prompt=infographic_prompt, size=size, mode="infographic")
        return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)

    def _generate_or_fallback(self, *, prompt: str, size: tuple[int, int], mode: str) -> Image.Image:
        if self._prefers_remote_backends():
            try:
                return self._generate_via_priority_backends(prompt=prompt, size=size, mode=mode)
            except Exception as exc:
                self.logger.warning("Image backend generation failed for %s, falling back: %s", mode, exc)
                if not self.settings.image_generation.fallback_to_renderer:
                    raise
                self._record_fallback_event(mode=mode, size=size, prompt=prompt, error=exc)
        elif str(self.settings.image_generation.backend).strip().lower() != "renderer":
            message = "Image API client is unavailable. Check IMAGE_BACKEND / IMAGE_API_KEY / IMAGE_BASE_URL."
            self.logger.warning("Image API client unavailable for %s, falling back: %s", mode, message)
            if not self.settings.image_generation.fallback_to_renderer:
                raise RuntimeError(message)
            self._record_fallback_event(mode=mode, size=size, prompt=prompt, error=RuntimeError(message))
        html_fallback = self._render_html_fallback(prompt=prompt, size=size, mode=mode)
        if html_fallback is not None:
            return html_fallback
        return self._render_fallback(prompt=prompt, size=size, mode=mode)

    def _generate_via_api(self, *, prompt: str, size: tuple[int, int], backend_name: str = "image2") -> Image.Image:
        if self.client is None:
            raise RuntimeError("Image API client is unavailable.")

        size_text = f"{size[0]}x{size[1]}"
        model_name = self.settings.image_generation.model_name
        kwargs: dict[str, Any] = {
            "model": model_name,
            "prompt": prompt,
            "size": size_text,
            "quality": self.settings.image_generation.quality,
            "background": self.settings.image_generation.background,
            "response_format": "b64_json",
            "output_format": "png",
            "n": 1,
        }
        style = self.settings.image_generation.style
        if style in {"vivid", "natural"}:
            kwargs["style"] = style

        timeout_override = self.BACKEND_TIMEOUTS.get(backend_name, self._api_timeout)
        last_error: Exception | None = None
        response = None
        original_timeout = getattr(self.client, "timeout", None)
        try:
            if hasattr(self.client, "timeout"):
                self.client.timeout = timeout_override
            for attempt, delay in enumerate(self.IMAGE_API_RETRY_DELAYS, start=1):
                if delay > 0:
                    time.sleep(delay)
                try:
                    response = self.client.images.generate(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
                    if "response_format" in kwargs:
                        try:
                            retry_kwargs = dict(kwargs)
                            retry_kwargs.pop("response_format", None)
                            response = self.client.images.generate(**retry_kwargs)
                            break
                        except Exception as retry_exc:
                            last_error = retry_exc
                    if not self._is_retryable_image_error(last_error) or attempt == len(self.IMAGE_API_RETRY_DELAYS):
                        raise last_error
                    next_delay = self.IMAGE_API_RETRY_DELAYS[attempt] if attempt < len(self.IMAGE_API_RETRY_DELAYS) else 0.0
                    self.logger.warning(
                        "Image API attempt %s/%s failed for backend %s model %s, retrying in %ss: %s",
                        attempt,
                        len(self.IMAGE_API_RETRY_DELAYS),
                        backend_name,
                        model_name,
                        next_delay,
                        last_error,
                    )
        finally:
            if hasattr(self.client, "timeout"):
                self.client.timeout = original_timeout

        if response is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Image API request failed without an explicit exception.")

        data = response.data[0]
        if getattr(data, "b64_json", None):
            raw = base64.b64decode(data.b64_json)
            self._successful_remote_backend = backend_name
            return Image.open(io.BytesIO(raw)).convert("RGB")
        if getattr(data, "url", None):
            request = Request(str(data.url), headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=timeout_override) as remote:
                self._successful_remote_backend = backend_name
                return Image.open(io.BytesIO(remote.read())).convert("RGB")
        raise RuntimeError(f"Image API returned neither b64_json nor url for backend {backend_name}.")

    def _parse_backend_priority(self) -> list[str]:
        raw = str(self.settings.image_generation.backend_priority or self.settings.image_generation.backend or "").strip()
        parts = [item.strip().lower() for item in raw.split(",") if item.strip()]
        if not parts:
            parts = [str(self.settings.image_generation.backend or "renderer").strip().lower() or "renderer"]
        normalized: list[str] = []
        for item in parts:
            alias = "local" if item in {"renderer", "local_renderer"} else item
            if alias not in normalized:
                normalized.append(alias)
        return normalized

    def _prefers_remote_backends(self) -> bool:
        return any(item not in {"renderer", "local"} for item in self._backend_priority)

    def _generate_via_priority_backends(self, *, prompt: str, size: tuple[int, int], mode: str) -> Image.Image:
        errors: list[str] = []
        for backend in self._backend_priority:
            if backend in {"renderer", "local"}:
                continue
            try:
                if backend in {"image2", "gpt-image-2", "dalle3", "dall-e-3", "openai"}:
                    image = self._generate_via_api(prompt=prompt, size=size, backend_name=backend)
                    self._successful_remote_backend = backend
                    return image
                raise RuntimeError(f"Unsupported image backend: {backend}")
            except Exception as exc:
                errors.append(f"{backend}: {exc}")
                self.logger.warning("Image backend %s failed for %s: %s", backend, mode, exc)
                self._record_fallback_event(mode=f"{mode}_{backend}", size=size, prompt=prompt, error=exc)
                continue
        if errors:
            raise RuntimeError(" | ".join(errors))
        raise RuntimeError("No remote image backend is available.")

    def _is_retryable_image_error(self, error: Exception) -> bool:
        message = str(error).strip().lower()
        retry_markers = (
            "502",
            "503",
            "504",
            "bad gateway",
            "gateway",
            "timeout",
            "timed out",
            "tempor",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "rate limit",
            "429",
            "server error",
            "upstream",
            "cf-ray",
        )
        non_retryable_markers = (
            "400",
            "401",
            "403",
            "404",
            "invalid",
            "unsupported",
            "requires an image model",
            "billing",
            "insufficient",
            "quota",
            "permission",
        )
        if any(marker in message for marker in non_retryable_markers):
            return False
        return any(marker in message for marker in retry_markers)

    def _compose_cover(
        self,
        background: Image.Image,
        *,
        title: str,
        summary: str,
        size: tuple[int, int],
    ) -> Image.Image:
        image = ImageOps.fit(background.convert("RGB"), size, method=Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        self._draw_cover_overlays(draw, size)

        label_font = self._load_font(self.settings.image_generation.font_path, 30)
        title_font = self._load_font(self.settings.image_generation.bold_font_path, 76)
        subtitle_font = self._load_font(self.settings.image_generation.font_path, 28)

        left = int(size[0] * 0.08)
        top = int(size[1] * 0.11)
        max_width = int(size[0] * 0.74)

        draw.rounded_rectangle((left, top, left + 250, top + 48), radius=18, fill=(24, 224, 255, 220))
        draw.text((left + 18, top + 8), "机器人产业观察", font=label_font, fill=(5, 15, 28, 255))

        title_y = top + 94
        title_lines = self._wrap_text(draw, title, title_font, max_width=max_width, max_lines=3)
        for line in title_lines:
            draw.text((left, title_y), line, font=title_font, fill=(245, 248, 255, 255))
            title_y += self._line_height(title_font, 1.18)

        subtitle = summary.strip()[:58]
        if subtitle:
            subtitle_lines = self._wrap_text(draw, subtitle, subtitle_font, max_width=max_width, max_lines=2)
            subtitle_y = title_y + 24
            for line in subtitle_lines:
                draw.text((left, subtitle_y), line, font=subtitle_font, fill=(190, 206, 226, 255))
                subtitle_y += self._line_height(subtitle_font, 1.25)

        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    def _draw_cover_overlays(self, draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
        width, height = size
        for step in range(12):
            alpha = int(170 * ((step + 1) / 12))
            draw.rectangle(
                (
                    0,
                    height - int(height * 0.42) + step * 20,
                    width,
                    height,
                ),
                fill=(6, 11, 20, alpha),
            )
        for step in range(8):
            alpha = int(95 * ((step + 1) / 8))
            draw.rectangle(
                (
                    0,
                    0,
                    int(width * 0.62) + step * 24,
                    height,
                ),
                fill=(3, 8, 16, alpha),
            )

    def _render_html_fallback(
        self,
        *,
        prompt: str,
        size: tuple[int, int],
        mode: str,
    ) -> Image.Image | None:
        if sync_playwright is None:
            return None

        html = self._build_html_fallback_markup(prompt=prompt, size=size, mode=mode)
        with tempfile.TemporaryDirectory(prefix="toutiao-image-fallback-") as temp_dir:
            html_path = Path(temp_dir) / f"{mode}.html"
            screenshot_path = Path(temp_dir) / f"{mode}.png"
            html_path.write_text(html, encoding="utf-8")
            try:
                with sync_playwright() as playwright:
                    browser = self._launch_html_fallback_browser(playwright)
                    try:
                        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
                        page.goto(html_path.as_uri(), wait_until="load")
                        page.screenshot(path=str(screenshot_path), full_page=False)
                    finally:
                        browser.close()
                return Image.open(screenshot_path).convert("RGB")
            except Exception as exc:
                install_hint = ""
                if "Executable doesn't exist" in str(exc) or "chrome-headless-shell.exe" in str(exc):
                    install_hint = " Run `python -m playwright install chromium` to enable the HTML screenshot fallback."
                self.logger.warning(
                    "HTML image fallback failed for %s, continuing to Pillow fallback: %s%s",
                    mode,
                    exc,
                    install_hint,
                )
                self._fallback_events.append(
                    {
                        "created_at": self._now(),
                        "mode": f"{mode}_html_fallback",
                        "size": f"{size[0]}x{size[1]}",
                        "requested_backend": self.settings.image_generation.backend,
                        "model": self.settings.image_generation.model_name,
                        "prompt_excerpt": textwrap.shorten(prompt, width=180, placeholder="..."),
                        "error": f"HTML fallback failed: {exc}{install_hint}",
                    }
                )
                return None

    def _launch_html_fallback_browser(self, playwright: Any) -> Any:
        launch_errors: list[str] = []
        for channel in self.HTML_FALLBACK_CHANNELS:
            try:
                return playwright.chromium.launch(channel=channel, headless=True)
            except Exception as exc:
                launch_errors.append(f"{channel}: {exc}")
        try:
            return playwright.chromium.launch(headless=True)
        except Exception as exc:
            launch_errors.append(f"default: {exc}")
        raise RuntimeError(" | ".join(launch_errors))

    def _build_html_fallback_markup(self, *, prompt: str, size: tuple[int, int], mode: str) -> str:
        escaped_prompt = (
            str(prompt or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        title = "AI 配图本地高保真兜底" if mode == "cover" else "正文图本地高保真兜底"
        subtitle = "image2 当前不可用，已自动切换到 HTML/CSS 截图渲染。"
        accent = self.settings.image_generation.accent_color or "#26D9FF"
        background = self.settings.image_generation.background_color or "#060B14"
        secondary = self.settings.image_generation.secondary_color or "#122238"
        text_color = self.settings.image_generation.text_color or "#F5F8FF"
        muted = self.settings.image_generation.muted_text_color or "#A4B4CA"
        width, height = size
        prompt_excerpt = textwrap.shorten(" ".join(escaped_prompt.split()), width=240, placeholder="...")
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      --bg: {background};
      --secondary: {secondary};
      --accent: {accent};
      --text: {text_color};
      --muted: {muted};
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 80% 20%, rgba(38, 217, 255, 0.28), transparent 28%),
        radial-gradient(circle at 20% 80%, rgba(110, 92, 255, 0.18), transparent 24%),
        linear-gradient(160deg, var(--bg), var(--secondary));
      color: var(--text);
    }}
    .frame {{
      position: relative;
      width: 100%;
      height: 100%;
      padding: {72 if mode == "cover" else 58}px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 10px 18px;
      border-radius: 999px;
      background: rgba(38, 217, 255, 0.16);
      border: 1px solid rgba(38, 217, 255, 0.45);
      color: var(--accent);
      font-size: {26 if mode == "cover" else 22}px;
      letter-spacing: 0.08em;
    }}
    .title {{
      margin-top: 34px;
      max-width: 78%;
      font-size: {74 if mode == "cover" else 58}px;
      line-height: 1.14;
      font-weight: 700;
      text-wrap: balance;
    }}
    .subtitle {{
      margin-top: 22px;
      max-width: 70%;
      font-size: {28 if mode == "cover" else 24}px;
      line-height: 1.65;
      color: var(--muted);
    }}
    .prompt-card {{
      position: absolute;
      left: {72 if mode == "cover" else 58}px;
      right: {72 if mode == "cover" else 58}px;
      bottom: {58 if mode == "cover" else 48}px;
      padding: 26px 30px;
      border-radius: 30px;
      background: rgba(9, 16, 30, 0.72);
      border: 1px solid rgba(255, 255, 255, 0.08);
      backdrop-filter: blur(12px);
      box-shadow: 0 24px 70px rgba(0, 0, 0, 0.28);
    }}
    .prompt-label {{
      font-size: 20px;
      color: var(--accent);
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .prompt-text {{
      margin-top: 12px;
      font-size: {24 if mode == "cover" else 22}px;
      line-height: 1.7;
      color: var(--text);
    }}
    .grid {{
      position: absolute;
      inset: 0;
      background-image:
        linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: linear-gradient(to bottom, transparent, rgba(0,0,0,0.6) 18%, rgba(0,0,0,0.85));
      pointer-events: none;
    }}
  </style>
</head>
<body>
  <div class="grid"></div>
  <main class="frame">
    <div class="badge">{title}</div>
    <section class="title">{title}</section>
    <section class="subtitle">{subtitle}</section>
    <section class="prompt-card">
      <div class="prompt-label">Prompt excerpt</div>
      <div class="prompt-text">{prompt_excerpt}</div>
    </section>
  </main>
</body>
</html>
"""

    def _render_fallback(self, *, prompt: str, size: tuple[int, int], mode: str) -> Image.Image:
        image = Image.new("RGB", size, self._hex_to_rgb(self.settings.image_generation.background_color))
        draw = ImageDraw.Draw(image)
        self._draw_gradient_background(image)

        label_font = self._load_font(self.settings.image_generation.font_path, 30)
        title_font = self._load_font(self.settings.image_generation.bold_font_path, 72 if mode == "cover" else 56)
        body_font = self._load_font(self.settings.image_generation.font_path, 28)

        label = "本地降级封面" if mode == "cover" else "本地降级配图"
        draw.rounded_rectangle((64, 60, 320, 108), radius=18, fill=self._rgba("#26D9FF", 0.92))
        draw.text((82, 70), label, font=label_font, fill=(3, 10, 18))

        headline = self._extract_fallback_title(prompt)
        lines = self._wrap_text(draw, headline, title_font, max_width=size[0] - 140, max_lines=4 if mode == "cover" else 5)
        y = 180
        for line in lines:
            draw.text((70, y), line, font=title_font, fill=(245, 248, 255))
            y += self._line_height(title_font, 1.16)

        note = "远程图片接口当前不可用，已切换为本地排版渲染。"
        for line in self._wrap_text(draw, note, body_font, max_width=size[0] - 140, max_lines=3):
            draw.text((70, y + 30), line, font=body_font, fill=(164, 180, 202))
            y += self._line_height(body_font, 1.2)

        return image

    def _extract_fallback_title(self, prompt: str) -> str:
        text = str(prompt or "").strip()
        for marker in ("Main Chinese headline:", "Topic title:", "标题："):
            if marker in text:
                snippet = text.split(marker, 1)[1].strip()
                snippet = snippet.split(".", 1)[0].strip()
                if snippet:
                    return snippet[:36]
        compact = " ".join(text.split())
        return compact[:36] or "内容封面"

    def _draw_gradient_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        size = image.size
        width, height = size
        for step in range(height):
            ratio = step / max(1, height - 1)
            r = int(6 + (18 - 6) * ratio)
            g = int(11 + (34 - 11) * ratio)
            b = int(20 + (54 - 20) * ratio)
            draw.line((0, step, width, step), fill=(r, g, b))

        accent = Image.new("RGBA", size, (0, 0, 0, 0))
        accent_draw = ImageDraw.Draw(accent)
        accent_draw.ellipse(
            (int(width * 0.62), int(height * 0.08), int(width * 1.02), int(height * 0.72)),
            fill=(38, 217, 255, 42),
        )
        accent_draw.ellipse(
            (int(width * 0.48), int(height * 0.28), int(width * 0.94), int(height * 0.94)),
            fill=(110, 92, 255, 38),
        )
        if image.mode == "RGBA":
            image.alpha_composite(accent)
            return

        composed = Image.alpha_composite(image.convert("RGBA"), accent).convert(image.mode)
        image.paste(composed)

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        *,
        max_width: int,
        max_lines: int,
    ) -> list[str]:
        chunks: list[str] = []
        current = ""
        for char in text.strip():
            candidate = f"{current}{char}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = char
        if current:
            chunks.append(current)
        if len(chunks) <= max_lines:
            return chunks
        trimmed = chunks[:max_lines]
        last = trimmed[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1]
        trimmed[-1] = (last or trimmed[-1][:1]) + "…"
        return trimmed

    def _line_height(self, font: ImageFont.ImageFont, multiplier: float) -> int:
        bbox = font.getbbox("中文Ag")
        raw = bbox[3] - bbox[1]
        return max(24, int(math.ceil(raw * multiplier)))

    def _load_infographic_prompts(self, path: Path, fallback: Any) -> list[str]:
        text = self._read_text(path).strip()
        if text:
            result = [item.strip() for item in text.split("\n\n") if item.strip()]
            if result:
                return result
        if isinstance(fallback, list):
            return [str(item).strip() for item in fallback if str(item).strip()]
        return []

    def _load_font(self, font_path: str, size: int) -> ImageFont.ImageFont:
        for candidate in [font_path, self.settings.image_generation.bold_font_path, self.settings.image_generation.font_path]:
            try:
                if candidate:
                    return ImageFont.truetype(candidate, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _parse_size(self, raw: str, fallback: tuple[int, int]) -> tuple[int, int]:
        if "x" not in str(raw):
            return fallback
        left, right = str(raw).split("x", 1)
        try:
            return max(256, int(left)), max(256, int(right))
        except ValueError:
            return fallback

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _optimize_publish_image(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            original_size = path.stat().st_size
        except OSError:
            return
        if original_size <= self.COMPRESS_TRIGGER_BYTES:
            return

        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                if image.width > self.MAX_PUBLISH_IMAGE_WIDTH:
                    scale_ratio = self.MAX_PUBLISH_IMAGE_WIDTH / float(image.width)
                    resized_height = max(1, int(image.height * scale_ratio))
                    image = image.resize(
                        (self.MAX_PUBLISH_IMAGE_WIDTH, resized_height),
                        Image.Resampling.LANCZOS,
                    )
                quality_steps = [88, 82, 76, 70, 64, 58]
                for quality in quality_steps:
                    buffer = io.BytesIO()
                    image.save(
                        buffer,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                    )
                    if buffer.tell() <= self.MAX_PUBLISH_IMAGE_BYTES or quality == quality_steps[-1]:
                        path.write_bytes(buffer.getvalue())
                        self.logger.info(
                            "Optimized publish image: %s (%s KB -> %s KB, quality=%s)",
                            path,
                            round(original_size / 1024, 1),
                            round(path.stat().st_size / 1024, 1),
                            quality,
                        )
                        return
        except Exception as exc:
            self.logger.warning("Failed to optimize publish image %s: %s", path, exc)

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _now(self) -> str:
        from datetime import datetime

        return datetime.now().isoformat(timespec="seconds")

    def _hex_to_rgb(self, value: str) -> tuple[int, int, int]:
        raw = str(value or "").strip().lstrip("#")
        if len(raw) != 6:
            return (6, 11, 20)
        return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]

    def _rgba(self, value: str, alpha: float) -> tuple[int, int, int, int]:
        r, g, b = self._hex_to_rgb(value)
        return (r, g, b, max(0, min(255, int(alpha * 255))))

    def _record_fallback_event(
        self,
        *,
        mode: str,
        size: tuple[int, int],
        prompt: str,
        error: Exception,
    ) -> None:
        self._fallback_events.append(
            {
                "created_at": self._now(),
                "mode": mode,
                "size": f"{size[0]}x{size[1]}",
                "requested_backend": self.settings.image_generation.backend,
                "model": self.settings.image_generation.model_name,
                "prompt_excerpt": textwrap.shorten(prompt, width=180, placeholder="..."),
                "error": str(error).strip(),
            }
        )

    def _write_fallback_notification(self, *, run_dir: Path, result_path: Path) -> None:
        notifications_path = self.settings.automation_dir / "notifications.json"
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        existing = self._load_json(notifications_path)
        items: list[dict[str, Any]] = []
        if isinstance(existing, list):
            items = [item for item in existing if isinstance(item, dict)]
        elif isinstance(existing, dict):
            items = [existing]

        normalized_run_dir = str(run_dir.resolve())
        deduped_items: list[dict[str, Any]] = []
        for item in items:
            item_type = str(item.get("type", "")).strip()
            item_data = item.get("data", {})
            item_run_dir = ""
            if isinstance(item_data, dict):
                item_run_dir = str(item_data.get("run_dir", "")).strip()
            if item_type == "image_generation_fallback" and item_run_dir == normalized_run_dir:
                continue
            deduped_items.append(item)

        affected_modes = sorted(
            {str(item.get("mode", "")).strip() for item in self._fallback_events if str(item.get("mode", "")).strip()}
        )
        requested_backend = str(self.settings.image_generation.backend).strip() or "image2"
        message = (
            f"{requested_backend} 出图失败，已自动降级为本地 renderer。"
            "请检查图片接口、中转站配置或额度。"
        )
        if affected_modes:
            message += f" 受影响环节：{', '.join(affected_modes)}。"

        entry = {
            "created_at": self._now(),
            "level": "warn",
            "type": "image_generation_fallback",
            "title": "Image2 fallback triggered",
            "message": message,
            "data": {
                "account_key": self.settings.account_key,
                "run_dir": normalized_run_dir,
                "result_path": str(result_path.resolve()),
                "requested_backend": requested_backend,
                "successful_remote_backend": remote_backend,
                "fallback_backend": "renderer",
                "model": self.settings.image_generation.model_name,
                "affected_modes": affected_modes,
                "event_count": len(self._fallback_events),
                "fallback_events": list(self._fallback_events),
            },
        }
        payload = [entry, *deduped_items[:49]]
        notifications_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
