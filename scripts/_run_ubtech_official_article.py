# -*- coding: utf-8 -*-
"""Generate, review, and illustrate an official-results article for a publish demo."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.utils.logging_utils import configure_logging


SOURCE_TEXT = """标题：优必选中期业绩：人形机器人收入占比46.5%，商业化要看交付兑现
来源：香港交易所官方公告《优必选科技股份有限公司截至2026年6月30日止六个月中期业绩公告》
发布时间：2026年8月28日
原文链接：https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0828/2026082801691.pdf

以下事实均来自上述香港交易所公告，金额为人民币：
1. 截至2026年6月30日止六个月，公司收入12.6913亿元，同比增长104.2%；整体毛利5.66878亿元，同比增长160.9%；整体毛利率44.7%，上年同期为35.0%。
2. 期内亏损3.38827亿元，同比减少23.0%；经调整EBITDA为亏损1.74144亿元，上年同期为亏损3.21841亿元。经调整EBITDA属于公告列示的非国际财务报告准则指标。
3. 全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%，上年同期为3820.6万元；公告同时列示该类产品及服务销量921台，同比增长1946.7%。这里的销量口径是公司公告中“全尺寸具身智能人形机器人产品及服务”的合计，不改写为全行业销量。
4. 其他智能机器人产品及服务收入2.45754亿元，占19.4%，同比下降18.8%；其他智能硬件设备收入2.59377亿元，占20.4%，同比增长2.9%；园林机械、汽车及液压零部件收入1.39176亿元，占11.0%，公告说明该部分与2026年3月完成的浙江丰龙电气有限公司股权收购有关，并自2026年4月起并表。
5. 研发费用3.03083亿元，同比增长38.9%，占收入比例从35.1%降至23.9%；截至2026年6月30日，公司拥有3112项已授权专利，其中海外授权专利530项。
6. 公告说明，收入增长主要归因于大规模场景应用加速以及全尺寸具身智能人形机器人产品和服务的批量订单交付；同时，业务分类发生调整，2025年同期比较口径按新分类重述。

写作方向：围绕“收入结构已经变化，但盈利和规模化交付仍要继续验证”写一篇今日头条科技商业分析。文章必须以香港交易所官方公告为一手来源，明确区分公司披露与作者分析，不把单家公司数据外推为整个人形机器人行业结论。解释收入增长、毛利率改善、仍然亏损、研发投入之间的关系，并分析921台、收入占比46.5%之后还要观察什么：客户复购、批量交付与验收、应收账款和回款、单机成本、售后服务成本，以及分类调整后的可比性。

文章要求：5至7个自然段，约1800至2200字；开头直接给出判断；中段围绕数据做因果分析；结尾给出可跟踪指标并留下自然的评论问题。不要使用“已跳过缓存、强制重新生成”等内部流程标签，不写不存在的客户、订单金额、市场份额或行业排名，不使用“行业已经规模化”等超出公告证据的结论，不堆砌数字，不写审核说明。
"""


def main() -> None:
    settings = load_settings(PROJECT_ROOT, account_key="robot_notes")
    settings.ensure_directories()
    logger = configure_logging(settings.logs_dir, settings.logging_level)
    app = ContentFactoryAppService(settings, logger)

    topic = TopicCandidate(
        title="优必选中期业绩：人形机器人收入占比46.5%，商业化要看交付兑现",
        url="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0828/2026082801691.pdf",
        source="hkex",
        source_type="pdf",
        published_at="2026-08-28T22:00:00+08:00",
        snippet=SOURCE_TEXT,
        summary="香港交易所公告披露，优必选2026年上半年收入12.6913亿元，同比增长104.2%；全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%，销量921台，但公司仍亏损3.38827亿元。",
        matched_industries=["humanoid_robot", "ai_hardware"],
        matched_keywords=["优必选", "中期业绩", "人形机器人", "收入结构", "交付", "盈利"],
        extracted_numbers=["12.6913亿元", "104.2%", "5.66878亿元", "44.7%", "3.38827亿元", "5.90299亿元", "46.5%", "921台", "3.03083亿元", "3112项"],
        heat_score=88,
        read_score=89,
        save_score=90,
        discussion_score=86,
        total_score=89,
        title_potential_score=88,
        title_playbook_track="AI机器人商业化",
        title_potential_reasons=["官方公告来源清楚", "收入结构、亏损和交付数据形成可解释张力", "后续跟踪指标明确"],
        is_high_read=True,
        is_high_save=True,
        is_high_discussion=True,
        topic_type="earnings_signal",
        topic_type_label="业绩信号",
        evidence_strength=96,
        quality_score=92,
        recommended_format="deep_analysis",
        recommended_format_label="深度分析",
        rank=1,
        commercial_intent="medium",
        content_mix="professional",
        cta_variant="question",
        cta_source_code="official_results_delivery_question",
        title_variant="data_hook",
        cover_variant="data_anchor",
        platform_variant="multi_platform_rewrite",
        topic_bucket="robotics_official_results",
    )

    input_text = app.topic_automation._build_input_text(topic)
    report = DailyTopicsReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lookback_days=7,
        max_topic_age_hours=96,
        focus_industries=["humanoid_robot", "ai_hardware"],
        total_raw_items=1,
        total_selected_items=1,
        topics=[topic],
        source_stats={"hkex": {"raw_count": 1, "status": "official_primary_source"}},
    )
    selection_exports = app.topic_automation._write_selection_snapshot(topic, report, input_text)
    content_exports = app.manual_run(
        paste_text=SOURCE_TEXT,
        style_override="tech_media",
        dry_run=False,
        refresh_cache=True,
        include_input_dir=False,
        fast_mode=False,
    )
    combined = {name: str(path) for name, path in selection_exports.items()}
    combined.update(content_exports)
    archived = app.topic_automation.archive_run(combined, selected_topic=topic)
    run_dir = Path(archived["archive_root"])
    review = app.prepare_review(str(run_dir))
    images = app.generate_images_for_run(str(run_dir))
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "content_exports": content_exports,
                "review_exports": review,
                "image_exports": images,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
