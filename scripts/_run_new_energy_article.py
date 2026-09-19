# -*- coding: utf-8 -*-
"""Generate, archive, review, and illustrate one manually verified topic."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.utils.logging_utils import configure_logging


SOURCE_TEXT = """标题：商务部：7 月新能源乘用车零售渗透率 65.1%，再创历史新高

来源：IT之家
发布时间：2026 年 8 月 21 日 07:36
原文链接：https://www.ithome.com/0/992/424.htm

IT之家 8 月 21 日消息，8 月 20 日，商务部消费促进司负责人介绍 2026 年 7 月我国消费市场情况。1—7 月，社会消费商品和服务零售总额同比增长 2.6%；社会消费品零售总额 28.77 万亿元，增长 1.2%，其中除汽车以外的消费品零售额增长 2.7%。

消费品以旧换新带动绿色、智能消费增长。7 月份，新能源乘用车零售渗透率 65.1%，再创历史新高。商务部商务大数据显示，7 月份重点平台具身智能机器人销售额增长 95.1%，外骨骼助力器销售额增长 39.5%，运动相机销售额增长 24.7%，扫地机器人销售额增长 19.4%。

IT之家从公告获悉，7 月份商品零售额增长 0.5%。限额以上单位粮油食品、饮料、日用品零售额分别增长 5.3%、3.5%、1.8%；部分升级类商品增长较快，限额以上单位通讯器材、文化办公用品、化妆品零售分别增长 20.4%、7.4%、6.8%。

1—7 月服务零售额增长 5.0%，其中餐饮收入增长 2.6%，旅游咨询租赁服务、文体休闲服务零售额增长超过 10%。7 月份电影总票房超过 50 亿元，增长超过 20%。

1—7 月乡村消费品零售额 3.8 万亿元，增长 2.4%，增速比城镇快 1.3 个百分点。县域消费市场动能加强，包含镇区和乡村的县乡消费品零售额占社会消费品零售总额比重达 39.1%，比上年同期提高 0.3 个百分点。

写作方向：围绕新能源乘用车零售渗透率 65.1% 这一数据，分析以旧换新如何改变汽车消费结构，以及“渗透率上升”与车企利润、补能网络、用户真实需求之间的距离。必须区分报道事实和作者分析，不新增企业、销量、利润、补贴金额或成本数据。正文写成自然中文科技商业分析，来源和统计口径写清楚，保留数据边界和可跟踪指标，避免内部流程标签、空泛口号和模板化二选一问句。"""


def main() -> None:
    settings = load_settings(PROJECT_ROOT, account_key="robot_notes")
    settings.ensure_directories()
    logger = configure_logging(settings.logs_dir, settings.logging_level)
    app = ContentFactoryAppService(settings, logger)
    topic = TopicCandidate(
        title="商务部：7月新能源乘用车零售渗透率65.1%，再创历史新高",
        url="https://www.ithome.com/0/992/424.htm",
        source="ithome",
        source_type="html",
        published_at="2026-08-21T07:36:48+08:00",
        snippet=SOURCE_TEXT,
        summary="据IT之家转述商务部消费促进司信息，2026年7月新能源乘用车零售渗透率为65.1%，消费品以旧换新带动绿色、智能消费增长。",
        matched_industries=["new_energy"],
        matched_keywords=["新能源乘用车", "渗透率", "以旧换新", "消费结构"],
        extracted_numbers=["65.1%", "28.77万亿元", "1.2%", "2.6%", "39.1%"],
        heat_score=78,
        read_score=84,
        save_score=82,
        discussion_score=72,
        total_score=84,
        title_potential_score=86,
        title_playbook_track="新能源储能",
        title_potential_reasons=["有明确数据锚点", "具备消费结构变化与产业兑现的分析空间"],
        is_high_read=True,
        is_high_save=True,
        is_high_discussion=True,
        topic_type="policy_market",
        topic_type_label="政策与市场",
        evidence_strength=78,
        quality_score=84,
        recommended_format="deep_analysis",
        recommended_format_label="深度分析",
        rank=1,
        commercial_intent="medium",
        content_mix="professional",
        cta_variant="newsletter",
        cta_source_code="new_energy_medium_newsletter",
        title_variant="data_hook",
        cover_variant="data_anchor",
        platform_variant="multi_platform_rewrite",
        topic_bucket="new_energy",
    )
    input_text = app.topic_automation._build_input_text(topic)
    report = DailyTopicsReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lookback_days=24,
        max_topic_age_hours=48,
        focus_industries=["new_energy", "energy_storage"],
        total_raw_items=1,
        total_selected_items=1,
        topics=[topic],
        source_stats={"ithome": {"raw_count": 1, "status": "manual_verified_source"}},
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
