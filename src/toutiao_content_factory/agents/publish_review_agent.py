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
        prompt = self.render_prompt(
            "publish_review_prompt.md",
            platform=self.settings.platform,
            style_digest=self.style_digest(style),
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
            return {
                "publish_readiness_score": int(payload.get("publish_readiness_score", 0)),
                "publish_risk_flags": self._clean_list(payload.get("publish_risk_flags"), 6),
                "publish_recommendation": str(payload.get("publish_recommendation", "manual_review")),
                "publish_notes": self._clean_list(payload.get("publish_notes"), 6),
            }
        return self._fallback(content, data_report, content_score, preflight_summary or {})

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        content_score: ContentScore,
        preflight_summary: dict[str, object],
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

        return {
            "publish_readiness_score": max(0, min(100, readiness)),
            "publish_risk_flags": self.unique(publish_risk_flags)[:6],
            "publish_recommendation": recommendation,
            "publish_notes": self.unique(publish_notes)[:6],
        }

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]
