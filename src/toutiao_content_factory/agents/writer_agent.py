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
    SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")

    def run(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> GeneratedContent:
        prompt = self.render_prompt(
            "article_prompt_v2.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            audience=self.settings.audience,
            style_digest=self.style_digest(style),
            brand_digest=self.brand_digest(),
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
            opening_lines = self._finalize_opening_lines(payload.get("opening_lines"), trend_report, data_report, style)
            comment_prompt = self._finalize_comment_prompt(payload.get("comment_prompt"), data_report)
            article = self._finalize_article(
                str(payload.get("article", "")).strip(),
                trend_report,
                data_report,
                comment_prompt=comment_prompt,
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
        return self._fallback(trend_report, data_report, style, cache_context)

    def _fallback(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> GeneratedContent:
        opening_lines = self._build_opening_lines(trend_report, data_report, style)
        comment_prompt = self._default_comment_prompt(data_report)
        article = self._build_article(opening_lines, trend_report, data_report, comment_prompt)
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
                self._build_data_paragraph(primary, core_data, evidence),
                self._build_trend_paragraph(primary, trends, key_points, angle),
                self._build_company_or_order_anchor(data_report),
                self._build_metrics_paragraph(metrics),
                self._build_risk_paragraph(risks, comment_prompt),
            ]
        raw_article = "\n\n".join(part for part in paragraphs if part.strip())
        return self._finalize_article(raw_article, trend_report, data_report, comment_prompt=comment_prompt)

    def _build_opening_lines(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
    ) -> list[str]:
        primary = self._primary_keyword(data_report)
        company = self._primary_company(data_report)
        number = data_report.growth_numbers[0] if data_report.growth_numbers else ""
        angle = self._article_angle(trend_report, data_report)
        content_format = data_report.content_format or "deep_analysis"

        if content_format == "quick_commentary":
            lines = [
                f"{number or '这条新进展'}不能只当新闻看。",
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
                f"{company or primary}这次变化，关键不在热度而在兑现链条。",
                f"{primary}真正值得拆的是主体位置、交付节奏和利润归属。",
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
                f"{company or primary}背后的执行层信号，比概念热度更值得看。",
                f"{number or '这轮变化'}之后，行业真正分化的往往是客户验证、交付节奏和毛利兑现。",
                self._short_angle_line(angle),
            ]
        else:
            lines = [
                f"{number or '这轮变化'}不等于行业已经全面起飞，它更像是筛选真正受益者的开始。",
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

    def _finalize_article(
        self,
        article: str,
        trend_report: TrendReport,
        data_report: DataReport,
        comment_prompt: str,
    ) -> str:
        cleaned = self.strip_prompt_leakage(article)
        cleaned = clean_article_text(cleaned)
        paragraphs = [self._normalize_paragraph_text(block) for block in cleaned.split("\n\n") if block.strip()]
        paragraphs = [block for block in paragraphs if block]

        if not paragraphs:
            paragraphs = [
                self._normalize_paragraph_text(
                    " ".join(self._build_opening_lines(trend_report, data_report, self.settings.get_style(None)))
                ),
                self._build_company_or_order_anchor(data_report),
                self._build_metrics_paragraph(self._pick_items(data_report.watch_metrics, 4)),
            ]

        if not self._has_concrete_anchor("\n\n".join(paragraphs), data_report):
            anchor = self._build_company_or_order_anchor(data_report)
            if anchor:
                insert_at = 3 if len(paragraphs) >= 3 else len(paragraphs)
                paragraphs.insert(insert_at, anchor)

        paragraphs = self._rebalance_paragraphs(paragraphs, data_report.content_format or "deep_analysis")
        paragraphs = self._ensure_closing_question(paragraphs, comment_prompt or self._default_comment_prompt(data_report))
        _min_paragraphs, max_paragraphs = self._paragraph_bounds(data_report.content_format or "deep_analysis")
        return "\n\n".join(paragraphs[:max_paragraphs]).strip()

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
        primary = self._primary_keyword(data_report)
        angle = self._article_angle(trend_report, data_report)
        first_sentence = self._first_sentence(article)
        if first_sentence:
            return f"{first_sentence}{self._summary_tail(primary, angle)}"
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
        primary = self._primary_keyword(data_report) if data_report is not None else "这个赛道"
        content_format = data_report.content_format if data_report is not None else ""
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
    ) -> list[str]:
        if isinstance(payload, list):
            lines = [self.clean_publish_text(str(item).strip()) for item in payload if str(item).strip()]
        else:
            lines = []
        if len(lines) < 3:
            lines = self._build_opening_lines(trend_report, data_report, style)
        return self._normalize_opening_lines(lines[:3])

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

    def _best_evidence_note(self, data_report: DataReport) -> str:
        for note in data_report.evidence_notes:
            text = self.clean_publish_text(note)
            if text:
                if not text.endswith(("。", "！", "？")):
                    text += "。"
                return f"从公开材料看，{text}"
        if data_report.growth_numbers:
            return f"目前至少已经明确的数字信号包括{'、'.join(data_report.growth_numbers[:2])}。"
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
