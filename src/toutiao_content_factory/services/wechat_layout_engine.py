"""微信公众号高级排版引擎 — 纯内联样式 + HTML结构渲染

微信编辑器只认内联 style，不支持 <style> 标签和伪元素。
因此本引擎直接在 HTML 生成阶段注入内联样式，
用 HTML 元素模拟伪元素装饰效果（如标题底线、引用引号等）。

设计参考: doocs/md / mdnice / 135编辑器
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


# ============================================================================
# 微信排版主题配置
# ============================================================================

@dataclass
class WechatTheme:
    """微信公众号排版主题配色与尺寸"""
    name: str = "default"
    primary_color: str = "#1a6fb5"
    secondary_color: str = "#3a8fd4"
    accent_color: str = "#e8f4fd"
    text_color: str = "#2f2f2f"
    muted_color: str = "#888888"
    bg_color: str = "#ffffff"
    card_bg: str = "#f8f9fb"
    border_color: str = "#e0e5eb"
    h2_bg: str = ""
    blockquote_bg: str = "#f7f9fc"
    strong_bg: str = ""
    list_bullet_color: str = ""
    font_family: str = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    title_size: str = "24px"
    heading_size: str = "20px"
    body_size: str = "16px"
    caption_size: str = "14px"
    line_height: str = "2.0"
    paragraph_spacing: str = "1.0em"
    card_padding: str = "20px"
    card_radius: str = "12px"

    def resolved_h2_bg(self) -> str:
        return self.h2_bg or self.primary_color

    def resolved_strong_bg(self) -> str:
        return self.strong_bg or self.accent_color

    def resolved_list_bullet_color(self) -> str:
        return self.list_bullet_color or self.primary_color


# ============================================================================
# 预设主题
# ============================================================================

THEME_PRESETS: dict[str, WechatTheme] = {
    "tech_blue": WechatTheme(
        name="tech_blue",
        primary_color="#1a6fb5",
        secondary_color="#3a8fd4",
        accent_color="#e8f4fd",
        h2_bg="#1a6fb5",
        blockquote_bg="#f0f6fc",
        strong_bg="#e8f4fd",
        list_bullet_color="#1a6fb5",
    ),
    "vibrant_orange": WechatTheme(
        name="vibrant_orange",
        primary_color="#e8723a",
        secondary_color="#f09860",
        accent_color="#fef3ec",
        text_color="#3a2a1a",
        h2_bg="#e8723a",
        blockquote_bg="#fdf5ee",
        strong_bg="#fef3ec",
        list_bullet_color="#e8723a",
        border_color="#f0d8c4",
        card_bg="#fdf8f4",
    ),
    "literary_green": WechatTheme(
        name="literary_green",
        primary_color="#2b8a5e",
        secondary_color="#4eaa80",
        accent_color="#edf7f1",
        text_color="#2a3a2f",
        h2_bg="#2b8a5e",
        blockquote_bg="#f0f8f3",
        strong_bg="#edf7f1",
        list_bullet_color="#2b8a5e",
        border_color="#c8e0d0",
        card_bg="#f5faf7",
    ),
    "minimal_gray": WechatTheme(
        name="minimal_gray",
        primary_color="#5a5a5a",
        secondary_color="#7a7a7a",
        accent_color="#f5f5f5",
        text_color="#2f2f2f",
        h2_bg="#5a5a5a",
        blockquote_bg="#f8f8f8",
        strong_bg="#f0f0f0",
        list_bullet_color="#5a5a5a",
        border_color="#e0e0e0",
        card_bg="#fafafa",
    ),
    "business_gold": WechatTheme(
        name="business_gold",
        primary_color="#b8860b",
        secondary_color="#d4a843",
        accent_color="#fdf8ed",
        text_color="#2a2518",
        h2_bg="#b8860b",
        blockquote_bg="#faf5e8",
        strong_bg="#fdf8ed",
        list_bullet_color="#b8860b",
        border_color="#e8d8b0",
        card_bg="#fcf9f2",
    ),
    "royal_purple": WechatTheme(
        name="royal_purple",
        primary_color="#7b4bbf",
        secondary_color="#9d6cd9",
        accent_color="#f3eefc",
        text_color="#2f283a",
        h2_bg="#7b4bbf",
        blockquote_bg="#f7f3fc",
        strong_bg="#f3eefc",
        list_bullet_color="#7b4bbf",
        border_color="#d8c8e8",
        card_bg="#faf7fc",
    ),
    "sakura_pink": WechatTheme(
        name="sakura_pink",
        primary_color="#e85c7a",
        secondary_color="#f08aa3",
        accent_color="#fef2f5",
        text_color="#3a282d",
        h2_bg="#e85c7a",
        blockquote_bg="#fdf5f7",
        strong_bg="#fef2f5",
        list_bullet_color="#e85c7a",
        border_color="#f0c8d0",
        card_bg="#fef8fa",
    ),
    "ocean_teal": WechatTheme(
        name="ocean_teal",
        primary_color="#148f8c",
        secondary_color="#3ab3b0",
        accent_color="#e9f7f7",
        text_color="#253535",
        h2_bg="#148f8c",
        blockquote_bg="#f0f7f7",
        strong_bg="#e9f7f7",
        list_bullet_color="#148f8c",
        border_color="#c8e0e0",
        card_bg="#f5faf9",
    ),
}

# 默认主题 = tech_blue
THEME_PRESETS["default"] = THEME_PRESETS["tech_blue"]


# ============================================================================
# 内联样式构建器 — 所有样式直接内联，不依赖 class 或 <style>
# ============================================================================

class InlineStyleBuilder:
    """根据主题配置生成内联样式字符串"""

    def __init__(self, theme: WechatTheme | None = None):
        self.t = theme or WechatTheme()

    # --- 容器 ---
    def article_wrapper(self) -> str:
        return (
            f"font-family: {self.t.font_family}; "
            f"font-size: {self.t.body_size}; "
            f"color: {self.t.text_color}; "
            f"line-height: {self.t.line_height}; "
            f"max-width: 100%; "
            f"word-wrap: break-word; "
            f"letter-spacing: 1px; "
            f"padding: 16px 0;"
        )

    # --- 标题 ---
    def h1(self) -> str:
        return (
            f"font-size: {self.t.title_size}; "
            f"font-weight: 900; "
            f"color: {self.t.text_color}; "
            f"margin: 2.8em 0 1.4em; "
            f"letter-spacing: 3px; "
            f"text-align: center; "
            f"line-height: 1.7; "
            f"padding: 0.8em 1.2em; "
            f"border-bottom: 4px solid {self.t.primary_color}; "
            f"border-top: 4px solid {self.t.primary_color}; "
            f"display: table; "
            f"margin-left: auto; "
            f"margin-right: auto; "
            f"background: linear-gradient(180deg, transparent 55%, {self.t.accent_color} 55%); "
            f"position: relative; "
            f"box-shadow: 0 6px 16px rgba(0,0,0,0.08); "
            f"border-radius: 4px;"
        )

    def h1_underline(self) -> str:
        """h1 底部装饰线（模拟 ::after）"""
        return (
            f"display: block; "
            f"width: 80px; "
            f"height: 4px; "
            f"background: linear-gradient(90deg, {self.t.primary_color}, {self.t.secondary_color}); "
            f"margin: 16px auto 0; "
            f"border-radius: 4px;"
        )

    def h2(self) -> str:
        """h2 精致的渐变色块"""
        return (
            f"font-size: {self.t.heading_size}; "
            f"font-weight: 900; "
            f"color: #ffffff; "
            f"background: linear-gradient(135deg, {self.t.resolved_h2_bg()}, {self.t.secondary_color}); "
            f"margin: 2.8em 0 1.4em; "
            f"padding: 14px 40px; "
            f"border-radius: 20px; "
            f"text-align: center; "
            f"line-height: 1.7; "
            f"display: table; "
            f"margin-left: auto; "
            f"margin-right: auto; "
            f"letter-spacing: 3px; "
            f"box-shadow: 0 6px 24px rgba(0,0,0,0.18); "
            f"border: 2px solid rgba(255,255,255,0.3);"
        )

    def h3(self) -> str:
        return (
            f"font-size: 19px; "
            f"font-weight: 900; "
            f"color: {self.t.primary_color}; "
            f"margin: 2.2em 0 1em; "
            f"padding: 12px 0 12px 22px; "
            f"border-left: 7px solid {self.t.primary_color}; "
            f"border-bottom: 2px solid {self.t.accent_color}; "
            f"line-height: 1.7; "
            f"background: linear-gradient(90deg, {self.t.accent_color}, transparent); "
            f"border-radius: 0 10px 10px 0; "
            f"box-shadow: 0 2px 8px rgba(0,0,0,0.06);"
        )

    # --- 段落 ---
    def paragraph(self) -> str:
        return (
            f"margin: {self.t.paragraph_spacing} 0; "
            f"text-align: justify; "
            f"letter-spacing: 1.5px; "
            f"line-height: {self.t.line_height}; "
            f"text-indent: 2em; "
            f"font-size: 16px;"
        )

    def strong(self) -> str:
        return (
            f"color: {self.t.primary_color}; "
            f"font-weight: 800; "
            f"background: {self.t.resolved_strong_bg()}; "
            f"padding: 2px 6px; "
            f"border-radius: 4px; "
            f"border-bottom: 2px solid {self.t.primary_color};"
        )

    def link(self) -> str:
        return (
            f"color: {self.t.primary_color}; "
            f"text-decoration: none; "
            f"border-bottom: 2px solid {self.t.primary_color}; "
            f"padding-bottom: 1px;"
        )

    # --- 引用块 ---
    def blockquote(self) -> str:
        return (
            f"border-left: 5px solid {self.t.primary_color}; "
            f"background: linear-gradient(90deg, {self.t.blockquote_bg}, transparent); "
            f"margin: 1.5em 0; "
            f"padding: 18px 22px; "
            f"border-radius: 0 12px 12px 0; "
            f"color: #5a5a5a; "
            f"font-size: 15px; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08); "
            f"position: relative;"
        )

    def blockquote_quote_mark(self) -> str:
        """引用块左上角引号装饰"""
        return (
            f"font-size: 36px; "
            f"color: {self.t.primary_color}; "
            f"opacity: 0.15; "
            f"font-family: Georgia, serif; "
            f"line-height: 1; "
            f"margin-bottom: 6px;"
        )

    # --- 信息卡片 ---
    def info_card(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.accent_color}, transparent); "
            f"border-left: 5px solid {self.t.primary_color}; "
            f"padding: {self.t.card_padding}; "
            f"margin: 1.2em 0; "
            f"border-radius: {self.t.card_radius}; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08);"
        )

    def info_card_icon(self) -> str:
        return (
            f"font-size: 20px; "
            f"float: right; "
            f"margin-top: -4px; "
            f"opacity: 0.6;"
        )

    # --- 高亮框 ---
    def highlight_box(self) -> str:
        return (
            f"background: linear-gradient(135deg, #fffbe6, transparent); "
            f"border: 2px solid #ffe08a; "
            f"border-left: 5px solid #f5a623; "
            f"padding: {self.t.card_padding}; "
            f"margin: 1.2em 0; "
            f"border-radius: {self.t.card_radius}; "
            f"font-size: {self.t.caption_size}; "
            f"color: #6d5600; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08);"
        )

    # --- 数据提示框 ---
    def data_callout(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.card_bg}, transparent); "
            f"border: 2px solid {self.t.border_color}; "
            f"padding: {self.t.card_padding}; "
            f"margin: 1.2em 0; "
            f"border-radius: {self.t.card_radius}; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08);"
        )

    def data_callout_title(self) -> str:
        return (
            f"font-size: {self.t.caption_size}; "
            f"color: {self.t.primary_color}; "
            f"margin-bottom: 8px; "
            f"font-weight: 800; "
            f"letter-spacing: 1px;"
        )

    # --- 金句引用 ---
    def golden_quote(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.accent_color}, transparent); "
            f"border-left: 5px solid {self.t.primary_color}; "
            f"padding: 24px {self.t.card_padding}; "
            f"margin: 1.5em 0; "
            f"border-radius: {self.t.card_radius}; "
            f"text-align: center; "
            f"font-size: 18px; "
            f"font-weight: 700; "
            f"color: {self.t.primary_color}; "
            f"line-height: 1.8; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08);"
        )

    # --- 结尾卡片 ---
    def closing_card(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.card_bg}, {self.t.accent_color}); "
            f"border: 2px dashed {self.t.border_color}; "
            f"padding: 28px {self.t.card_padding}; "
            f"margin: 2em 0; "
            f"border-radius: {self.t.card_radius}; "
            f"text-align: center; "
            f"color: {self.t.muted_color}; "
            f"font-size: {self.t.caption_size}; "
            f"box-shadow: 0 6px 20px rgba(0,0,0,0.12);"
        )

    # --- 标签卡片 ---
    def tag_card(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.accent_color}, #ffffff); "
            f"border: 1px solid {self.t.border_color}; "
            f"padding: 16px {self.t.card_padding}; "
            f"margin: 1em 0; "
            f"border-radius: {self.t.card_radius}; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08);"
        )

    # --- 步骤卡片 ---
    def step_card(self) -> str:
        return (
            f"background: linear-gradient(90deg, {self.t.accent_color}, transparent); "
            f"border-left: 5px solid {self.t.primary_color}; "
            f"padding: 18px {self.t.card_padding}; "
            f"margin: 1.2em 0; "
            f"border-radius: 0 12px 12px 0; "
            f"box-shadow: 0 4px 12px rgba(0,0,0,0.08);"
        )

    # --- 要点列表 ---
    def key_point(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.accent_color}, transparent); "
            f"border: 1px solid {self.t.primary_color}; "
            f"padding: 12px 18px; "
            f"margin: 0.8em 0; "
            f"border-radius: 10px; "
            f"box-shadow: 0 2px 8px rgba(0,0,0,0.06);"
        )

    # --- 统计卡片 ---
    def stat_card(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.primary_color}, {self.t.secondary_color}); "
            f"padding: 24px {self.t.card_padding}; "
            f"margin: 1.5em 0; "
            f"border-radius: 16px; "
            f"text-align: center; "
            f"color: #ffffff; "
            f"box-shadow: 0 6px 20px rgba(0,0,0,0.18);"
        )

    # --- 分割线 ---
    def divider(self) -> str:
        return (
            f"text-align: center; "
            f"margin: 2em 0; "
            f"color: {self.t.border_color}; "
            f"font-size: 14px; "
            f"letter-spacing: 10px; "
            f"position: relative; "
            f"padding: 10px 0;"
        )

    def hr_line(self) -> str:
        return (
            f"border: none; "
            f"height: 3px; "
            f"background: linear-gradient(90deg, transparent, {self.t.primary_color}, transparent); "
            f"margin: 2em 0; "
            f"border-radius: 2px;"
        )

    # --- 图片 ---
    def image(self) -> str:
        return (
            f"max-width: 100%; "
            f"height: auto; "
            f"border-radius: 12px; "
            f"display: block; "
            f"margin: 1.2em auto; "
            f"box-shadow: 0 6px 20px rgba(0,0,0,0.12); "
            f"border: 1px solid {self.t.border_color};"
        )

    def image_caption(self) -> str:
        return (
            f"text-align: center; "
            f"font-size: {self.t.caption_size}; "
            f"color: {self.t.muted_color}; "
            f"margin-top: 10px; "
            f"padding: 6px 0; "
            f"border-bottom: 1px dashed {self.t.border_color};"
        )

    # --- 代码 ---
    def inline_code(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.accent_color}, transparent); "
            f"padding: 3px 8px; "
            f"border-radius: 6px; "
            f"font-size: 90%; "
            f"font-family: 'SF Mono', 'Menlo', 'Consolas', monospace; "
            f"color: {self.t.primary_color}; "
            f"border: 1px solid {self.t.border_color}; "
            f"box-shadow: 0 2px 6px rgba(0,0,0,0.05);"
        )

    def code_block(self) -> str:
        return (
            f"background: #2d2d2d; "
            f"color: #f8f8f2; "
            f"padding: 20px; "
            f"border-radius: 12px; "
            f"overflow-x: auto; "
            f"font-size: 14px; "
            f"line-height: 1.6; "
            f"margin: 1.2em 0; "
            f"box-shadow: 0 6px 20px rgba(0,0,0,0.15);"
        )

    # --- 列表 ---
    def ul(self) -> str:
        return f"padding-left: 1.8em; margin: 1em 0; list-style: none;"

    def ol(self) -> str:
        return f"padding-left: 1.8em; margin: 1em 0;"

    def li(self) -> str:
        return f"margin-bottom: 0.8em; line-height: 1.8;"

    def li_bullet(self) -> str:
        """无序列表项前缀圆点装饰"""
        return (
            f"display: inline-block; "
            f"width: 8px; "
            f"height: 8px; "
            f"border-radius: 50%; "
            f"background: linear-gradient(135deg, {self.t.resolved_list_bullet_color()}, {self.t.secondary_color}); "
            f"margin-right: 10px; "
            f"vertical-align: middle; "
            f"margin-top: -2px; "
            f"box-shadow: 0 2px 4px rgba(0,0,0,0.1);"
        )

    # --- 表格 ---
    def table(self) -> str:
        return (
            f"width: 100%; "
            f"border-collapse: collapse; "
            f"margin: 1.2em 0; "
            f"font-size: {self.t.caption_size}; "
            f"border-radius: {self.t.card_radius}; "
            f"overflow: hidden; "
            f"box-shadow: 0 6px 20px rgba(0,0,0,0.08);"
        )

    def th(self) -> str:
        return (
            f"background: linear-gradient(135deg, {self.t.primary_color}, {self.t.secondary_color}); "
            f"color: #ffffff; "
            f"padding: 14px 16px; "
            f"text-align: left; "
            f"font-weight: 700;"
        )

    def td(self) -> str:
        return (
            f"padding: 14px 16px; "
            f"border-bottom: 1px solid {self.t.border_color};"
        )

    # --- 脚注 ---
    def footnote(self) -> str:
        return (
            f"font-size: 12px; "
            f"color: {self.t.muted_color}; "
            f"border-top: 2px solid {self.t.border_color}; "
            f"padding-top: 12px; "
            f"margin-top: 2em;"
        )

    def keyword_chip(self) -> str:
        return (
            f"display: inline-block; "
            f"padding: 1px 8px; "
            f"margin: 0 2px; "
            f"border-radius: 999px; "
            f"background: {self.t.accent_color}; "
            f"border: 1px solid {self.t.border_color}; "
            f"color: {self.t.primary_color}; "
            f"font-weight: 700; "
            f"font-size: 0.92em;"
        )

    def original_badge(self) -> str:
        return (
            f"display: inline-block; "
            f"padding: 5px 12px; "
            f"border-radius: 999px; "
            f"background: {self.t.primary_color}; "
            f"color: #ffffff; "
            f"font-size: 12px; "
            f"font-weight: 800; "
            f"letter-spacing: 1px;"
        )

    def footer_shell(self) -> str:
        return (
            f"margin: 28px 0 0; "
            f"padding: 0; "
            f"border: 1px solid {self.t.border_color}; "
            f"border-radius: {self.t.card_radius}; "
            f"background: linear-gradient(180deg, #ffffff 0%, {self.t.card_bg} 100%); "
            f"overflow: hidden; "
            f"box-shadow: 0 10px 24px rgba(0,0,0,0.06);"
        )

    def footer_meta(self) -> str:
        return (
            f"padding: 18px 20px 6px; "
            f"text-align: center; "
            f"color: {self.t.muted_color}; "
            f"font-size: 13px; "
            f"line-height: 1.8;"
        )

    def cta_card(self) -> str:
        return (
            f"margin: 14px 16px 18px; "
            f"padding: 18px 18px 16px; "
            f"border-radius: {self.t.card_radius}; "
            f"background: linear-gradient(135deg, {self.t.accent_color}, #ffffff); "
            f"border: 1px solid {self.t.border_color};"
        )

    def cta_heading(self) -> str:
        return (
            f"margin: 0 0 12px; "
            f"color: {self.t.text_color}; "
            f"font-size: 18px; "
            f"font-weight: 800; "
            f"line-height: 1.6;"
        )

    def cta_bullet(self) -> str:
        return (
            f"margin: 0 0 10px; "
            f"padding: 10px 12px; "
            f"border-radius: 10px; "
            f"background: rgba(255,255,255,0.76); "
            f"color: {self.t.text_color}; "
            f"font-size: 15px; "
            f"line-height: 1.8;"
        )

    def cta_closing(self) -> str:
        return (
            f"margin: 10px 0 0; "
            f"color: {self.t.muted_color}; "
            f"font-size: 14px; "
            f"line-height: 1.8;"
        )


# ============================================================================
# 自定义组件渲染器（内联样式版）
# ============================================================================

class WechatComponentRenderer:
    """将自定义 Markdown 扩展语法渲染为微信内联 HTML 组件"""

    BLOCK_PATTERNS = [
        (re.compile(r'^:::info\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "info_card"),
        (re.compile(r'^:::warning\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "highlight_box"),
        (re.compile(r'^:::alert\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "highlight_box"),
        (re.compile(r'^:::data\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "data_callout"),
        (re.compile(r'^:::tag\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "tag_card"),
        (re.compile(r'^:::step\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "step_card"),
        (re.compile(r'^:::key\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "key_point"),
        (re.compile(r'^:::stat\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "stat_card"),
        (re.compile(r'^```highlight\s*\n(.*?)```', re.DOTALL | re.MULTILINE), "highlight_box"),
        (re.compile(r'^>>\s+(.+?)$', re.MULTILINE), "pullquote"),
        (re.compile(r'^---$', re.MULTILINE), "divider"),
    ]

    @classmethod
    def _wrap_content_paragraphs(cls, content: str, style: str = "", inline_formatter=None) -> str:
        """将组件内容中的纯文本行包裹为 <p> 标签"""
        lines = content.strip().split("\n")
        wrapped = []
        p_style = f' style="{style}"' if style else ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<"):
                wrapped.append(stripped)
            else:
                rendered = inline_formatter(stripped) if inline_formatter else html.escape(stripped)
                wrapped.append(f"<p{p_style}>{rendered}</p>")
        return "\n".join(wrapped)

    @classmethod
    def _svg_ribbon(cls, sb: InlineStyleBuilder, *, compact: bool = False) -> str:
        height = "10" if compact else "14"
        margin = "0 0 10px" if compact else "0 0 12px"
        return (
            f'<svg viewBox="0 0 400 14" preserveAspectRatio="none" '
            f'style="display:block;width:100%;height:{height}px;margin:{margin};">'
            f'<path d="M0 10 C50 14 85 4 135 8 C185 12 220 0 270 4 C320 8 350 14 400 8 L400 14 L0 14 Z" '
            f'fill="{sb.t.accent_color}"></path>'
            f'<path d="M0 6 C60 10 90 0 145 4 C200 8 245 0 305 4 C345 6 370 8 400 5" '
            f'stroke="{sb.t.primary_color}" stroke-width="2" fill="none"></path>'
            f'</svg>'
        )

    @classmethod
    def _render_metrics(cls, metrics: list[tuple[str, str]], sb: InlineStyleBuilder, inline_formatter=None) -> str:
        if not metrics:
            return ""
        cells = []
        for label, value in metrics[:4]:
            rendered_value = inline_formatter(value) if inline_formatter else html.escape(value)
            rendered_label = inline_formatter(label) if inline_formatter else html.escape(label)
            cells.append(
                "<td style=\"width:50%;padding:6px;vertical-align:top;\">"
                f"<div style=\"padding:12px 12px 10px;border-radius:12px;background:#ffffff;"
                f"border:1px solid {sb.t.border_color};box-shadow:0 4px 12px rgba(0,0,0,0.04);\">"
                f"<p style=\"margin:0;color:{sb.t.primary_color};font-size:20px;font-weight:800;line-height:1.3;\">{rendered_value}</p>"
                f"<p style=\"margin:6px 0 0;color:{sb.t.muted_color};font-size:12px;line-height:1.6;\">{rendered_label}</p>"
                "</div>"
                "</td>"
            )
        if len(cells) % 2 == 1:
            cells.append("<td style=\"width:50%;padding:6px;\"></td>")
        rows = []
        for idx in range(0, len(cells), 2):
            rows.append(f"<tr>{cells[idx]}{cells[idx + 1]}</tr>")
        return (
            "<table style=\"width:100%;border-collapse:collapse;margin:8px 0 2px;\">"
            f"{''.join(rows)}"
            "</table>"
        )

    @classmethod
    def render_components(cls, markdown: str, sb: InlineStyleBuilder, inline_formatter=None) -> str:
        result = markdown

        for pattern, component_type in cls.BLOCK_PATTERNS:
            if component_type == "info_card":
                def info_replacer(m):
                    content = m.group(1).strip()
                    body = cls._wrap_content_paragraphs(
                        content,
                        "margin: 0.5em 0; line-height: 1.8;",
                        inline_formatter=inline_formatter,
                    )
                    return (
                        f'<section style="{sb.info_card()}">\n'
                        f'{cls._svg_ribbon(sb, compact=True)}\n'
                        f'<span style="{sb.info_card_icon()}">💡</span>\n'
                        f'{body}\n'
                        f'</section>'
                    )
                result = pattern.sub(info_replacer, result)

            elif component_type == "highlight_box":
                def highlight_replacer(m):
                    content = m.group(1).strip()
                    lines = [line.strip() for line in content.splitlines() if line.strip()]
                    title = ""
                    if lines and lines[0].startswith("# "):
                        title = lines.pop(0)[2:].strip()
                    body = cls._wrap_content_paragraphs(
                        "\n".join(lines),
                        "margin: 0.5em 0; line-height: 1.8;",
                        inline_formatter=inline_formatter,
                    )
                    title_html = ""
                    if title:
                        rendered_title = inline_formatter(title) if inline_formatter else html.escape(title)
                        title_html = (
                            f'<p style="margin:0 0 8px;color:{sb.t.text_color};font-size:17px;'
                            f'font-weight:800;line-height:1.6;">{rendered_title}</p>'
                        )
                    badge = (
                        f'<span style="display:inline-block;margin:0 0 10px;padding:4px 10px;'
                        f'border-radius:999px;background:#fff7db;color:#8a5b00;font-size:12px;'
                        f'font-weight:800;">风险提醒</span>'
                    )
                    return (
                        f'<section style="{sb.highlight_box()}">\n'
                        f'{cls._svg_ribbon(sb)}\n'
                        f'{badge}\n'
                        f'{title_html}\n'
                        f'{body}\n'
                        f'</section>'
                    )
                result = pattern.sub(highlight_replacer, result)

            elif component_type == "data_callout":
                def data_replacer(m):
                    content = m.group(1).strip()
                    lines = [line.strip() for line in content.splitlines() if line.strip()]
                    title_html = ""
                    if lines and lines[0].startswith("# "):
                        title = lines.pop(0)[2:].strip()
                        rendered_title = inline_formatter(title) if inline_formatter else html.escape(title)
                        title_html = f'<div style="{sb.data_callout_title()}">{rendered_title}</div>'
                    metrics: list[tuple[str, str]] = []
                    body_lines: list[str] = []
                    for line in lines:
                        match = re.match(r"^([^:：]{1,16})[:：]\s*(.+)$", line)
                        if match and len(metrics) < 4:
                            metrics.append((match.group(1).strip(), match.group(2).strip()))
                        else:
                            body_lines.append(line)
                    metrics_html = cls._render_metrics(metrics, sb, inline_formatter=inline_formatter)
                    body = cls._wrap_content_paragraphs(
                        "\n".join(body_lines),
                        "margin: 0.45em 0; line-height: 1.8;",
                        inline_formatter=inline_formatter,
                    )
                    return (
                        f'<section style="{sb.data_callout()}">\n'
                        f'{cls._svg_ribbon(sb)}\n'
                        f'{title_html}\n'
                        f'{metrics_html}\n'
                        f'{body}\n'
                        f'</section>'
                    )
                result = pattern.sub(data_replacer, result)

            elif component_type == "pullquote":
                def pullquote_replacer(m):
                    content = m.group(1).strip()
                    return (
                        f'<section style="{sb.golden_quote()}">\n'
                        f'<p style="margin: 0; text-align: center;">{content}</p>\n'
                        f'</section>'
                    )
                result = pattern.sub(pullquote_replacer, result)

            elif component_type == "tag_card":
                def tag_replacer(m):
                    content = m.group(1).strip()
                    body = cls._wrap_content_paragraphs(
                        content,
                        "margin: 0.5em 0; line-height: 1.8;",
                        inline_formatter=inline_formatter,
                    )
                    return (
                        f'<section style="{sb.tag_card()}">\n'
                        f'{body}\n'
                        f'</section>'
                    )
                result = pattern.sub(tag_replacer, result)

            elif component_type == "step_card":
                def step_replacer(m):
                    content = m.group(1).strip()
                    lines = content.split("\n", 1)
                    title_html = ""
                    body_text = content
                    if lines[0].startswith("# "):
                        title = lines[0][2:].strip()
                        rendered_title = inline_formatter(title) if inline_formatter else html.escape(title)
                        title_html = f'<div style="font-weight: 800; color: {sb.t.primary_color}; margin-bottom: 8px; font-size: 17px;">{rendered_title}</div>'
                        body_text = lines[1].strip() if len(lines) > 1 else ""
                    body = cls._wrap_content_paragraphs(body_text, inline_formatter=inline_formatter)
                    return (
                        f'<section style="{sb.step_card()}">\n'
                        f'{title_html}\n'
                        f'{body}\n'
                        f'</section>'
                    )
                result = pattern.sub(step_replacer, result)

            elif component_type == "key_point":
                def key_replacer(m):
                    content = m.group(1).strip()
                    body = cls._wrap_content_paragraphs(
                        content,
                        "margin: 0; line-height: 1.7; font-weight: 500;",
                        inline_formatter=inline_formatter,
                    )
                    return (
                        f'<section style="{sb.key_point()}">\n'
                        f'{body}\n'
                        f'</section>'
                    )
                result = pattern.sub(key_replacer, result)

            elif component_type == "stat_card":
                def stat_replacer(m):
                    content = m.group(1).strip()
                    lines = content.split("\n")
                    number = lines[0].strip() if len(lines) > 0 else ""
                    label = lines[1].strip() if len(lines) > 1 else ""
                    rendered_number = inline_formatter(number) if inline_formatter else html.escape(number)
                    rendered_label = inline_formatter(label) if inline_formatter else html.escape(label)
                    return (
                        f'<section style="{sb.stat_card()}">\n'
                        f'<p style="margin: 0; font-size: 48px; font-weight: 900; line-height: 1.2;">{rendered_number}</p>\n'
                        f'<p style="margin: 8px 0 0; font-size: 16px; opacity: 0.9; line-height: 1.5;">{rendered_label}</p>\n'
                        f'</section>'
                    )
                result = pattern.sub(stat_replacer, result)

            elif component_type == "divider":
                result = pattern.sub(
                    f'<p style="{sb.divider()}">✦ ✦ ✦</p>',
                    result,
                )

        return result

    @classmethod
    def add_closing_card(cls, html_content: str, brand_name: str = "", sb: InlineStyleBuilder | None = None) -> str:
        if sb is None:
            sb = InlineStyleBuilder()
        card = (
            f'<section style="{sb.closing_card()}">\n'
            f'<p style="margin-bottom: 8px;"><strong>✨ 以上为本文全部内容 ✨</strong></p>\n'
            f'<p style="margin: 4px 0;">如果你觉得有收获，欢迎'
            f'<span style="color: {sb.t.primary_color}; font-weight: 700;">点赞</span>、'
            f'<span style="color: {sb.t.primary_color}; font-weight: 700;">在看</span>、'
            f'<span style="color: {sb.t.primary_color}; font-weight: 700;">转发</span></p>\n'
            f'<p style="font-size: 13px; color: #aaa; margin-top: 12px;">{html.escape(brand_name)} · 深度行业观察</p>\n'
            f'</section>'
        )
        return html_content.rstrip() + "\n" + card


# ============================================================================
# Markdown → 微信内联 HTML 转换器
# ============================================================================

class MarkdownToWechatConverter:
    """将 Markdown 文章转换为微信兼容的纯内联样式 HTML"""

    DEFAULT_KEYWORDS = (
        "AIGC",
        "Agent",
        "SaaS",
        "ROI",
        "GMV",
        "AI",
        "增长",
        "转化",
        "留存",
        "复购",
        "利润",
        "现金流",
        "供应链",
        "出海",
        "自动化",
        "订阅",
        "私域",
        "线索",
        "客单价",
        "渗透率",
    )
    CTA_MARKERS = ("## 延伸服务", "## 延伸阅读", "## 进一步交流")
    METRIC_PATTERN = re.compile(
        r"(?<![\w])(\d+(?:\.\d+)?(?:%|％|万亿|亿元|万元|万|亿|倍|家|人|条|个点|bp|BP|年|个月|月|天|小时|分钟))"
    )

    def __init__(
        self,
        theme: WechatTheme | None = None,
        brand_name: str = "",
        *,
        cta_payload: dict[str, Any] | None = None,
        keyword_patterns: list[str] | tuple[str, ...] | None = None,
        show_original_badge: bool = True,
    ):
        self.theme = theme or WechatTheme()
        self.brand_name = brand_name
        self.cta_payload = cta_payload or {}
        self.show_original_badge = show_original_badge
        self.sb = InlineStyleBuilder(self.theme)
        keywords = keyword_patterns or list(self.DEFAULT_KEYWORDS)
        normalized = sorted({str(item).strip() for item in keywords if str(item).strip()}, key=len, reverse=True)
        self.keyword_patterns = normalized
        self.keyword_regex = self._build_keyword_regex(normalized)

    def convert(self, markdown: str, *, add_closing: bool = True) -> str:
        """完整转换流程：高级排版失败时，自动回退到基础 HTML 渲染。"""
        clean_markdown = self._strip_trailing_cta(markdown)
        try:
            html_content = self._safe_main_render(clean_markdown)
        except Exception as exc:
            logger.warning("Advanced WeChat layout failed, falling back to basic renderer: %s", exc)
            html_content = self._basic_markdown_to_html(clean_markdown)
        return self.render_postamble(html_content, add_closing=add_closing)

    def render_postamble(self, html_content: str, *, add_closing: bool = True) -> str:
        if not add_closing:
            return html_content
        footer = self._render_footer()
        if not footer:
            return html_content
        return html_content.rstrip() + "\n" + footer

    def _safe_main_render(self, markdown: str) -> str:
        structured = self._auto_structure(markdown)
        html_with_components = WechatComponentRenderer.render_components(
            structured,
            self.sb,
            inline_formatter=self._inline_format,
        )
        html_body = self._markdown_to_html(html_with_components)
        return (
            f'<section style="{self.sb.article_wrapper()}">\n'
            f'{html_body}\n'
            f'</section>'
        )

    def _strip_trailing_cta(self, markdown: str) -> str:
        text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return ""
        positions = [text.find(marker) for marker in self.CTA_MARKERS if marker in text]
        positions = [pos for pos in positions if pos >= 0]
        if not positions:
            return text
        return text[: min(positions)].rstrip()

    # ---- 智能结构化 ----
    # 纯文本段落没有 # 标题、> 引用等标记时，自动识别并添加结构

    def _auto_structure(self, markdown: str) -> str:
        """为纯文本段落自动添加 Markdown 结构标记

        规则：
        1. 短句（≤25字）+ 感叹号/问号结尾 → h2
        2. 短句（≤20字）+ 冒号结尾 → h3
        3. 含"——"破折号的总结性句子 → blockquote
        4. 互动提问（"你觉得"/"你怎么看"等）→ blockquote
        5. 首段短句（≤30字，文章第一个段落）→ blockquote（导语）
        """
        lines = markdown.split("\n")
        result: list[str] = []
        has_any_heading = any(line.strip().startswith("#") for line in lines if line.strip())
        first_nonempty_found = False

        for line in lines:
            stripped = line.strip()

            if not stripped:
                result.append(line)
                continue

            if (stripped.startswith("#") or stripped.startswith(">") or
                stripped.startswith("- ") or stripped.startswith("* ") or
                stripped.startswith(":::") or stripped.startswith("```") or
                stripped.startswith(">>") or stripped.startswith("---") or
                re.match(r'^\d+\.\s', stripped)):
                result.append(line)
                first_nonempty_found = True
                continue

            if stripped.startswith("<"):
                result.append(line)
                first_nonempty_found = True
                continue

            if not first_nonempty_found and len(stripped) <= 30:
                result.append(f"> {stripped}")
                first_nonempty_found = True
                continue
            first_nonempty_found = True

            if re.match(r'^(你觉得|你怎么看|你怎么认为|欢迎留言|你会选择)', stripped):
                result.append(f"> {stripped}")
                continue

            if len(stripped) <= 25 and re.search(r'[！？!?]$', stripped):
                if not has_any_heading:
                    result.append(f"## {stripped}")
                    continue

            if len(stripped) <= 20 and re.search(r'[：:]$', stripped):
                result.append(f"### {stripped}")
                continue

            if "——" in stripped and len(stripped) > 15:
                result.append(f"> {stripped}")
                continue

            result.append(line)

        return "\n".join(result)

    def _markdown_to_html(self, text: str) -> str:
        """Markdown → 内联样式 HTML（每个元素直接带 style 属性）"""
        lines = text.strip().split("\n")
        html_lines: list[str] = []
        in_list = False
        list_type = ""
        in_blockquote = False

        for line in lines:
            stripped = line.strip()

            if not stripped:
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                continue

            if stripped.startswith("<"):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                html_lines.append(line)
                continue

            if (stripped.startswith("</section") or stripped.startswith("</p>") or stripped.startswith("</div>")
                    or stripped.startswith("</span")):
                html_lines.append(line)
                continue

            if stripped.startswith("# ") and not stripped.startswith("## "):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                html_lines.append(self._render_heading(1, stripped[2:]))
                continue

            if stripped.startswith("## ") and not stripped.startswith("### "):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                html_lines.append(self._render_heading(2, stripped[3:]))
                continue

            if stripped.startswith("### "):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                html_lines.append(self._render_heading(3, stripped[4:]))
                continue

            if stripped.startswith("> ") or stripped == ">":
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                quote_content = stripped[2:].strip() if stripped.startswith("> ") else ""
                if not in_blockquote:
                    in_blockquote = True
                    html_lines.append(
                        f'<blockquote style="{self.sb.blockquote()}">'
                    )
                if quote_content:
                    html_lines.append(
                        f'<p style="margin: 0.3em 0; color: #5a5a5a; font-size: 15px; line-height: 1.85;">'
                        f'{self._inline_format(quote_content)}</p>'
                    )
                continue

            if re.match(r'^[\-\*]\s+', stripped):
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                content = re.sub(r'^[\-\*]\s+', '', stripped)
                if not in_list:
                    in_list = True
                    list_type = "ul"
                    html_lines.append(f'<ul style="{self.sb.ul()}">')
                html_lines.append(
                    f'<li style="{self.sb.li()}">'
                    f'<span style="{self.sb.li_bullet()}"></span>'
                    f'{self._inline_format(content)}</li>'
                )
                continue

            if re.match(r'^\d+\.\s+', stripped):
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                content = re.sub(r'^\d+\.\s+', '', stripped)
                if not in_list or list_type != "ol":
                    if in_list:
                        html_lines.append(f"</{list_type}>")
                    in_list = True
                    list_type = "ol"
                    html_lines.append(f'<ol style="{self.sb.ol()}">')
                html_lines.append(f'<li style="{self.sb.li()}">{self._inline_format(content)}</li>')
                continue

            if stripped == "---" or stripped == "***":
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                if in_blockquote:
                    html_lines.append("</blockquote>")
                    in_blockquote = False
                html_lines.append(f'<p style="{self.sb.divider()}">✦ ✦ ✦</p>')
                continue

            if in_list:
                html_lines.append(f"</{list_type}>")
                in_list = False
            if in_blockquote:
                html_lines.append("</blockquote>")
                in_blockquote = False
            html_lines.append(self._render_paragraph(stripped))

        if in_list:
            html_lines.append(f"</{list_type}>")
        if in_blockquote:
            html_lines.append("</blockquote>")

        return "\n".join(html_lines)

    def _render_heading(self, level: int, text: str) -> str:
        content = self._inline_format(text.strip())
        if level == 1:
            return (
                '<section style="margin: 18px 0 24px;">'
                f'{self._svg_divider()}'
                f'<h1 style="{self.sb.h1()}">{content}</h1>'
                f'<span style="{self.sb.h1_underline()}"></span>'
                "</section>"
            )
        if level == 2:
            return (
                '<section style="margin: 30px 0 16px;">'
                f'{self._svg_divider(compact=True)}'
                f'<h2 style="{self.sb.h2()}">{content}</h2>'
                "</section>"
            )
        return (
            '<section style="margin: 24px 0 12px;">'
            f'<h3 style="{self.sb.h3()}">{content}</h3>'
            "</section>"
        )

    def _render_paragraph(self, text: str) -> str:
        return f'<p style="{self.sb.paragraph()}">{self._inline_format(text)}</p>'

    def _svg_divider(self, *, compact: bool = False) -> str:
        height = "12px" if compact else "16px"
        margin = "0 auto 10px"
        return (
            f'<svg viewBox="0 0 320 16" preserveAspectRatio="none" '
            f'style="display:block;width:100%;max-width:320px;height:{height};margin:{margin};">'
            f'<path d="M8 8 H118" stroke="{self.theme.primary_color}" stroke-width="2.5" stroke-linecap="round"></path>'
            f'<circle cx="160" cy="8" r="4" fill="{self.theme.secondary_color}"></circle>'
            f'<path d="M202 8 H312" stroke="{self.theme.primary_color}" stroke-width="2.5" stroke-linecap="round"></path>'
            '</svg>'
        )

    def _inline_format(self, text: str) -> str:
        """处理行内格式：加粗、代码、链接、图片，并自动高亮数字和关键词。"""
        tokenized, tokens = self._tokenize_special_spans(str(text or ""))
        safe_text = self._format_plain_text(tokenized)
        for token, rendered in tokens.items():
            safe_text = safe_text.replace(token, rendered)
        return safe_text

    def _tokenize_special_spans(self, text: str) -> tuple[str, dict[str, str]]:
        tokens: dict[str, str] = {}
        counter = 0

        def register(rendered: str) -> str:
            nonlocal counter
            token = f"__WX_TOKEN_{counter}__"
            tokens[token] = rendered
            counter += 1
            return token

        def image_replacer(match: re.Match[str]) -> str:
            alt = self._format_plain_text(match.group(1).strip())
            src = html.escape(match.group(2).strip(), quote=True)
            rendered = (
                f'<img src="{src}" alt="{alt}" style="{self.sb.image()}">'
                f'<p style="{self.sb.image_caption()}">{alt}</p>'
            )
            return register(rendered)

        def link_replacer(match: re.Match[str]) -> str:
            label = self._format_plain_text(match.group(1).strip())
            href = html.escape(match.group(2).strip(), quote=True)
            return register(f'<a href="{href}" style="{self.sb.link()}">{label}</a>')

        def code_replacer(match: re.Match[str]) -> str:
            code = html.escape(match.group(1).strip())
            return register(f'<code style="{self.sb.inline_code()}">{code}</code>')

        def bold_replacer(match: re.Match[str]) -> str:
            strong_text = self._format_plain_text(match.group(1).strip())
            return register(f'<strong style="{self.sb.strong()}">{strong_text}</strong>')

        text = re.sub(r'!\[([^\]]*)\]\(([^\)]+)\)', image_replacer, text)
        text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', link_replacer, text)
        text = re.sub(r'`([^`]+)`', code_replacer, text)
        text = re.sub(r'\*\*(.+?)\*\*', bold_replacer, text)
        return text, tokens

    def _format_plain_text(self, text: str) -> str:
        return self._auto_highlight_plain_text(html.escape(text))

    def _auto_highlight_plain_text(self, text: str) -> str:
        highlighted = self.METRIC_PATTERN.sub(
            lambda m: f'<strong style="{self.sb.strong()}">{m.group(1)}</strong>',
            text,
        )
        if self.keyword_regex is not None:
            highlighted = self.keyword_regex.sub(
                lambda m: f'<span style="{self.sb.keyword_chip()}">{m.group(0)}</span>',
                highlighted,
            )
        return highlighted

    def _build_keyword_regex(self, patterns: list[str]) -> re.Pattern[str] | None:
        if not patterns:
            return None
        escaped = "|".join(re.escape(pattern) for pattern in patterns)
        if not escaped:
            return None
        return re.compile(escaped, flags=re.IGNORECASE)

    def _render_footer(self) -> str:
        if not self.brand_name and not self.show_original_badge and not self.cta_payload:
            return ""
        badge_html = ""
        if self.show_original_badge:
            badge_html = f'<span style="{self.sb.original_badge()}">原创</span>'
        brand_html = ""
        if self.brand_name:
            brand_html = (
                f'<p style="margin: 8px 0 0;">'
                f'{badge_html} <span style="margin-left:8px;">{html.escape(self.brand_name)} 出品</span>'
                f'</p>'
            ) if badge_html else f'<p style="margin: 8px 0 0;">{html.escape(self.brand_name)} 出品</p>'
        elif badge_html:
            brand_html = f'<p style="margin: 8px 0 0;">{badge_html}</p>'

        cta_html = self._render_cta_card()
        return (
            f'<section style="{self.sb.footer_shell()}">\n'
            f'{self._svg_divider()}\n'
            f'<div style="{self.sb.footer_meta()}">\n'
            f'<p style="margin: 0; color: {self.theme.text_color}; font-size: 15px; font-weight: 700;">以上内容，供你判断，不替你决策。</p>\n'
            f'{brand_html}\n'
            f'</div>\n'
            f'{cta_html}\n'
            f'</section>'
        )

    def _render_cta_card(self) -> str:
        payload = self.cta_payload if isinstance(self.cta_payload, dict) else {}
        heading = str(payload.get("heading", "")).strip()
        bullets = [str(item).strip() for item in payload.get("bullets", []) if str(item).strip()]
        closing = str(payload.get("closing", "")).strip()
        if not heading and not bullets and not closing:
            return ""
        items = []
        for item in bullets:
            items.append(
                f'<p style="{self.sb.cta_bullet()}">'
                f'<span style="color:{self.theme.primary_color};font-weight:800;">●</span> '
                f'{self._inline_format(item)}'
                f'</p>'
            )
        closing_html = ""
        if closing:
            closing_html = f'<p style="{self.sb.cta_closing()}">{self._inline_format(closing)}</p>'
        return (
            f'<div style="{self.sb.cta_card()}">\n'
            f'<p style="{self.sb.cta_heading()}">{self._inline_format(heading or "想继续往下聊？")}</p>\n'
            f'{"".join(items)}\n'
            f'{closing_html}\n'
            f'</div>'
        )

    def _basic_markdown_to_html(self, markdown: str) -> str:
        blocks = [block.strip() for block in re.split(r"\n\s*\n+", markdown) if block.strip()]
        html_blocks: list[str] = []
        for block in blocks:
            if block.startswith("### "):
                html_blocks.append(f'<h3 style="{self.sb.h3()}">{self._format_plain_text(block[4:])}</h3>')
                continue
            if block.startswith("## "):
                html_blocks.append(f'<h2 style="{self.sb.h2()}">{self._format_plain_text(block[3:])}</h2>')
                continue
            if block.startswith("# "):
                html_blocks.append(f'<h1 style="{self.sb.h1()}">{self._format_plain_text(block[2:])}</h1>')
                continue
            html_blocks.append(f'<p style="{self.sb.paragraph()}">{self._format_plain_text(block)}</p>')
        body = "\n".join(html_blocks)
        return (
            f'<section style="{self.sb.article_wrapper()}">\n'
            f'{body}\n'
            f'</section>'
        )


def append_wechat_footer(
    html_content: str,
    *,
    theme: WechatTheme | None = None,
    brand_name: str = "",
    cta_payload: dict[str, Any] | None = None,
    add_closing: bool = True,
    show_original_badge: bool = True,
) -> str:
    converter = MarkdownToWechatConverter(
        theme=theme,
        brand_name=brand_name,
        cta_payload=cta_payload,
        show_original_badge=show_original_badge,
    )
    return converter.render_postamble(html_content, add_closing=add_closing)


# ============================================================================
# 微信官方草稿 API 客户端
# ============================================================================

class WechatDraftApiClient:
    """微信公众号草稿 API 客户端

    使用官方 API 替代 RPA 浏览器自动化上传图文，
    通过 POST https://api.weixin.qq.com/cgi-bin/draft/add 上传草稿。
    """

    DRAFT_ADD_URL = "https://api.weixin.qq.com/cgi-bin/draft/add"
    DRAFT_UPDATE_URL = "https://api.weixin.qq.com/cgi-bin/draft/update"
    DRAFT_GET_URL = "https://api.weixin.qq.com/cgi-bin/draft/get"
    MATERIAL_ADD_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material"
    ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"

    def __init__(self, app_id: str, app_secret: str, logger: logging.Logger | None = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.logger = logger or logging.getLogger(__name__)
        self._access_token: str | None = None
        self._token_expires: float = 0

    @property
    def available(self) -> bool:
        return bool(self.app_id.strip() and self.app_secret.strip())

    def _get_access_token(self) -> str:
        import time
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        url = f"{self.ACCESS_TOKEN_URL}?grant_type=client_credential&appid={self.app_id}&secret={self.app_secret}"
        try:
            req = Request(url)
            resp = urlopen(req, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
            self._access_token = str(data.get("access_token", ""))
            expires_in = int(data.get("expires_in", 7200))
            self._token_expires = time.time() + expires_in - 300
            self.logger.info("WeChat access token refreshed, expires in %ds", expires_in)
            return self._access_token
        except Exception as exc:
            self.logger.error("Failed to get WeChat access token: %s", exc)
            raise RuntimeError(f"WeChat access token failed: {exc}") from exc

    def create_draft(
        self,
        title: str,
        content_html: str,
        author: str = "",
        digest: str = "",
        content_source_url: str = "",
        need_open_comment: int = 0,
        only_fans_can_comment: int = 0,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("WeChat Draft API is not configured. Set WECHAT_APP_ID and WECHAT_APP_SECRET.")

        access_token = self._get_access_token()
        url = f"{self.DRAFT_ADD_URL}?access_token={access_token}"

        payload = {
            "articles": [
                {
                    "title": title[:64],
                    "author": author,
                    "digest": digest[:120] if digest else content_html[:120].replace("<", "").replace(">", ""),
                    "content": content_html,
                    "content_source_url": content_source_url,
                    "need_open_comment": need_open_comment,
                    "only_fans_can_comment": only_fans_can_comment,
                }
            ]
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})

        try:
            resp = urlopen(req, timeout=15)
            result = json.loads(resp.read().decode("utf-8"))
            self.logger.info("WeChat draft created: %s", json.dumps(result, ensure_ascii=False))
            return {"status": "success", "draft_media_id": result.get("media_id", ""), "response": result}
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            self.logger.error("WeChat draft API error: %s", err_body)
            return {"status": "error", "error": err_body, "draft_media_id": ""}
        except Exception as exc:
            self.logger.error("WeChat draft creation failed: %s", exc)
            return {"status": "error", "error": str(exc), "draft_media_id": ""}

    def upload_image(self, image_path: Path | str) -> dict[str, Any]:
        if not self.available:
            return {"status": "error", "error": "API not configured"}

        access_token = self._get_access_token()
        url = f"{self.MATERIAL_ADD_URL}?access_token={access_token}&type=image"

        image_path = Path(image_path)
        if not image_path.exists():
            return {"status": "error", "error": f"Image not found: {image_path}"}

        import mimetypes
        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"

        boundary = "----WechatBoundary" + str(hash(str(image_path)) % 1000000)
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media"; filename="{image_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        body += image_path.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

        try:
            resp = urlopen(req, timeout=30)
            result = json.loads(resp.read().decode("utf-8"))
            return {"status": "success", "media_id": result.get("media_id", ""), "url": result.get("url", "")}
        except Exception as exc:
            self.logger.error("Failed to upload image to WeChat: %s", exc)
            return {"status": "error", "error": str(exc)}


# ============================================================================
# 顶层便捷函数
# ============================================================================

def convert_markdown_to_wechat_html(
    markdown: str,
    *,
    theme: WechatTheme | None = None,
    brand_name: str = "",
    add_closing: bool = True,
    cta_payload: dict[str, Any] | None = None,
    keyword_patterns: list[str] | tuple[str, ...] | None = None,
    show_original_badge: bool = True,
) -> str:
    """Markdown → 微信排版 HTML（一行调用完成全部转换）"""
    converter = MarkdownToWechatConverter(
        theme=theme,
        brand_name=brand_name,
        cta_payload=cta_payload,
        keyword_patterns=keyword_patterns,
        show_original_badge=show_original_badge,
    )
    return converter.convert(markdown, add_closing=add_closing)


def create_wechat_draft_via_api(
    markdown: str,
    title: str,
    *,
    app_id: str,
    app_secret: str,
    author: str = "",
    digest: str = "",
    theme: WechatTheme | None = None,
    brand_name: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """一站式：Markdown 转换 + 微信公众号草稿 API 上传"""
    html_content = convert_markdown_to_wechat_html(markdown, theme=theme, brand_name=brand_name)

    client = WechatDraftApiClient(app_id=app_id, app_secret=app_secret)
    return client.create_draft(
        title=title,
        content_html=html_content,
        author=author,
        digest=digest,
        content_source_url=source_url,
    )
