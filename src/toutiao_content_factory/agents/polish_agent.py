from __future__ import annotations

import re
import logging
from typing import Optional

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, IterationOverrides, StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.compliance_service import is_aigc_declaration_enabled
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class PolishAgent(BaseAgent):
    agent_name = "PolishAgent"
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


    ANTI_AI_FLAVOR_APPENDIX = (
        "\n\n[反AI味约束 - 必须遵守]\n"
        "1. 禁止使用以下词汇：综上所述、不可否认、双刃剑、值得关注的是、换言之、深入探讨、"
        "如前所述、值得注意的是、不难发现、由此可见、众所周知、毋庸置疑、总而言之、归根结底、"
        "从某种程度上说、令人瞩目的是、引人深思、一方面、另一方面、首先、其次、最后。\n"
        "2. 禁止使用以下AI句式模板：'从XX的视角来看'、'这一趋势并非孤立事件'、'值得持续关注'、"
        "'需要持续观察'、'将直接影响'、'这些因素都可能影响'、'数据口径以公开材料为准'、"
        "'后续需关注最新财报更新'、'关键变量的边际变化'、'市场预期可能需要重新校准'、"
        "'保持对核心指标的动态跟踪'、'区分短期波动与长期趋势'。\n"
        "3. 句子平均长度控制在20字以内，超过35字的句子必须拆分。\n"
        "4. 采用科技媒体记者的第一人称观察视角（如'笔者认为'、'在现场看到'）。\n"
        "5. 穿插具体的、未经AI抽象化的原始数据（如'同比下滑14.2%'，而非'大幅下滑'）。\n"
        "6. 每段不超过3个句子，段落之间用事实或数据衔接。\n"
        "7. 避免使用排比句和对称结构。\n"
        "8. 开头3句内必须出现至少1个具体数据或事实。\n"
        "9. 全文'据'/'数据显示'/'财报显示'等事实锚点不少于3处。\n"
        "10. 结尾避免总结式收束，改用一个具体判断或前瞻性观察结束。"
    )

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
        iteration_overrides: Optional[IterationOverrides] = None,
    ) -> None:
        super().__init__(settings, prompt_repository, openai_service, logger)
        self.iteration_overrides = iteration_overrides
        # 应用互动话术权重覆盖（为空时使用默认配置，不改变现有行为）
        self.engagement_template_weights = (
            dict(iteration_overrides.engagement_template_weights)
            if iteration_overrides and iteration_overrides.engagement_template_weights
            else {}
        )

    def _iteration_engagement_directive(self) -> str:
        """根据 iteration_overrides.engagement_template_weights 生成互动话术覆盖指令。

        仅当调整项非空时返回指令，否则返回空串（保持现有行为）。
        """
        weights = self.engagement_template_weights
        if not weights:
            return ""
        descriptions = {
            "stance": "立场鲜明型：先表态再给理由",
            "variable_query": "变量追问型：抛出待验证的关键变量",
            "counterfactual": "反事实假设型：假设另一种走向引导讨论",
            "experience": "经验征集型：邀请读者分享实操经验",
        }
        lines = ["[迭代调整-互动话术权重]"]
        for key, value in weights.items():
            desc = descriptions.get(str(key), str(key))
            try:
                weight = float(value)
            except (TypeError, ValueError):
                weight = 0.0
            lines.append(f"- {desc}（权重 {weight}）")
        lines.append("请按上述权重分布选择结尾互动话术类型，权重越高越优先采用。")
        return "\n".join(lines)

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
        revision_focus: list[str] | None = None,
    ) -> dict[str, object]:
        # 动态筛选 TOP 3 规则，用于门控校对
        active_rules = self.select_top_rules(
            industry=self.clean_publish_text(data_report.focus_industry).strip(),
            keywords=data_report.focus_keywords + data_report.supply_chain_keywords,
            content_format=data_report.content_format or "deep_analysis",
            limit=3,
        )
        rule_block = self.format_dynamic_rules_block(active_rules)

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
        if rule_block:
            prompt = prompt + "\n\n" + rule_block + "\n请在润色时严格执行以上规则。"
        engagement_directive = self._iteration_engagement_directive()
        if engagement_directive:
            prompt = prompt + "\n\n" + engagement_directive
        payload = self.request_json("你是 PolishAgent，只输出 JSON。", prompt)
        if payload:
            polished = str(payload.get("polished_article", "")).strip()
            if polished:
                original_chars = len(content.article.replace("\n", "").replace(" ", ""))
                article = self._finalize_article(polished, data_report, trend_report, revision_focus=revision_focus, original_char_count=original_chars)
                validation_notes, validation_score = self._validate_article(article, data_report, trend_report)

                # 规则契合度门控校对
                conformance_notes, conformance_score, needs_repolish = self._check_rule_conformance(
                    article, active_rules
                )
                model_notes = self._clean_list(payload.get("validation_notes"), 8)
                all_notes = self.unique(model_notes + validation_notes + conformance_notes)[:10]

                # 若契合度过低且需要二次润色，触发门控
                if needs_repolish and self.openai_service.available:
                    repolish_article = self._repolish_for_rules(article, active_rules, conformance_notes)
                    if repolish_article:
                        article = self._finalize_article(
                            repolish_article, data_report, trend_report,
                            revision_focus=revision_focus, original_char_count=original_chars,
                        )
                        # 二次校验
                        re_notes, re_score = self._validate_article(article, data_report, trend_report)
                        all_notes = self.unique(all_notes + re_notes)[:10]
                        validation_score = max(validation_score, re_score)
                        all_notes.append("已触发规则契合度二次润色，草稿已修正。")

                return {
                    "polished_article": article,
                    "validation_notes": all_notes,
                    "validation_score": max(
                        self._bound_score(payload.get("validation_score", 0)),
                        validation_score,
                        conformance_score,
                    ),
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

    def _check_rule_conformance(
        self,
        article: str,
        rules: list[dict],
    ) -> tuple[list[str], int, bool]:
        """规则契合度门控校对：检查草稿是否切实执行了注入的规则。

        返回：(契合度备注列表, 契合度分数 0-100, 是否需要二次润色)
        """
        if not rules or not article:
            return [], 80, False

        notes: list[str] = []
        total_penalty = 0.0
        max_penalty = 0.0
        failed_rules: list[dict] = []

        for rule in rules:
            penalty = float(rule.get("penalty_weight", 0.5))
            max_penalty += penalty
            category = rule.get("category", "")
            rule_id = rule.get("rule_id", "")
            title = rule.get("title", "")

            passed, issue = self._check_single_rule(article, rule)
            if passed:
                notes.append(f"[规则契合] {rule_id} {category}: 通过 - {title[:30]}")
            else:
                total_penalty += penalty
                failed_rules.append(rule)
                notes.append(f"[规则违背] {rule_id} {category}: {issue}")

        # 契合度分数 = 100 - (累计违背惩罚 / 最大惩罚) * 100
        if max_penalty > 0:
            conformance_score = max(0, int(100 - (total_penalty / max_penalty) * 100))
        else:
            conformance_score = 80

        # 若存在权重 >= 0.8 的规则被违背，或契合度低于 60，触发二次润色
        needs_repolish = any(
            float(r.get("penalty_weight", 0)) >= 0.8 for r in failed_rules
        ) or conformance_score < 60

        if needs_repolish:
            notes.append(f"[门控触发] 契合度 {conformance_score}/100，将进行规则二次润色。")

        return notes, conformance_score, needs_repolish

    def _check_single_rule(self, article: str, rule: dict) -> tuple[bool, str]:
        """检查单条规则的执行情况，返回 (是否通过, 问题描述)。"""
        category = rule.get("category", "")
        title = rule.get("title", "")
        paragraphs = [p.strip() for p in article.split("\n\n") if p.strip()]

        if category == "opening_hook":
            # 检查开头前两句是否有钩子（疑问句/反差数据/反直觉判断）
            if not paragraphs:
                return False, "文章为空，无法检查开头钩子。"
            first_para = paragraphs[0]
            sentences = re.split(r"[。！？!?]", first_para)
            first_two = "".join(sentences[:2])
            has_question = "？" in first_para or "?" in first_para
            has_number = bool(re.search(r"\d+(?:%|倍|亿|万|元)", first_para))
            has_contrast = any(k in first_para for k in ("但", "反而", "却", "不是", "而是", "然而"))
            if not (has_question or has_number or has_contrast):
                return False, "开头前两句缺少疑问句、反差数据或反直觉判断。"
            return True, ""

        if category == "title_attraction":
            # 标题规则在正文中较难直接检查，检查是否有冲突感表达
            if not paragraphs:
                return False, "文章为空。"
            return True, ""

        if category == "paragraph_density":
            # 检查是否存在过长段落（超过 165 字）
            long_paragraphs = [p for p in paragraphs if len(p.replace(" ", "")) > 165]
            if long_paragraphs:
                return False, f"存在 {len(long_paragraphs)} 个过长段落（超过165字），需拆短。"
            return True, ""

        if category == "data_anchor":
            # 检查是否包含具体数字
            numbers = re.findall(r"\d+(?:\.\d+)?(?:%|倍|亿元|亿|万元|万亿|GW|GWh|MW|万台|万套)", article)
            if len(numbers) < 1:
                return False, "正文缺少具体数字锚点。"
            return True, ""

        if category == "conflict_design":
            # 检查是否有冲突/争议表达
            conflict_tokens = ("但", "反而", "却", "不是", "而是", "然而", "风险", "前提是", "如果")
            if not any(token in article for token in conflict_tokens):
                return False, "正文缺少冲突感或风险边界讨论。"
            return True, ""

        if category == "closing_interaction":
            # 检查结尾是否有互动问题
            if not paragraphs:
                return False, "文章为空。"
            closing = paragraphs[-1]
            if "？" not in closing and "?" not in closing:
                return False, "结尾缺少开放式互动问题。"
            return True, ""

        if category == "anti_ai_flavor":
            # 检查是否包含 AI 套话
            ai_phrases = ("综上所述", "不可否认", "值得注意的是", "不难发现", "众所周知", "毋庸置疑")
            found = [p for p in ai_phrases if p in article]
            if found:
                return False, f"正文仍含AI套话: {'、'.join(found)}。"
            return True, ""

        # 默认通过
        return True, ""

    def _repolish_for_rules(
        self,
        article: str,
        rules: list[dict],
        conformance_notes: list[str],
    ) -> str:
        """规则契合度二次润色：针对违背的规则生成具体修正指令并调用 LLM。"""
        if not rules or not article:
            return ""

        # 构建二次润色指令
        failed_notes = [n for n in conformance_notes if n.startswith("[规则违背]")]
        if not failed_notes:
            return ""

        rule_instructions: list[str] = []
        for rule in rules:
            rule_id = rule.get("rule_id", "")
            title = rule.get("title", "")
            positive = rule.get("positive_example", "")
            negative = rule.get("negative_example", "")
            instruction = f"- {rule_id}: {title}"
            if positive:
                instruction += f"（参考正面示例: {positive}）"
            if negative:
                instruction += f"（避免负面示例: {negative}）"
            rule_instructions.append(instruction)

        system_prompt = (
            "你是 PolishAgent 的规则契合度校对器。"
            "你的任务是根据以下规则对文章进行二次润色，确保每条规则都被严格执行。"
            "只输出修正后的完整正文，不要输出 JSON 或解释。"
        )
        user_prompt = (
            "<evolution_rules>\n"
            "以下规则必须严格执行：\n"
            f"{chr(10).join(rule_instructions)}\n"
            "</evolution_rules>\n\n"
            "<conformance_issues>\n"
            f"{chr(10).join(failed_notes)}\n"
            "</conformance_issues>\n\n"
            "<source_document label=\"待润色文章\">\n"
            f"{self.sanitize_source_document(article)}\n"
            "</source_document>\n\n"
            "请根据规则和契合度问题，对文章进行最小化修正：\n"
            "1. 只修改违背规则的部分，保留已通过的内容。\n"
            "2. 不要改变文章的核心观点和数据。\n"
            "3. 不要增加素材中没有的新数据。\n"
            "4. 输出纯正文，不要包含 JSON、markdown 标题符号或解释。"
        )

        try:
            result = self.openai_service.create_json_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            # 尝试从 JSON 中提取，或直接使用返回文本
            if isinstance(result, dict):
                polished = str(result.get("polished_article") or result.get("article") or result.get("content") or "").strip()
            else:
                polished = str(result or "").strip()
            if polished and len(polished) > len(article) * 0.5:
                return polished
        except Exception as exc:
            self.logger.warning("规则契合度二次润色失败: %s", exc)

        return ""

    def _finalize_article(
        self,
        article: str,
        data_report: DataReport,
        trend_report: TrendReport,
        revision_focus: list[str] | None = None,
        original_char_count: int = 0,
    ) -> str:
        content_mode = self._content_mode(data_report)
        cleaned = self.strip_prompt_leakage(article)
        # Sanitization must be content-preserving; evidence-aware length
        # handling is performed by the workflow after polishing.
        cleaned = clean_article_text(cleaned, min_word_count=0)
        cleaned = self._remove_ai_cliches(cleaned)
        paragraphs = [self._normalize_paragraph(block) for block in cleaned.split("\n\n") if block.strip()]
        paragraphs = [block for block in paragraphs if block]

        # 后置过滤：剔除 LLM 在 polish 阶段可能模仿生成的 CTA / 营销段落
        # （MonetizationService.append_cta 会在导出阶段统一注入，polish 阶段不应出现）
        cta_markers = (
            "## 延伸服务",
            "## 延伸阅读",
            "## 进一步交流",
            "回复“",
            "回复\"",
            "如果你想把信息差变成结果",
            "做内容的目的不是多发几篇",
        )
        before_count = len(paragraphs)
        paragraphs = [p for p in paragraphs if not any(m in p for m in cta_markers)]
        removed = before_count - len(paragraphs)
        if removed > 0:
            self.logger.info("PolishAgent 后置过滤：剔除 %d 个 CTA/营销段落", removed)

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
        article_text = "\n\n".join(paragraphs[:12]).strip()
        unsupported_tokens = self._unsupported_numeric_tokens(article_text, data_report)
        if self._should_rebuild_cautious(article_text, data_report, unsupported_tokens):
            return self._rebuild_cautious_article(data_report, trend_report)
        if original_char_count > 0:
            polished_chars = len(article_text.replace("\n", "").replace(" ", ""))
            if polished_chars < original_char_count * 0.6:
                self.logger.warning(
                    "Polished article has only %d chars (original: %d, 60%% threshold: %d). "
                    "Polish was too aggressive, keeping original article.",
                    polished_chars, original_char_count, int(original_char_count * 0.6),
                )
                return article
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
                notes.append("正文仍缺少更具体的事实锚点，建议补进技术路线、市场份额、关键企业动态或争议背景。")
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
            notes.append("正文已经围绕产业走势、技术路线和后续变量展开。")
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
            return "你觉得这条消息最可能改变产业格局、技术路线还是舆论预期？"
        if content_mode == "international":
            return "你觉得接下来最值得盯的是谁会先让步、谁会继续施压，还是市场会先给出反应？"
        return f"您更看重{primary}后续先落地订单、交付，还是毛利率改善？"




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

    def _build_mode_fallback_anchor(self, data_report: DataReport, trend_report: TrendReport) -> str:
        mode = self._content_mode(data_report)
        if mode == "sports":
            return self._rebuild_cautious_sports_article(data_report, trend_report).split("\n\n")[0]
        if mode == "international":
            return self._rebuild_cautious_international_article(data_report, trend_report).split("\n\n")[0]
        return self._build_company_or_order_anchor(data_report)

    def _rebuild_cautious_sports_article(self, data_report: DataReport, trend_report: TrendReport) -> str:
        title = self.clean_publish_text(data_report.topic_title).strip() or "这条热点消息"
        source_anchor = self._build_source_anchor(data_report) or "从公开材料看，目前能确认的信息仍然有限。"
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=4)) or "技术路线变化、市场份额变化、关键企业动态和舆论争议"
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=3)) or "公开素材有限时，最需要防止的是把初步消息写成确定性结论"
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        paragraphs = [
            f"{title}不只是初步消息，它更像一个会改变后续判断的前置信号。{source_anchor}",
            f"真正值得看的，不是把新闻复述一遍，而是它会不会改变产业格局、技术路线和市场预期。",
            f"接下来更值得跟踪的，是{metrics}。这些变量会决定这条消息最终只是短期讨论，还是会真的影响后续走势。",
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
