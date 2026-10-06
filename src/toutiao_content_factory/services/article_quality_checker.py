"""文章质量预检工具 - 头条首发激励合规性检查"""
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from toutiao_content_factory.services.company_entities import (
    COMPANY_SUFFIX_RE,
    extract_known_company_names,
    extract_wechat_company_names,
    is_wechat_company_name,
    is_valid_company_name,
    normalize_company_name,
)
from toutiao_content_factory.services.subject_entities import (
    extract_known_organization_names,
    is_known_organization_name,
)


@dataclass
class QualityCheckResult:
    """质量检查结果"""
    is_passed: bool
    total_score: int
    issues: list[str]
    suggestions: list[str]
    compliance_score: int
    originality_score: int
    readability_score: int
    depth_score: int
    engagement_attractiveness_score: int = 0


@dataclass(frozen=True)
class FirstReleaseAssessment:
    """发布前可审计的头条首发激励质量评估。"""

    eligible: bool
    char_count: int
    minimum_char_count: int
    standards_covered: int
    minimum_standards: int
    quality_score: int
    covered_standards: tuple[str, ...]
    missing_standards: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "char_count": self.char_count,
            "minimum_char_count": self.minimum_char_count,
            "standards_covered": self.standards_covered,
            "minimum_standards": self.minimum_standards,
            "quality_score": self.quality_score,
            "covered_standards": list(self.covered_standards),
            "missing_standards": list(self.missing_standards),
            "reasons": list(self.reasons),
        }


@dataclass
class BodyIntegrityResult:
    """**正文自身**的完整性结论，与标题无关。

    ⚠️ 2026-09-17：这三类问题原本混在 `check_editorial_integrity()` 的返回值里，
    而该函数的调用方之一是 `TitleAgent.validate_title_compliance(title, body)` ——
    于是**正文自己的问题被算到每一条标题头上**。实测：某篇正文命中
    `_BODY_FRAGMENT_PATTERN`（误报），结果该选题 39/39 条标题候选全部因
    「编辑一致性」被拒。正文有问题是**这篇稿子**的问题，不是标题的问题。
    """

    passed: bool
    issues: list[str]
    matched_fragments: list[str]


@dataclass
class TitleBodyConsistencyResult:
    """**标题与正文之间**的一致性结论，只看标题自己有没有问题。

    输入标题 + 正文，只回答：标题提到的主体是否在正文里、标题数字是否有正文依据。
    绝不因为「正文自身存在 fragment / 占位 / HTML 泄漏」而判失败。
    """

    passed: bool
    issues: list[str]


class ArticleQualityChecker:
    """文章质量预检器"""

    # 2026-05-25 版平台规则要求首发正文超过 100 字。平台资格线只负责
    # 判断能否申报；内部仍以 1500 字左右的完整叙事稿作为深度目标。
    FIRST_RELEASE_MIN_CHARS = 101
    FIRST_RELEASE_MIN_STANDARDS = 4
    NARRATIVE_EXEMPT_TITLE_MARKERS = (
        "RULE_009",
        "信息密度不足",
        "RULE_006",
        "具象",
    )

    _STORY_BUSINESS_DRIFT_MARKERS = (
        "ipo", "出货量", "市场份额", "毛利率", "经营现金流", "估值",
        "产能", "订单落地", "商业化", "产品交付", "资本市场",
    )

    @classmethod
    def check_topic_domain_consistency(cls, content: str, topic_type: str) -> list[str]:
        """Require human review when a social story turns into company analysis."""
        if str(topic_type or "").strip().lower() not in {"social_story", "narrative"}:
            return []
        folded = str(content or "").casefold()
        hits = [marker for marker in cls._STORY_BUSINESS_DRIFT_MARKERS if marker in folded]
        if len(hits) < 3:
            return []
        return [
            "社会故事稿混入企业经营或资本市场分析："
            + "、".join(hits[:4])
            + "。需核对选题与正文后重写。"
        ]

    # 首发激励计划要求的五大质量标准关键词
    QUALITY_KEYWORDS = {
        "首发时效": ["第一时间", "及时", "最新", "刚刚", "刚刚发布", "最新消息", "今日", "今天", "昨日", "近日", "本周"],
        "原创深度": ["观点", "视角", "思考", "判断", "分析", "见解", "洞察"],
        "信息增量": ["原因", "影响", "意义", "背后", "深层", "实质", "核心"],
        "创作规范": ["根据", "据", "基于", "数据", "显示", "统计", "来源", "回应", "通报", "报道"],
        "用户价值": ["共鸣", "连接", "触动", "启发", "收获", "价值", "提醒", "建议", "可以", "如何"]
    }

    # 需要避免的模板化表达
    TEMPLATE_PATTERNS = [
        r"不是.*而是.*",
        r"不只是.*而是.*",
        r"真正.*是.*",
        r"关键.*不在.*而在.*",
        r"这意味着.*",
        r"这说明.*",
        r"可以看出.*",
        r"从中可以.*"
    ]
    # 预编译模板正则表达式
    _TEMPLATE_PATTERNS_COMPILED = [re.compile(pattern) for pattern in TEMPLATE_PATTERNS]

    # 违规风险词汇
    # "第一条/第一步/第一阶段" are ordinary ordinal phrases, while an
    # unqualified "第一" is still treated as a potentially absolute claim.
    _FIRST_ORDINAL_SUFFIXES = (
        "\u65f6\u95f4", "\u7ec4", "\u6761", "\u6b65", "\u7c7b", "\u9879", "\u70b9",
        "\u90e8\u5206", "\u9636\u6bb5", "\u8f6e", "\u53e5", "\u5f20", "\u4ef6", "\u6b3e",
        "\u4ee3", "\u5c42", "\u73af\u8282", "\u65b9\u9762", "\u79cd", "\u4e2a", "\u6b21",
        "\u4f4d", "\u7bc7", "\u7ae0", "\u6ce2", "\u5b63\u5ea6", "\u5468", "\u5929", "\u5e74",
    )

    RISKY_WORDS = [
        "绝对", "完全", "一定", "必然", "肯定", "无疑",
        "首次", "第一", "首创", "独家",
        "最好", "最差", "最强", "最弱",
        "一定成功", "绝对赚钱", "保证收益"
    ]

    # 标题党特征
    CLICKBAIT_PATTERNS = [
        r"竟然.*了",
        r"居然.*了",
        r"震惊.*",
        r"不可思议.*",
        r"没想到.*",
        r"太.*了",
        r"必须.*",
        r"一定要.*"
    ]
    # 预编译标题党正则表达式
    _CLICKBAIT_PATTERNS_COMPILED = [re.compile(pattern) for pattern in CLICKBAIT_PATTERNS]
    _UNSUPPORTED_FINANCIAL_LINK_PATTERNS = (
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

    AI_CLICHE_PHRASES = [
        "正如我们所知",
        "在当今快节奏的世界里",
        "如前所述",
        "需要指出的是",
        "不言而喻",
        "众所周知的事实",
        "显而易见的是",
        "不难发现",
        "众所周知",
        "由此可见",
        "显而易见",
        "毋庸置疑",
    ]

    SOURCE_MARKER_PATTERNS = [
        r"对标账号高热内容",
        r"对标热点",
        r"36氪头条号",
        r"benchmark_hotspot",
        r"evergreen_pool",
        r"evergreen://[^\s，。]*",
        r"(?m)^\s*(?:作者|编辑|责编|责任编辑|撰文|审校|整理)\s*[|｜：:]",
        r"(?:公众号|微信号|微博|抖音号)\s*[：:]",
        r"(?m)^\s*(?:关注我|欢迎关注|关注本号|关注本账号|关注公众号|扫码关注)",
        r"(?m)^\s*#[^\s#]+(?:\s+#[^\s#]+)*\s*$",
    ]
    # 预编译来源标记正则表达式
    _SOURCE_MARKER_PATTERNS_COMPILED = [re.compile(pattern) for pattern in SOURCE_MARKER_PATTERNS]
    
    # 预编译数字百分比模式
    _NUMBER_PATTERN_COMPILED = re.compile(r"\d+\.?\d*%")
    
    # 数据关键词列表（用于合规性检查）
    _DATA_KEYWORDS = ["数据", "统计", "调查", "报告", "研究", "显示", "表明"]
    # 来源表达关键词列表（用于合规性检查）
    _SOURCE_KEYWORDS = ["根据", "基于", "来源", "引自", "报道", "指出", "透露"]
    # 观点表达关键词列表（用于原创性检查）
    _OPINION_WORDS = ["认为", "觉得", "看来", "判断", "观点", "视角"]
    # 独特观点词汇列表
    _UNIQUE_VIEW_WORDS = ["与众不同", "独特", "创新", "突破", "另辟蹊径", "新视角", "新思路"]
    # 分析洞察词汇列表
    _ANALYSIS_WORDS = ["分析", "洞察", "剖析", "解读", "探讨", "研究"]
    # 因果分析词汇列表
    _CAUSAL_WORDS = ["因此", "所以", "导致", "造成", "因为", "由于", "结果"]
    # 行业术语列表
    _INDUSTRY_TERMS = ["产业链", "上游", "下游", "产能", "毛利率", "营收", "净利润", "市占率", "渗透率"]
    # 深度因果词汇列表
    _DEPTH_CAUSAL_WORDS = ["根本原因", "核心驱动", "关键变量", "底层逻辑", "边际变化", "拐点"]

    # === 互动吸引力维度配置 ===
    # 互动吸引力-公司名检测正则（英文大写开头2-6字母 / 中文2-6字+公司后缀）
    _ENGAGEMENT_COMPANY_PATTERN = re.compile(
        r"[A-Z][a-zA-Z]{1,5}"
        r"|[\u4e00-\u9fa5]{2,6}(?:公司|集团|科技|智能|机器人|半导体|能源|电子|汽车|制药|生物|医疗|航空|航天|通信|网络|软件|控股|资本|证券|银行|保险)"
    )
    # 互动吸引力-数字检测正则
    _ENGAGEMENT_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
    # 互动吸引力-对立观点关键词
    _ENGAGEMENT_OPPOSITION_WORDS = ["然而", "但是", "另一方面", "相反", "不过", "与此相对", "反之", "尽管如此"]
    # 互动吸引力-互动话术模板正则（预编译）
    _ENGAGEMENT_TEMPLATE_PATTERNS_COMPILED = [
        re.compile(r"你更看好.+还是"),
        re.compile(r"你更倾向.+还是"),
        re.compile(r"背后.+哪个变量"),
        re.compile(r"哪个变量.+决定"),
        re.compile(r"如果.+是否"),
        re.compile(r"假如.+是否会"),
        re.compile(r"在你的行业里"),
        re.compile(r"在你的领域里"),
        re.compile(r"你所在行业"),
        re.compile(r"(?:你觉得|你怎么看|你认为|在你看来|你会选择).{0,80}[？?]"),
        re.compile(r"(?:后面|接下来|下一步|后续).{0,30}(?:最先|首先|哪个|什么|如何|是否|会不会|值得).{0,40}[？?]"),
    ]

    def __init__(self, *, wechat_mode: bool = False):
        self.wechat_mode = wechat_mode
        self.min_word_count = 1500
        self.max_template_usage = 3

    def check_article(
        self,
        title: str,
        content: str,
        check_first_publish: bool = True
    ) -> QualityCheckResult:
        """
        检查文章质量和合规性

        Args:
            title: 文章标题
            content: 文章正文
            check_first_publish: 是否检查首发激励合规性

        Returns:
            QualityCheckResult: 检查结果
        """
        issues = []
        suggestions = []
        content_lower = content.lower()  # 预计算一次，避免重复
        # 预计算段落和字数，避免重复计算
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        word_count = len(content.replace(" ", "").replace("\n", ""))

        # 1. 基本检查
        if word_count < self.min_word_count:
            issues.append(f"字数不足{self.min_word_count}字")
        elif word_count < 300:
            suggestions.append("建议文章字数达到300字以上以提升阅读体验")

        # 2. 模板化检查
        template_count = self._count_templates(content)
        if template_count > self.max_template_usage:
            issues.append(f"模板化表达过多（{template_count}处），建议增加表达多样性")
            suggestions.append("可以使用不同的开头和结尾方式，避免固定句式")
        elif template_count > 1:
            suggestions.append("存在少量模板化表达，可以适当优化")

        # 3. 首发激励合规性检查
        compliance_score = 0
        if check_first_publish:
            compliance_result = self._check_compliance(content, content_lower)
            compliance_score = compliance_result["score"]
            issues.extend(compliance_result["issues"])
            suggestions.extend(compliance_result["suggestions"])

            # 平台当前的首发正文门槛是“超过 100 字”。低于资格线只取消
            # 首发声明；普通发布下限仍由 AppService 按内容体裁判断。
            if word_count < self.FIRST_RELEASE_MIN_CHARS:
                suggestions.append(
                    f"字数{word_count}字，未达到头条首发正文超过100字的要求，本篇将不声明首发"
                    "（不影响正常发布与阅读收益）"
                )
            has_data_ref = self._has_any_keyword(content_lower, self._DATA_KEYWORDS)
            if not has_data_ref:
                suggestions.append("建议增加数据引用以提升首发激励合规性")
            has_source_expr = self._has_any_keyword(content_lower, self._SOURCE_KEYWORDS)
            if not has_source_expr:
                suggestions.append("建议增加来源感表达以提升首发激励合规性")

        # 4. 原创性检查
        originality_score = self._check_originality(content, title, content_lower, paragraphs)

        # 5. 可读性检查
        readability_score = self._check_readability(content, paragraphs)

        # 6. 深度检查
        depth_score = self._check_depth(content)

        # 7. 标题检查（传入content用于标题-正文一致性校验）
        title_issues = self.check_title(title, content)
        issues.extend(title_issues["issues"])
        suggestions.extend(title_issues["suggestions"])

        # 8. 风险词汇检查
        risk_issues = self._check_risky_words(content_lower)
        issues.extend(risk_issues)

        # 9. 来源标记泄露检查
        source_marker_issues = self._check_source_markers(content)
        issues.extend(source_marker_issues)

        # 10. 成稿完整性检查
        issues.extend(self._check_body_integrity(content))

        # 11. 互动吸引力检查
        engagement_result = self._check_engagement_attractiveness(content)
        engagement_attractiveness_score = engagement_result["score"]
        issues.extend(engagement_result["warnings"])

        # 计算总分
        total_score = (
            (25 if word_count >= self.min_word_count else 0) +
            (15 if template_count <= self.max_template_usage else 0) +
            compliance_score +
            originality_score +
            readability_score +
            depth_score
        )

        if source_marker_issues:
            total_score = max(0, total_score - 20)

        is_passed = len([i for i in issues if "建议" not in i]) <= 2

        return QualityCheckResult(
            is_passed=is_passed,
            total_score=min(100, total_score),
            issues=issues,
            suggestions=suggestions,
            compliance_score=compliance_score,
            originality_score=originality_score,
            readability_score=readability_score,
            depth_score=depth_score,
            engagement_attractiveness_score=engagement_attractiveness_score,
        )

    def _count_templates(self, content: str) -> int:
        """统计模板化表达使用次数"""
        count = 0
        for pattern in self._TEMPLATE_PATTERNS_COMPILED:
            matches = pattern.findall(content)
            count += len(matches)
        return count

    #: 叙事/故事体裁的结尾与用语特征。用于把「首发激励资格」类判定降级为建议。
    #:
    #: ⚠️ 2026-09-19：`_check_compliance` 查的是**头条首发激励的五大质量标准**
    #: （首发时效 / 原创深度 / 信息增量 / 创作规范 / 用户价值），这套画像本来就是
    #: 给"产业分析稿"写的 —— 故事体裁不讲"信息增量""创作规范（数据、统计）"，
    #: 天然只能覆盖 2 项左右，于是固定产出「内容质量标准覆盖不足」这条 issue。
    #: 它被 `is_passed = 非建议类问题 <= 2` 计入，再叠上字数那条，
    #: 一篇正常的叙事稿必然 `is_passed=False` → `publish_service` 直接拒绝发布。
    #: 两处口径统一为：**拿不到首发激励是"少赚"，不是"不能发"**，
    #: 所以这类判定在叙事体裁下只作为建议。
    NARRATIVE_MARKERS = (
        "换成是你",
        "你会怎么处理",
        "当事人",
        "人情",
        "家长里短",
        "邻里",
        "赡养",
        "离婚",
    )

    def _looks_like_narrative(self, content: str) -> bool:
        sample = str(content or "")
        if not sample:
            return False
        hits = sum(1 for marker in self.NARRATIVE_MARKERS if marker in sample)
        return hits >= 2

    def _covered_quality_standards(self, content_lower: str) -> tuple[str, ...]:
        return tuple(
            standard
            for standard, keywords in self.QUALITY_KEYWORDS.items()
            if any(keyword in content_lower for keyword in keywords)
        )

    def assess_first_release(self, content: str) -> FirstReleaseAssessment:
        """判断正文是否值得启用首发声明，并返回具体缺项。"""
        normalized = re.sub(r"\s+", "", str(content or ""))
        covered = self._covered_quality_standards(str(content or "").lower())
        missing = tuple(standard for standard in self.QUALITY_KEYWORDS if standard not in covered)
        reasons: list[str] = []
        if len(normalized) < self.FIRST_RELEASE_MIN_CHARS:
            reasons.append(
                f"正文需超过100字（当前{len(normalized)}字）"
            )
        if len(covered) < self.FIRST_RELEASE_MIN_STANDARDS:
            reasons.append(
                f"五项首发质量标准仅覆盖{len(covered)}项，至少需要{self.FIRST_RELEASE_MIN_STANDARDS}项"
            )
        if self._check_source_markers(content):
            reasons.append("正文仍含内部来源标记")
        return FirstReleaseAssessment(
            eligible=not reasons,
            char_count=len(normalized),
            minimum_char_count=self.FIRST_RELEASE_MIN_CHARS,
            standards_covered=len(covered),
            minimum_standards=self.FIRST_RELEASE_MIN_STANDARDS,
            quality_score=len(covered) * 20,
            covered_standards=covered,
            missing_standards=missing,
            reasons=tuple(reasons),
        )

    def _check_compliance(self, content: str, content_lower: str) -> dict:
        """检查首发激励合规性"""
        issues = []
        suggestions = []
        score = 0
        narrative = self._looks_like_narrative(content)

        # 检查五大质量标准覆盖情况
        standards_covered = len(self._covered_quality_standards(content_lower))

        if standards_covered >= 4:
            score += 30
        elif standards_covered >= 3:
            score += 20
            suggestions.append("建议加强内容深度，增加更多信息增量")
        elif standards_covered >= 2:
            score += 10
            # 故事体裁：这是"首发激励资格不足"，不是"文章不合格"
            target = suggestions if narrative else issues
            target.append("内容质量标准覆盖不足" if not narrative else "内容未覆盖首发激励的多数质量标准，本篇将不声明首发")
            suggestions.append("建议增加原创观点和深度分析")
        else:
            target = suggestions if narrative else issues
            target.append(
                "严重不足：缺少五大质量标准的覆盖"
                if not narrative
                else "未覆盖首发激励的质量标准，本篇将不声明首发"
            )
            suggestions.append("需要在首发时效、原创深度、信息增量、创作规范、用户价值等方面全面提升")

        # 检查信息来源
        has_source = any(kw in content_lower for kw in self._DATA_KEYWORDS)
        if has_source:
            score += 10
        else:
            suggestions.append("建议增加数据来源支撑，提高内容权威性")

        # 检查原创表达
        has_opinion = any(kw in content_lower for kw in self._OPINION_WORDS)
        if has_opinion:
            score += 10
        else:
            suggestions.append("建议增加作者个人观点和独特视角")

        return {
            "score": score,
            "issues": issues,
            "suggestions": suggestions
        }

    def _check_originality(self, content: str, title: str, content_lower: str, paragraphs: list[str]) -> int:
        """检查原创性"""
        score = 0

        # 检查是否有独特观点
        if self._has_any_keyword(content_lower, self._UNIQUE_VIEW_WORDS):
            score += 10

        # 检查是否有分析洞察
        if self._has_any_keyword(content_lower, self._ANALYSIS_WORDS):
            score += 15

        # 检查是否有因果分析
        if self._has_any_keyword(content_lower, self._CAUSAL_WORDS):
            score += 10

        # 检查段落数量（使用预计算的 paragraphs）
        if len(paragraphs) >= 5:
            score += 15
        elif len(paragraphs) >= 3:
            score += 10
        else:
            score += 5

        return min(50, score)

    def _check_readability(self, content: str, paragraphs: list[str]) -> int:
        """检查可读性"""
        score = 0

        # 检查段落长度（使用预计算的 paragraphs）
        avg_length = sum(len(p) for p in paragraphs) / len(paragraphs) if paragraphs else 0

        if avg_length <= 150:
            score += 20
        elif avg_length <= 200:
            score += 15
        elif avg_length <= 300:
            score += 10
        else:
            score += 5

        # 检查标点符号
        if "。" in content:
            score += 10

        # 检查是否有英文过多
        english_ratio = sum(1 for c in content if c.isascii()) / len(content) if content else 0
        if english_ratio < 0.1:
            score += 10
        elif english_ratio < 0.2:
            score += 5

        max_block_length = 0
        for paragraph in paragraphs:
            if len(paragraph) > max_block_length:
                max_block_length = len(paragraph)
        if max_block_length > 150:
            score -= 10

        return min(40, score)

    # RULE_007: 标题模板滥用检测模板
    _TITLE_TEMPLATE_PATTERNS = [
        re.compile(r".+之后，.+(开始|进入|先)"),
        re.compile(r".+验证期[，,].+先流向"),
        re.compile(r".+之后，.+重排利润"),
        re.compile(r".+筛选期$"),
        # 2026-08-24 新增：抽象悬念开头（头条热榜对标发现"有曝光无点击"的高频失败模式）
        re.compile(r"^(.+)?背后，"),
        re.compile(r"谁在抢跑$"),
        re.compile(r"利润先流向谁$"),
        re.compile(r"真正的收入来自什么$"),
        re.compile(r"^(90%|.*\d+%)背后"),
        # 2026-09-16 新增：「…：X如何实测」这一族。三份证据都指向它是零阅读句式 ——
        #   ① `feedback_loop_service` 的注释点名「XX亿：XX如何实测」是「0 阅读但展现 300-470」的模板；
        #   ② 账号历史实绩：「基地10亿：机器人如何实测」10 阅读、「月之暗面等大模型1240亿：大模型如何实测」21 阅读；
        #   ③ 这两句正是 `title_agent._fallback_fact_titles` 硬编码的兜底模板，而它被**塞在
        #      整个候选列表最前面**，`select_auto_title` 又取「第一个通过的」→ 必然被选中。
        #      2026-09-16 云端发布的就是它（`titles.txt` 候选 #1）。
        # 注意：`实测` 这个词本身在 RULE_009 的动作词表里，所以这类标题以前能顺利过闸。
        re.compile(r"如何实测$"),
        re.compile(r"项目实测$"),
        re.compile(r"如何验证$"),
        re.compile(r"下一步看什么$"),
        re.compile(r".{0,12}实测[：:].{0,12}$"),
    ]

    # 2026-09-16：按实测数据排候选点击吸引力的偏好打分（不参与「是否合格」判定）。
    #
    # 依据（`docs/TITLE_STRATEGY_20260916.md`，94 条有效作品的 CTR 分布）：
    # - 高 CTR 共性：**下判断 / 设问带前提 / 反直觉 / 短**，头部 15 条平均 20.1 字；
    # - 零阅读模式：模糊主体、纯陈述、**冒号+数字堆砌**、抽象名词罗列、硬凑标题党；
    # - 反面证据：命中「主体+数字+后果」四要素越多的组 CTR 越低
    #   （命中 3 项 0.00% vs 0 项 0.86%），所以**不能**用要素数量当排序键。
    # 权重刻意取得很小，只用来在**已通过闸门**的候选之间排序，不可能让不合格标题复活。
    _TITLE_JUDGMENT_PATTERN = re.compile(
        r"(才是|不在|不是.{1,8}而是|关键在于|先看|谁在|凭什么|够不够|能不能|卡在|落后于|[？?]$)"
    )
    _TITLE_FUTURE_PROMISE_PATTERN = re.compile(r"(谁将|将实现|有望|剑指|必将|一定)")
    _TITLE_COLON_ENUM_PATTERN = re.compile(r"：.*[，,].*[，,]")

    def title_skeleton(self, title: str) -> str:
        """取标题的「开头骨架」（前 12 字内、到第一个分隔符为止）。

        用来识别**候选塌缩**：2026-09-16 实测一次运行 13 个候选里 11 个骨架相同
        （全是「灵巧手产业：」），候选列表看着有 13 条、实际只有 1 个思路。
        提示词里第 19/20 条已明令禁止，但模型会无视，所以还需要代码侧兜底。
        """
        text = (title or "").strip()
        if not text:
            return ""
        head = text.split("：", 1)[0]
        for separator in ("，", ",", "。", "；", ";"):
            head = head.split(separator, 1)[0]
        return head.strip()[:12]

    def title_click_preference(self, title: str) -> int:
        """给出标题的「点击吸引力」相对分（越大越好），只用于候选排序。"""
        text = (title or "").strip()
        if not text:
            return -99
        score = 0
        length = len(text)
        if length <= 22:
            score += 2
        elif length > 28:
            score -= 2
        if self._TITLE_JUDGMENT_PATTERN.search(text):
            score += 2
        if self._TITLE_FUTURE_PROMISE_PATTERN.search(text):
            score -= 1
        if self._TITLE_COLON_ENUM_PATTERN.search(text):
            score -= 1
        if len(re.findall(r"\d", text)) >= 4:
            score -= 1
        return score

    # RULE_006: 具象元素检测
    _TITLE_COMPANY_PATTERN = re.compile(
        r"(宇树|特斯拉|英伟达|Optimus|澜起|富士康|宁德时代|比亚迪|华为|Walker|优必选|智元|Anthropic|OpenAI|苹果|Macmini|HBM|DDR5)"
    )
    _TITLE_PLACE_PATTERN = re.compile(r"(日本|中国|美国|韩国|德国|机场|工厂|电站|产线|海外)")
    _TITLE_SUBJECT_PATTERN = re.compile(
        r"(机器人|人形机器人|具身智能|灵巧手|宇树|特斯拉|英伟达|Optimus|澜起|富士康|宁德时代|比亚迪|华为|Walker|优必选|智元|Anthropic|OpenAI|苹果|Macmini|HBM|DDR5|波士顿动力)"
        # ⚠️ 2026-09-19：社会故事赛道的主体是**人和生活事件**，不是公司名。
        # 这条规则原先只认公司/机器人词，于是「张凯毅官宣离婚」「老人高空作业」
        # 这类标题全被判「纯抽象」，实测 18 条模型标题 + 3 条候选**全部被拒**，
        # 标题池输出 0 条、文章发不出去。
        # 下面这些是补充的主体词；对产业类标题没有影响（只加词、不减词）。
        r"|(当事|老人|老太|大爷|大妈|夫妻|妻子|丈夫|儿子|女儿|儿媳|女婿|婆婆|岳父|岳母|父母|"
        r"孩子|邻居|房东|租客|同事|老板|员工|农民工|保安|外卖员|快递员|司机|摊主|店主|村民"
        r"|离婚|结婚|再婚|相亲|彩礼|赡养|养老|退休|遗产|房产|物业|官司|判决|赔偿|欠薪|讨薪"
        r"|车祸|事故|身亡|受伤|获赔|救人|坠楼|溺水|走失|扶不扶|让座|吵架|纠纷)"
    )
    # A title must name a real subject when the article is about a specific
    # company. These placeholders are especially risky because a number can
    # make an otherwise empty title look concrete to a score-based gate.
    _GENERIC_TITLE_SUBJECT_PATTERN = re.compile(
        r"(?:^|[：:，,、\s（(])"
        r"((?:其他智能|其他机器人|某(?:智能|机器人)?(?:公司|企业|厂商|项目)?|"
        r"这家(?:公司|企业)|该(?:公司|企业)|相关(?:公司|企业|厂商)))"
    )
    _SOURCE_ATTRIBUTION_TITLE_PATTERN = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,16}旗下(?:"
        r"科技(?!公司|企业|业务|产品|品牌)|"
        r"媒体(?!公司|业务|品牌)|"
        r"资讯(?!公司|平台|业务)|"
        r"网站"
        r")"
    )
    _BODY_HTML_LEAK_PATTERN = re.compile(
        r"<!doctype\s+html\b|</?(?:html|head|body|script|style|div|table|tr|td)\b",
        re.IGNORECASE,
    )
    _BODY_TEMPLATE_LEAK_PATTERN = re.compile(
        r"\{\{[^{}\n]{1,120}\}\}|<%=?[^%\n]{1,120}%>|\[\[(?:[^\]\n]{1,120})\]\]"
    )
    _BODY_PLACEHOLDER_PATTERN = re.compile(
        r"(?i)(?:\b(?:todo|tbd|placeholder)\b|占位符|占位文本|待补充(?:数据|来源|内容)?|"
        r"请补充(?:数据|来源|内容)?|此处(?:补充|填写))"
    )
    # These are instructions to the writer, observed verbatim in published copy.
    # Keep the list narrow so ordinary reporting language remains publishable.
    _BODY_EDITORIAL_LEAK_PATTERN = re.compile(
        r"把素材里能确认的部分列一下|这些是目前能核实的范围|报道没有交代的部分不补|"
        r"主体层面可以先看.{0,80}订单或收入已经兑现|更硬的验证要落在"
    )
    # ⚠️ 2026-09-17（Blocker 2）：旧规则是
    #   `[\u4e00-\u9fffA-Za-z0-9]{2,24}、(?:为|是|在|与|和|并|但|或)`
    # 在 457 篇去重后的真实正文上实测：**命中 101 篇（22.10%）、114 次，
    # 且逐条看上下文后**全部是正常中文**（精度 0）。主因不是「顿号后接词」这件事，
    # 而是单字连接词会命中**更长的词内部**：
    #   - 178 次 `、并` 中绝大多数是 `订单、并网、交付`（`并` 是「并网」首字）；
    #   - 32 次 `、和`、8 次 `、与`、16 次 `、在` 全是并列项连接词
    #     （`在手订单规模`、`验证进度、与主流厂商`）；
    #   - 24 次 `、是` 里绝大多数是 `、是否完成客户验收`（`是` 是「是否」首字）。
    # 又经 `check_editorial_integrity` 的跨层泄漏，这 101 篇的**全部标题候选**都会被否掉。
    #
    # 收窄为两条**结构上成立**的信号（顿号不可能引出谓语；句子不可能以连接词收尾）：
    #   ① `、` + 系动词（为/是），但排除 `、是否`（正常并列疑问项）；
    #   ② 以连接词/介词收尾、且后面没有任何终句标点（正文/段落的悬空尾巴）。
    # 同一 457 篇语料上：**只剩 2 篇命中（0.44%），2 次全部是真残句**
    #   （`…智能机器人、为最主要的增长驱动力。`、`…提到小鹏、是因为以前那些机器人`）。
    # 既有回归夹具 `优必选、为最主要的增长驱动力…` 依然被 ① 捕获。
    # 已放弃的候选（噪声过大，数据留档在 docs/TITLE_GATE_BLOCKER_REANALYSIS_20260917.md）：
    #   「段落无终句标点」120/243、「。，标点错接」52/243、「悬空从句开头词」64/243、
    #   「、并（排除并网）」2/2 仍全是误报、「、为是」全量 26 次而其中 24 次是 `、是否`。
    _BODY_FRAGMENT_PATTERN = re.compile(
        r"、[为是](?!否)"
        r"|(?:并且|而且|同时|因此|所以|以及|或者|但是|不过|因为|如果|由于|虽然|即使|"
        r"只要|随着)[ \t]*"
        r"(?:\r?\n[ \t]*\r?\n|[ \t]*\Z)"
    )

    @classmethod
    def _extract_company_mentions(cls, text: str, *, wechat_mode: bool = False) -> list[str]:
        """Extract grounded-looking company names for title/body checks."""

        candidates: list[str] = []
        raw_text = str(text or "")
        extractor = extract_wechat_company_names if wechat_mode else extract_known_company_names
        validator = is_wechat_company_name if wechat_mode else is_valid_company_name
        for candidate in extractor(raw_text):
            normalized = normalize_company_name(candidate)
            if validator(normalized) and normalized not in candidates:
                candidates.append(normalized)
        for match in COMPANY_SUFFIX_RE.finditer(raw_text):
            normalized = normalize_company_name(match.group(0))
            if is_valid_company_name(normalized) and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    @classmethod
    def _extract_subject_mentions(cls, text: str, *, wechat_mode: bool = False) -> list[str]:
        subjects = cls._extract_company_mentions(text, wechat_mode=wechat_mode)
        for organization in extract_known_organization_names(text):
            if organization not in subjects:
                subjects.append(organization)
        return subjects

    @staticmethod
    def _company_matches(left: str, right: str) -> bool:
        left_folded = str(left or "").casefold()
        right_folded = str(right or "").casefold()
        return bool(
            left_folded
            and right_folded
            and (left_folded in right_folded or right_folded in left_folded)
        )

    @classmethod
    def _check_title_integrity(
        cls,
        title: str,
        content: str = "",
        expected_companies: Iterable[object] | None = None,
        *, wechat_mode: bool = False,
    ) -> list[str]:
        title_text = str(title or "").strip()
        if not title_text:
            return []

        issues: list[str] = []
        generic_subjects = [
            match.group(1)
            for match in cls._GENERIC_TITLE_SUBJECT_PATTERN.finditer(title_text)
            if match.group(1)
        ]
        title_companies = cls._extract_subject_mentions(title_text, wechat_mode=wechat_mode)
        body_text = str(content or "")
        if wechat_mode:
            body_text = body_text.split("参考来源：", 1)[0]
            body_text = re.sub(r"https?://[^\s<>\"']+", "", body_text)
        body_companies = cls._extract_subject_mentions(body_text, wechat_mode=wechat_mode)
        expected: list[str] = []
        for raw_company in expected_companies or ():
            normalized = normalize_company_name(raw_company)
            if (
                (is_wechat_company_name(normalized) if wechat_mode else is_valid_company_name(normalized))
                or is_known_organization_name(normalized)
            ) and normalized not in expected:
                expected.append(normalized)

        if generic_subjects:
            grounded = expected or body_companies
            if grounded:
                grounded_text = "、".join(grounded[:3])
                issues.append(
                    f"标题使用了模糊主体“{'、'.join(generic_subjects[:3])}”，"
                    f"但材料已有明确主体（{grounded_text}），不能用泛化主体替代。"
                )
            else:
                issues.append(
                    f"标题使用了无法回指事实的模糊主体“{'、'.join(generic_subjects[:3])}”，"
                    "请改为真实公司、项目或明确事件主体。"
                )

        if body_text or (wechat_mode and str(content or "").strip()):
            body_folded = (re.sub(r"\s+", "", body_text) if wechat_mode else body_text).casefold()
            for company in title_companies:
                if company.casefold() not in body_folded:
                    issues.append(
                        f"标题提及“{company}”但正文未出现，标题与正文主体不一致。"
                    )

            expected_in_body = [
                company
                for company in expected
                if company.casefold() in body_folded
            ]
            if expected_in_body and title_companies and not any(
                cls._company_matches(title_company, expected_company)
                for title_company in title_companies
                for expected_company in expected_in_body
            ):
                issues.append(
                    f"标题主体（{'、'.join(title_companies[:3])}）与数据报告确认主体"
                    f"（{'、'.join(expected_in_body[:3])}）不一致。"
                )

        return list(dict.fromkeys(issues))

    @classmethod
    def check_body_integrity(cls, content: str) -> BodyIntegrityResult:
        """只看正文自身是否有硬伤，包括编辑指令泄漏和长段重复。"""
        article = str(content or "").strip()
        if not article:
            return BodyIntegrityResult(passed=True, issues=[], matched_fragments=[])

        issues: list[str] = []
        matched_fragments: list[str] = []
        if cls._BODY_HTML_LEAK_PATTERN.search(article):
            issues.append("正文疑似泄露HTML标签或网页模板，必须清除后再发布。")
        if cls._BODY_TEMPLATE_LEAK_PATTERN.search(article):
            issues.append("正文疑似泄露模板占位语法，必须补全真实内容后再发布。")
        if cls._BODY_PLACEHOLDER_PATTERN.search(article):
            issues.append("正文仍包含待补充或占位文本，不能作为成稿发布。")
        if cls._BODY_EDITORIAL_LEAK_PATTERN.search(article):
            issues.append("正文泄露了写作或事实核对指令，必须改成面向读者的成稿。")
        if cls._repeated_long_span(article):
            issues.append("正文存在重复的长段表述，请合并或重写后再发布。")
        matched_fragments = [
            match.group(0) for match in cls._BODY_FRAGMENT_PATTERN.finditer(article)
        ]
        if matched_fragments:
            issues.append("正文存在悬空残句（以连接词收尾且无终句标点），请重写完整句子。")
        issues = list(dict.fromkeys(issues))
        return BodyIntegrityResult(
            passed=not issues, issues=issues, matched_fragments=matched_fragments
        )

    @staticmethod
    def _repeated_long_span(content: str, span_length: int = 70) -> str | None:
        """Find a repeated, non-overlapping prose span while ignoring spacing."""
        compact = re.sub(r"[\W_]+", "", content, flags=re.UNICODE)
        seen: dict[str, int] = {}
        for index in range(len(compact) - span_length + 1):
            span = compact[index:index + span_length]
            previous = seen.setdefault(span, index)
            if index - previous >= span_length:
                return span
        return None

    @classmethod
    def _check_body_integrity(cls, content: str) -> list[str]:
        """向后兼容的薄封装：只返回 issues 文案。"""
        return cls.check_body_integrity(content).issues

    @classmethod
    def check_title_body_consistency(
        cls,
        title: str,
        content: str,
        expected_companies: Iterable[object] | None = None,
        *, wechat_mode: bool = False,
    ) -> TitleBodyConsistencyResult:
        """只看**标题与正文之间**的一致性，不掺入正文自身的完整性问题。"""
        issues = cls._check_title_integrity(title, str(content or ""), expected_companies, wechat_mode=wechat_mode)
        return TitleBodyConsistencyResult(passed=not issues, issues=issues)

    def check_editorial_integrity(
        self,
        title: str,
        content: str,
        expected_companies: Iterable[object] | None = None,
        require_body: bool = False,
    ) -> dict[str, list[str]]:
        """Return deterministic hard-quality findings shared by publish gates.

        ⚠️ 返回的 `issues` 是**两层合并**的结果（正文层 + 标题层），这是给「整篇稿子」
        用的闸门（发布前、内容守卫）看的。**标题层的调用方不要用它** ——
        正文自己的问题会污染标题判定。标题层请用
        `check_title_body_consistency()`。

        2026-09-17 之前，`TitleAgent.validate_title_compliance` 正是误用了这个合并结果，
        导致一篇正文触发悬空残句规则时，该选题 39/39 条标题候选全部被拒。
        现在返回值里额外带上 `body_integrity` / `title_body_consistency` 两个分层结果，
        便于调用方各取所需（对外键名保持不变）。
        """

        consistency = self.check_title_body_consistency(title, content, expected_companies, wechat_mode=self.wechat_mode)
        body_integrity = self.check_body_integrity(content)
        title_issues = consistency.issues
        body_issues = body_integrity.issues
        hard_blockers = [*title_issues, *body_issues]
        if require_body and not str(content or "").strip():
            hard_blockers.insert(0, "正文为空，不能进入发布流程。")
        hard_blockers = list(dict.fromkeys(hard_blockers))
        return {
            "issues": hard_blockers,
            "hard_blockers": hard_blockers,
            "title_issues": title_issues,
            "body_issues": body_issues,
            "body_integrity": {
                "passed": body_integrity.passed,
                "issues": body_integrity.issues,
                "matched_fragments": body_integrity.matched_fragments,
            },
            "title_body_consistency": {
                "passed": consistency.passed,
                "issues": consistency.issues,
            },
        }

    def check_title(
        self,
        title: str,
        content: str = "",
        expected_companies: Iterable[object] | None = None,
        *,
        content_domain: str = "industry",
    ) -> dict:
        """Run the shared title gate and apply domain-specific policy once.

        ``raw_issues`` keeps the unfiltered result for ranking and audit logs;
        ``issues`` is the publish decision for the requested content domain.
        """
        result = self._check_title(
            title,
            content,
            expected_companies=expected_companies,
        )
        raw_issues = [str(item) for item in result.get("issues", []) if str(item).strip()]
        issues = raw_issues
        if str(content_domain or "industry").strip().lower() == "narrative":
            issues = [
                item
                for item in raw_issues
                if not any(marker in item for marker in self.NARRATIVE_EXEMPT_TITLE_MARKERS)
            ]
        return {**result, "raw_issues": raw_issues, "issues": issues}

    def _check_title(
        self,
        title: str,
        content: str = "",
        expected_companies: Iterable[object] | None = None,
    ) -> dict:
        """检查标题质量

        增强（RULE_006/007/008 落地）：
        - 模板滥用硬校验：检测"XX之后，XX开始/进入/先"等模板
        - 纯抽象标题硬校验：必须包含具象数字、公司名或地名
        - 标题-正文关键词一致性：标题中的核心实体应在正文中出现
        """
        issues = []
        suggestions = []
        title_lower = title.lower()

        # 检查标题党
        for pattern in self._CLICKBAIT_PATTERNS_COMPILED:
            if pattern.search(title_lower):
                issues.append("标题存在标题党嫌疑")
                break
        if any(pattern.search(title) for pattern in self._UNSUPPORTED_FINANCIAL_LINK_PATTERNS):
            issues.append("标题把财务指标与批量交付或规模化结果直接绑定，缺少证据边界")

        # 检查标题长度（头条实测爆款标题可达 38 字，上限放宽到 40 避免误杀高信息密度标题）
        title_len = len(title)
        if title_len > 40:
            issues.append("标题过长，可能影响展示")
            suggestions.append("建议标题控制在40字以内")
        elif title_len < 10:
            issues.append("标题过短，信息量不足")
            suggestions.append("建议标题至少10个字")

        # 检查是否有数字
        if not any(c.isdigit() for c in title):
            suggestions.append("标题中可以使用数字吸引点击（如'3大趋势'）")

        # RULE_007: 模板滥用硬校验
        for pattern in self._TITLE_TEMPLATE_PATTERNS:
            if pattern.search(title):
                issues.append(
                    f"标题使用了滥用模板（RULE_007）。历史数据：同模板标题CTR从3.2%降至0.4%。"
                    "请改用差异化句式。"
                )
                break
        if self._SOURCE_ATTRIBUTION_TITLE_PATTERN.search(title):
            issues.append(
                "标题把素材来源或媒体归属描述当成主体，必须改为选题中的真实公司、产品或事件主体。"
            )

        # RULE_006: 纯抽象标题硬校验（主体词含"机器人/具身智能"等）
        has_number = bool(re.search(r"\d", title))
        has_company = bool(
            self._TITLE_SUBJECT_PATTERN.search(title)
            or extract_known_organization_names(title)
            or (self.wechat_mode and self._extract_company_mentions(title, wechat_mode=True))
        )
        has_place = bool(self._TITLE_PLACE_PATTERN.search(title))
        if not has_number and not has_company and not has_place:
            issues.append(
                "标题为纯抽象概念堆砌（RULE_006）。历史数据：具象标题平均展现6806，"
                "纯抽象标题仅1753，差距3.9倍。必须加入具象数字、公司名或地名。"
            )

        # Do not let a number make an otherwise vague placeholder subject
        # look concrete, e.g. “其他智能12.7亿：机器人如何实测”.
        issues.extend(
            self._check_title_integrity(
                title,
                content,
                expected_companies=expected_companies,
                wechat_mode=self.wechat_mode,
            )
        )

        # RULE_009: 高阅读四要素校验（2026-08-25 头条真实数据：1.3万 vs 23 阅读）
        # 标题必须含"主体/动作/数字/结果"中至少 2 项，否则信息密度不足、点击率极低
        # 2026-09-15 扩充词表：原词表过窄，导致"出货量…8万台""产能210万套…激增"
        # 这类含具体数字的合格标题也被误判为信息密度不足而遭清空，直接阻断发布。
        _ACTION_PATTERN = re.compile(
            r"(夺冠|跳远|跑完|飞起|进厂|开工|敬礼|复刻|跌破|暴跌|暴涨|降价|量产|上市|发布|连发|印发|出台|亮相|"
            r"超越|刷新|纪录|签约|交付|订单|跌去|跌逾|降超|演示|实测|开跑|"
            # 增补：出货/营收/投产/融资等常见经营动作
            r"出货|交付量|营收|收入|净利|毛利|亏损|扭亏|投产|下线|扩产|减产|涨价|提价|降本|"
            r"融资|上市|并购|收购|入股|投资|合作|中标|拿下|推出|开售|预售|开卖|涨价|控价|"
            r"下滑|下降|增长|上升|提升|扩大|缩小|受益|承压|遇冷|升温|回暖|提速|放缓|翻倍|"
            r"规划|布局|加码|转向|切入|卡位|突围|洗牌|分化|重估|"
            # ⚠️ 2026-09-19：社会故事体裁的动作词。
            # 上面全是经营动作（交付/量产/出货…），社会故事讲的是人的行为与遭遇，
            # 于是「张凯毅官宣离婚」「绳子被邻居剪断」这类标题凑不齐「主体+动作」
            # 两项，被 RULE_009 判信息密度不足 —— 实测 18 条模型标题全被拒、标题池为 0。
            r"离婚|结婚|再婚|官宣|分手|复合|起诉|控告|报警|判决|判赔|获赔|索赔|赔偿|"
            r"剪断|割断|推倒|打伤|砸伤|撞伤|撞死|身亡|去世|离世|走失|失踪|失联|坠楼|跳楼|"
            r"溺水|触电|中毒|摔倒|昏倒|抢救|送医|救人|施救|救出|抱出|抬出|扶起|背出|"
            r"捡到|捡走|拿走|偷走|骗走|骗局|被骗|被讹|讹人|逃逸|拒赔|赖账|欠钱|欠薪|讨薪|"
            r"举报|投诉|曝光|维权|上诉|开庭|调解|和解|退还|退款|罚款|被罚|处罚|拘留|判刑|"
            r"撒谎|反悔|变卦|闹上法庭|反目|翻脸|断绝|认亲|寻亲|团圆|重逢|拍照|弹出|打断)"
        )
        _RESULT_PATTERN = re.compile(
            r"(冠军|纪录|跌破|暴跌|暴涨|降价|跌去|跌逾|降超|突破|超越|第一|新纪录|创纪录|里程碑|"
            r"首台|首批|量产|交付|签约|回落|蒸发|"
            # 增补：常见结果/后果词
            r"激增|骤降|腰斩|翻倍|扭亏|亏损|承压|遇冷|升温|回暖|提速|放缓|下滑|下降|增长|上升|"
            r"受益|洗牌|分化|重估|卡位|突围|过剩|紧缺|紧张|改善|恶化|触底|见顶|见底|扩大|缩小|"
            # ⚠️ 2026-09-19：社会故事的结果词（同上，补齐「主体+结果」两项）
            r"获赔|判赔|赔偿|赔了|赔钱|胜诉|败诉|被判|判刑|获刑|拘留|罚款|被罚|处罚|"
            r"身亡|去世|离世|失踪|失联|得救|获救|脱险|生还|无效|康复|出院|痊愈|"
            r"追回|要回|讨回|退还|退款|道歉|认错|和解|调解成功|找回|寻回|归还|返还|"
            r"离婚|净身出户|分得|归女方|归男方|抚养权|断绝关系|反目成仇)"
        )
        element_count = sum(
            [
                bool(has_number or has_company or has_place),
                bool(_ACTION_PATTERN.search(title) or (self.wechat_mode and re.search(r"预告|预览|披露|测试|验证|部署", title))),
                bool(_RESULT_PATTERN.search(title)),
            ]
        )
        if element_count < 2:
            issues.append(
                "标题信息密度不足（RULE_009）。头条实测：同一事件完整标题（主体+数字+后果）阅读1.3万，"
                "简略标题仅23阅读。必须包含具体主体/数字 + 强动作或结果词。"
            )

        # 标题-正文一致性校验：标题中的核心实体应在正文中出现
        if content:
            # 提取标题中的公司名
            title_companies = self._TITLE_COMPANY_PATTERN.findall(title)
            for company in title_companies:
                if company not in content:
                    issues.append(
                        f"标题提及'{company}'但正文未出现，标题-正文不一致会导致阅读完成率骤降"
                    )
            # 提取标题中的数字
            title_numbers = re.findall(r"\d+", title)
            for number in title_numbers:
                if number not in content:
                    suggestions.append(
                        f"标题中的数字'{number}'未在正文中出现，建议补充对应数据支撑"
                    )

        return {"issues": issues, "suggestions": suggestions}

    def _check_risky_words(self, content_lower: str) -> list:
        """检查违规风险词汇。

        2026-09-16 修掉两个会削弱这道防线的坑：
        1. 「一定」此前没有任何限定词排除，于是「存在一定的风险」「一定程度上」
           这类**正常表达**被误报成绝对化表述（9-16 那篇储能稿就中招：正文
           写「也存在一定的风险」，告警却报"存在绝对化表述：'一定'"）。
        2. 命中即 `break`，于是**排在表前面的误报会把后面真正的违规词挤掉**
           ——「最好」「最强」「保证收益」这些反而永远报不出来。
        现在改为：按词收集全部命中（限定语排除后），再按「长词优先」去掉被
        包含的短词，避免同一处重复报。
        """
        hits: list[str] = []

        for word in self.RISKY_WORDS:
            if word == "\u7b2c\u4e00" and not self._contains_unqualified_first(content_lower):
                continue
            if word == "\u4e00\u5b9a" and not self._contains_unqualified_certainty(content_lower):
                continue
            if word in content_lower:
                hits.append(word)

        # 「绝对赚钱」命中时已隐含「绝对」，不必对同一处报两次。
        de_duplicated = [
            word
            for word in hits
            if not any(word != other and word in other for other in hits)
        ]
        return [f"存在绝对化表述：'{word}'" for word in de_duplicated]

    # 「一定」后面跟这些词时是「某种程度」的限定语，不是绝对化断言：
    # 「一定的风险」「一定程度上」「起一定作用」「有一定难度」。
    _CERTAINTY_LIMITER_SUFFIXES = (
        "\u7684", "\u5730", "\u7a0b\u5ea6", "\u4f5c\u7528", "\u6761\u4ef6",
        "\u5f71\u54cd", "\u8303\u56f4", "\u6bd4\u4f8b", "\u65f6\u95f4", "\u89c4\u6a21",
        "\u6570\u91cf", "\u96be\u5ea6", "\u98ce\u9669", "\u5dee\u8ddd", "\u5e2e\u52a9",
        "\u5173\u7cfb", "\u610f\u4e49", "\u4ef7\u503c", "\u57fa\u7840", "\u9053\u7406",
        "\u89c4\u5f8b", "\u9650\u5236", "\u8981\u6c42", "\u6807\u51c6", "\u89c4\u5219",
        "\u7ecf\u9a8c", "\u6280\u672f", "\u5b9e\u529b", "\u8d44\u91d1", "\u7a7a\u95f4",
        "\u4f59\u5730", "\u5f39\u6027", "\u538b\u529b", "\u6210\u672c", "\u4f18\u52bf",
        "\u597d\u5904", "\u79ef\u6781", "\u8d1f\u9762", "\u53c2\u8003", "\u8bf4\u670d\u529b",
    )

    def _contains_unqualified_certainty(self, content_lower: str) -> bool:
        """判断「一定」是否被用作**绝对化断言**（而非程度限定语）。

        返回 True 表示存在未经限定的断言式用法（如「一定能」「一定会」），
        应当报违规；全部命中都带限定后缀（如「一定的风险」）则返回 False。
        """
        marker = "\u4e00\u5b9a"
        start = 0
        while True:
            index = content_lower.find(marker, start)
            if index < 0:
                return False
            suffix = content_lower[index + len(marker) :]
            if not any(suffix.startswith(item) for item in self._CERTAINTY_LIMITER_SUFFIXES):
                return True
            start = index + len(marker)

    def _contains_unqualified_first(self, content_lower: str) -> bool:
        first_marker = "\u7b2c\u4e00"
        start = 0
        while True:
            index = content_lower.find(first_marker, start)
            if index < 0:
                return False
            suffix = content_lower[index + len(first_marker):]
            if not any(suffix.startswith(item) for item in self._FIRST_ORDINAL_SUFFIXES):
                # Rankings such as "出货量第一" or "全球第一" are
                # qualified claims tied to a scope/metric, not the generic
                # absolute wording this gate is intended to catch.
                prefix = content_lower[max(0, index - 18):index]
                if not re.search(
                    r"(?:国内|中国|全球|行业|市场|出货量|销量|排名|份额|占比|市占率)"
                    r"[^。！？!?\n]{0,10}$",
                    prefix,
                ):
                    return True
            start = index + len(first_marker)

    def _check_depth(self, content: str) -> int:
        score = 0
        number_matches = self._NUMBER_PATTERN_COMPILED.findall(content)
        if len(number_matches) >= 3:
            score += 10
        elif len(number_matches) >= 1:
            score += 5

        industry_count = sum(1 for term in self._INDUSTRY_TERMS if term in content)
        if industry_count >= 4:
            score += 10
        elif industry_count >= 2:
            score += 5

        if any(term in content for term in self._DEPTH_CAUSAL_WORDS):
            score += 5

        return min(25, score)

    def _check_engagement_attractiveness(self, article: str) -> dict:
        """检查文章的用户互动吸引力

        从四个维度评估文章是否能吸引用户互动（阅读/点赞/评论）：
        1. 具体公司名（25分）：检测是否包含具体公司名称
        2. 具体数字（25分）：检测是否包含足够的数据支撑
        3. 对立观点（25分）：检测是否呈现多元视角
        4. 互动话术（25分）：检测是否使用互动引导话术

        Args:
            article: 文章正文

        Returns:
            dict: 包含 score（总分）、details（评分细节）、warnings（警告列表）
        """
        warnings: list[str] = []
        details: dict = {}

        # 1. 检测具体公司名（25分）：≥1个得25分，否则0分
        company_matches = self._ENGAGEMENT_COMPANY_PATTERN.findall(article)
        company_score = 25 if len(company_matches) >= 1 else 0
        details["company_names"] = company_matches[:10]
        details["company_count"] = len(company_matches)
        details["company_score"] = company_score

        # 2. 检测具体数字（25分）：≥3个得25分，按比例递减
        number_matches = self._ENGAGEMENT_NUMBER_PATTERN.findall(article)
        num_count = len(number_matches)
        if num_count >= 3:
            number_score = 25
        elif num_count == 2:
            number_score = 16
        elif num_count == 1:
            number_score = 8
        else:
            number_score = 0
        details["number_count"] = num_count
        details["number_score"] = number_score

        # 3. 检测对立观点（25分）：≥1次得25分，否则0分
        opposition_found = [w for w in self._ENGAGEMENT_OPPOSITION_WORDS if w in article]
        opposition_score = 25 if len(opposition_found) >= 1 else 0
        details["opposition_words"] = opposition_found
        details["opposition_score"] = opposition_score

        # 4. 检测互动话术（25分）：≥1处得25分，否则0分
        engagement_matches: list[str] = []
        for pattern in self._ENGAGEMENT_TEMPLATE_PATTERNS_COMPILED:
            matches = pattern.findall(article)
            engagement_matches.extend(matches)
        engagement_score = 25 if len(engagement_matches) >= 1 else 0
        details["engagement_templates"] = engagement_matches
        details["engagement_score"] = engagement_score

        # 计算总分（满分100）
        total = company_score + number_score + opposition_score + engagement_score

        if total < 60:
            warnings.append("互动吸引力不足，建议补充具体公司/数字/对立观点/互动话术")

        return {
            "score": total,
            "details": details,
            "warnings": warnings,
        }

    @staticmethod
    def _has_any_keyword(text_lower: str, keywords: list[str]) -> bool:
        """检查文本中是否包含任意关键词的辅助函数"""
        return any(kw in text_lower for kw in keywords)

    def _check_source_markers(self, content: str) -> list[str]:
        issues = []
        for pattern in self._SOURCE_MARKER_PATTERNS_COMPILED:
            matches = pattern.findall(content)
            if matches:
                issues.append(f"正文包含来源标记泄露：'{matches[0]}'，需在发布前清除")
        return issues

    @staticmethod
    def format_report(result: QualityCheckResult) -> str:
        """格式化检查报告"""
        report = []
        report.append("=" * 50)
        report.append("📋 文章质量检查报告")
        report.append("=" * 50)
        report.append("")
        report.append(f"✅ 检查结果: {'通过' if result.is_passed else '需要优化'}")
        report.append(f"📊 总分: {result.total_score}/100")
        report.append(f"  - 首发合规性: {result.compliance_score}/50")
        report.append(f"  - 原创性: {result.originality_score}/50")
        report.append(f"  - 可读性: {result.readability_score}/40")
        report.append(f"  - 深度: {result.depth_score}/25")
        report.append("")

        if result.issues:
            report.append("⚠️ 问题:")
            for issue in result.issues:
                report.append(f"  • {issue}")
            report.append("")

        if result.suggestions:
            report.append("💡 建议:")
            for suggestion in result.suggestions:
                report.append(f"  • {suggestion}")
            report.append("")

        report.append("=" * 50)

        return "\n".join(report)
