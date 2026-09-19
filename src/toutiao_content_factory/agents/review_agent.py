from __future__ import annotations

import re
import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import CacheContext, ContentScore, DataReport, GeneratedContent
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class ReviewAgent(BaseAgent):
    agent_name = "ReviewAgent"

    def _auto_publish_mode(self) -> bool:
        publishing = getattr(self.settings, "publishing", None)
        return bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )

    def run(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
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
            "review_prompt.md",
            platform=self.settings.platform,
            style_digest=self.style_digest(style, extra_sections=[benchmark_digest]),
            article_angle=content.article_angle,
            validation_notes_json=self.json_text(content.validation_notes[:8]),
            titles_json=self.json_text(content.titles),
            recommended_titles_json=self.json_text(content.recommended_titles),
            summary=content.summary,
            article=content.article,
            hot_topic_note=self._hot_topic_review_note(data_report),
        )

        local_opening_lines = self._optimize_opening(content, data_report)
        matched_patterns = self._matched_self_benchmark_patterns(content, data_report, self_benchmark_context)
        local_review_notes = self._build_review_notes(
            content,
            data_report,
            cache_context,
            self_benchmark_context=self_benchmark_context,
            matched_patterns=matched_patterns,
        )
        local_revision_suggestions = self._build_revision_suggestions(
            content,
            data_report,
            self_benchmark_context=self_benchmark_context,
            matched_patterns=matched_patterns,
        )
        unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)

        payload = self.request_json("你是 ReviewAgent，只输出 JSON。", prompt)
        score_fields = (
            "title_attraction",
            "information_density",
            "save_potential",
            "completion_rate_potential",
            "ai_generated_risk",
        )
        if payload and self._validate_score_fields(payload, score_fields):
            title_attraction = self._bound_score(payload.get("title_attraction", 0))
            information_density = self._bound_score(payload.get("information_density", 0))
            save_potential = self._bound_score(payload.get("save_potential", 0))
            completion_rate_potential = self._bound_score(payload.get("completion_rate_potential", 0))
            ai_generated_risk = self._bound_score(payload.get("ai_generated_risk", 0))
            if unsupported_tokens:
                information_density = max(20, information_density - min(18, len(unsupported_tokens) * 4))
                ai_generated_risk = min(95, ai_generated_risk + min(26, len(unsupported_tokens) * 6))
            title_attraction, information_density, save_potential, completion_rate_potential, ai_generated_risk = self._apply_self_benchmark_adjustments(
                title_attraction,
                information_density,
                save_potential,
                completion_rate_potential,
                ai_generated_risk,
                self_benchmark_context=self_benchmark_context,
                matched_patterns=matched_patterns,
            )

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
                    self_benchmark_context=self_benchmark_context,
                    matched_patterns=matched_patterns,
                ),
                revision_suggestions=self._clean_list(payload.get("revision_suggestions"), 4)
                or local_revision_suggestions,
                pass_status=(
                    "needs_revision"
                    if unsupported_tokens
                    else self._normalize_pass_status(payload.get("pass_status", "ready_with_light_edit"))
                ),
            )
            self._apply_length_gate(score, content, data_report)
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

        return self._fallback(
            content,
            data_report,
            cache_context,
            self_benchmark_context=self_benchmark_context,
            matched_patterns=matched_patterns,
        )

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
        self_benchmark_context: dict[str, object] | None = None,
        matched_patterns: list[str] | None = None,
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
        title_attraction, information_density, save_potential, completion_rate_potential, ai_generated_risk = self._apply_self_benchmark_adjustments(
            title_attraction,
            information_density,
            save_potential,
            completion_rate_potential,
            ai_generated_risk,
            self_benchmark_context=self_benchmark_context,
            matched_patterns=matched_patterns or [],
        )
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
                self_benchmark_context=self_benchmark_context,
                matched_patterns=matched_patterns or [],
            ),
            revision_suggestions=self._build_revision_suggestions(
                content,
                data_report,
                self_benchmark_context=self_benchmark_context,
                matched_patterns=matched_patterns or [],
            ),
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
        self._apply_length_gate(score, content, data_report)

        review_notes = self.unique(
            self._build_review_notes(
                content,
                data_report,
                cache_context,
                self_benchmark_context=self_benchmark_context,
                matched_patterns=matched_patterns or [],
            )
            + list(content.validation_notes[:2])
        )[:6]
        return {
            "optimized_opening_lines": optimized_opening_lines,
            "review_notes": review_notes,
            "content_score": score,
        }

    def _hot_topic_review_note(self, data_report: DataReport) -> str:
        """Return a review brief whose length contract matches the run mode."""
        if not bool(getattr(data_report, "is_hot_topic", False)):
            return ""
        if self._auto_publish_mode():
            return (
                "\n\n【本次为自动发布长文候选】本文基于热榜线索创作，但正文仍需达到约1800字、最低1750字，"
                "并按完整分析稿检查事件事实、机制、影响、主体和风险边界。证据不足时应收窄结论，"
                "不得用重复套话或新增事实填充篇幅；只有清理后的来源事实足够时，才允许继续扩展。"
            )
        return (
            "\n\n【本次为热榜快评模式】本文基于实时热榜标题创作，"
            "属于人工审核短评体裁（约850-900字）。评分请注意：\n"
            "1. 缺少订单金额、营收、出货量等硬数据是正常形态，不要因此大幅扣信息密度；\n"
            "2. ai_generated_risk 主要看句式模板化、套话堆砌、段落重复等写作腔调问题，"
            "不要因为『缺数据/缺来源』扣高；\n"
            "3. 信息密度按『观点密度+热点关联+互动引导』评估；\n"
            "4. 综合分只能作为参考，仍需确认正文达到短评字数并通过事实、合规和标题检查。"
        )

    def _apply_length_gate(
        self,
        score: ContentScore,
        content: GeneratedContent,
        data_report: DataReport,
    ) -> None:
        """Keep a short hot/auto draft from being scored as publish-ready."""
        is_hot_topic = bool(getattr(data_report, "is_hot_topic", False))
        if not (is_hot_topic or self._auto_publish_mode()):
            return
        minimum = 1750 if self._auto_publish_mode() else 850
        article_chars = len(re.sub(r"\s+", "", str(content.article or "")))
        if article_chars >= minimum:
            return
        score.pass_status = "needs_revision"
        score.completion_rate_potential = min(score.completion_rate_potential, 35)
        score.revision_suggestions = self.unique(
            [
                *score.revision_suggestions,
                f"正文当前仅{article_chars}字，未达到该运行模式要求的最低{minimum}字。",
            ]
        )[:6]

    #: 社会故事标题的「冲突/关系/后果」抓手 —— 相当于产业稿里的数字与公司。
    STORY_TITLE_HOOKS = (
        "索赔", "赔偿", "起诉", "判", "拒", "翻脸", "争执", "离", "赡养", "继承",
        "过户", "摔倒", "受伤", "去世", "报警", "破门", "赶出", "欠", "骗",
        "难", "为何", "怎么办", "凭什么", "该不该", "誰", "谁",
    )

    def _score_title(self, title: str) -> int:
        score = 56
        if any(char.isdigit() for char in title):
            score += 10
        if "？" in title or "?" in title:
            score += 6
        # ⚠️ 2026-09-20：这个 +12 的「判断感」词表是**产业稿**的
        # （真正/背后/为什么/不是/利润/订单/交付/毛利）。社会故事标题一个都不沾，
        # 于是稳定吃不到这 12 分 —— 实测「家政服务边界模糊，老人摔伤索赔为何难」
        # 被打了 20 分（满分 100），直接拖垮 overall_score 并卡住自动发布。
        # 叙事体裁下改用"冲突/关系/后果"抓手，这才是故事标题的信息量来源。
        if any(token in title for token in ("真正", "背后", "为什么", "不是", "利润", "订单", "交付", "毛利")):
            score += 12
        elif any(token in title for token in self.STORY_TITLE_HOOKS):
            score += 12
        if any(token in title for token in super().SCENE_TOKENS):
            score += 8
        if 16 <= len(title) <= min(28, self.settings.publishing.title_max_length):
            score += 10
        elif len(title) > self.settings.publishing.title_max_length:
            score -= 12
        # 故事标题天然偏长（要交代"谁遇到了什么事"），超过 28 但仍在上限内
        # 不该一分不给：放宽到上限给 6 分。
        elif len(title) > min(28, self.settings.publishing.title_max_length):
            score += 6
        if any(token in title for token in ("看懂", "机会", "又火了", "开始进入兑现筛选期", "更该看订单还是毛利")):
            score -= 14
        return max(20, min(95, score))

    def _score_information_density(self, content: GeneratedContent, data_report: DataReport) -> int:
        score = 50
        # ⚠️ 2026-09-20：这一段加分全是**产业要素** ——
        # 数字（growth_numbers）、关键判断、公司、证据条目、证据强度。
        # 社会故事稿这些几乎全为 0（一条民生新闻没有"公司"和"可引用数字"），
        # 于是 information_density 被系统性压在 56 分左右，
        # 而它占 overall_score 的 1/5、且 `_determine_pass_status` 里
        # `information_density <= 68` 直接判 needs_revision。
        # 结果：叙事稿永远够不到自动发布门槛（实测 overall 56~64 < 要求 70）。
        # 叙事体裁改用「人物处境 + 冲突 + 处境细节 + 来源感」来衡量信息量。
        narrative = False
        try:
            narrative = self._content_mode(data_report) == "social_story"
        except Exception:  # noqa: BLE001 - 判定不了就按产业稿算（保守）
            narrative = False

        if not narrative:
            score += min(18, len(data_report.growth_numbers) * 4)
            score += min(12, len(data_report.key_points) * 3)
            score += min(10, len(data_report.companies[:4]) * 2)
            score += min(10, len(data_report.evidence_notes[:4]) * 2)
            score += min(8, data_report.evidence_strength // 10)
        else:
            # 故事体裁：来源条数 = 可核对的事实来源，等价于产业稿的证据条目
            score += min(16, len(data_report.evidence_notes[:4]) * 4)
            # 具体细节（时间/地点/身份/金额）是故事信息量的真身
            score += min(14, self._count_story_details(content) * 3)
            score += min(10, len(getattr(data_report, "source_facts", []) or []) * 2)
            score += min(8, data_report.evidence_strength // 10)

        score += min(8, content.validation_score // 12)
        article = self._clean_article(content)
        if self._has_scene_anchor(article):
            score += 6
        if self._has_conflict_shape(article):
            score += 10 if narrative else 6
        if self._has_benefit_shift_signal(article):
            score += 6
        # 实时热榜二创：标题型热点没有完整原文/证据链是正常形态，
        # 信息密度按"观点密度 + 热点关联 + 互动引导"评估，而不是硬性数字证据
        if bool(getattr(data_report, "is_hot_topic", False)):
            score += 12
            if self._has_strong_judgment_opening(self._paragraphs(article)[0] if self._paragraphs(article) else ""):
                score += 6
            if self._has_tracking_variable_close(article):
                score += 6
        return min(98, score)

    #: 故事里的具体细节标记 —— 时间、地点、身份、金额、判决结果。
    #: 这些才是民生故事"信息量"的载体（产业稿靠数字与公司）。
    STORY_DETAIL_PATTERNS = (
        r"\d{1,2}月\d{1,2}[日号]",
        r"（?(?:广东|北京|上海|江苏|浙江|山东|河南|四川|湖南|湖北|福建|安徽|江西|河北|"
        r"陕西|辽宁|吉林|黑龙江|山西|云南|贵州|广西|甘肃|新疆|内蒙古|天津|重庆|海南|宁夏|青海|西藏)"
        r"[\u4e00-\u9fff]{0,6}(?:市|县|区|镇|村)",
        r"\d+(?:\.\d+)?\s*(?:元|万元|万|亿元)",
        r"(?:法院|法官|民警|消防|社区|居委会|民政|物业|医院)[\u4e00-\u9fff]{0,6}(?:判|认定|调解|回应|介入|表示)",
        r"(?:判决|调解|赔偿|补偿|承担)[\u4e00-\u9fff]{0,8}(?:责任|义务|损失)",
        r"(?:独居|高龄|退休|失业|外来|留守|残疾)[\u4e00-\u9fff]{0,4}",
    )

    def _count_story_details(self, content: GeneratedContent) -> int:
        """数一数正文里有多少「具体细节」。

        用于叙事体裁的信息密度：一条民生故事的信息量体现在
        「9月17日、广东佛山、80岁、赔偿400元、法院认定」这类可核对的具体信息上，
        而不是靠公司名和可引用数字。
        """
        article = self._clean_article(content)
        if not article:
            return 0
        hits = 0
        for pattern in self.STORY_DETAIL_PATTERNS:
            hits += len(re.findall(pattern, article))
        return hits

    def _score_save_potential(self, content: GeneratedContent, data_report: DataReport) -> int:
        article = self._clean_article(content)
        score = 52
        score += min(16, len(data_report.watch_metrics) * 4)
        score += min(10, len(data_report.growth_numbers[:3]) * 3)
        if any(token in article for token in ("订单", "交付", "毛利", "验证", "产能", "客户", "份额")):
            score += 8
        if self._has_benefit_shift_signal(article):
            score += 8
        if self._has_scene_anchor(article):
            score += 4
        if content.summary:
            score += 4
        return min(96, score)

    def _score_completion(self, content: GeneratedContent, opening_lines: list[str], data_report: DataReport) -> int:
        article = self._clean_article(content)
        paragraphs = self._paragraphs(article)
        first_paragraph = paragraphs[0] if paragraphs else ""
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
        if self._has_scene_anchor(article):
            score += 4
        if self._has_conflict_shape(article):
            score += 4
        if self._has_strong_judgment_opening(first_paragraph):
            score += 4
        if self._has_tracking_variable_close(article):
            score += 4
        return max(30, min(95, score))

    def _paragraph_bounds(self, data_report: DataReport) -> tuple[int, int]:
        if self._auto_publish_mode():
            return 7, 12
        if bool(getattr(data_report, "is_hot_topic", False)):
            return 5, 8
        if data_report.content_format == "quick_commentary":
            return 4, 5
        if data_report.content_format == "data_recap":
            return 4, 5
        if data_report.content_format == "project_breakdown":
            return 5, 6
        return 5, 7

    def _word_bounds(self, data_report: DataReport) -> tuple[int, int]:
        if self._auto_publish_mode():
            target = max(int(data_report.word_target or 0), 1800)
            return 1750, target + 300
        if bool(getattr(data_report, "is_hot_topic", False)):
            target = max(int(data_report.word_target or 0), 900)
            return 850, target + 150
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
        paragraphs = self._paragraphs(article)
        first_paragraph = paragraphs[0] if paragraphs else ""
        is_hot_topic = bool(getattr(data_report, "is_hot_topic", False))
        generic_hits = len(
            re.findall(
                r"值得关注的是|不难发现|换句话说|需要指出的是|从某种意义上说|可以看到|总体来看",
                article,
            )
        )
        average_length = round(sum(len(item) for item in paragraphs) / len(paragraphs)) if paragraphs else 0
        repeated_transitions = len(re.findall(r"先看|再看|最后看|真正决定", article))
        unsupported_count = len(self._unsupported_numeric_tokens(content, data_report))

        risk = 48
        if is_hot_topic:
            # 热榜快评：标题型热点无原文素材是正常形态，素材缺失不算 AI 痕迹。
            # 只保留句式模板、段落长度、套话等真正反映写作腔调的信号。
            risk -= min(8, len(data_report.companies[:4]) * 2)
            risk -= min(6, len(data_report.evidence_notes[:4]) * 2)
            risk -= min(6, content.validation_score // 16)
            if average_length > 170:
                risk += 5
        else:
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
        if not is_hot_topic:
            if not self._has_scene_anchor(article):
                risk += 4
            if not self._has_conflict_shape(article):
                risk += 5
            if not self._has_benefit_shift_signal(article):
                risk += 5
            if not self._has_strong_judgment_opening(first_paragraph):
                risk += 4
            if not self._has_tracking_variable_close(article):
                risk += 4
            else:
                risk -= 4
        if cache_context.similar_hit:
            risk -= 3
        return max(18, min(80, risk))

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
        targets = self._target_self_benchmark_patterns(context)
        if not targets:
            return []

        article = self._clean_article(content)
        title = content.recommended_titles[0] if content.recommended_titles else ""
        paragraphs = self._paragraphs(article)
        first_paragraph = paragraphs[0] if paragraphs else article
        distinct_regions = [token for token in super().CROSS_BORDER_TOKENS if token in article]
        companies = [item for item in data_report.companies[:4] if item and item in article]
        numbers = self._extract_numeric_tokens(article)
        matched: list[str] = []

        for pattern in targets:
            if pattern == "场景先行开头":
                if any(token in first_paragraph or token in title for token in super().SCENE_TOKENS):
                    matched.append(pattern)
            elif pattern == "跨国对比冲突":
                if len(distinct_regions) >= 2 and self._has_conflict_shape(article):
                    matched.append(pattern)
            elif pattern == "利润迁移框架":
                if self._has_benefit_shift_signal(article) and self._has_conflict_shape(article):
                    matched.append(pattern)
            elif pattern == "具体数字配具体主体":
                if numbers and (companies or len(distinct_regions) >= 1 or any(token in article for token in ("客户", "项目", "订单"))):
                    matched.append(pattern)
            elif pattern == "结尾保留跟踪变量":
                if self._has_tracking_variable_close(article):
                    matched.append(pattern)

        return self.unique(matched)

    def _apply_self_benchmark_adjustments(
        self,
        title_attraction: int,
        information_density: int,
        save_potential: int,
        completion_rate_potential: int,
        ai_generated_risk: int,
        *,
        self_benchmark_context: dict[str, object] | None,
        matched_patterns: list[str],
    ) -> tuple[int, int, int, int, int]:
        targets = self._target_self_benchmark_patterns(self_benchmark_context)
        if not targets:
            return (
                title_attraction,
                information_density,
                save_potential,
                completion_rate_potential,
                ai_generated_risk,
            )

        matched_count = len(matched_patterns)
        if matched_count >= 4:
            title_attraction += 2
            information_density += 6
            save_potential += 6
            completion_rate_potential += 6
            ai_generated_risk -= 6
        elif matched_count >= 3:
            title_attraction += 1
            information_density += 4
            save_potential += 4
            completion_rate_potential += 4
            ai_generated_risk -= 4
        elif matched_count == 2:
            information_density += 2
            save_potential += 2
            completion_rate_potential += 2
            ai_generated_risk -= 2
        elif matched_count == 0:
            information_density -= 4
            save_potential -= 4
            completion_rate_potential -= 3
            ai_generated_risk += 6

        return (
            max(20, min(98, title_attraction)),
            max(20, min(98, information_density)),
            max(20, min(96, save_potential)),
            max(20, min(95, completion_rate_potential)),
            max(18, min(80, ai_generated_risk)),
        )

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
        self_benchmark_context: dict[str, object] | None = None,
        matched_patterns: list[str] | None = None,
    ) -> list[str]:
        article = self._clean_article(content)
        notes: list[str] = []
        paragraphs = self._paragraphs(article)
        max_length = max((len(item) for item in paragraphs), default=0)
        target_patterns = self._target_self_benchmark_patterns(self_benchmark_context)
        matched = matched_patterns or []

        if not self._has_source_anchor(article):
            notes.append("建议补一句公开材料、财报或机构统计的出处感表达，证据会更稳。")
        if max_length >= 180:
            notes.append("正文仍有长段落，建议单段控制在 2 到 3 句，提升移动端完读率。")
        if paragraphs and not self._has_strong_judgment_opening(paragraphs[0]):
            notes.append("首段判断感还不够强，建议前三句直接回答为什么现在值得看、谁先受益。")
        if len(data_report.companies) and not any(company in article for company in data_report.companies[:3]):
            notes.append("建议在正文里点名 1 到 2 个公司或订单主体，主体感会更强。")
        if not any(token in article for token in ("订单", "交付", "验证", "毛利", "量产")):
            notes.append("可以再补一句订单、交付或毛利指标，让文章更有收藏价值。")
        if not self._has_scene_anchor(article):
            notes.append("建议补一个具体场景锚点，例如机场、电厂、园区、示范项目，增强读者停留感。")
        if not self._has_conflict_shape(article):
            notes.append("这篇稿子的冲突感还不够，建议明确写出谁受益、谁承压、利润向哪一环迁移。")
        if not self._has_tracking_variable_close(article):
            notes.append("结尾还不够像高质量分析号，建议收束到 1 到 2 个可跟踪变量，而不是泛泛总结。")
        if target_patterns and len(matched) <= 1:
            notes.append(
                f"这篇稿子还没打中你自己高表现样本里的关键结构，建议优先补齐：{'、'.join(target_patterns[:3])}。"
            )
        unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)
        if unsupported_tokens:
            notes.append(f"正文出现未锚定数字：{'、'.join(unsupported_tokens[:4])}，要删掉杜撰比例或份额。")
        if cache_context.similar_hit:
            notes.append("这次题材和历史内容接近，发布前再确认开头角度是否足够新。")

        if not notes:
            notes.append("整体结构已经可发，发布前重点再核对数字口径和公司名称。")
        return notes[:4]

    def _build_revision_suggestions(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        self_benchmark_context: dict[str, object] | None = None,
        matched_patterns: list[str] | None = None,
    ) -> list[str]:
        article = self._clean_article(content)
        paragraphs = self._paragraphs(article)
        suggestions: list[str] = []
        target_patterns = self._target_self_benchmark_patterns(self_benchmark_context)
        matched = matched_patterns or []
        # ⚠️ 2026-09-20：下面这一串建议全是产业稿口径 ——
        # 实测一条「保姆照看老人期间摔伤」的故事稿收到的建议是
        # 「补充家政行业相关数据，如纠纷率、赔付金额」「细化产业链分析，
        # 说明各环节利润分配」「增加具体公司、项目或订单主体」。
        # 这些建议对故事稿不仅无用，还会被 writer 当成改写方向，把稿子带偏。
        try:
            narrative = self._content_mode(data_report) == "social_story"
        except Exception:  # noqa: BLE001
            narrative = False

        if not self._has_source_anchor(article):
            suggestions.append("在首段或第二段补一句“从公开材料看”或具体公告/报告出处，增强证据感。")
        if max((len(item) for item in self._paragraphs(article)), default=0) >= 180:
            suggestions.append("拆短长段落，每段尽量控制在 2 到 3 句，首屏不要堆太满。")

        if narrative:
            if paragraphs and not self._has_strong_judgment_opening(paragraphs[0]):
                suggestions.append("重写前三句：第一段先把“发生了什么”讲清楚（时间、地点、涉及的人、结果）。")
            if self._count_story_details(content) < 4:
                suggestions.append("补进可核对的具体信息：时间、地点、当事人身份、金额或处理结果。")
            if not self._has_conflict_shape(article):
                suggestions.append("把当事人的处境和两难写出来：他当时能怎么选、哪一步本可以不一样。")
            if not self._has_tracking_variable_close(article):
                suggestions.append("最后一段给读者一句具体可用的提醒（该留什么证据、找谁、走什么流程）。")
            unsupported_tokens = self._unsupported_numeric_tokens(content, data_report)
            if unsupported_tokens:
                suggestions.append("删掉素材里没有明确出现的新数字和细节，证据不够就留白。")
            if not suggestions:
                suggestions.append("保持当前主线不变，重点再压一压首段句长即可。")
            return suggestions[:4]

        if paragraphs and not self._has_strong_judgment_opening(paragraphs[0]):
            suggestions.append("重写前三句，先写判断，再写数据，再点出谁先受益或哪一环先兑现。")
        if not any(company in article for company in data_report.companies[:3]):
            suggestions.append("增加具体公司、项目或订单主体，避免写成泛行业评论。")
        if not any(token in article for token in ("订单", "交付", "验证", "毛利", "客户")):
            suggestions.append("补充订单、交付、客户验证或毛利变化，提升文章的收藏价值。")
        if not self._has_scene_anchor(article):
            suggestions.append("首段补进具体落地场景，不要只写赛道判断，最好能落到机场、电厂、园区或示范项目。")
        if not self._has_conflict_shape(article):
            suggestions.append("增加反差句和利益迁移句，明确写出谁先吃到利润、谁仍在价格战或验证期。")
        if not self._has_tracking_variable_close(article):
            suggestions.append("最后一段改成跟踪变量清单，明确后续该盯订单转化、并网节奏、回款、毛利或客户验收。")
        if target_patterns and len(matched) <= 1:
            suggestions.append(
                f"按你自己的高表现样本补结构：优先把{'、'.join(target_patterns[:3])}压进开头和结尾。"
            )
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
        self_benchmark_context: dict[str, object] | None = None,
        matched_patterns: list[str] | None = None,
    ) -> list[str]:
        lines = [
            f"标题吸引力 {title_attraction}，主要看数字、判断感、主体感和长度控制。",
            f"信息密度 {information_density}，核心取决于数字、公司、产业链和跟踪指标是否齐全。",
            f"完读率潜力 {completion_rate_potential}，重点看首屏判断是否清楚、段落是否适合手机阅读。",
            f"AI 痕迹风险 {ai_generated_risk}，分数越高越像模板稿，通常需要继续压缩套话和长句。",
        ]
        target_patterns = self._target_self_benchmark_patterns(self_benchmark_context)
        matched = matched_patterns or []
        if target_patterns:
            lines[1] = (
                f"自有爆款模式命中 {len(matched)}/{len(target_patterns[:5])}，当前命中项：{'、'.join(matched[:4]) or '暂无'}。"
            )
        return lines[:4]

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
        # 文章已在 writer_agent._finalize_article 中清洗过，不再重复清洗以避免截断补充内容
        return article

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

    def _has_strong_judgment_opening(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if "不是" in normalized and "而是" in normalized:
            return True
        return any(token in normalized for token in ("真正", "背后", "利润", "毛利", "订单", "交付", "兑现", "分化"))

    def _has_tracking_variable_close(self, article: str) -> bool:
        paragraphs = self._paragraphs(article)
        if not paragraphs:
            return False
        closing = "\n".join(paragraphs[-2:]) if len(paragraphs) >= 2 else paragraphs[-1]
        return any(
            token in closing
            for token in ("接下来最值得跟踪", "后续最值得跟踪", "真正值得跟踪", "继续跟踪", "变量", "订单转化", "并网节奏", "回款", "客户验收")
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

    def _has_scene_anchor(self, article: str) -> bool:
        return any(token in article for token in super().SCENE_TOKENS)

    def _has_conflict_shape(self, article: str) -> bool:
        if "不是" in article and "而是" in article:
            return True
        return any(token in article for token in super().CROSS_BORDER_TOKENS) or any(token in article for token in ("分化", "改写", "重估", "转移", "迁移", "突围", "挤压", "洗牌"))

    def _has_benefit_shift_signal(self, article: str) -> bool:
        return any(token in article for token in super().BENEFIT_SHIFT_TOKENS)

    def _unsupported_numeric_tokens(self, content: GeneratedContent, data_report: DataReport) -> list[str]:
        article = self._clean_article(content)
        return super()._unsupported_numeric_tokens(article, data_report)


    def _normalize_pass_status(self, value: object) -> str:
        normalized = str(value).strip()
        if normalized in {"ready", "ready_with_light_edit", "needs_revision"}:
            return normalized
        return "ready_with_light_edit"
