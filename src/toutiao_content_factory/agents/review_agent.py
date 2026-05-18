from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, ContentScore, DataReport, GeneratedContent


class ReviewAgent(BaseAgent):
    agent_name = "ReviewAgent"

    def run(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> dict[str, object]:
        prompt = self.render_prompt(
            "review_prompt.md",
            platform=self.settings.platform,
            style_digest=self.style_digest(style),
            article_angle=content.article_angle,
            validation_notes_json=self.json_text(content.validation_notes[:8]),
            titles_json=self.json_text(content.titles),
            recommended_titles_json=self.json_text(content.recommended_titles),
            summary=content.summary,
            article=content.article,
        )

        local_opening_lines = self._optimize_opening(content)
        local_review_notes = self._build_review_notes(content, data_report, cache_context)
        local_revision_suggestions = self._build_revision_suggestions(content, data_report)

        payload = self.request_json("你是 ReviewAgent，只输出 JSON。", prompt)
        if payload:
            title_attraction = self._bound_score(payload.get("title_attraction", 0))
            information_density = self._bound_score(payload.get("information_density", 0))
            save_potential = self._bound_score(payload.get("save_potential", 0))
            completion_rate_potential = self._bound_score(payload.get("completion_rate_potential", 0))
            ai_generated_risk = self._bound_score(payload.get("ai_generated_risk", 0))

            score = ContentScore(
                overall_score=self._compute_overall(
                    title_attraction,
                    information_density,
                    save_potential,
                    completion_rate_potential,
                    ai_generated_risk,
                ),
                title_attraction=title_attraction,
                information_density=information_density,
                save_potential=save_potential,
                completion_rate_potential=completion_rate_potential,
                ai_generated_risk=ai_generated_risk,
                reasoning=self._clean_list(payload.get("reasoning"), 4)
                or self._build_reasoning(
                    title_attraction,
                    information_density,
                    completion_rate_potential,
                    ai_generated_risk,
                ),
                revision_suggestions=self._clean_list(payload.get("revision_suggestions"), 4)
                or local_revision_suggestions,
                pass_status=self._normalize_pass_status(payload.get("pass_status", "ready_with_light_edit")),
            )
            review_notes = self.unique(
                self._clean_list(payload.get("review_notes"), 4)
                + local_review_notes
                + list(content.validation_notes[:2])
            )[:6]
            return {
                "optimized_opening_lines": self._clean_list(payload.get("optimized_opening_lines"), 3)
                or local_opening_lines,
                "review_notes": review_notes,
                "content_score": score,
            }

        return self._fallback(content, data_report, cache_context)

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> dict[str, object]:
        optimized_opening_lines = self._optimize_opening(content)
        title_attraction = self._score_title(content.recommended_titles[0] if content.recommended_titles else "")
        information_density = self._score_information_density(content, data_report)
        save_potential = self._score_save_potential(content, data_report)
        completion_rate_potential = self._score_completion(content, optimized_opening_lines)
        ai_generated_risk = self._score_ai_risk(content, data_report, cache_context)
        overall_score = self._compute_overall(
            title_attraction,
            information_density,
            save_potential,
            completion_rate_potential,
            ai_generated_risk,
        )

        score = ContentScore(
            overall_score=overall_score,
            title_attraction=title_attraction,
            information_density=information_density,
            save_potential=save_potential,
            completion_rate_potential=completion_rate_potential,
            ai_generated_risk=ai_generated_risk,
            reasoning=self._build_reasoning(
                title_attraction,
                information_density,
                completion_rate_potential,
                ai_generated_risk,
            ),
            revision_suggestions=self._build_revision_suggestions(content, data_report),
            pass_status=self._determine_pass_status(
                overall_score,
                completion_rate_potential,
                ai_generated_risk,
                information_density,
            ),
        )

        review_notes = self.unique(
            self._build_review_notes(content, data_report, cache_context) + list(content.validation_notes[:2])
        )[:6]
        return {
            "optimized_opening_lines": optimized_opening_lines,
            "review_notes": review_notes,
            "content_score": score,
        }

    def _score_title(self, title: str) -> int:
        score = 58
        if any(char.isdigit() for char in title):
            score += 10
        if "？" in title or "?" in title:
            score += 8
        if any(token in title for token in ("真正", "背后", "为什么", "不是", "利润", "订单", "交付")):
            score += 12
        if 16 <= len(title) <= 28:
            score += 10
        return min(95, score)

    def _score_information_density(self, content: GeneratedContent, data_report: DataReport) -> int:
        score = 50
        score += min(18, len(data_report.growth_numbers) * 4)
        score += min(12, len(data_report.key_points) * 3)
        score += min(10, len(data_report.companies[:4]) * 2)
        score += min(10, len(data_report.evidence_notes[:4]) * 2)
        score += min(8, content.validation_score // 12)
        return min(98, score)

    def _score_save_potential(self, content: GeneratedContent, data_report: DataReport) -> int:
        score = 52
        score += min(16, len(data_report.watch_metrics) * 4)
        score += min(10, len(data_report.growth_numbers[:3]) * 3)
        if any(token in content.article for token in ("订单", "交付", "毛利", "验证", "产能", "客户")):
            score += 8
        if content.summary:
            score += 4
        return min(96, score)

    def _score_completion(self, content: GeneratedContent, opening_lines: list[str]) -> int:
        paragraphs = self._paragraphs(content.article)
        paragraph_lengths = [len(item) for item in paragraphs]
        average_length = round(sum(paragraph_lengths) / len(paragraph_lengths)) if paragraph_lengths else 0
        max_length = max(paragraph_lengths) if paragraph_lengths else 0

        score = 56
        if 700 <= len(content.article) <= 1700:
            score += 14
        if 5 <= len(paragraphs) <= 8:
            score += 10
        elif 4 <= len(paragraphs) <= 10:
            score += 6
        if len(opening_lines) == 3:
            score += 8
        if average_length and average_length <= 110:
            score += 8
        elif average_length and average_length <= 145:
            score += 4
        else:
            score -= 6
        if max_length and max_length <= 160:
            score += 6
        elif max_length >= 220:
            score -= 6
        if "##" not in content.article and not re.search(r"^\d+\.", content.article, re.M):
            score += 4
        return max(30, min(95, score))

    def _score_ai_risk(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> int:
        article = content.article
        generic_hits = len(
            re.findall(
                r"值得关注|不难发现|换句话说|需要指出的是|从某种意义上说|真正值得看的是|不容忽视",
                article,
            )
        )
        paragraphs = self._paragraphs(article)
        average_length = round(sum(len(item) for item in paragraphs) / len(paragraphs)) if paragraphs else 0
        repeated_transitions = len(re.findall(r"先看|再看|最后说|真正决定", article))

        risk = 48
        risk -= min(16, len(data_report.growth_numbers) * 3)
        risk -= min(10, len(data_report.companies[:4]) * 2)
        risk -= min(8, len(data_report.evidence_notes[:4]) * 2)
        risk -= min(8, content.validation_score // 15)
        if self._has_source_anchor(article):
            risk -= 6
        if average_length > 150:
            risk += 5
        risk += generic_hits * 5
        risk += max(0, repeated_transitions - 3) * 3
        if cache_context.similar_hit:
            risk -= 3
        return max(18, min(80, risk))

    def _compute_overall(
        self,
        title_attraction: int,
        information_density: int,
        save_potential: int,
        completion_rate_potential: int,
        ai_generated_risk: int,
    ) -> int:
        return round(
            (
                title_attraction
                + information_density
                + save_potential
                + completion_rate_potential
                + (100 - ai_generated_risk)
            )
            / 5
        )

    def _optimize_opening(self, content: GeneratedContent) -> list[str]:
        lines = content.opening_lines[:3]
        if not lines:
            lines = [item.strip() for item in self._paragraphs(content.article)[:3] if item.strip()]

        optimized: list[str] = []
        for index, line in enumerate(lines[:3]):
            text = re.sub(r"\s+", " ", line).strip()
            if index < 2 and len(text) > 46:
                pieces = [item.strip() for item in re.split(r"[，；]", text) if item.strip()]
                if pieces:
                    text = pieces[0]
                if len(text) > 46:
                    text = text[:46].rstrip("，。；!?？")
            elif index == 2 and len(text) > 62:
                if "，" in text:
                    pieces = [item.strip() for item in text.split("，") if item.strip()]
                    if len(pieces) >= 2:
                        text = "，".join(pieces[:2]).rstrip("，。；!?？")
            if text and text[-1] not in "。！？!?":
                text += "。"
            optimized.append(text)
        return optimized[:3]

    def _build_review_notes(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> list[str]:
        notes: list[str] = []
        paragraphs = self._paragraphs(content.article)
        max_length = max((len(item) for item in paragraphs), default=0)

        if not self._has_source_anchor(content.article):
            notes.append("建议补一句公开材料、财报或机构统计的出处感表达，证据会更稳。")
        if max_length >= 180:
            notes.append("正文仍有长段落，建议把单段控制在 2 到 3 句，提升手机端完读率。")
        if len(data_report.companies) and not any(company in content.article for company in data_report.companies[:3]):
            notes.append("建议在正文里点名 1 到 2 个公司或项目主体，主体感会更强。")
        if not any(token in content.article for token in ("订单", "交付", "验证", "毛利", "量产")):
            notes.append("可以再补一句订单、交付或毛利指标，让收藏价值更强。")
        if cache_context.similar_hit:
            notes.append("本次题材和历史内容接近，发文前再确认开头角度是否足够新。")

        if not notes:
            notes.append("整体结构已经可发，发布前主要核对数字口径和公司名称。")
        return notes[:4]

    def _build_revision_suggestions(self, content: GeneratedContent, data_report: DataReport) -> list[str]:
        suggestions: list[str] = []
        if not self._has_source_anchor(content.article):
            suggestions.append("在首段或第二段补一句‘从公开材料看’或具体机构/公告出处，增强证据感。")
        if max((len(item) for item in self._paragraphs(content.article)), default=0) >= 180:
            suggestions.append("拆短长段落，每段尽量控制在 2 到 3 句，首屏不要堆太满。")
        if not any(company in content.article for company in data_report.companies[:3]):
            suggestions.append("增加具体公司、项目或订单主体，避免写成泛行业评论。")
        if not any(token in content.article for token in ("订单", "交付", "验证", "毛利", "客户")):
            suggestions.append("补充订单、交付、客户验证或毛利变化，提升收藏价值。")
        if not suggestions:
            suggestions.append("保持当前主线不变，重点再压一压首段句长即可。")
        return suggestions[:4]

    def _build_reasoning(
        self,
        title_attraction: int,
        information_density: int,
        completion_rate_potential: int,
        ai_generated_risk: int,
    ) -> list[str]:
        return [
            f"标题吸引力 {title_attraction}，主要看数字、判断感、主体感和长度控制。",
            f"信息密度 {information_density}，核心取决于数字、公司、产业链与观察指标是否齐全。",
            f"完读率潜力 {completion_rate_potential}，重点看首屏判断是否清楚、段落是否适合手机端阅读。",
            f"AI 痕迹风险 {ai_generated_risk}，分数越高越像模板稿，通常需要继续压缩套话和长句。",
        ]

    def _determine_pass_status(
        self,
        overall_score: int,
        completion_rate_potential: int,
        ai_generated_risk: int,
        information_density: int,
    ) -> str:
        if overall_score >= 84 and completion_rate_potential >= 82 and ai_generated_risk <= 32:
            return "ready"
        if completion_rate_potential <= 68 or ai_generated_risk >= 58 or information_density <= 68:
            return "needs_revision"
        return "ready_with_light_edit"

    def _paragraphs(self, article: str) -> list[str]:
        return [item.strip() for item in article.split("\n\n") if item.strip()]

    def _has_source_anchor(self, article: str) -> bool:
        markers = ("据", "数据显示", "公开材料", "财报", "公告", "机构统计", "报告")
        return any(marker in article for marker in markers)

    def _bound_score(self, value: object) -> int:
        try:
            score = int(value)
        except (TypeError, ValueError):
            score = 0
        return max(0, min(100, score))

    def _normalize_pass_status(self, value: object) -> str:
        normalized = str(value).strip()
        if normalized in {"ready", "ready_with_light_edit", "needs_revision"}:
            return normalized
        return "ready_with_light_edit"

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]
