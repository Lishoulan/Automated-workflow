from __future__ import annotations

import re
import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import DataReport, ExtractionResult, TrendReport
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class DataAgent(BaseAgent):
    agent_name = "DataAgent"
    PROMPT_INJECTION_PATTERNS = (
        re.compile(r"请忽略以上.{0,10}指令", re.IGNORECASE),
        re.compile(r"忽略之前的.{0,10}(指令|要求|规则)", re.IGNORECASE),
        re.compile(r"你现在是", re.IGNORECASE),
        re.compile(r"从现在起", re.IGNORECASE),
        re.compile(r"SYSTEM\s*:", re.IGNORECASE),
        re.compile(r"IMPORTANT\s*:", re.IGNORECASE),
        re.compile(r"\[SYSTEM\]", re.IGNORECASE),
        re.compile(r"忽略以上所有", re.IGNORECASE),
    )
    MODE_NOISE_TOKENS = ("产业链", "订单", "交付", "毛利", "利润", "兑现", "融资", "客户验证", "科技制造", "项目落地")
    TOPIC_TYPE_LABELS = {
        "data_recap": "数据复盘",
        "project_progress": "项目进展",
        "financing_update": "融资动态",
        "earnings_signal": "业绩信号",
        "policy_market": "政策与市场",
        "product_launch": "产品发布",
        "industry_event": "行业事件",
    }
    CONTENT_FORMAT_LABELS = {
        "deep_analysis": "深度分析",
        "quick_commentary": "快讯点评",
        "project_breakdown": "项目拆解",
        "data_recap": "数据复盘",
    }

    def run(
        self,
        extraction: ExtractionResult,
        trend_report: TrendReport,
        style: StyleProfile,
    ) -> DataReport:
        # 清洗用户输入，防止 Prompt 注入
        extraction.topic_title = self._sanitize_prompt_input(extraction.topic_title)
        extraction.writing_brief = self._sanitize_prompt_input(extraction.writing_brief)
        extraction.selection_reason = self._sanitize_prompt_input(extraction.selection_reason)
        extraction.suggested_angle = self._sanitize_prompt_input(extraction.suggested_angle)

        benchmark_digest = self.benchmark_strategy_digest(
            purpose="data",
            track_hint=extraction.focus_industry or trend_report.recommended_angle,
            title_hint=extraction.topic_title,
            focus_industry=extraction.focus_industry,
            keywords=extraction.focus_keywords + extraction.supply_chain_keywords,
            angle_hint=trend_report.recommended_angle,
            extra_text=extraction.writing_brief,
        )
        prompt = self.render_prompt(
            "data_prompt_v2.md",
            brand_name=self.settings.brand_name,
            industry_focus=self.settings.industry_focus,
            style_digest=self.style_digest(style),
            brand_digest=self.brand_digest(extra_sections=[benchmark_digest]),
            source_digest=self._format_source_digest(extraction),
            signals_json=self.json_text(self._extraction_payload(extraction)),
            trend_json=self.json_text(trend_report.__dict__),
        )
        payload = self.request_json("你是 DataAgent，只输出 JSON。", prompt)
        if payload:
            return self._build_from_payload(payload, extraction, trend_report)
        return self._fallback(extraction, trend_report)

    @staticmethod
    def _sanitize_prompt_input(text: str) -> str:
        if not text:
            return text
        for pattern in DataAgent.PROMPT_INJECTION_PATTERNS:
            text = pattern.sub("", text)
        return text.strip()

    def _build_from_payload(
        self,
        payload: dict,
        extraction: ExtractionResult,
        trend_report: TrendReport,
    ) -> DataReport:
        merged = dict(payload)
        merged["topic_title"] = str(merged.get("topic_title") or extraction.topic_title)
        merged["selection_reason"] = str(merged.get("selection_reason") or extraction.selection_reason)
        merged["suggested_angle"] = str(merged.get("suggested_angle") or extraction.suggested_angle)
        merged["follow_up_focus"] = str(merged.get("follow_up_focus") or extraction.follow_up_focus)
        merged["writing_brief"] = str(merged.get("writing_brief") or extraction.writing_brief)

        focus_keywords = self.clean_publish_list(
            list(merged.get("focus_keywords") or extraction.focus_keywords or extraction.supply_chain_keywords),
            limit=6,
        )
        focus_industry = self.clean_publish_text(str(merged.get("focus_industry") or extraction.focus_industry or ""))
        supply_chain_keywords = self.clean_publish_list(
            list(merged.get("supply_chain_keywords") or extraction.supply_chain_keywords or focus_keywords),
            limit=8,
        )
        companies = self._sanitize_companies(
            list(merged.get("companies") or extraction.companies),
            extraction,
            limit=8,
        )
        focus_companies = self._sanitize_companies(
            list(merged.get("focus_companies") or extraction.focus_companies or companies),
            extraction,
            limit=4,
        )
        secondary = focus_keywords[1] if len(focus_keywords) > 1 else "关键环节"
        core_data = self.clean_publish_list(list(merged.get("core_data") or extraction.core_data), limit=6)
        industry_trends = self.clean_publish_list(
            list(merged.get("industry_trends") or extraction.industry_trends),
            limit=6,
        )
        growth_numbers = self.clean_publish_list(
            list(merged.get("growth_numbers") or extraction.growth_numbers),
            limit=8,
        )
        key_points = self.clean_publish_list(list(merged.get("key_points") or extraction.key_points), limit=6)
        watch_metrics = self.clean_publish_list(
            list(
                merged.get("watch_metrics")
                or self._build_watch_metrics(
                    focus_industry or extraction.focus_industry or "相关赛道",
                    secondary,
                    merged["follow_up_focus"],
                )
            ),
            limit=5,
        )
        evidence_notes = self.clean_publish_list(
            list(merged.get("evidence_notes") or self._build_evidence_notes(extraction)),
            limit=5,
        )
        risk_flags = self.clean_publish_list(
            list(
                merged.get("risk_flags")
                or self._build_risk_flags(
                    focus_industry or extraction.focus_industry or "相关赛道",
                    secondary,
                    trend_report,
                    supply_chain_keywords,
                )
            ),
            limit=4,
        )
        normalized_fields = self._normalize_mode_fields(
            extraction=extraction,
            focus_industry=focus_industry,
            focus_keywords=focus_keywords,
            supply_chain_keywords=supply_chain_keywords,
            core_data=core_data,
            industry_trends=industry_trends,
            key_points=key_points,
            watch_metrics=watch_metrics,
            evidence_notes=evidence_notes,
            risk_flags=risk_flags,
            companies=companies,
            focus_companies=focus_companies,
        )
        (
            focus_industry,
            focus_keywords,
            supply_chain_keywords,
            core_data,
            industry_trends,
            key_points,
            watch_metrics,
            evidence_notes,
            risk_flags,
            companies,
            focus_companies,
        ) = normalized_fields
        route = self._resolve_content_route(
            extraction=extraction,
            focus_industry=focus_industry or extraction.focus_industry or "相关赛道",
            companies=companies,
            growth_numbers=growth_numbers,
            core_data=core_data,
            evidence_notes=evidence_notes,
        )

        return DataReport(
            core_data=core_data,
            industry_trends=industry_trends,
            growth_numbers=growth_numbers,
            companies=companies,
            supply_chain_keywords=supply_chain_keywords,
            key_points=key_points,
            watch_metrics=watch_metrics,
            evidence_notes=evidence_notes,
            risk_flags=risk_flags,
            focus_industry=focus_industry,
            focus_keywords=focus_keywords,
            focus_companies=focus_companies,
            topic_title=merged["topic_title"],
            selection_reason=merged["selection_reason"],
            suggested_angle=merged["suggested_angle"],
            follow_up_focus=merged["follow_up_focus"],
            writing_brief=merged["writing_brief"],
            topic_type=str(route["topic_type"]),
            topic_type_label=str(route["topic_type_label"]),
            content_format=str(route["content_format"]),
            content_format_label=str(route["content_format_label"]),
            format_brief=str(route["format_brief"]),
            paragraph_instruction=str(route["paragraph_instruction"]),
            evidence_strength=int(route["evidence_strength"]),
            topic_quality_score=int(route["topic_quality_score"]),
            topic_quality_flags=list(route["topic_quality_flags"]),
            word_target=int(route["word_target"]),
            is_hot_topic=bool(getattr(extraction, "is_hot_topic", False)),
        )

    def _fallback(self, extraction: ExtractionResult, trend_report: TrendReport) -> DataReport:
        keywords = self.clean_publish_list(
            extraction.focus_keywords[:5] or extraction.supply_chain_keywords[:5] or ["科技制造", "产业链"],
            limit=5,
        )
        primary = self.clean_publish_text(extraction.focus_industry.strip() or (keywords[0] if keywords else "相关赛道"))
        secondary = keywords[1] if len(keywords) > 1 else "关键环节"
        content_mode = self._content_mode(extraction)
        companies = self._sanitize_companies(extraction.focus_companies + extraction.companies, extraction, limit=8)
        evidence_notes = self._build_evidence_notes(extraction)
        supply_chain_keywords = self.clean_publish_list(extraction.supply_chain_keywords[:8] or keywords, limit=8)
        watch_metrics = self._build_watch_metrics(primary, secondary, extraction.follow_up_focus)
        risk_flags = self._build_risk_flags(primary, secondary, trend_report, keywords)
        core_data = self.clean_publish_list(extraction.core_data[:6], limit=6)
        industry_trends = self.clean_publish_list(extraction.industry_trends[:6], limit=6)
        key_points = self.clean_publish_list(extraction.key_points[:6], limit=6)
        focus_keywords = self.clean_publish_list(extraction.focus_keywords[:6] or keywords, limit=6)
        normalized_fields = self._normalize_mode_fields(
            extraction=extraction,
            focus_industry=primary,
            focus_keywords=focus_keywords,
            supply_chain_keywords=supply_chain_keywords,
            core_data=core_data,
            industry_trends=industry_trends,
            key_points=key_points,
            watch_metrics=watch_metrics,
            evidence_notes=evidence_notes,
            risk_flags=risk_flags,
            companies=companies,
            focus_companies=companies[:4],
        )
        (
            primary,
            focus_keywords,
            supply_chain_keywords,
            core_data,
            industry_trends,
            key_points,
            watch_metrics,
            evidence_notes,
            risk_flags,
            companies,
            focus_companies,
        ) = normalized_fields
        route = self._resolve_content_route(
            extraction=extraction,
            focus_industry=primary,
            companies=companies,
            growth_numbers=self.clean_publish_list(extraction.growth_numbers[:8], limit=8),
            core_data=core_data,
            evidence_notes=evidence_notes,
        )

        return DataReport(
            core_data=core_data,
            industry_trends=industry_trends,
            growth_numbers=self.clean_publish_list(extraction.growth_numbers[:8], limit=8),
            companies=companies,
            supply_chain_keywords=supply_chain_keywords,
            key_points=key_points,
            watch_metrics=watch_metrics,
            evidence_notes=evidence_notes,
            risk_flags=risk_flags,
            focus_industry=primary,
            focus_keywords=focus_keywords,
            focus_companies=focus_companies,
            topic_title=extraction.topic_title,
            selection_reason=extraction.selection_reason,
            suggested_angle=extraction.suggested_angle,
            follow_up_focus=extraction.follow_up_focus,
            writing_brief=extraction.writing_brief,
            topic_type=str(route["topic_type"]),
            topic_type_label=str(route["topic_type_label"]),
            content_format=str(route["content_format"]),
            content_format_label=str(route["content_format_label"]),
            format_brief=str(route["format_brief"]),
            paragraph_instruction=str(route["paragraph_instruction"]),
            evidence_strength=int(route["evidence_strength"]),
            topic_quality_score=int(route["topic_quality_score"]),
            topic_quality_flags=list(route["topic_quality_flags"]),
            word_target=int(route["word_target"]),
            is_hot_topic=bool(getattr(extraction, "is_hot_topic", False)),
        )

    def _resolve_content_route(
        self,
        extraction: ExtractionResult,
        focus_industry: str,
        companies: list[str],
        growth_numbers: list[str],
        core_data: list[str],
        evidence_notes: list[str],
    ) -> dict[str, object]:
        content_mode = self._content_mode(extraction)
        topic_type = extraction.topic_type.strip() or self._infer_topic_type(extraction)
        evidence_strength = extraction.evidence_strength or self._estimate_evidence_strength(
            extraction,
            companies,
            growth_numbers,
            core_data,
            evidence_notes,
        )
        if bool(getattr(extraction, "is_hot_topic", False)):
            # 实时热榜二创：统一走热点快评，不按证据强度硬性要求长文
            content_format = "quick_commentary"
        elif content_mode == "sports":
            content_format = "quick_commentary"
        elif content_mode == "international":
            content_format = "quick_commentary"
        else:
            content_format = extraction.content_format.strip() or self._choose_content_format(
                topic_type,
                evidence_strength,
                extraction.topic_quality_flags,
                companies,
                growth_numbers,
                evidence_notes,
            )
        topic_quality_score = extraction.topic_quality_score or min(
            96,
            round(evidence_strength * 0.72 + len(evidence_notes) * 2 + len(growth_numbers) * 2),
        )
        topic_quality_flags = self._build_topic_quality_flags(
            extraction=extraction,
            core_data=core_data,
            growth_numbers=growth_numbers,
            companies=companies,
            evidence_notes=evidence_notes,
        )
        word_target = extraction.word_target or self._word_target_for_format(content_format, content_mode=content_mode)
        paragraph_instruction = extraction.paragraph_instruction.strip() or self._paragraph_instruction_for_format(
            content_format,
            content_mode=content_mode,
        )
        format_brief = extraction.format_brief.strip() or self._format_brief_for_format(
            content_format,
            focus_industry,
            content_mode=content_mode,
        )
        return {
            "topic_type": topic_type,
            "topic_type_label": self.TOPIC_TYPE_LABELS.get(topic_type, topic_type or "行业事件"),
            "content_format": content_format,
            "content_format_label": self.CONTENT_FORMAT_LABELS.get(content_format, content_format or "深度分析"),
            "format_brief": format_brief,
            "paragraph_instruction": paragraph_instruction,
            "evidence_strength": evidence_strength,
            "topic_quality_score": topic_quality_score,
            "topic_quality_flags": topic_quality_flags,
            "word_target": word_target,
        }

    def _build_topic_quality_flags(
        self,
        extraction: ExtractionResult,
        core_data: list[str],
        growth_numbers: list[str],
        companies: list[str],
        evidence_notes: list[str],
    ) -> list[str]:
        flags = list(extraction.topic_quality_flags)
        # 实时热榜二创：标题型热点天然缺证据/公司/数字，不把这些记为质量缺陷
        if bool(getattr(extraction, "is_hot_topic", False)):
            return flags
        if not core_data:
            flags.append("missing_core_data")
        if not growth_numbers:
            flags.append("missing_growth_numbers")
        if not companies:
            flags.append("missing_companies")
        if not evidence_notes:
            flags.append("missing_evidence_notes")
        if not extraction.topic_title.strip():
            flags.append("missing_topic_title")
        if len(core_data) + len(growth_numbers) + len(companies) <= 2:
            flags.append("low_information_density")
        result: list[str] = []
        seen: set[str] = set()
        for item in flags:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _infer_topic_type(self, extraction: ExtractionResult) -> str:
        text = f"{extraction.topic_title} {extraction.merged_text}".lower()
        if any(token in text for token in ("统计数据表", "统计公报", "产量统计", "销量统计", "装机量", "出货量", "价格指数")):
            return "data_recap"
        if any(token in text for token in ("政策", "意见", "通知", "规划", "补贴", "新规", "纲要")):
            return "policy_market"
        if any(token in text for token in ("财报", "业绩", "营收", "净利", "一季报", "半年报", "年报", "毛利")):
            return "earnings_signal"
        if any(token in text for token in ("项目", "中标", "签约", "并网", "投产", "验收", "示范")):
            return "project_progress"
        if any(token in text for token in ("融资", "募资", "a轮", "b轮", "c轮", "天使轮", "种子轮", "投资")):
            return "financing_update"
        if any(token in text for token in ("发布", "推出", "首发", "新品", "方案", "平台")):
            return "product_launch"
        return "industry_event"

    def _estimate_evidence_strength(
        self,
        extraction: ExtractionResult,
        companies: list[str],
        growth_numbers: list[str],
        core_data: list[str],
        evidence_notes: list[str],
    ) -> int:
        score = 28
        score += min(24, len(growth_numbers) * 6)
        score += min(16, len(companies[:4]) * 4)
        score += min(14, len(core_data[:4]) * 3)
        score += min(14, len(evidence_notes[:4]) * 3)
        if extraction.topic_title and re.search(r"(订单|中标|签约|量产|并网|交付|财报|融资|项目)", extraction.topic_title):
            score += 10
        if extraction.follow_up_focus.strip():
            score += 4
        if "title_only" in extraction.topic_quality_flags:
            score -= 10
        if extraction.topic_type == "policy_market" and not companies:
            score -= 8
        return max(20, min(96, score))

    def _choose_content_format(
        self,
        topic_type: str,
        evidence_strength: int,
        topic_quality_flags: list[str],
        companies: list[str],
        growth_numbers: list[str],
        evidence_notes: list[str],
    ) -> str:
        flags = set(topic_quality_flags)
        if topic_type == "data_recap":
            return "data_recap"
        if "title_only" in flags and topic_type in {"policy_market", "industry_event", "product_launch"}:
            return "quick_commentary"
        if topic_type == "policy_market":
            if evidence_strength >= 78 and "title_only" not in flags and companies and len(growth_numbers) >= 2:
                return "deep_analysis"
            return "quick_commentary"
        if topic_type in {"project_progress", "earnings_signal"} and evidence_strength >= 56:
            return "project_breakdown"
        if topic_type in {"financing_update", "policy_market", "product_launch"} and evidence_strength < 60:
            return "quick_commentary"
        if evidence_strength >= 68 and len(growth_numbers) >= 2 and (companies or evidence_notes):
            return "deep_analysis"
        if companies and evidence_strength >= 54:
            return "project_breakdown"
        return "quick_commentary"

    def _word_target_for_format(self, content_format: str, content_mode: str = "generic") -> int:
        if content_mode == "sports":
            return 900
        if content_mode == "international":
            return 1000
        if content_format == "quick_commentary":
            return 800
        if content_format == "data_recap":
            return 900
        if content_format == "project_breakdown":
            return 1100
        return self.settings.article_word_target

    def _paragraph_instruction_for_format(self, content_format: str, content_mode: str = "generic") -> str:
        if content_mode == "sports":
            return "4到6段，每段2到3句，先判断影响，再讲关键人物和后续走势，结尾只留一个互动问题。"
        if content_mode == "international":
            return "5到6段，每段2到3句，先事件、再博弈、再影响、再变量，结尾不要空喊口号。"
        if content_format == "quick_commentary":
            return "4到5段，每段2句，首段直接判断，末段只留一个互动问题。"
        if content_format == "data_recap":
            return "4到5段，每段2句，重点解释数字含义、受益环节和后续验证点。"
        if content_format == "project_breakdown":
            return "5到6段，每段2到3句，围绕主体、环节位置、订单兑现和交付节奏展开。"
        return "5到7段，每段2到3句，先判断、再数据、再产业链、再主体、再风险边界。"

    def _format_brief_for_format(self, content_format: str, focus_industry: str, content_mode: str = "generic") -> str:
        if content_mode == "sports":
            return "写成深度分析稿，重点解释事件对产业格局、技术路线和市场预期的影响。"
        if content_mode == "international":
            return "写成国际热点解读稿，重点解释各方博弈关系、后续变量和外溢影响。"
        if content_format == "quick_commentary":
            return "写成短而硬的事件点评，不要强行拉成长篇深度稿。"
        if content_format == "data_recap":
            return "写成数据复盘稿，重点解释数字变化背后的产业链含义。"
        if content_format == "project_breakdown":
            return f"写成{focus_industry}项目拆解稿，重点写主体、环节位置、交付路径和利润兑现。"
        return f"写成{focus_industry}深度分析稿，重点拆订单、交付、毛利和产业链分化。"

    def _sanitize_companies(
        self,
        raw_items: list[str],
        extraction: ExtractionResult,
        limit: int,
    ) -> list[str]:
        cleaned = self.clean_publish_list(raw_items)
        candidates = [item for item in cleaned if self._is_valid_company_name(item)]
        for item in self._extract_company_like_names(extraction):
            if item not in candidates:
                candidates.append(item)
        return self.unique(candidates)[:limit]

    def _extract_company_like_names(self, extraction: ExtractionResult) -> list[str]:
        text_parts = [extraction.merged_text]
        text_parts.extend(str(item.get("fact", "")) for item in extraction.evidence_items[:6])
        text_parts.extend(str(item.get("preview", "")) for item in extraction.source_summaries[:4])
        text_parts.extend(str(item.get("title", "")) for item in extraction.source_summaries[:4])
        combined = "\n".join(part for part in text_parts if part)

        results: list[str] = []
        for match in self.COMPANY_SUFFIX_RE.findall(combined):
            candidate = self.clean_publish_text(match)
            if self._is_valid_company_name(candidate):
                results.append(candidate)
        return self.unique(results)[:8]

    def _build_watch_metrics(self, primary: str, secondary: str, follow_up_focus: str) -> list[str]:
        if follow_up_focus.strip():
            items = [self.clean_publish_text(item) for item in re.split(r"[、，,；;]", follow_up_focus) if item.strip()]
            items = [item for item in items if item]
            if items:
                return self.unique(items)[:5]

        text = f"{primary} {secondary}".lower()
        if any(token in text for token in self.SPORTS_TOKENS):
            return self.unique(["技术路线变化", "市场份额变化", "关键企业动态", "舆论争议是否继续发酵"])[:5]
        # 只有当没有科技相关词汇时，才判断为国际热点
        tech_tokens = ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "ai", "大模型", "智能制造", "工业自动化"]
        tech_tokens_count = sum(1 for token in tech_tokens if token in text)
        if tech_tokens_count == 0 and any(token in text for token in self.INTERNATIONAL_TOKENS):
            return self.unique(["政策表态", "谈判进展", "相关国家是否跟进动作", "市场反馈"])[:5]

        delivery_metric = "样机转量产速度" if any(token in primary for token in ("机器人", "装备")) else "项目交付节奏"
        candidates = [
            "新增订单和中标数量",
            delivery_metric,
            "重点客户验证进度",
            "海外与本地化服务进展",
            f"{secondary}环节毛利率变化",
        ]
        return self.unique(candidates)[:5]

    def _normalize_mode_fields(
        self,
        *,
        extraction: ExtractionResult,
        focus_industry: str,
        focus_keywords: list[str],
        supply_chain_keywords: list[str],
        core_data: list[str],
        industry_trends: list[str],
        key_points: list[str],
        watch_metrics: list[str],
        evidence_notes: list[str],
        risk_flags: list[str],
        companies: list[str],
        focus_companies: list[str],
    ) -> tuple[str, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str]]:
        mode = self._content_mode(extraction)
        if mode == "sports":
            normalized_focus = self.clean_publish_text(focus_industry).strip() or "体育赛事"
            if any(token in normalized_focus for token in self.MODE_NOISE_TOKENS):
                normalized_focus = "体育赛事"
            focus_keywords = self._clean_mode_list(
                focus_keywords,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=self._sports_keywords(extraction),
                limit=6,
            )
            supply_chain_keywords = self._clean_mode_list(
                supply_chain_keywords,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=["技术路线", "市场份额", "关键企业", "产业走势"],
                limit=6,
            )
            core_data = self._clean_mode_list(
                core_data,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=self._sports_core_points(extraction),
                limit=5,
            )
            industry_trends = self._clean_mode_list(
                industry_trends,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=[
                    "这类信息的热度，正在从新闻转向趋势判断。",
                    "读者更关心的是技术路线变化会不会改变产业预期。",
                ],
                limit=4,
            )
            key_points = self._clean_mode_list(
                key_points,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=[
                    "真正值得写的不是复述消息，而是解释它会如何影响后续比赛。",
                    "关键企业、技术路线和市场份额变化，才是决定讨论度的核心。",
                ],
                limit=4,
            )
            watch_metrics = self._clean_mode_list(
                watch_metrics,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=["技术路线变化", "市场份额变化", "关键企业动态", "舆论争议是否继续发酵"],
                limit=5,
            )
            evidence_notes = self._clean_mode_list(
                evidence_notes,
                blocked=("manual_paste", *self.MODE_NOISE_TOKENS),
                fallback=self._sports_evidence_notes(extraction),
                limit=4,
            )
            risk_flags = self._clean_mode_list(
                risk_flags,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=[
                    "如果后续名单、状态或临场信息继续变化，当前判断可能很快改写。",
                    "公开信息不足时，最需要避免的是把初步消息写成确定结论。",
                ],
                limit=4,
            )
            return normalized_focus, focus_keywords, supply_chain_keywords, core_data, industry_trends, key_points, watch_metrics, evidence_notes, risk_flags, [], []

        if mode == "international":
            normalized_focus = self.clean_publish_text(focus_industry).strip() or "国际热点"
            if any(token in normalized_focus for token in self.MODE_NOISE_TOKENS):
                normalized_focus = "国际热点"
            focus_keywords = self._clean_mode_list(
                focus_keywords,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=self._international_keywords(extraction),
                limit=6,
            )
            supply_chain_keywords = self._clean_mode_list(
                supply_chain_keywords,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=["博弈方", "政策变量", "谈判窗口", "市场反馈"],
                limit=6,
            )
            core_data = self._clean_mode_list(
                core_data,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=self._international_core_points(extraction),
                limit=5,
            )
            industry_trends = self._clean_mode_list(
                industry_trends,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=[
                    "这类国际事件的热度，通常会从单点消息转向持续博弈观察。",
                    "真正值得跟的不是表态本身，而是后续动作与市场反应。",
                ],
                limit=4,
            )
            key_points = self._clean_mode_list(
                key_points,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=[
                    "真正值得写的是各方利益、压力和下一步动作。",
                    "政策变化与市场反馈，决定事件会继续升级还是转入谈判。",
                ],
                limit=4,
            )
            watch_metrics = self._clean_mode_list(
                watch_metrics,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=["政策表态", "谈判进展", "相关国家是否跟进动作", "市场反馈"],
                limit=5,
            )
            evidence_notes = self._clean_mode_list(
                evidence_notes,
                blocked=("manual_paste", *self.MODE_NOISE_TOKENS),
                fallback=self._international_evidence_notes(extraction),
                limit=4,
            )
            risk_flags = self._clean_mode_list(
                risk_flags,
                blocked=self.MODE_NOISE_TOKENS,
                fallback=[
                    "表态本身不一定等于最终动作，后续政策变化可能很快修正当前判断。",
                    "公开信息不足时，最需要避免的是把短期消息写成长周期定局。",
                ],
                limit=4,
            )
            return normalized_focus, focus_keywords, supply_chain_keywords, core_data, industry_trends, key_points, watch_metrics, evidence_notes, risk_flags, [], []

        return focus_industry, focus_keywords, supply_chain_keywords, core_data, industry_trends, key_points, watch_metrics, evidence_notes, risk_flags, companies, focus_companies

    def _clean_mode_list(
        self,
        items: list[str],
        *,
        blocked: tuple[str, ...],
        fallback: list[str],
        limit: int,
    ) -> list[str]:
        result: list[str] = []
        for item in self.clean_publish_list(items, limit=limit * 3):
            text = self.clean_publish_text(item).strip()
            if not text:
                continue
            lowered = text.lower()
            if any(token.lower() in lowered for token in blocked):
                continue
            result.append(text)
        if not result:
            result = [self.clean_publish_text(item).strip() for item in fallback if self.clean_publish_text(item).strip()]
        return self.unique(result)[:limit]

    def _sports_keywords(self, extraction: ExtractionResult) -> list[str]:
        text = f"{extraction.topic_title} {extraction.selection_reason} {extraction.suggested_angle}".lower()
        if "world cup" in text or "世界杯" in text:
            return ["全球市场", "名单", "技术路线", "产业走势"]
        if "football" in text:
            return ["行业动态", "名单", "关键企业", "产业走势"]
        if "tennis" in text or "法网" in text:
            return ["细分领域", "排名", "关键企业", "产业走势"]
        return ["行业动态", "名单", "关键企业", "产业走势"]

    def _international_keywords(self, extraction: ExtractionResult) -> list[str]:
        text = f"{extraction.topic_title} {extraction.selection_reason} {extraction.suggested_angle}".lower()
        if "tariff" in text or "关税" in text:
            return ["关税", "谈判", "政策变量", "市场反馈"]
        if "white house" in text or "美国" in text:
            return ["美国", "政策表态", "谈判窗口", "市场反馈"]
        return ["国际热点", "博弈方", "政策变量", "市场反馈"]

    def _sports_core_points(self, extraction: ExtractionResult) -> list[str]:
        title = self._sports_subject_label(extraction)
        return [
            f"{title}触发的讨论重点，不在消息本身，而在后续比赛判断。",
            "技术路线取舍、关键企业动态和市场份额变化，决定这条消息会不会继续发酵。",
        ]

    def _international_core_points(self, extraction: ExtractionResult) -> list[str]:
        title = self.clean_publish_text(extraction.topic_title).strip() or "这条国际消息"
        return [
            f"{title}真正值得看的是各方动作，而不只是表态本身。",
            "后续政策变化、谈判进展和市场反馈，会决定事件的下一阶段走向。",
        ]

    def _extract_preview_evidence(self, extraction: ExtractionResult) -> list[str]:
        notes: list[str] = []
        if extraction.source_summaries:
            source = str(extraction.source_summaries[0].get("source_type", "")).strip().lower()
            if source == "paste":
                return notes
            preview = self.clean_publish_text(str(extraction.source_summaries[0].get("preview", "")).strip())
            title = self.clean_publish_text(extraction.topic_title).strip()
            if preview and preview != title and "http" not in preview.lower():
                notes.append(preview)
        return notes

    def _sports_evidence_notes(self, extraction: ExtractionResult) -> list[str]:
        return self._extract_preview_evidence(extraction)

    def _international_evidence_notes(self, extraction: ExtractionResult) -> list[str]:
        return self._extract_preview_evidence(extraction)

    def _sports_subject_label(self, extraction: ExtractionResult) -> str:
        text = f"{extraction.topic_title} {extraction.selection_reason} {extraction.suggested_angle}".lower()
        if "world cup" in text or "世界杯" in text:
            return "世界杯名单"
        if "football" in text or "足球" in text:
            return "这份名单"
        if "tennis" in text or "法网" in text:
            return "这次签表"
        return "这条体育消息"

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
            title = str(summary.get("title", "")).strip()
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
        text = " ".join([primary, secondary, trend_report.recommended_angle, *keywords]).lower()
        tech_tokens = ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "ai", "大模型", "智能制造", "工业自动化"]
        tech_tokens_count = sum(1 for token in tech_tokens if token in text)
        mode = "sports" if any(token in text for token in self.SPORTS_TOKENS) \
            else "international" if tech_tokens_count == 0 and any(token in text for token in self.INTERNATIONAL_TOKENS) \
            else "generic"
        if mode == "sports":
            risks = [
                f"{primary}热度升高，不等于后续走势已经被提前锁定。",
                "如果后续名单、状态或临场信息继续变化，当前判断很容易被快速改写。",
                "公开素材有限时，最需要防止的是把初步消息写成确定性结论。",
            ]
            return self.clean_publish_list(risks, limit=4)
        if mode == "international":
            risks = [
                f"{primary}局势变化很快，表态本身不一定等于最终动作。",
                "如果后续谈判、政策或市场反应出现反向变化，当前判断需要及时修正。",
                "公开素材有限时，最需要防止的是把短期消息写成长期定局。",
            ]
            return self.clean_publish_list(risks, limit=4)

        risks = [
            f"{primary}热度上升，不等于所有环节都能同步兑现利润。",
            f"如果订单落地慢于扩产或渠道铺设节奏，{secondary}等环节的利润率可能先承压。",
        ]
        if any(token in trend_report.recommended_angle for token in ("海外", "本地化")):
            risks.append("海外项目交付和本地化服务投入，可能拉长回款与利润兑现周期。")
        else:
            risks.append("客户验证和项目交付如果延后，行业估值很容易先交易预期再回头修正。")
        if any(token in keywords for token in ("逆变器", "系统集成", "减速器", "电机", "热管理")):
            risks.append(f"{secondary}等关键环节一旦竞争加剧，价格战会先侵蚀毛利。")
        return self.clean_publish_list(risks, limit=4)

    def _content_mode(self, extraction: ExtractionResult) -> str:
        text = " ".join(
            item
            for item in (
                extraction.focus_industry,
                extraction.topic_title,
                extraction.selection_reason,
                extraction.suggested_angle,
                extraction.writing_brief,
                extraction.format_brief,
                " ".join(extraction.focus_keywords[:6]),
                " ".join(extraction.supply_chain_keywords[:6]),
            )
            if item
        ).lower()
        if any(token in text for token in self.SPORTS_TOKENS):
            return "sports"
        tech_tokens = ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "ai", "大模型", "智能制造", "工业自动化"]
        tech_tokens_count = sum(1 for token in tech_tokens if token in text)
        if tech_tokens_count == 0 and any(token in text for token in self.INTERNATIONAL_TOKENS):
            return "international"
        return "generic"

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
