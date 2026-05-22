from __future__ import annotations

import base64
import io
import json
import logging
import math
import textwrap
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps

from toutiao_content_factory.config import AppSettings

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    OpenAI = None


class ImageGenerationService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.client = None
        self._fallback_events: list[dict[str, Any]] = []

        image_settings = settings.image_generation
        if image_settings.backend != "renderer" and image_settings.model_api_key and OpenAI is not None:
            client_kwargs: dict[str, Any] = {"api_key": image_settings.model_api_key}
            if image_settings.model_base_url:
                client_kwargs["base_url"] = image_settings.model_base_url
            self.client = OpenAI(**client_kwargs)

    def generate_for_run(self, run_dir: Path) -> dict[str, Path]:
        self._fallback_events = []
        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        title = str(publish_meta.get("selected_title", "")).strip() or "科技行业观察"
        summary = str(publish_meta.get("summary", "")).strip()

        cover_prompt = self._read_text(publish_dir / "cover_prompt.txt").strip() or str(
            publish_meta.get("cover_prompt", "")
        ).strip()
        infographic_prompts = self._load_infographic_prompts(
            publish_dir / "infographic_prompts.txt",
            fallback=publish_meta.get("infographic_prompts", []),
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

        infographic_paths: list[Path] = []
        max_infographics = max(1, int(self.settings.image_generation.max_generated_assets))
        for index, prompt in enumerate(infographic_prompts[:max_infographics], start=1):
            infographic = self._generate_infographic_image(prompt)
            run_path = run_images_dir / f"infographic_{index}.png"
            publish_path = publish_images_dir / f"infographic_{index}.png"
            infographic.save(run_path, format="PNG")
            infographic.save(publish_path, format="PNG")
            infographic_paths.append(run_path)

        result_payload = {
            "generated_at": self._now(),
            "backend": self.settings.image_generation.backend,
            "model": self.settings.image_generation.model_name,
            "used_fallback": bool(self._fallback_events),
            "fallback_events": list(self._fallback_events),
            "cover_prompt": cover_prompt,
            "infographic_prompts": infographic_prompts[:max_infographics],
            "artifacts": {
                "cover": str(cover_run_path),
                "infographics": [str(path) for path in infographic_paths],
            },
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
        background_prompt = (
            f"{prompt} "
            "Do not render any text, letters, watermarks, logos, UI panels or captions. "
            "Leave clean negative space for later Chinese title overlay. "
            "High-end editorial cover, strong lighting contrast, precise composition."
        ).strip()
        background = self._generate_or_fallback(prompt=background_prompt, size=size, mode="cover")
        return self._compose_cover(background, title=title, summary=summary, size=size)

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
        if self.client is not None:
            try:
                return self._generate_via_api(prompt=prompt, size=size)
            except Exception as exc:
                self.logger.warning("Image API generation failed for %s, falling back: %s", mode, exc)
                if not self.settings.image_generation.fallback_to_renderer:
                    raise
                self._record_fallback_event(mode=mode, size=size, prompt=prompt, error=exc)
        elif str(self.settings.image_generation.backend).strip().lower() != "renderer":
            message = "Image API client is unavailable. Check IMAGE_BACKEND / IMAGE_API_KEY / IMAGE_BASE_URL."
            self.logger.warning("Image API client unavailable for %s, falling back: %s", mode, message)
            if not self.settings.image_generation.fallback_to_renderer:
                raise RuntimeError(message)
            self._record_fallback_event(mode=mode, size=size, prompt=prompt, error=RuntimeError(message))
        return self._render_fallback(prompt=prompt, size=size, mode=mode)

    def _generate_via_api(self, *, prompt: str, size: tuple[int, int]) -> Image.Image:
        size_text = f"{size[0]}x{size[1]}"
        kwargs: dict[str, Any] = {
            "model": self.settings.image_generation.model_name,
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

        try:
            response = self.client.images.generate(**kwargs)
        except Exception:
            kwargs.pop("response_format", None)
            response = self.client.images.generate(**kwargs)

        data = response.data[0]
        if getattr(data, "b64_json", None):
            raw = base64.b64decode(data.b64_json)
            return Image.open(io.BytesIO(raw)).convert("RGB")
        if getattr(data, "url", None):
            request = Request(str(data.url), headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=90) as remote:
                return Image.open(io.BytesIO(remote.read())).convert("RGB")
        raise RuntimeError("Image API returned neither b64_json nor url.")

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

    def _render_fallback(self, *, prompt: str, size: tuple[int, int], mode: str) -> Image.Image:
        image = Image.new("RGB", size, self._hex_to_rgb(self.settings.image_generation.background_color))
        draw = ImageDraw.Draw(image)
        self._draw_gradient_background(image)

        label_font = self._load_font(self.settings.image_generation.font_path, 30)
        title_font = self._load_font(self.settings.image_generation.bold_font_path, 64 if mode == "cover" else 52)
        body_font = self._load_font(self.settings.image_generation.font_path, 28)

        label = "AI 配图生成降级"
        draw.rounded_rectangle((64, 60, 280, 108), radius=18, fill=self._rgba("#26D9FF", 0.92))
        draw.text((82, 70), label, font=label_font, fill=(3, 10, 18))

        lines = self._wrap_text(draw, prompt[:220], title_font, max_width=size[0] - 140, max_lines=6)
        y = 170
        for line in lines:
            draw.text((70, y), line, font=title_font, fill=(245, 248, 255))
            y += self._line_height(title_font, 1.14)

        note = "当前为本地降级图，可继续重试 image2 获取更高质量画面。"
        for line in self._wrap_text(draw, note, body_font, max_width=size[0] - 140, max_lines=3):
            draw.text((70, y + 32), line, font=body_font, fill=(164, 180, 202))
            y += self._line_height(body_font, 1.2)

        return image

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
                "fallback_backend": "renderer",
                "model": self.settings.image_generation.model_name,
                "affected_modes": affected_modes,
                "event_count": len(self._fallback_events),
                "fallback_events": list(self._fallback_events),
            },
        }
        payload = [entry, *deduped_items[:49]]
        notifications_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
