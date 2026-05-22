from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.article_sanitizer import clean_article_text


class WriterAgent(BaseAgent):
    agent_name = "WriterAgent"

    COMPANY_SUFFIX_RE = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,12}(?:科技|能源|汽车|股份|集团|电气|智能|机器人|半导体|电子|时代|精工|制造|材料|电池|重工|工业|光伏|储能|动力|机电|控股|装备|技术)"
    )
    INVALID_COMPANY_MARKERS = {
        "这家公司",
        "某公司",
        "相关公司",
        "龙头公司",
        "头部企业",
        "企业",
        "公司",
        "厂商",
        "供应商",
        "项目方",
        "客户",
        "车企",
    }
    INVALID_COMPANY_FRAGMENTS = {
        "更像",
        "再落到",
        "再拆",
        "资本",
        "开始",
        "真正",
        "故事",
        "机会",
        "验证",
        "订单",
        "交付",
        "利润",
        "估值",
        "背后",
    }
    INVALID_ANGLE_FRAGMENTS = (
        "这不只是",
        "更像",
        "提前卡位",
        "估值故事",
        "订单能否接上",
        "融资新闻",
        "宏观",
        "资金向",
        "赛道聚集",
        "出口数据翻倍",
        "验证节点",
    )
    ORDER_ANCHOR_TOKENS = ("订单", "中标", "客户", "定点", "签约", "项目", "交付", "量产", "验收", "采购")
    SCENE_TOKENS = ("机场", "电厂", "电站", "港口", "车间", "仓库", "园区", "工厂", "基地", "物流中心", "示范项目", "生产线")
    CROSS_BORDER_TOKENS = ("中国", "日本", "美国", "欧洲", "德国", "东京", "海外", "出海", "国际", "日韩")
    BENEFIT_SHIFT_TOKENS = ("利润", "毛利", "份额", "议价", "红利", "系统集成", "零部件", "整机", "运维", "服务", "回款")
    SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")

    MODE_NOISE_TOKENS = ("产业链", "订单", "交付", "毛利", "利润", "兑现", "融资", "客户验证", "科技制造", "项目落地")

    MODE_NOISE_TOKENS = ("产业链", "订单", "交付", "毛利", "利润", "兑现", "融资", "客户验证", "科技制造", "项目落地")
    SPORTS_MODE_TOKENS = ("体育", "足球", "篮球", "网球", "world cup", "football", "tennis", "french open", "grand slam")
    INTERNATIONAL_MODE_TOKENS = ("国际", "美国", "欧洲", "关税", "外交", "地缘", "国际热点", "white house", "tariff")

    def run(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> GeneratedContent:
        benchmark_track = self.clean_publish_text(data_report.focus_industry).strip() or self.infer_benchmark_track(
            data_report.topic_title,
            data_report.focus_keywords,
            data_report.supply_chain_keywords,
            trend_report.recommended_angle,
        )
        self_benchmark_context = self.growth_ops_service.get_self_benchmark_track_context(benchmark_track)
        benchmark_digest = self.growth_ops_service.build_benchmark_digest(benchmark_track, purpose="writer")
        prompt = self.render_prompt(
            "article_prompt_v2.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            audience=self.settings.audience,
            style_digest=self.style_digest(style),
            brand_digest=self.brand_digest(extra_sections=[benchmark_digest]),
            cache_note=self.cache_note(cache_context),
            trend_json=self.json_text(trend_report.__dict__),
            data_json=self.json_text(data_report.__dict__),
            article_word_target=str(data_report.word_target or self.settings.article_word_target),
            content_format_label=data_report.content_format_label or "深度分析",
            format_brief=data_report.format_brief or "写成完整产业分析稿。",
            paragraph_instruction=data_report.paragraph_instruction or "5到7段，每段2到3句。",
            evidence_strength=str(data_report.evidence_strength or 0),
            topic_quality_score=str(data_report.topic_quality_score or 0),
        )
        payload = self.request_json("你是 WriterAgent，只输出 JSON。", prompt)
        if payload:
            opening_lines = self._finalize_opening_lines(
                payload.get("opening_lines"),
                trend_report,
                data_report,
                style,
                benchmark_track=benchmark_track,
                self_benchmark_context=self_benchmark_context,
            )
            comment_prompt = self._finalize_comment_prompt(payload.get("comment_prompt"), data_report)
            article = self._finalize_article(
                str(payload.get("article", "")).strip(),
                trend_report,
                data_report,
                comment_prompt=comment_prompt,
                benchmark_track=benchmark_track,
                self_benchmark_context=self_benchmark_context,
            )
            summary = self._finalize_summary(payload.get("summary"), article, trend_report, data_report)
            tags = self._sanitize_tags(payload.get("tags"), data_report)
            recommended_topics = self._finalize_topics(payload.get("recommended_topics"), data_report, trend_report)
            return GeneratedContent(
                article=article,
                summary=summary,
                comment_prompt=comment_prompt,
                article_angle=self._article_angle(trend_report, data_report),
                style_name=style.label,
                opening_lines=opening_lines,
                tags=tags,
                recommended_topics=recommended_topics,
                cache_note=cache_context.note,
                content_format=data_report.content_format,
                content_format_label=data_report.content_format_label,
            )
        return self._fallback(
            trend_report,
            data_report,
            style,
            cache_context,
            benchmark_track=benchmark_track,
            self_benchmark_context=self_benchmark_context,
        )

    def _fallback(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
        benchmark_track: str = "",
        self_benchmark_context: dict[str, object] | None = None,
    ) -> GeneratedContent:
        opening_lines = self._build_opening_lines(
            trend_report,
            data_report,
            style,
            benchmark_track=benchmark_track,
            self_benchmark_context=self_benchmark_context,
        )
        comment_prompt = self._default_comment_prompt(data_report)
        article = self._build_article(
            opening_lines,
            trend_report,
            data_report,
            comment_prompt,
            benchmark_track=benchmark_track,
            self_benchmark_context=self_benchmark_context,
        )
        summary = self._build_summary(article, trend_report, data_report)
        tags = self._sanitize_tags(None, data_report)
        recommended_topics = self._finalize_topics(None, data_report, trend_report)
        return GeneratedContent(
            article=article,
            summary=summary,
            comment_prompt=comment_prompt,
            article_angle=self._article_angle(trend_report, data_report),
            style_name=style.label,
            opening_lines=opening_lines,
            tags=tags,
            recommended_topics=recommended_topics,
            cache_note=cache_context.note,
            content_format=data_report.content_format,
            content_format_label=data_report.content_format_label,
        )

    def _build_article(
        self,
        opening_lines: list[str],
        trend_report: TrendReport,
        data_report: DataReport,
        comment_prompt: str,
        benchmark_track: str = "",
        self_benchmark_context: dict[str, object] | None = None,
    ) -> str:
        primary = self._primary_keyword(data_report)
        angle = self._article_angle(trend_report, data_report)
        core_data = self._pick_items(data_report.core_data, limit=3)
        trends = self._pick_items(data_report.industry_trends, limit=3)
        key_points = self._pick_items(data_report.key_points, limit=3)
        metrics = self._pick_items(data_report.watch_metrics, limit=4)
        risks = self._pick_items(data_report.risk_flags, limit=3)
        evidence = self._best_evidence_note(data_report)
        content_format = data_report.content_format or "deep_analysis"
        scene_anchor = self._scene_anchor(data_report, trend_report)
        content_mode = self._content_mode(data_report)

        if content_mode == "sports":
            paragraphs = self._build_sports_article(
                opening_lines,
                trend_report,
                data_report,
                comment_prompt,
            )
            raw_article = "\n\n".join(part for part in paragraphs if part.strip())
            return self._finalize_article(
                raw_article,
                trend_report,
                data_report,
                comment_prompt=comment_prompt,
                benchmark_track=benchmark_track,
                self_benchmark_context=self_benchmark_context,
            )

        if content_mode == "international":
            paragraphs = self._build_international_article(
                opening_lines,
                trend_report,
                data_report,
                comment_prompt,
            )
            raw_article = "\n\n".join(part for part in paragraphs if part.strip())
            return self._finalize_article(
                raw_article,
                trend_report,
                data_report,
                comment_prompt=comment_prompt,
                benchmark_track=benchmark_track,
                self_benchmark_context=self_benchmark_context,
            )

        if content_format == "quick_commentary":
            paragraphs = [
                " ".join(opening_lines[:3]),
                self._build_data_paragraph(primary, core_data[:2], evidence),
                self._build_company_or_order_anchor(data_report),
                self._build_risk_paragraph(risks[:2], comment_prompt),
            ]
        elif content_format == "project_breakdown":
            paragraphs = [
                " ".join(opening_lines[:3]),
                self._build_company_or_order_anchor(data_report),
                self._build_data_paragraph(primary, core_data, evidence),
                self._build_trend_paragraph(primary, trends, key_points, angle),
                self._build_metrics_paragraph(metrics),
                self._build_risk_paragraph(risks, comment_prompt),
            ]
        elif content_format == "data_recap":
            paragraphs = [
                " ".join(opening_lines[:3]),
                self._build_data_paragraph(primary, core_data, evidence),
                self._build_trend_paragraph(primary, trends[:2], key_points[:2], angle),
                self._build_metrics_paragraph(metrics[:3]),
                self._build_risk_paragraph(risks[:2], comment_prompt),
            ]
        else:
            paragraphs = [
                " ".join(opening_lines[:3]),
                self._build_conflict_or_scene_paragraph(
                    primary,
                    scene_anchor,
                    data_report,
                    trend_report,
                    benchmark_track=benchmark_track,
                    self_benchmark_context=self_benchmark_context,
                ),
                self._build_data_paragraph(primary, core_data, evidence),
                self._build_trend_paragraph(primary, trends, key_points, angle),
                self._build_company_or_order_anchor(data_report),
                self._build_metrics_paragraph(metrics),
                self._build_risk_paragraph(risks, comment_prompt),
            ]
        raw_article = "\n\n".join(part for part in paragraphs if part.strip())
        return self._finalize_article(
            raw_article,
            trend_report,
            data_report,
            comment_prompt=comment_prompt,
            benchmark_track=benchmark_track,
            self_benchmark_context=self_benchmark_context,
        )

    def _build_opening_lines(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        benchmark_track: str = "",
        self_benchmark_context: dict[str, object] | None = None,
    ) -> list[str]:
        primary = self._primary_keyword(data_report)
        company = self._primary_company(data_report)
        number = data_report.growth_numbers[0] if data_report.growth_numbers else ""
        angle = self._article_angle(trend_report, data_report)
        content_format = data_report.content_format or "deep_analysis"
        scene_anchor = self._scene_anchor(data_report, trend_report)
        contrast_line = self._contrast_line(data_report, trend_report)
        patterns = self._self_benchmark_patterns(self_benchmark_context)
        resolved_track = benchmark_track or self.clean_publish_text(data_report.focus_industry).strip() or self.infer_benchmark_track(
            data_report.topic_title,
            data_report.focus_keywords,
            data_report.supply_chain_keywords,
            angle,
        )
        content_mode = self._content_mode(data_report)

        if content_mode == "sports":
            localized_primary = self._localize_sports_text(primary)
            lines = [
                f"{scene_anchor or '这条名单消息'}不只是赛前信息，它很可能直接改变外界对下一阶段比赛的判断。",
                f"{localized_primary}现在真正值得看的，不是谁上了热搜，而是谁掌握了后续走势里的主动权。",
                self._short_angle_line(angle or "真正的看点不在表面名单，而在阵容选择背后的取舍"),
            ]
            return self._normalize_opening_lines(lines)

        if content_mode == "international":
            lines = [
                f"{scene_anchor or '这条国际消息'}不能只当单点新闻看，它背后往往连着更大的博弈变化。",
                f"{primary}现在最值得看的，不是谁先表态，而是谁在真实让步、谁在继续施压。",
                self._short_angle_line(angle or "真正的变化不在表态本身，而在下一步动作"),
            ]
            return self._normalize_opening_lines(lines)

        if content_format == "quick_commentary":
            lines = [
                f"{scene_anchor or number or '这条新进展'}不能只当新闻看。",
                f"{primary}现在更该看谁先把执行层信号做实。",
                self._short_angle_line(angle),
            ]
            return self._normalize_opening_lines(lines)

        if content_format == "data_recap":
            lines = [
                f"{number or '这组数据'}不是结果，而是{primary}重新定价的起点。",
                f"{primary}接下来真正要看的是哪些环节先把数字变成订单和利润。",
                self._short_angle_line(angle),
            ]
            return self._normalize_opening_lines(lines)

        if content_format == "project_breakdown":
            lines = [
                f"{scene_anchor or company or primary}这次变化，关键不在热度而在兑现链条。",
                f"{primary}真正值得拆的是主体位置、交付节奏和利润归属。",
                self._short_angle_line(angle),
            ]
            return self._normalize_opening_lines(lines)

        if content_format == "deep_analysis" and patterns:
            opening_lead = self._self_benchmark_opening_lead(
                primary=primary,
                scene_anchor=scene_anchor,
                contrast_line=contrast_line,
                number=number,
                patterns=patterns,
            )
            if opening_lead:
                lines = [
                    opening_lead,
                    self._self_benchmark_second_line(primary, data_report, trend_report, patterns),
                    self._short_angle_line(angle),
                ]
                return self._normalize_opening_lines(lines)

        if "储能" in resolved_track or "新能源" in resolved_track:
            lines = [
                f"{number or scene_anchor or '这轮项目变化'}不是景气平均抬升，而是{primary}开始拼并网、回款和毛利。",
                f"{primary}现在真正该看的，不是签约数量有多热闹，而是谁先把订单变成出货和现金流。",
                self._short_angle_line(angle),
            ]
            return self._normalize_opening_lines(lines)

        if "机器人" in resolved_track:
            lines = [
                f"{scene_anchor or number or '这轮机器人进展'}不是演示更热闹，而是{primary}开始进入客户验证和交付筛选。",
                f"{primary}接下来分胜负的关键，不是讲故事能力，而是谁先把场景、订单和交付跑通。",
                self._short_angle_line(angle),
            ]
            return self._normalize_opening_lines(lines)

        if style.key == "finance_report":
            lines = [
                f"{number or '这组数据'}不是终点，而是{primary}重新定价的起点。",
                "市场接下来不会平均奖励所有公司，只会奖励先把订单和交付兑现出来的那一批。",
                self._short_angle_line(angle),
            ]
        elif style.key == "tech_media":
            lines = [
                f"{scene_anchor or company or primary}背后的执行层信号，比概念热度更值得看。",
                contrast_line or f"{number or '这轮变化'}之后，行业真正分化的往往是客户验证、交付节奏和毛利兑现。",
                self._short_angle_line(angle),
            ]
        else:
            lines = [
                contrast_line or f"{number or '这轮变化'}不等于行业已经全面起飞，它更像是筛选真正受益者的开始。",
                f"{primary}现在最重要的不是故事，而是谁能把订单、交付和利润串起来。",
                self._short_angle_line(angle),
            ]
        return self._normalize_opening_lines(lines)

    def _build_data_paragraph(self, primary: str, core_data: list[str], evidence: str) -> str:
        data_text = "；".join(core_data) if core_data else "公开信息还不足以支撑过度乐观的结论"
        if evidence:
            return (
                f"先落到数据层面。{data_text}。"
                f"{evidence}"
                f"这至少说明，{primary}已经不只是概念热度，而是在往真实项目、客户需求和回款节奏上走。"
            )
        return f"先落到数据层面。{data_text}。这至少说明，{primary}已经开始从预期叙事转向执行验证。"

    def _build_trend_paragraph(
        self,
        primary: str,
        trends: list[str],
        key_points: list[str],
        angle: str,
    ) -> str:
        trend_text = "；".join(trends[:2]) if trends else ""
        point_text = "；".join(key_points[:2]) if key_points else ""
        body = "；".join(part for part in (trend_text, point_text) if part)
        if body:
            return (
                f"但真正值得拆开的，不只是事件热度，而是{primary}内部的兑现节奏。"
                f"{body}。"
                f"{self._long_angle_line(angle)}"
            )
        return f"{self._long_angle_line(angle)}市场更看重执行层兑现，而不是停留在题材热度。"

    def _build_conflict_or_scene_paragraph(
        self,
        primary: str,
        scene_anchor: str,
        data_report: DataReport,
        trend_report: TrendReport,
        benchmark_track: str = "",
        self_benchmark_context: dict[str, object] | None = None,
    ) -> str:
        article_text = " ".join(
            [
                data_report.topic_title,
                *data_report.core_data[:3],
                *data_report.key_points[:3],
                *data_report.evidence_notes[:3],
                trend_report.recommended_angle,
            ]
        )
        benefit_shift = self._benefit_shift_line(data_report, trend_report)
        patterns = self._self_benchmark_patterns(self_benchmark_context)
        resolved_track = benchmark_track or self.clean_publish_text(data_report.focus_industry).strip()
        if scene_anchor:
            if "场景先行开头" in patterns:
                return (
                    f"{scene_anchor}之所以值得写，不是因为它本身有多大，而是它把{primary}执行层的筛选信号提前暴露了出来。"
                    f"{benefit_shift}"
                )
            return (
                f"{scene_anchor}之所以值得写，不是因为它本身有多大，而是它把{primary}的真实执行层信号提前暴露了出来。"
                f"{benefit_shift}"
            )
        if any(token in article_text for token in self.CROSS_BORDER_TOKENS):
            if "跨国对比冲突" in patterns or "机器人" in resolved_track:
                return (
                    f"这条线真正有意思的地方，不只是新闻热度，而是跨区域主体正在用交付效率和成本控制重写{primary}的分工。"
                    f"{benefit_shift}"
                )
            return (
                f"这条线真正有意思的地方，不是新闻热度，而是跨区域、跨主体的竞争关系已经开始改写{primary}的分工。"
                f"{benefit_shift}"
            )
        return (
            f"真正需要拆开的，不只是项目本身，而是{primary}这条链上的权重正在重新分配。"
            f"{benefit_shift}"
        )

    def _build_metrics_paragraph(self, metrics: list[str]) -> str:
        metric_text = "、".join(metrics[:4]) if metrics else "订单兑现、客户验证、项目交付和毛利变化"
        return (
            f"接下来最值得持续跟踪的，不是新闻条数，而是{metric_text}。"
            "这些指标会直接回答一件事：行业还停留在预期里，还是已经开始把收入和利润落到报表里。"
        )

    def _build_risk_paragraph(self, risks: list[str], comment_prompt: str) -> str:
        risk_text = "；".join(risks[:3]) if risks else "如果订单兑现慢于扩产节奏，短期景气很容易先热后冷"
        question = comment_prompt.strip() or self._default_comment_prompt(None)
        if question and question[-1] not in "？?":
            question += "？"
        return f"风险边界也要讲清楚。{risk_text}。{question}"

    def _build_sports_article(
        self,
        opening_lines: list[str],
        trend_report: TrendReport,
        data_report: DataReport,
        comment_prompt: str,
    ) -> list[str]:
        title = self._localize_sports_text(self.clean_publish_text(data_report.topic_title).strip()) or "这条体育消息"
        summary_bits = self._sports_summary_bits(data_report)
        follow_text = self._sports_follow_text(data_report)
        angle = self._localize_sports_text(self._article_angle(trend_report, data_report))
        issue_text = self._sports_issue_text(data_report, trend_report)
        trend_text = "；".join(self._localize_sports_text(item) for item in self._pick_items(data_report.industry_trends, 2))
        evidence = self._localize_sports_text(self._best_evidence_note(data_report))
        risk_text = "；".join(self._localize_sports_text(item) for item in self._pick_items(data_report.risk_flags, 2)) or "目前公开信息仍有限，真正的影响还要看后续名单、赛程和临场表现"
        if evidence.startswith("Current public material centers on"):
            evidence = ""

        paragraphs = [
            " ".join(opening_lines[:3]),
            f"{title}之所以会有讨论度，不是因为信息量有多大，而是它碰到的正是球迷最敏感的判断点：{issue_text}。",
            f"从现有素材看，最值得抓住的线索是{summary_bits}。{evidence or '这至少说明，外界对后续阵容与比赛走势的预期，已经开始被重新改写。'}",
            f"如果把这件事放到后续比赛里看，真正要盯的不是情绪本身，而是{follow_text}。这会直接决定这条消息最终是普通噪音，还是会变成影响赛果的前置信号。",
            f"{angle}。{('另外，' + trend_text + '。') if trend_text else ''}{risk_text}。",
            comment_prompt.strip() or self._default_comment_prompt(data_report),
        ]
        return [item for item in paragraphs if item.strip()]

    def _build_international_article(
        self,
        opening_lines: list[str],
        trend_report: TrendReport,
        data_report: DataReport,
        comment_prompt: str,
    ) -> list[str]:
        title = self.clean_publish_text(data_report.topic_title).strip() or "这条国际消息"
        summary_bits = self._international_summary_bits(data_report)
        follow_text = self._international_follow_text(data_report)
        angle = self._article_angle(trend_report, data_report)
        issue_text = self._international_issue_text(data_report, trend_report)
        trend_text = "；".join(self._pick_items(data_report.industry_trends, 2))
        evidence = self._best_evidence_note(data_report)
        risk_text = "；".join(self._pick_items(data_report.risk_flags, 2)) or "目前公开信息仍有限，接下来还要看政策动作、谈判进展和市场反馈"

        paragraphs = [
            " ".join(opening_lines[:3]),
            f"{title}真正值得看的，不是表面表态，而是{issue_text}。这通常意味着各方的底线、筹码和时间窗口正在重新变化。",
            f"从现有素材看，最应该先看懂的是{summary_bits}。{evidence or '这至少说明，事件已经不再只是口头层面，而是在逼近更具体的动作选择。'}",
            f"接下来真正值得跟踪的，是{follow_text}。这些变量会决定局势是继续升级、短暂缓和，还是转向新的谈判与博弈阶段。",
            f"{angle}。{('另外，' + trend_text + '。') if trend_text else ''}{risk_text}。",
            comment_prompt.strip() or self._default_comment_prompt(data_report),
        ]
        return [item for item in paragraphs if item.strip()]

    def _finalize_article(
        self,
        article: str,
        trend_report: TrendReport,
        data_report: DataReport,
        comment_prompt: str,
        benchmark_track: str = "",
        self_benchmark_context: dict[str, object] | None = None,
    ) -> str:
        content_mode = self._content_mode(data_report)
        cleaned = self.strip_prompt_leakage(article)
        if content_mode in {"sports", "international"}:
            cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").strip()
            if self._should_rebuild_mode_article(cleaned):
                rebuilt = (
                    self._build_sports_article(
                        self._build_opening_lines(
                            trend_report,
                            data_report,
                            self.settings.get_style(None),
                            benchmark_track=benchmark_track,
                            self_benchmark_context=self_benchmark_context,
                        ),
                        trend_report,
                        data_report,
                        comment_prompt,
                    )
                    if content_mode == "sports"
                    else self._build_international_article(
                        self._build_opening_lines(
                            trend_report,
                            data_report,
                            self.settings.get_style(None),
                            benchmark_track=benchmark_track,
                            self_benchmark_context=self_benchmark_context,
                        ),
                        trend_report,
                        data_report,
                        comment_prompt,
                    )
                )
                cleaned = "\n\n".join(rebuilt)
        else:
            cleaned = clean_article_text(cleaned)
        paragraphs = [self._normalize_paragraph_text(block) for block in cleaned.split("\n\n") if block.strip()]
        paragraphs = [block for block in paragraphs if block]

        if not paragraphs:
            if content_mode == "sports":
                paragraphs = self._build_sports_article(
                    self._build_opening_lines(
                        trend_report,
                        data_report,
                        self.settings.get_style(None),
                        benchmark_track=benchmark_track,
                        self_benchmark_context=self_benchmark_context,
                    ),
                    trend_report,
                    data_report,
                    comment_prompt,
                )
            elif content_mode == "international":
                paragraphs = self._build_international_article(
                    self._build_opening_lines(
                        trend_report,
                        data_report,
                        self.settings.get_style(None),
                        benchmark_track=benchmark_track,
                        self_benchmark_context=self_benchmark_context,
                    ),
                    trend_report,
                    data_report,
                    comment_prompt,
                )
            else:
                paragraphs = [
                    self._normalize_paragraph_text(
                        " ".join(
                            self._build_opening_lines(
                                trend_report,
                                data_report,
                                self.settings.get_style(None),
                                benchmark_track=benchmark_track,
                                self_benchmark_context=self_benchmark_context,
                            )
                        )
                    ),
                    self._build_company_or_order_anchor(data_report),
                    self._build_metrics_paragraph(self._pick_items(data_report.watch_metrics, 4)),
                ]

        if content_mode == "generic" and not self._has_concrete_anchor("\n\n".join(paragraphs), data_report):
            anchor = self._build_company_or_order_anchor(data_report)
            if anchor:
                insert_at = 3 if len(paragraphs) >= 3 else len(paragraphs)
                paragraphs.insert(insert_at, anchor)

        paragraphs = self._rebalance_paragraphs(paragraphs, data_report.content_format or "deep_analysis")
        if content_mode == "generic":
            paragraphs = self._apply_self_benchmark_structure(
                paragraphs,
                trend_report,
                data_report,
                self_benchmark_context=self_benchmark_context,
            )
        paragraphs = self._ensure_closing_question(paragraphs, comment_prompt or self._default_comment_prompt(data_report))
        _min_paragraphs, max_paragraphs = self._paragraph_bounds(data_report.content_format or "deep_analysis")
        article_text = "\n\n".join(paragraphs[:max_paragraphs]).strip()
        if content_mode == "sports":
            article_text = self._localize_sports_text(article_text)
            article_text = re.sub(r"\bThe joy and despair of\b", "", article_text, flags=re.I)
            article_text = re.sub(r"\bIn, or out\?\b", "谁进谁出", article_text, flags=re.I)
        return article_text

    def _should_rebuild_mode_article(self, article: str) -> bool:
        text = self.clean_publish_text(article).strip()
        if not text:
            return True
        if "manual_paste" in text.lower():
            return True
        if any(token in text for token in self.MODE_NOISE_TOKENS):
            return True
        if re.search(r"(^|\n)\s*[A-Za-z][A-Za-z0-9 ,:'?!.\-]{18,}", text):
            return True
        return False

    def _finalize_summary(
        self,
        raw_summary: object,
        article: str,
        trend_report: TrendReport,
        data_report: DataReport,
    ) -> str:
        summary = self.strip_prompt_leakage(str(raw_summary or "")).replace("\n", " ").strip()
        summary = re.sub(r"\s+", " ", summary)
        if summary and len(summary) <= 120:
            return summary.rstrip("。") + "。"
        return self._build_summary(article, trend_report, data_report)

    def _build_summary(self, article: str, trend_report: TrendReport, data_report: DataReport) -> str:
        content_mode = self._content_mode(data_report)
        primary = self._primary_keyword(data_report)
        angle = self._article_angle(trend_report, data_report)
        first_sentence = self._first_sentence(article)
        if first_sentence:
            return f"{first_sentence}{self._summary_tail(primary, angle)}"
        if content_mode == "sports":
            return "这条体育热点真正值得看的，不是表面消息本身，而是它会不会改变后续比赛预期、阵容选择和舆论走势。"
        if content_mode == "international":
            return "这条国际热点真正值得看的，不是表态本身，而是谁在施压、谁在让步，以及下一步局势会如何变化。"
        return f"{primary}正在从题材热度转向执行验证，真正决定下一阶段强弱的，是订单、交付和毛利谁先兑现。"

    def _summary_tail(self, primary: str, angle: str) -> str:
        tail = self.clean_publish_text(angle).strip()
        if not tail:
            return f"{primary}接下来更该盯的是订单、交付和利润兑现。"
        if tail[-1] not in "。！？!?":
            tail += "。"
        return tail

    def _finalize_comment_prompt(self, raw_prompt: object, data_report: DataReport | None) -> str:
        text = self.strip_prompt_leakage(str(raw_prompt or "")).replace("\n", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            return self._default_comment_prompt(data_report)
        return text.rstrip("。！？!?") + "？"

    def _default_comment_prompt(self, data_report: DataReport | None) -> str:
        content_mode = self._content_mode(data_report)
        primary = self._primary_keyword(data_report) if data_report is not None else "这个赛道"
        content_format = data_report.content_format if data_report is not None else ""
        if content_mode == "sports":
            return "你觉得这条消息最可能改变阵容选择、比赛走势还是舆论预期"
        if content_mode == "international":
            return "你觉得接下来最值得盯的是谁会先让步、谁会继续施压，还是市场会先给出反应"
        if content_format == "data_recap":
            return f"你觉得{primary}下一步更该盯需求扩张、价格变化还是利润兑现"
        if content_format == "project_breakdown":
            return f"你更看好{primary}里哪类项目最先把订单兑现成利润"
        if content_format == "quick_commentary":
            return f"这条新进展里，你最看重客户验证、交付节奏还是毛利变化"
        return f"你更看好{primary}里哪个环节最先把订单兑现成利润？"

    def _finalize_opening_lines(
        self,
        payload: object,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        benchmark_track: str = "",
        self_benchmark_context: dict[str, object] | None = None,
    ) -> list[str]:
        if isinstance(payload, list):
            lines = [self.clean_publish_text(str(item).strip()) for item in payload if str(item).strip()]
        else:
            lines = []
        if len(lines) < 3:
            lines = self._build_opening_lines(
                trend_report,
                data_report,
                style,
                benchmark_track=benchmark_track,
                self_benchmark_context=self_benchmark_context,
            )
        normalized = self._normalize_opening_lines(lines[:3])
        patterns = self._self_benchmark_patterns(self_benchmark_context)
        if patterns and normalized:
            first_line = normalized[0]
            if (
                ("场景先行开头" in patterns and not any(token in first_line for token in self.SCENE_TOKENS))
                or ("跨国对比冲突" in patterns and "不是" not in first_line and "而是" not in first_line)
            ):
                return self._build_opening_lines(
                    trend_report,
                    data_report,
                    style,
                    benchmark_track=benchmark_track,
                    self_benchmark_context=self_benchmark_context,
                )
        return normalized

    def _normalize_opening_lines(self, lines: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in lines:
            text = self.strip_prompt_leakage(self.clean_publish_text(item)).strip()
            text = re.sub(r"^#{1,6}\s*", "", text)
            text = text.rstrip("。！？!?")
            if not text:
                continue
            cleaned.append(text + "。")
        return self.unique(cleaned)[:3]

    def _finalize_topics(
        self,
        payload: object,
        data_report: DataReport,
        trend_report: TrendReport,
    ) -> list[str]:
        topics = self._clean_list(payload, 5) if isinstance(payload, list) else []
        if not topics:
            topics = self.clean_publish_list(
                data_report.focus_keywords + data_report.supply_chain_keywords + trend_report.platform_topics,
                limit=5,
            )
        return [item.lstrip("#") for item in topics[:5]]

    def _article_angle(self, trend_report: TrendReport, data_report: DataReport) -> str:
        content_mode = self._content_mode(data_report)
        if content_mode in {"sports", "international"}:
            for candidate in (
                data_report.suggested_angle,
                trend_report.recommended_angle,
                *data_report.key_points[:3],
                *trend_report.competitive_edges[:2],
            ):
                text = self.clean_publish_text(str(candidate).strip())
                if text and len(text) <= 120:
                    return text.rstrip("。")
        for candidate in (
            data_report.suggested_angle,
            trend_report.recommended_angle,
            *data_report.key_points[:3],
            *trend_report.competitive_edges[:2],
        ):
            text = self._normalize_angle_candidate(candidate)
            if text:
                return text.rstrip("。")
        primary = self._primary_keyword(data_report)
        return f"{primary}真正要看的，是订单、交付与毛利谁先兑现"

    def _content_mode(self, data_report: DataReport | None) -> str:
        if data_report is None:
            return "generic"
        combined = " ".join(
            str(item)
            for item in (
                data_report.focus_industry,
                data_report.topic_title,
                data_report.selection_reason,
                data_report.writing_brief,
                data_report.format_brief,
                " ".join(data_report.focus_keywords[:4]),
                " ".join(data_report.supply_chain_keywords[:4]),
            )
            if str(item).strip()
        ).lower()
        if any(token in combined for token in self.SPORTS_MODE_TOKENS):
            return "sports"
        if any(token in combined for token in self.INTERNATIONAL_MODE_TOKENS):
            return "international"
        if any(token in combined for token in ("体育", "足球", "篮球", "网球", "world cup", "football", "tennis", "french open", "grand slam")):
            return "sports"
        if any(token in combined for token in ("国际", "美国", "欧洲", "关税", "外交", "地缘", "国际热点", "white house", "tariff")):
            return "international"
        return "generic"

    def _sports_summary_bits(self, data_report: DataReport) -> str:
        items = self._mode_clean_items(
            data_report.core_data + data_report.key_points + data_report.evidence_notes,
            limit=3,
            blocked=self.MODE_NOISE_TOKENS,
        )
        if items:
            return "、".join(self._localize_sports_text(item) for item in items[:3])
        title = self._localize_sports_text(self.clean_publish_text(data_report.topic_title).strip())
        return title or "这条消息触发的阵容与走势变化"

    def _sports_follow_text(self, data_report: DataReport) -> str:
        items = self._mode_clean_items(data_report.watch_metrics, limit=4, blocked=self.MODE_NOISE_TOKENS)
        if items:
            return "、".join(self._localize_sports_text(item) for item in items)
        if data_report.follow_up_focus.strip():
            cleaned = self._localize_sports_text(self.clean_publish_text(data_report.follow_up_focus).strip("。"))
            if cleaned:
                return cleaned
        return "阵容变化、关键人物状态、赛程难度和舆论争议是否继续发酵"

    def _sports_issue_text(self, data_report: DataReport, trend_report: TrendReport) -> str:
        text = " ".join(
            [
                self.clean_publish_text(data_report.topic_title),
                *self._pick_items(data_report.key_points, 3),
                *self._pick_items(trend_report.audience_hooks, 2),
            ]
        )
        if any(token in text.lower() for token in ("squad", "名单", "入选", "落选")):
            return "谁该进名单、谁该被放弃，以及教练到底在押注什么"
        if any(token in text.lower() for token in ("seed", "签表", "draw")):
            return "签表与对位变化会不会提前改写后续走势"
        if any(token in text.lower() for token in ("interview", "奖金", "pay", "row")):
            return "场外争议会不会反过来影响球员状态与赛事叙事"
        return "这件事背后的真正胜负手和后续变化"

    def _international_summary_bits(self, data_report: DataReport) -> str:
        items = self._mode_clean_items(
            data_report.core_data + data_report.key_points + data_report.evidence_notes,
            limit=3,
            blocked=self.MODE_NOISE_TOKENS,
        )
        if items:
            return "、".join(items[:3])
        title = self.clean_publish_text(data_report.topic_title).strip()
        return title or "这场国际博弈里已经浮出的关键信号"

    def _international_follow_text(self, data_report: DataReport) -> str:
        items = self._mode_clean_items(data_report.watch_metrics, limit=4, blocked=self.MODE_NOISE_TOKENS)
        if items:
            return "、".join(items)
        if data_report.follow_up_focus.strip():
            cleaned = self.clean_publish_text(data_report.follow_up_focus).strip("。")
            if cleaned:
                return cleaned
        return "政策表态、谈判进展、相关国家是否跟进动作以及市场反馈"

    def _international_issue_text(self, data_report: DataReport, trend_report: TrendReport) -> str:
        text = " ".join(
            [
                self.clean_publish_text(data_report.topic_title),
                *self._pick_items(data_report.key_points, 3),
                *self._pick_items(trend_report.audience_hooks, 2),
            ]
        )
        if any(token in text for token in ("仲裁", "制裁", "关税", "封锁", "禁令")):
            return "谁在动用制度与规则工具重新施压，谁又在试图守住自身利益"
        if any(token in text for token in ("停火", "谈判", "峰会", "会晤")):
            return "谈判窗口究竟是真的打开了，还是只是在给各方争取时间"
        return "谁在施压、谁在让步，以及这场博弈接下来会往哪边走"

    def _localize_sports_text(self, text: str) -> str:
        normalized = self.clean_publish_text(text).strip()
        replacements = {
            "World Cup": "世界杯",
            "world cup": "世界杯",
            "football": "足球",
            "Football": "足球",
            "squad selection": "名单选择",
            "squad": "名单",
            "In, or out?": "谁进谁出",
            "The joy and despair of": "",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        normalized = re.sub(r"\bIn, or out\?\b", "谁进谁出", normalized, flags=re.I)
        normalized = re.sub(r"\bThe joy and despair of\b", "", normalized, flags=re.I)
        normalized = re.sub(r"\bCurrent public material centers on\b", "", normalized, flags=re.I)
        normalized = normalized.replace("paste", "")
        normalized = re.sub(r"\s+", " ", normalized).strip(" ，。")
        return normalized

    def _scene_anchor(self, data_report: DataReport, trend_report: TrendReport) -> str:
        texts = [
            data_report.topic_title,
            *data_report.core_data[:3],
            *data_report.evidence_notes[:3],
            *data_report.key_points[:3],
            trend_report.recommended_angle,
        ]
        combined = " ".join(self.clean_publish_text(item) for item in texts if item)
        for token in self.SCENE_TOKENS:
            if token in combined:
                index = combined.find(token)
                start = max(0, index - 8)
                snippet = combined[start : index + len(token)]
                snippet = re.sub(r"[，。；：:、\s]+", "", snippet)
                if 2 <= len(snippet) <= 16:
                    return snippet
                return token
        return ""

    def _contrast_line(self, data_report: DataReport, trend_report: TrendReport) -> str:
        scene = self._scene_anchor(data_report, trend_report)
        if scene:
            return f"{scene}不是热闹场面，而是{self._primary_keyword(data_report)}重新分配利润的前哨。"
        article_text = " ".join(
            [
                data_report.topic_title,
                *data_report.key_points[:3],
                trend_report.recommended_angle,
            ]
        )
        if any(token in article_text for token in self.CROSS_BORDER_TOKENS):
            return f"{self._primary_keyword(data_report)}这轮变化，不只是升温，而是在改写谁能先拿订单、谁能先收利润。"
        return ""

    def _benefit_shift_line(self, data_report: DataReport, trend_report: TrendReport) -> str:
        text = " ".join(
            [
                data_report.topic_title,
                *data_report.key_points[:3],
                *data_report.watch_metrics[:3],
                trend_report.recommended_angle,
            ]
        )
        if any(token in text for token in ("系统集成", "集成商")):
            return "利润最先变化的，往往不是最上游材料端，而是更贴近交付与验收的一侧。"
        if any(token in text for token in ("零部件", "减速器", "电机", "逆变器", "芯片")):
            return "真正先被市场重估的，通常不是最显眼的终端，而是卡住关键效率和交付节奏的零部件环节。"
        if any(token in text for token in ("运维", "服务", "本地化")):
            return "随着项目从展示走向落地，利润中枢往往会向服务响应更快、交付更稳的环节迁移。"
        return "接下来真正会被重估的，不是所有主体，而是谁先把执行层能力变成订单和回款。"

    def _self_benchmark_patterns(self, context: dict[str, object] | None) -> list[str]:
        if not context:
            return []
        return [str(item).strip() for item in context.get("patterns", []) if str(item).strip()]

    def _self_benchmark_opening_lead(
        self,
        *,
        primary: str,
        scene_anchor: str,
        contrast_line: str,
        number: str,
        patterns: list[str],
    ) -> str:
        if "场景先行开头" in patterns and scene_anchor:
            return f"{scene_anchor}不是热闹场面，而是{primary}执行层分水岭提前出现的地方。"
        if "跨国对比冲突" in patterns and contrast_line:
            return contrast_line
        if "利润迁移框架" in patterns:
            return f"{number or primary}不是终点，而是{primary}利润开始重新分配的起点。"
        return ""

    def _self_benchmark_second_line(
        self,
        primary: str,
        data_report: DataReport,
        trend_report: TrendReport,
        patterns: list[str],
    ) -> str:
        if "利润迁移框架" in patterns:
            return f"{primary}现在真正该看的，不是新闻热度有多高，而是利润会先向哪一环迁移、谁能先把交付做实。"
        if "跨国对比冲突" in patterns:
            return f"{primary}这轮变化拉开差距的关键，不是讲故事能力，而是谁先把场景、订单和交付跑通。"
        return self._benefit_shift_line(data_report, trend_report)

    def _apply_self_benchmark_structure(
        self,
        paragraphs: list[str],
        trend_report: TrendReport,
        data_report: DataReport,
        *,
        self_benchmark_context: dict[str, object] | None = None,
    ) -> list[str]:
        patterns = self._self_benchmark_patterns(self_benchmark_context)
        if not patterns or not paragraphs:
            return paragraphs

        updated = list(paragraphs)
        article = "\n\n".join(updated)
        primary = self._primary_keyword(data_report)
        scene_anchor = self._scene_anchor(data_report, trend_report)
        _min_paragraphs, max_paragraphs = self._paragraph_bounds(data_report.content_format or "deep_analysis")

        if "场景先行开头" in patterns and not self._article_has_scene_anchor(article):
            conflict_paragraph = self._build_conflict_or_scene_paragraph(
                primary,
                scene_anchor,
                data_report,
                trend_report,
                self_benchmark_context=self_benchmark_context,
            )
            if conflict_paragraph:
                if len(updated) >= max_paragraphs and len(updated) >= 2:
                    updated[1] = conflict_paragraph
                else:
                    insert_at = 1 if len(updated) >= 2 else len(updated)
                    updated.insert(insert_at, conflict_paragraph)
                article = "\n\n".join(updated)

        if "利润迁移框架" in patterns and not self._article_has_benefit_shift_signal(article):
            benefit_line = self._benefit_shift_line(data_report, trend_report)
            if len(updated) >= max_paragraphs and len(updated) >= 3:
                updated[2] = f"{updated[2]} {benefit_line}".strip()
            else:
                insert_at = 2 if len(updated) >= 3 else len(updated)
                updated.insert(insert_at, benefit_line)
            article = "\n\n".join(updated)

        if "结尾保留跟踪变量" in patterns and not self._article_has_tracking_variable_close(article):
            metrics = "、".join(self._pick_items(data_report.watch_metrics, 4)) or "订单转化、交付节奏、回款和毛利变化"
            closing_line = (
                f"接下来最值得持续跟踪的，不是新闻条数，而是{metrics}。"
                "这些变量会直接决定这条线是停留在预期里，还是已经开始兑现到收入和利润。"
            )
            updated[-1] = f"{updated[-1]} {closing_line}".strip()

        return updated

    def _article_has_scene_anchor(self, article: str) -> bool:
        return any(token in article for token in self.SCENE_TOKENS)

    def _article_has_benefit_shift_signal(self, article: str) -> bool:
        return any(token in article for token in self.BENEFIT_SHIFT_TOKENS)

    def _article_has_tracking_variable_close(self, article: str) -> bool:
        paragraphs = [item.strip() for item in article.split("\n\n") if item.strip()]
        if not paragraphs:
            return False
        closing = "\n".join(paragraphs[-2:]) if len(paragraphs) >= 2 else paragraphs[-1]
        return any(
            token in closing
            for token in ("接下来最值得持续跟踪", "后续最值得跟踪", "真正值得跟踪", "继续跟踪", "变量", "订单转化", "并网节奏", "回款", "客户验收")
        )

    def _normalize_angle_candidate(self, value: object) -> str:
        text = self.clean_publish_text(str(value).strip())
        if not text:
            return ""
        if len(text) > 42:
            return ""
        if any(marker in text for marker in self.INVALID_ANGLE_FRAGMENTS):
            return ""
        if not any(token in text for token in ("订单", "交付", "毛利", "客户", "验证", "成本", "量产", "回款", "服务", "定点")):
            return ""
        return text

    def _primary_keyword(self, data_report: DataReport | None, fallback: str = "这个赛道") -> str:
        if data_report is None:
            return fallback
        for item in (
            data_report.focus_industry,
            *data_report.focus_keywords[:2],
            *data_report.supply_chain_keywords[:2],
        ):
            text = self.clean_publish_text(str(item).strip())
            if text and not self.is_internal_label(text):
                return text
        return fallback

    def _company_candidates(self, data_report: DataReport) -> list[str]:
        candidates = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        return [item for item in candidates if self._is_valid_company_name(item)]

    def _primary_company(self, data_report: DataReport) -> str:
        companies = self._company_candidates(data_report)
        return companies[0] if companies else ""

    def _is_valid_company_name(self, text: str) -> bool:
        candidate = self.clean_publish_text(text)
        if not candidate or len(candidate) < 2 or len(candidate) > 12:
            return False
        if candidate in self.INVALID_COMPANY_MARKERS:
            return False
        if any(marker in candidate for marker in ("：", "，", "。", "？", "！", "、", "数据", "趋势", "摘要")):
            return False
        if any(marker in candidate for marker in self.INVALID_COMPANY_FRAGMENTS):
            return False
        if candidate.startswith(("这家", "该", "某", "相关", "头部", "龙头", "重点", "当前", "后续", "行业", "赛道")):
            return False
        has_suffix = bool(self.COMPANY_SUFFIX_RE.fullmatch(candidate))
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", candidate))
        return has_suffix or (has_cjk and len(candidate) <= 8 and candidate not in {"公司", "企业", "客户"})

    def _build_company_or_order_anchor(self, data_report: DataReport) -> str:
        primary = self._primary_keyword(data_report)
        companies = self._company_candidates(data_report)[:2]
        follow_up = self._follow_up_points(data_report)
        metrics = self._pick_items(data_report.watch_metrics, limit=3)
        nodes = self._focus_nodes(data_report)

        if companies:
            company_text = "、".join(companies)
            follow_text = "、".join(follow_up or metrics or ["客户验证", "订单节奏", "毛利兑现"])
            node_text = nodes or primary
            return (
                f"真正该盯住的，不是泛泛的景气度，而是{company_text}这类主体能不能把{node_text}上的订单和交付兑现出来。"
                f"如果后续还能持续给出{follow_text}的明确信号，这类公司才有机会从题材受益者走向业绩兑现者。"
            )

        follow_text = "、".join(follow_up or metrics or ["客户验证", "项目交付", "毛利变化"])
        node_text = nodes or primary
        return (
            f"即便标题里没有直接点名公司，执行层主体其实已经很清楚：上游看{node_text}，下游看客户验证、量产节奏和海外交付。"
            f"谁能先把{follow_text}做实，谁才更可能在这一轮里先拿到利润和估值的双兑现。"
        )

    def _focus_nodes(self, data_report: DataReport) -> str:
        nodes = self.clean_publish_list(data_report.focus_keywords[1:] + data_report.supply_chain_keywords[1:], limit=3)
        return "、".join(nodes)

    def _follow_up_points(self, data_report: DataReport) -> list[str]:
        if data_report.follow_up_focus.strip():
            return self.clean_publish_list(
                [item for item in re.split(r"[、，,；;]", data_report.follow_up_focus) if item.strip()],
                limit=4,
            )
        return []

    def _has_concrete_anchor(self, article: str, data_report: DataReport) -> bool:
        article_text = article.lower()
        company_hits = sum(1 for item in self._company_candidates(data_report)[:4] if item.lower() in article_text)
        order_hits = sum(1 for token in self.ORDER_ANCHOR_TOKENS if token in article)
        return company_hits >= 1 or order_hits >= 3

    def _rebalance_paragraphs(self, paragraphs: list[str], content_format: str = "deep_analysis") -> list[str]:
        blocks = [block.strip() for block in paragraphs if block.strip()]
        expanded: list[str] = []
        for block in blocks:
            if len(block) > 165:
                expanded.extend(self._split_long_paragraph(block))
            else:
                expanded.append(block)

        min_paragraphs, max_paragraphs = self._paragraph_bounds(content_format)

        while len(expanded) < min_paragraphs:
            longest_index = max(range(len(expanded)), key=lambda idx: len(expanded[idx]))
            split_blocks = self._split_long_paragraph(expanded[longest_index], force=True)
            if len(split_blocks) <= 1:
                break
            expanded = expanded[:longest_index] + split_blocks + expanded[longest_index + 1 :]

        while len(expanded) > max_paragraphs:
            shortest_index = min(range(1, len(expanded)), key=lambda idx: len(expanded[idx]))
            expanded[shortest_index - 1] = f"{expanded[shortest_index - 1]} {expanded[shortest_index]}".strip()
            del expanded[shortest_index]

        return [block.strip() for block in expanded if block.strip()]

    def _paragraph_bounds(self, content_format: str) -> tuple[int, int]:
        if content_format == "quick_commentary":
            return 4, 5
        if content_format == "data_recap":
            return 4, 5
        if content_format == "project_breakdown":
            return 5, 6
        return 5, 7

    def _split_long_paragraph(self, paragraph: str, force: bool = False) -> list[str]:
        sentences = [item.strip() for item in self.SENTENCE_SPLIT_RE.split(paragraph) if item.strip()]
        if len(sentences) <= 1 and not force:
            return [paragraph]
        if len(sentences) <= 1 and force:
            parts = [item.strip() for item in re.split(r"(?<=[，；;])", paragraph) if item.strip()]
            return parts if len(parts) > 1 else [paragraph]

        result: list[str] = []
        current = ""
        current_count = 0
        for sentence in sentences:
            candidate = f"{current}{sentence}" if current else sentence
            if current and (len(candidate) > 118 or current_count >= 2):
                result.append(current.strip())
                current = sentence
                current_count = 1
            else:
                current = candidate
                current_count += 1
        if current.strip():
            result.append(current.strip())
        return result or [paragraph]

    def _ensure_closing_question(self, paragraphs: list[str], comment_prompt: str) -> list[str]:
        if not paragraphs:
            return [comment_prompt]
        question = comment_prompt.strip()
        if question and question[-1] not in "？?":
            question += "？"
        if not question:
            question = "你更看好哪个环节先兑现利润？"

        last = paragraphs[-1]
        if "？" in last or "?" in last:
            return paragraphs
        if len(last) <= 150:
            paragraphs[-1] = f"{last} {question}".strip()
            return paragraphs
        paragraphs.append(question)
        return paragraphs

    def _normalize_paragraph_text(self, text: str) -> str:
        normalized = self.strip_prompt_leakage(text).strip()
        normalized = normalized.replace("**", "").replace("__", "")
        normalized = re.sub(r"^#{1,6}\s*", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _mode_clean_items(self, items: list[str], limit: int, blocked: tuple[str, ...]) -> list[str]:
        result: list[str] = []
        for item in self.clean_publish_list(items, limit=limit * 3):
            text = self.clean_publish_text(item).strip()
            if not text:
                continue
            lowered = text.lower()
            if any(token.lower() in lowered for token in blocked):
                continue
            result.append(text)
        return self.unique(result)[:limit]

    def _best_evidence_note(self, data_report: DataReport) -> str:
        content_mode = self._content_mode(data_report)
        if content_mode in {"sports", "international"}:
            items = self._mode_clean_items(
                data_report.evidence_notes,
                limit=2,
                blocked=("manual_paste", *self.MODE_NOISE_TOKENS),
            )
            if items:
                return items[0]
            title = self.clean_publish_text(data_report.topic_title).strip()
            if title:
                return f"Current public material centers on {title}."
        for note in data_report.evidence_notes:
            text = self.clean_publish_text(note)
            if text:
                return text
        if data_report.growth_numbers:
            return " / ".join(data_report.growth_numbers[:2])
        return ""

    def _pick_items(self, items: list[str], limit: int) -> list[str]:
        cleaned = self.clean_publish_list(items, limit=limit * 2)
        return cleaned[:limit]

    def _first_sentence(self, article: str) -> str:
        for sentence in self.SENTENCE_SPLIT_RE.split(article):
            text = sentence.strip()
            if text:
                if text[-1] not in "。！？!?":
                    text += "。"
                return text
        return ""

    def _short_angle_line(self, angle: str) -> str:
        text = self.clean_publish_text(angle).strip().rstrip("。")
        if not text:
            return "真正该看的，是订单和交付谁先兑现。"
        return f"{text}。"

    def _long_angle_line(self, angle: str) -> str:
        text = self.clean_publish_text(angle).strip().rstrip("。")
        if not text:
            return "真正决定下一阶段强弱的，是订单、交付和毛利兑现。"
        return f"真正决定下一阶段强弱的，是{text}。"

    def _sanitize_tags(self, payload: object, data_report: DataReport) -> list[str]:
        if isinstance(payload, list):
            raw_items = [str(item).strip().lstrip("#") for item in payload if str(item).strip()]
        else:
            raw_items = [str(item).strip().lstrip("#") for item in data_report.supply_chain_keywords[:6] if str(item).strip()]

        result: list[str] = []
        seen: set[str] = set()
        for item in self.clean_publish_list(raw_items):
            if not item or self.is_internal_label(item):
                continue
            tag = f"#{item}"
            if tag in seen:
                continue
            seen.add(tag)
            result.append(tag)
            if len(result) >= 6:
                break
        return result

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]
