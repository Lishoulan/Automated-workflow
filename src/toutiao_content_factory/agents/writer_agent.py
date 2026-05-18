from __future__ import annotations

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport


class WriterAgent(BaseAgent):
    agent_name = "WriterAgent"

    META_NOISE_PREFIXES = (
        "账号定位：",
        "目标平台：",
        "以下是今日自动选中的热点选题",
        "写作要求：",
        "匹配行业：",
        "匹配关键词：",
        "提取数字：",
        "热度评分：",
        "当日入选主题数：",
        "关注赛道：",
        "来源摘要：",
        "片段：",
        "摘要：",
        "标题：",
        "来源：",
        "发布日期：",
        "原文链接：",
        "补充片段：",
        "可确认数字：",
        "请围绕这条产业线索",
    )
    ANALYSIS_NOISE_PREFIXES = (
        "材料里最强的增长信号包括",
        "材料中提到的关键数据包括",
        "当前更值得写的产业链环节包括",
        "写作要求",
        "请围绕",
    )

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
            article_word_target=str(self.settings.article_word_target),
        )
        payload = self.request_json("你是 WriterAgent，只输出 JSON。", prompt)
        if payload:
            article = self.strip_prompt_leakage(str(payload.get("article", "")).strip())
            summary = self.strip_prompt_leakage(str(payload.get("summary", "")).strip())
            comment_prompt = self.strip_prompt_leakage(str(payload.get("comment_prompt", "")).strip())
            opening_lines = self.clean_publish_list(self._clean_list(payload.get("opening_lines"), 3), limit=3)
            tags = self._sanitize_tags(payload.get("tags"), data_report)
            recommended_topics = self.clean_publish_list(self._clean_list(payload.get("recommended_topics"), 5), limit=5)
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
        summary = self._build_summary(trend_report, data_report)
        article = self._build_article(opening_lines, trend_report, data_report)
        tags = self._sanitize_tags(data_report.supply_chain_keywords[:6], data_report)
        recommended_topics = self.clean_publish_list(
            trend_report.platform_topics + data_report.watch_metrics,
            limit=5,
        )
        focus_keyword = self._primary_keyword(data_report)
        comment_prompt = (
            f"如果只跟一个变量，你会优先盯{focus_keyword}的订单兑现，"
            "还是盯客户验证和交付节奏？欢迎留下你的判断。"
        )
        return GeneratedContent(
            article=self.strip_prompt_leakage(article),
            summary=self.strip_prompt_leakage(summary),
            comment_prompt=self.strip_prompt_leakage(comment_prompt),
            article_angle=self._article_angle(trend_report, data_report),
            style_name=style.label,
            opening_lines=opening_lines,
            tags=tags,
            recommended_topics=recommended_topics,
            cache_note=cache_context.note,
        )

    def _build_opening_lines(
        self,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
    ) -> list[str]:
        primary = self._primary_keyword(data_report)
        number = data_report.growth_numbers[0] if data_report.growth_numbers else "一笔关键融资"
        angle = self._article_angle(trend_report, data_report)
        company = self._primary_company(data_report)
        judgment = self._judgment_lead(data_report, angle)

        if style.key == "finance_report":
            return [
                f"{number} 不是终点，它只是{primary}重新定价的起点。",
                "真正决定估值能不能站住的，不是情绪，而是订单、交付和毛利兑现。",
                self._lead_with_angle("真正该盯的变量，是", angle),
            ]
        if style.key == "viral_science":
            return [
                f"{primary} 又热了，但这次真正变化的不是讨论声量，而是商业化节奏。",
                f"{number} 这样的数字最容易冲上热搜，却不一定直接变成业绩。",
                self._lead_with_angle("真正决定后面走势的，是", angle),
            ]
        if style.key == "tech_media":
            return [
                f"{company or primary}所在的{primary}环节还在升温，但行业现在关心的已经不是概念本身。",
                f"{number} 背后，更值得盯的是客户验证、订单结构和交付能力。",
                self._lead_with_angle("真正决定下一阶段强弱的，是", angle),
            ]
        return [
            judgment,
            f"当市场开始反复讨论{primary}，真正值得看的已经不是概念热度，而是谁能先把订单和利润兑现出来。",
            self._lead_with_angle("真正决定这一轮能不能走到业绩兑现的，是", angle),
        ]

    def _build_summary(self, trend_report: TrendReport, data_report: DataReport) -> str:
        primary = self._primary_keyword(data_report, fallback="相关赛道")
        number = data_report.growth_numbers[0] if data_report.growth_numbers else "一笔关键信号"
        angle = self._article_angle(trend_report, data_report)
        return (
            f"{primary}的热度正在抬升，{number}只是表层信号。"
            f"{self._lead_with_angle('真正决定后面能不能走成业绩的，是', angle).rstrip('。')}，这会决定订单、交付和毛利谁先落到报表里。"
        )

    def _build_article(
        self,
        opening_lines: list[str],
        trend_report: TrendReport,
        data_report: DataReport,
    ) -> str:
        lead_paragraph = " ".join(self._ensure_sentence(line) for line in opening_lines[:3])
        primary = self._primary_keyword(data_report)
        angle = self._article_angle(trend_report, data_report)
        core_data = self._pick_items(data_report.core_data, limit=3)
        industry_trends = self._pick_items(data_report.industry_trends, limit=3)
        analysis_points = self._analysis_points(data_report, trend_report, limit=3)
        metrics = self._pick_items(data_report.watch_metrics, limit=4)
        risks = self._pick_items(data_report.risk_flags, limit=3)
        companies = self.unique(
            [item.strip() for item in data_report.focus_companies if item.strip()]
            + [item.strip() for item in data_report.companies if item.strip()]
        )[:3]

        core_snapshot = self._join_clauses(core_data)
        trend_snapshot = self._join_clauses(industry_trends)
        analysis_snapshot = self._join_clauses(analysis_points)
        metrics_snapshot = self._join_metrics(metrics)
        risk_snapshot = self._join_clauses(risks)
        company_snapshot = "、".join(companies)
        evidence_anchor = self._build_evidence_anchor(data_report)
        data_paragraph = self._build_data_paragraph(primary, core_snapshot, evidence_anchor)
        risk_paragraph = self._build_risk_paragraph(risk_snapshot)
        transition_paragraph = self._build_transition_paragraph(trend_snapshot, angle, data_report)

        paragraphs = [
            lead_paragraph,
            data_paragraph,
            transition_paragraph,
            self._build_company_paragraph(company_snapshot, analysis_snapshot, data_report),
            (
                f"接下来最值得跟踪的，不是新闻条数，而是{metrics_snapshot}。"
                "这些指标能直接回答一件事：行业还停留在预期里，还是已经开始兑现到订单和利润。"
            ),
            risk_paragraph,
        ]
        return "\n\n".join(item.strip() for item in paragraphs if item.strip()).strip()

    def _build_company_paragraph(
        self,
        company_snapshot: str,
        analysis_snapshot: str,
        data_report: DataReport,
    ) -> str:
        focus_nodes = self._focus_nodes(data_report)
        primary = self._primary_keyword(data_report)

        if company_snapshot:
            detail = ""
            if focus_nodes:
                detail = f"更直接地说，后面要盯的不是情绪高低，而是{focus_nodes}这些环节能不能持续把验证进度做出来。"
            elif analysis_snapshot:
                detail = f"{analysis_snapshot}。"
            body = (
                f"落到公司层面，{company_snapshot}是当前更值得跟踪的主体。"
                f"这类公司之所以重要，不只是因为它站在{primary}主线上，更因为它能同时映射订单、交付和利润兑现。"
                f"{detail}"
                "如果后续还能持续给出客户验证、出货节奏和毛利改善的数据，"
                "它们才会从题材受益者走向业绩兑现者。"
            )
            return body.strip()

        return (
            "落到竞争格局里，真正拉开差距的还是执行层能力。"
            f"{analysis_snapshot + '。' if analysis_snapshot else ''}"
            "谁能更早把订单、交付和利润串起来，谁就更有机会拿到下一阶段溢价。"
        ).strip()

    def _build_data_paragraph(self, primary: str, core_snapshot: str, evidence_anchor: str) -> str:
        normalized_core = core_snapshot.replace("；", " ").replace("。", " ").strip()
        normalized_anchor = evidence_anchor.replace("从公开材料看，", "").replace("。", " ").strip()
        if normalized_anchor and normalized_anchor in normalized_core:
            return (
                f"最先落地的是数据。{core_snapshot}。"
                f"这至少说明，{primary}不再只是概念热度，而是在往真实项目、客户需求和商业回款上走。"
            )
        return (
            f"最先落地的是数据。{core_snapshot}。{evidence_anchor}"
            f"这至少说明，{primary}不再只是概念热度，而是在往真实项目、客户需求和商业回款上走。"
        )

    def _build_risk_paragraph(self, risk_snapshot: str) -> str:
        normalized = risk_snapshot.strip()
        duplicate_clauses = (
            "订单落地慢于扩产",
            "海外项目交付",
            "本地化服务投入",
            "利润率可能先承压",
        )
        has_duplicate = any(token in normalized for token in duplicate_clauses)
        tail = (
            "一旦订单兑现慢于扩产节奏，或者海外执行开始分化，前面的高景气叙事就很容易先热后冷。"
            if not has_duplicate
            else "真正需要警惕的，不是短期热度回落，而是验证链条在中途断掉。"
        )
        return f"当然，乐观判断也有边界。{normalized}。{tail}"

    def _build_evidence_anchor(self, data_report: DataReport) -> str:
        ranked_notes = sorted(data_report.evidence_notes, key=self._evidence_note_rank)
        for note in ranked_notes:
            text = self._normalize_sentence(note)
            if not text or text in {"sample_news", "sample_report"}:
                continue
            if "：" in text:
                prefix, rest = text.split("：", 1)
                if prefix.startswith("sample_") or prefix.lower().startswith("source"):
                    text = rest.strip()
            return f"从公开材料看，{self._ensure_sentence(text)}"
        if data_report.growth_numbers:
            return f"目前至少已经可以确认的数字信号包括{self._join_metrics(data_report.growth_numbers[:2])}。"
        return "目前公开信息还不算充分，但已有信号足以支撑继续跟踪。"

    def _article_angle(self, trend_report: TrendReport, data_report: DataReport) -> str:
        primary = self._primary_keyword(data_report)
        if data_report.suggested_angle.strip():
            suggested = self._normalize_angle(data_report.suggested_angle)
            if suggested:
                return suggested
        candidate = trend_report.recommended_angle.strip()
        if candidate:
            normalized = self._normalize_angle(candidate)
            if normalized:
                return normalized

        for item in self._analysis_points(data_report, trend_report, limit=3):
            normalized = self._normalize_angle(item)
            if normalized:
                return normalized
        return f"{primary}真正要看的，是订单、交付与利润谁能先兑现"

    def _primary_keyword(self, data_report: DataReport, fallback: str = "这个赛道") -> str:
        candidates = [
            self.clean_publish_text(data_report.focus_keywords[0]) if data_report.focus_keywords else "",
            self.clean_publish_text(data_report.focus_industry.strip()),
            self.clean_publish_text(data_report.supply_chain_keywords[0]) if data_report.supply_chain_keywords else "",
        ]
        for item in candidates:
            if item and not self.is_internal_label(item):
                return item
        for item in candidates:
            if item:
                return item
        return fallback

    def _focus_nodes(self, data_report: DataReport) -> str:
        candidates = self.clean_publish_list(
            [item.strip() for item in data_report.focus_keywords[1:] if item.strip()]
            + [item.strip() for item in data_report.supply_chain_keywords[1:] if item.strip()]
        )
        return "、".join(candidates[:3])

    def _analysis_points(
        self,
        data_report: DataReport,
        trend_report: TrendReport,
        limit: int,
    ) -> list[str]:
        candidates = (
            data_report.key_points
            + trend_report.competitive_edges
            + data_report.industry_trends
            + trend_report.hot_trends
        )
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text = self._normalize_sentence(item)
            if not text or text in seen or self._is_analysis_noise(text):
                continue
            if self._is_generic_competitive_edge(text):
                continue
            seen.add(text)
            cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _join_clauses(self, items: list[str]) -> str:
        cleaned: list[str] = []
        for item in items:
            text = self._normalize_sentence(item)
            if text:
                cleaned.append(text)
        return "；".join(cleaned) if cleaned else "目前公开信息还不足以给出过度乐观的结论"

    def _join_metrics(self, items: list[str]) -> str:
        cleaned: list[str] = []
        for item in items:
            text = self._normalize_sentence(item)
            if text:
                cleaned.append(text)
        return "、".join(cleaned) if cleaned else "后续订单、交付与毛利变化"

    def _pick_items(self, items: list[str], limit: int) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = self._normalize_sentence(item)
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned or ["目前公开信息仍然有限，后续需要继续跟踪真实订单与交付节奏"]

    def _normalize_sentence(self, text: str) -> str:
        normalized = text.strip().lstrip("- ").strip()
        if not normalized:
            return ""

        for prefix in ("行业判断：", "关键结论：", "结论：", "证据锚点："):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()

        if not normalized or normalized.startswith(self.META_NOISE_PREFIXES):
            return ""
        return normalized.rstrip("。；;，, ")

    def _normalize_angle(self, text: str) -> str:
        normalized = self._normalize_sentence(text)
        if not normalized or self._is_analysis_noise(normalized):
            return ""
        if normalized.startswith("聚焦"):
            normalized = normalized[2:].strip()
        if normalized.startswith("从") and "切入" in normalized:
            normalized = normalized.split("切入", 1)[-1].strip("，,：: ")
        return normalized.rstrip("。")

    def _lead_with_angle(self, prefix: str, angle: str) -> str:
        normalized_angle = self._normalize_angle(angle)
        if not normalized_angle:
            return prefix.rstrip("，,：: ") + "订单、交付与利润兑现。"
        if normalized_angle.startswith(("这", "真正", "接下来", "后面")):
            return f"{normalized_angle}。"
        return f"{prefix}{normalized_angle}。"

    def _is_analysis_noise(self, text: str) -> bool:
        return text.startswith(self.ANALYSIS_NOISE_PREFIXES)

    def _is_generic_competitive_edge(self, text: str) -> bool:
        generic_patterns = (
            "交付能力比故事能力更重要",
            "系统集成和关键零部件卡位决定下一阶段议价权",
            "海外与本地化服务能力会拉开订单差距",
        )
        return text in generic_patterns

    def _evidence_note_rank(self, text: str) -> tuple[int, int]:
        normalized = text.strip()
        if "证据锚点" in normalized:
            return (0, -len(normalized))
        if any(token in normalized for token in ("协会", "财报", "公告", "公开材料", "报告")):
            return (1, -len(normalized))
        if any(char.isdigit() for char in normalized):
            return (2, -len(normalized))
        return (3, -len(normalized))

    def _ensure_sentence(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            return ""
        if normalized[-1] not in "。！？!?":
            normalized += "。"
        return normalized

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]

    def _primary_company(self, data_report: DataReport) -> str:
        candidates = self.clean_publish_list(data_report.focus_companies + data_report.companies, limit=4)
        for item in candidates:
            if item and item not in {"这家公司", "该公司", "某公司"}:
                return item
        return ""

    def _judgment_lead(self, data_report: DataReport, angle: str) -> str:
        primary = self._primary_keyword(data_report)
        company = self._primary_company(data_report)
        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else ""
        if company and number:
            return f"{number} 并不自动等于{company}已经跑出来，它更像是{primary}进入兑现筛选期的起点。"
        if company:
            return f"{company}被反复讨论，不代表{primary}已经全面起飞，更可能只是产业链重新定价的开始。"
        if number:
            return f"{number} 不代表{primary}已经全面起飞，它更像是产业链重新定价的起点。"
        return self._lead_with_angle("这一轮最值得看的，不是热度本身，而是", angle)

    def _build_transition_paragraph(self, trend_snapshot: str, angle: str, data_report: DataReport) -> str:
        primary = self._primary_keyword(data_report)
        focus_nodes = self._focus_nodes(data_report)
        if focus_nodes:
            return (
                f"但真正值得拆开的，不只是事件热度，而是产业链内部的分工变化。{trend_snapshot}。"
                f"{self._lead_with_angle('接下来更该盯的，是', angle)}"
                f"市场不会平均奖励所有公司，利润和估值往往会先向{focus_nodes}这类卡位更深、验证更快的环节集中。"
            )
        return (
            f"但真正值得拆开的，不只是事件热度，而是{primary}内部的兑现节奏。{trend_snapshot}。"
            f"{self._lead_with_angle('接下来更该盯的，是', angle)}"
            "市场不会平均奖励所有公司，而会优先奖励交付更稳、验证更快的那一批主体。"
        )

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
