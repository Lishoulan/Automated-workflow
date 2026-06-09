# -*- coding: utf-8 -*-
"""Frontend image renderer — generates cover and infographic images via HTML + Playwright screenshot.

Replaces the Pillow-based renderer with high-quality frontend templates.
Falls back to Pillow if Playwright is unavailable.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "images"
_BRAND = "手蓝机器人笔记"


class FrontendImageRenderer:
    """Render cover images and infographics using HTML templates + Playwright screenshot."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self._browser = None

    def _get_browser(self):
        """Lazy-init Playwright browser (use system Edge/Chrome, or download Chromium)."""
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            # Try system Edge first, then Chrome, then default Chromium
            for channel in ["msedge", "chrome", None]:
                try:
                    kwargs = {"channel": channel} if channel else {}
                    self._browser = pw.chromium.launch(**kwargs)
                    self._pw = pw
                    self.logger.info("FrontendImageRenderer: launched browser (channel=%s)", channel or "chromium")
                    return self._browser
                except Exception:
                    continue
            self.logger.warning("FrontendImageRenderer: no browser available")
            pw.stop()
            return None
        except Exception as exc:
            self.logger.warning("FrontendImageRenderer: Playwright unavailable (%s), will use Pillow fallback", exc)
            return None

    def _screenshot(self, html: str, output_path: str, width: int, height: int) -> str | None:
        """Take a screenshot of HTML content at exact pixel dimensions."""
        browser = self._get_browser()
        if browser is None:
            return None
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="networkidle")
            page.wait_for_timeout(500)  # let fonts load
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=False, type="png")
            page.close()
            return str(out.resolve())
        except Exception as exc:
            self.logger.warning("FrontendImageRenderer screenshot failed: %s", exc)
            return None

    # ---- Cover image ----

    def render_cover(
        self,
        title: str,
        output_path: str,
        *,
        eyebrow: str = "深度分析",
        subtitle: str = "",
        brand: str = _BRAND,
    ) -> str | None:
        """Render a cover image (1200x675) from HTML template."""
        template = self._env.get_template("cover.html")
        html = template.render(
            eyebrow=eyebrow,
            title=title,
            subtitle=subtitle or self._auto_subtitle(title),
            brand=brand,
            date=datetime.now().strftime("%Y.%m.%d"),
        )
        return self._screenshot(html, output_path, width=1200, height=675)

    # ---- Infographic ----

    def render_infographic(
        self,
        title: str,
        output_path: str,
        *,
        eyebrow: str = "关键数据",
        metrics: list[dict] | None = None,
        section_title: str = "核心要点",
        items: list[str] | None = None,
        brand: str = _BRAND,
    ) -> str | None:
        """Render an infographic (800x1200) from HTML template."""
        if metrics is None:
            metrics = self._auto_metrics(title)
        if items is None:
            items = self._auto_items(title)
        template = self._env.get_template("infographic.html")
        html = template.render(
            eyebrow=eyebrow,
            title=title,
            metrics=metrics,
            section_title=section_title,
            items=items,
            brand=brand,
            date=datetime.now().strftime("%Y.%m.%d"),
        )
        return self._screenshot(html, output_path, width=800, height=1200)

    # ---- Auto-extract helpers ----

    @staticmethod
    def _auto_subtitle(title: str) -> str:
        """Generate a subtitle from the title."""
        if len(title) > 20:
            return title[:30] + "..."
        return "科技前沿 · 深度解读"

    @staticmethod
    def _auto_metrics(title: str) -> list[dict]:
        """Extract metric-like data from title/content."""
        # Try to find numbers with units in the text
        patterns = [
            (r"(\d+(?:\.\d+)?)\s*万亿", "市场规模"),
            (r"(\d+(?:\.\d+)?)\s*千亿", "产业规模"),
            (r"(\d+(?:\.\d+)?)\s*%", "增长率"),
            (r"(\d+(?:\.\d+)?)\s*亿", "投资额"),
            (r"(\d+(?:\.\d+)?)\s*万", "数量级"),
            (r"(\d+(?:\.\d+)?)\s*km/h", "速度"),
            (r"\$(\d+(?:\.\d+)?)\s*万", "价格"),
        ]
        metrics = []
        for pattern, label in patterns:
            m = re.search(pattern, title)
            if m:
                unit = pattern.split(r")")[-1].strip() if r")" in pattern else ""
                metrics.append({"label": label, "value": m.group(1), "unit": unit})
                if len(metrics) >= 3:
                    break
        if not metrics:
            metrics = [
                {"label": "行业热度", "value": "A+", "unit": ""},
                {"label": "关注指数", "value": "TOP", "unit": ""},
                {"label": "趋势评级", "value": "↑", "unit": ""},
            ]
        return metrics

    @staticmethod
    def _auto_items(text: str) -> list[str]:
        """Split text into list items."""
        # Split by sentence-ending punctuation
        parts = re.split(r"[。；！？\n]", text)
        items = [p.strip() for p in parts if len(p.strip()) > 6]
        if not items:
            items = [text[:60] + "..." if len(text) > 60 else text]
        return items[:6]

    # ---- Cleanup ----

    def close(self) -> None:
        """Close the Playwright browser."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if hasattr(self, "_pw"):
            try:
                self._pw.stop()
            except Exception:
                pass
