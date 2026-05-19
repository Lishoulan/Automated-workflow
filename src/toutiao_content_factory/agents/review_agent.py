from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, ContentScore, DataReport, GeneratedContent
from toutiao_content_factory.services.article_sanitizer import clean_article_text


class ReviewAgent(BaseAgent):
    agent_name = "ReviewAgent"
    NUMERIC_TOKEN_RE = re.compile(
        r"\d+(?:\.\d+)?(?:%|倍|亿元|亿|万元|万亿元|万千瓦|万台|万套|GW|GWh|MWh|kWh|MW|W|年|月|季度|Q[1-4])?"
    )

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

        local_opening_lines = self._optimize_opening(content, data_report)
        local_review_notes = self._build_review_notes(content, data_report, cache_context)
        local_revision_suggestions = self._build_revision_suggestions(content, data_report)
        unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)

        payload = self.request_json("你是 ReviewAgent，只输出 JSON。", prompt)
        if payload:
            title_attraction = self._bound_score(payload.get("title_attraction", 0))
            information_density = self._bound_score(payload.get("information_density", 0))
            save_potential = self._bound_score(payload.get("save_potential", 0))
            completion_rate_potential = self._bound_score(payload.get("completion_rate_potential", 0))
            ai_generated_risk = self._bound_score(payload.get("ai_generated_risk", 0))
            if unsupported_tokens:
                information_density = max(20, information_density - min(18, len(unsupported_tokens) * 4))
                ai_generated_risk = min(95, ai_generated_risk + min(26, len(unsupported_tokens) * 6))

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
                pass_status=(
                    "needs_revision"
                    if unsupported_tokens
                    else self._normalize_pass_status(payload.get("pass_status", "ready_with_light_edit"))
                ),
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
        optimized_opening_lines = self._optimize_opening(content, data_report)
        title_attraction = self._score_title(content.recommended_titles[0] if content.recommended_titles else "")
        information_density = self._score_information_density(content, data_report)
        save_potential = self._score_save_potential(content, data_report)
        completion_rate_potential = self._score_completion(content, optimized_opening_lines, data_report)
        ai_generated_risk = self._score_ai_risk(content, data_report, cache_context)
        unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)
        if unsupported_tokens:
            information_density = max(20, information_density - min(18, len(unsupported_tokens) * 4))
            completion_rate_potential = max(20, completion_rate_potential - min(12, len(unsupported_tokens) * 3))
            ai_generated_risk = min(95, ai_generated_risk + min(26, len(unsupported_tokens) * 6))
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
            pass_status=(
                "needs_revision"
                if unsupported_tokens
                else self._determine_pass_status(
                    overall_score,
                    completion_rate_potential,
                    ai_generated_risk,
                    information_density,
                )
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
        score = 56
        if any(char.isdigit() for char in title):
            score += 10
        if "？" in title or "?" in title:
            score += 6
        if any(token in title for token in ("真正", "背后", "为什么", "不是", "利润", "订单", "交付", "毛利")):
            score += 12
        if 16 <= len(title) <= min(28, self.settings.publishing.title_max_length):
            score += 10
        elif len(title) > self.settings.publishing.title_max_length:
            score -= 12
        if any(token in title for token in ("看懂", "机会", "又火了", "开始进入兑现筛选期")):
            score -= 14
        return max(20, min(95, score))

    def _score_information_density(self, content: GeneratedContent, data_report: DataReport) -> int:
        score = 50
        score += min(18, len(data_report.growth_numbers) * 4)
        score += min(12, len(data_report.key_points) * 3)
        score += min(10, len(data_report.companies[:4]) * 2)
        score += min(10, len(data_report.evidence_notes[:4]) * 2)
        score += min(8, data_report.evidence_strength // 10)
        score += min(8, content.validation_score // 12)
        return min(98, score)

    def _score_save_potential(self, content: GeneratedContent, data_report: DataReport) -> int:
        article = self._clean_article(content)
        score = 52
        score += min(16, len(data_report.watch_metrics) * 4)
        score += min(10, len(data_report.growth_numbers[:3]) * 3)
        if any(token in article for token in ("订单", "交付", "毛利", "验证", "产能", "客户", "份额")):
            score += 8
        if content.summary:
            score += 4
        return min(96, score)

    def _score_completion(self, content: GeneratedContent, opening_lines: list[str], data_report: DataReport) -> int:
        article = self._clean_article(content)
        paragraphs = self._paragraphs(article)
        paragraph_lengths = [len(item) for item in paragraphs]
        average_length = round(sum(paragraph_lengths) / len(paragraph_lengths)) if paragraph_lengths else 0
        max_length = max(paragraph_lengths) if paragraph_lengths else 0
        min_paragraphs, max_paragraphs = self._paragraph_bounds(data_report)
        min_chars, max_chars = self._word_bounds(data_report)
        compact_format = data_report.content_format in {"quick_commentary", "data_recap"}

        score = 56
        if min_chars <= len(article) <= max_chars:
            score += 14
        if min_paragraphs <= len(paragraphs) <= max_paragraphs:
            score += 10
        elif max(3, min_paragraphs - 1) <= len(paragraphs) <= max_paragraphs + 1:
            score += 6
        if len(opening_lines) == 3:
            score += 8
        if average_length and average_length <= (96 if compact_format else 110):
            score += 8
        elif average_length and average_length <= (126 if compact_format else 145):
            score += 4
        else:
            score -= 6
        if max_length and max_length <= (138 if compact_format else 160):
            score += 6
        elif max_length >= (185 if compact_format else 220):
            score -= 8
        if "##" not in article and "#" not in article:
            score += 4
        return max(30, min(95, score))

    def _paragraph_bounds(self, data_report: DataReport) -> tuple[int, int]:
        if data_report.content_format == "quick_commentary":
            return 4, 5
        if data_report.content_format == "data_recap":
            return 4, 5
        if data_report.content_format == "project_breakdown":
            return 5, 6
        return 5, 7

    def _word_bounds(self, data_report: DataReport) -> tuple[int, int]:
        target = max(int(data_report.word_target or 0), 600)
        if data_report.content_format == "quick_commentary":
            return max(500, target - 180), target + 220
        if data_report.content_format == "data_recap":
            return max(600, target - 180), target + 260
        if data_report.content_format == "project_breakdown":
            return max(750, target - 220), target + 300
        return max(700, target - 250), target + 450

    def _score_ai_risk(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> int:
        article = self._clean_article(content)
        generic_hits = len(
            re.findall(
                r"值得关注的是|不难发现|换句话说|需要指出的是|从某种意义上说|可以看到|总体来看",
                article,
            )
        )
        paragraphs = self._paragraphs(article)
        average_length = round(sum(len(item) for item in paragraphs) / len(paragraphs)) if paragraphs else 0
        repeated_transitions = len(re.findall(r"先看|再看|最后看|真正决定", article))
        unsupported_count = len(self._unsupported_numeric_tokens(content, data_report))

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
        risk += min(24, unsupported_count * 6)
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

    def _optimize_opening(self, content: GeneratedContent, data_report: DataReport) -> list[str]:
        article = self._clean_article(content)
        lines = [self._normalize_opening_line(item) for item in content.opening_lines[:3] if self._normalize_opening_line(item)]
        if len(lines) < 3:
            sentences = self._collect_sentences(article)
            for sentence in sentences:
                normalized = self._normalize_opening_line(sentence)
                if normalized and normalized not in lines:
                    lines.append(normalized)
                if len(lines) >= 3:
                    break

        keyword = self._primary_keyword(data_report)
        company = self._primary_company(data_report)
        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else ""

        if not lines:
            lines = [f"{keyword}热度再起，真正该盯的是兑现速度。"]
        if not self._is_strong_opening(lines[0]):
            if number and keyword:
                lines[0] = f"{number}不是终点，{keyword}真正要比的是兑现。"
            elif company and keyword:
                lines[0] = f"{company}火了之后，{keyword}开始拼交付。"
            else:
                lines[0] = f"{keyword}热度再起，真正分胜负的是兑现速度。"

        optimized: list[str] = []
        limits = (30, 34, 38)
        for index, line in enumerate(lines[:3]):
            text = re.sub(r"\s+", " ", line).strip()
            pieces = [item.strip() for item in re.split(r"[，；]", text) if item.strip()]
            if pieces and len(text) > limits[index]:
                text = pieces[0]
            if len(text) > limits[index]:
                text = text[: limits[index]].rstrip("，。！？；：:、 ")
            if text and text[-1] not in "。！？?":
                text += "。"
            optimized.append(text)

        return optimized[:3]

    def _build_review_notes(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> list[str]:
        article = self._clean_article(content)
        notes: list[str] = []
        paragraphs = self._paragraphs(article)
        max_length = max((len(item) for item in paragraphs), default=0)

        if not self._has_source_anchor(article):
            notes.append("建议补一句公开材料、财报或机构统计的出处感表达，证据会更稳。")
        if max_length >= 180:
            notes.append("正文仍有长段落，建议单段控制在 2 到 3 句，提升移动端完读率。")
        if len(data_report.companies) and not any(company in article for company in data_report.companies[:3]):
            notes.append("建议在正文里点名 1 到 2 个公司或订单主体，主体感会更强。")
        if not any(token in article for token in ("订单", "交付", "验证", "毛利", "量产")):
            notes.append("可以再补一句订单、交付或毛利指标，让文章更有收藏价值。")
        unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)
        if unsupported_tokens:
            notes.append(f"正文出现未锚定数字：{'、'.join(unsupported_tokens[:4])}，要删掉杜撰比例或份额。")
        if cache_context.similar_hit:
            notes.append("这次题材和历史内容接近，发布前再确认开头角度是否足够新。")

        if not notes:
            notes.append("整体结构已经可发，发布前重点再核对数字口径和公司名称。")
        return notes[:4]

    def _build_revision_suggestions(self, content: GeneratedContent, data_report: DataReport) -> list[str]:
        article = self._clean_article(content)
        suggestions: list[str] = []
        if not self._has_source_anchor(article):
            suggestions.append("在首段或第二段补一句“从公开材料看”或具体公告/报告出处，增强证据感。")
        if max((len(item) for item in self._paragraphs(article)), default=0) >= 180:
            suggestions.append("拆短长段落，每段尽量控制在 2 到 3 句，首屏不要堆太满。")
        if not any(company in article for company in data_report.companies[:3]):
            suggestions.append("增加具体公司、项目或订单主体，避免写成泛行业评论。")
        if not any(token in article for token in ("订单", "交付", "验证", "毛利", "客户")):
            suggestions.append("补充订单、交付、客户验证或毛利变化，提升文章的收藏价值。")
        unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)
        if unsupported_tokens:
            suggestions.append("删掉素材里没有明确出现的新数字、新比例和新份额，证据不够就改成定性表达。")
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
            f"信息密度 {information_density}，核心取决于数字、公司、产业链和跟踪指标是否齐全。",
            f"完读率潜力 {completion_rate_potential}，重点看首屏判断是否清楚、段落是否适合手机阅读。",
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

    def _clean_article(self, content: GeneratedContent) -> str:
        article = content.polished_article or content.article
        return clean_article_text(article, title_candidates=content.recommended_titles + content.titles)

    def _paragraphs(self, article: str) -> list[str]:
        return [item.strip() for item in article.split("\n\n") if item.strip()]

    def _collect_sentences(self, article: str) -> list[str]:
        return [item.strip() for item in re.split(r"(?<=[。！？!?])", article) if item.strip()]

    def _normalize_opening_line(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        normalized = re.sub(r"^(但是|然而|同时|另外|需要指出的是|换句话说|不难发现)[，,]?", "", normalized)
        normalized = normalized.strip("#*- ")
        return normalized

    def _is_strong_opening(self, text: str) -> bool:
        return bool(
            text
            and (
                any(char.isdigit() for char in text)
                or any(token in text for token in ("真正", "背后", "订单", "交付", "利润", "毛利", "不是", "而是"))
            )
        )

    def _primary_keyword(self, data_report: DataReport) -> str:
        candidates = self.clean_publish_list(
            data_report.focus_keywords + [data_report.focus_industry] + data_report.supply_chain_keywords,
            limit=8,
        )
        for item in candidates:
            if item and not self.is_internal_label(item):
                return item
        return candidates[0] if candidates else "这个赛道"

    def _primary_company(self, data_report: DataReport) -> str:
        for item in self.clean_publish_list(data_report.focus_companies + data_report.companies, limit=4):
            if item and item not in {"某公司", "该公司", "头部公司"}:
                return item
        return ""

    def _has_source_anchor(self, article: str) -> bool:
        markers = ("据", "数据显示", "公开材料", "财报", "公告", "机构统计", "报告")
        return any(marker in article for marker in markers)

    def _unsupported_numeric_tokens(self, content: GeneratedContent, data_report: DataReport) -> list[str]:
        article = self._clean_article(content)
        article_tokens = self._extract_numeric_tokens(article)
        if not article_tokens:
            return []
        allowed = self._evidence_numeric_tokens(data_report)
        unsupported: list[str] = []
        for token in article_tokens:
            if token in allowed:
                continue
            if token not in unsupported:
                unsupported.append(token)
        return unsupported

    def _extract_numeric_tokens(self, text: str) -> list[str]:
        tokens: list[str] = []
        for match in self.NUMERIC_TOKEN_RE.findall(str(text or "")):
            token = str(match).strip().replace(" ", "")
            if not self._is_significant_numeric_token(token):
                continue
            if token not in tokens:
                tokens.append(token)
        return tokens

    def _evidence_numeric_tokens(self, data_report: DataReport) -> set[str]:
        texts = [
            data_report.topic_title,
            *data_report.growth_numbers,
            *data_report.core_data,
            *data_report.industry_trends,
            *data_report.key_points,
            *data_report.watch_metrics,
            *data_report.evidence_notes,
            *data_report.risk_flags,
        ]
        allowed: set[str] = set()
        for text in texts:
            for token in self._extract_numeric_tokens(text):
                allowed.add(token)
        return allowed

    def _is_significant_numeric_token(self, token: str) -> bool:
        if not token:
            return False
        suffixes = ("%", "倍", "亿元", "亿", "万元", "万亿元", "万千瓦", "万台", "万套", "GW", "GWh", "MWh", "kWh", "MW", "W", "年", "月", "季度")
        if token.startswith("Q") and len(token) == 2:
            return True
        if any(marker in token for marker in suffixes):
            return True
        return len(re.sub(r"\D", "", token)) >= 4

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
