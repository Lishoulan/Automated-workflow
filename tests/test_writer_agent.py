from __future__ import annotations

from unittest.mock import MagicMock

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.models import CacheContext, DataReport, TrendReport


def _build_writer_agent(app_settings, mock_openai_service, logger) -> WriterAgent:
    return WriterAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )


def test_trim_low_confidence_paragraphs_removes_cta_and_unknown_company(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        companies=["汇川技术"],
        growth_numbers=["12%"],
        evidence_notes=["公开资料显示产线在扩容"],
        core_data=["产线利用率提升"],
    )

    paragraphs = [
        "公开资料显示，汇川技术的订单节奏开始改善，产线爬坡速度比去年更快。",
        "以某头部厂商为例，陌生科技的伺服系统毛利率提升到了31%。",
        "## 延伸服务\n回复“咨询”领取行业周报。",
    ]

    cleaned = agent._trim_low_confidence_paragraphs(paragraphs, data_report)

    assert cleaned == ["公开资料显示，汇川技术的订单节奏开始改善，产线爬坡速度比去年更快。"]


def test_build_supplementary_paragraphs_stays_grounded(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        watch_metrics=["订单转化", "项目回款"],
        risk_flags=["客户验收延后"],
        evidence_notes=["财报披露新签项目增加"],
        growth_numbers=["12%", "18%"],
    )

    paragraphs = agent._build_supplementary_paragraphs(data_report, existing_paragraphs=[])
    joined = "\n".join(paragraphs)

    assert paragraphs
    assert "订单转化" in joined
    assert "客户验收延后" in joined
    assert "财报披露新签项目增加" in joined
    assert "12%" in joined and "18%" in joined


def test_supplementary_paragraphs_use_evidence_fields_without_old_template_openers(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        watch_metrics=["部署数量", "重复订单"],
        risk_flags=["单机成本未披露"],
        evidence_notes=["公开材料：特斯拉Optimus进入工厂"],
        growth_numbers=["12%", "18%"],
    )

    joined = "\n".join(agent._build_supplementary_paragraphs(data_report))

    assert "部署数量" in joined
    assert "单机成本未披露" in joined
    assert "特斯拉Optimus进入工厂" in joined
    assert "12%" in joined and "18%" in joined
    assert "后续需要持续跟踪的变量包括" not in joined
    assert "从已有证据看" not in joined
    assert "关键数字锚点" not in joined


def test_medium_grounded_draft_stays_blocked_without_llm_expansion(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        topic_title="英伟达发布78 TOPS机器人芯片",
        focus_industry="人形机器人",
        focus_keywords=["人形机器人", "机器人芯片"],
        companies=["英伟达"],
        core_data=["英伟达发布78 TOPS机器人芯片，功耗下降40%"],
        industry_trends=["机器人芯片正在从算力参数走向客户验证"],
        key_points=["芯片能否进入量产要看功耗和交付"],
        watch_metrics=["芯片出货", "客户验证", "量产节奏"],
        evidence_notes=["公开材料：英伟达发布78 TOPS机器人芯片，功耗下降40%"],
        growth_numbers=["78 TOPS", "40%"],
        risk_flags=["量产和客户采购节奏尚未披露"],
        content_format="project_breakdown",
        word_target=1100,
        evidence_strength=88,
    )
    existing = [
        "这次信息把算力参数带到了真实执行环节。英伟达是材料中明确出现的主体，判断重点不应停在宣传口径，还要观察参数如何进入实际方案。",
        "第二个观察点是芯片参数如何进入产品方案。78 TOPS给出的是算力指标，功耗下降40%则对应部署约束，两者需要结合使用场景核对，不能直接写成订单结果。",
        "从开发者角度看，芯片价值不只由峰值算力决定，还要看软件适配、设备空间和持续运行条件是否能够配合。",
        "对整机厂商而言，参数优势只有进入稳定的产品方案，才有可能影响采购选择。这个过程需要实际验证，不能用行业热度替代。",
        "如果客户验证停留在展示阶段，芯片发布带来的影响就主要是预期变化，而不是已经确认的收入变化。",
        "功耗指标与设备续航、散热设计和运行成本有关，但这些关系还需要结合具体产品和测试口径判断。",
        "明确主体可以作为分析起点，但不应替它补写未披露的客户名单或采购规模。",
        "机器人芯片的竞争还会落到交付和支持能力上，单一参数很难解释最终的商业结果。",
        "后续判断需要把芯片出货、客户验证和量产节奏放在同一时间线里观察，避免把一次发布直接外推成行业拐点。",
        "现阶段最稳妥的结论是信息已经提供了可追踪的技术信号，商业化强度仍要等待更多公开记录。",
        "软件适配的成熟度会影响芯片进入产品的速度，公开材料目前没有给出完整的适配范围，因此只能把它列为观察项。",
        "设备端的空间和散热约束同样重要，参数优势要在真实产品中保持稳定，才可能转换成可持续的使用价值。",
        "采购方最终关注的不只是峰值指标，还包括供货稳定、技术支持和后续升级，这些内容需要更多公告来确认。",
        "如果部署成本没有同步下降，功耗变化对商业结果的意义就会受到限制，不能简单把这项改进等同于利润增加。",
        "英伟达的技术位置可以作为分析起点，但产业链中的其他主体和受益环节仍需依据具体材料逐一核对。",
        "从行业角度看，芯片标准变化可能推动整机方案调整，不过这种影响需要经过客户采用和产品交付才能被验证。",
        "公开披露的参数解决了部分信息问题，尚未披露的采购方式、交付周期和价格口径仍然决定判断能走多远。",
        "文章把这项变化、产品约束和商业结果分开处理，才能避免把一次发布写成已经完成的产业升级。",
        "后续若出现持续出货或客户验收记录，当前的阶段性判断可以更新；在此之前，保守表述更符合材料边界。",
        "这条线索的价值在于提供了新的核对方向，而不是立即给出确定的市场份额或收入结论。",
        "只要后续记录能够补齐主体、时间和结果，读者就能更准确地判断这次芯片发布究竟改变了什么。",
        "读者还需要关注信息的统计范围和使用条件，只有口径一致，前后的变化才具有可比性，判断才不会被单项参数带偏。",
    ]
    current_chars = len("".join(existing))
    assert 1000 <= current_chars < 1500

    ensured = agent.ensure_article_min_chars("\n\n".join(existing), data_report)
    ensured_chars = len(ensured.replace("\n", "").replace(" ", ""))

    # Evidence-aware fallback must not manufacture repetitive copy just to
    # cross the public length threshold. The short result remains blocked.
    assert 0 < ensured_chars < 1500
    assert agent._unsupported_numeric_tokens(ensured, data_report) == []
    assert "主体关系也不能省略" not in ensured
    assert "当前记录的核心内容是" not in ensured
    assert not any(
        fragment in ensured
        for fragment in agent.INCOMPLETE_ARTICLE_FRAGMENTS
    )
    mock_openai_service.create_json_completion.assert_not_called()


def test_explicit_publish_length_is_not_clamped_to_quick_commentary_floor(
    app_settings,
    mock_openai_service,
    logger,
    caplog,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    agent._build_supplementary_paragraphs = MagicMock(return_value=[])
    data_report = DataReport(
        content_format="quick_commentary",
        word_target=800,
        companies=["优必选"],
        core_data=["优必选发布机器人进展"],
        evidence_notes=["公开材料：优必选发布机器人进展"],
    )

    with caplog.at_level("WARNING"):
        ensured = agent.ensure_article_min_chars("优必选的阶段性进展。" * 70, data_report, min_chars=1750)

    assert len(ensured.replace("\n", "").replace(" ", "")) < 1750
    assert "minimum 1750" in caplog.text


def test_writer_auto_publish_prompt_uses_long_form_floor(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    object.__setattr__(app_settings.publishing, "enabled", True)
    object.__setattr__(app_settings.publishing, "auto_publish", True)
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(content_format="quick_commentary", word_target=800)

    assert agent._article_word_target_for_prompt(data_report) == "1800"
    assert agent._min_word_target_for(data_report) == "1800"
    assert "自动发布候选" in agent._writer_system_prompt(app_settings.get_style("deep_industry_analysis"))


def test_supplementary_paragraphs_are_bounded_and_reject_incomplete_fragments(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        companies=["测试主体"],
        core_data=["公开材料披露测试主体完成一项阶段性技术进展"],
        evidence_notes=["来源材料记录了这项进展"],
        watch_metrics=["客户验证", "交付节奏", "巡检任务的成展开"],
        risk_flags=["商业化结果尚未披露"],
        growth_numbers=["12%", "18%"],
        evidence_strength=80,
    )

    supplements = agent._build_supplementary_paragraphs(data_report)

    assert 0 < len(supplements) <= agent.MAX_LOCAL_SUPPLEMENTARY_PARAGRAPHS
    assert all(
        not any(fragment in paragraph for fragment in agent.INCOMPLETE_ARTICLE_FRAGMENTS)
        for paragraph in supplements
    )
    assert len({paragraph.strip() for paragraph in supplements}) == len(supplements)


def test_supplementary_paragraphs_skip_evidence_already_covered_by_article(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        watch_metrics=[
            "全尺寸人形机器人实际交付量、产品构成和交付周期",
            "意向订单、框架协议与实际收入之间的转化率",
            "制造、物流和商业服务场景中的客户复购率",
            "单机物料成本、核心零部件自研比例及单机毛利",
        ],
        risk_flags=[
            "销量口径需要核对产品构成和销售确认方式",
            "项目型收入未必能够持续转化为可复制利润",
        ],
        evidence_notes=["半年报披露营收增长和人形机器人收入占比提升"],
        growth_numbers=["12.7亿元", "104.2%"],
    )
    existing = [
        "文章已经写到全尺寸人形机器人实际交付量、产品构成和交付周期。",
        "文章已经写到意向订单、框架协议与实际收入之间的转化率。",
        "文章已经写到制造、物流和商业服务场景中的客户复购率。",
        "文章已经写到单机物料成本、核心零部件自研比例及单机毛利。",
    ]

    supplements = agent._build_supplementary_paragraphs(data_report, existing_paragraphs=existing)

    assert not any("后续复盘可以先核对" in item for item in supplements)


def test_local_source_fallback_builds_grounded_long_draft_when_model_is_unavailable(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        topic_title="辰星技术赴港IPO前改名，机器人业务能否穿越盈利考验",
        focus_industry="工业机器人",
        focus_keywords=["工业机器人", "并联机器人"],
        companies=["辰星技术"],
        core_data=["2025年中国并联机器人出货量占五分之一市场"],
        growth_numbers=["2025年", "7.7%", "2.53亿", "2880万", "30%"],
        evidence_notes=["公开材料：辰星技术的经营现金流仍需核对"],
        watch_metrics=["客户验证", "交付节奏", "经营现金流"],
        risk_flags=["盈利稳定性尚未得到连续数据证明"],
        content_format="project_breakdown",
        word_target=1800,
        evidence_strength=88,
        source_facts=[
            "公司最早叫辰星自动化，2025年股改后改成了阿童木机器人，不到十个月又改回了辰星技术",
            "公司脱胎于天津大学并联机器人实验室，创始团队具有学术背景",
            "食品饮料、日化、制药、新能源、3C和汽车工厂使用其机器人进行分拣、搬运、包装和码垛",
            "2025年中国并联机器人出货量第一，占了五分之一的市场，全球排第二，7.7%的份额",
            "高速工业机器人出货量同样国内第一，全球第四",
            "2023年到2025年，辰星技术收入从9349万增长到2.53亿",
            "2026年上半年收入1.35亿，同比增长了四成多",
            "毛利率从17%提升到30%上下",
            "2026年上半年亏损2880万，经营活动现金流流出2856万",
            "2026年上半年研发开支占收入将近18%，公司拥有175项专利",
            "公司2023年亏损3925万，2024年亏损4707万",
            "公司2025年净利润为73.9万",
            "公司2026年全年大概率仍然亏损",
            "2023年至2026年上半年经营活动现金流全部为负数",
            "政府补助对公司2025年的盈利起到关键作用",
            "公司销售费用一年超过三千万元",
            "公司需要继续投入研发、市场和产能建设",
            "应收账款和存货存在需要进一步核对的隐患",
            "机器人前瞻（公众号：robot_pro） 作者 | 钟宸编辑 | 漠影看着提速，实则分化",
            "关注我。聚焦科技商业、AI产业与先进制造，用数据拆热点。",
            "#机器人 #具身智能 #AI #科技",
        ],
    )

    content = agent._fallback(
        TrendReport(recommended_angle="先核对收入增长能否转化为现金流"),
        data_report,
        app_settings.get_style("deep_industry_analysis"),
        CacheContext(),
    )
    article = content.article
    article_chars = len(article.replace("\n", "").replace(" ", ""))
    assert article_chars >= agent.LOCAL_SOURCE_ARTICLE_TARGET
    assert "辰星技术" in article
    assert "2.53亿" in article
    assert "2880万" in article
    assert "机器人前瞻" not in article
    assert "robot_pro" not in article
    assert "作者" not in article
    assert "编辑" not in article
    assert "关注我" not in article
    assert "#机器人" not in article
    assert article.rstrip().split("\n\n")[-1].endswith("？")
    assert agent._unsupported_numeric_tokens(article, data_report) == []
    assert "材料里的推测会明确保留为推测" in article
    mock_openai_service.create_json_completion.assert_not_called()


def test_service_robot_local_fallback_uses_scene_and_delivery_analysis(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        topic_title="香港真实开放场景服务机器人落地酒吧",
        focus_industry="服务机器人",
        focus_keywords=["服务机器人", "商业服务"],
        companies=["智平方"],
        core_data=["智平方机器人在香港酒吧真实上岗"],
        evidence_notes=["公开材料披露了具体服务场景和交付环节"],
        watch_metrics=["连续运行时间", "客户复购", "服务成本"],
        risk_flags=["单个示范项目不能代表规模化复制"],
        content_format="project_breakdown",
        word_target=1800,
        evidence_strength=86,
        source_facts=[
            "智平方 AlphaBot 2 已在香港兰桂坊酒吧真实上岗，提供鸡尾酒制作和顾客互动服务",
            "项目涉及机器人硬件出关、跨境运输、清关合规和当地运行资质",
            "公开材料提到团队正在建立面向海外客户的付费服务体系和本地化运营流程",
            "机器人使用 NeuroVLA 具身模型处理感知、决策和动作执行，模型能力要结合现场任务检验",
            "披露数据显示，运动控制抖动和反射响应时间出现改善，但具体测试条件仍需回到原文核对",
            "酒吧场景的价值不只在于完成一次调酒，还要看营业时段内能否稳定运行并减少人工重复工作",
            "设备出现故障后需要本地维护、备件和远程支持，服务响应速度会影响客户是否继续使用",
            "现有材料没有披露连续客户数量、完整收费价格和长期回款记录，这些仍属于待验证信息，现场服务质量也需要持续观察，并应结合不同班次的客流变化和人工介入次数持续记录",
        ],
    )

    content = agent._fallback(
        TrendReport(recommended_angle="服务机器人能否从示范上岗走向持续付费"),
        data_report,
        app_settings.get_style("deep_industry_analysis"),
        CacheContext(),
    )
    assert agent._is_service_robot_source(data_report, data_report.source_facts)
    article = content.article
    article_chars = len(article.replace("\n", "").replace(" ", ""))

    assert article_chars >= agent.LOCAL_SOURCE_ARTICLE_TARGET, article
    assert "兰桂坊" in article
    assert "清关合规" in article
    assert "付费服务体系" in article
    assert "赴港IPO" not in article
    assert "资本市场" not in article
    assert article.rstrip().split("\n\n")[-1].endswith("？")
    assert agent._unsupported_numeric_tokens(article, data_report) == []
    mock_openai_service.create_json_completion.assert_not_called()


def test_short_model_draft_uses_grouped_source_facts_to_clear_publish_floor(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    mock_openai_service.available = True
    data_report = DataReport(
        topic_title="优必选半年报的人形机器人业务",
        companies=["优必选"],
        content_format="project_breakdown",
        word_target=1800,
        source_facts=[
            "优必选2026年上半年营收为12.7亿元，同比增长104.2%，这是公司披露的整体收入规模变化",
            "全尺寸人形机器人产品及解决方案收入约为5.9亿元，增长率达1445.0%，对应产品分类口径的收入变化",
            "优必选整体毛利率为44.7%，同比提升9.7个百分点，但单项毛利率变化仍需结合成本口径核对",
            "优必选人形机器人总销量达16123台，全尺寸产品销量达921台，两个数字对应不同产品范围",
            "优必选物流场景收入从0.6亿元增至1.4亿元，相关变化仍要结合项目交付和收入确认口径观察",
            "优必选2026年上半年研发人员数达1103人，同比增加286人，披露中还包括硕士及以上研发人员数量",
            "优必选研发费用为3.0亿元，同比增加38.9%，这项投入需要与产品交付和后续回款一起判断",
            "优必选经营活动现金流和客户复购仍需后续公告核对，现有材料没有给出连续验证结果",
        ],
        growth_numbers=["12.7亿元", "104.2%", "44.7%"],
        watch_metrics=["交付节奏", "客户验收", "经营现金流"],
        risk_flags=["订单和回款节奏仍需验证"],
        evidence_strength=90,
    )

    article = agent.ensure_article_min_chars(
        "优必选的阶段性数据已经出现，后续要看交付节奏还是经营现金流？",
        data_report,
        min_chars=1750,
    )

    article_chars = len(article.replace("\n", "").replace(" ", ""))
    assert article_chars >= 1750
    assert "12.7亿元" in article
    assert "16123台" in article
    assert article.rstrip().split("\n\n")[-1].endswith("？")


def test_cautious_polish_fallback_keeps_unknown_order_claim_conditional(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(
        focus_industry="人形机器人",
        focus_companies=["特斯拉", "柏林工厂", "公开材料"],
        companies=["特斯拉", "柏林工厂", "公开材料", "AI机器人"],
        core_data=["特斯拉Optimus进入柏林工厂"],
        industry_trends=["行业从样机展示走向真实场景验证"],
        evidence_notes=["公开材料：特斯拉Optimus进入柏林工厂"],
        watch_metrics=["部署数量", "重复订单", "单机成本"],
        risk_flags=["订单金额尚未披露"],
        topic_title="特斯拉Optimus机器人进柏林工厂",
    )

    article = agent._rebuild_cautious_article(
        data_report,
        TrendReport(recommended_angle="需要继续核对任务完成率和连续运行时间"),
    )

    assert "特斯拉" in article
    assert "柏林工厂" not in agent._company_candidates(data_report)
    assert "获得样机订单" not in article
    assert "后续更值得跟踪的" not in article
    assert "风险边界也要保留" not in article
    assert "限制条件" in article


def test_finalize_article_rebuilds_unsupported_sample_order_claim(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(
        focus_industry="人形机器人",
        companies=["特斯拉"],
        focus_companies=["特斯拉"],
        core_data=["特斯拉Optimus进入柏林工厂"],
        evidence_notes=["公开材料：特斯拉Optimus进入柏林工厂"],
        watch_metrics=["部署数量", "重复订单"],
        risk_flags=["订单金额尚未披露"],
    )
    trend_report = TrendReport()
    article = "减速器、执行器和视觉传感器已经获得样机订单，这说明供应链开始放量。"

    finalized = agent._finalize_article(article, data_report, trend_report)

    assert "获得样机订单" not in finalized
    assert "特斯拉" in finalized


def test_conditional_order_question_is_not_treated_as_an_assertion(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(core_data=["订单金额尚未披露"])

    assert not agent._has_unsupported_order_claim("若后续获得订单，再判断商业化进度。", data_report)
    assert not agent._has_unsupported_order_claim("订单是否落地，仍需等待公告。", data_report)
    assert agent._has_unsupported_order_claim("公司已经获得重复订单。", data_report)


def test_finalize_comment_prompt_replaces_generic_question_with_grounded_choices(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        companies=["优必选"],
        watch_metrics=["交付节奏", "经营现金流"],
    )

    prompt = agent._finalize_comment_prompt("对此你怎么看？", data_report)

    assert "优必选" in prompt
    assert "交付节奏" in prompt
    assert "经营现金流" in prompt
    assert "还是" in prompt
    assert prompt.endswith("？")


def test_unknown_known_brand_is_rejected_from_polished_article(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(companies=["特斯拉"])

    assert not agent._has_unknown_company_mention("特斯拉已经发布新产品。", data_report)
    assert agent._has_unknown_company_mention("华为已经发布新产品。", data_report)


def test_source_fact_company_aliases_are_allowed_during_trim(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        companies=["辰星技术"],
        source_facts=["公司改名为阿童木机器人，业务覆盖并联机器人场景。"],
    )

    assert not agent._has_unknown_company_mention("阿童木机器人进入并联机器人市场。", data_report)
    assert agent._has_unknown_company_mention("陌生机器人公司获得大额订单。", data_report)


def test_order_evidence_must_match_company_and_order_type(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(
        core_data=["特斯拉已经获得采购订单。"],
        companies=["特斯拉"],
    )

    assert not agent._has_unsupported_order_claim("特斯拉已经获得采购订单。", data_report)
    assert agent._has_unsupported_order_claim("比亚迪已经获得采购订单。", data_report)
    assert agent._has_unsupported_order_claim("特斯拉已经获得重复订单。", data_report)


def test_writer_system_prompt_uses_evidence_boundary_without_rigid_ai_templates(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    prompt = agent._writer_system_prompt(app_settings.get_style("deep_industry_analysis"))

    assert prompt.count("你是 WriterAgent") == 1
    assert "事实边界" in prompt
    assert "绝不虚构公司、人物、数字、时间、订单" in prompt
    assert "每个段落只推进一个主要判断" in prompt
    assert "素材没有三个数据点时" in prompt
    assert "每段必须包含至少一个具体场景锚点" not in prompt
    assert "第一人称现场观察" in prompt
    assert "每个数字机械套同一个来源前缀" in prompt
    assert "You are WriterAgent" not in prompt


def test_polish_constraints_prioritize_grounding_and_natural_variation(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )

    assert "不得新增素材中没有的公司、人物、数字、时间、订单" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "不要为了满足固定段数" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "第一人称经历" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "每个数字机械添加相同" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "采用科技媒体记者的第一人称观察视角" not in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "你是 PolishAgent" in agent.POLISH_SYSTEM_PROMPT
