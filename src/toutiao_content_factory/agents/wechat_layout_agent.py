from __future__ import annotations

from toutiao_content_factory.agents.base import BaseAgent


class WechatLayoutAgent(BaseAgent):
    agent_name = "WechatLayoutAgent"

    DEFAULT_LAYOUT = {
        "layout_style": "insight_brief",
        "header_variant": "brand_summary",
        "section_heading_style": "badge_number",
        "divider_style": "double_dot",
        "lead_variant": "smart_take",
        "closing_variant": "comment_card",
        "image_frame_style": "editorial_card",
        "component_plan": [
            {"type": "brand_header", "enabled": True},
            {"type": "summary_strip", "enabled": True},
            {"type": "lead_card", "enabled": True},
            {"type": "section_heading", "enabled": True},
            {"type": "data_callout", "enabled": True},
            {"type": "risk_callout", "enabled": True},
            {"type": "inline_visual", "enabled": True},
            {"type": "conclusion_card", "enabled": True},
            {"type": "closing_card", "enabled": True},
            {"type": "footer_badge", "enabled": True},
        ],
        "icon_tokens": {
            "header": "✦",
            "insight": "◉",
            "data": "▣",
            "risk": "△",
            "closing": "✳",
        },
        "tone_keywords": ["专业", "克制", "有信息密度", "轻设计感"],
        "data_callout_title": "核心判断",
        "risk_callout_title": "风险与变量",
        "conclusion_title": "结论先行",
        "closing_title": "留言区 · 一起聊聊",
    }

    def plan_layout(
        self,
        *,
        title: str,
        summary: str,
        article: str,
        account_name: str,
        account_focus: str,
    ) -> dict[str, object]:
        prompt = self.render_prompt(
            "wechat_layout_prompt.md",
            account_name=account_name,
            account_focus=account_focus,
            title=title,
            summary=summary,
            article_excerpt=article[:2200],
        )
        payload = self.request_json("你是微信公众号排版组件设计 Agent，只输出 JSON。", prompt)
        if not payload:
            return dict(self.DEFAULT_LAYOUT)
        return self._normalize_layout_payload(payload)

    def _normalize_layout_payload(self, payload: dict) -> dict[str, object]:
        result = dict(self.DEFAULT_LAYOUT)
        for key in (
            "layout_style",
            "header_variant",
            "section_heading_style",
            "divider_style",
            "lead_variant",
            "closing_variant",
            "image_frame_style",
            "data_callout_title",
            "risk_callout_title",
            "conclusion_title",
            "closing_title",
        ):
            value = str(payload.get(key, "")).strip()
            if value:
                result[key] = value

        component_plan = payload.get("component_plan", [])
        if isinstance(component_plan, list):
            normalized_plan: list[dict[str, object]] = []
            for item in component_plan:
                if not isinstance(item, dict):
                    continue
                component_type = str(item.get("type", "")).strip()
                if not component_type:
                    continue
                normalized_plan.append(
                    {
                        "type": component_type,
                        "enabled": bool(item.get("enabled", True)),
                    }
                )
            if normalized_plan:
                result["component_plan"] = normalized_plan

        icon_tokens = payload.get("icon_tokens", {})
        if isinstance(icon_tokens, dict):
            merged_icons = dict(self.DEFAULT_LAYOUT["icon_tokens"])
            for key, value in icon_tokens.items():
                cleaned_key = str(key).strip()
                cleaned_value = str(value).strip()
                if cleaned_key and cleaned_value:
                    merged_icons[cleaned_key] = cleaned_value
            result["icon_tokens"] = merged_icons

        tone_keywords = payload.get("tone_keywords", [])
        if isinstance(tone_keywords, list):
            cleaned = [str(item).strip() for item in tone_keywords if str(item).strip()]
            if cleaned:
                result["tone_keywords"] = cleaned[:6]

        return result
