from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.compliance_service import is_aigc_declaration_enabled


class PolishAgent(BaseAgent):
    agent_name = "PolishAgent"
    SPORTS_TOKENS = ("体育", "足球", "篮球", "网球", "世界杯", "奥运", "英超", "西甲", "法网", "world cup", "football", "tennis", "french open", "grand slam")
    INTERNATIONAL_TOKENS = ("国际", "美国", "欧洲", "俄罗斯", "乌克兰", "中东", "关税", "制裁", "外交", "局势", "油价", "黄金", "仲裁", "白宫")

    COMPANY_SUFFIX_RE = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,12}(?:科技|能源|汽车|股份|集团|电气|智能|机器人|半导体|电子|时代|精工|制造|材料|电池|重工|工业|光伏|储能|动力|机电|控股|装备|技术)"
    )
    INVALID_COMPANY_MARKERS = {"这家公司", "某公司", "相关公司", "头部企业", "企业", "公司", "厂商", "供应商", "项目方", "客户"}
    INVALID_COMPANY_FRAGMENTS = {"更像", "再落到", "再拆", "资本", "开始", "真正", "故事", "机会", "验证", "订单", "交付", "利润", "估值", "背后"}
    AI_CLICHE_PHRASES = {
        "在当今快节奏的社会中": "",
        "不可否认的是": "",
        "总而言之": "",
        "综上所述": "",
        "令人深思的是": "",
        "发人深省": "",
        "众所周知": "",
        "显而易见": "",
        "毋庸置疑": "",
        "这不禁让人思考": "",
        "不难发现": "",
        "不可否认": "",
        "双刃剑": "利弊并存",
        "值得关注的是": "",
        "换言之": "",
        "深入探讨": "拆解",
        "如前所述": "",
        "值得注意的是": "",
        "由此可见": "",
        "归根结底": "核心问题在于",
        "从某种程度上说": "",
        "毋容讳言": "",
        "令人瞩目的是": "",
        "引人深思": "",
        "一方面": "",
        "另一方面": "",
        "首先": "",
        "其次": "",
        "最后": "",
    }
    ORDER_ANCHOR_TOKENS = ("订单", "中标", "客户", "定点", "签约", "项目", "交付", "量产", "验收", "采购")
    SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")
    NUMERIC_TOKEN_RE = re.compile(
        r"\d+(?:\.\d+)?(?:%|倍|亿元|亿|万元|万亿元|万千瓦|万台|万套|GW|GWh|MWh|kWh|MW|W|年|月|季度|Q[1-4])?"
    )

    ANTI_AI_FLAVOR_APPENDIX = (
        "\n\n[反AI味约束 - 必须遵守]\n"
        "1. 禁止使用以下词汇：综上所述、不可否认、双刃剑、值得关注的是、换言之、深入探讨、"
        "如前所述、值得注意的是、不难发现、由此可见、众所周知、毋庸置疑、总而言之、归根结底、"
        "从某种程度上说、令人瞩目的是、引人深思、一方面、另一方面、首先、其次、最后。\n"
        "2. 句子平均长度控制在20字以内，超过35字的句子必须拆分。\n"
        "3. 采用科技媒体记者的第一人称观察视角（如'笔者认为'、'在现场看到'）。\n"
        "4. 穿插具体的、未经AI抽象化的原始数据（如'同比下滑14.2%'，而非'大幅下滑'）。\n"
        "5. 每段不超过3个句子，段落之间用事实或数据衔接。\n"
        "6. 避免使用排比句和对称结构。\n"
        "7. 开头3句内必须出现至少1个具体数据或事实。\n"
        "8. 全文'据'/'数据显示'/'财报显示'等事实锚点不少于3处。\n"
        "9. 结尾避免总结式收束，改用一个具体判断或前瞻性观察结束。"
    )

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
        revision_focus: list[str] | None = None,
    ) -> dict[str, object]:
        prompt = self.render_prompt(
            "polish_prompt.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            audience=self.settings.audience,
            style_digest=self.style_digest(style),
            cache_note=self.cache_note(cache_context),
            revision_focus=self._format_revision_focus(revision_focus),
            trend_json=self.json_text(trend_report.__dict__),
            data_json=self.json_text(data_report.__dict__),
            article=content.article,
            summary=content.summary,
        )
        prompt = prompt + self.ANTI_AI_FLAVOR_APPENDIX
        payload = self.request_json("你是 PolishAgent，只输出 JSON。", prompt)
        if payload:
            polished = str(payload.get("polished_article", "")).strip()
            if polished:
                article = self._finalize_article(polished, data_report, trend_report, revision_focus=revision_focus)
                validation_notes, validation_score = self._validate_article(article, data_report, trend_report)
                model_notes = self._clean_list(payload.get("validation_notes"), 8)
                return {
                    "polished_article": article,
                    "validation_notes": self.unique(model_notes + validation_notes)[:8],
                    "validation_score": max(self._bound_score(payload.get("validation_score", 0)), validation_score),
                }
        return self._fallback(content, trend_report, data_report, revision_focus=revision_focus)

    def _fallback(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        revision_focus: list[str] | None = None,
    ) -> dict[str, object]:
        article = self._finalize_article(content.article, data_report, trend_report, revision_focus=revision_focus)
        validation_notes, validation_score = self._validate_article(article, data_report, trend_report)
        return {
            "polished_article": article,
            "validation_notes": validation_notes,
            "validation_score": validation_score,
        }

    def _finalize_article(
        self,
        article: str,
        data_report: DataReport,
        trend_report: TrendReport,
        revision_focus: list[str] | None = None,
    ) -> str:
        content_mode = self._content_mode(data_report)
        cleaned = self.strip_prompt_leakage(article)
        cleaned = clean_article_text(cleaned)
        cleaned = self._remove_ai_cliches(cleaned)
        paragraphs = [self._normalize_paragraph(block) for block in cleaned.split("\n\n") if block.strip()]
        paragraphs = [block for block in paragraphs if block]

        if not paragraphs:
            paragraphs = [self._build_mode_fallback_anchor(data_report, trend_report)]

        if content_mode == "generic" and not self._has_concrete_anchor("\n\n".join(paragraphs), data_report):
            anchor = self._build_company_or_order_anchor(data_report)
            if anchor:
                insert_at = 3 if len(paragraphs) >= 3 else len(paragraphs)
                paragraphs.insert(insert_at, anchor)

        if revision_focus and self._should_emphasize_source(revision_focus):
            joined = "\n\n".join(paragraphs)
            if not self._has_source_anchor(joined):
                source_anchor = self._build_source_anchor(data_report)
                if source_anchor:
                    paragraphs.insert(1 if len(paragraphs) > 1 else 0, source_anchor)

        paragraphs = self._rebalance_paragraphs(paragraphs)
        article_text = "\n\n".join(paragraphs[:7]).strip()
        unsupported_tokens = self._unsupported_numeric_tokens(article_text, data_report)
        if self._should_rebuild_cautious(article_text, data_report, unsupported_tokens):
            return self._rebuild_cautious_article(data_report, trend_report)
        return article_text

    def _validate_article(
        self,
        article: str,
        data_report: DataReport,
        trend_report: TrendReport,
    ) -> tuple[list[str], int]:
        notes: list[str] = []
        score = 38
        article_lower = article.lower()
        paragraphs = [item.strip() for item in article.split("\n\n") if item.strip()]
        max_paragraph_length = max((len(item) for item in paragraphs), default=0)

        evidence_hits = self._count_hits(article_lower, data_report.growth_numbers[:4])
        content_mode = self._content_mode(data_report)
        if evidence_hits:
            score += 10
            notes.append(f"正文已落入 {evidence_hits} 个核心数字或增速信号。")
        else:
            if content_mode == "sports":
                notes.append("正文仍缺少更具体的事实锚点，建议补进名单、赛程、人物状态或争议背景。")
            elif content_mode == "international":
                notes.append("正文仍缺少更具体的事实锚点，建议补进政策动作、谈判节点或市场反应。")
            else:
                notes.append("正文仍缺少明确数字锚点，建议至少保留 1 到 2 组关键数据。")

        company_hits = self._count_hits(article_lower, self._company_candidates(data_report)[:4])
        order_hits = sum(1 for token in self.ORDER_ANCHOR_TOKENS if token in article)
        if content_mode != "generic":
            score += 8
            notes.append("正文主体已经围绕事件影响和后续变量展开，不需要强行补公司订单主体。")
        elif company_hits or order_hits >= 3:
            score += 12
            if company_hits:
                notes.append(f"正文已点名 {company_hits} 个公司或项目主体，主体感更清楚。")
            else:
                notes.append("正文虽然没充分点名公司，但已经落到订单、客户和交付主体。")
        else:
            notes.append("正文主体感仍偏弱，建议明确公司、客户、订单方或交付项目。")

        keyword_hits = self._count_hits(article_lower, data_report.supply_chain_keywords[:5])
        if content_mode == "sports":
            score += 8
            notes.append("正文已经围绕比赛走势、阵容判断和后续变量展开。")
        elif content_mode == "international":
            score += 8
            notes.append("正文已经围绕博弈关系、后续变量和外溢影响展开。")
        elif keyword_hits >= 2:
            score += 10
            notes.append("正文已覆盖关键产业链环节，不只是复述新闻事件。")
        else:
            notes.append("正文对产业链位置展开还不够，建议补上环节分工与利润归属。")

        angle = self.clean_publish_text(trend_report.recommended_angle).lower()
        if angle and angle[:10] in article_lower:
            score += 8
            notes.append("核心切入角度已经在正文中落地。")
        else:
            notes.append("推荐切入角度还可以再集中一点，避免观点发散。")

        if self._has_source_anchor(article):
            score += 10
            notes.append("正文带有公开材料或来源感表达，证据更稳。")
        else:
            notes.append("建议补一句公开材料、公告或机构统计的出处感表达。")

        unsupported_tokens = self._unsupported_numeric_tokens(article, data_report)
        if unsupported_tokens:
            penalty = len(unsupported_tokens) * 12
            if "title_only" in set(data_report.topic_quality_flags):
                penalty += 12
            elif data_report.evidence_strength <= 72:
                penalty += 8
            score -= min(44, penalty)
            preview = "、".join(unsupported_tokens[:4])
            notes.append(f"正文出现素材未明确给出的新增数字：{preview}，建议删掉或改成定性判断。")
        else:
            score += 8
            notes.append("正文没有明显扩写出素材之外的新数字，证据边界控制较稳。")

        if max_paragraph_length <= 150:
            score += 10
            notes.append("段落长度适合移动端阅读。")
        else:
            notes.append("仍有偏长段落，建议继续拆短，提升完读率。")

        if "#" not in article and "##" not in article:
            score += 8
            notes.append("正文已去掉 markdown 式标题符号。")
        else:
            notes.append("正文里仍残留 markdown 符号，需要继续清理。")

        if len(paragraphs) >= 5:
            score += 6
            notes.append("段落结构已经接近头条原生长文节奏。")
        else:
            notes.append("段落数量偏少，建议至少整理成 5 段。")

        template_count = self._count_template_patterns(article)
        if template_count <= 2:
            score += 10
            notes.append("正文表达多样化，模板化句式使用较少。")
        elif template_count <= 4:
            score += 5
            notes.append("正文存在少量模板化表达，建议适当调整句式。")
        else:
            notes.append("正文模板化表达偏多，建议增加表达多样性。")

        ai_cliche_count = sum(1 for phrase in self.AI_CLICHE_PHRASES if phrase in article)
        if ai_cliche_count > 2:
            score -= 10
            notes.append(f"正文包含 {ai_cliche_count} 个 AI 常见口头禅，建议删除或改写。")

        return self.unique(notes)[:8], min(100, score)

    def _count_template_patterns(self, article: str) -> int:
        patterns = [
            "不是.*而是.*",
            "不只是.*而是.*",
            "真正值得.*的是.*",
            "关键不在.*而在.*",
            "这意味着.*",
            "这说明.*",
            "可以看出.*",
            "从中可以.*",
        ]
        count = 0
        for pattern in patterns:
            matches = re.findall(pattern, article)
            count += len(matches)
        cliche_count = sum(1 for phrase in self.AI_CLICHE_PHRASES if phrase in article)
        count += cliche_count
        return count

    def _remove_ai_cliches(self, article: str) -> str:
        for phrase, replacement in self.AI_CLICHE_PHRASES.items():
            if phrase not in article:
                continue
            if replacement:
                article = article.replace(phrase, replacement)
            else:
                article = article.replace(phrase, "")
        article = re.sub(r"[，、]{2,}", "，", article)
        article = re.sub(r"[。]{2,}", "。", article)
        article = re.sub(r"\s{2,}", " ", article)
        article = re.sub(r"^[，。、；：\s]+", "", article)
        article = re.sub(r"[，。、；：\s]+$", "", article, flags=re.MULTILINE)
        return article.strip()

    def _should_rebuild_cautious(
        self,
        article: str,
        data_report: DataReport,
        unsupported_tokens: list[str],
    ) -> bool:
        if not unsupported_tokens:
            return False
        flags = set(data_report.topic_quality_flags)
        if "title_only" in flags:
            return True
        if data_report.content_format == "quick_commentary" and len(unsupported_tokens) >= 2:
            return True
        if data_report.evidence_strength <= 72 and len(unsupported_tokens) >= 2:
            return True
        return len(unsupported_tokens) >= 4 and not self._has_source_anchor(article)

    def _unsupported_numeric_tokens(self, article: str, data_report: DataReport) -> list[str]:
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
            token = str(match).strip()
            if not self._is_significant_numeric_token(token):
                continue
            normalized = token.replace(" ", "")
            if normalized not in tokens:
                tokens.append(normalized)
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
        cleaned = str(token or "").strip()
        if not cleaned:
            return False
        suffixes = ("%", "倍", "亿元", "亿", "万元", "万亿元", "万千瓦", "万台", "万套", "GW", "GWh", "MWh", "kWh", "MW", "W", "年", "月", "季度")
        if cleaned.startswith("Q") and len(cleaned) == 2:
            return True
        if any(marker in cleaned for marker in suffixes):
            return True
        return len(re.sub(r"\D", "", cleaned)) >= 4

    def _rebuild_cautious_article(self, data_report: DataReport, trend_report: TrendReport) -> str:
        content_mode = self._content_mode(data_report)
        if content_mode == "sports":
            return self._rebuild_cautious_sports_article(data_report, trend_report)
        if content_mode == "international":
            return self._rebuild_cautious_international_article(data_report, trend_report)
        primary = self.clean_publish_text(
            data_report.focus_industry or (data_report.focus_keywords[0] if data_report.focus_keywords else "相关赛道")
        )
        companies = self._company_candidates(data_report)[:2]
        source_anchor = self._build_source_anchor(data_report) or "从公开材料看，当前已确认的信号仍然有限。"
        numbers = "、".join(self.clean_publish_list(data_report.growth_numbers, limit=3))
        core_data = "；".join(self.clean_publish_list(data_report.core_data, limit=3))
        trends = "；".join(self.clean_publish_list(data_report.industry_trends, limit=3))
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=4))
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=3))
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        closing_question = self._build_closing_question(data_report)

        first_paragraph = (
            f"这更像{primary}的一条执行前信号，而不是已经兑现到利润表里的确定性结论。"
            f"{source_anchor}真正该看的，是后续有没有订单、并网、交付或毛利数据继续跟上。"
        )
        second_paragraph = (
            f"目前公开信息里相对明确的数字，主要集中在{numbers}。"
            if numbers
            else f"目前公开信息里更明确的，仍然是{primary}方向上的政策和项目推进信号。"
        )
        if core_data:
            second_paragraph += f"{core_data}。"
        else:
            second_paragraph += "这意味着眼下更适合先判断节奏，不适合提前下过满的业绩结论。"

        if companies:
            third_paragraph = (
                f"从主体上看，{ '、'.join(companies) }这类公司更值得跟踪，但重点不是先猜份额，而是看它们是否持续给出可执行订单、交付节奏和客户验证。"
            )
        else:
            third_paragraph = (
                f"从产业链位置看，{primary}真正能否走强，还是要回到设备交付、并网验证、系统集成和后续运维这些执行环节。"
            )
        if trends:
            third_paragraph += f"{trends}。"

        fourth_paragraph = (
            f"接下来更值得跟踪的，是{metrics}。"
            if metrics
            else f"接下来更值得跟踪的，是订单落地、交付节奏、项目并网和利润兑现。"
        )
        if angle:
            fourth_paragraph += f"{angle}"
        else:
            fourth_paragraph += "只要这些指标还没有继续落地，就不适合把这类题提前写成全行业普涨。"

        fifth_paragraph = (
            f"风险边界也要保留：{risks}。{closing_question}"
            if risks
            else f"风险边界也很明确：如果后续订单、交付和利润数据跟不上，当前信号就更接近预期修复而不是业绩兑现。{closing_question}"
        )
        paragraphs = [first_paragraph, second_paragraph, third_paragraph, fourth_paragraph, fifth_paragraph]
        return "\n\n".join(self._rebalance_paragraphs(paragraphs)[:7]).strip()

    def _build_closing_question(self, data_report: DataReport) -> str:
        content_mode = self._content_mode(data_report)
        primary = self.clean_publish_text(
            data_report.focus_industry or (data_report.focus_keywords[0] if data_report.focus_keywords else "这个方向")
        )
        if content_mode == "sports":
            return "你觉得这条消息最可能改变阵容选择、比赛走势还是舆论预期？"
        if content_mode == "international":
            return "你觉得接下来最值得盯的是谁会先让步、谁会继续施压，还是市场会先给出反应？"
        return f"您更看重{primary}后续先落地订单、交付，还是毛利率改善？"

    def _company_candidates(self, data_report: DataReport) -> list[str]:
        candidates = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        return [item for item in candidates if self._is_valid_company_name(item)]

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
        companies = self._company_candidates(data_report)[:2]
        metrics = self.clean_publish_list(data_report.watch_metrics[:4], limit=4)
        follow_up = [item for item in re.split(r"[、，,；;]", data_report.follow_up_focus) if str(item).strip()]
        follow_points = self.clean_publish_list(follow_up, limit=4)
        primary = self.clean_publish_text(
            data_report.focus_industry or (data_report.focus_keywords[0] if data_report.focus_keywords else "相关赛道")
        )
        nodes = self.clean_publish_list(data_report.focus_keywords[1:] + data_report.supply_chain_keywords[1:], limit=3)

        if companies:
            company_text = "、".join(companies)
            metric_text = "、".join(follow_points or metrics or ["客户验证", "订单节奏", "交付兑现"])
            node_text = "、".join(nodes) if nodes else primary
            return (
                f"真正需要盯住的，是{company_text}这类主体能不能把{node_text}上的订单和交付兑现出来。"
                f"如果后续还能持续给出{metric_text}的明确信号，这类公司才可能从题材受益者走向业绩兑现者。"
            )

        metric_text = "、".join(follow_points or metrics or ["客户验证", "项目交付", "毛利变化"])
        node_text = "、".join(nodes) if nodes else primary
        return (
            f"即便标题里没有直接点名公司，执行层主体其实已经很清楚：上游看{node_text}，下游看客户验证、量产节奏和海外交付。"
            f"谁能先把{metric_text}做实，谁才更可能先兑现利润。"
        )

    def _has_concrete_anchor(self, article: str, data_report: DataReport) -> bool:
        article_lower = article.lower()
        company_hits = sum(1 for item in self._company_candidates(data_report)[:4] if item.lower() in article_lower)
        order_hits = sum(1 for token in self.ORDER_ANCHOR_TOKENS if token in article)
        return company_hits >= 1 or order_hits >= 3

    def _rebalance_paragraphs(self, paragraphs: list[str]) -> list[str]:
        blocks = [block.strip() for block in paragraphs if block.strip()]
        expanded: list[str] = []
        for block in blocks:
            if len(block) > 160:
                expanded.extend(self._split_long_paragraph(block))
            else:
                expanded.append(block)

        while len(expanded) < 5:
            longest_index = max(range(len(expanded)), key=lambda idx: len(expanded[idx]))
            split_blocks = self._split_long_paragraph(expanded[longest_index], force=True)
            if len(split_blocks) <= 1:
                break
            expanded = expanded[:longest_index] + split_blocks + expanded[longest_index + 1 :]

        while len(expanded) > 7:
            shortest_index = min(range(1, len(expanded)), key=lambda idx: len(expanded[idx]))
            expanded[shortest_index - 1] = f"{expanded[shortest_index - 1]} {expanded[shortest_index]}".strip()
            del expanded[shortest_index]

        return [block for block in expanded if block]

    def _split_long_paragraph(self, paragraph: str, force: bool = False) -> list[str]:
        sentences = [item.strip() for item in self.SENTENCE_SPLIT_RE.split(paragraph) if item.strip()]
        if len(sentences) <= 1 and not force:
            return [paragraph]
        if len(sentences) <= 1 and force:
            parts = [item.strip() for item in re.split(r"(?<=[，；;])", paragraph) if item.strip()]
            return parts if len(parts) > 1 else [paragraph]

        result: list[str] = []
        current = ""
        count = 0
        for sentence in sentences:
            candidate = f"{current}{sentence}" if current else sentence
            if current and (len(candidate) > 118 or count >= 2):
                result.append(current.strip())
                current = sentence
                count = 1
            else:
                current = candidate
                count += 1
        if current.strip():
            result.append(current.strip())
        return result or [paragraph]

    def _normalize_paragraph(self, text: str) -> str:
        cleaned = self.strip_prompt_leakage(text).strip()
        cleaned = cleaned.replace("**", "").replace("__", "")
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
        cleaned = re.sub(r"^\-\s+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _build_source_anchor(self, data_report: DataReport) -> str:
        for note in data_report.evidence_notes:
            text = self.clean_publish_text(note).strip()
            if text:
                if not text.endswith(("。", "！", "？")):
                    text += "。"
                return f"从公开材料看，{text}"
        if data_report.growth_numbers:
            return f"目前至少已经明确的数字信号包括{'、'.join(data_report.growth_numbers[:2])}。"
        return ""

    def _should_emphasize_source(self, revision_focus: list[str]) -> bool:
        joined = " ".join(revision_focus)
        return any(token in joined for token in ("出处", "证据", "来源", "公开材料", "公告", "财报"))

    def _format_revision_focus(self, revision_focus: list[str] | None) -> str:
        if not revision_focus:
            return "默认提升证据感、段落节奏和主体锚点。"
        return "\n".join(f"- {item}" for item in revision_focus[:6])

    def _count_hits(self, article_text: str, candidates: list[str]) -> int:
        hits = 0
        for item in candidates:
            token = str(item).strip().lower()
            if token and token in article_text:
                hits += 1
        return hits

    def _content_mode(self, data_report: DataReport) -> str:
        text = " ".join(
            item
            for item in (
                data_report.focus_industry,
                data_report.topic_title,
                data_report.selection_reason,
                data_report.suggested_angle,
                data_report.writing_brief,
                data_report.format_brief,
                " ".join(data_report.focus_keywords[:6]),
                " ".join(data_report.supply_chain_keywords[:6]),
            )
            if item
        ).lower()
        if any(token in text for token in self.SPORTS_TOKENS):
            return "sports"
        if any(token in text for token in self.INTERNATIONAL_TOKENS):
            return "international"
        return "generic"

    def _build_mode_fallback_anchor(self, data_report: DataReport, trend_report: TrendReport) -> str:
        mode = self._content_mode(data_report)
        if mode == "sports":
            return self._rebuild_cautious_sports_article(data_report, trend_report).split("\n\n")[0]
        if mode == "international":
            return self._rebuild_cautious_international_article(data_report, trend_report).split("\n\n")[0]
        return self._build_company_or_order_anchor(data_report)

    def _rebuild_cautious_sports_article(self, data_report: DataReport, trend_report: TrendReport) -> str:
        title = self.clean_publish_text(data_report.topic_title).strip() or "这条体育消息"
        source_anchor = self._build_source_anchor(data_report) or "从公开材料看，目前能确认的信息仍然有限。"
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=4)) or "阵容变化、赛程难度、关键人物状态和舆论争议"
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=3)) or "公开素材有限时，最需要防止的是把赛前消息写成确定性结论"
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        paragraphs = [
            f"{title}不只是赛前消息，它更像一个会改变后续判断的前置信号。{source_anchor}",
            f"真正值得看的，不是把新闻复述一遍，而是它会不会改变阵容选择、比赛走势和舆论预期。",
            f"接下来更值得跟踪的，是{metrics}。这些变量会决定这条消息最终只是短期讨论，还是会真的影响后续比赛。",
            f"{angle or '真正有价值的写法，是把关键人物、关键节点和后续走势说清楚。'}",
            f"风险边界也要保留：{risks}。{self._build_closing_question(data_report)}",
        ]
        return "\n\n".join(self._rebalance_paragraphs(paragraphs)[:7]).strip()

    def _rebuild_cautious_international_article(self, data_report: DataReport, trend_report: TrendReport) -> str:
        title = self.clean_publish_text(data_report.topic_title).strip() or "这条国际消息"
        source_anchor = self._build_source_anchor(data_report) or "从公开材料看，目前能确认的信息仍然有限。"
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=4)) or "政策表态、谈判进展、相关国家动作和市场反馈"
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=3)) or "公开素材有限时，最需要防止的是把短期消息写成长期定局"
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        paragraphs = [
            f"{title}不能只当单点新闻看，它更像一场更大博弈里的新动作。{source_anchor}",
            "真正值得看的，不是表态本身，而是谁在施压、谁在让步，以及下一步动作会不会继续升级。",
            f"接下来更值得跟踪的，是{metrics}。这些变量会决定局势是继续升级、短暂缓和，还是转向新的谈判阶段。",
            f"{angle or '真正有价值的写法，是把事件、利益关系和后续变量连接起来。'}",
            f"风险边界也要保留：{risks}。{self._build_closing_question(data_report)}",
        ]
        return "\n\n".join(self._rebalance_paragraphs(paragraphs)[:7]).strip()

    def _has_source_anchor(self, article: str) -> bool:
        markers = ("据", "数据显示", "公开材料", "财报", "公告", "机构统计", "报告")
        return any(marker in article for marker in markers)

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]

    def _bound_score(self, value: object) -> int:
        try:
            score = int(value)
        except (TypeError, ValueError):
            score = 0
        return max(0, min(100, score))
