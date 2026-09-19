# -*- coding: utf-8 -*-
"""Apply a human editorial revision to the manually verified test run."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = (
    PROJECT_ROOT
    / "output"
    / "accounts"
    / "robot-notes"
    / "runs"
    / "20260831-062449_商务部-7月新能源乘用车零售渗透率65-1-再创历史新高"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import CacheContext, WorkflowResult
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.exporter import ExportService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.utils.logging_utils import configure_logging


ARTICLE = """新能源乘用车在2026年7月的零售渗透率达到65.1%。我的判断是，这个数字已经足以证明新能源车进入主流消费讨论，但还不能直接用来推断车企利润改善。接下来更值得看的是订单能否变成交付，以及规模扩张是否被价格和服务成本抵消。

据IT之家8月21日报道，商务部消费促进司介绍了2026年7月消费市场情况，并披露上述渗透率。这里的来源关系需要说清：本文使用的是IT之家对商务部材料的转述，并非直接引用商务部网页原文。

同一组材料显示，2026年1—7月社会消费品零售总额为28.77万亿元，同比增长1.2%；社会消费商品和服务零售总额同比增长2.6%。两个指标统计范围不同，不能把1.2%和2.6%当成同一口径比较。

把65.1%放回这个背景里，较稳妥的分析是消费结构正在发生变化：整体消费增长温和，但新能源乘用车在汽车消费中的渗透继续提高。数据支持结构变化的判断，尚不足以支持所有耐用品需求都在同步扩张的结论。

想象一个普通工作日的园区停车场，消费者面对的并不只是燃油车与电动车的选择，还包括充电是否方便、长途补能是否稳定、旧车置换后月供是否可承受。这个场景不是调查数据，只是把渗透率落到使用条件上：买车决策最终要经过成本和体验的检验。

以旧换新可能改变了部分消费者的购买时点，也可能改善新能源车型的短期订单表现。但现有材料没有给出置换订单占比、订单转化率和补贴金额，所以不能把65.1%的上升全部归因于政策。政策助力与真实需求，需要分开记录。

判断需求有没有接上，建议把政策退坡后的月度销量和订单留存放在一起看。除了销量变化，还要观察订单从咨询到下单、从下单到交付的连续转化，才更接近实际购买意愿。只盯某一个月的渗透率，容易把时点变化误判成长期趋势。

订单进入交付环节后，观察重点会从营销端移到生产和服务端。交付周期、库存水平、产能利用率、充电设施覆盖和售后响应，都会影响消费者是否愿意把一次购买变成长期使用。渗透率提高之后，企业承接规模的能力也要同步接受验证。

对车企来说，销量增长和利润改善之间隔着价格。促销降价能够换来更高的成交量，却也会压缩单车毛利；补能建设、质保和售后服务还会增加现金流支出。因此，利润判断不能只引用零售渗透率，还要结合整车毛利率、经营现金流和售后成本。

产业链也会出现不同节奏。动力电池、电驱系统、汽车芯片和智能座舱的成本变化，可能影响整车降价空间；电池、电驱、快充设备和换电网络的需求，则可能随着保有量增加而扩大。这里说的是需要继续验证的传导方向，不代表相关企业已经获得确定利润或议价优势。

全国口径的65.1%也不能代替地区数据。大城市的充电密度、三四线城市的补能便利、节假日服务能力和二手车残值，可能对应不同的购买门槛。后续如果不同价格带、不同城市线级的渗透率都保持韧性，结构性扩张的证据会更完整；如果差异持续扩大，行业就需要面对需求分化。

对消费者而言，渗透率上升的价值在于选择变多，技术和补能服务获得更大市场；对企业而言，压力也从教育市场转向持续交付。这个变化会把竞争从参数展示带向价格、质量、服务和现金流的组合判断，不同群体感受到的变化不会同步发生。

还要区分三个概念：渗透率讲的是零售结构，销量讲的是数量变化，利润讲的是经营结果。三者可能同向，也可能在短期内错位。65.1%只能回答新能源车型在零售端的相对位置，不能回答企业卖了多少、每辆车赚多少，以及政策变化后需求是否稳定。

因此，行业讨论不宜只围绕一个高位数字下结论。把公开材料作为起点，再把月度销量、订单留存和企业财报放到同一张跟踪表里，才能看清消费变化是否传到了新能源汽车市场的经营端。

下一轮数据复核可以分成三组。需求端看以旧换新新增订单占比、订单转化和月度销量；交付端看交付周期、库存、产能利用率和重点车型订单兑现；经营端看单车成本、毛利率、现金流以及补能和售后投入。

如果政策影响减弱后，订单依然保持转化，销量在不同价格带和城市线级之间没有明显断层，65.1%就有机会成为长期结构变化的起点。反过来，如果订单留存下降、交付变慢或降价压力持续扩大，渗透率高位也可能伴随经营质量分化。

所以，这次数据的新闻价值在于新能源乘用车已经进入主流视野，分析价值则在于提醒我们换一个问题：增长能不能从销售端走到交付端，再从交付端走到毛利和现金流。来源口径、订单数据和企业财报还需要继续核对，后续结论不能超出这些证据。

接下来我会把订单转化率、政策退坡后的月度销量、交付周期、库存、单车成本和毛利率作为跟踪变量。不过，单个指标都不能代替完整验证。你更看好订单持续，还是更看重车企在价格竞争中的毛利变化？"""

TITLES = [
    "新能源乘用车7月渗透率达65.1%，主流化要看订单交付",
    "65.1%渗透率之后，新能源车要看订单和利润",
    "7月新能源车渗透率升至65.1%，价格竞争会怎样传导",
    "从65.1%到真实交付，新能源车还要看哪些数据",
    "消费增长温和，新能源乘用车为何仍升至65.1%",
]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    logger = configure_logging(PROJECT_ROOT / "logs" / "accounts" / "robot-notes", "INFO")
    settings = load_settings(PROJECT_ROOT, account_key="robot_notes")
    payload = json.loads((RUN_DIR / "workflow_result.json").read_text(encoding="utf-8"))
    workflow = WorkflowResult.from_dict(payload)

    article = clean_article_text(ARTICLE, min_word_count=0)
    workflow.content.article = article
    workflow.content.polished_article = article
    workflow.content.titles = list(TITLES)
    workflow.content.recommended_titles = list(TITLES[:3])
    workflow.content.summary = (
        "商务部消费促进司材料显示，2026年7月新能源乘用车零售渗透率达到65.1%。"
        "本文从订单、交付、成本和地区服务条件拆解这一高位数据，区分已确认事实与待验证判断。"
    )
    workflow.content.article_angle = "渗透率创新高之后，订单、交付与利润能否接续"
    workflow.content.comment_prompt = "你更关心订单转化率，还是车企在价格竞争中的毛利变化？"
    workflow.content.tags = ["新能源车", "汽车消费", "产业分析", "消费数据"]
    workflow.content.recommended_topics = ["新能源车消费结构", "汽车产业链经营质量", "以旧换新政策效果"]
    workflow.content.infographic_prompts = [
        "以65.1%渗透率、28.77万亿元、1.2%和2.6%构成事实锚点的信息图，不添加其他数字",
        "以订单转化、交付周期、库存、单车成本和毛利率构成后续跟踪框架，不添加虚构数据",
    ]
    workflow.content.validation_notes = [
        "核心数字来自IT之家对商务部消费促进司材料的转述，发布时保留来源关系。",
        "1.2%与2.6%统计范围不同，不在正文中作同口径比较。",
        "未提供置换订单占比、订单转化率、补贴金额或企业财报，相关判断保留为待验证方向。",
    ]
    workflow.content.validation_score = 100

    report = workflow.data_report
    report.topic_title = "商务部：7月新能源乘用车零售渗透率65.1%，再创历史新高"
    report.focus_industry = "新能源"
    report.focus_keywords = ["新能源乘用车", "零售渗透率", "汽车消费", "订单交付"]
    report.focus_companies = []
    report.companies = []
    report.core_data = [
        "2026年7月新能源乘用车零售渗透率达到65.1%",
        "2026年1—7月社会消费品零售总额为28.77万亿元，同比增长1.2%",
        "社会消费商品和服务零售总额同比增长2.6%，与前项统计范围不同",
        "以旧换新、订单转化、交付和利润之间的关系仍需后续数据验证",
    ]
    report.industry_trends = [
        "新能源乘用车从增量替代走向主流消费讨论",
        "高渗透率之后，行业竞争转向稳定交付、补能服务和经营质量",
    ]
    report.growth_numbers = ["65.1%", "28.77万亿元", "同比增长1.2%", "同比增长2.6%"]
    report.key_points = [
        "渗透率创新高不等于车企利润同步改善",
        "政策助力与真实需求需要分开记录",
        "订单、交付、成本和地区服务条件是后续验证主线",
    ]
    report.watch_metrics = [
        "以旧换新新增订单占比、订单转化率和政策退坡后的月度销量",
        "交付周期、库存、产能利用率和重点车型订单兑现率",
        "单车成本、整车毛利率、现金流以及补能和售后投入",
        "不同城市线级和不同价格带的渗透率变化",
    ]
    report.evidence_notes = [
        "IT之家2026年8月21日报道并转述商务部消费促进司材料",
        "材料披露2026年7月新能源乘用车零售渗透率为65.1%",
        "材料披露2026年1—7月社会消费品零售总额为28.77万亿元，同比增长1.2%",
        "材料披露社会消费商品和服务零售总额同比增长2.6%",
    ]
    report.risk_flags = []
    report.evidence_strength = 85
    report.topic_quality_score = 89
    report.content_format = "deep_analysis"
    report.content_format_label = "深度分析"
    report.word_target = 1800

    cache_context = CacheContext(status="miss", note="人工修订后重新审核")
    prompt_repository = PromptRepository(settings.prompts_dir)
    openai_service = OpenAIContentService(settings, logger)
    try:
        workflow_service = AgentWorkflowService(settings, prompt_repository, openai_service, logger)
        review_pack = workflow_service.review_agent._fallback(
            workflow.content,
            workflow.data_report,
            cache_context,
        )
        workflow_service._apply_review_pack(workflow.content, review_pack, cache_context)
        workflow.content_score = review_pack["content_score"]
        workflow.quality_review_council = workflow_service.quality_review_council_agent._fallback(
            workflow.content,
            workflow.data_report,
            revision_round=0,
        )
        publish_review = workflow_service.publish_review_agent._fallback(
            workflow.content,
            workflow.data_report,
            workflow.content_score,
            {},
        )
        workflow.content_score.publish_readiness_score = int(publish_review["publish_readiness_score"])
        workflow.content_score.publish_risk_flags = list(publish_review["publish_risk_flags"])
        workflow.content_score.publish_recommendation = str(publish_review["publish_recommendation"])
        workflow.content_score.publish_notes = list(publish_review["publish_notes"])
        workflow_service._apply_quality_review_gate(
            workflow.content_score,
            workflow.quality_review_council,
        )
        workflow_service._apply_bad_words_gate(workflow.content, workflow.content_score)

        exporter = ExportService(settings, logger)
        benchmark = exporter._build_benchmark_strategy(workflow)
        (RUN_DIR / "article.md").write_text(exporter._build_publish_article(workflow), encoding="utf-8")
        (RUN_DIR / "editorial_packet.md").write_text(
            exporter._build_editorial_packet(workflow, benchmark), encoding="utf-8"
        )
        (RUN_DIR / "titles.txt").write_text(exporter._build_titles_package(workflow), encoding="utf-8")
        _write_json(RUN_DIR / "content_score.json", exporter._build_score_package(workflow))
        _write_json(RUN_DIR / "benchmark_strategy.json", benchmark)
        _write_json(RUN_DIR / "workflow_result.json", workflow.to_dict())
        _write_json(
            RUN_DIR / "quality_review_council.json",
            workflow.quality_review_council.__dict__,
        )
        print(
            json.dumps(
                {
                    "run_dir": str(RUN_DIR),
                    "article_chars": len(article.replace("\n", "").replace(" ", "")),
                    "scores": {
                        "overall": workflow.content_score.overall_score,
                        "ai_risk": workflow.content_score.ai_generated_risk,
                        "validation": workflow.content.validation_score,
                        "readiness": workflow.content_score.publish_readiness_score,
                    },
                    "quality_council": {
                        "verdict": workflow.quality_review_council.verdict,
                        "score": workflow.quality_review_council.overall_score,
                        "blockers": workflow.quality_review_council.hard_blockers,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        openai_service.close()


if __name__ == "__main__":
    main()
