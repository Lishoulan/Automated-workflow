from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import ContentScore, DataReport, GeneratedContent


class PublishReviewAgent(BaseAgent):
    agent_name = "PublishReviewAgent"

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
        )
        payload = self.request_json("你是 PublishReviewAgent，只输出 JSON。", prompt)
        if payload:
            result = {
                "publish_readiness_score": int(payload.get("publish_readiness_score", 0)),
                "publish_risk_flags": self._clean_list(payload.get("publish_risk_flags"), 6),
                "publish_recommendation": str(payload.get("publish_recommendation", "manual_review")),
                "publish_notes": self._clean_list(payload.get("publish_notes"), 6),
            }
            return self._apply_self_benchmark_adjustments(
                result,
                content,
                data_report,
                content_score,
                self_benchmark_context=self_benchmark_context,
            )
        return self._fallback(
            content,
            data_report,
            content_score,
            preflight_summary or {},
            self_benchmark_context=self_benchmark_context,
        )

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        content_score: ContentScore,
        preflight_summary: dict[str, object],
        self_benchmark_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        title = content.recommended_titles[0] if content.recommended_titles else ""
        article_length = len(re.sub(r"\s+", "", content.article))
        publish_risk_flags: list[str] = []
        publish_notes: list[str] = []
        readiness = 60

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

        result = {
            "publish_readiness_score": max(0, min(100, readiness)),
            "publish_risk_flags": self.unique(publish_risk_flags)[:6],
            "publish_recommendation": recommendation,
            "publish_notes": self.unique(publish_notes)[:6],
        }
        return self._apply_self_benchmark_adjustments(
            result,
            content,
            data_report,
            content_score,
            self_benchmark_context=self_benchmark_context,
        )

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]

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
        scene_tokens = ("机场", "电厂", "电站", "港口", "车间", "仓库", "园区", "工厂", "基地", "物流中心", "示范项目")
        cross_border_tokens = ("中国", "日本", "美国", "欧洲", "德国", "东京", "海外", "出海", "国际", "日韩")

        for pattern in patterns:
            if pattern == "场景先行开头":
                if any(token in first_paragraph or token in title for token in scene_tokens):
                    matched.append(pattern)
            elif pattern == "跨国对比冲突":
                if sum(1 for token in cross_border_tokens if token in article) >= 2 and "不是" in article and "而是" in article:
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
