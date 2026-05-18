from __future__ import annotations

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, ExtractionResult, TrendReport


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
        prompt = self.render_prompt(
            "trend_prompt_v2.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            audience=self.settings.audience,
            style_digest=self.style_digest(style),
            brand_digest=self.brand_digest(),
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
