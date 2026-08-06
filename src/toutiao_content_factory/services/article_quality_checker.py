"""文章质量预检工具 - 头条首发激励合规性检查"""
import re
from dataclasses import dataclass
from typing import Optional


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


class ArticleQualityChecker:
    """文章质量预检器"""

    # 首发激励计划要求的五大质量标准关键词
    QUALITY_KEYWORDS = {
        "首发时效": ["第一时间", "及时", "最新", "刚刚", "刚刚发布", "最新消息", "今日", "昨日"],
        "原创深度": ["观点", "视角", "思考", "判断", "分析", "见解", "洞察"],
        "信息增量": ["原因", "影响", "意义", "背后", "深层", "实质", "核心"],
        "创作规范": ["根据", "基于", "数据", "显示", "统计", "来源"],
        "用户价值": ["共鸣", "连接", "触动", "启发", "收获", "价值", "意义"]
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

    def __init__(self):
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

            if word_count < 1500:
                issues.append("首发激励要求文章字数不少于1500字")
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
        title_issues = self._check_title(title, content)
        issues.extend(title_issues["issues"])
        suggestions.extend(title_issues["suggestions"])

        # 8. 风险词汇检查
        risk_issues = self._check_risky_words(content_lower)
        issues.extend(risk_issues)

        # 9. 来源标记泄露检查
        source_marker_issues = self._check_source_markers(content)
        issues.extend(source_marker_issues)

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
        )

    def _count_templates(self, content: str) -> int:
        """统计模板化表达使用次数"""
        count = 0
        for pattern in self._TEMPLATE_PATTERNS_COMPILED:
            matches = pattern.findall(content)
            count += len(matches)
        return count

    def _check_compliance(self, content: str, content_lower: str) -> dict:
        """检查首发激励合规性"""
        issues = []
        suggestions = []
        score = 0

        # 检查五大质量标准覆盖情况
        standards_covered = 0
        for standard, keywords in self.QUALITY_KEYWORDS.items():
            if any(kw in content_lower for kw in keywords):
                standards_covered += 1

        if standards_covered >= 4:
            score += 30
        elif standards_covered >= 3:
            score += 20
            suggestions.append("建议加强内容深度，增加更多信息增量")
        elif standards_covered >= 2:
            score += 10
            issues.append("内容质量标准覆盖不足")
            suggestions.append("建议增加原创观点和深度分析")
        else:
            issues.append("严重不足：缺少五大质量标准的覆盖")
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
    ]

    # RULE_006: 具象元素检测
    _TITLE_COMPANY_PATTERN = re.compile(
        r"(宇树|特斯拉|英伟达|Optimus|澜起|富士康|宁德时代|比亚迪|华为|Walker|优必选|智元|Anthropic|OpenAI|苹果|Macmini|HBM|DDR5)"
    )
    _TITLE_PLACE_PATTERN = re.compile(r"(日本|中国|美国|韩国|德国|机场|工厂|电站|产线|海外)")

    def _check_title(self, title: str, content: str = "") -> dict:
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

        # 检查标题长度
        title_len = len(title)
        if title_len > 30:
            issues.append("标题过长，可能影响展示")
            suggestions.append("建议标题控制在30字以内")
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

        # RULE_006: 纯抽象标题硬校验
        has_number = bool(re.search(r"\d", title))
        has_company = bool(self._TITLE_COMPANY_PATTERN.search(title))
        has_place = bool(self._TITLE_PLACE_PATTERN.search(title))
        if not has_number and not has_company and not has_place:
            issues.append(
                "标题为纯抽象概念堆砌（RULE_006）。历史数据：具象标题平均展现6806，"
                "纯抽象标题仅1753，差距3.9倍。必须加入具象数字、公司名或地名。"
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
        """检查违规风险词汇"""
        issues = []

        for word in self.RISKY_WORDS:
            if word in content_lower:
                issues.append(f"存在绝对化表述：'{word}'")
                break

        return issues

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
