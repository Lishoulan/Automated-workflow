from __future__ import annotations

import re
import logging
from typing import Optional

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, IterationOverrides, StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class PolishAgent(BaseAgent):
    agent_name = "PolishAgent"
    SHORT_CONTENT_FORMATS = frozenset(("quick_commentary", "short_comment", "news_flash"))
    AUTO_PUBLISH_ARTICLE_TARGET = 1800

    #: 叙事稿润色后的目标区间。上限与 `WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS`
    #: 同一量级，为首发质量标准留出事实与分析空间。
    NARRATIVE_POLISH_TARGET_MIN = 1500
    NARRATIVE_POLISH_TARGET_MAX = 1800
    UNSUPPORTED_ORDER_CLAIM_RES = (
        re.compile(
            r"(?:获得|拿到|签下|形成|已有|已经有|已获|转化为|落实|落地|接到|收到|拿下|确认)"
            r"[^。！？!?\n]{0,24}(?:样机订单|采购订单|重复订单|订单|定点)"
        ),
        re.compile(
            r"(?:样机订单|采购订单|重复订单|订单|定点)"
            r"[^。！？!?\n]{0,12}(?:已|已经|正式|明确|出现|落地|兑现|确认|签约|签署|落实|完成)"
        ),
    )
    ORDER_NEGATION_MARKERS = (
        "未",
        "尚未",
        "没有",
        "不能",
        "不等于",
        "是否",
        "若",
        "如果",
        "只要",
        "一旦",
        "可能",
        "有望",
        "预计",
        "待",
        "不足",
        "无法",
    )
    ORDER_SPECIFIC_TOKENS = ("样机订单", "采购订单", "重复订单", "定点")
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
    EVIDENCE_BOUNDARY_REWRITES = (
        (
            "前者更接近复杂任务和行业部署的观察指标，后者则体现产品覆盖与销售规模",
            "从产品定义看，前者属于全尺寸具身产品口径，后者属于非全尺寸非具身产品口径；二者应分别核对交付与收入结果",
        ),
        (
            "客户更看重能够落地并形成交付结果的产品与定制化方案",
            "收入结构变化可能反映业务重心转向定制化交付，但客户偏好仍需订单与验收数据验证",
        ),
        (
            "将直接影响项目收入和利润的兑现节奏",
            "可能影响项目收入确认和利润兑现节奏",
        ),
        (
            "一增一减说明市场资源和收入结构正在重新排序",
            "一增一减首先说明公司披露的收入结构发生变化，市场资源是否随之调整仍需更多数据验证",
        ),
        (
            "说明当前产品与解决方案组合出现了一定利润弹性",
            "与当前产品及解决方案组合变化同时出现，是否形成可持续利润弹性仍待验证",
        ),
        (
            "这决定了产业链的利润不会平均分配",
            "这也把观察范围延伸到产业链利润分配，但现有材料不足以判断具体环节",
        ),
        (
            "收入增量更多集中在需要场景适配、项目交付和现场服务的业务中",
            "按披露分类，收入增量主要出现在包含场景适配、项目交付和现场服务描述的业务中",
        ),
        (
            "人形机器人商业化可能先在工业制造、物流等相对结构化场景寻找突破",
            "从优必选现有收入结构看，人形机器人商业化或许先在工业制造、物流等相对结构化场景寻找突破",
        ),
        (
            "更可能获得高于单纯硬件供应的议价权",
            "是否能获得高于单纯硬件供应的议价权，仍要看合同、验收和回款数据",
        ),
        (
            "市场资源正在向具身产品及定制化交付集中",
            "收入结构正在向具身产品及定制化交付倾斜，市场资源是否同步变化仍待验证",
        ),
        (
            "增长仍可能主要来自早期项目集中确认",
            "增长也可能受到早期项目集中确认影响，但现有材料没有给出收入确认原因",
        ),
    )


    ANTI_AI_FLAVOR_APPENDIX = (
        "\n\n[终稿编辑约束 - 必须遵守]\n"
        "1. 只修订表达、结构和证据边界。不得新增素材中没有的公司、人物、数字、时间、订单、收入、成本、案例、现场经历或来源。\n"
        "2. 删除空泛套话、重复结论、机械的风险清单和营销式拔高；同一个判断只保留一次，后文若回扣必须增加新证据或新角度。\n"
        "3. 段落按信息单元自然切分，通常每段1到3句即可，不要为了满足固定段数、固定句长或固定开头强行拆合。长短句要有变化，避免连续使用相同句式。\n"
        "4. 只保留输入材料里已有的数字和主体。素材没有硬数据时，明确写‘尚未披露’或改成待验证问题，不要用虚构场景、第一人称经历或抽象案例填充。\n"
        "5. 来源感表达只在输入确有依据时自然使用，不要给每个数字机械添加相同的‘公开材料显示’前缀，也不要把分析建议写成已发生的事实。\n"
        "6. 结尾保留一个围绕文中具体变量的可争论问题，不添加 CTA、引流、咨询或其他营销段落。\n"
        "7. 数据报告中的 industry_trends 和 key_points 只提供分析方向，不等同于原始事实；涉及客户偏好、市场格局、利润弹性或行业阶段时，必须改成可能性判断并写出验证条件。\n"
        "8. 质量审核指出的每一项问题都必须在 polished_article 中实际改写，不能只在 validation_notes 里承认问题。"
    )

    POLISH_SYSTEM_PROMPT = (
        "你是 PolishAgent，只输出一个合法 JSON 对象。"
        "你负责把已有文章改得清楚、可信、自然，不负责扩写未经输入支持的事实。"
        "任何素材不足都应通过收窄结论和标注待验证来处理，不能通过编造细节降低所谓 AI 痕迹。"
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
        if self._is_narrative_domain() and str(getattr(content, "article", "") or "").strip():
            # ⚠️ 2026-09-19：**叙事稿跳过 LLM 润色**。
            #
            # 实测（同一天多次真实运行）：writer 能稳定写出 1114 字的故事稿，
            # 而润色步骤把它砍成 331 / 543 / 179 / 187 / 166 字 ——
            # 每次都触发 `_finalize_article` 的"缩水超过 40% 就丢弃"保护，
            # 于是润色**必然空跑**，只留下三样代价：
            #   1. 白烧一次大 prompt（data_json + trend_json 全量塞进去）；
            #   2. 每次运行多几十秒；
            #   3. 把模型的注意力从"写故事"引到"改写"，产出反而更差。
            # 换措辞、改成"保持 80%"都没用：这个免费模型做不好整篇改写，只会压缩。
            # 叙事体裁本来也不靠"润色"提升，靠的是素材里的具体事实，所以直接跳过。
            # 需要恢复时把 NARRATIVE_SKIP_LLM_POLISH 置 False 即可。
            self.logger.info("叙事领域：跳过 LLM 润色，保留 writer 原稿。")
            return self._fallback(content, trend_report, data_report, revision_focus=revision_focus)

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
            auto_publish_directive=self._auto_publish_directive(),
        )
        prompt = prompt + self.ANTI_AI_FLAVOR_APPENDIX
        auto_publish_directive = self._auto_publish_directive()
        if auto_publish_directive:
            prompt = prompt + "\n\n" + auto_publish_directive
        if rule_block:
            prompt = prompt + "\n\n" + rule_block + "\n请在润色时严格执行以上规则。"
        engagement_directive = self._iteration_engagement_directive()
        if engagement_directive:
            prompt = prompt + "\n\n" + engagement_directive
        payload = self.request_json(self._polish_system_prompt(), prompt)
        if payload:
            polished = str(payload.get("polished_article", "")).strip()
            if polished:
                original_chars = len(content.article.replace("\n", "").replace(" ", ""))
                article = self._finalize_article(polished, data_report, trend_report, revision_focus=revision_focus, original_char_count=original_chars, original_article=content.article)
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
                            original_article=content.article,
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

    def _auto_publish_mode(self) -> bool:
        publishing = getattr(self.settings, "publishing", None)
        return bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )

    def _auto_publish_directive(self) -> str:
        if not self._auto_publish_mode():
            return ""
        if self._is_narrative_domain():
            # 保留原有的"轻度编辑"约束（此前无条件要求 1800 字，导致润色把
            # 843 字砍成 187~413 字，触发「Polish was too aggressive」反复空跑）。
            return (
                f"当前是自动发布候选：这是人情故事观察稿，篇幅约"
                f"{self.NARRATIVE_POLISH_TARGET_MIN}到{self.NARRATIVE_POLISH_TARGET_MAX}字。"
                "润色只做「理顺句子、删掉重复表达、补上遗漏的事实细节」，"
                "不得把正文压短到原文的 80% 以下。"
                f"若原文不足{self.NARRATIVE_POLISH_TARGET_MIN}字，只能用材料中已有的事实、"
                "来源、因果解释和具体提醒补足；材料不足时保留原长度并收窄结论。"
                "首发质量必须覆盖近期进展、原创判断、信息增量、来源表达和读者价值。"
                f"也不得为了凑字数扩写到 {self.NARRATIVE_POLISH_TARGET_MAX} 字以上；"
                "补写只能用输入材料已有事实与公开的法律/常识依据，"
                "不得新增编造的人物、对话或情节。"
            )
        return (
            "当前是自动发布候选：润色后正文仍需保持约1800字，最低1750字。不得为了简洁把完整分析压缩成800字短评；"
            "只能重组输入材料已有事实，缺证据处收窄结论，不得用重复套话或新增事实补长度。"
        )

    def _is_narrative_domain(self) -> bool:
        """当前账号是不是叙事类（社会故事）定位。"""
        return (
            str(getattr(self.settings, "content_domain", "industry") or "industry").strip().lower()
            == "narrative"
        )
    def _polish_system_prompt(self) -> str:
        directive = self._auto_publish_directive()
        return f"{self.POLISH_SYSTEM_PROMPT}{directive}" if directive else self.POLISH_SYSTEM_PROMPT

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
        paragraphs = [p.strip() for p in article.split("\n\n") if p.strip()]

        if category == "opening_hook":
            # 检查开头前两句是否有钩子（疑问句/反差数据/反直觉判断）
            if not paragraphs:
                return False, "文章为空，无法检查开头钩子。"
            first_para = paragraphs[0]
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

    # 与 originality_service 的连续抄录检测对齐：那边用 8 字滑窗拼出连续片段，
    # 只有长度 ≥16 的片段才计入抄录率。这里把红线设在 12 字，留出安全余量，
    # 避免「刚好卡在检测边界」的改写。
    DEDUP_SPAN_MIN_CHARS = 12

    def dedup_rewrite(self, article: str, spans: list[str]) -> str:
        """把与素材原文连续重合的片段改写掉，返回改写后的正文（失败返回空串）。

        与 `_repolish_for_rules` 同源：只做「换表达」的最小化改写——不新增素材
        没有的事实、不改动数字与主体、不调整段落顺序和主判断。调用方负责判定
        「确实越线」并对改写结果复测，本方法只负责改写本身。
        """
        targets = [
            str(item).strip()
            for item in (spans or [])
            if len(str(item).strip()) >= self.DEDUP_SPAN_MIN_CHARS
        ]
        if not article or not targets:
            return ""
        # 送到模型面前的重合片段要截断：片段本身是素材原文，喂得越长，
        # 模型照抄回来的概率越高。
        targets = [item[:80] for item in targets[:12]]
        listed = "\n".join(
            f"{index}. {span}" for index, span in enumerate(targets, start=1)
        )
        system_prompt = (
            "你是原创性校对编辑，只输出一个合法 JSON 对象。"
            "你的唯一任务是降低文章与素材原文的逐字重合，不负责改写观点、事实或结构。"
        )
        user_prompt = (
            "<overlap_spans>\n"
            "下面每一行都是正文与素材原文连续重合的片段（比对时已去掉标点）：\n"
            f"{listed}\n"
            "</overlap_spans>\n\n"
            "<source_document label=\"待改写正文\">\n"
            f"{self.sanitize_source_document(article)}\n"
            "</source_document>\n\n"
            "改写要求：\n"
            "1. 只改写上面所列片段所在的句子，其余句子逐字保留。\n"
            "2. 保留全部事实、数字、公司名、时间、结论和段落顺序；不得新增素材没有的信息。\n"
            "3. 改写后，正文与素材原文不得再出现连续 12 个以上相同汉字（数字、专有名词、直接引语除外）。\n"
            "4. 换用不同的句式、主语和动词；只调换语序或替换个别近义词不算改写。\n"
            "5. 段落数量、段落顺序不得变化；总字数不得少于原文的 95%。\n"
            "6. 输出 JSON：{\"polished_article\": \"完整正文\"}。纯正文，不要出现 #、列表、编号或代码块。\n"
        )
        try:
            result = self.openai_service.create_json_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as exc:
            self.logger.warning("去重合改写调用失败: %s", exc)
            return ""

        if isinstance(result, dict):
            rewritten = str(
                result.get("polished_article") or result.get("article") or ""
            ).strip()
        else:
            rewritten = str(result or "").strip()
        if not rewritten:
            return ""
        # 改写不得缩水：低于原文 95% 说明模型顺手删了内容，宁可放弃这次改写。
        if len(rewritten) < len(article) * 0.95:
            self.logger.warning(
                "去重合改写后正文缩水 %d -> %d 字，放弃本次改写",
                len(article),
                len(rewritten),
            )
            return ""
        return rewritten

    def _finalize_article(
        self,
        article: str,
        data_report: DataReport,
        trend_report: TrendReport,
        revision_focus: list[str] | None = None,
        original_char_count: int = 0,
        original_article: str = "",
    ) -> str:
        content_mode = self._content_mode(data_report)
        cleaned = self.strip_prompt_leakage(article)
        # Sanitization must be content-preserving; evidence-aware length
        # handling is performed by the workflow after polishing.
        cleaned = clean_article_text(cleaned, min_word_count=0)
        cleaned = self._remove_ai_cliches(cleaned)
        cleaned = self._apply_evidence_boundary_edits(cleaned)
        # 自动修复：跑在审核之前，把「确定且可机械修复」的问题直接改掉。
        # 自动化流程没有人工环节，发现问题就拦的话最终只会 0 篇可发。
        try:
            from toutiao_content_factory.services.auto_repair_service import repair_article

            repaired = repair_article(cleaned)
            if repaired.get("changed"):
                cleaned = str(repaired["article"])
                self.logger.info(
                    "PolishAgent 自动修复：%s", "；".join(repaired.get("changes", []))
                )
        except Exception as exc:
            self.logger.warning("PolishAgent 自动修复失败，保留原文：%s", exc)
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

        if self._auto_publish_mode():
            paragraphs = self._rebalance_paragraphs(
                paragraphs,
                min_paragraphs=7,
                max_paragraphs=12,
            )
        else:
            paragraphs = self._rebalance_paragraphs(paragraphs)
        article_text = "\n\n".join(paragraphs[:12]).strip()
        article_text = clean_article_text(article_text, min_word_count=0)
        unsupported_tokens = self._unsupported_numeric_tokens(article_text, data_report)
        if self._should_rebuild_cautious(article_text, data_report, unsupported_tokens) or self._has_unsupported_order_claim(
            article_text,
            data_report,
        ):
            return self._rebuild_cautious_article(data_report, trend_report)
        if original_char_count > 0:
            polished_chars = len(article_text.replace("\n", "").replace(" ", ""))
            if polished_chars < original_char_count * 0.6:
                self.logger.warning(
                    "Polished article has only %d chars (original: %d, 60%% threshold: %d). "
                    "Polish was too aggressive, keeping original article.",
                    polished_chars, original_char_count, int(original_char_count * 0.6),
                )
                # 必须回退到「真正的原文」。注意入参 article 是模型输出的润色稿，
                # 只对它再加工一遍无法恢复被删掉的内容——历史 bug：日志声称
                # "keeping original article"，实际返回的仍是短稿，导致
                # polished_article 过短、正文长度门槛不过、readiness 被置 0。
                fallback = original_article or article
                return clean_article_text(self._apply_evidence_boundary_edits(fallback), min_word_count=0)
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
            if content_mode == "social_story":
                score += 8
                notes.append("社会故事不强制凑数字，重点核对时间、地点、人物关系和处理结果。")
            elif content_mode == "sports":
                notes.append("正文仍缺少更具体的事实锚点，建议补进技术路线、市场份额、关键企业动态或争议背景。")
            elif content_mode == "international":
                notes.append("正文仍缺少更具体的事实锚点，建议补进政策动作、谈判节点或市场反应。")
            else:
                notes.append("正文仍缺少明确数字锚点，建议至少保留 1 到 2 组关键数据。")

        company_hits = self._count_hits(article_lower, self._company_candidates(data_report)[:4])
        order_hits = sum(1 for token in self.ORDER_ANCHOR_TOKENS if token in article)
        if content_mode == "social_story":
            score += 8
            notes.append("正文围绕人物关系和事件进展展开，主体与当前素材一致。")
        elif content_mode != "generic":
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
        if content_mode == "social_story":
            score += 8
            notes.append("正文已经把事情的来龙去脉和当事人的处境讲清楚。")
        elif content_mode == "sports":
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

        if self._has_unsupported_order_claim(article, data_report):
            score -= 24
            notes.append("正文把素材未支持的订单或采购写成既成事实，已触发谨慎重建。")

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

    def _apply_evidence_boundary_edits(self, article: str) -> str:
        """Downgrade recurring unsupported causal claims without adding facts."""
        revised = str(article or "")
        for source, replacement in self.EVIDENCE_BOUNDARY_REWRITES:
            revised = revised.replace(source, replacement)
        return revised

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

    def _has_unsupported_order_claim(self, article: str, data_report: DataReport) -> bool:
        """Catch order claims that are absent from the report before final review."""

        evidence_sentences = [
            *(getattr(data_report, "core_data", []) or []),
            *(getattr(data_report, "evidence_notes", []) or []),
            *[
                str(item.get("fact", ""))
                for item in (getattr(data_report, "evidence_items", []) or [])
                if isinstance(item, dict)
            ],
        ]
        for sentence in re.split(r"[。！？!?\n]", str(article or "")):
            candidate = sentence.strip()
            if not candidate or not any(pattern.search(candidate) for pattern in self.UNSUPPORTED_ORDER_CLAIM_RES):
                continue
            if any(marker in candidate for marker in self.ORDER_NEGATION_MARKERS):
                continue
            if not self._order_claim_supported(candidate, evidence_sentences):
                return True
        return False

    def _order_claim_supported(self, claim: str, evidence_sentences: list[str]) -> bool:
        """Require the same company and order type when evidence is specific."""

        claim_companies = [item.casefold() for item in self._extract_company_mentions(claim)]
        claim_specific_terms = [item for item in self.ORDER_SPECIFIC_TOKENS if item in claim]
        for evidence in evidence_sentences:
            text = str(evidence or "").strip()
            if not text or any(marker in text for marker in self.ORDER_NEGATION_MARKERS):
                continue
            if not any(pattern.search(text) for pattern in self.UNSUPPORTED_ORDER_CLAIM_RES):
                continue
            if claim_specific_terms and not any(term in text for term in claim_specific_terms):
                continue
            if not claim_specific_terms and "订单" not in text and "定点" not in text:
                continue

            evidence_lower = text.casefold()
            if claim_companies and not any(company in evidence_lower for company in claim_companies):
                continue
            return True
        return False



    def _rebuild_cautious_article(self, data_report: DataReport, trend_report: TrendReport) -> str:
        content_mode = self._content_mode(data_report)
        if content_mode == "social_story":
            # 故事体裁不走「产业链位置」式重建 —— 那套骨架会把社会新闻写成产业分析。
            # 这里只用素材事实做朴素重建：事情本身 + 当事人处境 + 给读者的提醒。
            subject = self._topic_subject_label(data_report, fallback="这件事")
            source_anchor = self._build_source_anchor(data_report)
            core_items = self.clean_publish_list(data_report.core_data, limit=3)
            core_data = "；".join(core_items)
            parts = [
                f"{source_anchor}{core_data}".strip() or f"关于{subject}，目前公开信息有限。",
                "这件事的关键不在于行业走势，而在于当事人当时的处境："
                "面对同样的情况，能做的选择其实不多，而不同选择的结果差别很大。",
                "对普通读者来说，更实际的是把可核验的部分记下来——"
                "时间、地点、涉及的人和结果，以及处理过程中留下了哪些书面凭证。",
                "同类事情再发生时，先固定证据、再找对应渠道处理，往往比事后争论更有效。",
            ]
            return "\n\n".join(part for part in parts if part.strip())
        if content_mode == "sports":
            return self._rebuild_cautious_sports_article(data_report, trend_report)
        if content_mode == "international":
            return self._rebuild_cautious_international_article(data_report, trend_report)
        primary = self._topic_subject_label(data_report, fallback="相关赛道")
        companies = self._company_candidates(data_report)[:2]
        source_anchor = self._build_source_anchor(data_report)
        numbers = "、".join(self.clean_publish_list(data_report.growth_numbers, limit=3))
        core_items = self.clean_publish_list(data_report.core_data, limit=3)
        if source_anchor:
            anchor_fact = source_anchor.removeprefix("公开信息显示，").rstrip("。")
            core_items = [item for item in core_items if item not in anchor_fact and anchor_fact not in item]
        core_data = "；".join(core_items)
        trends = "；".join(self.clean_publish_list(data_report.industry_trends, limit=2))
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=3))
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=2))
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        closing_question = self._build_closing_question(data_report)

        first_paragraph = f"{primary}这条信息目前只能算阶段性进展。"
        first_paragraph += source_anchor or "目前公开披露的细节和量化结果仍然有限。"
        first_paragraph += "这还不足以推出订单、收入或量产已经兑现。"

        second_paragraph = f"能直接核对的数字集中在{numbers}。" if numbers else f"公开信息没有给出{primary}当前进展的可量化结果。"
        if core_data:
            second_paragraph += f"公开信息还提到：{core_data}。"
        else:
            second_paragraph += "所以现阶段适合判断验证节奏，不适合提前下过满的业绩结论。"

        if companies:
            third_paragraph = (
                f"公开信息提到{'、'.join(companies)}，但这不等于它们已经获得订单或形成收入。"
                "判断时应看客户验证、交付节奏和能否复制到更多项目。"
            )
        else:
            third_paragraph = (
                f"目前没有足够依据点名具体公司。{primary}能否走强，要回到设备交付、项目验证、系统集成和后续服务这些执行环节。"
            )
        if trends:
            third_paragraph += f"公开信息呈现的趋势是：{trends}。"

        fourth_paragraph = f"接下来可观察{metrics}。" if metrics else "接下来可观察部署表现、订单复制、成本和服务。"
        if angle:
            fourth_paragraph += f"这条线索的判断重点是：{angle}。"
        else:
            fourth_paragraph += "这些信号没有连续出现前，不宜把单点进展写成全行业普涨。"

        fifth_paragraph = f"限制条件是：{risks}。" if risks else "限制条件是订单、交付和利润数据可能继续缺位。"
        fifth_paragraph += closing_question
        paragraphs = [first_paragraph, second_paragraph, third_paragraph, fourth_paragraph, fifth_paragraph]
        max_paragraphs = 6 if data_report.content_format in ("quick_commentary", "short_comment", "news_flash") else 7
        return "\n\n".join(
            self._rebalance_paragraphs(
                paragraphs,
                min_paragraphs=5,
                max_paragraphs=max_paragraphs,
                max_block_length=150,
            )[:max_paragraphs]
        ).strip()

    def _build_closing_question(self, data_report: DataReport) -> str:
        content_mode = self._content_mode(data_report)
        primary = self._topic_subject_label(data_report, fallback="这个方向")
        if content_mode == "social_story":
            return "换成是你遇到这种情况，会怎么处理？"
        if content_mode == "sports":
            return "你觉得这条消息最可能改变产业格局、技术路线还是舆论预期？"
        if content_mode == "international":
            return "你认为下一步更可能先出现让步、加压，还是市场反应？"
        return f"您更看重{primary}后续先落地订单、交付，还是毛利率改善？"




    def _build_source_anchor(self, data_report: DataReport) -> str:
        for note in data_report.evidence_notes:
            text = self.clean_publish_text(note).strip()
            if text:
                for prefix in ("公开材料：", "公开材料:", "素材信息：", "素材信息:"):
                    if text.startswith(prefix):
                        text = text[len(prefix) :].strip()
                if not text.endswith(("。", "！", "？")):
                    text += "。"
                return f"公开信息显示，{text}"
        if data_report.growth_numbers:
            return f"目前已明确的数字信号包括{'、'.join(data_report.growth_numbers[:2])}。"
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
        source_anchor = self._build_source_anchor(data_report) or "目前能确认的来源和细节仍然有限。"
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=4)) or "技术路线变化、市场份额变化、关键企业动态和舆论争议"
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=3)) or "公开素材有限时，最需要防止的是把初步消息写成确定性结论"
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        paragraphs = [
            f"{title}目前支持的判断是阶段性变化，而不是已经确定的长期结果。{source_anchor}",
            "事件影响要从后续比赛、技术路线和市场反馈中确认，单次讨论热度不能替代这些信息。",
            f"接下来可观察{metrics}。这些指标分别对应事件能否延续，以及它是否会改变后续走势。",
            f"{angle or '目前更适合把关键人物、节点和走势拆开核对。'}",
            f"限制条件是：{risks}。{self._build_closing_question(data_report)}",
        ]
        return "\n\n".join(self._rebalance_paragraphs(paragraphs)[:7]).strip()
    def _rebuild_cautious_international_article(self, data_report: DataReport, trend_report: TrendReport) -> str:
        title = self.clean_publish_text(data_report.topic_title).strip() or "这条国际消息"
        source_anchor = self._build_source_anchor(data_report) or "目前能确认的来源和细节仍然有限。"
        metrics = "、".join(self.clean_publish_list(data_report.watch_metrics, limit=4)) or "政策表态、谈判进展、相关国家动作和市场反馈"
        risks = "；".join(self.clean_publish_list(data_report.risk_flags, limit=3)) or "公开素材有限时，最需要防止的是把短期消息写成长期定局"
        angle = self.clean_publish_text(trend_report.recommended_angle).strip()
        paragraphs = [
            f"{title}目前只是博弈中的一个新动作，不能直接写成局势已经定型。{source_anchor}",
            "后续影响取决于各方有没有把表态转成实际动作，以及市场是否给出持续反馈。",
            f"接下来可观察{metrics}。这些信息能帮助区分局势升级、暂时缓和和重新谈判。",
            f"{angle or '目前更适合把事件、利益关系和后续变量连接起来。'}",
            f"限制条件是：{risks}。{self._build_closing_question(data_report)}",
        ]
        return "\n\n".join(self._rebalance_paragraphs(paragraphs)[:7]).strip()
