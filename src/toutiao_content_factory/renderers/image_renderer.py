from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from toutiao_content_factory.config import ImageGenerationSettings
from toutiao_content_factory.models import ImageArtifacts, VisualBrief


class ImageRenderer:
    def __init__(self, settings: ImageGenerationSettings) -> None:
        self.settings = settings
        self.title_font = self._load_font(settings.bold_font_path, 66)
        self.subtitle_font = self._load_font(settings.font_path, 28)
        self.body_font = self._load_font(settings.font_path, 24)
        self.label_font = self._load_font(settings.font_path, 20)
        self.metric_font = self._load_font(settings.bold_font_path, 42)
        self.small_font = self._load_font(settings.font_path, 18)

    def render(self, brief: VisualBrief, image_dir: Path) -> ImageArtifacts:
        image_dir.mkdir(parents=True, exist_ok=True)
        cover_png = image_dir / "cover.png"
        infographic_1 = image_dir / "infographic_1.png"
        infographic_2 = image_dir / "infographic_2.png"
        cover_svg = image_dir / "cover.svg"
        infographic_1_svg = image_dir / "infographic_1.svg"
        infographic_2_svg = image_dir / "infographic_2.svg"
        max_assets = max(1, min(self.settings.max_generated_assets, 3))

        self._render_cover(brief, cover_png)
        infographic_pngs: list[Path] = []
        infographic_svgs: list[Path] = []

        self._write_svg_stub(brief, cover_svg, mode="cover")
        if max_assets >= 2:
            self._render_infographic_metrics(brief, infographic_1)
            self._write_svg_stub(brief, infographic_1_svg, mode="metrics")
            infographic_pngs.append(infographic_1)
            infographic_svgs.append(infographic_1_svg)
        else:
            self._remove_if_exists(infographic_1)
            self._remove_if_exists(infographic_1_svg)

        if max_assets >= 3:
            self._render_infographic_story(brief, infographic_2)
            self._write_svg_stub(brief, infographic_2_svg, mode="story")
            infographic_pngs.append(infographic_2)
            infographic_svgs.append(infographic_2_svg)
        else:
            self._remove_if_exists(infographic_2)
            self._remove_if_exists(infographic_2_svg)

        return ImageArtifacts(
            cover_png=cover_png,
            infographic_pngs=infographic_pngs,
            cover_svg=cover_svg,
            infographic_svgs=infographic_svgs,
        )

    def _remove_if_exists(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _render_cover(self, brief: VisualBrief, path: Path) -> None:
        width, height = self.settings.width, self.settings.height
        image = Image.new("RGB", (width, height), self.settings.background_color)
        draw = ImageDraw.Draw(image)

        self._draw_background(draw, width, height)
        self._draw_data_grid(draw, width, height)

        self._draw_text_block(
            draw,
            86,
            92,
            brief.cover_title,
            brief.cover_subtitle,
            summary=brief.summary,
            tags=brief.chain_nodes[:4],
        )
        self._draw_metric_cards(draw, brief, start_x=780, start_y=92, card_width=400)
        self._draw_footer(draw, width, height, left=f"风格 / {brief.style_name}", right="AI 内容工厂 · Cover")

        image.save(path)

    def _render_infographic_metrics(self, brief: VisualBrief, path: Path) -> None:
        width, height = self.settings.mobile_width, self.settings.mobile_height
        image = Image.new("RGB", (width, height), self.settings.background_color)
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, width, height)

        self._draw_headline(draw, 72, 74, "一图看懂", brief.article_title)
        self._draw_metric_panel(draw, brief, 72, 238, width - 144, 620)
        self._draw_chain_panel(draw, brief, 72, 900, width - 144, 380)
        self._draw_score_panel(draw, brief, 72, 1320, width - 144, 420)
        self._draw_footer(draw, width, height, left="科技数据图谱", right="AI 内容工厂 · Infographic 1")
        image.save(path)

    def _render_infographic_story(self, brief: VisualBrief, path: Path) -> None:
        width, height = self.settings.mobile_width, self.settings.mobile_height
        image = Image.new("RGB", (width, height), self.settings.background_color)
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, width, height)

        self._draw_headline(draw, 72, 74, "趋势图谱", brief.cover_title)
        self._draw_timeline_panel(draw, brief, 72, 230, width - 144, 560)
        self._draw_topic_panel(draw, brief, 72, 840, width - 144, 540)
        self._draw_highlight_banner(draw, brief, 72, 1430, width - 144, 260)
        self._draw_footer(draw, width, height, left="移动端适配 · 深色科技风", right="AI 内容工厂 · Infographic 2")
        image.save(path)

    def _draw_background(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        for y in range(height):
            ratio = y / max(height - 1, 1)
            color = self._mix_color(self.settings.primary_color, self.settings.background_color, ratio * 0.72)
            draw.line([(0, y), (width, y)], fill=color)

        accent = self._hex_rgba(self.settings.accent_color, 62)
        draw.ellipse((width - 340, -120, width + 180, 280), fill=accent)
        draw.ellipse((-200, height - 420, 280, height + 80), fill=self._hex_rgba("#7B7EFF", 48))

    def _draw_data_grid(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        line_color = self._hex_rgba("#CFE5FF", 18)
        for x in range(60, width, 70):
            draw.line([(x, 0), (x, height)], fill=line_color, width=1)
        for y in range(60, height, 70):
            draw.line([(0, y), (width, y)], fill=line_color, width=1)

    def _draw_text_block(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        title: str,
        subtitle: str,
        summary: str,
        tags: list[str],
    ) -> None:
        draw.text((x, y), "TECH SIGNAL", font=self.label_font, fill=self.settings.accent_color)
        title_lines = self._wrap_lines(title, self.title_font, max_width=620, max_lines=3)
        current_y = y + 48
        for line in title_lines:
            draw.text((x, current_y), line, font=self.title_font, fill=self.settings.text_color)
            current_y += 82

        subtitle_lines = self._wrap_lines(subtitle, self.subtitle_font, max_width=600, max_lines=3)
        current_y += 18
        for line in subtitle_lines:
            draw.text((x, current_y), line, font=self.subtitle_font, fill=self.settings.muted_text_color)
            current_y += 42

        current_y += 10
        summary_lines = self._wrap_lines(summary, self.body_font, max_width=590, max_lines=4)
        for line in summary_lines:
            draw.text((x, current_y), line, font=self.body_font, fill=self.settings.text_color)
            current_y += 34

        self._draw_tags(draw, x, current_y + 26, tags)

    def _draw_metric_cards(
        self,
        draw: ImageDraw.ImageDraw,
        brief: VisualBrief,
        start_x: int,
        start_y: int,
        card_width: int,
    ) -> None:
        metrics = brief.key_metrics[:4]
        for index, metric in enumerate(metrics):
            y = start_y + index * 138
            self._draw_card(draw, start_x, y, card_width, 118)
            draw.text((start_x + 28, y + 24), metric.label, font=self.label_font, fill=self.settings.muted_text_color)
            value_text = f"{metric.value}{metric.unit}"
            draw.text((start_x + 28, y + 56), value_text, font=self.metric_font, fill=self.settings.accent_color)
            if metric.note:
                draw.text((start_x + 210, y + 68), metric.note, font=self.small_font, fill=self.settings.text_color)

    def _draw_metric_panel(self, draw: ImageDraw.ImageDraw, brief: VisualBrief, x: int, y: int, w: int, h: int) -> None:
        self._draw_card(draw, x, y, w, h)
        draw.text((x + 32, y + 28), "核心增长数据", font=self.subtitle_font, fill=self.settings.text_color)
        bar_x = x + 48
        bar_y = y + 120
        chart_w = w - 96
        chart_h = 330
        max_value = max([metric.value for metric in brief.key_metrics] or [100])
        metrics = brief.key_metrics[:4]
        gap = 36
        column_w = (chart_w - gap * (len(metrics) - 1)) / max(len(metrics), 1)
        for index, metric in enumerate(metrics):
            left = bar_x + index * (column_w + gap)
            ratio = metric.value / max_value if max_value else 0
            top = bar_y + chart_h - ratio * (chart_h - 34)
            draw.rounded_rectangle((left, top, left + column_w, bar_y + chart_h), radius=18, fill=self.settings.accent_color)
            draw.text((left, bar_y + chart_h + 20), metric.label[:8], font=self.small_font, fill=self.settings.muted_text_color)
            draw.text((left, top - 32), f"{metric.value}{metric.unit}", font=self.small_font, fill=self.settings.text_color)

        trend_y = y + 500
        draw.text((x + 32, trend_y), "趋势观察", font=self.subtitle_font, fill=self.settings.text_color)
        self._draw_bullet_lines(draw, x + 36, trend_y + 52, brief.trend_labels[:5], line_gap=52)

    def _draw_chain_panel(self, draw: ImageDraw.ImageDraw, brief: VisualBrief, x: int, y: int, w: int, h: int) -> None:
        self._draw_card(draw, x, y, w, h)
        draw.text((x + 32, y + 28), "产业链结构", font=self.subtitle_font, fill=self.settings.text_color)
        nodes = brief.chain_nodes[:5]
        node_width = (w - 64 - 20 * (len(nodes) - 1)) / max(len(nodes), 1)
        center_y = y + 180
        for index, node in enumerate(nodes):
            left = x + 32 + index * (node_width + 20)
            top = center_y - 52
            draw.rounded_rectangle((left, top, left + node_width, top + 104), radius=18, fill=self.settings.secondary_color)
            draw.text((left + 18, top + 28), node, font=self.body_font, fill=self.settings.text_color)
            if index < len(nodes) - 1:
                mid_x = left + node_width
                draw.line((mid_x + 6, center_y, mid_x + 18, center_y), fill=self.settings.accent_color, width=4)
                draw.polygon(
                    [(mid_x + 18, center_y - 6), (mid_x + 18, center_y + 6), (mid_x + 30, center_y)],
                    fill=self.settings.accent_color,
                )

        self._draw_tags(draw, x + 32, y + 290, brief.topic_highlights[:6])

    def _draw_score_panel(self, draw: ImageDraw.ImageDraw, brief: VisualBrief, x: int, y: int, w: int, h: int) -> None:
        self._draw_card(draw, x, y, w, h)
        draw.text((x + 32, y + 28), "内容评分", font=self.subtitle_font, fill=self.settings.text_color)
        scores = list(brief.score_snapshot.items())
        row_y = y + 110
        for label, value in scores:
            draw.text((x + 36, row_y), label, font=self.body_font, fill=self.settings.muted_text_color)
            draw.rounded_rectangle((x + 260, row_y + 6, x + w - 36, row_y + 30), radius=12, fill=self._hex_rgba("#FFFFFF", 24))
            fill_w = (w - 332) * min(max(value, 0), 100) / 100
            draw.rounded_rectangle((x + 260, row_y + 6, x + 260 + fill_w, row_y + 30), radius=12, fill=self.settings.accent_color)
            draw.text((x + w - 110, row_y - 8), str(value), font=self.metric_font, fill=self.settings.text_color)
            row_y += 64

    def _draw_timeline_panel(self, draw: ImageDraw.ImageDraw, brief: VisualBrief, x: int, y: int, w: int, h: int) -> None:
        self._draw_card(draw, x, y, w, h)
        draw.text((x + 32, y + 28), "从热度到兑现", font=self.subtitle_font, fill=self.settings.text_color)
        line_x = x + 84
        draw.line((line_x, y + 120, line_x, y + h - 60), fill=self._hex_rgba(self.settings.accent_color, 180), width=6)
        current_y = y + 142
        for point in brief.timeline_points[:5]:
            draw.ellipse((line_x - 14, current_y - 6, line_x + 14, current_y + 22), fill=self.settings.accent_color)
            wrapped = self._wrap_lines(point, self.body_font, max_width=w - 170, max_lines=3)
            box_h = 30 + len(wrapped) * 34
            self._draw_card(draw, x + 130, current_y - 18, w - 176, box_h, fill=self.settings.secondary_color)
            inner_y = current_y
            for line in wrapped:
                draw.text((x + 156, inner_y), line, font=self.body_font, fill=self.settings.text_color)
                inner_y += 34
            current_y += box_h + 26

    def _draw_topic_panel(self, draw: ImageDraw.ImageDraw, brief: VisualBrief, x: int, y: int, w: int, h: int) -> None:
        self._draw_card(draw, x, y, w, h)
        draw.text((x + 32, y + 28), "今日热点高光", font=self.subtitle_font, fill=self.settings.text_color)
        current_y = y + 90
        for index, topic in enumerate(brief.topic_highlights[:4], start=1):
            self._draw_card(draw, x + 28, current_y, w - 56, 94, fill=self._mix_color(self.settings.card_color, self.settings.secondary_color, 0.38))
            draw.text((x + 56, current_y + 22), f"{index:02d}", font=self.metric_font, fill=self.settings.accent_color)
            wrapped = self._wrap_lines(topic, self.body_font, max_width=w - 170, max_lines=2)
            text_y = current_y + 24
            for line in wrapped:
                draw.text((x + 132, text_y), line, font=self.body_font, fill=self.settings.text_color)
                text_y += 32
            current_y += 110

    def _draw_highlight_banner(self, draw: ImageDraw.ImageDraw, brief: VisualBrief, x: int, y: int, w: int, h: int) -> None:
        self._draw_card(draw, x, y, w, h, fill=self._mix_color(self.settings.card_color, self.settings.accent_color, 0.12))
        draw.text((x + 32, y + 28), "封面判断", font=self.subtitle_font, fill=self.settings.accent_color)
        lines = self._wrap_lines(brief.cover_subtitle, self.subtitle_font, max_width=w - 64, max_lines=4)
        current_y = y + 88
        for line in lines:
            draw.text((x + 32, current_y), line, font=self.subtitle_font, fill=self.settings.text_color)
            current_y += 40

    def _draw_headline(self, draw: ImageDraw.ImageDraw, x: int, y: int, eyebrow: str, title: str) -> None:
        draw.text((x, y), eyebrow, font=self.label_font, fill=self.settings.accent_color)
        current_y = y + 42
        for line in self._wrap_lines(title, self.title_font, max_width=self.settings.mobile_width - 160, max_lines=3):
            draw.text((x, current_y), line, font=self.title_font, fill=self.settings.text_color)
            current_y += 78

    def _draw_bullet_lines(self, draw: ImageDraw.ImageDraw, x: int, y: int, lines: list[str], line_gap: int) -> None:
        current_y = y
        for text in lines:
            draw.ellipse((x, current_y + 6, x + 10, current_y + 16), fill=self.settings.accent_color)
            wrapped = self._wrap_lines(text, self.body_font, max_width=820, max_lines=2)
            inner_y = current_y - 2
            for line in wrapped:
                draw.text((x + 24, inner_y), line, font=self.body_font, fill=self.settings.text_color)
                inner_y += 30
            current_y += line_gap

    def _draw_tags(self, draw: ImageDraw.ImageDraw, x: int, y: int, tags: list[str]) -> None:
        current_x = x
        current_y = y
        for tag in tags:
            width = self.label_font.getlength(tag) + 36
            if current_x + width > self.settings.width - 90 and self.settings.width > 1200:
                current_x = x
                current_y += 44
            draw.rounded_rectangle(
                (current_x, current_y, current_x + width, current_y + 32),
                radius=16,
                fill=self._hex_rgba("#FFFFFF", 18),
                outline=self._hex_rgba(self.settings.accent_color, 80),
            )
            draw.text((current_x + 18, current_y + 5), tag, font=self.label_font, fill=self.settings.text_color)
            current_x += width + 12

    def _draw_footer(self, draw: ImageDraw.ImageDraw, width: int, height: int, left: str, right: str) -> None:
        draw.text((56, height - 40), left, font=self.small_font, fill=self.settings.muted_text_color)
        right_width = self.small_font.getlength(right)
        draw.text((width - right_width - 56, height - 40), right, font=self.small_font, fill=self.settings.muted_text_color)

    def _draw_card(
        self,
        draw: ImageDraw.ImageDraw,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str | None = None,
    ) -> None:
        fill_color = fill or self.settings.card_color
        draw.rounded_rectangle(
            (x, y, x + w, y + h),
            radius=28,
            fill=fill_color,
            outline=self._hex_rgba("#D4E9FF", 26),
            width=2,
        )

    def _wrap_lines(self, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> list[str]:
        text = text.strip()
        if not text:
            return []
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            if font.getlength(candidate) <= max_width:
                current = candidate
                continue
            lines.append(current)
            current = char
            if len(lines) >= max_lines - 1:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines and "".join(lines) != text:
            lines[-1] = lines[-1].rstrip("，,。 ") + "…"
        return lines

    def _load_font(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    def _write_svg_stub(self, brief: VisualBrief, path: Path, mode: str) -> None:
        width = self.settings.width if mode == "cover" else self.settings.mobile_width
        height = self.settings.height if mode == "cover" else self.settings.mobile_height
        title = brief.cover_title if mode == "cover" else brief.article_title
        subtitle = brief.cover_subtitle if mode == "cover" else brief.summary
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0%" stop-color="{self.settings.primary_color}"/>
      <stop offset="100%" stop-color="{self.settings.background_color}"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <circle cx="{width - 120}" cy="90" r="160" fill="{self.settings.accent_color}" fill-opacity="0.15"/>
  <rect x="48" y="48" rx="28" ry="28" width="{width - 96}" height="{height - 96}" fill="{self.settings.card_color}" fill-opacity="0.74" stroke="rgba(255,255,255,0.14)"/>
  <text x="84" y="120" font-size="24" fill="{self.settings.accent_color}" font-family="Microsoft YaHei">AI CONTENT FACTORY</text>
  <text x="84" y="214" font-size="58" fill="{self.settings.text_color}" font-family="Microsoft YaHei">{self._escape_xml(title)}</text>
  <text x="84" y="286" font-size="28" fill="{self.settings.muted_text_color}" font-family="Microsoft YaHei">{self._escape_xml(subtitle)}</text>
</svg>"""
        path.write_text(svg, encoding="utf-8")

    def _escape_xml(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _mix_color(self, color_a: str, color_b: str, ratio: float) -> tuple[int, int, int]:
        ratio = max(0.0, min(1.0, ratio))
        a = self._hex_to_rgb(color_a)
        b = self._hex_to_rgb(color_b)
        return tuple(int(a[index] * (1 - ratio) + b[index] * ratio) for index in range(3))

    def _hex_to_rgb(self, value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))

    def _hex_rgba(self, value: str, alpha: int) -> tuple[int, int, int, int]:
        rgb = self._hex_to_rgb(value)
        return rgb[0], rgb[1], rgb[2], alpha
