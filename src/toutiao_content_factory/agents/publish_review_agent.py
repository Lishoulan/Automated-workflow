from __future__ import annotations

import re
import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import ContentScore, DataReport, GeneratedContent
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class PublishReviewAgent(BaseAgent):
    agent_name = "PublishReviewAgent"
    HOT_TOPIC_MANUAL_MIN_CHARS = 850
    AUTO_PUBLISH_ARTICLE_MIN_CHARS = 1750

    def run(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        style: StyleProfile,
        content_score: ContentScore,
        preflight_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        benchmark_track = self.clean_publish_text(data_report.focus_industry).strip() or self.infer_benchmark_track(
            data_report.topic_title,
            data_report.focus_keywords,
            data_report.supply_chain_keywords,
            content.article_angle,
        )
        self_benchmark_context = self.growth_ops_service.get_self_benchmark_track_context(benchmark_track)
        benchmark_digest = self.growth_ops_service.build_benchmark_digest(benchmark_track, purpose="review")
        prompt = self.render_prompt(
            "publish_review_prompt.md",
            platform=self.settings.platform,
            style_digest=self.style_digest(style, extra_sections=[benchmark_digest]),
            article=content.article,
            summary=content.summary,
            tags_json=self.json_text(content.tags),
            recommended_titles_json=self.json_text(content.recommended_titles),
            score_json=self.json_text(
                {
                    "overall_score": content_score.overall_score,
                    "title_attraction": content_score.title_attraction,
                    "information_density": content_score.information_density,
                    "save_potential": content_score.save_potential,
                    "completion_rate_potential": content_score.completion_rate_potential,
                    "ai_generated_risk": content_score.ai_generated_risk,
                    "validation_score": content.validation_score,
                }
            ),
            evidence_notes_json=self.json_text(data_report.evidence_notes),
            risk_flags_json=self.json_text(data_report.risk_flags),
            preflight_json=self.json_text(preflight_summary or {}),
            hot_topic_note=self._hot_topic_publish_note(data_report),
        )
        payload = self.request_json("你是 PublishReviewAgent，只输出 JSON。", prompt)
        if payload and self._validate_score_fields(payload, ("publish_readiness_score",)):
            result = {
                "publish_readiness_score": self._bound_score(payload["publish_readiness_score"]),
                "publish_risk_flags": self._clean_list(payload.get("publish_risk_flags"), 6),
                "publish_recommendation": str(payload.get("publish_recommendation", "manual_review")),
                "publish_notes": self._clean_list(payload.get("publish_notes"), 6),
            }
            result = self._apply_self_benchmark_adjustments(
                result,
                content,
                data_report,
                content_score,
                self_benchmark_context=self_benchmark_context,
            )
            return self._apply_length_gate(result, content, data_report)
        return self._fallback(
            content,
            data_report,
            content_score,
            preflight_summary or {},
            self_benchmark_context=self_benchmark_context,
        )

    def _hot_topic_publish_note(self, data_report: DataReport) -> str:
        """Return the publish contract for manual hot reviews or auto long-form."""
        if not self._is_hot_topic(data_report):
            return ""
        if self._auto_publish_mode():
            return (
                "\n\n【本次为自动发布长文候选】虽然选题来自热榜，正文仍必须按完整分析稿处理："
                "目标约1800字，清理后的正文最低1750字。热榜标签不能豁免事实、合规和标题检查；"
                "只有来源事实足够时才能扩展篇幅，证据不足必须收窄结论并保持人工审核，"
                "不得用重复套话、未披露数字或虚构案例凑够字数。"
            )
        return (
            "\n\n【本次为热榜快评模式】本文属于人工审核短评，清理后的正文最低850字，"
            "通常控制在850-900字。缺少订单、营收等硬数据可以是正常形态，但不能用重复套话填充；"
            "发布就绪度评估请重点检查标题党、绝对化表述、AI 套话、事实误导和合规风险。"
        )

    def _auto_publish_mode(self) -> bool:
        publishing = getattr(self.settings, "publishing", None)
        return bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )

    def _is_hot_topic(self, data_report: DataReport) -> bool:
        return bool(
            getattr(data_report, "is_hot_topic", False)
            or self._content_mode(data_report) == "hot_topic"
        )

    def _required_article_length(self, data_report: DataReport) -> int:
        if self._auto_publish_mode():
            return self.AUTO_PUBLISH_ARTICLE_MIN_CHARS
        if self._is_hot_topic(data_report):
            return self.HOT_TOPIC_MANUAL_MIN_CHARS
        return 0

    @staticmethod
    def _article_char_count(content: GeneratedContent) -> int:
        article = str(content.polished_article or content.article or "")
        return len(re.sub(r"\s+", "", article))

    def _apply_length_gate(
        self,
        result: dict[str, object],
        content: GeneratedContent,
        data_report: DataReport,
    ) -> dict[str, object]:
        """Force short hot/auto drafts into revision regardless of model score."""
        minimum = self._required_article_length(data_report)
        if minimum <= 0:
            return result

        article_chars = self._article_char_count(content)
        if article_chars >= minimum:
            return result

        length_flag = f"正文当前仅{article_chars}字，未达到该运行模式要求的最低{minimum}字。"
        risk_flags = [
            length_flag,
            *[
                str(item).strip()
                for item in result.get("publish_risk_flags", [])
                if str(item).strip()
            ],
        ]
        publish_notes = [
            "请补足有依据的事实、机制和风险边界；证据不足时保留人工审核，不要重复套话凑字数。",
            *[
                str(item).strip()
                for item in result.get("publish_notes", [])
                if str(item).strip()
            ],
        ]
        result["publish_readiness_score"] = 0
        result["publish_recommendation"] = "revise_before_publish"
        result["publish_risk_flags"] = self.unique(risk_flags)[:6]
        result["publish_notes"] = self.unique(publish_notes)[:6]
        return result

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        content_score: ContentScore,
        preflight_summary: dict[str, object],
        self_benchmark_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        title = content.recommended_titles[0] if content.recommended_titles else ""
        article_length = self._article_char_count(content)
        publish_risk_flags: list[str] = []
        publish_notes: list[str] = []
        readiness = 60
        is_hot_topic = self._is_hot_topic(data_report)

        if content_score.ai_generated_risk <= 35:
            readiness += 10
        else:
            publish_risk_flags.append("AI 痕迹偏重，建议发布前再加一句更具体的行业判断。")

        if data_report.risk_flags:
            publish_risk_flags.extend(data_report.risk_flags[:2])

        if article_length < 700:
            publish_risk_flags.append("正文偏短，可能影响头条停留时长。")
        else:
            readiness += 8

        if len(title) > self.settings.publishing.title_max_length:
            publish_risk_flags.append("标题偏长，移动端可能截断。")
        else:
            readiness += 7

        if data_report.evidence_notes:
            readiness += 8
        else:
            if is_hot_topic:
                # 热榜快评：标题型热点无原文素材，缺来源说明是正常形态，不扣 readiness
                publish_notes.append("热榜快评模式：基于公开热点展开观点，不引用具体来源。")
            else:
                publish_risk_flags.append("缺少来源说明，建议补一句数据或报告出处。")

        if content.tags:
            readiness += 5

        if content.validation_score >= 70:
            readiness += 5
        elif content.validation_notes:
            publish_notes.extend(content.validation_notes[:2])

        blockers = list(preflight_summary.get("blocking_flags", [])) if isinstance(preflight_summary, dict) else []
        warnings = list(preflight_summary.get("warnings", [])) if isinstance(preflight_summary, dict) else []
        if blockers:
            publish_risk_flags.extend(str(item) for item in blockers[:3])
            readiness -= min(18, len(blockers) * 6)
        if warnings:
            publish_notes.extend(str(item) for item in warnings[:3])
            readiness -= min(8, len(warnings) * 2)
        if any("敏感词" in str(item) or "违禁词" in str(item) for item in blockers):
            readiness = 0

        if not publish_notes:
            publish_notes = [
                "发布前核对公司名、数字口径和时间点。",
                "如有原始研报或公告，建议在文末补一句出处说明。",
                "封面标题尽量与正文主标题保持一致。",
            ]

        recommendation = "ready_to_publish"
        if publish_risk_flags or readiness < 78:
            recommendation = "manual_review"
        if readiness < 68:
            recommendation = "revise_before_publish"
        if any("敏感词" in str(item) or "违禁词" in str(item) for item in publish_risk_flags):
            recommendation = "revise_before_publish"

        result = {
            "publish_readiness_score": max(0, min(100, readiness)),
            "publish_risk_flags": self.unique(publish_risk_flags)[:6],
            "publish_recommendation": recommendation,
            "publish_notes": self.unique(publish_notes)[:6],
        }
        result = self._apply_self_benchmark_adjustments(
            result,
            content,
            data_report,
            content_score,
            self_benchmark_context=self_benchmark_context,
        )
        return self._apply_length_gate(result, content, data_report)



    def _apply_self_benchmark_adjustments(
        self,
        result: dict[str, object],
        content: GeneratedContent,
        data_report: DataReport,
        content_score: ContentScore,
        *,
        self_benchmark_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        patterns = self._target_self_benchmark_patterns(self_benchmark_context)
        if not patterns:
            return result

        matched = self._matched_self_benchmark_patterns(content, data_report, self_benchmark_context)
        readiness = int(result.get("publish_readiness_score", 0))
        risk_flags = list(result.get("publish_risk_flags", []))
        publish_notes = list(result.get("publish_notes", []))

        if len(matched) >= 4:
            readiness += 8
            publish_notes.append(f"命中自有高表现模式：{'、'.join(matched[:4])}")
        elif len(matched) >= 3:
            readiness += 5
        elif len(matched) == 2:
            readiness += 2
        elif self._is_hot_topic(data_report):
            # 热榜快评：无历史自对标样本可比，不因命中少扣分
            pass
        else:
            readiness -= 4
            risk_flags.append(f"这篇稿子对你自己的高表现样本命中较少，优先补齐：{'、'.join(patterns[:3])}")

        if content_score.overall_score >= 85 and len(matched) >= 3:
            readiness += 2
        if content_score.ai_generated_risk <= 35 and len(matched) >= 2:
            readiness += 2

        result["publish_readiness_score"] = max(0, min(100, readiness))
        result["publish_risk_flags"] = self.unique([str(item).strip() for item in risk_flags if str(item).strip()])[:6]
        result["publish_notes"] = self.unique([str(item).strip() for item in publish_notes if str(item).strip()])[:6]
        if result["publish_readiness_score"] >= 82 and result.get("publish_recommendation") == "manual_review":
            result["publish_recommendation"] = "ready_to_publish"
        if result["publish_readiness_score"] < 68:
            result["publish_recommendation"] = "revise_before_publish"
        return result

    def _target_self_benchmark_patterns(self, context: dict[str, object] | None) -> list[str]:
        if not context:
            return []
        return [str(item).strip() for item in context.get("patterns", []) if str(item).strip()]

    def _matched_self_benchmark_patterns(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        context: dict[str, object] | None,
    ) -> list[str]:
        patterns = self._target_self_benchmark_patterns(context)
        if not patterns:
            return []

        article = self._clean_article(content)
        title = content.recommended_titles[0] if content.recommended_titles else ""
        paragraphs = self._paragraphs(article)
        first_paragraph = paragraphs[0] if paragraphs else article
        matched: list[str] = []

        for pattern in patterns:
            if pattern == "场景先行开头":
                if any(token in first_paragraph or token in title for token in self.SCENE_TOKENS):
                    matched.append(pattern)
            elif pattern == "跨国对比冲突":
                if sum(1 for token in self.CROSS_BORDER_TOKENS if token in article) >= 2 and "不是" in article and "而是" in article:
                    matched.append(pattern)
            elif pattern == "利润迁移框架":
                if any(token in article for token in ("利润", "毛利", "系统集成", "零部件", "整机", "运维", "服务", "回款")):
                    matched.append(pattern)
            elif pattern == "具体数字配具体主体":
                if re.search(r"\d", article) and any(item in article for item in data_report.companies[:4]):
                    matched.append(pattern)
            elif pattern == "结尾保留跟踪变量":
                if any(token in article for token in ("接下来最值得持续跟踪", "后续最值得跟踪", "真正值得跟踪", "继续跟踪", "变量", "订单转化", "并网节奏", "回款", "客户验收")):
                    matched.append(pattern)

        return self.unique(matched)

    def _clean_article(self, content: GeneratedContent) -> str:
        return re.sub(r"\s+", " ", (content.polished_article or content.article or "").strip())

    def _paragraphs(self, article: str) -> list[str]:
        return [item.strip() for item in article.split("\n\n") if item.strip()]
