"""微信公众号高级排版引擎 — CSS Inline + 组件渲染 + API草案备份"""
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
# 默认微信排版 CSS 主题
# ============================================================================

@dataclass
class WechatTheme:
    """微信公众号排版主题配置"""
    name: str = "default"
    primary_color: str = "#1a6fb5"
    secondary_color: str = "#3a8fd4"
    accent_color: str = "#e8f4fd"
    text_color: str = "#2f2f2f"
    muted_color: str = "#888888"
    bg_color: str = "#ffffff"
    card_bg: str = "#f8f9fb"
    border_color: str = "#e0e5eb"
    font_family: str = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    title_size: str = "22px"
    heading_size: str = "18px"
    body_size: str = "16px"
    caption_size: str = "14px"
    line_height: str = "1.8"
    paragraph_spacing: str = "0.8em"
    card_padding: str = "16px"
    card_radius: str = "8px"
    blockquote_border_width: str = "3px"
    blockquote_bg: str = "#f7f9fc"

    DEFAULT_THEME_CSS = """
    /* 微信公众号基础样式 */
    .wechat-article {{
        font-family: {font_family};
        font-size: {body_size};
        color: {text_color};
        line-height: {line_height};
        max-width: 100%;
        word-wrap: break-word;
    }}
    .wechat-article h1 {{
        font-size: {title_size};
        font-weight: 700;
        color: {text_color};
        margin: 1.2em 0 0.6em;
        letter-spacing: 0.5px;
    }}
    .wechat-article h2 {{
        font-size: {heading_size};
        font-weight: 600;
        color: {primary_color};
        margin: 1em 0 0.5em;
        padding-left: 10px;
        border-left: {blockquote_border_width} solid {primary_color};
    }}
    .wechat-article h3 {{
        font-size: 17px;
        font-weight: 600;
        color: {text_color};
        margin: 0.8em 0 0.4em;
    }}
    .wechat-article p {{
        margin: {paragraph_spacing} 0;
        text-align: justify;
    }}
    .wechat-article strong {{
        color: {primary_color};
        font-weight: 600;
    }}
    .wechat-article blockquote {{
        border-left: {blockquote_border_width} solid {primary_color};
        background: {blockquote_bg};
        margin: 0.8em 0;
        padding: 10px 16px;
        border-radius: 0 4px 4px 0;
        color: #5a5a5a;
        font-size: 15px;
    }}
    .wechat-article .info-card {{
        background: {accent_color};
        border-left: {blockquote_border_width} solid {primary_color};
        padding: {card_padding};
        margin: 0.8em 0;
        border-radius: {card_radius};
    }}
    .wechat-article .highlight-box {{
        background: linear-gradient(135deg, #fff9e6, #fff3cd);
        border: 1px solid #ffe08a;
        padding: {card_padding};
        margin: 0.8em 0;
        border-radius: {card_radius};
        font-size: {caption_size};
        color: #6d5600;
    }}
    .wechat-article .data-callout {{
        background: {card_bg};
        border: 1px solid {border_color};
        padding: {card_padding};
        margin: 0.8em 0;
        border-radius: {card_radius};
    }}
    .wechat-article .data-callout-title {{
        font-size: {caption_size};
        color: {muted_color};
        margin-bottom: 4px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    .wechat-article .closing-card {{
        background: {card_bg};
        border: 1px dashed {border_color};
        padding: {card_padding};
        margin: 1.2em 0;
        border-radius: {card_radius};
        text-align: center;
        color: {muted_color};
        font-size: {caption_size};
    }}
    .wechat-article .divider-line {{
        text-align: center;
        margin: 1.2em 0;
        color: {border_color};
        font-size: 12px;
        letter-spacing: 8px;
    }}
    .wechat-article img {{
        max-width: 100%;
        height: auto;
        border-radius: 4px;
    }}
    .wechat-article .image-caption {{
        text-align: center;
        font-size: {caption_size};
        color: {muted_color};
        margin-top: 4px;
    }}
    .wechat-article code {{
        background: #f2f4f5;
        padding: 2px 6px;
        border-radius: 3px;
        font-size: 90%;
        font-family: "SF Mono", "Menlo", "Consolas", monospace;
    }}
    .wechat-article pre {{
        background: #f6f8fa;
        padding: 12px;
        border-radius: 6px;
        overflow-x: auto;
        font-size: 14px;
        line-height: 1.5;
    }}
    .wechat-article ol, .wechat-article ul {{
        padding-left: 1.5em;
        margin: 0.5em 0;
    }}
    .wechat-article li {{
        margin-bottom: 0.3em;
    }}
    """

    def css(self) -> str:
        return self.DEFAULT_THEME_CSS.format(
            font_family=self.font_family,
            body_size=self.body_size,
            text_color=self.text_color,
            line_height=self.line_height,
            title_size=self.title_size,
            heading_size=self.heading_size,
            primary_color=self.primary_color,
            blockquote_border_width=self.blockquote_border_width,
            blockquote_bg=self.blockquote_bg,
            paragraph_spacing=self.paragraph_spacing,
            accent_color=self.accent_color,
            card_padding=self.card_padding,
            card_radius=self.card_radius,
            muted_color=self.muted_color,
            caption_size=self.caption_size,
            card_bg=self.card_bg,
            border_color=self.border_color,
        )


# ============================================================================
# 自定义组件渲染器
# ============================================================================

class WechatComponentRenderer:
    """将自定义 Markdown 扩展语法渲染为微信内联 HTML 组件"""

    # 组件正则模式
    BLOCK_PATTERNS = [
        # :::info ... :::
        (re.compile(r'^:::info\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "info_card"),
        # :::warning ... :::
        (re.compile(r'^:::warning\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "highlight_box"),
        # :::data ... :::
        (re.compile(r'^:::data\s*\n(.*?)\n:::', re.DOTALL | re.MULTILINE), "data_callout"),
        # ```highlight ... ```
        (re.compile(r'^```highlight\s*\n(.*?)```', re.DOTALL | re.MULTILINE), "highlight_box"),
        # >> 金句引用
        (re.compile(r'^>>\s+(.+?)$', re.MULTILINE), "pullquote"),
        # --- 分割线
        (re.compile(r'^---$', re.MULTILINE), "divider"),
    ]

    @classmethod
    def render_components(cls, markdown: str) -> str:
        """将自定义 Markdown 扩展转换为 HTML"""
        result = markdown

        for pattern, component_type in cls.BLOCK_PATTERNS:
            if component_type == "info_card":
                def info_replacer(m):
                    content = m.group(1).strip()
                    return f'<section class="info-card">\n{content}\n</section>'
                result = pattern.sub(info_replacer, result)

            elif component_type == "highlight_box":
                def highlight_replacer(m):
                    content = m.group(1).strip()
                    return f'<section class="highlight-box">\n{content}\n</section>'
                result = pattern.sub(highlight_replacer, result)

            elif component_type == "data_callout":
                def data_replacer(m):
                    content = m.group(1).strip()
                    # Extract title from first line if it starts with #
                    lines = content.split("\n", 1)
                    title_html = ""
                    body = content
                    if lines[0].startswith("# "):
                        title = lines[0][2:].strip()
                        title_html = f'<div class="data-callout-title">{html.escape(title)}</div>'
                        body = lines[1].strip() if len(lines) > 1 else ""
                    return f'<section class="data-callout">\n{title_html}\n{body}\n</section>'
                result = pattern.sub(data_replacer, result)

            elif component_type == "pullquote":
                def pullquote_replacer(m):
                    content = m.group(1).strip()
                    return f'<blockquote><strong>✦</strong> {content}</blockquote>'
                result = pattern.sub(pullquote_replacer, result)

            elif component_type == "divider":
                result = pattern.sub('<p class="divider-line">· · ·</p>', result)

        return result

    @classmethod
    def add_closing_card(cls, html_content: str, brand_name: str = "") -> str:
        """在文章末尾添加互动卡片"""
        card = f"""<section class="closing-card">
<p><strong>✳ 以上为本文全部内容</strong></p>
<p>如果你觉得有收获，欢迎<span style="color: #1a6fb5;">点赞</span>、<span style="color: #1a6fb5;">在看</span>、<span style="color: #1a6fb5;">转发</span></p>
<p style="font-size: 12px; color: #aaa; margin-top: 8px;">{brand_name} · 深度行业观察</p>
</section>"""
        return html_content.rstrip() + "\n" + card


# ============================================================================
# CSS Inline 转换器
# ============================================================================

class CssInliner:
    """将 HTML 中的 <style> 块转换为内联样式（微信编辑器只认内联样式）"""

    @classmethod
    def convert(cls, html_content: str, theme: WechatTheme | None = None) -> str:
        """将 class-based CSS 转换为内联 style 属性

        采用简单的 CSS 解析器（无需 premailer 等重依赖），
        支持微信富文本编辑器的常见场景。
        """
        if theme is None:
            theme = WechatTheme()

        # 提取所有 CSS 规则
        css_text = theme.css()
        rules = cls._parse_css_rules(css_text)

        # 简单策略：包裹外层 div，对已知 class 应用内联样式
        wrapped = f'<section style="font-family: {theme.font_family}; font-size: {theme.body_size}; color: {theme.text_color}; line-height: {theme.line_height}; max-width: 100%; word-wrap: break-word;">\n{html_content}\n</section>'

        # 对已知的 class 进行内联样式注入
        for selector, styles in rules.items():
            class_name = selector.lstrip(".")
            # 替换 class="xxx" 为 class="xxx" style="..."
            wrapped = cls._inject_inline_style(wrapped, class_name, styles)

        return wrapped

    @classmethod
    def _parse_css_rules(cls, css_text: str) -> dict[str, str]:
        """极简 CSS 解析：提取 .classname { ... } 规则"""
        rules: dict[str, str] = {}
        # 匹配选择器块
        pattern = re.compile(
            r'([^{]+)\s*\{([^}]*)\}',
            re.DOTALL | re.MULTILINE,
        )
        for match in pattern.finditer(css_text):
            selectors_raw = match.group(1).strip()
            properties = match.group(2).strip()

            # 只处理以 . 开头的类选择器
            for selector in selectors_raw.split(","):
                selector = selector.strip()
                if selector.startswith("."):
                    clean_selector = selector.split()[0]  # 去掉伪类等
                    rules[clean_selector] = "; ".join(
                        p.strip().rstrip(";")
                        for p in properties.split(";")
                        if p.strip()
                    )

        return rules

    @classmethod
    def _inject_inline_style(cls, html_content: str, class_name: str, styles: str) -> str:
        """为指定 class 的元素注入内联样式"""
        # 匹配 class="xxx" 或 class="xxx yyy"
        pattern = re.compile(
            rf'(<[^>]*class\s*=\s*["\'])([^"\']*\b{class_name}\b[^"\']*)(["\'][^>]*>)',
            re.IGNORECASE,
        )
        return pattern.sub(
            rf'\1\2\3 style="{html.escape(styles)}"',
            html_content,
        )


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
            self._token_expires = time.time() + expires_in - 300  # 提前 5 分钟刷新
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
        """通过 API 创建微信公众号草稿

        参考文档: https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
        """
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
        """上传图片到微信素材库，返回 media_id 和 url"""
        if not self.available:
            return {"status": "error", "error": "API not configured"}

        # 注意：微信草稿/图文消息中的图片需要先用草稿素材接口上传
        # 这里使用的是永久素材接口作为简化实现
        # 生产环境建议使用 draft/material 专用接口
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
# Markdown → 微信 HTML 转换器（完整流程）
# ============================================================================

class MarkdownToWechatConverter:
    """将 Markdown 文章转换为微信兼容的 HTML（含组件渲染 + CSS Inline）"""

    def __init__(self, theme: WechatTheme | None = None, brand_name: str = ""):
        self.theme = theme or WechatTheme()
        self.brand_name = brand_name

    def convert(self, markdown: str, *, add_closing: bool = True) -> str:
        """完整转换流程：组件 → 基础 HTML → CSS Inline → 闭合卡片"""
        # Step 1: 渲染自定义组件
        html_with_components = WechatComponentRenderer.render_components(markdown)

        # Step 2: 基础 Markdown → HTML
        html_base = self._markdown_to_html(html_with_components)

        # Step 3: CSS Inline 转换
        html_inlined = CssInliner.convert(html_base, self.theme)

        # Step 4: 添加互动卡片
        if add_closing:
            html_inlined = self._append_closing(html_inlined)

        return html_inlined

    def _markdown_to_html(self, text: str) -> str:
        """简易 Markdown → HTML 转换（兼容微信），处理标题、段落、加粗、链接、列表等"""
        lines = text.strip().split("\n")
        html_lines: list[str] = []
        in_list = False
        list_type = ""

        for line in lines:
            stripped = line.strip()

            # 空行
            if not stripped:
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                continue

            # 已经是 HTML 块（section/blockquote）
            if stripped.startswith("<section") or stripped.startswith("<blockquote"):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                html_lines.append(line)
                continue

            # 关闭标签
            if stripped.startswith("</section") or stripped.startswith("</blockquote"):
                html_lines.append(line)
                continue

            # 标题
            if stripped.startswith("### "):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                html_lines.append(f"<h3>{self._inline_format(stripped[4:])}</h3>")
                continue
            if stripped.startswith("## "):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                html_lines.append(f"<h2>{self._inline_format(stripped[3:])}</h2>")
                continue
            if stripped.startswith("# "):
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                html_lines.append(f"<h1>{self._inline_format(stripped[2:])}</h1>")
                continue

            # 无序列表
            if re.match(r'^[\-\*]\s+', stripped):
                content = re.sub(r'^[\-\*]\s+', '', stripped)
                if not in_list:
                    in_list = True
                    list_type = "ul"
                    html_lines.append(f"<{list_type}>")
                html_lines.append(f"<li>{self._inline_format(content)}</li>")
                continue

            # 有序列表
            if re.match(r'^\d+\.\s+', stripped):
                content = re.sub(r'^\d+\.\s+', '', stripped)
                if not in_list or list_type != "ol":
                    if in_list:
                        html_lines.append(f"</{list_type}>")
                    in_list = True
                    list_type = "ol"
                    html_lines.append(f"<{list_type}>")
                html_lines.append(f"<li>{self._inline_format(content)}</li>")
                continue

            # 分割线
            if stripped == "---" or stripped == "***":
                if in_list:
                    html_lines.append(f"</{list_type}>")
                    in_list = False
                html_lines.append('<p class="divider-line">· · ·</p>')
                continue

            # 普通段落
            if in_list:
                html_lines.append(f"</{list_type}>")
                in_list = False
            html_lines.append(f"<p>{self._inline_format(stripped)}</p>")

        if in_list:
            html_lines.append(f"</{list_type}>")

        return "\n".join(html_lines)

    def _inline_format(self, text: str) -> str:
        """处理行内格式：加粗、斜体、链接、代码"""
        # 加粗 **text**
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # 行内代码 `code`
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        # 链接 [text](url)
        text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', text)
        # 图片 ![alt](url)
        text = re.sub(
            r'!\[([^\]]*)\]\(([^\)]+)\)',
            r'<img src="\2" alt="\1"><p class="image-caption">\1</p>',
            text,
        )
        return text

    def _append_closing(self, html_content: str) -> str:
        return WechatComponentRenderer.add_closing_card(html_content, self.brand_name)


# ============================================================================
# 顶层便捷函数
# ============================================================================

def convert_markdown_to_wechat_html(
    markdown: str,
    *,
    theme: WechatTheme | None = None,
    brand_name: str = "",
    add_closing: bool = True,
) -> str:
    """Markdown → 微信排版 HTML（一行调用完成全部转换）"""
    converter = MarkdownToWechatConverter(theme=theme, brand_name=brand_name)
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
