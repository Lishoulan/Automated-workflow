# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
from io import BytesIO
import json
import logging
import os
import platform
import random
import re
import time
from urllib.request import urlopen
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.creative_scoring_service import CreativeScoringService


GRADIENT_PRESETS = [
    ["#0F2027", "#203A43", "#2C5364"],
    ["#232526", "#414345", "#232526"],
    ["#1A1A2E", "#16213E", "#0F3460"],
    ["#0B0C10", "#1F2833", "#45A29E"],
    ["#2C3E50", "#4CA1AF", "#2C3E50"],
]

BRAND = "小椰科技"

def hex_to_rgb(h):
    r = h.lstrip("#").strip()
    if len(r) != 6:
        return (128, 128, 128)
    return (int(r[0:2], 16), int(r[2:4], 16), int(r[4:6], 16))


def load_font(size):
    cand = []
    # 1. 项目内置字体目录
    sp = None
    try: sp = Path(str(__file__)).resolve().parent.parent.parent.parent / "static" / "fonts"
    except NameError: sp = None
    except AttributeError: sp = None
    if sp is not None and sp.exists():
        for f in sorted(sp.iterdir()):
            if f.suffix.lower() in (".ttf", ".ttc", ".otf"):
                cand.append(str(f))
    # 2. 根据操作系统提供候选字体路径
    system = platform.system()
    if system == "Windows":
        cand += [
            "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/deng.ttf",
        ]
    elif system == "Darwin":
        cand += [
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:  # Linux 及其他
        cand += [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ]
    for c in cand:
        if not c or not Path(c).exists():
            continue
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap_text(text, font, max_w, max_lines=5):
    lines = []
    for p in text.split("\n"):
        p = p.strip()
        if not p:
            continue
        cur = ""
        for ch in p:
            t = cur + ch
            bb = font.getbbox(t)
            if bb[2] - bb[0] > max_w and cur:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    lines[-1] = lines[-1].rstrip() + "..."
                    return lines
            else:
                cur = t
        if cur:
            lines.append(cur)
            if len(lines) >= max_lines:
                break
    return lines


def grad_bg(draw, size, colors):
    w, h = size
    rgbs = [hex_to_rgb(c) for c in colors]
    n = len(rgbs) - 1
    if n < 1:
        draw.rectangle((0, 0, w, h), fill=rgbs[0] if rgbs else (30, 30, 30))
        return
    for y in range(h):
        r = y / max(h - 1, 1)
        s = r * n
        i = min(int(s), n - 1)
        p = s - i
        a, b = rgbs[i], rgbs[i + 1]
        draw.line([(0, y), (w, y)], fill=(
            int(a[0] + (b[0] - a[0]) * p),
            int(a[1] + (b[1] - a[1]) * p),
            int(a[2] + (b[2] - a[2]) * p),
        ))


def shadow_text(draw, text, xy, font, offset=3, alpha=80):
    x, y = xy
    for dx in (-offset, 0, offset):
        for dy in (-offset, 0, offset):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, alpha), anchor="mm")


def generate_text_cover(title, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    W, H = 900, 383
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = random.choice(GRADIENT_PRESETS)
    grad_bg(draw, (W, H), colors)
    tf = load_font(56)
    wf = load_font(18)
    mx = 60
    mw = W - mx * 2
    lines = wrap_text(title, tf, mw, 4)
    lh_list = []
    total = 0
    for line in lines:
        bb = tf.getbbox(line)
        lh = bb[3] - bb[1]
        lh_list.append(lh)
        total += lh
    sp = 12
    bh = total + sp * (len(lines) - 1)
    sy = (H - bh) / 2
    for i, line in enumerate(lines):
        cx = W / 2
        cy = sy + sum(lh_list[:i]) + lh_list[i] / 2 + sp * i
        shadow_text(draw, line, (cx, cy), tf, offset=3, alpha=80)
        draw.text((cx, cy), line, font=tf, fill=(255, 255, 255, 255), anchor="mm")
    ly = H - 48
    dl = 60
    draw.line([(W / 2 - dl, ly), (W / 2 + dl, ly)], fill=(255, 255, 255, 100), width=1)
    wb = wf.getbbox(BRAND)
    wh = wb[3] - wb[1]
    draw.text((W / 2, ly + 10 + wh / 2), BRAND, font=wf, fill=(255, 255, 255, 140), anchor="mm")
    rgb = Image.new("RGB", img.size, (0, 0, 0))
    rgb.paste(img, mask=img.split()[3])
    if output.suffix.lower() in (".jpg", ".jpeg"):
        rgb.save(output, format="JPEG", quality=95)
    else:
        rgb.save(output, format="PNG")
    return str(output.resolve())


def gen_infographic(text, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    W, H = 800, 1000
    img = Image.new("RGBA", (W, H), (245, 247, 250, 255))
    draw = ImageDraw.Draw(img)
    cm = 30
    card = Image.new("RGBA", (W - cm*2, H - cm*2), (255, 255, 255, 255))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((0, 0, card.width-1, card.height-1), radius=16, fill=(255, 255, 255, 255))
    img.paste(card, (cm, cm))
    del card, cd
    tf = load_font(42)
    margin = 60
    mw = W - margin * 2
    lines = wrap_text(text, tf, mw, 8)
    sp = 14
    lh_list = []
    total = 0
    for line in lines:
        bb = tf.getbbox(line)
        lh = bb[3] - bb[1]
        lh_list.append(lh)
        total += lh
    bh = total + sp * (len(lines) - 1)
    sy = (H - bh) / 2
    for i, line in enumerate(lines):
        cx = W / 2
        cy = sy + sum(lh_list[:i]) + lh_list[i] / 2 + sp * i
        shadow_text(draw, line, (cx, cy), tf, offset=2, alpha=50)
        draw.text((cx, cy), line, font=tf, fill=(40, 45, 50, 255), anchor="mm")
    img.save(output, format="PNG")
    return str(output.resolve())


class ImageGenerationAPIUnavailable(RuntimeError):
    """Raised when the configured image API cannot produce a usable asset."""


def _format_api_error(exc: BaseException) -> str:
    """Return a short, redacted error chain suitable for run artifacts."""
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 3:
        seen.add(id(current))
        detail = str(current).strip().replace("\n", " ")
        detail = re.sub(
            r"(?i)(api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=<redacted>",
            detail,
        )
        detail = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer <redacted>", detail)
        detail = re.sub(
            r"(?i)([?&](?:api[_-]?key|token)=)[^&\s]+",
            r"\1<redacted>",
            detail,
        )
        if detail:
            parts.append(f"{current.__class__.__name__}: {detail[:400]}")
        else:
            parts.append(current.__class__.__name__)
        current = current.__cause__
    return " <- ".join(parts)[:1200]


class ImageGenerationService:
    MAX_PUBLISH_IMAGE_BYTES = 1_500_000
    COMPRESS_TRIGGER_BYTES = 2_000_000
    MAX_PUBLISH_IMAGE_WIDTH = 1200
    API_COVER_OUTPUT_SIZE = (1200, 675)
    API_BACKENDS = {"image2", "image-api", "api", "openai", "auto"}

    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger
        self._frontend_renderer = None

    def _get_frontend_renderer(self):
        """Lazy-init the frontend image renderer."""
        if self._frontend_renderer is not None:
            return self._frontend_renderer
        try:
            from toutiao_content_factory.services.frontend_image_renderer import FrontendImageRenderer
            self._frontend_renderer = FrontendImageRenderer(logger=self.logger)
            return self._frontend_renderer
        except Exception as exc:
            self.logger.debug("Frontend renderer init failed: %s", exc)
            return None

    def _api_backend_requested(self) -> bool:
        backend = str(getattr(self.settings.image_generation, "backend", "") or "")
        backend = backend.strip().lower().split(",", 1)[0].strip()
        return backend in self.API_BACKENDS or backend.startswith("gpt-image-")

    def _resolve_image_api_key(self) -> str:
        image_settings = self.settings.image_generation
        candidates = (
            getattr(image_settings, "model_api_key", ""),
            os.getenv("IMAGE_API_KEY", ""),
            "" if getattr(self.settings, "platform", "") == "wechat" else os.getenv("OPENAI_API_KEY", ""),
        )
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                return value
        return ""

    def _get_image_api_client(self):
        api_key = self._resolve_image_api_key()
        if not api_key:
            raise ImageGenerationAPIUnavailable(
                "IMAGE_API_KEY or OPENAI_API_KEY is not configured"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImageGenerationAPIUnavailable("openai SDK is not installed") from exc

        image_settings = self.settings.image_generation
        timeout = max(
            10.0,
            float(getattr(image_settings, "request_timeout_seconds", 180.0) or 180.0),
        )
        if getattr(self.settings, "platform", "") == "wechat":
            deadline = os.environ.get("WECHAT_RUN_DEADLINE_EPOCH")
            if deadline:
                # Keep two minutes for draft saving inside the shared run budget.
                remaining = float(deadline) - time.time() - 120
                if remaining < 10:
                    raise ImageGenerationAPIUnavailable("Insufficient cover-generation budget; manuscript retained")
                timeout = min(timeout, remaining)
        base_url = str(getattr(image_settings, "model_base_url", "") or "").strip()
        # Image credentials are independent from the content-model relay. An
        # empty IMAGE_BASE_URL therefore intentionally means the OpenAI API.
        kwargs = {
            "api_key": api_key,
            "base_url": base_url or "https://api.openai.com/v1",
            "timeout": timeout,
            "max_retries": 0,
        }
        try:
            return OpenAI(**kwargs)
        except Exception as exc:
            raise ImageGenerationAPIUnavailable(
                f"image API client initialization failed: {exc.__class__.__name__}"
            ) from exc

    @staticmethod
    def _response_value(item: Any, name: str) -> Any:
        if isinstance(item, dict):
            return item.get(name)
        return getattr(item, name, None)

    def _generate_api_image(self, prompt: str, output_path: Path, size: str) -> str:
        """Generate one image through the configured GPT Image API."""
        image_settings = self.settings.image_generation
        model = str(getattr(image_settings, "model_name", "gpt-image-2") or "gpt-image-2").strip()
        api_mode = str(getattr(image_settings, "api_mode", "images") or "images").strip().lower()
        if api_mode not in {"images", "image", "generations", "auto"}:
            raise ImageGenerationAPIUnavailable(
                f"unsupported image API mode: {api_mode or 'empty'}"
            )

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": str(size or "auto"),
            "quality": str(getattr(image_settings, "quality", "high") or "high").strip().lower(),
            "output_format": "png",
        }
        background = str(getattr(image_settings, "background", "opaque") or "opaque").strip().lower()
        if background in {"transparent", "opaque", "auto"}:
            payload["background"] = background
        if model != "gpt-image-2":
            style = str(getattr(image_settings, "style", "") or "").strip().lower()
            if style in {"vivid", "natural"}:
                payload["style"] = style

        client = self._get_image_api_client()
        try:
            response = client.images.generate(**payload)
        except Exception as exc:
            raise ImageGenerationAPIUnavailable(
                f"image API request failed: {exc.__class__.__name__}"
            ) from exc

        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response.get("data")
        if not data:
            raise ImageGenerationAPIUnavailable("image API returned no image data")
        item = data[0]
        b64_json = self._response_value(item, "b64_json")
        raw: bytes
        if b64_json:
            encoded = str(b64_json)
            if encoded.startswith("data:") and "," in encoded:
                encoded = encoded.split(",", 1)[1]
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ImageGenerationAPIUnavailable("image API returned invalid base64 data") from exc
        else:
            image_url = self._response_value(item, "url")
            if not image_url:
                raise ImageGenerationAPIUnavailable("image API response has no b64_json or url")
            try:
                timeout = max(
                    10.0,
                    float(getattr(image_settings, "request_timeout_seconds", 180.0) or 180.0),
                )
                with urlopen(str(image_url), timeout=timeout) as response_stream:
                    raw = response_stream.read()
            except Exception as exc:
                raise ImageGenerationAPIUnavailable(
                    f"image API image download failed: {exc.__class__.__name__}"
                ) from exc

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(BytesIO(raw)) as image:
                image.load()
                image.convert("RGBA").save(output, format="PNG", optimize=True)
        except Exception as exc:
            raise ImageGenerationAPIUnavailable(
                f"image API returned an unreadable image: {exc.__class__.__name__}"
            ) from exc
        return str(output.resolve())

    @classmethod
    def _compose_api_cover(cls, source_path: Path, title: str, output_path: Path) -> str:
        """Crop the AI visual to the publish canvas and typeset the exact title locally."""
        target_width, target_height = cls.API_COVER_OUTPUT_SIZE
        with Image.open(source_path) as source:
            base = ImageOps.fit(
                source.convert("RGB"),
                (target_width, target_height),
                method=Image.Resampling.LANCZOS,
                centering=(0.58, 0.5),
            ).convert("RGBA")

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        for x in range(target_width):
            progress = min(1.0, x / 760.0)
            alpha = int(205 * (1.0 - progress) ** 1.35)
            overlay_draw.line((x, 0, x, target_height), fill=(4, 10, 18, alpha))
        base = Image.alpha_composite(base, overlay)

        draw = ImageDraw.Draw(base)
        title_font = load_font(58)
        lines = wrap_text(title, title_font, 570, max_lines=3) or [title]
        line_heights = [title_font.getbbox(line)[3] - title_font.getbbox(line)[1] for line in lines]
        spacing = 12
        block_height = sum(line_heights) + spacing * max(0, len(lines) - 1)
        start_y = max(165, (target_height - block_height) / 2)
        draw.line((72, 92, 176, 92), fill=(52, 218, 255, 235), width=4)
        current_y = start_y
        for line, line_height in zip(lines, line_heights):
            draw.text(
                (72, current_y),
                line,
                font=title_font,
                fill=(248, 251, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0, 150),
            )
            current_y += line_height + spacing

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        base.convert("RGB").save(output, format="PNG", optimize=True)
        return str(output.resolve())

    def generate_for_run(self, run_dir, include_infographics=True):
        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError("publish package not found: " + str(publish_dir))
        pm = self._load_json(publish_dir / "publish_meta.json")
        title = str(pm.get("selected_title", "")).strip() or "科技行业观察"
        self.logger.info("Generating cover: %s", title[:40])
        ri = run_dir / "images"
        pi = publish_dir / "images"
        ri.mkdir(parents=True, exist_ok=True)
        pi.mkdir(parents=True, exist_ok=True)
        cr = ri / "cover.png"
        cp = pi / "cover.png"

        wechat_cover = getattr(self.settings, "platform", "") == "wechat"
        cover_prompt = (self._build_wechat_cover_prompt(title)
                        if wechat_cover else self._build_api_cover_prompt(title))
        cover_backend = ""
        api_attempted = False
        api_error_type = ""
        api_error_detail = ""
        api_cover_source = ri / "cover_ai.png"
        api_cover_source.unlink(missing_ok=True)
        renderer = None
        infographic_backends: set[str] = set()

        if self._api_backend_requested():
            api_attempted = True
            try:
                self._generate_api_image(
                    cover_prompt,
                    api_cover_source,
                    str(getattr(self.settings.image_generation, "cover_size", "auto") or "auto"),
                )
                if wechat_cover:
                    # The model owns the editorial layout; retain its typography
                    # instead of replacing it with the Toutiao dark title overlay.
                    with Image.open(api_cover_source) as source:
                        ImageOps.fit(source.convert("RGB"), (1200, 511),
                            method=Image.Resampling.LANCZOS).save(cr, format="PNG", optimize=True)
                else:
                    self._compose_api_cover(api_cover_source, title, cr)
                cover_backend = "image2"
                self.logger.info("Cover generated via image API (%s)", getattr(self.settings.image_generation, "model_name", "gpt-image-2"))
            except Exception as exc:
                api_error_type = exc.__class__.__name__
                api_error_detail = _format_api_error(exc)
                api_cover_source.unlink(missing_ok=True)
                self.logger.warning(
                    "GPT image cover failed (%s): %s; local fallback enabled=%s",
                    api_error_type,
                    api_error_detail,
                    bool(getattr(self.settings.image_generation, "fallback_to_renderer", True)),
                )
                if not bool(getattr(self.settings.image_generation, "fallback_to_renderer", True)):
                    (publish_dir / "image_generation_result.json").write_text(json.dumps({
                        "generated_at": self._now(), "api_attempted": True, "api_succeeded": False,
                        "status": "failed", "api_error_type": api_error_type,
                        "api_error_detail": api_error_detail, "cover_prompt": cover_prompt,
                        "cover_style": "warm_editorial" if wechat_cover else "dark_tech",
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    raise

        if not cover_backend:
            renderer = self._get_frontend_renderer()
            if renderer is not None:
                try:
                    result_path = renderer.render_cover(title, str(cr))
                    if result_path and cr.exists():
                        cover_backend = "frontend"
                        self.logger.info("Cover generated via frontend renderer")
                except Exception as exc:
                    self.logger.warning("Frontend cover failed, falling back to Pillow: %s", exc)

        if not cover_backend:
            try:
                generate_text_cover(title, str(cr))
                cover_backend = "pillow"
            except Exception as e:
                self.logger.error("Cover failed: %s", e)
                raise

        if cr.exists():
            with Image.open(cr) as cover_image:
                cover_image.save(cp, format="PNG", optimize=True)
        ips = []
        publish_infographic_paths = []
        infographic_specs = []
        successful_infographic_specs = []
        if include_infographics:
            infographic_specs = self._build_infographic_specs(
                run_dir=run_dir,
                publish_dir=publish_dir,
                publish_meta=pm,
                title=title,
            )
            max_assets = max(0, min(3, int(getattr(self.settings.image_generation, "max_generated_assets", 2) or 2)))
            for idx, spec in enumerate(infographic_specs[:max_assets], start=1):
                pt = str(spec.get("prompt", "")).strip() or title
                rp = ri / ("infographic_" + str(idx) + ".png")
                pp = pi / ("infographic_" + str(idx) + ".png")
                # Statistical inserts stay deterministic so exact values and
                # source notes cannot be altered by an image model.
                infographic_frontend = False
                if renderer is None:
                    renderer = self._get_frontend_renderer()
                if renderer is not None:
                    try:
                        result_path = renderer.render_infographic(
                            str(spec.get("title", title)),
                            str(rp),
                            eyebrow=str(spec.get("eyebrow", "关键数据")),
                            metrics=list(spec.get("metrics", [])),
                            section_title=str(spec.get("section_title", "核心要点")),
                            items=list(spec.get("items", [])),
                            chart=dict(spec.get("chart") or {}),
                            source_note=str(spec.get("source_note", "")),
                            period_label=str(spec.get("period_label", "")),
                            notes=list(spec.get("notes", [])),
                        )
                        if result_path and rp.exists():
                            infographic_frontend = True
                            infographic_backends.add("frontend")
                    except Exception as e:
                        self.logger.debug("Frontend infographic %d failed: %s", idx, e)
                if not infographic_frontend:
                    try:
                        fallback_text = "\n".join(
                            [
                                str(spec.get("title", title)),
                                *[str(item) for item in spec.get("items", [])[:6]],
                            ]
                        )
                        gen_infographic(fallback_text or pt, str(rp))
                        infographic_backends.add("pillow")
                    except Exception as e:
                        self.logger.warning("Infographic %d skipped: %s", idx, e)
                        continue
                if rp.exists():
                    Image.open(rp).save(pp, format="PNG")
                    ips.append(rp)
                    publish_infographic_paths.append(pp)
                    successful_infographic_specs.append(spec)
        else:
            for d in (pi, ri):
                for f in sorted(d.glob("infographic_*.png")):
                    f.unlink(missing_ok=True)
        if not infographic_backends:
            infographic_backend = "none"
        elif len(infographic_backends) == 1:
            infographic_backend = next(iter(infographic_backends))
        else:
            infographic_backend = "mixed"
        result = {
            "generated_at": self._now(),
            "backend": cover_backend,
            "cover_backend": cover_backend,
            "infographic_backend": infographic_backend,
            "model": str(getattr(self.settings.image_generation, "model_name", "gpt-image-2")) if cover_backend == "image2" else "local",
            "used_fallback": (api_attempted and cover_backend != "image2") or cover_backend == "pillow",
            "api_attempted": api_attempted,
            "api_succeeded": cover_backend == "image2",
            "api_error_type": api_error_type,
            "api_error_detail": api_error_detail,
            "cover_prompt": cover_prompt,
            "cover_style": "warm_editorial" if wechat_cover else "dark_tech",
            "infographic_prompts": [
                str(spec.get("prompt", ""))
                for spec in successful_infographic_specs
                if str(spec.get("prompt", "")).strip()
            ],
            "artifacts": {
                "cover": str(cr),
                "cover_ai_source": str(api_cover_source) if api_cover_source.exists() else "",
                "infographics": [str(p) for p in ips],
            },
            "publish_artifacts": {
                "cover": str(cp),
                "infographics": [str(p) for p in publish_infographic_paths],
            },
            "inline_ready": bool(publish_infographic_paths),
            "infographic_specs": successful_infographic_specs,
        }
        selected_topic = pm.get("selected_topic") if isinstance(pm.get("selected_topic"), dict) else {}
        cover_variant = str(selected_topic.get("cover_variant", "")).strip()
        style_key = str(pm.get("style_key", "")).strip()
        try:
            content_domain = str(self.settings.get_style(style_key).content_domain)
        except (KeyError, AttributeError):
            content_domain = str(getattr(self.settings, "content_domain", "industry"))
        title_score_payload = pm.get("creative_score") if isinstance(pm.get("creative_score"), dict) else {}
        scorer = CreativeScoringService(
            self.settings.output_dir / "creative_score_calibration.json", wechat_mode=self.settings.wechat_auto_publish_mode
        )
        if not title_score_payload:
            title_score_payload = scorer.score_title(
                title,
                cover_variant=cover_variant,
                content_domain=content_domain,
            )
        cover_score_payload = scorer.score_cover(
            cr,
            title_score=int(title_score_payload.get("score", 0) or 0),
            title=title,
            cover_variant=cover_variant,
            content_domain=content_domain,
        )
        creative_score_payload = {
            "generated_at": self._now(),
            "title": title_score_payload,
            "cover": cover_score_payload,
            "combined_score": cover_score_payload["combined_score"],
        }
        creative_score_path = publish_dir / "title_cover_score.json"
        creative_score_path.write_text(
            json.dumps(creative_score_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["creative_score"] = creative_score_payload
        rp = publish_dir / "image_generation_result.json"
        rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        out = {
            "cover_image": cr,
            "image_generation_result": rp,
            "title_cover_score": creative_score_path,
        }
        for idx, p in enumerate(ips, start=1):
            out["infographic_" + str(idx)] = p
        return out

    @staticmethod
    def _load_json(path):
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_infographic_prompts(self, publish_dir):
        pf = publish_dir / "infographic_prompts.txt"
        if pf.exists():
            txt = pf.read_text(encoding="utf-8").strip()
            if txt:
                return [line.strip() for line in txt.split("\n") if line.strip()]
        pm = self._load_json(publish_dir / "publish_meta.json")
        pts = pm.get("infographic_prompts", [])
        if isinstance(pts, list):
            return [str(p).strip() for p in pts if str(p).strip()]
        return []

    def _build_infographic_specs(
        self,
        *,
        run_dir: Path,
        publish_dir: Path,
        publish_meta: dict[str, Any],
        title: str,
    ) -> list[dict[str, Any]]:
        """Build small, evidence-backed visual briefs for article inserts.

        The visual layer must not invent metrics. It first reuses explicit
        prompts when a caller supplied them, then derives labels and points
        from the persisted workflow data and the final publish article.
        """
        workflow = self._load_json(run_dir / "workflow_result.json")
        raw_data_report = workflow.get("data_report", {}) if isinstance(workflow, dict) else {}
        data_report = raw_data_report if isinstance(raw_data_report, dict) else {}
        article = self._read_text(publish_dir / "article.md")
        topic = str((publish_meta.get("selected_topic") or {}).get("title", "")).strip()

        core_data = self._text_list(data_report.get("core_data"))
        evidence_notes = self._text_list(data_report.get("evidence_notes"))
        growth_numbers = self._text_list(data_report.get("growth_numbers"))
        fact_items = self._unique_visual_items(
            [
                *core_data,
                *evidence_notes,
                *self._text_list(data_report.get("key_points")),
                *self._article_paragraphs(article),
                str(publish_meta.get("summary", "")),
            ],
            limit=5,
        )
        tracking_items = self._unique_visual_items(
            [
                *self._text_list(data_report.get("watch_metrics")),
                *self._text_list(data_report.get("key_points")),
            ],
            limit=4,
        )
        if not tracking_items:
            tracking_items = self._unique_visual_items(
                [
                    *self._text_list(data_report.get("risk_flags")),
                    *self._article_paragraphs(article)[-3:],
                ],
                limit=4,
            ) or fact_items
        evidence_values = [
            *core_data,
            *evidence_notes,
            *growth_numbers,
            article,
        ]
        metrics = self._extract_visual_metrics(evidence_values)
        chart = self._build_statistical_chart(evidence_values)
        source_note = self._source_note(data_report, publish_meta)
        period_label = self._period_label(evidence_values)
        notes = self._text_list(data_report.get("risk_flags"))[:2]
        evidence_strength = data_report.get("evidence_strength")
        if not metrics and str(evidence_strength).strip().isdigit() and int(evidence_strength) > 0:
            metrics = [{"label": "证据强度", "value": str(int(evidence_strength)), "unit": "/100"}]

        explicit_prompts = self._load_infographic_prompts(publish_dir)
        if explicit_prompts:
            specs: list[dict[str, Any]] = []
            for index, prompt in enumerate(explicit_prompts[:3], start=1):
                specs.append(
                    {
                        "title": self._clip_visual_text(title, 42),
                        "eyebrow": "关键数据" if index == 1 else "跟踪框架",
                        "section_title": "文章中的事实锚点" if index == 1 else "后续观察重点",
                        "metrics": metrics if index == 1 else [],
                        "items": fact_items if index == 1 else tracking_items,
                        "chart": chart if index == 1 else {},
                        "source_note": source_note,
                        "period_label": period_label,
                        "notes": notes,
                        "prompt": self._build_infographic_prompt(
                            title=self._clip_visual_text(title, 42),
                            topic_label=topic or title,
                            purpose="事实数据统计图" if index == 1 else "后续观察框架",
                            base_prompt=prompt,
                            chart=chart if index == 1 else {},
                            metrics=metrics if index == 1 else [],
                            items=fact_items if index == 1 else tracking_items,
                            source_note=source_note,
                            period_label=period_label,
                        ),
                    }
                )
            return specs

        topic_label = topic or title
        return [
            {
                "title": self._clip_visual_text(topic_label, 42),
                "eyebrow": "关键数据",
                "section_title": "文章中的事实锚点",
                "metrics": metrics,
                "items": fact_items,
                "chart": chart,
                "source_note": source_note,
                "period_label": period_label,
                "notes": notes,
                "prompt": self._build_infographic_prompt(
                    title=self._clip_visual_text(topic_label, 42),
                    topic_label=topic_label,
                    purpose="事实数据统计图",
                    chart=chart,
                    metrics=metrics,
                    items=fact_items,
                    source_note=source_note,
                    period_label=period_label,
                ),
            },
            {
                "title": self._clip_visual_text(topic_label, 42),
                "eyebrow": "跟踪框架",
                "section_title": "后续观察重点",
                "metrics": [],
                "items": tracking_items,
                "chart": {},
                "source_note": source_note,
                "period_label": period_label,
                "notes": notes,
                "prompt": self._build_infographic_prompt(
                    title=self._clip_visual_text(topic_label, 42),
                    topic_label=topic_label,
                    purpose="后续观察框架",
                    chart={},
                    metrics=[],
                    items=tracking_items,
                    source_note=source_note,
                    period_label=period_label,
                ),
            },
        ]

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _article_paragraphs(article: str) -> list[str]:
        raw_blocks = re.split(r"\n\s*\n", str(article or ""))
        paragraphs = []
        for block in raw_blocks:
            cleaned = re.sub(r"^\s*#+\s*", "", block.strip())
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                paragraphs.append(cleaned)
        return paragraphs

    @classmethod
    def _unique_visual_items(cls, values: list[str], limit: int) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(value or ""))
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if len(cleaned) < 8:
                continue
            cleaned = cls._clip_visual_text(cleaned, 96)
            key = re.sub(r"\W+", "", cleaned).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(cleaned)
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _clip_visual_text(text: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(value) <= limit:
            return value
        return value[: max(1, limit - 3)].rstrip() + "..."

    @classmethod
    def _extract_visual_metrics(cls, values: list[str], limit: int = 3) -> list[dict[str, str]]:
        """Extract labelled, source-derived metrics without inventing values."""
        metric_pattern = re.compile(
            r"(?P<value>[+-]?\d+(?:\.\d+)?(?:\s*[-~至—]\s*[+-]?\d+(?:\.\d+)?)?)\s*"
            r"(?P<unit>万亿元|亿元|万元|千亿|万亿|万台|万辆|万件|GWh|MWh|kWh|GW|MW|km/h|"
            r"个月|小时|分钟|天|项|台|辆|件|%|％|亿|万)",
            re.IGNORECASE,
        )
        metrics: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for source in values:
            text = str(source or "")
            for match in metric_pattern.finditer(text):
                value = re.sub(r"\s+", "", match.group("value"))
                unit = match.group("unit").replace("％", "%")
                key = (value, unit.lower())
                if key in seen:
                    continue
                metrics.append(
                    {
                        "label": cls._metric_label(text, match.start()),
                        "value": value,
                        "unit": unit,
                    }
                )
                seen.add(key)
                if len(metrics) >= max(0, limit):
                    return metrics
        return metrics

    @classmethod
    def _metric_label(cls, text: str, start: int) -> str:
        context = re.sub(r"\s+", "", text[max(0, start - 34):start])
        context = re.split(r"[。；;\n]", context)[-1]
        context = re.split(r"[，,]", context)[-1]
        context = re.sub(r"^.*?(?:公告|报告|披露)[：:]", "", context)
        context = re.sub(r"\d{4}年(?:上半年|下半年|第[一二三四]季度)", "", context)
        context = re.sub(r"[+-]?\d+(?:\.\d+)?(?:[-~至—][+-]?\d+(?:\.\d+)?)?"
                         r"(?:万亿元|亿元|万元|千亿|万亿|万台|万辆|万件|GWh|MWh|kWh|GW|MW|km/h|"
                         r"个月|小时|分钟|天|项|台|辆|件|%|％|亿|万)", "", context, flags=re.IGNORECASE)
        context = context.strip("，,：:；;、()（）")
        context = re.sub(r"(?:由|从|约|约为|达到|为)$", "", context).strip()
        if not context:
            return "数据锚点"
        return cls._clip_visual_text(context[-18:], 18)

    @classmethod
    def _build_statistical_chart(
        cls,
        values: list[str],
    ) -> dict[str, Any]:
        """Return a chart only when the source contains a defensible comparison."""
        text = "\n".join(str(value or "") for value in values)
        share_chart = cls._extract_share_chart(text)
        comparison_chart = cls._extract_percentage_comparison(text)
        if share_chart:
            if comparison_chart:
                share_chart["secondary_chart"] = comparison_chart
            return share_chart
        if comparison_chart:
            return comparison_chart

        # A generic bar chart is intentionally omitted when the extracted
        # metrics have different units or represent different concepts.
        return {}

    @classmethod
    def _extract_share_chart(cls, text: str) -> dict[str, Any] | None:
        percentage_pattern = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|％)")
        number_pattern = (
            r"[+-]?\d+(?:\.\d+)?(?:[-~至—][+-]?\d+(?:\.\d+)?)?\s*"
            r"(?:万亿元|亿元|万元|千亿|万亿|万台|万辆|万件|GWh|MWh|kWh|GW|MW|km/h|"
            r"个月|小时|分钟|天|项|台|辆|件|%|％|亿|万)"
        )
        for match in percentage_pattern.finditer(text):
            sentence_start = max(
                text.rfind("。", 0, match.start()),
                text.rfind("；", 0, match.start()),
                text.rfind(";", 0, match.start()),
                text.rfind("\n", 0, match.start()),
            ) + 1
            sentence_end_candidates = [
                position for position in (
                    text.find("。", match.end()),
                    text.find("；", match.end()),
                    text.find(";", match.end()),
                    text.find("\n", match.end()),
                )
                if position >= 0
            ]
            sentence_end = min(sentence_end_candidates, default=len(text))
            window = text[sentence_start:sentence_end]
            marker = re.search(r"占总收入|占收入|收入占比|占比|渗透率|比例", window)
            if not marker:
                continue
            value = float(match.group("value"))
            if not 0 <= value <= 100:
                continue
            sentence = text[sentence_start:match.start()]
            sentence = re.sub(r"^.*?(?:公告|报告|披露)[：:]", "", sentence)
            sentence = re.sub(r"(?:占总收入|占收入|收入占比|占比|渗透率|比例).*?$", "", sentence)
            sentence = re.sub(number_pattern, "", sentence, flags=re.IGNORECASE)
            sentence = sentence.strip("，,：:；;、 ")
            if "渗透率" in marker.group(0):
                label = cls._clip_visual_text((sentence + "渗透率") if sentence else "市场渗透率", 32)
                reference = "市场总量"
                note = "来源材料中的渗透率，不代表预测值"
            elif "比例" in marker.group(0) and "占" not in marker.group(0):
                label = cls._clip_visual_text((sentence + "比例") if sentence else "结构比例", 32)
                reference = "整体"
                note = "来源材料中的结构比例"
            else:
                label = cls._clip_visual_text((sentence + "占比") if sentence else "业务收入占比", 32)
                reference = "总收入"
                note = "公司披露的业务收入占比，不代表行业份额"
            return {
                "type": "share",
                "label": label,
                "value": value,
                "display_value": f"{match.group('value')}%",
                "reference": reference,
                "note": note,
            }
        return None

    @classmethod
    def _extract_percentage_comparison(cls, text: str) -> dict[str, Any] | None:
        comparison_pattern = re.compile(
            r"(?P<previous>[+-]?\d+(?:\.\d+)?)\s*%\s*"
            r"(?P<connector>升至|提高至|增至|上升至|降至|下降至|降为|变为|调整至)\s*"
            r"(?P<current>[+-]?\d+(?:\.\d+)?)\s*%"
        )
        for match in comparison_pattern.finditer(text):
            previous = float(match.group("previous"))
            current = float(match.group("current"))
            context = text[max(0, match.start() - 48):match.start()]
            context = re.split(r"[。；;\n]", context)[-1]
            context = re.split(r"[，,]", context)[-1]
            context = re.sub(r"^.*?(?:公告|报告|披露)[：:]", "", context)
            context = re.sub(r"(?:由|从)\s*$", "", context).strip("，,：: ")
            label = cls._clip_visual_text(context or "指标变化", 22)
            maximum = max(abs(previous), abs(current), 1.0)
            direction = "上升" if current >= previous else "下降"
            return {
                "type": "comparison",
                "label": label,
                "unit": "%",
                "entries": [
                    {
                        "label": "上年同期",
                        "value": previous,
                        "display_value": f"{match.group('previous')}%",
                        "width_pct": round(abs(previous) / maximum * 100, 1),
                    },
                    {
                        "label": "本期",
                        "value": current,
                        "display_value": f"{match.group('current')}%",
                        "width_pct": round(abs(current) / maximum * 100, 1),
                    },
                ],
                "note": f"同一指标的同期对比，材料显示为{direction}",
            }
        return None

    def _source_note(self, data_report: dict[str, Any], publish_meta: dict[str, Any]) -> str:
        notes = self._text_list(data_report.get("evidence_notes"))
        for note in notes:
            if any(token in note for token in ("来源", "公告", "报告", "数据")):
                return self._clip_visual_text(note, 86)
        summaries = data_report.get("source_summaries")
        if isinstance(summaries, list):
            for summary in summaries:
                if isinstance(summary, dict):
                    title = str(summary.get("title", "")).strip()
                    if title:
                        return self._clip_visual_text(f"来源：{title}", 86)
        style = str(publish_meta.get("style_label", "")).strip()
        return f"数据来自本次文章已保存的核验材料{f' · {style}' if style else ''}"

    @staticmethod
    def _period_label(values: list[str]) -> str:
        text = "\n".join(str(value or "") for value in values)
        patterns = [
            r"截至\d{4}年\d{1,2}月\d{1,2}日(?:止[^，。；\n]*)?",
            r"\d{4}年(?:上半年|下半年|第[一二三四]季度)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)[:34]
        return "以来源材料披露期为准"

    @classmethod
    def _build_infographic_prompt(
        cls,
        *,
        title: str,
        topic_label: str,
        purpose: str,
        chart: dict[str, Any],
        metrics: list[dict[str, str]],
        items: list[str],
        source_note: str,
        period_label: str,
        base_prompt: str = "",
    ) -> str:
        metric_lines = "\n".join(
            f"- {metric.get('label', '数据锚点')}: {metric.get('value', '')}{metric.get('unit', '')}"
            for metric in metrics[:5]
        ) or "- 没有足够的同口径数值，只展示事实摘录，不补造统计量"
        fact_lines = "\n".join(f"- {cls._clip_visual_text(item, 88)}" for item in items[:5])
        if chart.get("type") == "share":
            chart_line = (
                f"主图为单一占比进度条：{chart.get('label', '业务收入')} "
                f"{chart.get('display_value', '')}，参照对象为{chart.get('reference', '总量')}。"
            )
            secondary = chart.get("secondary_chart") or {}
            if secondary:
                chart_line += f"下方增加同口径前后对比：{secondary.get('label', '指标变化')}。"
        elif chart.get("type") == "comparison":
            chart_line = (
                f"主图为同一指标的横向条形对比：{chart.get('label', '指标变化')}，"
                "只显示来源材料给出的上年同期与本期。"
            )
        else:
            chart_line = "没有可靠的同口径比较数据，不绘制柱状图、折线图或饼图，只做事实卡片与摘录。"
        base_line = f"原始视觉方向：{base_prompt.strip()}\n" if base_prompt.strip() else ""
        return (
            "Use case: infographic-diagram\n"
            "Asset type: 今日头条移动端正文数据图，竖版 1024x1536\n"
            f"Primary request: 为“{topic_label}”制作{purpose}，标题为“{title}”。\n"
            "Style/medium: 高级数据新闻编辑风，简洁的平面信息图，留白充足，"
            "清晰网格，深墨色文字配青绿色和珊瑚色强调，适合手机阅读。\n"
            f"Data period: {period_label}\n"
            f"Chart direction: {chart_line}\n"
            "Verified metrics (use only these exact values):\n"
            f"{metric_lines}\n"
            "Evidence excerpts (use as short labels or footnotes, do not rewrite into new facts):\n"
            f"{fact_lines or '- 无可用事实摘录'}\n"
            f"Source note (verbatim): “{source_note}”\n"
            "Composition: top title and period, a strong data hierarchy, then the main chart, "
            "two or three compact evidence callouts, and a small source footnote at the bottom.\n"
            "Typography: readable Simplified Chinese sans-serif, exact numeric labels, no tiny text, "
            "left-aligned body copy, consistent units and decimal precision.\n"
            "Data integrity: use only the listed values and wording; do not estimate, interpolate, "
            "round, extrapolate to the industry, or invent missing dates, companies, clients, orders, "
            "market share, rankings, forecasts, or trend lines. Do not compare values with different units.\n"
            "Constraints: render the title, labels, numbers, units, period, and source note accurately; "
            "show that company-level shares are not industry shares; preserve a clear reading order.\n"
            "Avoid: fake dashboard widgets, decorative 3D, photorealistic stock imagery, neon overload, "
            "dense paragraphs, unreadable legends, excessive gradients, watermarks, logos, and extra text.\n"
            f"{base_line}"
        ).strip()

    @staticmethod
    def _build_cover_prompt(title: str) -> str:
        return (
            "Use case: productivity-visual\n"
            "Asset type: 今日头条科技商业文章 16:9 封面\n"
            f"Primary request: 为文章“{title}”制作高级科技媒体封面。\n"
            "Style/medium: 编辑部数据新闻风，克制、现代、专业；用抽象数据网格和主题相关的几何视觉隐喻，"
            "不要把封面做成普通渐变海报。\n"
            "Composition: 移动端可读的强标题层级，主体视觉简洁，保留足够留白，标题区域清楚。\n"
            f"Text (verbatim): “{title}”\n"
            "Constraints: 只使用给定标题，不添加数字、公司、排名、口号或未经证实的事实；中文清晰；无水印、无额外文字。"
        )

    @staticmethod
    def _build_wechat_cover_prompt(title: str) -> str:
        return (
            "Use case: photorealistic-natural\n"
            "Asset type: 微信公众号横版封面，宽高比2.35:1，适合手机缩略图。\n"
            "Primary request: 设计高品质中文商业杂志封面，主题由下方标题决定。\n"
            "Style/medium: 暖象牙白纸质背景，炭灰文字和克制朱红点缀，细腻纹理、自然光、"
            "真实材料、柔和阴影、有层次的摄影或精致编辑插画。\n"
            "Scene/backdrop: 根据文章主题选择可信的实体场景；汽车题用无品牌汽车、展厅，"
            "只有出口题才出现港口货轮；机器人题用合理机械结构与工厂；"
            "能源题用电池或储能设备。不得所有主题都画成汽车港口。\n"
            "Composition/framing: 左侧醒目大标题，右侧主题主体，有留白；标题可分两至四行，"
            "朱红强调关键词，主标题和主体离边缘至少8%，兼顾微信宽图和方形缩略图。\n"
            f"Text (verbatim): {json.dumps(str(title), ensure_ascii=False)}\n"
            "Constraints: 唯一允许的文字是上述标题，必须逐字准确，不换标题、不添加副标题、"
            "数字、数据图表、日期、口号或未经核验的事实。保留数字对应的公司、品牌和统计周期，"
            "不暗示不同口径之间的因果关系。文字必须清晰、端正、可读。"
            "无品牌标识、无水印、无虚假车型或真实人物肖像。\n"
            "Avoid: 蓝底文字卡片、模板式数据卡、廉价渐变、密集小字、霓虹科技光效、"
            "花哨仪表盘、虚构公司logo、变形机械。"
        )

    @staticmethod
    def _build_api_cover_prompt(title: str) -> str:
        topic = str(title or "technology industry analysis").strip()
        lowered = topic.casefold()
        if any(keyword in lowered for keyword in ("robot", "robotics", "机器人", "人形")):
            subject = (
                "a physically plausible industrial humanoid robot with visible actuators and gripper detail "
                "working near a modern assembly line"
            )
        elif any(keyword in lowered for keyword in ("energy", "battery", "storage", "能源", "电池", "储能")):
            subject = (
                "clean battery modules and a credible grid-scale energy storage installation in a modern facility"
            )
        else:
            subject = "one credible industrial technology subject that visually represents the article topic"
        return (
            "Use case: photorealistic-natural\n"
            "Asset type: premium Chinese technology and business article cover\n"
            f"Primary request: create a credible editorial cover for the article topic \"{topic}\".\n"
            f"Scene/backdrop: a clean modern industrial environment; subject: {subject}.\n"
            "Style/medium: high-end business journalism and product photography, realistic materials, crisp engineering detail.\n"
            "Composition/framing: wide landscape cover; keep the main subject on the right half and leave generous uncluttered negative space on the left for a locally typeset headline.\n"
            "Lighting/mood: controlled studio-documentary lighting, calm, analytical, premium, with subtle depth and no visual noise.\n"
            "Color palette: dark neutral background, steel blue and restrained cyan accents, with a small amount of warm practical light.\n"
            "Text (verbatim): \"\". Do not render any text, letters, numbers, logos, labels, or watermark in the generated image.\n"
            "Constraints: the local application will add the exact article title after generation; keep the left side readable and avoid invented company branding or specific claims.\n"
            "Avoid: fantasy sci-fi, floating hologram overload, generic gradient poster, stock-photo cliches, clutter, distorted machinery, extra objects, illegible text, logos, and watermark."
        )

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _now():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class UnsplashFallback:
    """Fetch high-quality stock photos from Unsplash API as cover image fallback.
    
    Uses the free Unsplash API. Register at https://unsplash.com/developers
    Set UNSPLASH_ACCESS_KEY in .env for authenticated access (higher rate limits).
    Without a key, uses the public search endpoint (limited to ~50 req/hour).
    """
    
    UNSPLASH_API = "https://api.unsplash.com"
    PUBLIC_SEARCH = "https://source.unsplash.com"
    
    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger
        self.access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    
    def fetch_cover(self, title: str, output_path: str, target_size=(900, 383)) -> str | None:
        """Fetch a relevant photo from Unsplash, crop to cover size, save to output_path."""
        keywords = self._extract_keywords(title)
        if not keywords:
            return None
        
        image_url = self._search_photo(keywords)
        if not image_url:
            return None
        
        try:
            import requests
            from PIL import Image
            from io import BytesIO
            
            resp = requests.get(image_url, timeout=15)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content))
            
            # Crop to target aspect ratio (center crop)
            target_w, target_h = target_size
            src_w, src_h = img.size
            src_ratio = src_w / src_h
            target_ratio = target_w / target_h
            
            if src_ratio > target_ratio:
                # Source is wider - crop width
                new_w = int(src_h * target_ratio)
                offset = (src_w - new_w) // 2
                img = img.crop((offset, 0, offset + new_w, src_h))
            else:
                # Source is taller - crop height
                new_h = int(src_w / target_ratio)
                offset = (src_h - new_h) // 2
                img = img.crop((0, offset, src_w, offset + new_h))
            
            img = img.resize(target_size, Image.LANCZOS)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            img.save(output, format="PNG", quality=95)
            self.logger.info("Unsplash cover saved: %s (from %s)", output_path, image_url)
            return str(output.resolve())
        except Exception as exc:
            self.logger.warning("Unsplash fetch failed: %s", exc)
            return None
    
    def _extract_keywords(self, title: str) -> list[str]:
        """Extract English keywords from a Chinese/English title."""
        import re
        
        # English words only
        eng_words = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", title)
        # Known tech keywords to try
        known = ["robot", "AI", "technology", "energy", "chip", "battery", 
                 "factory", "automation", "data", "future", "innovation",
                 "industry", "machine", "digital", "solar", "electric"]
        
        candidates = [w.lower() for w in eng_words if w.lower() in known]
        if candidates:
            return candidates[:3]
        
        # Fallback: use known tech keywords based on title content
        cn_keywords = {
            "机器人": "robot", "人形": "humanoid robot", "AI": "artificial intelligence",
            "储能": "energy storage", "电池": "battery", "光伏": "solar panel",
            "芯片": "microchip", "新能源": "renewable energy", "自动驾驶": "autonomous car",
            "数据": "big data", "算力": "computing", "华为": "technology",
        }
        for cn, en in cn_keywords.items():
            if cn in title:
                return [en]
        
        return ["technology"]  # Generic fallback
    
    def _search_photo(self, keywords: list[str]) -> str | None:
        """Search Unsplash for a photo matching keywords."""
        import random
        query = ",".join(keywords[:2])
        
        try:
            import requests
            
            if self.access_key:
                url = f"{self.UNSPLASH_API}/search/photos"
                headers = {"Authorization": f"Client-ID {self.access_key}"}
                params = {"query": query, "per_page": 5, "orientation": "landscape"}
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                resp.raise_for_status()
                results = resp.json().get("results", [])
                if results:
                    choice = random.choice(results)
                    raw_url = choice.get("urls", {}).get("raw", "")
                    if raw_url:
                        return f"{raw_url}&w=1200"
            else:
                # Use public source.unsplash.com
                return f"{self.PUBLIC_SEARCH}/900x383/?{query}"
        except Exception as exc:
            self.logger.debug("Unsplash search failed: %s", exc)
        
        return None


# Monkey-patch ImageGenerationService to try Unsplash first, then fall back to Pillow
_original_generate = ImageGenerationService.generate_for_run

def _patched_generate(self, run_dir, include_infographics=True):
    """Try Unsplash cover first, fall back to Pillow renderer."""
    result = _original_generate(self, run_dir, include_infographics=include_infographics)
    
    # If Pillow already generated a cover, that's fine
    if result.get("cover_image") and Path(str(result["cover_image"])).exists():
        return result
    
    # Try Unsplash fallback
    publish_dir = run_dir / self.settings.review.publish_dirname
    pm = self._load_json(publish_dir / "publish_meta.json")
    title = str(pm.get("selected_title", "")).strip() or "Technology"
    
    fallback = UnsplashFallback(self.settings, self.logger)
    cover_path = str(run_dir / "images" / "cover.png")
    
    unsplash_result = fallback.fetch_cover(title, cover_path)
    if unsplash_result:
        pi = publish_dir / "images"
        pi.mkdir(parents=True, exist_ok=True)
        from PIL import Image as PILImage
        PILImage.open(cover_path).save(str(pi / "cover.png"), format="PNG")
        result["cover_image"] = Path(cover_path)
        result["backend"] = "unsplash_fallback"
        self.logger.info("Unsplash fallback cover used for: %s", title)
    
    return result

ImageGenerationService.generate_for_run = _patched_generate
