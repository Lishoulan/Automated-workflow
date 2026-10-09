"""One bounded Cloudflare Workers AI call for the WeChat cover fallback."""
from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time

import httpx
from PIL import Image, ImageDraw, ImageOps, ImageStat

MODEL = "@cf/black-forest-labs/flux-1-schnell"


def generate_cloud_cover(title: str, output: Path) -> str:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_AI_TOKEN", "").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token:
        raise RuntimeError("Cloudflare free image fallback needs Account ID and API Token")
    timeout = 90.0
    deadline = os.environ.get("WECHAT_RUN_DEADLINE_EPOCH")
    if deadline:
        timeout = min(timeout, float(deadline) - time.time() - 120)
    if timeout < 10:
        raise RuntimeError("Insufficient cloud-cover budget; manuscript retained")
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = output.with_name("cover_free_ai.png")
    raw.unlink(missing_ok=True)
    # FLUX renders the illustration; deterministic typesetting preserves every
    # character and number in the selected title without inventing subtitles.
    prompt = (
        "Premium business magazine conceptual illustration, not a documentary photo. "
        "Illustrate this article topic: " + json.dumps(title, ensure_ascii=False) + ". "
        "A single credible physical subject relevant to the topic, elegant materials, "
        "soft natural light, warm ivory, charcoal and restrained vermilion palette. "
        "Keep the subject centered with space around it, clean editorial composition. "
        "No identifiable brands or people. No text, letters, numbers, logos, "
        "watermarks, signs, charts, neon effects or unrelated decorative icons."
    )
    if any(word in title for word in ("汽车", "奇瑞", "星途", "车企")):
        prompt += " Show an unbranded dark car with a restrained export-port setting."
    if len(prompt) > 2048:
        raise RuntimeError("Cover prompt exceeds the cloud model input limit")
    output.with_name("cover_free_prompt.txt").write_text(prompt, encoding="utf-8")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
    # No SDK retry, redirect, billing upgrade, or automatic top-up. Free-plan
    # quota exhaustion is an error and keeps the manuscript for a later window.
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                      follow_redirects=False) as client:
        response = client.post(url, headers={"Authorization": "Bearer " + token},
                               json={"prompt": prompt, "steps": 8})
    if response.status_code != 200:
        raise RuntimeError(f"Cloudflare image request failed (HTTP {response.status_code})")
    payload = response.json()
    if payload.get("success") is not True:
        raise RuntimeError("Cloudflare did not confirm image generation success")
    encoded = (payload.get("result") or {}).get("image")
    if not isinstance(encoded, str) or len(encoded) > 28_000_000:
        raise RuntimeError("Cloudflare returned an invalid image payload")
    data = base64.b64decode(encoded, validate=True)
    with Image.open(BytesIO(data)) as image:
        if min(image.size) < 256 or max(ImageStat.Stat(image.convert("RGB")).stddev) < 2:
            raise RuntimeError("Cloudflare returned a blank or undersized image")
        image.convert("RGB").save(raw, "PNG")
        artwork = ImageOps.fit(image.convert("RGB"), (600, 511), method=Image.Resampling.LANCZOS)
    from toutiao_content_factory.services.image_generation_service import load_font

    canvas = Image.new("RGBA", (1200, 511), (248, 243, 233, 255))
    canvas.paste(artwork, (600, 0))
    overlay = Image.new("RGBA", canvas.size)
    overlay_draw = ImageDraw.Draw(overlay)
    for x in range(600, 750):
        overlay_draw.line((x, 0, x, 511), fill=(248, 243, 233, int(255 * (750 - x) / 150)))
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    for size in range(60, 27, -2):
        font = load_font(size)
        if bytes(font.getmask("奇")) == bytes(font.getmask("瑞")):
            raise RuntimeError("A Chinese font is required for cloud cover typesetting")
        lines = [""]
        for char in title:
            if lines[-1] and font.getlength(lines[-1] + char) > 610:
                lines.append(char)
            else:
                lines[-1] += char
        if len(lines) <= 4:
            break
    if len(lines) > 4:
        raise RuntimeError("Cover title is too long to typeset without truncation")
    draw.line((58, 64, 152, 64), fill=(158, 27, 23), width=4)
    y = max(100, (511 - (size + 18) * len(lines)) // 2)
    for index, line in enumerate(lines):
        draw.text((58, y), line, font=font, fill=(158, 27, 23) if index == 0 else (35, 35, 35))
        y += size + 18
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return str(output)
