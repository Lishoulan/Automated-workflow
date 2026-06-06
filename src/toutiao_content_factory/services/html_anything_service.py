"""html-anything 排版后端 — 通过本地 html-anything 服务的 /api/convert 接口生成微信公众号排版 HTML。

html-anything 是一个开源项目 (https://github.com/nexu-io/html-anything)，提供 75 套
skill 模板，支持杂志文章、PPT、海报等多种排版场景，并通过 juice 内联 CSS 实现
微信公众号一键粘贴。

使用前提：
  1. 本地运行 html-anything 服务：pnpm -F @html-anything/next dev
  2. 至少一个本地 AI agent 已登录（Claude Code / Cursor / Codex 等）
  3. 在 config.yaml 中设置 layout_backend: html_anything 或设置环境变量
     WECHAT_LAYOUT_BACKEND=html_anything
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from toutiao_content_factory.config import AppSettings


# 微信公众号文章推荐的模板 ID
_RECOMMENDED_TEMPLATES: dict[str, str] = {
    "deep_industry_analysis": "doc-kami-parchment",
    "tech_media": "doc-kami-parchment",
    "finance_report": "doc-kami-parchment",
    "viral_science": "magazine-poster",
    "default": "doc-kami-parchment",
}


class HtmlAnythingService:
    """封装 html-anything 的 /api/convert SSE 接口，供微信排版流程调用。"""

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        wechat = self.settings.wechat
        backend = str(wechat.layout_backend or "").strip().lower()
        return backend == "html_anything" or bool(wechat.html_anything_enabled)

    @property
    def available(self) -> bool:
        """检查 html-anything 服务是否可达。"""
        if not self.enabled:
            return False
        try:
            url = self._base_url.rstrip("/") + "/api/agents"
            req = Request(url, method="GET")
            with urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # 核心转换
    # ------------------------------------------------------------------

    def convert(self, markdown: str, *, style: str = "") -> dict[str, Any]:
        """将 Markdown 通过 html-anything 转换为微信兼容 HTML。

        Parameters
        ----------
        markdown : str
            待转换的 Markdown 文本。
        style : str
            写作风格（如 deep_industry_analysis），用于自动选择模板。

        Returns
        -------
        dict[str, Any]
            至少包含 ``html`` 和 ``provider`` 字段。
        """
        if not self.enabled:
            raise RuntimeError(
                "html-anything is not enabled. "
                "Set WECHAT_LAYOUT_BACKEND=html_anything or html_anything_enabled=true."
            )

        cleaned_markdown = str(markdown or "").strip()
        if not cleaned_markdown:
            raise RuntimeError("html-anything conversion received empty markdown.")

        wechat = self.settings.wechat
        base_url = self._base_url
        template_id = self._resolve_template(style)
        agent = str(wechat.html_anything_agent or "claude").strip()

        # 1. 检测可用的 agent
        detected_agent = self._detect_agent(base_url, preferred=agent)
        if detected_agent:
            agent = detected_agent

        # 2. 调用 /api/convert
        payload = {
            "agent": agent,
            "templateId": template_id,
            "content": cleaned_markdown,
            "format": "markdown",
        }

        self.logger.info(
            "html-anything convert: agent=%s template=%s content_len=%d",
            agent, template_id, len(cleaned_markdown),
        )

        html = self._call_convert_api(base_url, payload)
        if not html:
            raise RuntimeError("html-anything returned empty HTML.")

        return {
            "html": html,
            "provider": "html-anything",
            "template_id": template_id,
            "agent": agent,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @property
    def _base_url(self) -> str:
        return str(
            self.settings.wechat.html_anything_url or "http://localhost:3000"
        ).rstrip("/")

    def _resolve_template(self, style: str) -> str:
        """根据写作风格和配置选择模板 ID。"""
        configured = str(self.settings.wechat.html_anything_template or "").strip()
        if configured:
            return configured
        return _RECOMMENDED_TEMPLATES.get(style, _RECOMMENDED_TEMPLATES["default"])

    def _detect_agent(self, base_url: str, *, preferred: str = "claude") -> str:
        """从 /api/agents 端点检测可用的 agent，优先返回 preferred。"""
        try:
            url = f"{base_url}/api/agents"
            req = Request(url, method="GET")
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            self.logger.debug("html-anything agent detection failed: %s", exc)
            return ""

        agents: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    agent_id = str(item.get("id", "") or item.get("name", "")).strip()
                else:
                    agent_id = str(item).strip()
                if agent_id:
                    agents.append(agent_id)
        elif isinstance(data, dict):
            for key in ("agents", "items", "data"):
                items = data.get(key)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            agent_id = str(item.get("id", "") or item.get("name", "")).strip()
                        else:
                            agent_id = str(item).strip()
                        if agent_id:
                            agents.append(agent_id)
                    break

        if not agents:
            return ""

        # 优先匹配 preferred
        for a in agents:
            if preferred.lower() in a.lower():
                return a
        # 否则返回第一个
        return agents[0]

    def _call_convert_api(self, base_url: str, payload: dict[str, Any]) -> str:
        """调用 /api/convert 端点，收集 SSE 流式响应并提取最终 HTML。

        html-anything 的 /api/convert 返回 SSE 流，事件格式：
            event: <type>
            data: <json>

        type 可能是：
          - text_delta: 增量文本
          - done: 生成完成
          - error: 错误

        我们需要收集所有 text_delta 并拼装成完整 HTML。
        """
        url = f"{base_url}/api/convert"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=180) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(
                f"html-anything API error: {exc.code} {err_body}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"html-anything service unreachable at {base_url}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"html-anything API call failed: {exc}") from exc

        # 如果响应是 JSON（非 SSE），直接提取 HTML
        if "text/event-stream" not in content_type:
            try:
                result = json.loads(raw)
                html = self._extract_html_from_json(result)
                if html:
                    return html
            except json.JSONDecodeError:
                pass
            # 如果不是 JSON，可能是直接返回的 HTML
            if self._looks_like_html(raw):
                return raw.strip()
            raise RuntimeError(f"html-anything returned unexpected format: {raw[:200]}")

        # 解析 SSE 流
        html_parts: list[str] = []
        for event_type, event_data in self._parse_sse(raw):
            if event_type == "text_delta":
                text = ""
                if isinstance(event_data, dict):
                    text = str(event_data.get("text", "") or event_data.get("delta", ""))
                elif isinstance(event_data, str):
                    text = event_data
                if text:
                    html_parts.append(text)
            elif event_type == "done":
                break
            elif event_type == "error":
                msg = event_data if isinstance(event_data, str) else json.dumps(event_data, ensure_ascii=False)
                raise RuntimeError(f"html-anything generation error: {msg}")

        full_html = "".join(html_parts)
        if not full_html:
            # SSE 流可能直接包含完整 HTML 而非增量
            if self._looks_like_html(raw):
                return self._extract_html_from_raw(raw)
            raise RuntimeError("html-anything returned no HTML content from SSE stream.")

        return full_html

    @staticmethod
    def _parse_sse(raw: str) -> list[tuple[str, Any]]:
        """解析 SSE 文本流，返回 (event_type, data) 列表。"""
        results: list[tuple[str, Any]] = []
        current_event = ""
        current_data = ""

        for line in raw.split("\n"):
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                current_data = line[5:].strip()
                # 尝试解析 JSON
                try:
                    parsed = json.loads(current_data)
                    results.append((current_event, parsed))
                except json.JSONDecodeError:
                    results.append((current_event, current_data))
                current_event = ""
                current_data = ""
            elif line.strip() == "":
                current_event = ""
                current_data = ""

        return results

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        t = text.strip().lower()
        return "<html" in t or "<!doctype html" in t or "<section" in t

    @staticmethod
    def _extract_html_from_json(payload: dict[str, Any]) -> str:
        """从 JSON 响应中提取 HTML。"""
        for key in ("html", "content", "data"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        nested = payload.get("data")
        if isinstance(nested, dict):
            for key in ("html", "content"):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return ""

    def _extract_html_from_raw(self, raw: str) -> str:
        """从 SSE 原始文本中提取 HTML（当增量解析失败时的后备方案）。"""
        # 尝试找到 HTML 开始和结束标签
        html_match = re.search(r"(<(?:html|!doctype|section)[\s\S]*</html>)", raw, re.IGNORECASE)
        if html_match:
            return html_match.group(1).strip()

        # 尝试从 data 行中提取
        html_parts: list[str] = []
        for line in raw.split("\n"):
            if line.startswith("data:"):
                data_str = line[5:].strip()
                try:
                    parsed = json.loads(data_str)
                    if isinstance(parsed, dict):
                        text = str(parsed.get("text", "") or parsed.get("html", "") or parsed.get("delta", ""))
                        if text:
                            html_parts.append(text)
                except json.JSONDecodeError:
                    pass
        return "".join(html_parts)
