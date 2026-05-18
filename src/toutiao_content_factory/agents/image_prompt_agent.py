from __future__ import annotations

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import DataReport, GeneratedContent


class ImagePromptAgent(BaseAgent):
    agent_name = "ImagePromptAgent"

    def run(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        style: StyleProfile,
    ) -> dict[str, object]:
        prompt = self.render_prompt(
            "image_prompt.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            style_digest=self.style_digest(style),
            image_direction=style.image_instruction,
            summary=content.summary,
            tags_json=self.json_text(content.tags),
            data_json=self.json_text(
                {
                    "growth_numbers": data_report.growth_numbers,
                    "companies": data_report.companies,
                    "supply_chain_keywords": data_report.supply_chain_keywords,
                    "watch_metrics": data_report.watch_metrics,
                }
            ),
        )
        payload = self.request_json("你是 ImagePromptAgent，只输出 JSON。", prompt)
        if payload:
            return {
                "cover_prompt": str(payload.get("cover_prompt", "")).strip(),
                "infographic_prompts": self._clean_list(payload.get("infographic_prompts"), 3),
            }
        return self._fallback(content, data_report, style)

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        style: StyleProfile,
    ) -> dict[str, object]:
        keyword = data_report.supply_chain_keywords[0] if data_report.supply_chain_keywords else "科技制造"
        secondary = data_report.supply_chain_keywords[1] if len(data_report.supply_chain_keywords) > 1 else "产业链"
        numbers = "、".join(data_report.growth_numbers[:3]) or "关键增长数据"
        companies = "、".join(data_report.companies[:4]) or "头部企业"

        cover_prompt = (
            f"深色科技风头条封面，16:9，中文主标题“{keyword}进入兑现阶段”，"
            f"副标题“重点看{secondary}”，蓝黑色背景，数据流、图表层、产业链节点高亮，"
            f"风格要求：{style.image_instruction}"
        )
        infographic_prompts = [
            f"信息图：{keyword}产业链结构图，突出上游、中游、下游关键环节，深色科技风，中文标签。",
            f"一图看懂：{keyword}关键公司与卡位位置，包含 {companies}，移动端友好，数据卡片布局。",
            f"数据图：展示 {numbers} 的对比图表，蓝紫高亮，适合头条科技文章配图。",
        ]
        return {
            "cover_prompt": cover_prompt,
            "infographic_prompts": infographic_prompts,
        }

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item) for item in payload])[:limit]
