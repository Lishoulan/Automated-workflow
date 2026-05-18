from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import DataReport, ExtractionResult, TrendReport


class DataAgent(BaseAgent):
    agent_name = "DataAgent"

    def run(
        self,
        extraction: ExtractionResult,
        trend_report: TrendReport,
        style: StyleProfile,
    ) -> DataReport:
        prompt = self.render_prompt(
            "data_prompt_v2.md",
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            style_digest=self.style_digest(style),
            brand_digest=self.brand_digest(),
            source_digest=self._format_source_digest(extraction),
            signals_json=self.json_text(self._extraction_payload(extraction)),
            trend_json=self.json_text(trend_report.__dict__),
        )
        payload = self.request_json("你是 DataAgent，只输出 JSON。", prompt)
        if payload:
            merged = dict(payload)
            merged["topic_title"] = str(merged.get("topic_title") or extraction.topic_title)
            merged["selection_reason"] = str(merged.get("selection_reason") or extraction.selection_reason)
            merged["suggested_angle"] = str(merged.get("suggested_angle") or extraction.suggested_angle)
            merged["follow_up_focus"] = str(merged.get("follow_up_focus") or extraction.follow_up_focus)
            merged["writing_brief"] = str(merged.get("writing_brief") or extraction.writing_brief)
            merged["focus_industry"] = self.clean_publish_text(
                str(merged.get("focus_industry") or extraction.focus_industry)
            )
            merged["focus_keywords"] = self.clean_publish_list(
                list(merged.get("focus_keywords") or extraction.focus_keywords),
                limit=6,
            )
            merged["focus_companies"] = self.clean_publish_list(
                list(merged.get("focus_companies") or extraction.focus_companies),
                limit=4,
            )
            merged["companies"] = self.clean_publish_list(
                list(merged.get("companies") or extraction.companies),
                limit=8,
            )
            merged["supply_chain_keywords"] = self.clean_publish_list(
                list(merged.get("supply_chain_keywords") or extraction.supply_chain_keywords),
                limit=8,
            )
            merged["core_data"] = self.clean_publish_list(list(merged.get("core_data") or extraction.core_data), limit=6)
            merged["industry_trends"] = self.clean_publish_list(
                list(merged.get("industry_trends") or extraction.industry_trends),
                limit=6,
            )
            merged["growth_numbers"] = self.clean_publish_list(
                list(merged.get("growth_numbers") or extraction.growth_numbers),
                limit=8,
            )
            merged["key_points"] = self.clean_publish_list(list(merged.get("key_points") or extraction.key_points), limit=6)
            merged["watch_metrics"] = self.clean_publish_list(
                list(merged.get("watch_metrics") or self._build_watch_metrics(
                    merged["focus_industry"] or extraction.focus_industry,
                    (merged["focus_keywords"][1] if len(merged["focus_keywords"]) > 1 else "关键环节"),
                    merged["follow_up_focus"],
                )),
                limit=5,
            )
            merged["evidence_notes"] = self.clean_publish_list(
                list(merged.get("evidence_notes") or self._build_evidence_notes(extraction)),
                limit=5,
            )
            merged["risk_flags"] = self.clean_publish_list(
                list(merged.get("risk_flags") or self._build_risk_flags(
                    merged["focus_industry"] or extraction.focus_industry,
                    (merged["focus_keywords"][1] if len(merged["focus_keywords"]) > 1 else "关键环节"),
                    trend_report,
                    merged["supply_chain_keywords"] or extraction.supply_chain_keywords,
                )),
                limit=4,
            )
            return DataReport.from_dict(merged)
        return self._fallback(extraction, trend_report)

    def _fallback(self, extraction: ExtractionResult, trend_report: TrendReport) -> DataReport:
        keywords = self.clean_publish_list(
            extraction.focus_keywords[:5] or extraction.supply_chain_keywords[:5] or ["科技制造", "产业链"],
            limit=5,
        )
        primary = self.clean_publish_text(extraction.focus_industry.strip() or keywords[0])
        secondary = keywords[1] if len(keywords) > 1 else "关键环节"
        watch_metrics = self._build_watch_metrics(primary, secondary, extraction.follow_up_focus)
        evidence_notes = self._build_evidence_notes(extraction)
        risk_flags = self._build_risk_flags(primary, secondary, trend_report, keywords)

        return DataReport(
            core_data=self.clean_publish_list(extraction.core_data[:6], limit=6),
            industry_trends=self.clean_publish_list(extraction.industry_trends[:6], limit=6),
            growth_numbers=self.clean_publish_list(extraction.growth_numbers[:8], limit=8),
            companies=self.clean_publish_list(extraction.companies[:8], limit=8),
            supply_chain_keywords=keywords,
            key_points=self.clean_publish_list(extraction.key_points[:6], limit=6),
            watch_metrics=watch_metrics,
            evidence_notes=evidence_notes,
            risk_flags=risk_flags,
            focus_industry=primary,
            focus_keywords=self.clean_publish_list(extraction.focus_keywords[:6], limit=6),
            focus_companies=self.clean_publish_list(extraction.focus_companies[:4], limit=4),
            topic_title=extraction.topic_title,
            selection_reason=extraction.selection_reason,
            suggested_angle=extraction.suggested_angle,
            follow_up_focus=extraction.follow_up_focus,
            writing_brief=extraction.writing_brief,
        )

    def _build_watch_metrics(self, primary: str, secondary: str, follow_up_focus: str) -> list[str]:
        if follow_up_focus.strip():
            items = [self.clean_publish_text(item) for item in re.split(r"[、，,]", follow_up_focus) if item.strip()]
            if items:
                return self.unique(items)[:5]
        delivery_metric = "样机转量产速度" if any(token in primary for token in ("机器人", "装备")) else "项目交付节奏"
        candidates = [
            "新增订单和中标数量",
            delivery_metric,
            "重点客户验证进度",
            "海外与本地化服务进展",
            f"{secondary}环节毛利率变化",
        ]
        return self.unique(candidates)[:5]

    def _build_evidence_notes(self, extraction: ExtractionResult) -> list[str]:
        notes: list[str] = []
        for item in extraction.evidence_items[:4]:
            fact = str(item.get("fact", "")).strip()
            source = str(item.get("source", "")).strip()
            if not fact:
                continue
            if source:
                notes.append(f"{source}：{fact}")
            else:
                notes.append(f"证据锚点：{fact}")

        if extraction.growth_numbers and not notes:
            notes.append(f"关键数据：{'、'.join(extraction.growth_numbers[:3])}")

        for summary in extraction.source_summaries[:2]:
            title = summary.get("title", "").strip()
            if title:
                notes.append(f"来源摘要：{title}")
        return self.clean_publish_list(notes, limit=5)

    def _build_risk_flags(
        self,
        primary: str,
        secondary: str,
        trend_report: TrendReport,
        keywords: list[str],
    ) -> list[str]:
        risks = [
            f"{primary}热度上升不等于所有环节都能同步兑现。",
            f"如果订单落地慢于扩产或渠道铺设节奏，{secondary}等环节的利润率可能先承压。",
        ]
        if any(token in trend_report.recommended_angle for token in ("海外", "本地化")):
            risks.append("海外项目交付和本地化服务投入可能拉长回款与利润兑现周期。")
        else:
            risks.append("客户验证和项目交付如果延后，行业估值很容易先交易预期再回头修正。")
        if any(token in keywords for token in ("逆变器", "系统集成", "减速器", "电机", "热管理")):
            risks.append(f"{secondary}等关键环节一旦竞争加剧，价格战会先侵蚀毛利。")
        return self.clean_publish_list(risks, limit=4)

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
