from __future__ import annotations

import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import CacheContext, ExtractionResult, TrendReport
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class TrendAgent(BaseAgent):
    agent_name = "TrendAgent"

    ANGLE_NOISE_PREFIXES = (
        "材料里最强的增长信号包括",
        "材料中提到的关键数据包括",
        "当前更值得写的产业链环节包括",
        "写作要求",
        "请围绕",
    )

    def run(
        self,
        extraction: ExtractionResult,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> TrendReport:
        benchmark_digest = self.benchmark_strategy_digest(
            purpose="trend",
            track_hint=extraction.focus_industry,
            title_hint=extraction.topic_title,
            focus_industry=extraction.focus_industry,
            keywords=extraction.focus_keywords + extraction.supply_chain_keywords,
            angle_hint=extraction.suggested_angle,
        )
        prompt = self.render_prompt(
            "trend_prompt_v2.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            audience=self.settings.audience,
            style_digest=self.style_digest(style),
            brand_digest=self.brand_digest(extra_sections=[benchmark_digest]),
            cache_note=self.cache_note(cache_context),
            source_digest=self._format_source_digest(extraction),
            signals_json=self.json_text(self._extraction_payload(extraction)),
        )
        payload = self.request_json("你是 TrendAgent，只输出 JSON。", prompt)
        if payload:
            return TrendReport.from_dict(payload)
        return self._fallback(extraction, cache_context)

    def _fallback(self, extraction: ExtractionResult, cache_context: CacheContext) -> TrendReport:
        primary = extraction.focus_industry.strip() or (
            extraction.supply_chain_keywords[0] if extraction.supply_chain_keywords else "科技制造"
        )
        secondary = self._secondary_node(extraction, primary, "产业链") or "产业链"
        content_mode = self._content_mode(extraction)

        if content_mode == "social_story":
            hot_trends = self.unique(
                extraction.industry_trends[:4]
                + [
                    f"{primary}引发讨论，核心是公开事实、现实影响与规则边界。",
                    "后续是否有权威回应或正式规则变化，是判断事件走向的关键。",
                ]
            )[:4]
            platform_topics = self.unique(
                [f"{primary}事实梳理", f"{primary}现实影响", "规则边界", "普通人如何判断"]
            )[:5]
            audience_hooks = self.unique(
                [
                    "先把已经确认的事实与网上讨论分开，再谈这件事会影响谁。",
                    "公众建议、媒体讨论和已经生效的政策，不能混为一谈。",
                    "读者真正需要的是事实进展、适用边界和可核验的信息来源。",
                ]
            )[:3]
            recommended_angle = (
                extraction.suggested_angle.strip().rstrip("。")
                if extraction.suggested_angle.strip()
                else f"梳理{primary}已确认的事实、尚未确定的部分，以及普通人需要留意的规则边界"
            )
            return TrendReport(
                hot_trends=hot_trends,
                platform_topics=platform_topics,
                audience_hooks=audience_hooks,
                recommended_angle=recommended_angle,
                timing_judgment="话题仍在讨论期，发布前需要再次核对权威回应和政策状态。",
                competitive_edges=[
                    "按事实、讨论、待确认信息分层表达。",
                    "不虚构当事人，也不把建议写成已经实施的政策。",
                    "给出读者可以自行核验的公开信息入口。",
                ],
            )

        if content_mode == "sports":
            hot_trends = self.unique(
                extraction.industry_trends[:4]
                + [
                    f"{primary}相关内容正从单纯热点讨论转向趋势预期和事后复盘。",
                    f"{secondary}会成为影响后续判断的重要线索。",
                ]
            )[:4]
            platform_topics = self.unique(
                [
                    f"{primary}复盘",
                    f"{primary}争议点",
                    f"{secondary}走势",
                    "技术路线变化",
                    "市场预期",
                ]
            )[:5]
            audience_hooks = self.unique(
                [
                    f"这条{primary}消息真正值得看的，不是表面信息，而是它会不会改写后续判断。",
                    f"{secondary}为什么会影响产业格局、技术路线或舆论风向？",
                    "真正有阅读价值的，不是复述消息，而是把影响解释清楚。",
                ]
            )[:3]
            recommended_angle = extraction.suggested_angle.strip().rstrip("。") if extraction.suggested_angle.strip() else (
                f"这条{primary}热点真正值得写的，不是把新闻复述一遍，而是解释{secondary}背后的胜负手、争议点和后续走势"
            )
            timing_judgment = f"{primary}现在已经进入高讨论窗口，读者更关心后续走势而不是事件本身。"
            competitive_edges = [
                "先解释影响，再复盘消息本身。",
                "把关键人物、关键节点和后续走势说清楚。",
                "尽量避免空翻译和堆背景资料。",
            ]
            return TrendReport(
                hot_trends=hot_trends,
                platform_topics=platform_topics,
                audience_hooks=audience_hooks,
                recommended_angle=recommended_angle,
                timing_judgment=timing_judgment,
                competitive_edges=competitive_edges,
            )

        if content_mode == "international":
            hot_trends = self.unique(
                extraction.industry_trends[:4]
                + [
                    f"{primary}正在从单点消息转向更连续的博弈观察。",
                    f"{secondary}会成为判断局势后续变化的重要线索。",
                ]
            )[:4]
            platform_topics = self.unique(
                [
                    f"{primary}解读",
                    f"{primary}博弈",
                    f"{secondary}变化",
                    "政策变量",
                    "市场反馈",
                ]
            )[:5]
            audience_hooks = self.unique(
                [
                    f"这条{primary}消息真正值得看的，不是表态本身，而是谁在施压、谁在让步。",
                    f"{secondary}为什么会影响下一步谈判、政策动作或市场反应？",
                    "真正有阅读价值的，不是播报消息，而是把博弈关系和影响链条说透。",
                ]
            )[:3]
            recommended_angle = extraction.suggested_angle.strip().rstrip("。") if extraction.suggested_angle.strip() else (
                f"这条{primary}热点真正值得写的，不是事件表面，而是{secondary}背后的利益重新分配和下一步博弈"
            )
            timing_judgment = f"{primary}已经进入需要持续跟踪政策与博弈变量的阶段。"
            competitive_edges = [
                "先解释事件，再解释利益，再解释后续变量。",
                "把政策动作和市场反应连接起来。",
                "避免空喊立场，也不要只做表面播报。",
            ]
            return TrendReport(
                hot_trends=hot_trends,
                platform_topics=platform_topics,
                audience_hooks=audience_hooks,
                recommended_angle=recommended_angle,
                timing_judgment=timing_judgment,
                competitive_edges=competitive_edges,
            )

        hot_trends = self.unique(
            extraction.industry_trends[:4]
            + [
                f"{primary}相关环节正从热点扩散转向验证与兑现。",
                f"{secondary}将成为区分赢家和跟风公司的关键节点。",
            ]
        )[:4]

        platform_topics = self.unique(
            [
                f"{primary}产业链",
                f"{primary}订单兑现",
                f"{secondary}卡位",
                "国产替代",
                "科技制造趋势",
            ]
        )[:5]

        audience_hooks = self.unique(
            [
                f"这轮{primary}升温，最该看的不是热度，而是谁先兑现。",
                f"{secondary}为什么会在下一阶段决定利润分配？",
                "如果只盯整机和概念股，你可能会错过真正的受益环节。",
            ]
        )[:3]

        base_angle = self._choose_angle(extraction, primary, secondary)
        if cache_context.similar_hit and cache_context.avoid_angle:
            recommended_angle = (
                f"避开“{cache_context.avoid_angle}”，改从{secondary}的兑现节奏、交付能力和竞争壁垒切入"
            )
        else:
            recommended_angle = base_angle

        numbers = extraction.growth_numbers[:2]
        timing_judgment = (
            f"{'、'.join(numbers)} 同时出现，说明行业正在从情绪驱动切到数据驱动。"
            if numbers
            else f"{primary}正在从概念热度转向产业兑现。"
        )

        competitive_edges = self.unique(
            [
                "交付能力比故事能力更重要。",
                "系统集成和关键零部件卡位决定下一阶段议价权。",
                "海外与本地化服务能力会拉开订单差距。",
            ]
        )[:3]

        return TrendReport(
            hot_trends=hot_trends,
            platform_topics=platform_topics,
            audience_hooks=audience_hooks,
            recommended_angle=recommended_angle,
            timing_judgment=timing_judgment,
            competitive_edges=competitive_edges,
        )

    def _choose_angle(self, extraction: ExtractionResult, primary: str, secondary: str) -> str:
        content_mode = self._content_mode(extraction)
        if content_mode == "sports":
            if extraction.suggested_angle.strip():
                return extraction.suggested_angle.strip().rstrip("。")
            return f"这条{primary}热点真正值得写的，不是把新闻复述一遍，而是解释{secondary}背后的胜负手、争议点和后续走势"
        if content_mode == "international":
            if extraction.suggested_angle.strip():
                return extraction.suggested_angle.strip().rstrip("。")
            return f"这条{primary}热点真正值得写的，不是事件表面，而是{secondary}背后的利益重新分配和下一步博弈"
        if extraction.suggested_angle.strip():
            return extraction.suggested_angle.strip().rstrip("。")
        points = self._analysis_points(extraction)
        combined = " ".join(points)
        focus_company = self._focus_company(extraction)
        secondary_node = self._secondary_node(extraction, primary, secondary)

        has_validation = self._contains_any(combined, ("验证", "量产", "示范", "商业化", "兑现"))
        has_delivery = self._contains_any(combined, ("交付", "出货", "爬坡", "落地"))
        has_orders = self._contains_any(combined, ("订单", "客户", "签约", "项目"))
        has_overseas = self._contains_any(combined, ("海外", "出海", "本地化"))
        has_profit = self._contains_any(combined, ("利润", "毛利", "盈利", "议价"))

        if has_validation and has_overseas:
            return f"这一轮{primary}已经从示范走向商业验证，接下来要看海外交付和本地化服务谁先兑现"
        if has_validation and secondary_node:
            return f"这一轮{primary}正在进入商业验证阶段，利润会率先向{secondary_node}等关键环节集中"
        if has_orders and has_delivery and secondary_node:
            return f"{primary}热度抬升之后，真正拉开差距的是{secondary_node}等环节的订单兑现和交付能力"
        if has_profit and secondary_node:
            return f"{primary}景气上行之后，市场会重新分配利润，先受益的往往是{secondary_node}等关键环节"
        if focus_company and (has_orders or has_delivery):
            return f"{primary}能不能从热度走向业绩，先看{focus_company}这类主体能否把订单和交付跑通"
        return f"{primary}真正进入业绩阶段之前，最该跟的是订单兑现、交付能力和利润分化"

    def _analysis_points(self, extraction: ExtractionResult) -> list[str]:
        candidates = extraction.key_points + extraction.industry_trends
        points: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text = self._clean_statement(item)
            if not text or text in seen:
                continue
            seen.add(text)
            points.append(text)
        return points

    def _clean_statement(self, text: str) -> str:
        cleaned = text.strip().strip("。；;，, ")
        if not cleaned:
            return ""

        for prefix in ("行业判断：", "关键结论：", "结论：", "证据锚点："):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()

        if not cleaned or cleaned.startswith(self.ANGLE_NOISE_PREFIXES):
            return ""
        return cleaned

    def _secondary_node(self, extraction: ExtractionResult, primary: str, fallback: str = "") -> str:
        candidates = extraction.focus_keywords[1:] + extraction.supply_chain_keywords[1:]
        for item in candidates:
            normalized = item.strip()
            if normalized and normalized not in {primary, "产业链"}:
                return normalized
        return fallback

    def _focus_company(self, extraction: ExtractionResult) -> str:
        for item in extraction.focus_companies + extraction.companies:
            normalized = item.strip()
            if normalized:
                return normalized
        return ""

    def _contains_any(self, text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    def _content_mode(self, extraction: ExtractionResult) -> str:
        text = " ".join(
            item
            for item in (
                extraction.focus_industry,
                extraction.topic_title,
                extraction.suggested_angle,
                extraction.writing_brief,
                " ".join(extraction.focus_keywords[:6]),
                " ".join(extraction.supply_chain_keywords[:6]),
            )
            if item
        ).lower()
        if any(token in text for token in self.SPORTS_TOKENS):
            return "sports"
        if any(token in text for token in self.INTERNATIONAL_TOKENS):
            return "international"
        return "generic"

    def _format_source_digest(self, extraction: ExtractionResult) -> str:
        return "\n".join(
            f"- {item['title']} [{item['source_type']}] {item['preview']}"
            for item in extraction.source_summaries
        )

    def _extraction_payload(self, extraction: ExtractionResult) -> dict:
        return {
            "core_data": extraction.core_data,
            "industry_trends": extraction.industry_trends,
            "growth_numbers": extraction.growth_numbers,
            "companies": extraction.companies,
            "supply_chain_keywords": extraction.supply_chain_keywords,
            "key_points": extraction.key_points,
            "focus_industry": extraction.focus_industry,
            "focus_keywords": extraction.focus_keywords,
            "focus_companies": extraction.focus_companies,
            "topic_title": extraction.topic_title,
            "selection_reason": extraction.selection_reason,
            "suggested_angle": extraction.suggested_angle,
            "follow_up_focus": extraction.follow_up_focus,
            "writing_brief": extraction.writing_brief,
            "evidence_items": extraction.evidence_items,
        }
