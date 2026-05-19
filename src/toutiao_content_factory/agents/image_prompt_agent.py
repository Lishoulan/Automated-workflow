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
        summary = content.summary.strip() or content.article_angle.strip() or "围绕项目、订单、交付与利润兑现展开"
        title = content.recommended_titles[0] if content.recommended_titles else f"{keyword}进入兑现阶段"

        cover_prompt = (
            f"今日头条科技分析封面图，16:9 横版，主标题“{title}”，副标题“重点看{secondary}”，"
            f"深蓝黑背景，冷色科技光效，保留大面积留白，只放少量清晰中文大字，"
            f"主体用数据面板、趋势折线、产业链节点高亮表达，不要人物，不要海报拼贴，不要密集小字。"
            f"补充信息：{summary}。风格要求：{style.image_instruction}"
        )
        infographic_prompts = [
            f"正文配图 1：竖版数据卡信息图，主题是“{keyword}核心数据与项目进展”，突出 {numbers}，"
            f"使用 2 到 4 个数据卡片、1 个简洁柱状图或折线图，深色科技风，少量中文短标签，移动端易读。",
            f"正文配图 2：竖版产业链判断图，主题是“{keyword}产业链谁受益、谁承压”，包含 {companies}，"
            f"用流程箭头、上下游模块和利润迁移示意表达，避免长段正文，避免人物海报。",
            f"备用图：围绕“{summary}”制作一图看懂信息图，强调项目、订单、交付、毛利四个判断模块，深色科技媒体风。",
        ]
        return {
            "cover_prompt": cover_prompt,
            "infographic_prompts": infographic_prompts,
        }

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item) for item in payload])[:limit]
