"""ArticleQualityChecker 互动吸引力维度测试"""
from __future__ import annotations

import pytest

from toutiao_content_factory.services.article_quality_checker import (
    ArticleQualityChecker,
    BodyIntegrityResult,
    QualityCheckResult,
    TitleBodyConsistencyResult,
)


class TestCheckEngagementAttractiveness:
    """测试 _check_engagement_attractiveness 方法"""

    def setup_method(self) -> None:
        self.checker = ArticleQualityChecker()

    def test_high_engagement_article_scores_80_plus(self) -> None:
        """包含公司名、3+数字、对立观点、互动话术的文章应得 80+ 分"""
        article = (
            "华为科技发布了最新财报，营收增长30%，出货量达1.5亿台，市占率25%。"
            "特斯拉也在同步推进产能扩张。"
            "然而，市场对此存在分歧。"
            "你更看好华为还是特斯拉？欢迎在评论区给出你的判断。"
        )
        result = self.checker._check_engagement_attractiveness(article)
        assert result["score"] >= 80
        assert result["score"] <= 100
        assert len(result["warnings"]) == 0
        details = result["details"]
        assert details["company_score"] == 25
        assert details["number_score"] == 25
        assert details["opposition_score"] == 25
        assert details["engagement_score"] == 25

    def test_low_engagement_article_scores_below_60(self) -> None:
        """只有抽象论述的文章应得 < 60 分并添加 warning"""
        article = (
            "从宏观视角来看，产业发展有其内在规律。"
            "创新与变革交织，机遇与挑战并存。"
            "我们需要以更长远的眼光审视这一切，在变局中寻找确定性。"
            "整体格局正在重塑，各方力量博弈仍在继续。"
        )
        result = self.checker._check_engagement_attractiveness(article)
        assert result["score"] < 60
        assert len(result["warnings"]) == 1
        assert "互动吸引力不足" in result["warnings"][0]

    def test_company_detection_chinese_suffix(self) -> None:
        """检测中文公司名后缀"""
        article = "字节跳动科技今天发布了新产品。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["company_count"] >= 1
        assert result["details"]["company_score"] == 25

    def test_company_detection_english_uppercase(self) -> None:
        """检测英文大写字母开头的公司名"""
        article = "OpenAI 发布了新模型，Tesla 也在跟进。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["company_count"] >= 1
        assert result["details"]["company_score"] == 25

    def test_company_not_detected(self) -> None:
        """无公司名时得 0 分"""
        article = "产业发展趋势值得关注，整体格局正在重塑。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["company_count"] == 0
        assert result["details"]["company_score"] == 0

    def test_number_count_three_or_more(self) -> None:
        """3+ 个数字得 25 分"""
        article = "营收30亿，利润1.5亿，增长率25%。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["number_count"] >= 3
        assert result["details"]["number_score"] == 25

    def test_number_count_two(self) -> None:
        """2 个数字得 16 分"""
        article = "营收30亿，利润15亿。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["number_count"] == 2
        assert result["details"]["number_score"] == 16

    def test_number_count_one(self) -> None:
        """1 个数字得 8 分"""
        article = "营收达到三十亿，利润十五亿，但只有一个数字5被记录。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["number_count"] == 1
        assert result["details"]["number_score"] == 8

    def test_number_count_zero(self) -> None:
        """0 个数字得 0 分"""
        article = "产业发展趋势值得关注，没有具体数据。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["number_count"] == 0
        assert result["details"]["number_score"] == 0

    def test_opposition_detected(self) -> None:
        """检测对立观点关键词"""
        for word in ["然而", "但是", "另一方面", "相反", "不过", "与此相对", "反之", "尽管如此"]:
            article = f"前景看似很好，{word}也存在风险。"
            result = self.checker._check_engagement_attractiveness(article)
            assert word in result["details"]["opposition_words"]
            assert result["details"]["opposition_score"] == 25

    def test_opposition_not_detected(self) -> None:
        """无对立观点时得 0 分"""
        article = "前景一片大好，持续增长可期。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["opposition_score"] == 0

    def test_engagement_template_team_style(self) -> None:
        """检测站队式互动话术"""
        article = "你更看好华为还是特斯拉，欢迎留言。"
        result = self.checker._check_engagement_attractiveness(article)
        assert len(result["details"]["engagement_templates"]) >= 1
        assert result["details"]["engagement_score"] == 25

    def test_engagement_template_variable_style(self) -> None:
        """检测变量追问式互动话术"""
        article = "这背后，哪个变量才是决定性因素？"
        result = self.checker._check_engagement_attractiveness(article)
        assert len(result["details"]["engagement_templates"]) >= 1
        assert result["details"]["engagement_score"] == 25

    def test_engagement_template_counterfactual_style(self) -> None:
        """检测反事实假设式互动话术"""
        article = "如果这个指标没有达标，是否意味着策略需要调整？"
        result = self.checker._check_engagement_attractiveness(article)
        assert len(result["details"]["engagement_templates"]) >= 1
        assert result["details"]["engagement_score"] == 25

    def test_engagement_template_experience_style(self) -> None:
        """检测经验补充式互动话术"""
        article = "在你的行业里，是否见过类似的案例？"
        result = self.checker._check_engagement_attractiveness(article)
        assert len(result["details"]["engagement_templates"]) >= 1
        assert result["details"]["engagement_score"] == 25

    @pytest.mark.parametrize(
        "article",
        [
            "你觉得机器人芯片里哪个环节最有机会？",
            "在你看来，机器人芯片的利润会如何分配？",
            "下一步最先要验证的是什么？",
        ],
    )
    def test_natural_open_questions_are_detected(self, article: str) -> None:
        """WriterAgent 的自然结尾问句应计入互动话术。"""
        result = self.checker._check_engagement_attractiveness(article)
        assert len(result["details"]["engagement_templates"]) >= 1
        assert result["details"]["engagement_score"] == 25

    def test_engagement_template_not_detected(self) -> None:
        """无互动话术时得 0 分"""
        article = "产业发展趋势值得关注，整体格局正在重塑。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["details"]["engagement_score"] == 0

    def test_return_format(self) -> None:
        """返回格式包含 score、details、warnings 字段"""
        result = self.checker._check_engagement_attractiveness("测试文本")
        assert "score" in result
        assert "details" in result
        assert "warnings" in result
        assert isinstance(result["score"], int)
        assert isinstance(result["details"], dict)
        assert isinstance(result["warnings"], list)

    def test_partial_engagement_below_threshold(self) -> None:
        """部分维度满足但总分 < 60 时应添加 warning"""
        # 仅公司名(25) + 1个数字(8) = 33 < 60
        article = "华为科技发布了新产品，型号为5。"
        result = self.checker._check_engagement_attractiveness(article)
        assert result["score"] < 60
        assert len(result["warnings"]) == 1


class TestCheckArticleIntegration:
    """测试 check_article 方法集成互动吸引力维度"""

    def setup_method(self) -> None:
        self.checker = ArticleQualityChecker()

    def test_result_has_engagement_score_field(self) -> None:
        """check_article 返回结果包含 engagement_attractiveness_score 字段"""
        result = self.checker.check_article("测试标题", "这是一段测试内容。")
        assert hasattr(result, "engagement_attractiveness_score")
        assert isinstance(result.engagement_attractiveness_score, int)

    def test_low_engagement_warning_merged_into_issues(self) -> None:
        """互动吸引力不足的 warning 合并到 issues 中"""
        # 构造一篇满足最低字数但互动吸引力不足的文章
        abstract_content = (
            "从宏观视角来看，产业发展有其内在规律。创新与变革交织，机遇与挑战并存。"
            "我们需要以更长远的眼光审视这一切，在变局中寻找确定性。"
            "整体格局正在重塑，各方力量博弈仍在继续。"
            "未来走向取决于多种因素的综合作用，任何单一维度的判断都可能失之偏颇。"
            "在这个充满不确定性的时代，保持清醒和理性尤为重要。"
            "每一个决策都需要深思熟虑，每一步行动都需要谨慎对待。"
            "唯有如此，方能在复杂多变的环境中找到前行的方向。"
        ) * 10  # 重复以确保字数足够
        result = self.checker.check_article("产业发展趋势分析", abstract_content)
        assert result.engagement_attractiveness_score < 60
        engagement_warnings = [i for i in result.issues if "互动吸引力不足" in i]
        assert len(engagement_warnings) >= 1

    def test_high_engagement_no_warning(self) -> None:
        """高互动吸引力文章不会添加互动警告"""
        good_content = (
            "华为科技发布了最新财报，营收增长30%，出货量达1.5亿台，市占率25%。"
            "特斯拉也在同步推进产能扩张。"
            "然而，市场对此存在分歧。"
            "你更看好华为还是特斯拉？欢迎在评论区给出你的判断。"
        ) * 20  # 重复以满足字数要求
        result = self.checker.check_article("华为科技财报分析", good_content)
        assert result.engagement_attractiveness_score >= 80
        engagement_warnings = [i for i in result.issues if "互动吸引力不足" in i]
        assert len(engagement_warnings) == 0

    def test_quality_check_result_default_value(self) -> None:
        """QualityCheckResult 的 engagement_attractiveness_score 默认值为 0"""
        result = QualityCheckResult(
            is_passed=True,
            total_score=80,
            issues=[],
            suggestions=[],
            compliance_score=30,
            originality_score=30,
            readability_score=20,
            depth_score=15,
        )
        assert result.engagement_attractiveness_score == 0

    def test_title_rejects_unsupported_financial_delivery_link(self) -> None:
        result = self.checker._check_title("优必选毛利率提升支撑批量交付")

        assert any("财务指标" in issue for issue in result["issues"])

    def test_title_rejects_vague_subject_even_with_a_number(self) -> None:
        result = self.checker._check_title(
            "其他智能12.7亿：机器人如何实测",
            "公开材料显示，优必选的相关业务仍要看交付。",
        )

        assert any("模糊主体" in issue for issue in result["issues"])

    def test_title_with_grounded_company_and_number_passes_subject_gate(self) -> None:
        result = self.checker._check_title(
            "优必选卖出921台全尺寸机器人，交付仍比销量更关键",
            "公开材料显示，优必选卖出921台全尺寸机器人，后续仍要看交付。",
        )

        assert not any("模糊主体" in issue or "主体不一致" in issue for issue in result["issues"])

    def test_editorial_integrity_rejects_html_and_residual_sentence(self) -> None:
        result = self.checker.check_editorial_integrity(
            "优必选业务观察",
            "<!doctype html>优必选、为最主要的增长驱动力出现在现有材料中。",
        )

        assert any("HTML" in issue for issue in result["hard_blockers"])
        assert any("残句" in issue for issue in result["hard_blockers"])


class TestRiskyWordOrdinalHandling:
    def setup_method(self) -> None:
        self.checker = ArticleQualityChecker()

    @pytest.mark.parametrize(
        "text",
        [
            "\u7b2c\u4e00\u6761\u5efa\u8bae\u662f\u5148\u786e\u8ba4\u6570\u636e\u6765\u6e90\u3002",
            "\u7b2c\u4e00\u6b65\u662f\u62c6\u89e3\u6210\u672c\u3002",
            "\u7b2c\u4e00\u9636\u6bb5\u5148\u505a\u6837\u672c\u9a8c\u8bc1\u3002",
            "\u7b2c\u4e00\u65f6\u95f4\u540c\u6b65\u6570\u636e\u3002",
        ],
    )
    def test_ordinal_first_is_not_flagged_as_absolute_claim(self, text: str) -> None:
        assert self.checker._check_risky_words(text.lower()) == []

    def test_unqualified_first_is_still_flagged(self) -> None:
        text = "\u8fd9\u662f\u7b2c\u4e00\u3002"

        issues = self.checker._check_risky_words(text.lower())

        assert len(issues) == 1
        assert "\u7b2c\u4e00" in issues[0]

    def test_ordinal_followed_by_unqualified_first_is_flagged(self) -> None:
        text = "\u7b2c\u4e00\u6761\u5efa\u8bae\u5df2\u5b8c\u6210\uff0c\u4f46\u8fd9\u662f\u7b2c\u4e00\u3002"

        assert self.checker._contains_unqualified_first(text.lower()) is True


class TestBlocker2LayeredIntegrity:
    """Blocker 2：正文层的硬伤不得污染标题层判定（跨层泄漏回归）。

    背景：`check_editorial_integrity()` 曾把「正文自身」的问题和「标题自身」的问题
    合并进同一个 `issues`，而 `TitleAgent.validate_title_compliance` 又误用了这个合并结果。
    真实数据里一篇正文命中 `_BODY_FRAGMENT_PATTERN` 就会让该选题 **39/39 条标题候选全灭**。
    """

    def setup_method(self) -> None:
        self.checker = ArticleQualityChecker()

    # ---------- Case A：正常中文里的「、并且」不是残句 ----------
    @pytest.mark.parametrize(
        "article",
        [
            "真正该看的，是后续有没有订单、并且能否如期交付。",
            "真正该看的，是后续有没有订单、并网、交付或毛利数据继续兑现。",
            "关键还是订单转化率、在手订单规模和客户复购节奏。",
            "接下来该跟踪样机验证进度、与主流硬件厂商的联合开发节点。",
            "机器人训练数据、是否完成客户验收，决定了后续放量节奏。",
            "要看的是设备交付、并网验证、系统集成和后端运维能否闭环。",
        ],
    )
    def test_case_a_normal_clauses_are_not_flagged_as_fragments(self, article: str) -> None:
        body = self.checker.check_body_integrity(article)

        assert isinstance(body, BodyIntegrityResult)
        assert body.passed is True
        assert body.matched_fragments == []
        assert not any("残句" in issue for issue in body.issues)

    def test_case_a_normal_body_does_not_pollute_the_title_gate(self) -> None:
        """正文正常时，任何标题都不该因为「正文残句」被拒。"""
        body_text = "真正该看的，是后续有没有订单、并且能否如期交付，还要看交付节奏。"

        merged = self.checker.check_editorial_integrity("优必选订单节奏观察", body_text)

        assert merged["body_integrity"]["passed"] is True
        assert not any("残句" in issue for issue in merged["issues"])

    # ---------- Case B：真正明显截断的残句仍然能被识别 ----------
    @pytest.mark.parametrize(
        "article",
        [
            # 项目既有夹具（RULE 早已认定这是残句），必须继续被捕获：
            "<!doctype html>优必选、为最主要的增长驱动力出现在现有材料中。",
            "公开信息提到小鹏、是因为以前那些机器人。",
            "这批订单能否兑现，最终还是要看交付节奏并",
            "样品验证结果决定了后续能否放量以及",
        ],
    )
    def test_case_b_truly_truncated_text_is_still_detected(self, article: str) -> None:
        body = self.checker.check_body_integrity(article)

        assert body.passed is False
        assert any("残句" in issue for issue in body.issues)
        # 必须给出命中的片段，人工复核时才知道是哪一句有问题。
        assert body.matched_fragments

    def test_case_b_truncated_body_is_surfaced_in_the_merged_view(self) -> None:
        body_text = "优必选、为最主要的增长驱动力出现在现有材料中。"

        merged = self.checker.check_editorial_integrity("优必选业务观察", body_text)

        assert merged["body_integrity"]["passed"] is False
        assert any("残句" in issue for issue in merged["body_issues"])
        # 合并视图（给整篇稿子的闸门）仍要能看到这条问题。
        assert any("残句" in issue for issue in merged["issues"])

    def test_non_terminal_paragraph_tail_is_detected(self) -> None:
        """段落以连接词收尾（后面是空行）同样属于悬空残句。"""
        article = "先说结论：这轮行情的关键变量是订单兑现。\n\n接下来真正要看的是交付节奏并\n\n以上。"

        body = self.checker.check_body_integrity(article)

        assert body.passed is False
        assert body.matched_fragments

    # ---------- Case C：正文层 fail / 标题层 pass 必须可区分 ----------
    def test_case_c_body_failure_and_title_consistency_are_distinguishable(self) -> None:
        """正文有硬伤，但标题本身与正文主体一致 —— 两层结论必须分开。"""
        body_text = "<!doctype html>优必选、为最主要的增长驱动力出现在现有材料中。"
        title = "优必选业务观察"

        body = self.checker.check_body_integrity(body_text)
        consistency = self.checker.check_title_body_consistency(title, body_text)

        assert body.passed is False                      # 正文层：不合格
        assert isinstance(consistency, TitleBodyConsistencyResult)
        assert consistency.passed is True                 # 标题层：标题本身没问题
        assert consistency.issues == []

    def test_case_c_merged_view_keeps_the_two_layers_apart(self) -> None:
        body_text = "<!doctype html>优必选、为最主要的增长驱动力出现在现有材料中。"
        title = "优必选业务观察"

        merged = self.checker.check_editorial_integrity(title, body_text)

        assert merged["body_integrity"]["passed"] is False
        assert merged["title_body_consistency"]["passed"] is True
        # 正文层的问题只出现在 body_issues，不得混进 title_issues。
        assert any("HTML" in issue for issue in merged["body_issues"])
        assert all("HTML" not in issue for issue in merged["title_issues"])
        assert merged["title_issues"] == []

    def test_case_c_title_side_problems_still_reported_separately(self) -> None:
        """反过来：标题自己有问题时，title_body_consistency 必须报出来。"""
        body_text = "公开材料显示，优必选的相关业务仍要看交付。"
        title = "其他智能公司的机器人业务观察"

        consistency = self.checker.check_title_body_consistency(title, body_text)

        assert consistency.passed is False
        assert consistency.issues

    def test_body_fragment_rule_does_not_match_the_common_comma_plus_conjunction(self) -> None:
        """回归：旧规则在真实语料上的主误报来源（`、并`/`、是否`）必须不再命中。"""
        pattern = ArticleQualityChecker._BODY_FRAGMENT_PATTERN

        for text in [
            "是后续有没有订单、并网、交付或毛利数据",
            "订单转化率、在手订单规模和客户复购节奏",
            "项目持续了多久、是否完成客户验收",
            "验证进度、与主流人形机器人硬件厂商的联合开发",
            "订单金额、在手订单和新增客户数量",
        ]:
            assert pattern.search(text) is None, text

