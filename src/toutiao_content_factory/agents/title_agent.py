from __future__ import annotations

import json
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, IterationOverrides, StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.concrete_anchors import (
    GATE_MIN_ANCHORS,
    STATUS_FAIL,
    STATUS_UNAVAILABLE,
    ConcreteAnchor,
    concrete_hook_status,
    extract_concrete_anchors,
    quantity_token,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.utils.title_utils import fit_title_to_limit


#: 标题来源的三种池（D1）。三者**互不混池排名**，按优先级递补：
#: PRIMARY=model → RESCUE=fallback → LAST_RESORT=emergency。
TITLE_SOURCE_MODEL = "model"
TITLE_SOURCE_FALLBACK = "fallback"
TITLE_SOURCE_EMERGENCY = "emergency"

#: 一次标题校验的汇总状态（D2，对外字段名见 `TitleAgent.run()` 的返回值）。
TITLE_STATUS_PASSED = "passed"           # 有合格候选，且没有候选被拒
TITLE_STATUS_PARTIAL = "partial"         # 有合格候选，同时也有被拒的
TITLE_STATUS_ALL_REJECTED = "all_rejected"   # 一个都没过 —— **不是**「放行原文」，是真失败

#: 正文不可用时（`_title_body_reference()` 拿不到任何文本）记录的 reason code。
REASON_VALIDATION_CONTEXT_UNAVAILABLE = "validation_context_unavailable"


@dataclass(frozen=True)
class TitleValidation:
    """一条标题的**硬校验**结果（model / fallback / emergency 共用同一入口）。"""

    title: str
    passed: bool
    reason_codes: tuple[str, ...] = ()
    concrete_hook_status: str = STATUS_UNAVAILABLE
    validation_context: str = "body"       # "body" | "unavailable"
    rejected_by: str = ""                  # 失败时：哪一层判的（title_gate / rule_gate）

    @property
    def body_aware(self) -> bool:
        return self.validation_context == "body"


@dataclass
class TitlePool:
    """一个来源池的生成/校验结果。`valid_titles` 是**已排序**的合格候选。"""

    source: str
    generated: list[str] = field(default_factory=list)
    valid_titles: list[str] = field(default_factory=list)
    rejected: list[TitleValidation] = field(default_factory=list)
    unavailable: list[TitleValidation] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.valid_titles:
            return TITLE_STATUS_PASSED if not self.rejected else TITLE_STATUS_PARTIAL
        return TITLE_STATUS_ALL_REJECTED

    @property
    def generated_count(self) -> int:
        return len(self.generated)

    @property
    def passed_count(self) -> int:
        return len(self.valid_titles)

    @property
    def reject_count(self) -> int:
        return len(self.rejected)

    @property
    def unavailable_count(self) -> int:
        return len(self.unavailable)

    def reason_summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for item in self.rejected:
            for code in item.reason_codes or ("unspecified",):
                summary[code] = summary.get(code, 0) + 1
        for item in self.unavailable:
            code = REASON_VALIDATION_CONTEXT_UNAVAILABLE
            summary[code] = summary.get(code, 0) + 1
        return dict(sorted(summary.items(), key=lambda kv: -kv[1]))

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "generated_count": self.generated_count,
            # 字段名与 `review_service.select_auto_title()` 的返回保持一致
            # （`passed_count` / `reject_count` / `unavailable_count`），
            # 避免同一件事在两层叫两个名字。
            "passed_count": self.passed_count,
            "reject_count": self.reject_count,
            "unavailable_count": self.unavailable_count,
            "reason_summary": self.reason_summary(),
            "top_reasons": sorted(
                self.reason_summary().items(), key=lambda kv: -kv[1]
            )[:5],
        }


class TitleAgent(BaseAgent):
    agent_name = "TitleAgent"

    LOW_QUALITY_TOKENS = ("炸裂", "封神", "赢麻", "疯了", "吓人", "彻底变天", "全网都在看")
    PLACEHOLDER_TOKENS = ("这家公司", "某公司", "该公司", "这条赛道")
    CONTRAST_TOKENS = ("不是", "而是", "背后", "真正", "为什么", "谁在", "谁能", "谁会")
    PROFIT_TOKENS = ("利润", "毛利", "盈利", "兑现", "估值", "份额", "红利", "毛利率")
    VALIDATION_TOKENS = ("订单", "交付", "验证", "客户", "量产", "出货", "签约", "开工")
    DEPTH_TOKENS = ("产业链", "卡位", "环节", "壁垒", "拐点", "分化", "重估", "节奏")
    TEMPLATEY_TOKENS = (
        "看懂",
        "这条线",
        "又火了",
        "别忽视",
        "切入",
        "机会来了",
        "开始进入兑现筛选期",
        "开始进入筛选期",
        "全面起飞",
        "更该看订单还是毛利",
        "真正先兑现的是谁",
        "先看客户验证和交付",
        "开始分化，先吃到利润的是哪一环",
    )
    WEAK_SUFFIXES = ("现在走到哪一步了", "会怎么走", "还能走多远", "怎么看", "是什么信号")
    UNSUPPORTED_FINANCIAL_LINK_PATTERNS = (
        re.compile(
            r"(?:毛利率?|盈利|利润)[^，。；：:]{0,12}"
            r"(?:撑住|支撑|决定|证明)[^，。；：:]{0,12}"
            r"(?:批量|量产|订单|交付)"
        ),
        re.compile(
            r"(?:收入|增速|毛利率?)[^，。；：:]{0,12}"
            r"(?:意味着|说明|证明)[^，。；：:]{0,12}"
            r"(?:成熟|规模化|量产)"
        ),
    )

    DISCOURAGED_TITLE_PATTERNS = (
        "竟然", "万万没想到", "背后原因", "原来是因为", "天理难容",
        "大变局", "出事了", "震惊", "背后真相", "令人震惊",
        "不敢相信", "看哭了", "太可怕了", "细思极恐",
        # 已验证零点击的同质骨架（历史重复发布主因），从标题生成源头硬拦截
        "重排利润", "先流向谁", "改写座次", "利润先流向",
    )

    #: **内部流程指令**泄漏到标题里的特征词。
    #:
    #: 实测来源（`.runtime/sample_phase1.json`，随机 30 条最终选中标题里命中 3 条）：
    #: 选题元数据的「体裁要求：适合写成人形机器人项目或订单拆解稿…」有一截
    #: （`适合写成人形机器人`）被当成关键词收进了 `focus_keywords`，于是
    #: `_primary_topic_label()` 把它当作赛道主体，拼出了
    #: **「适合写成人形机器人入场，AI谁最紧张」** —— 一条把生产指令当标题的候选，
    #: 而且它能通过 RULE_006（含「人形机器人」）与 RULE_009（含「入场」动作词）。
    #: 模型侧同样会照抄（模板被塞进 prompt），所以这是**所有来源共有**的泄漏。
    #: 与 `RULE_007` 不同，这不是「模板用太多」，而是标题里出现了不该给读者看的词。
    INSTRUCTION_LEAK_TOKENS = (
        "适合写成", "重点写", "拆解稿", "体裁要求", "固定写法", "写作要求",
        "段落要求", "跟踪变量", "入选理由", "推荐主结论", "请围绕", "请重点分析",
        "证据强度", "选题质量分", "标题潜力分", "标题打法赛道", "标题抓手",
        "质量标记", "建议字数", "素材摘要", "补充片段", "对标账号", "本稿",
    )

    CURIOSITY_TOKENS = ("不是", "而是", "背后", "真正", "为什么", "谁在", "谁能", "谁会", "到底", "竟然", "难道", "居然", "反常", "意外", "颠覆", "反转", "没想到", "想不到", "秘密", "真相")
    RATIONAL_TOKENS = ("利润", "毛利", "盈利", "兑现", "估值", "份额", "红利", "毛利率", "订单", "交付", "验证", "客户", "量产", "出货", "签约", "开工", "数据", "增长", "下滑", "同比", "环比", "占比", "规模", "增速", "收入", "营收", "成本", "拐点", "分化", "重估")

    MODE_NOISE_TOKENS = ("产业链", "订单", "交付", "毛利", "利润", "兑现", "科技制造", "融资")

    # 高频同质化模式词组：当标题同时包含同一组内的多个词时，说明结构雷同
    HOMOGENEOUS_PATTERNS = (
        ("利润", "兑现"),
        ("利润", "分化"),
        ("利润", "交付"),
        ("利润", "零部件"),
        ("兑现", "交付"),
        ("兑现", "零部件"),
        ("兑现", "量产"),
        ("分化", "零部件"),
        ("分化", "交付"),
        ("利润", "集中"),
        ("利润", "迁移"),
        ("利润", "转移"),
    )

    # 量化单位表已搬到 `services/concrete_anchors.QUANTITY_UNITS`（唯一真相源）。
    # 2026-09-17（Blocker 1）：这里曾经有一份 `NUMERIC_UNITS`，与提取逻辑各说各话，
    # 缺 `个月` 与量纲组合，于是 `18个月`→`18个`、`460万台`→`460万`。
    # 不要再在这里重建第二份单位表。

    # 语义骨架提取用的替换词（标题去重时忽略这些词的具体差异）
    SKELETON_FILLERS = (
        "人形机器人", "机器人", "AI芯片", "芯片", "半导体", "储能", "光伏",
        "新能源", "零部件", "整机", "执行器", "减速器", "传感器", "大脑",
        "核心件", "产业链", "环节",
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
        # 2026-09-15：标题环节可选使用独立的强模型（.env 的 OPENAI_TITLE_MODEL）。
        # 实测依据：glm-4.6 生成的标题能通过 RULE_006/007/009 全部校验，而 glm-4-flash
        # 生成的标题会被全部过滤（4~6 次/轮）；但 4.6 写 1750+ 字长文会反复超时。
        # 故只让「标题」这一短输出环节走强模型，正文写作保持快模型。
        _title_model = os.getenv("OPENAI_TITLE_MODEL", "").strip()
        if _title_model and _title_model != openai_service.model:
            self.openai_service = OpenAIContentService(
                settings,
                logger,
                force_mock=openai_service.force_mock,
                model_override=_title_model,
            )
            logger.info(
                "TitleAgent 使用独立模型: %s（正文仍用 %s）",
                _title_model,
                openai_service.model,
            )
        self.iteration_overrides = iteration_overrides
        # 应用标题模板权重覆盖（为空时使用默认配置，不改变现有行为）
        self.title_template_weights = (
            dict(iteration_overrides.title_template_weights)
            if iteration_overrides and iteration_overrides.title_template_weights
            else {}
        )

    def _gate_concrete_hook(self, title: str, body: str, body_available: bool) -> tuple[list[str], str]:
        """具象锚点三态：FAIL 才拒绝；UNAVAILABLE 不判标题有罪，但必须留痕。"""
        max_length = self._title_max_length()
        if not (body_available and 10 <= len(title) <= max_length):
            return [], STATUS_UNAVAILABLE
        hook_status, _anchor = self.concrete_hook_status(title, body)
        if hook_status == STATUS_FAIL:
            return ["missing_concrete_hook"], hook_status
        if hook_status == STATUS_UNAVAILABLE:
            self._note_concrete_hook_unavailable(body)
        return [], hook_status

    def _title_gate_reasons(self, title: str, body: str) -> tuple[list[str], str]:
        """**标题闸门**（与正文无关的那部分 + 需要正文的那部分），返回 reason codes。

        这是 `validate_title_compliance()` 与 `validate_title_candidate()` 共用的底层，
        保证两个入口不会各自漂移出一份判定。
        """
        body_available = bool(body.strip())
        reason_codes: list[str] = []
        max_length = self._title_max_length()
        if len(title) > max_length:
            reason_codes.append("title_too_long")
        for pattern in self.DISCOURAGED_TITLE_PATTERNS:
            if pattern in title:
                reason_codes.append("discouraged_pattern")
                break
        if any(pattern.search(title) for pattern in self.UNSUPPORTED_FINANCIAL_LINK_PATTERNS):
            reason_codes.append("unsupported_financial_link")
        # 内部流程指令泄漏（「适合写成人形机器人入场，AI谁最紧张」）。
        for token in self.INSTRUCTION_LEAK_TOKENS:
            if token in title:
                reason_codes.append("internal_instruction_leak")
                break

        # ⚠️ 2026-09-17（Blocker 2）：标题层只问「标题与正文是否一致」，
        # 不碰 `check_editorial_integrity()`（它把正文自身的问题也合并进来了）。
        consistency = ArticleQualityChecker().check_title_body_consistency(title, body)
        if consistency.issues:
            reason_codes.append("title_body_inconsistency")

        hook_reasons, hook_status = self._gate_concrete_hook(title, body, body_available)
        reason_codes.extend(hook_reasons)
        return reason_codes, hook_status

    def validate_title_candidate(self, title: str, body_text: str = "") -> TitleValidation:
        """**唯一**的标题硬校验入口（D3）——三池（model / fallback / emergency）共用。

        此前 `_fallback_fact_titles()` 调的是不带正文的
        `validate_title_compliance(title)`，于是兜底标题**跳过了**「标题与正文一致性」
        与「具象锚点承接」两道检查 —— 同一个标题走模型路径会被拒、走兜底路径能活。
        那是校验不对称，不是风格差异。

        一次校验同时跑两层，两层都必须过：

        1. **标题闸门**（本类）：长度 / 禁用句式 / 财务因果越界 / 标题-正文一致性 /
           具象锚点三态。
        2. **规则闸门**（`ArticleQualityChecker._check_title`）：RULE_006 具象化、
           RULE_007 模板滥用、RULE_009 信息密度、标题主体是否在正文出现。

        第 2 层原本只在 `agent_workflow._filter_titles_by_quality()` 里**事后**跑，
        于是 `TitleAgent` 报出的「合格候选」不等于「能发出去的候选」，而 D1 的分池
        判断（「模型池有没有合格标题」）正建立在这个集合上。合并到这里之后，
        `TitlePool.valid_titles` 才真正等于可发布集合。
        """
        body = str(body_text or "")
        body_available = bool(body.strip())
        validation_context = "body" if body_available else "unavailable"

        reason_codes, hook_status = self._title_gate_reasons(title, body)
        rule_issues = self._rule_gate_issues(title, body)
        reason_codes.extend(rule_issues)

        # ⚠️ 2026-09-19：叙事体裁的标题闸门必须**收窄到安全类检查**。
        #
        # 实测（云端 run 35443949496）：模型产出 18 条标题，全部被拒，
        # 最终走 emergency 池、2 条候选也全拒（missing_concrete_hook + rule_009），
        # 于是 `select_auto_title` 返回空标题 → `qualifies_for_auto_approval=False`
        # → 整篇判 `review_required`、**没有发布**。
        #
        # 被拒的两个理由是给"产业分析稿"设计的：
        #   - `missing_concrete_hook`：要求标题承接正文里的**高置信度锚点**
        #     （公司名 0.9 / 带量级的数字 0.8）。社会故事里没有公司，
        #     数字是「20年」「4次」这类弱锚点（0.45）→ 正文高置信度锚点不足 →
        #     闸门自己都判定 UNAVAILABLE（日志原文：「无法要求标题承接具象信息」），
        #     却仍按 FAIL 拒标题。
        #   - `rule_009_low_information`：按"数据/公司/产业链"密度衡量信息量，
        #     故事标题天然不满足。
        #
        # 叙事体裁下保留的仍是**安全类**检查：长度、禁用句式、财务因果越界、
        # 内部指令泄漏、标题-正文一致性。放开的只是那两个"产业信息密度"判据。
        if self._is_narrative_mode():
            hook_status = STATUS_UNAVAILABLE
            reason_codes = [
                code
                for code in reason_codes
                if code not in self.NARRATIVE_EXEMPT_TITLE_CODES
            ]

        if not body_available:
            # 不偷偷放行：正文缺失意味着整条 body-aware 闸门这次**无法生效**，
            # 必须留下一个可统计的标记（下游据此知道这条标题只是「过了无正文版校验」）。
            reason_codes.append(REASON_VALIDATION_CONTEXT_UNAVAILABLE)
            self._note_validation_context_unavailable()

        deduped = tuple(dict.fromkeys(reason_codes))
        rejected_by = ""
        if deduped:
            rejected_by = "rule_gate" if rule_issues else "title_gate"
        return TitleValidation(
            title=title,
            passed=not deduped,
            reason_codes=deduped,
            concrete_hook_status=hook_status,
            validation_context=validation_context,
            rejected_by=rejected_by,
        )

    #: 叙事体裁下**不再作为拒绝理由**的码。它们衡量的是产业分析稿的信息密度，
    #: 对社会故事标题不适用；安全类检查（长度/句式/越界/泄漏/一致性）一条都不放。
    NARRATIVE_EXEMPT_TITLE_CODES = frozenset(
        {
            "missing_concrete_hook",
            "rule_009_low_information",
            "rule_006_missing_concrete_subject",
        }
    )

    def _is_narrative_mode(self) -> bool:
        """当前账号是否叙事体裁（社会故事）。"""
        return (
            str(getattr(self.settings, "content_domain", "industry") or "industry").strip().lower()
            == "narrative"
        )

    def validate_title_compliance(self, title: str, body_text: str = "") -> bool:
        """**标题闸门**的布尔入口（保持原有语义，供既有调用方与回归测试使用）。

        注意它**不含** RULE_006/007/009 —— 那部分由发布前的
        `agent_workflow._filter_titles_by_quality()` 与
        `review_service.select_auto_title()` 施加。需要「一次问清能不能发」时请用
        `validate_title_candidate()`。
        """
        reason_codes, _hook = self._title_gate_reasons(title, str(body_text or ""))
        return not reason_codes

    def _rule_gate_issues(self, title: str, body_text: str) -> list[str]:
        """RULE_006/007/009 那条闸门的 reason codes（与标题闸门同一入口内执行）。"""
        try:
            result = ArticleQualityChecker()._check_title(title, body_text)
        except Exception as exc:  # pragma: no cover - 规则闸门不应把标题链路带崩
            self.logger.warning("标题规则闸门执行失败，本次按未通过记录: %s", exc)
            return [f"rule_gate_error:{type(exc).__name__}"]
        codes: list[str] = []
        for issue in result.get("issues", []):
            text = str(issue)
            if not text.strip():
                continue
            if "RULE_007" in text:
                codes.append("rule_007_template_abuse")
            elif "RULE_006" in text:
                codes.append("rule_006_abstract_subject")
            elif "RULE_009" in text:
                codes.append("rule_009_low_information")
            elif "标题过长" in text or "标题过短" in text:
                codes.append("rule_length")
            elif "标题党" in text:
                codes.append("rule_clickbait")
            elif "未出现" in text:
                codes.append("rule_subject_not_in_body")
            elif "财务指标" in text:
                codes.append("rule_financial_link")
            else:
                codes.append("rule_other")
        return list(dict.fromkeys(codes))

    def _note_validation_context_unavailable(self) -> None:
        """正文不可用时留一条 WARNING（每个 run 一次）。"""
        if getattr(self, "_validation_context_unavailable_logged", False):
            return
        self._validation_context_unavailable_logged = True
        self.logger.warning(
            "标题校验缺少正文：本次所有候选只通过「无正文版」校验，"
            "标题-正文一致性与具象锚点闸门**无法生效**（已标记 %s）。",
            REASON_VALIDATION_CONTEXT_UNAVAILABLE,
        )

    def _note_concrete_hook_unavailable(self, body_text: str) -> None:
        """正文锚点不足时记一条 WARNING（每个 run 只报一次）。

        这是「记录而不是绕过」：旧实现直接 `return True`，我们既不知道闸门没生效，
        也不知道根因在正文。这条日志把根因指向**正文**，供后续换选题 / 重写正文。
        """
        if getattr(self, "_concrete_hook_unavailable_logged", False):
            return
        self._concrete_hook_unavailable_logged = True
        anchors = self.extract_concrete_anchors(body_text)
        credible = [a for a in anchors if a.is_high_confidence]
        self.logger.warning(
            "标题具象锚点闸门本次 UNAVAILABLE：正文只有 %d 个锚点（其中高置信度 %d 个，"
            "阈值 %d）⇒ 无法要求标题承接具象信息。根因在**正文**（内容太薄），"
            "不是标题。锚点样本: %s",
            len(anchors),
            len(credible),
            GATE_MIN_ANCHORS,
            [(a.text, a.kind) for a in anchors[:6]],
        )

    def _title_max_length(self) -> int:
        """标题长度上限的唯一真相源：发布端的 `title_max_length`。

        此前这里**硬编码 30**，而 `ArticleQualityChecker._check_title` 用 40、
        发布裁剪 `_clip_title` 又读配置——三处各说各话。提示词让模型按 16-40 字
        产出，于是偏长的候选在这一步被静默砍掉，候选池因此枯竭（2026-09-16 实测：
        20 个候选过完只剩 1 条）。统一读配置，配置改一处即全链路生效。
        """
        try:
            return max(1, int(self.settings.publishing.title_max_length))
        except Exception:
            return 30

    @staticmethod
    def _title_body_reference(content: GeneratedContent) -> str:
        """标题-正文一致性校验该拿哪段文本：优先成稿正文，再退回摘要。

        不要用 `summary` 当正文——摘要不含公司名，会把「标题有主体、摘要里
        没有」误判成主体不一致，从而毙掉本可用的候选。
        """
        for candidate in (
            getattr(content, "polished_article", "") or "",
            getattr(content, "article", "") or "",
        ):
            if str(candidate).strip():
                return str(candidate)
        return str(getattr(content, "summary", "") or "")

    @classmethod
    def _is_quantified_number(cls, number: str, tail: str) -> str:
        """一个数字串是否带量化语义，是则返回它应记入的**完整** token。

        量化提取的唯一实现在 `services/concrete_anchors.quantity_token()`，
        这里只是保持旧签名（供既有回归测试与调用方使用）。

        2026-09-17（Blocker 1）修掉的两个截断 bug：
        - `18个月` 曾被切成 `18个`（单位表里没有 `个月`）；
        - `460万台` 曾被切成 `460万`（单位表里没有量纲组合，先匹到 `万` 就返回了）。
        两者都会让「标题写了 460万台目标」这种**正确承接**被判成没承接。
        """
        token, _confidence = quantity_token(number, tail)
        if token:
            return token
        # 四位年份按**裸数字**记：标题里常写「2027 年量产」或「2027 量产」，
        # 记成「2027年」会让后者匹配不上。
        if len(number) == 4 and number.startswith(("19", "20")):
            return number
        return ""

    @classmethod
    def extract_concrete_anchors(cls, text: str) -> list[ConcreteAnchor]:
        """正文里的具象锚点（公司名 / 量化值 / 年份），带类型、置信度与出处区间。"""
        return extract_concrete_anchors(text)

    @staticmethod
    def concrete_hook_status(title: str, body_text: str) -> tuple[str, ConcreteAnchor | None]:
        """标题与正文锚点的**三态**关系：PASS / FAIL / UNAVAILABLE。

        与 `_has_concrete_hook()` 的区别：后者只回答「标题有没有碰到任何一个具象元素」，
        前者是**闸门**语义 —— 正文锚点不足时返回 UNAVAILABLE（不判标题有罪，但如实留痕），
        且只认高置信度锚点。详见 `services/concrete_anchors.py` 的模块说明。
        """
        return concrete_hook_status(title, body_text)

    @classmethod
    def _extract_concrete_elements(cls, text: str) -> set[str]:
        """具象地标元素的**字面集合**（公司名/量化值/年份，不过滤置信度）。

        保留这个入口是为了兼容既有回归测试与「有没有碰到具象信息」的粗判；
        闸门语义请用 `concrete_hook_status()`。
        """
        return {anchor.text for anchor in extract_concrete_anchors(text)}

    @classmethod
    def _has_concrete_hook(cls, title: str, body_text: str) -> bool:
        """标题是否碰到正文里**至少一个**具象元素（不做置信度/数量门槛）。

        注意这里与旧实现的语义差别：旧实现在正文提不出任何元素时返回 True
        （静默放行）；新实现同样放行，但**闸门路径已改用 `concrete_hook_status()`**，
        会把这种情况记成 `UNAVAILABLE` 而不是悄悄通过。
        """
        body_elements = cls._extract_concrete_elements(body_text)
        if not body_elements:
            return True
        return any(elem in title for elem in body_elements)

    def _build_title_pool(
        self,
        source: str,
        titles: list[str],
        body_text: str,
        data_report: DataReport,
        preferred_titles: list[str] | None = None,
    ) -> TitlePool:
        """把一个来源的候选变成**经过同一个校验入口**的池（D1/D3）。

        `valid_titles` 只在**该池内部**按 `_title_score` 排序 —— 三个池之间不做
        任何混合排名，这是 D1 的核心：兜底候选数量（22~25）远多于模型（20），
        一旦同池竞争，兜底靠数量和权重就能抢掉模型候选。
        """
        pool = TitlePool(source=source, generated=list(titles))
        if not titles:
            return pool
        valid: list[str] = []
        for title in titles:
            outcome = self.validate_title_candidate(title, body_text)
            substantive = tuple(
                code
                for code in outcome.reason_codes
                if code != REASON_VALIDATION_CONTEXT_UNAVAILABLE
            )
            if outcome.passed:
                valid.append(title)
            elif substantive:
                pool.rejected.append(outcome)
            else:
                # 唯一的问题是「没有正文可校验」——记在 unavailable 里，
                # 与「被判不合格」区分开，不然统计上会把两者混成同一件事。
                pool.unavailable.append(outcome)
        pool.valid_titles = self._rank_titles(valid, data_report, list(preferred_titles or []))
        return pool

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> dict[str, list[str] | str]:
        playbook_context = self._build_title_playbook_context(data_report)
        prompt = self.render_prompt(
            "title_prompt_v2.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            style_digest=self.style_digest(style),
            cache_note=self.cache_note(cache_context),
            avoid_titles_json=self.json_text(cache_context.avoid_titles[:8]),
            summary=content.summary,
            article_angle=content.article_angle,
            key_points_json=self.json_text(data_report.key_points[:6]),
            playbook_track=playbook_context["track"],
            playbook_keywords_json=self.json_text(playbook_context["top_keywords"]),
            title_templates_json=self.json_text(playbook_context["title_templates"]),
            topic_imitation_json=self.json_text(playbook_context["topic_imitation_pool"]),
            benchmark_titles_json=self.json_text(playbook_context["example_titles"]),
            title_playbook_note=playbook_context["note"],
        )

        preferred_from_model: list[str] = []
        model_title_a = ""
        model_title_b = ""
        system_prompt = f"你是 TitleAgent，只输出 JSON。{self.best_practices_instruction()}"
        payload = self.request_json(system_prompt, prompt)
        raw_model_titles = payload.get("titles") if isinstance(payload, dict) else None
        model_raw_count = len(raw_model_titles) if isinstance(raw_model_titles, list) else 0
        if payload:
            model_titles = self._dedupe_titles(payload.get("titles"), cache_context)
            preferred_from_model = self._dedupe_titles(payload.get("recommended_titles"), cache_context)
            model_title_a = str(payload.get("title_a", "") or "").strip()
            model_title_b = str(payload.get("title_b", "") or "").strip()
        else:
            model_titles = []
        model_deduped_count = len(model_titles)

        # ⚠️ 2026-09-16：这里原先传 `content.summary` 当「正文」去做标题-正文
        # 一致性校验，而 summary 是摘要，**通常不含公司名**。于是
        # `_check_title_integrity` 会把「标题提到某公司、但 summary 里没有该公司」
        # 判成主体不一致，直接毙掉候选——可提示词恰恰要求标题必须带具体主体。
        # 实测（9-16 储能稿的 summary + 6 个合格候选）：用 summary 误杀 3/6，
        # 用正文全文只拒 1/6，且那 1 条是**真该拒**的（正文确实没提宁德时代）。
        # 20 个候选过完这道错校验只剩 1 条，下游无论怎么选都挑不出好标题。
        body_reference = self._title_body_reference(content)

        # ── D1：三层池，按优先级递补，**池间不混合排名** ─────────────────────
        # PRIMARY：模型候选。只要有 ≥1 条合格，本次就只用模型池。
        model_pool = self._build_title_pool(
            TITLE_SOURCE_MODEL, model_titles, body_reference, data_report, preferred_from_model
        )
        chosen = model_pool
        fallback_pool: TitlePool | None = None
        emergency_pool: TitlePool | None = None
        fallback_generated = 0
        if not chosen.valid_titles:
            # RESCUE：模型池全灭时才启用兜底（兜底有 22~25 条，绝不允许它参与正常竞争）。
            fallback_titles = self._fallback_titles(
                content,
                trend_report,
                data_report,
                style,
                cache_context,
                playbook_context=playbook_context,
            )
            fallback_generated = len(fallback_titles)
            fallback_pool = self._build_title_pool(
                TITLE_SOURCE_FALLBACK, fallback_titles, body_reference, data_report, []
            )
            chosen = fallback_pool
        if not chosen.valid_titles:
            # LAST RESORT：前两层都提不出合格标题，才轮到应急池。同样要过正式校验，
            # 不做「无校验放行」。
            emergency_pool = self._build_title_pool(
                TITLE_SOURCE_EMERGENCY,
                self._emergency_titles(content, data_report),
                body_reference,
                data_report,
                [],
            )
            chosen = emergency_pool

        ranked_titles = list(chosen.valid_titles)

        if ranked_titles:
            recommended_titles = self._pick_recommended_titles(ranked_titles, data_report)
            dual = self._pick_dual_titles(
                ranked_titles, data_report, model_title_a, model_title_b, cache_context
            )
        else:
            # D2：**一个合格标题都没有**就是真失败。这里既不清空后再偷偷塞回原始候选，
            # 也不把不合格的标题当保底发出去 —— 交给 topic attempt loop 换下一个选题。
            recommended_titles = []
            dual = {"title_a": "", "title_b": ""}

        final_titles = ranked_titles[: self.settings.title_count]
        validation_payload = chosen.to_dict()
        validation_payload["pools"] = {
            "model": model_pool.to_dict(),
            "fallback": fallback_pool.to_dict() if fallback_pool else None,
            "emergency": emergency_pool.to_dict() if emergency_pool else None,
        }
        validation_payload["validation_context"] = (
            "body" if str(body_reference or "").strip() else "unavailable"
        )

        self.logger.info(
            "TitleAgent 候选统计: 模型原始 %d → 去重后 %d；兜底 %d；"
            "选中池=%s；生成 %d → 合格 %d（拒 %d / 无正文 %d）；输出 %d",
            model_raw_count,
            model_deduped_count,
            fallback_generated,
            chosen.source,
            len(chosen.generated),
            len(chosen.valid_titles),
            len(chosen.rejected),
            len(chosen.unavailable),
            len(final_titles),
        )
        if not ranked_titles and chosen.generated:
            # 旧实现：`if compliant_titles: ...` 否则**保留全部未过滤候选**，
            # 等于「全灭时整道闸静默失效」。现在全灭就是全灭，并给出可观测的
            # status + reason 汇总，让上层知道「为什么一个标题都没有」。
            self.logger.warning(
                "TitleAgent 标题选择失败 status=%s：来源池=%s，%d 条候选无一通过校验。"
                "top_reasons=%s",
                chosen.status,
                chosen.source,
                len(chosen.generated),
                chosen.reason_summary(),
            )

        return {
            "titles": final_titles,
            "recommended_titles": recommended_titles[: self.settings.recommended_title_count],
            "title_a": dual["title_a"],
            "title_b": dual["title_b"],
            "alt_title": dual["title_b"],
            # 只有真的选出标题时才报来源；全灭时留空，避免把「最后试过的池」
            # 误读成「这篇用的是 emergency 标题」。
            "selected_title_source": chosen.source if chosen.valid_titles else "",
            "title_validation": validation_payload,
        }

    def _is_narrative_style(self, style: StyleProfile) -> bool:
        """当前账号是不是叙事类（社会故事）定位。"""
        return str(getattr(style, "content_domain", "industry") or "industry").strip().lower() == "narrative"

    def _narrative_fallback_titles(
        self,
        content: GeneratedContent,
        data_report: DataReport,
    ) -> list[str]:
        """社会故事号的兜底标题：只用「主体 + 事件 + 结果/提醒」组句。

        铁律：**一个产业词都不许出现**（订单/交付/产能/产业链/重估/突围/样机…）。
        素材是真实社会新闻时，最稳的兜底其实就是**选题原标题**——它是平台自己
        给这条新闻起的标题，天然贴合事件，也天然是"人间故事"的说法，
        比任何拼接句式都安全，所以放在第一位。
        """
        titles: list[str] = []

        def push(text: str) -> None:
            cleaned = self._sanitize_title(str(text or ""))
            if cleaned and len(cleaned) >= 8 and cleaned not in titles:
                titles.append(cleaned)

        topic = self._sanitize_title(str(getattr(data_report, "topic_title", "") or ""))
        if topic:
            push(topic)

        # 拼句用的「事件短名」：选题原标题动辄 20+ 字，再套句式就会超 30 字上限。
        #
        # ⚠️ 2026-09-19：这里原来回退到 `_primary_topic_label`（关键词），
        # 而长青题的 `matched_keywords` 往往是「养老」「借钱」这类**单个泛词**，
        # 于是拼出「老人这件事，最后是怎么处理的」「老人给普通人提了个醒」这种
        # 完全不知道在说什么的标题（实测 `.runtime/probe_evergreen_titles.py`）。
        # 正确做法是从选题标题里**裁出有意义的半句**（一般在逗号处断开）：
        #   「老人在家摔倒后，子女才发现没人知道他的日常情况」→「老人在家摔倒后」
        # 这样既够具体，又留得下后半句句式的字数。
        topic_head = self._topic_head_phrase(topic)
        has_natural_head = bool(
            topic_head
            and len(topic_head) < len(topic)
            and topic[len(topic_head)] in "，,：:；;。！!？?"
        )
        subject = topic if len(topic) <= 14 else (topic_head if has_natural_head else "")

        # 无标点的长新闻标题不能按字符硬截后继续套句式。云端实测会生成
        # “女子父亲突然离世邻居1分钟赶这件事……”这类病句；这种场景只保留原标题。
        if subject:
            push(f"{subject}这件事，最后是怎么处理的")
            push(f"{subject}之后，当事人面临的是什么")
            push(f"{subject}：换成你该先做什么")
            push(f"{subject}给普通人提了个醒")

        # 数字锚点：社会新闻里的金额/时长/年龄往往是最抓人的信息
        for number in list(getattr(data_report, "growth_numbers", []) or [])[:2]:
            value = self.clean_publish_text(number)
            if subject and value and len(value) <= 16:
                push(f"{subject}，{value}这个数字说明了什么")

        return titles[: self.settings.title_count]

    @staticmethod
    def _topic_head_phrase(topic_title: str, limit: int = 14) -> str:
        """从选题标题里裁出一段「说得清是什么事」的短名。

        标题通常是「事件，后续/追问」结构，在第一个标点处断开最自然：
            「老人在家摔倒后，子女才发现没人知道他的日常情况」→「老人在家摔倒后」
            「子女轮流照顾老人，为什么最后总是闹到翻脸」→「子女轮流照顾老人」
        断出来的半句若仍超长，再退到按字数硬裁（不切断标点）。
        """
        text = str(topic_title or "").strip()
        if not text:
            return ""
        for mark in ("，", ",", "：", ":", "；", ";", "。", "！", "!", "？", "?"):
            index = text.find(mark)
            if 0 < index <= limit:
                return text[:index].strip()
        return text[:limit].strip()

    def _emergency_titles(self, content: GeneratedContent, data_report: DataReport) -> list[str]:
        """标题兜底地板：所有候选都被去重杀光时，至少保住一个可用标题。

        只做最低限度的加工——优先用选题原标题，再退到正文首句，
        最后退到「行业 + 主体」拼接。目标不是好标题，而是让这一轮不空手而归。
        """
        candidates: list[str] = []

        topic = self._sanitize_title(str(getattr(data_report, "topic_title", "") or ""))
        if topic:
            candidates.append(topic)

        company = self._primary_company(data_report)
        label = self._primary_topic_label(data_report)
        # ⚠️ 2026-09-19：最后这两条兜底句是**产业句式**，叙事号不许用
        # （实测会拼出「老人热度之外，谁在拿走真正订单」这种科技号标题）。
        # 叙事号这里直接退回"选题原标题 + 正文首句"，两者都是事件本身的说法。
        narrative = str(getattr(self.settings, "content_domain", "industry") or "industry").strip().lower() == "narrative"
        if not narrative:
            if company and label and f"{company}{label}" not in candidates:
                candidates.append(self._fit_title_length(f"{company}{label}横向对比"))
            if label:
                candidates.append(self._fit_title_length(f"{label}热度之外，谁在拿走真正订单"))

        article_first = ""
        for line in str(getattr(content, "article", "") or "").splitlines():
            line = self._sanitize_title(line)
            if len(line) >= 12:
                article_first = line
                break
        if article_first:
            candidates.append(article_first)

        result: list[str] = []
        for item in candidates:
            item = self._fit_title_length(item.strip())
            if item and item not in result and len(item) >= 8:
                result.append(item)
        return result[: self.settings.title_count]

    def _fallback_titles(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
        playbook_context: dict[str, object] | None = None,
    ) -> list[str]:
        # Never rank narrative candidates together with industry templates.
        # Merely prepending story titles still allowed the generic scorer to
        # promote phrases such as "增速里先回款的是哪一环" above the event title.
        if self._is_narrative_style(style):
            return self._dedupe_titles(
                self._narrative_fallback_titles(content, data_report),
                cache_context,
            )

        keyword = self._primary_topic_label(data_report)
        secondary = "关键环节"
        for candidate in self.clean_publish_list(data_report.focus_keywords[1:] + data_report.supply_chain_keywords[1:]):
            if candidate and candidate != keyword and candidate not in keyword and keyword not in candidate:
                secondary = candidate
                break

        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else "这组数据"
        company = self._primary_company(data_report)
        scene = self._primary_scene(content, data_report)
        angle = self.clean_publish_text(content.article_angle or trend_report.recommended_angle).rstrip("。")

        templates: list[str] = []
        if playbook_context:
            templates.extend(self._adapt_playbook_titles(playbook_context, data_report))

        # 利润/兑现类（控制在 4 条以内；移除所有"X背后，利润先流向谁""开始重排利润"
        # "毛利率分化改写座次"等已验证零点击同质骨架），改为数据/公司/场景锚定的多样化句式，
        # 每条标题必须带具体信息量而非泛化骨架，同时满足 RULE_006 具象化。
        profit_variants: list[str] = []
        if number and number not in {"这组数据", "一组数据", "市场数据"}:
            profit_variants.append(f"{number}的增速里，{keyword}先回款的是哪一环")
        else:
            profit_variants.append(f"{keyword}开始放量，先收到钱的是哪一环")
        if company:
            profit_variants.append(f"{company}的{keyword}生意，真正的收入来自什么")
        elif scene:
            profit_variants.append(f"{scene}跑通的订单，最后给了哪类供应商")
        else:
            profit_variants.append(f"{secondary if secondary not in keyword else '供应端'}先收到钱，{keyword}的账怎么算")
        profit_variants.append(f"{keyword}从样机走到交付，中间卡在哪一步")
        templates.extend(profit_variants[:4])
        # 技术突破/产品迭代类
        templates.extend(
            [
            f"{keyword}技术路线之争，谁选对了方向",
            f"{keyword}迭代加速，旧方案正在被淘汰",
            f"{keyword}下一代方案已定型，谁在领跑",
            f"{keyword}技术拐点到了，窗口期有多长",
            ]
        )
        # 竞争格局/公司博弈类
        templates.extend(
            [
            f"{keyword}升温之后，先突围的不是热门公司",
            f"{keyword}这轮重估，先拉开差距的是谁",
            f"{keyword}不是比概念，是比谁先跑通",
            f"{keyword}走到验证期，谁会先掉队",
            ]
        )
        # 市场/需求/政策类
        templates.extend(
            [
            f"{keyword}需求端变了，供给还没跟上",
            f"{scene or keyword}把{keyword}带到了哪一步",
            f"{keyword}热度之外，谁在拿走真正订单",
            f"{keyword}爆单之前，谁先卡住{secondary if secondary not in keyword else '关键环节'}",
            ]
        )
        if company:
            templates.extend(
                [
                    f"{company}之后，{keyword}格局变了",
                    f"{company}入场，{keyword}谁最紧张",
                    f"{company}押注{secondary if secondary not in company else '新路线'}，赌对了吗",
                ]
            )
        if scene:
            templates.extend(
                [
                    f"{scene}成了{keyword}新试验场",
                    f"{scene}落地后，{keyword}谁先跑通",
                    f"{scene}背后，{keyword}谁在抢跑",
                ]
            )
        if angle and len(angle) <= 26:
            templates.append(angle)

        if style.key == "finance_report":
            templates[:3] = [
                f"{keyword}重估先落在哪一环",
                f"{number}之后，{keyword}谁先拿到真金白银",
                f"{keyword}验证期里，谁更容易跑出来",
            ]
        elif style.key == "viral_science":
            templates[:3] = [
                f"{keyword}很热，但先跑出来的未必是热门公司",
                f"{number}背后，{keyword}谁才是真赢家",
                f"{keyword}看着热，关键却在{secondary}",
            ]
        elif style.key == "tech_media":
            templates[:3] = [
                f"{keyword}热度回升，真正变化发生在哪",
                f"{keyword}走到验证期，市场开始重选公司",
                f"{keyword}背后，谁在改写竞争规则",
            ]

        fact_titles = self._fallback_fact_titles(content, data_report)
        if fact_titles:
            # ⚠️ 2026-09-16：这里以前是 `fact_titles + templates`（**塞到最前面**）。
            # 配上 `select_auto_title` 取「第一个通过闸门的候选」，等于让**硬编码模板
            # 永远压过模型产出的标题** —— 云端 9-16 发布的就是这个兜底模板
            # （「工信部推动机器人80%：具身智能如何实测」，实测同句式历史阅读 10–21）。
            # 兜底就该待在兜底的位置：模型候选全不可用时才轮到它。
            templates = templates + fact_titles

        # A financial title must be anchored to the selected topic itself.
        # Playbook templates are useful for style, but cannot replace the
        # subject and figures from a real earnings headline.
        evidence_titles = self._fallback_evidence_titles(data_report)
        if evidence_titles:
            templates = evidence_titles + templates

        # ⚠️ 2026-09-17：兜底句式是**用锚定词做字符串拼接**出来的，当两个锚定词指向
        # 同一主体时会拼出病句。实测（cache/b4cc878dbe9eb9c9，比亚迪跨界人形机器人）：
        #   「比亚迪的比亚迪生意，真正的收入来自什么」  ← company == keyword
        #   「比亚迪460万台：比亚迪真实进度看交付」      ← anchor == detail
        # 这两条都会进入候选池、并被 `select_auto_title` 当成正常候选参与择优，
        # 所以在**生成侧**就要拦掉，不能指望下游认出来。
        anchors = [company, keyword, secondary, self._topic_subject_label(data_report, fallback="")]
        templates = [t for t in templates if not self._has_repeated_anchor(t, anchors)]

        return self._dedupe_titles(templates, cache_context)

    @staticmethod
    def _has_repeated_anchor(title: str, anchors: list[str]) -> bool:
        """兜底标题里同一个锚定主体出现两次以上，说明是拼接产生的病句。"""
        for anchor in anchors:
            anchor = str(anchor or "").strip()
            if len(anchor) >= 2 and title.count(anchor) >= 2:
                return True
        return False

    def _fallback_fact_titles(
        self,
        content: GeneratedContent,
        data_report: DataReport,
    ) -> list[str]:
        """Build a factual title when the model returns no usable candidate.

        The title must borrow its subject and numeric anchor from the selected
        topic.  The final ``实测`` wording is an analysis question/action, not
        a claim that an unsupported order, delivery, or revenue result exists.
        """
        topic_text = re.sub(r"\s+", "", self.clean_publish_text(data_report.topic_title))
        factual_text = " ".join(
            self.clean_publish_text(str(item))
            for item in (
                data_report.topic_title,
                *(data_report.core_data or [])[:4],
                *(data_report.evidence_notes or [])[:4],
                *(data_report.key_points or [])[:4],
                *(getattr(data_report, "source_facts", []) or [])[:8],
            )
            if str(item).strip()
        )
        if not factual_text:
            return []

        numbers: list[str] = []
        for source in (
            *(data_report.growth_numbers or [])[:6],
            data_report.topic_title,
            *(data_report.core_data or [])[:4],
            *(data_report.evidence_notes or [])[:4],
        ):
            for token in self._extract_numeric_tokens(str(source)):
                if token not in numbers:
                    numbers.append(token)
        number = next(
            (token for token in numbers if not token.endswith(("年", "月"))),
            numbers[0] if numbers else "",
        )

        scene = ""
        for candidate in ("充电站", "储能电站", "换电站", "电厂", "电站", "机场", "工厂", "园区", "基地"):
            if candidate in factual_text:
                scene = candidate
                break

        keyword_values = self.clean_publish_list(
            [*(data_report.focus_keywords or []), *(data_report.supply_chain_keywords or [])],
            limit=16,
        )

        def usable_term(value: object) -> str:
            text = re.sub(r"\s+", "", self.clean_publish_text(str(value or "")))
            if len(text) < 2 or self.is_internal_label(text) or text in {"行业", "赛道", "产业链"}:
                return ""
            return text

        topic_terms: list[str] = []
        factual_terms: list[str] = []
        for raw in keyword_values:
            term = usable_term(raw)
            if not term or term == scene:
                continue
            if term in topic_text:
                topic_terms.append(term)
            if term in factual_text:
                factual_terms.append(term)
        detail = max(topic_terms or factual_terms, key=len, default="项目")
        anchor = scene or self._primary_company(data_report) or self._topic_subject_label(data_report, fallback="项目")
        anchor = re.sub(r"\s+", "", anchor)[:8]
        detail = re.sub(r"\s+", "", detail)[:8]
        if not anchor:
            return []

        raw_titles: list[str] = []
        if number:
            raw_titles.extend(
                [
                    f"{anchor}{number}：{detail}真实进度看交付",
                    f"{anchor}{number}，{detail}交付进度才是关键",
                ]
            )
        else:
            raw_titles.extend(
                [
                    f"{anchor}：{detail}真实进度看交付",
                    f"{anchor}，{detail}交付进度才是关键",
                ]
            )

        result: list[str] = []
        for raw_title in raw_titles:
            title = self._sanitize_title(raw_title)
            # ⚠️ 2026-09-17（D3）：这里以前是
            # `self.validate_title_compliance(title)` —— **不带正文**。
            # 于是兜底标题只过了「长度/禁用词」这几条与正文无关的检查，
            # 「标题与正文一致性」和「具象锚点承接」两道闸门对它是**失效**的：
            # 同一个标题走模型路径会被拒、走兜底路径能活。
            # 现在这里只做**格式清洗**，校验统一交给 `_build_title_pool()` 的
            # `validate_title_candidate(title, body_text)`。
            if title and title not in result:
                result.append(title)
        return result

    def _fallback_evidence_titles(self, data_report: DataReport) -> list[str]:
        topic_title = re.sub(r"\s+", "", self.clean_publish_text(data_report.topic_title))
        if not topic_title:
            return []
        finance_tokens = ("财报", "业绩", "营收", "净利润", "归母", "半年报", "年报", "一季报")
        if not any(token in topic_title for token in finance_tokens):
            return []

        subject = self._primary_company(data_report)
        if not subject:
            prefix = re.split(r"20\d{2}|年度|半年报|年报|一季报|季度|归母净利润|净利润|营收", topic_title, maxsplit=1)[0]
            prefix = prefix.strip(" ：:，,")
            if 2 <= len(prefix) <= 16 and not self.is_internal_label(prefix):
                subject = prefix
        if not subject:
            return []

        report_label = "半年报" if "半年" in topic_title else "年报" if "年报" in topic_title else "一季报" if "一季" in topic_title else "财报"
        amount_match = re.search(
            r"(?:归母)?净利润[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(亿元|亿|万元)",
            topic_title,
        )
        amount = f"{amount_match.group(1)}{amount_match.group(2)}" if amount_match else ""
        growth_match = re.search(
            r"(同比(?:增长|下降|减少|提升)?)[^0-9]{0,8}(\d+(?:\.\d+)?%)",
            topic_title,
        )
        growth = f"{growth_match.group(1)}{growth_match.group(2)}" if growth_match else ""
        if not amount and not growth:
            return []

        parts = [f"{subject}发布{report_label}"]
        if amount:
            parts.append(f"净利润{amount}")
        if growth:
            parts.append(growth)
        return ["：".join([parts[0], "，".join(parts[1:])])]

    def _build_title_playbook_context(self, data_report: DataReport) -> dict[str, object]:
        if self._is_narrative_mode():
            return {
                "track": "社会故事",
                "top_keywords": [],
                "title_templates": [],
                "topic_imitation_pool": [],
                "example_titles": [],
                "self_benchmark_titles": [],
                "self_benchmark_patterns": [],
                "note": "叙事账号只依据当前事件生成标题，不使用产业标题对标库。",
            }
        track_label = self._infer_track_label(data_report)
        selected = self.growth_ops_service.get_title_playbook_track_context(track_label)
        benchmark_accounts = [str(item).strip() for item in selected.get("benchmark_accounts", []) if str(item).strip()]
        self_benchmark_titles = [
            str(item).strip() for item in selected.get("self_benchmark_titles", []) if str(item).strip()
        ]
        self_benchmark_hooks = [
            str(item).strip() for item in selected.get("self_benchmark_concrete_hooks", []) if str(item).strip()
        ]
        note_parts = [
            "先学习这些对标账号的标题结构、判断感和主体感，再结合当前文章信息重写",
            f"可参考账号：{'、'.join(benchmark_accounts[:5]) or '当前赛道对标库'}",
        ]
        if self_benchmark_hooks:
            note_parts.append(f"可优先复用已验证的具象钩子要素：{'、'.join(self_benchmark_hooks[:6])}")
        if self_benchmark_titles:
            note_parts.append(f"优先吸收你自己的已验证样本：{'；'.join(self_benchmark_titles[:2])}")
        note_parts.append("禁止直接照抄样本标题，禁止只替换一个名词。")
        title_templates = [str(item).strip() for item in selected.get("title_templates", []) if str(item).strip()][:8]
        # 应用迭代调整项：按 title_template_weights 重排标题模板，权重高的优先
        if self.title_template_weights:
            title_templates = self._reorder_templates_by_weights(
                title_templates, self.title_template_weights
            )
        return {
            "track": str(selected.get("track", track_label)).strip() or track_label,
            "top_keywords": [str(item).strip() for item in selected.get("top_keywords", []) if str(item).strip()][:8],
            "title_templates": title_templates,
            "topic_imitation_pool": [
                str(item).strip() for item in selected.get("topic_imitation_pool", []) if str(item).strip()
            ][:8],
            "example_titles": [str(item).strip() for item in selected.get("example_titles", []) if str(item).strip()][:10],
            "self_benchmark_titles": self_benchmark_titles[:5],
            "self_benchmark_patterns": [
                str(item).strip() for item in selected.get("self_benchmark_patterns", []) if str(item).strip()
            ][:5],
            "note": "；".join(note_parts),
        }

    def _reorder_templates_by_weights(self, templates: list[str], weights: dict) -> list[str]:
        """按权重重排标题模板：包含高权重关键词的模板优先，未匹配的保持相对顺序。"""
        if not templates or not weights:
            return templates

        def score(template: str) -> float:
            best = 0.0
            for key, value in weights.items():
                token = str(key or "").strip()
                if not token or token not in template:
                    continue
                try:
                    weight = float(value)
                except (TypeError, ValueError):
                    weight = 0.0
                if weight > best:
                    best = weight
            return best

        # 稳定排序：权重高的在前，相同权重（含未匹配的 0 分）保持原相对顺序
        return sorted(templates, key=lambda t: -score(t))

    def _load_title_playbook_payload(self) -> dict[str, object]:
        path = self.settings.output_dir / "growth_ops" / "title_playbook.json"
        if not path.exists():
            try:
                self.growth_ops_service.build_benchmark_report()
            except Exception as exc:
                self.logger.warning("无法自动构建标题打法库，回退到默认标题策略: %s", exc)
                return {}
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("读取标题打法库失败，回退到默认标题策略: %s", exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _infer_track_label(self, data_report: DataReport) -> str:
        text = " ".join(
            [
                data_report.focus_industry,
                data_report.topic_title,
                *data_report.focus_keywords[:6],
                *data_report.supply_chain_keywords[:6],
                *data_report.key_points[:4],
            ]
        )
        normalized = self.clean_publish_text(text)
        # 首先判断是否是科技相关行业（优先级更高）
        if any(token in normalized for token in ("机器人", "人形", "具身", "减速器", "灵巧手", "关节", "伺服")):
            return "AI机器人"
        if any(token in normalized for token in ("芯片", "半导体", "封测", "晶圆", "EDA", "光刻", "代工", "封测")):
            return "半导体芯片"
        if any(token in normalized for token in ("储能", "光伏", "锂电", "电池", "逆变器", "PCS", "风电", "充电")):
            return "新能源储能"
        if any(token in normalized for token in ("AI应用", "大模型", "LLM", "GPT", "AI搜索", "AI助手", "AI体检", "AI教育", "AI医疗")):
            return "AI应用"
        if any(token in normalized for token in ("智能制造", "工业自动化", "PLC", "MES", "数控", "工业互联网", "工控")):
            return "工业自动化"
        # 只有当只有国际相关词汇，且没有科技相关词汇时，才判断为国际热点
        tech_tokens_count = sum(1 for token in ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "AI", "大模型", "智能制造", "工业自动化"] if token in normalized)
        if tech_tokens_count == 0:
            # 只有国际相关词汇时
            if any(token in normalized for token in self.INTERNATIONAL_TOKENS):
                return "国际热点"
            if any(token in normalized for token in self.SPORTS_TOKENS):
                return "体育赛事"
        return "科技商业"

    def _adapt_playbook_titles(self, playbook_context: dict[str, object], data_report: DataReport) -> list[str]:
        keyword = self._playbook_subject_label(data_report, playbook_context)
        result: list[str] = []
        for raw_title in playbook_context.get("title_templates", []):
            title = self._adapt_playbook_title(str(raw_title).strip(), keyword, playbook_context)
            if title:
                result.append(title)
        return result

    def _playbook_subject_label(self, data_report: DataReport, playbook_context: dict[str, object]) -> str:
        track = str(playbook_context.get("track", "")).strip()
        focus_industry = self.clean_publish_text(data_report.focus_industry).strip()
        if track == "AI机器人":
            if "机器人" in focus_industry or "具身" in focus_industry:
                return focus_industry
            return "人形机器人"
        if track == "半导体芯片":
            if "芯片" in focus_industry or "半导体" in focus_industry:
                return focus_industry
            return "半导体"
        if track == "新能源储能":
            if "储能" in focus_industry or "新能源" in focus_industry or "光伏" in focus_industry:
                return focus_industry
            return "储能"
        if track == "AI应用":
            if "AI" in focus_industry or "大模型" in focus_industry:
                return focus_industry
            return "AI应用"
        if track == "工业自动化":
            if "自动化" in focus_industry or "制造" in focus_industry:
                return focus_industry
            return "工业自动化"
        if track == "国际热点":
            if "国际" in focus_industry or "地缘" in focus_industry:
                return focus_industry
            return "国际局势"
        if track == "体育赛事":
            if "体育" in focus_industry or "足球" in focus_industry or "篮球" in focus_industry:
                return focus_industry
            return "体育赛事"
        if focus_industry and not self.is_internal_label(focus_industry):
            return focus_industry
        return self._primary_topic_label(data_report)

    def _adapt_playbook_title(
        self,
        title: str,
        keyword: str,
        playbook_context: dict[str, object],
    ) -> str:
        track = str(playbook_context.get("track", "")).strip()
        adapted = title
        if track == "AI机器人":
            if keyword and keyword not in {"人形机器人", "机器人"}:
                adapted = adapted.replace("人形机器人", keyword)
                adapted = adapted.replace("机器人", keyword)
        elif track == "半导体芯片":
            if keyword and keyword not in {"半导体", "芯片"}:
                adapted = adapted.replace("半导体", keyword)
                adapted = adapted.replace("芯片", keyword)
        elif track == "新能源储能":
            if keyword and keyword not in {"储能", "储能项目"}:
                adapted = adapted.replace("储能项目", keyword)
                adapted = adapted.replace("储能", keyword)
        elif track == "AI应用":
            if keyword and keyword not in {"AI应用", "AI"}:
                adapted = adapted.replace("AI应用", keyword)
        elif track == "工业自动化":
            if keyword and keyword not in {"工业自动化", "智能制造"}:
                adapted = adapted.replace("工业自动化", keyword)
                adapted = adapted.replace("智能制造", keyword)
        elif track == "国际热点":
            if keyword and keyword not in {"国际局势", "国际热点"}:
                adapted = adapted.replace("国际局势", keyword)
                adapted = adapted.replace("国际热点", keyword)
        elif track == "体育赛事":
            if keyword and keyword not in {"体育赛事", "比赛"}:
                adapted = adapted.replace("体育赛事", keyword)
                adapted = adapted.replace("比赛", keyword)
        else:
            if keyword and keyword not in {"机器人", "科技制造"}:
                adapted = adapted.replace("机器人", keyword)
        return self._fit_title_length(adapted.strip())

    def _pick_dual_titles(
        self,
        ranked_titles: list[str],
        data_report: DataReport,
        model_title_a: str,
        model_title_b: str,
        cache_context: CacheContext,
    ) -> dict[str, str]:
        curiosity_title = ""
        rational_title = ""

        if model_title_a:
            sanitized = self._sanitize_title(model_title_a)
            if sanitized and self.normalize_title(sanitized) not in {self.normalize_title(t) for t in cache_context.avoid_titles}:
                curiosity_title = sanitized
        if model_title_b:
            sanitized = self._sanitize_title(model_title_b)
            if sanitized and self.normalize_title(sanitized) not in {self.normalize_title(t) for t in cache_context.avoid_titles}:
                rational_title = sanitized

        if not curiosity_title or not rational_title:
            for title in ranked_titles:
                is_curiosity = self._is_curiosity_title(title)
                is_rational = self._is_rational_title(title)
                if not curiosity_title and is_curiosity:
                    curiosity_title = title
                if not rational_title and is_rational:
                    rational_title = title
                if curiosity_title and rational_title:
                    break

        if not curiosity_title and ranked_titles:
            curiosity_title = ranked_titles[0]
        if not rational_title and len(ranked_titles) > 1:
            for title in ranked_titles[1:]:
                if title != curiosity_title:
                    rational_title = title
                    break
        if not rational_title and len(ranked_titles) == 1:
            rational_title = ranked_titles[0]

        return {"title_a": curiosity_title, "title_b": rational_title}

    def _is_curiosity_title(self, title: str) -> bool:
        if "？" in title or "?" in title:
            return True
        return any(token in title for token in self.CURIOSITY_TOKENS)

    def _is_rational_title(self, title: str) -> bool:
        if any(char.isdigit() for char in title):
            if any(token in title for token in self.RATIONAL_TOKENS):
                return True
        return any(token in title for token in self.RATIONAL_TOKENS[:14])

    def _rank_titles(
        self,
        titles: list[str],
        data_report: DataReport,
        preferred_titles: list[str],
    ) -> list[str]:
        preferred_set = {self.normalize_title(item) for item in preferred_titles}
        return sorted(
            titles,
            key=lambda item: self._title_score(item, data_report, preferred_set),
            reverse=True,
        )

    def _pick_recommended_titles(self, titles: list[str], data_report: DataReport) -> list[str]:
        selected: list[str] = []
        seen_buckets: set[str] = set()
        seen_skeletons: set[str] = set()
        for title in titles:
            bucket = self._title_bucket(title, data_report)
            skeleton = self._title_skeleton(title)
            if bucket in seen_buckets:
                continue
            if skeleton in seen_skeletons:
                continue
            selected.append(title)
            seen_buckets.add(bucket)
            seen_skeletons.add(skeleton)
            if len(selected) >= self.settings.recommended_title_count:
                return selected

        for title in titles:
            if title in selected:
                continue
            selected.append(title)
            if len(selected) >= self.settings.recommended_title_count:
                break
        return selected

    def _title_score(
        self,
        title: str,
        data_report: DataReport,
        preferred_titles: set[str],
    ) -> int:
        click_score = 46
        save_score = 48
        discussion_score = 38
        brand_fit_score = 50

        normalized = self.normalize_title(title)
        if normalized in preferred_titles:
            click_score += 5
            brand_fit_score += 4

        if any(char.isdigit() for char in title):
            click_score += 8
            save_score += 4

        if "？" in title or "?" in title:
            click_score += 3
            discussion_score += 6

        if any(token in title for token in self.CONTRAST_TOKENS):
            click_score += 8
            discussion_score += 4

        if any(token in title for token in self.PROFIT_TOKENS):
            save_score += 6
            brand_fit_score += 5

        if any(token in title for token in self.VALIDATION_TOKENS):
            save_score += 5
            brand_fit_score += 4

        if any(token in title for token in self.DEPTH_TOKENS):
            save_score += 4
            brand_fit_score += 5

        if any(token in title for token in self.SCENE_TOKENS):
            click_score += 8
            discussion_score += 4
            brand_fit_score += 5

        if any(token in title for token in self.BENEFIT_SHIFT_TOKENS):
            save_score += 6
            brand_fit_score += 5
        if self._content_mode(data_report) in {"sports", "international"} and any(
            token in title for token in self.MODE_NOISE_TOKENS
        ):
            click_score -= 20
            save_score -= 18
            brand_fit_score -= 24

        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            click_score += 7
            brand_fit_score += 8

        keywords = self.clean_publish_list(data_report.focus_keywords + data_report.supply_chain_keywords)
        if any(keyword and keyword in title for keyword in keywords[:5]):
            brand_fit_score += 8
        else:
            brand_fit_score -= 8

        length = len(title)
        max_length = int(self.settings.publishing.title_max_length)
        if 16 <= length <= 28:
            click_score += 8
            brand_fit_score += 6
        elif 13 <= length <= max_length:
            click_score += 2
        else:
            click_score -= 12
            brand_fit_score -= 10

        if any(token in title for token in self.TEMPLATEY_TOKENS):
            click_score -= 12
            brand_fit_score -= 14

        if title.count("，") >= 2:
            click_score -= 4
            brand_fit_score -= 4

        if any(token in title for token in self.LOW_QUALITY_TOKENS):
            click_score -= 18
            brand_fit_score -= 20

        if any(raw in title for raw in self.INTERNAL_LABEL_MAP.keys()):
            click_score -= 16
            brand_fit_score -= 20

        # 同质化模式惩罚：标题同时包含高频同质词组时大幅降分
        homogeneous_hits = sum(
            1 for pair in self.HOMOGENEOUS_PATTERNS
            if pair[0] in title and pair[1] in title
        )
        if homogeneous_hits >= 2:
            click_score -= 14
            save_score -= 12
            brand_fit_score -= 16
        elif homogeneous_hits == 1:
            click_score -= 6
            save_score -= 5
            brand_fit_score -= 7

        total = round(
            click_score * 0.36
            + save_score * 0.30
            + discussion_score * 0.14
            + brand_fit_score * 0.20
        )
        return max(1, total)

    TECH_TOKENS = ("技术", "迭代", "方案", "路线", "架构", "突破", "拐点", "下一代", "换代", "升级")

    def _title_bucket(self, title: str, data_report: DataReport) -> str:
        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            return "company"
        if any(token in title for token in self.TECH_TOKENS):
            return "tech"
        if any(token in title for token in self.PROFIT_TOKENS):
            return "profit"
        if any(token in title for token in self.VALIDATION_TOKENS):
            return "validation"
        if any(token in title for token in self.CONTRAST_TOKENS):
            return "contrast"
        return "general"

    def _dedupe_titles(self, payload: object, cache_context: CacheContext) -> list[str]:
        if isinstance(payload, list):
            raw_titles = [str(item).strip() for item in payload if str(item).strip()]
        else:
            raw_titles = []

        normalized_avoid = {self.normalize_title(item) for item in cache_context.avoid_titles}
        result: list[str] = []
        seen: set[str] = set()
        seen_skeletons: set[str] = set()
        for title in raw_titles:
            cleaned_title = self._sanitize_title(title)
            normalized = self.normalize_title(cleaned_title)
            if not normalized or normalized in seen or normalized in normalized_avoid:
                continue
            if any(token in cleaned_title for token in self.LOW_QUALITY_TOKENS + self.PLACEHOLDER_TOKENS):
                continue
            if any(token in cleaned_title for token in self.TEMPLATEY_TOKENS):
                continue
            skeleton = self._title_skeleton(cleaned_title)
            if skeleton in seen_skeletons:
                continue
            seen.add(normalized)
            seen_skeletons.add(skeleton)
            result.append(cleaned_title)
        return result

    def _title_skeleton(self, title: str) -> str:
        """提取标题语义骨架：将具体行业/产品词替换为占位符，保留结构模式。

        例如 "人形机器人利润向零部件集中" 和 "AI芯片利润向核心件集中"
        都会变成 "{SUBJ}利润向{SUBJ}集中"，视为同一骨架。
        """
        skeleton = title
        for filler in self.SKELETON_FILLERS:
            if filler in skeleton:
                skeleton = skeleton.replace(filler, "{S}")
        # 压缩连续占位符
        while "{S}{S}" in skeleton:
            skeleton = skeleton.replace("{S}{S}", "{S}")
        return skeleton

    def _sanitize_title(self, title: str) -> str:
        cleaned = self.strip_prompt_leakage(self.clean_publish_text(title))
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
        cleaned = re.sub(r"^\d{1,2}[\.、]\s+", "", cleaned)
        cleaned = cleaned.replace("“", "").replace("”", "").replace("\"", "")
        cleaned = cleaned.replace("《", "").replace("》", "").replace("【", "").replace("】", "")
        cleaned = re.sub(r"\s+", "", cleaned).strip("，。！？；：:、- ")
        for suffix in self.WEAK_SUFFIXES:
            cleaned = cleaned.replace(suffix, "")
        return self._fit_title_length(cleaned)

    def _fit_title_length(self, title: str) -> str:
        max_length = max(1, int(self.settings.publishing.title_max_length))
        if len(title) <= max_length:
            return title
        # 与发布侧共用同一套语义边界回溯逻辑，避免两处行为漂移。
        return fit_title_to_limit(title, max_length)

    def _primary_topic_label(self, data_report: DataReport) -> str:
        topic_label = self._topic_subject_label(data_report, fallback="")
        if topic_label:
            return topic_label
        preferred = self.clean_publish_text(data_report.focus_industry)
        if preferred and not self.is_internal_label(preferred):
            return preferred

        candidates = self.clean_publish_list(
            data_report.focus_keywords + data_report.supply_chain_keywords,
            limit=10,
        )
        for item in candidates:
            if item and not self.is_internal_label(item):
                return item
        return candidates[0] if candidates else "科技制造"

    def _primary_company(self, data_report: DataReport) -> str:
        companies = self._company_candidates(data_report)
        if not companies:
            return ""

        factual_text = " ".join(
            str(item)
            for item in (
                data_report.topic_title,
                *(getattr(data_report, "source_facts", []) or []),
            )
            if str(item).strip()
        )
        generic_terms = {
            "机器人",
            "并联机器人",
            "工业机器人",
            "高速工业机器人",
            "人形机器人",
            "AI机器人",
            "机器人行业",
        }
        ranked = sorted(
            enumerate(companies),
            key=lambda item: (
                item[1] in factual_text,
                item[1] not in generic_terms,
                any(item[1].endswith(suffix) for suffix in ("技术", "科技", "自动化", "智能", "集团", "公司")),
                -item[0],
            ),
            reverse=True,
        )
        return ranked[0][1]

    def _primary_scene(self, content: GeneratedContent, data_report: DataReport) -> str:
        candidates = [
            content.summary,
            content.article,
            *data_report.core_data[:3],
            *data_report.evidence_notes[:3],
            data_report.topic_title,
        ]
        text = " ".join(self.clean_publish_text(item) for item in candidates if item)
        for token in self.SCENE_TOKENS:
            if token in text:
                index = text.find(token)
                start = max(0, index - 8)
                snippet = text[start : index + len(token)]
                snippet = re.sub(r"[（()）\[\]【】，。；：:、\s]+", "", snippet)
                if 2 <= len(snippet) <= 14:
                    return snippet
                return token
        return ""




