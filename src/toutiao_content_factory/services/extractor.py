from __future__ import annotations

import re
from collections import Counter

from toutiao_content_factory.models import ExtractionResult, SourceDocument


class SignalExtractor:
    GENERIC_COMPANY_EXCLUSIONS = {
        "人形机器人",
        "机器人",
        "储能",
        "新能源",
        "智能装备",
        "产业链",
        "系统集成",
        "热管理",
        "机器视觉",
        "国内人形机器人",
    }
    MAX_KEYWORDS_COUNT = 6
    MAX_COMPANIES_COUNT = 4
    MAX_SENTENCES_COUNT = 20
    MAX_DATA_SENTENCES_COUNT = 6
    MAX_TRENDS_COUNT = 6
    MAX_KEY_POINTS_COUNT = 6
    MAX_EVIDENCE_COUNT = 6
    MAX_GROWTH_NUMBERS_COUNT = 12
    MAX_TITLE_COMPANY_CANDIDATES = 12
    SOURCE_PREVIEW_LENGTH = 180
    INDUSTRY_KEYWORDS = [
        "储能",
        "新能源",
        "人形机器人",
        "机器人",
        "智能装备",
        "工业自动化",
        "减速器",
        "电机",
        "丝杠",
        "逆变器",
        "热管理",
        "系统集成",
        "机器视觉",
        "AI硬件",
        "算力",
        "电池",
        "EMS",
        "PCS",
    ]
    NOISE_MARKERS = (
        "标题：",
        "来源：",
        "发布日期：",
        "原文链接：",
        "素材正文：",
        "摘要：",
        "片段：",
        "补充片段：",
        "提取数字：",
        "匹配赛道：",
        "匹配关键词：",
        "主赛道：",
        "主关键词：",
        "可确认数字：",
        "账号定位：",
        "账号：",
        "目标平台：",
        "关注赛道：",
        "自动生成输入",
        "入选理由：",
        "推荐主结论：",
        "跟踪变量：",
        "固定写法：",
        "请围绕这条产业线索",
        "请重点分析",
        "写作要求：",
        "推荐标题",
    )
    COMPANY_STOP_WORDS = {
        "这家公司",
        "这类公司",
        "相关公司",
        "头部企业",
        "主流新能源",
        "一众主流新能源",
        "国内新能源",
        "海外新能源",
        "整车企业",
        "车企",
        "企业",
        "公司",
        "客户",
        "项目方",
        "供应商",
        "品牌",
        "产业链",
        "行业",
        "市场",
        "赛道",
        "新能源车",
    }
    COMPANY_PREFIX_NOISE = ("这家", "这类", "相关", "头部", "一众", "主流", "国内", "海外", "而", "某", "该", "再", "曾经")
    COMPANY_PATTERN = re.compile(
        r"[A-Za-z0-9\u4e00-\u9fff]{2,24}(?:公司|集团|科技|能源|机器人|股份|电气|动力|智能|电源|汽车|材料|电池|储能|装备)"
    )
    NUMBER_PATTERN = re.compile(
        r"(?:同比|环比|增长|提升|下降|下滑|减少|扩大|超|近|约|达)?\s*"
        r"\d+(?:\.\d+)?(?:%|亿|亿元|亿美元|万元|万台|万辆|万套|万吨|万千瓦|万千瓦时|"
        r"GWh|MWh|kWh|Wh|Ah|GW|MW|kW|W|V|A|个月|季度|年|倍|天|小时|分钟)"
    )

    def extract(self, documents: list[SourceDocument]) -> ExtractionResult:
        merged_text = "\n\n".join(document.content for document in documents)
        source_summaries = [self._build_source_summary(document) for document in documents]
        focus_industry = self._resolve_focus_industry(documents, merged_text)
        focus_documents = self._resolve_focus_documents(documents, focus_industry)
        analysis_text = "\n\n".join(document.content for document in focus_documents) or merged_text
        sentences = self._split_sentences(merged_text)
        analysis_sentences = self._split_sentences(analysis_text)
        topic_title = self._first_metadata_value(documents, "topic_title")
        selection_reason = self._first_metadata_value(documents, "selection_reason")
        suggested_angle = self._first_metadata_value(documents, "suggested_angle")
        follow_up_focus = self._first_metadata_value(documents, "follow_up_focus")
        writing_brief = self._first_metadata_value(documents, "writing_brief")
        topic_type = self._first_metadata_value(documents, "topic_type")
        content_format = self._first_metadata_value(documents, "content_format")
        format_brief = self._first_metadata_value(documents, "format_brief")
        paragraph_instruction = self._first_metadata_value(documents, "paragraph_instruction")
        evidence_strength = self._parse_metadata_int(self._first_metadata_value(documents, "evidence_strength"))
        topic_quality_score = self._parse_metadata_int(self._first_metadata_value(documents, "topic_quality_score"))
        topic_quality_flags = self._parse_metadata_list(self._first_metadata_value(documents, "topic_quality_flags"))
        word_target = self._parse_metadata_int(self._first_metadata_value(documents, "word_target"))

        focus_keywords = self._resolve_focus_keywords(documents, analysis_text, focus_industry)
        filtered_sentences = self._filter_sentences_by_focus(analysis_sentences, focus_industry, focus_keywords)

        growth_numbers = self._extract_growth_numbers(analysis_text)
        companies = self._extract_companies(analysis_text, topic_title)
        focus_companies = self._resolve_focus_companies(companies, filtered_sentences, focus_keywords)
        supply_chain_keywords = self._extract_keywords(analysis_text)
        core_data = self._pick_data_sentences(filtered_sentences or analysis_sentences or sentences)
        industry_trends = self._build_trends(filtered_sentences or sentences, focus_keywords or supply_chain_keywords)
        key_points = self._build_key_points(
            filtered_sentences or analysis_sentences or sentences,
            focus_keywords or supply_chain_keywords,
            growth_numbers,
        )
        evidence_items = self._build_evidence_items(
            filtered_sentences or sentences,
            focus_documents or documents,
            source_summaries,
        )

        return ExtractionResult(
            merged_text=merged_text,
            core_data=core_data,
            industry_trends=industry_trends,
            growth_numbers=growth_numbers,
            companies=companies,
            supply_chain_keywords=supply_chain_keywords,
            key_points=key_points,
            focus_industry=focus_industry,
            focus_keywords=focus_keywords,
            focus_companies=focus_companies,
            topic_title=topic_title,
            selection_reason=selection_reason,
            suggested_angle=suggested_angle,
            follow_up_focus=follow_up_focus,
            writing_brief=writing_brief,
            topic_type=topic_type,
            content_format=content_format,
            format_brief=format_brief,
            paragraph_instruction=paragraph_instruction,
            evidence_strength=evidence_strength,
            topic_quality_score=topic_quality_score,
            topic_quality_flags=topic_quality_flags,
            word_target=word_target,
            evidence_items=evidence_items,
            source_summaries=source_summaries,
        )

    def _resolve_focus_documents(self, documents: list[SourceDocument], focus_industry: str) -> list[SourceDocument]:
        if not focus_industry:
            return list(documents)

        filtered = [
            document
            for document in documents
            if focus_industry in document.content
            or document.metadata.get("primary_industry", "").strip() == focus_industry
        ]
        return filtered or list(documents)

    def _build_source_summary(self, document: SourceDocument) -> dict[str, str]:
        preview = document.content[:self.SOURCE_PREVIEW_LENGTH].replace("\n", " ")
        return {
            "title": document.title or "untitled",
            "source_type": document.source_type,
            "path": str(document.path) if document.path else "manual_paste",
            "preview": preview,
        }

    def _first_metadata_value(self, documents: list[SourceDocument], key: str) -> str:
        for document in documents:
            value = document.metadata.get(key, "").strip()
            if value:
                return value
        return ""

    def _parse_metadata_int(self, value: str) -> int:
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else 0

    def _parse_metadata_list(self, value: str) -> list[str]:
        raw = str(value or "").strip()
        if not raw:
            return []
        return self._deduplicate([item.strip() for item in re.split(r"[、，,；;|/]", raw) if item.strip()])[:8]

    def _split_sentences(self, text: str) -> list[str]:
        chunks = re.split(r"[。！？；\n]+", text)
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    def _resolve_focus_industry(self, documents: list[SourceDocument], merged_text: str) -> str:
        for document in documents:
            metadata_value = document.metadata.get("primary_industry", "").strip()
            if metadata_value:
                return metadata_value

        counts = Counter(keyword for keyword in self.INDUSTRY_KEYWORDS if keyword in merged_text)
        return counts.most_common(1)[0][0] if counts else ""

    def _resolve_focus_keywords(
        self,
        documents: list[SourceDocument],
        analysis_text: str,
        focus_industry: str,
    ) -> list[str]:
        metadata_keywords: list[str] = []
        for document in documents:
            raw = document.metadata.get("primary_keywords", "").strip()
            if not raw:
                continue
            metadata_keywords.extend([item.strip() for item in raw.split("、") if item.strip()])

        if metadata_keywords:
            return self._deduplicate(metadata_keywords)[:self.MAX_KEYWORDS_COUNT]

        ranked = self._extract_keywords(analysis_text)
        if focus_industry:
            ranked = [focus_industry] + [item for item in ranked if item != focus_industry]
        return self._deduplicate(ranked)[:self.MAX_KEYWORDS_COUNT]

    def _filter_sentences_by_focus(
        self,
        sentences: list[str],
        focus_industry: str,
        focus_keywords: list[str],
    ) -> list[str]:
        if not focus_industry and not focus_keywords:
            return []

        anchors = self._deduplicate(([focus_industry] if focus_industry else []) + focus_keywords)[:self.MAX_KEYWORDS_COUNT]
        result: list[str] = []
        for sentence in sentences:
            if self._is_noise_sentence(sentence):
                continue
            if any(anchor and anchor in sentence for anchor in anchors):
                result.append(sentence)
                continue
            if self.NUMBER_PATTERN.search(sentence) and any(
                token in sentence for token in ("订单", "交付", "毛利", "验证", "量产", "中标", "并网", "装机", "报价", "出货")
            ):
                result.append(sentence)
        return self._deduplicate(result)[:self.MAX_SENTENCES_COUNT]

    def _extract_keywords(self, text: str) -> list[str]:
        counts = Counter(keyword for keyword in self.INDUSTRY_KEYWORDS if keyword in text)
        ranked: list[str] = []
        for keyword, _ in counts.most_common(10):
            if any(keyword in existing for existing in ranked):
                continue
            ranked.append(keyword)
        return ranked or ["科技制造", "产业链"]

    def _resolve_focus_companies(
        self,
        companies: list[str],
        filtered_sentences: list[str],
        focus_keywords: list[str],
    ) -> list[str]:
        ranked: list[str] = []
        for company in companies:
            sentence_hits = sum(1 for sentence in filtered_sentences if company in sentence)
            keyword_hits = sum(1 for keyword in focus_keywords if keyword and any(keyword in sentence and company in sentence for sentence in filtered_sentences))
            ranked.append((sentence_hits * 3 + keyword_hits, company))
        ranked.sort(reverse=True)
        return [company for _score, company in ranked if company][:self.MAX_COMPANIES_COUNT]

    def _pick_data_sentences(self, sentences: list[str]) -> list[str]:
        scored = [
            sentence
            for sentence in sentences
            if (
                self.NUMBER_PATTERN.search(sentence)
                or any(token in sentence for token in ("数据", "装机", "毛利", "中标", "并网", "订单", "交付", "出货", "产能"))
            )
            and not self._is_noise_sentence(sentence)
        ]
        return self._deduplicate(scored)[:self.MAX_DATA_SENTENCES_COUNT]

    def _build_trends(self, sentences: list[str], keywords: list[str]) -> list[str]:
        trend_candidates: list[str] = []
        for sentence in sentences:
            if self._is_noise_sentence(sentence):
                continue
            if any(word in sentence for word in ("趋势", "加快", "扩产", "验证", "海外", "需求", "订单", "交付", "回报周期", "并网", "招标", "出货")):
                trend_candidates.append(sentence)

        if not trend_candidates and keywords:
            trend_candidates = [f"{keywords[0]}相关环节进入验证和订单联动阶段。"]

        return self._deduplicate(trend_candidates)[:self.MAX_TRENDS_COUNT]

    def _build_key_points(
        self,
        sentences: list[str],
        keywords: list[str],
        growth_numbers: list[str],
    ) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for sentence in sentences:
            if self._is_noise_sentence(sentence):
                continue
            score = 0
            if any(word in sentence for word in ("验证", "量产", "商业", "示范", "回报周期", "扩产")):
                score += 3
            if any(word in sentence for word in ("订单", "交付", "客户", "本地化", "海外", "出货")):
                score += 3
            if any(word in sentence for word in ("利润", "毛利", "集中度", "议价", "卡位")):
                score += 2
            if any(keyword in sentence for keyword in keywords[:4]):
                score += 1
            if score > 0:
                ranked.append((score, sentence))

        ranked.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        points = [sentence for _score, sentence in ranked[:self.MAX_KEY_POINTS_COUNT]]

        if not points and keywords:
            primary = keywords[0]
            secondary = keywords[1] if len(keywords) > 1 else "关键环节"
            if growth_numbers:
                points.append(f"{primary}景气信号已经出现，但真正决定兑现节奏的还是{secondary}等环节的订单和交付。")
            else:
                points.append(f"{primary}正在从概念热度走向商业验证，下一步要看{secondary}等环节谁先兑现。")

        return self._deduplicate(points)[:self.MAX_KEY_POINTS_COUNT]

    def _build_evidence_items(
        self,
        sentences: list[str],
        documents: list[SourceDocument],
        source_summaries: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        evidence: list[dict[str, str]] = []
        source_name = source_summaries[0]["title"] if source_summaries else "source"
        for sentence in sentences:
            if self._is_noise_sentence(sentence):
                continue
            if not self.NUMBER_PATTERN.search(sentence) and not any(
                token in sentence for token in ("数据显示", "回报周期", "订单", "交付", "毛利")
            ):
                continue
            matched_source = source_name
            for document in documents:
                title = document.title or "source"
                content = document.content
                if sentence in content or sentence[:self.SOURCE_PREVIEW_LENGTH // 10] in content:
                    matched_source = title
                    break
            evidence.append(
                {
                    "fact": sentence.strip(),
                    "source": matched_source,
                }
            )
            if len(evidence) >= self.MAX_EVIDENCE_COUNT:
                break
        return evidence

    def _is_noise_sentence(self, sentence: str) -> bool:
        stripped = sentence.strip("，。；：: ")
        if len(stripped) <= 4:
            return True
        if any(marker in stripped for marker in self.NOISE_MARKERS):
            return True
        return stripped in {"关键结论", "数据观察", "产业链关键词", "行业判断"}

    def _extract_growth_numbers(self, analysis_text: str) -> list[str]:
        return self._deduplicate(self.NUMBER_PATTERN.findall(analysis_text))[:self.MAX_GROWTH_NUMBERS_COUNT]

    def _extract_companies(self, analysis_text: str, topic_title: str) -> list[str]:
        candidates = self._deduplicate(self.COMPANY_PATTERN.findall(analysis_text))
        title_candidates = self._extract_title_company_candidates(topic_title)
        merged = self._deduplicate(title_candidates + candidates)
        return [item for item in merged if self._is_valid_company_name(item)][:self.MAX_TITLE_COMPANY_CANDIDATES]

    def _extract_title_company_candidates(self, topic_title: str) -> list[str]:
        if not topic_title:
            return []
        candidates: list[str] = []
        for part in re.split(r"[，,：:、（）()\-\s]", topic_title):
            current = part.strip()
            if 2 <= len(current) <= 8 and re.search(r"[\u4e00-\u9fff]", current):
                candidates.append(current)
        return candidates

    def _is_valid_company_name(self, value: str) -> bool:
        candidate = value.strip()
        if not candidate or len(candidate) < 2 or len(candidate) > 18:
            return False
        if candidate in self.GENERIC_COMPANY_EXCLUSIONS or candidate in self.COMPANY_STOP_WORDS:
            return False
        if any(candidate.startswith(prefix) for prefix in self.COMPANY_PREFIX_NOISE):
            return False
        if any(token in candidate for token in ("数据", "趋势", "摘要", "原因", "结论", "价格战", "订单", "交付", "风险")):
            return False
        if re.search(r"[。！？；：:#]", candidate):
            return False
        has_suffix = bool(
            re.search(
                r"(公司|集团|科技|能源|机器人|股份|电气|动力|智能|电源|汽车|材料|电池|储能|装备)$",
                candidate,
            )
        )
        short_cjk_name = bool(re.fullmatch(r"[\u4e00-\u9fff]{2,6}", candidate))
        return has_suffix or short_cjk_name

    def _deduplicate(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result
