# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import platform
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from toutiao_content_factory.config import AppSettings


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


class ImageGenerationService:
    MAX_PUBLISH_IMAGE_BYTES = 1_500_000
    COMPRESS_TRIGGER_BYTES = 2_000_000
    MAX_PUBLISH_IMAGE_WIDTH = 1200

    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger

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
        try:
            generate_text_cover(title, str(cr))
        except Exception as e:
            self.logger.error("Cover failed: %s", e)
            raise
        if cr.exists():
            Image.open(cr).save(cp, format="PNG")
        ips = []
        if include_infographics:
            prompts = self._load_infographic_prompts(publish_dir)
            for idx, pt in enumerate(prompts[:3], start=1):
                rp = ri / ("infographic_" + str(idx) + ".png")
                pp = pi / ("infographic_" + str(idx) + ".png")
                try:
                    gen_infographic(pt, str(rp))
                    if rp.exists():
                        Image.open(rp).save(pp, format="PNG")
                    ips.append(rp)
                except Exception as e:
                    self.logger.warning("Infographic %d skipped: %s", idx, e)
        else:
            for d in (pi, ri):
                for f in sorted(d.glob("infographic_*.png")):
                    f.unlink(missing_ok=True)
        result = {
            "generated_at": self._now(),
            "backend": "pillow",
            "model": "local",
            "used_fallback": False,
            "cover_prompt": title,
            "infographic_prompts": [str(p) for p in ips],
            "artifacts": {"cover": str(cr), "infographics": [str(p) for p in ips]},
        }
        rp = publish_dir / "image_generation_result.json"
        rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        out = {"cover_image": cr, "image_generation_result": rp}
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
