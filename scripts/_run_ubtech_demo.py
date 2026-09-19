# -*- coding: utf-8 -*-
"""Generate and review one manually verified robotics topic for a publish demo."""
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


SOURCE_TEXT = """标题：优必选半年收入翻倍，人形机器人商业化要看交付
来源：智东西（转述优必选2026年中期业绩报告）
发布时间：2026年8月28日
原文链接：https://www.zhidx.com/p/589065.html

据智东西8月28日报道，优必选（9980.HK）公布2026年中期业绩报告。以下数字均按该报道对公司半年报的转述记录，正文必须保留来源边界，不要写成已经独立核验的原始公告：

1. 2026年上半年，优必选总收入12.7亿元，同比增长104.2%。
2. 整体毛利5.7亿元，同比增长160.9%；整体毛利率44.7%，同比提升9.7个百分点。
3. 上半年亏损3.4亿元，同比减少23.0%；经调整EBITDA为-1.7亿元，同比减亏1.5亿元，降幅45.9%。
4. 研发费用3.0亿元，较2025年上半年的2.2亿元增加38.9%；截至2026年6月30日，获授权专利3112项，其中海外授权专利530项。
5. 报道还转述了公司关于全尺寸具身智能机器人产品及解决方案收入、不同业务分类和机器人销量的数据。但这些数字存在分类口径差异，本文不要使用5.9亿元、5.7亿元、1445.0%、1524.0%或16123台等争议数据，也不要把销量写成全尺寸机器人的销量。

写作方向：围绕“收入翻倍不等于商业化已经跑通”写一篇今日头条科技商业分析。只使用上面四组相对清晰的数字，区分报道事实、公司披露和作者判断。重点解释收入增长、毛利改善、仍然亏损、研发投入之间的关系，并回答人形机器人商业化下一步应该看哪些指标：批量交付、客户复购、验收回款、单机成本和售后服务成本。

文章要求：
- 5至7个自然段，约1800至2200字；每段有明确功能，不写审核笔记，不出现内部字段、缓存、工作流、专家团等字样。
- 标题要具体、克制、有判断，不使用“暴涨”“彻底改变”“行业第一”等未经来源支持的绝对化表达。
- 开头直接给出判断；中段解释数据之间的因果关系；结尾给出三项可跟踪指标并留下自然的评论问题。
- 明确说明本文主要依据智东西对优必选中期业绩报告的转述，未把它外推为整个人形机器人行业的统计结论。
"""


def main() -> None:
    settings = load_settings(PROJECT_ROOT, account_key="robot_notes")
    settings.ensure_directories()
    logger = configure_logging(settings.logs_dir, settings.logging_level)
    app = ContentFactoryAppService(settings, logger)

    topic = TopicCandidate(
        title="优必选半年收入翻倍，人形机器人商业化要看交付",
        url="https://www.zhidx.com/p/589065.html",
        source="zhidx",
        source_type="html",
        published_at="2026-08-28",
        snippet=SOURCE_TEXT,
        summary="智东西转述优必选2026年中期业绩：上半年收入12.7亿元，同比增长104.2%，毛利率44.7%，但仍亏损3.4亿元。本文聚焦收入增长能否转化为可持续交付和盈利。",
        matched_industries=["humanoid_robot", "ai_hardware"],
        matched_keywords=["优必选", "人形机器人", "中期业绩", "交付", "毛利率"],
        extracted_numbers=["12.7亿元", "104.2%", "5.7亿元", "44.7%", "3.4亿元", "3.0亿元"],
        heat_score=92,
        read_score=91,
        save_score=94,
        discussion_score=88,
        total_score=91,
        title_potential_score=86,
        title_playbook_track="AI机器人商业化",
        title_potential_reasons=["公司主体清楚", "收入与亏损形成可解释的反差", "有明确的后续跟踪指标"],
        is_high_read=True,
        is_high_save=True,
        is_high_discussion=True,
        topic_type="earnings_signal",
        topic_type_label="业绩信号",
        evidence_strength=88,
        quality_score=88,
        recommended_format="project_breakdown",
        recommended_format_label="项目拆解",
        rank=1,
        commercial_intent="medium",
        content_mix="professional",
        cta_variant="question",
        cta_source_code="robotics_delivery_question",
        title_variant="judgment_with_data",
        cover_variant="data_anchor",
        platform_variant="multi_platform_rewrite",
        topic_bucket="robotics",
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
        source_stats={"zhidx": {"raw_count": 1, "status": "manual_verified_source"}},
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
