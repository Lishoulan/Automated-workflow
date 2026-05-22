from __future__ import annotations

import json
import logging

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import AIRequestError
from toutiao_content_factory.models import ExtractionResult, GeneratedContent
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class ContentGenerator:
    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.prompt_repository = prompt_repository
        self.openai_service = openai_service
        self.logger = logger

    def generate(self, extraction: ExtractionResult) -> GeneratedContent:
        if not self.openai_service.available:
            self.logger.warning("未配置 OpenAI API Key 或启用 dry-run，使用本地草稿模式。")
            return self._build_local_draft(extraction)

        analysis_payload = self._generate_analysis(extraction)
        return self._generate_article(extraction, analysis_payload)

    def _generate_analysis(self, extraction: ExtractionResult) -> dict:
        prompt = self.prompt_repository.render(
            "analysis_prompt.md",
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            source_digest=self._format_source_digest(extraction),
            signals_json=json.dumps(self._extraction_to_dict(extraction), ensure_ascii=False, indent=2),
        )
        system_prompt = "你是科技产业研究编辑。只输出 JSON。"
        return self.openai_service.create_json_completion(system_prompt, prompt)

    def _generate_article(self, extraction: ExtractionResult, analysis_payload: dict) -> GeneratedContent:
        prompt = self.prompt_repository.render(
            "generation_prompt.md",
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            article_word_target=str(self.settings.article_word_target),
            article_angle=str(analysis_payload.get("article_angle", "从产业链兑现和增长质量切入")),
            analysis_json=json.dumps(analysis_payload, ensure_ascii=False, indent=2),
        )
        system_prompt = "你是中文科技行业内容主编。只输出 JSON。"

        try:
            payload = self.openai_service.create_json_completion(system_prompt, prompt)
        except AIRequestError:
            raise

        return GeneratedContent(
            titles=self._ensure_list(payload.get("titles"), 20, fallback=self._fallback_titles(extraction)),
            recommended_titles=self._ensure_list(
                payload.get("recommended_titles"), 3, fallback=self._fallback_titles(extraction)[:3]
            ),
            article=str(payload.get("article", self._build_article_body(extraction))),
            comment_prompt=str(payload.get("comment_prompt", "你更看好产业链里的哪个环节？欢迎留言讨论。")),
            summary=str(payload.get("summary", self._build_summary(extraction))),
            tags=self._ensure_list(payload.get("tags"), 6, fallback=self._fallback_tags(extraction)),
            cover_prompt="",
            infographic_prompts=[],
            review_notes=self._ensure_list(payload.get("review_notes"), 3, fallback=[]),
        )

    def _build_local_draft(self, extraction: ExtractionResult) -> GeneratedContent:
        titles = self._fallback_titles(extraction)
        return GeneratedContent(
            titles=titles,
            recommended_titles=titles[:3],
            article=self._build_article_body(extraction),
            comment_prompt="如果你只能盯一个产业链环节，你会选哪一段？欢迎在评论区聊聊。",
            summary=self._build_summary(extraction),
            tags=self._fallback_tags(extraction),
            cover_prompt="",
            infographic_prompts=[],
            review_notes=[
                "建议人工再补一条更鲜明的观点句。",
                "如有原始报告来源，建议在发文前补充出处。",
                "如果要提高收藏率，可增加一段‘重点观察指标’。",
            ],
        )

    def _extraction_to_dict(self, extraction: ExtractionResult) -> dict:
        return {
            "core_data": extraction.core_data,
            "industry_trends": extraction.industry_trends,
            "growth_numbers": extraction.growth_numbers,
            "companies": extraction.companies,
            "supply_chain_keywords": extraction.supply_chain_keywords,
            "key_points": extraction.key_points,
            "source_summaries": extraction.source_summaries,
        }

    def _format_source_digest(self, extraction: ExtractionResult) -> str:
        lines: list[str] = []
        for item in extraction.source_summaries:
            lines.append(
                f"- {item['title']} [{item['source_type']}] {item['preview']}"
            )
        return "\n".join(lines)

    def _build_article_body(self, extraction: ExtractionResult) -> str:
        headline = self._fallback_titles(extraction)[0]
        core_data = extraction.core_data[:4] or ["目前材料里最强的信号集中在增长数字和交付节奏变化。"]
        trends = extraction.industry_trends[:4] or ["行业正在从讲故事阶段进入验证与兑现阶段。"]
        key_points = extraction.key_points[:4] or ["值得持续跟踪订单、验证和交付三组指标。"]

        core_data_block = "\n".join(f"- {item}" for item in core_data)
        trend_block = "\n".join(f"- {item}" for item in trends)
        key_point_block = "\n".join(f"- {item}" for item in key_points)

        return (
            f"# {headline}\n\n"
            "不是所有高增长，最后都能变成高质量利润。\n\n"
            "最近这批材料最值得看的，不是热度本身，而是增长开始往订单、验证和交付环节传导。\n\n"
            "## 核心数据\n\n"
            f"{core_data_block}\n\n"
            "## 行业分析\n\n"
            "如果只看新闻标题，很容易把行业升温理解成普涨逻辑。但真正先受益的，通常是卡住关键零部件、系统集成和交付效率的公司。\n\n"
            f"{trend_block}\n\n"
            "## 趋势判断\n\n"
            f"{key_point_block}\n\n"
            "接下来更值得盯的是三个指标：新增订单、样机转量产速度、海外或本地化交付能力。\n\n"
            "## 结尾互动\n\n"
            "你更看好这条产业链里的哪个环节先兑现业绩？欢迎留言交流。"
        )

    def _build_summary(self, extraction: ExtractionResult) -> str:
        focus = "、".join(extraction.supply_chain_keywords[:4] or ["科技制造"])
        number = extraction.growth_numbers[0] if extraction.growth_numbers else "增长加速"
        return f"围绕 {focus} 的产业链正在进入验证和兑现阶段，{number} 只是开始，真正决定竞争力的是卡位能力与交付效率。"

    def _fallback_titles(self, extraction: ExtractionResult) -> list[str]:
        keyword = extraction.supply_chain_keywords[0] if extraction.supply_chain_keywords else "科技制造"
        secondary = extraction.supply_chain_keywords[1] if len(extraction.supply_chain_keywords) > 1 else "产业链"
        number = extraction.growth_numbers[0] if extraction.growth_numbers else "增长提速"

        templates = [
            f"{keyword}热度上来后，真正先受益的是哪些环节？",
            f"{number}背后，{keyword}产业链开始分化了",
            f"看懂{keyword}这一轮升温，要先看{secondary}",
            f"{keyword}赛道升温，为什么别只盯整机？",
            f"{keyword}进入验证加速期，最先兑现业绩的是谁？",
            f"{keyword}热度之外，产业链卡位已经开始了",
            f"{keyword}数据很好看，但利润不会平均分配",
            f"一篇看懂{keyword}产业链最新变化",
            f"{keyword}和{secondary}，谁是这轮行情的关键抓手？",
            f"{keyword}扩产提速后，哪些公司更容易跑出来？",
            f"{keyword}进入下半场，交付能力比故事更重要",
            f"{number}只是表象，{keyword}真正看这三件事",
            f"{keyword}火了以后，市场最先奖励哪类公司？",
            f"{keyword}增长开始传导，哪些环节值得重点跟踪？",
            f"{keyword}赛道最值得盯的，不只是龙头公司",
            f"{keyword}订单和验证同步提速，下一步看什么？",
            f"{keyword}正在变热，别忽略{secondary}这段机会",
            f"{keyword}逻辑没有结束，但赚钱方式已经变了",
            f"{keyword}行业升温后，先别急着追，先看产业链结构",
            f"{keyword}从热度走向兑现，核心变量已经浮出水面",
        ]
        return templates[:20]

    def _fallback_tags(self, extraction: ExtractionResult) -> list[str]:
        tags = [f"#{item}" for item in extraction.supply_chain_keywords[:5]]
        return tags or ["#科技", "#产业链", "#行业分析"]

    def _build_cover_prompt(self, extraction: ExtractionResult) -> str:
        keyword = extraction.supply_chain_keywords[0] if extraction.supply_chain_keywords else "科技制造"
        secondary = extraction.supply_chain_keywords[1] if len(extraction.supply_chain_keywords) > 1 else "产业链"
        return (
            f"深色科技风头条封面，16:9，中文主标题“{keyword}进入兑现阶段”，"
            f"副标题“重点看{secondary}”，蓝黑色背景，数据流、芯片纹理、信息图布局，"
            "画面高级、适合今日头条科技内容。"
        )

    def _build_infographic_prompts(self, extraction: ExtractionResult) -> list[str]:
        keyword = extraction.supply_chain_keywords[0] if extraction.supply_chain_keywords else "科技制造"
        companies = "、".join(extraction.companies[:4]) or "头部企业"
        numbers = "、".join(extraction.growth_numbers[:3]) or "关键增长数字"
        return [
            f"信息图：{keyword}产业链结构图，深色科技风，中文标签，突出上游、中游、下游关键环节。",
            f"一图看懂：{keyword}核心公司与卡位环节，包含 {companies}，移动端友好，数据卡片布局。",
            f"数据图：展示 {numbers} 的对比图表，深色背景，蓝紫高亮，适合头条封面或配图。",
        ]

    def _ensure_list(self, value: object, expected_size: int, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned[:expected_size] or fallback
        return fallback
