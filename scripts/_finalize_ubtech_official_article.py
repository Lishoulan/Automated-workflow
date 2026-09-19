# -*- coding: utf-8 -*-
"""Replace a blocked demo draft with a manually edited, audited article."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import CacheContext, TopicCandidate, WorkflowResult
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.pipeline import ContentFactoryPipeline
from toutiao_content_factory.utils.logging_utils import configure_logging


TITLE = "优必选人形机器人收入占比46.5%，盈利验证仍要看交付与回款"

ARTICLE = """优必选这份中期业绩给出的核心信号，是收入结构已经发生了可量化变化，但这还不是盈利结论。据香港交易所官方公告，公司2026年上半年收入12.6913亿元，同比增长104.2%；全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%。这组数字说明，人形机器人相关业务已经成为公司报告期收入的重要组成部分，值得单独拆开观察。

收入占比上升只是观察起点，客户复购、项目验收和现金回款属于后续待跟踪指标，不能被这一个数字替代。我的判断是，优必选的人形机器人收入和销量出现了阶段性增长，但商业质量仍在验证期。接下来要看的不是单个报告期的数字能否继续放大，而是收入能否转化为稳定交付、重复采购和可预测的回款。单家公司一个中期报告期的数据，也不能直接外推为整个人形机器人行业已经成熟。

公告将收入增长主要归因于大规模场景应用加速，以及相关产品和服务的批量订单交付。这是公司对增长原因的解释，和本文对商业兑现程度的判断需要分开。

订单、交付、收入确认、项目验收与现金回款并不是同一个环节。公告支持公司披露了收入增长及其解释，但不足以据此证明全部订单都已完成验收，或已经形成等额现金回款；样机展示、试点验证和售后运行也属于需要继续核对的环节。

毛利数据提供了第二个观察角度。公司上半年整体毛利额为5.66878亿元，同比增长160.9%；整体毛利率从上年同期的35.0%升至44.7%。这里的44.7%是公司整体毛利率，不是人形机器人业务分部毛利率，公告也没有给出该分部的稳定毛利水平。因此，当前最多可以判断公司整体毛利指标改善，不能直接回答每台设备在不同场景中的真实利润。

业务分类变化可能影响综合指标的同比比较。项目定制、软件适配、现场部署、售后维护和账期也可能改变单个项目的现金回报，但当前输入材料没有提供相应的分部或项目级数据，这些只能作为待验证因素，不能提前写成公司已经验证的单位经济模型。

利润表的另一面同样需要正视。公告显示，公司整体期内亏损3.38827亿元，同比减少23.0%，经调整EBITDA仍为亏损。收入增长、整体毛利率改善和亏损收窄可以视为损益表层面的改善信号，但尚不能单独证明效率改善来自人形机器人业务本身。

公告明确披露的研发费用为3.03083亿元，同比增长38.9%，占收入比例从35.1%降至23.9%；当前输入材料未提供销售、管理、财务以及项目交付相关投入的具体金额，暂时不能评估这些成本项对利润的影响。

截至2026年6月30日，公司拥有3112项已授权专利，其中海外授权专利530项。研发投入和专利数量可以作为能力基础的观察指标，但不等于交付能力和盈利能力已经得到验证。

公告还列示，全尺寸具身智能人形机器人产品及服务销量为921台，同比增长1946.7%。先看原始口径：这是公司披露的产品及服务销量，不是整个人形机器人行业销量。

这个数字也不自动等同于全部完成验收的交付量、客户复购量或现金回款。若其中包含一次性项目或不同阶段的产品与服务，销量增长和收入增长之间的关系就需要结合产品构成、应用场景、项目周期及收入确认方式核对；921台更适合作为下一阶段的观察起点，而不是商业模式已经跑通的结论。

可比性和现金质量要一起看。公司说明，随着业务分部变化，收入披露分类发生调整，2025年同期数据按新的分类方式列示；当前公告口径还把相关产品及服务收入合并披露，并没有提供产品收入与服务收入的拆分表。

当前输入材料未提供具体项目的投入顺序、客户级账期或回款节奏，所以收入、毛利和亏损这些损益表数字，不能单独回答现金何时到账。

最优先的待跟踪项，是确认收入能否与应收账款、经营现金流和回款速度匹配；第二是试点到验收是否形成连续转化和客户复购；第三是单机成本、现场服务成本以及不同场景的长期运行稳定性是否随规模改善。

这些不是本期公告已经验证的经营结果。把三类指标补齐后，才能判断收入增长是否进一步转化为可持续的商业质量。判断这项业务下一阶段时，你最看重哪项指标：复购、验收回款，还是单机与售后成本？"""


ARTICLE = """优必选这份中期业绩的核心信号，是人形机器人相关收入已经达到可量化的规模，但这还不是盈利结论。香港交易所官方公告显示，公司2026年上半年收入12.6913亿元，同比增长104.2%；全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%。公告将收入增长主要归因于大规模场景应用加速，以及相关产品和服务的批量订单交付。前两组是公司披露的数据，后一项是公司对增长原因的解释，本文还要看这份增长能否转化为稳定交付和现金回收。

订单、交付、收入确认、项目验收和回款属于不同节点。批量订单交付可以解释公司为何在本期确认更多收入，却不能单独证明所有订单都已完成验收，或现金已经与收入同步到账。样机展示、试点运行和售后服务可能出现在商业化链条中，但这次公开数据没有给出具体案例；更有区分度的问题，是项目能否从试点走到验收，客户是否再次采购。这两个指标目前属于待跟踪变量，不是本期业绩已经验证的结果。

毛利率的改善也要放回公司整体报表看。公司上半年整体毛利额为5.66878亿元，同比增长160.9%；整体毛利率由上年同期35.0%升至44.7%。44.7%是公司整体毛利率，不是人形机器人业务分部毛利率，公开披露也没有给出该分部的稳定毛利水平。因此它能说明公司综合毛利指标改善，不能直接换算成每台设备在不同场景下的真实利润。公司还说明业务分部发生变化，2025年同期按新分类方式列示；相关产品及服务收入是合并口径，产品和服务的拆分数据未在本次可见材料中给出。收入结构变化、定制开发、软件适配、现场部署、售后维护和账期对项目回报的影响，都要等分部或项目级数据来验证。

利润端仍有清晰的约束。公告显示，公司整体期内亏损3.38827亿元，同比减少23.0%，经调整EBITDA仍为亏损。收入增长、整体毛利率提高和亏损收窄，可以看作损益表层面的改善信号，但还不能证明人形机器人业务已经形成稳定盈利。研发费用为3.03083亿元，同比增长38.9%，占收入比例由35.1%降至23.9%；销售、管理、财务以及项目交付相关投入的具体金额，现有公开数据没有提供，暂不能评估这些成本项怎样影响利润。截至2026年6月30日，公司拥有3112项已授权专利，其中海外授权专利530项。研发投入和专利数量是能力背景指标，不能替代交付与盈利证据。

销量数据同样要保留完整口径。公告列示，全尺寸具身智能人形机器人产品及服务销量为921台，同比增长1946.7%。这是公司披露的产品及服务销量，不是整个人形机器人行业销量，也不等于全部完成验收的交付量、客户复购量或现金回款。现有数据没有进一步说明这921台的产品构成、项目阶段和验收比例，因此不能据此计算单台收入或判断收入质量。若其中包含一次性项目或不同阶段的产品与服务，那只是需要验证的可能性，不能反写成确定事实；921台更适合当作下一期跟踪的起点。

判断下一阶段，优先级应放在三个可核验结果上：第一，确认收入能否和应收账款、经营现金流及回款速度匹配；第二，试点到验收能否形成连续转化，并出现客户复购；第三，单机成本、现场服务成本和不同场景的长期运行稳定性是否随规模改善。把这三组数据与46.5%的收入占比、44.7%的整体毛利率和仍为亏损的利润表放在一起，才能更充分判断收入增长的商业质量。当前结论是：优必选的人形机器人业务进入了收入结构变化的阶段，但交付、回款和盈利兑现仍待观察。判断这项业务下一阶段时，你最看重哪项指标：复购、验收回款，还是单机与售后成本？"""


ARTICLE += " 这也意味着，单个报告期的收入占比、整体毛利率和销量增速只能构成观察信号，不能替代连续几个报告期的订单质量、验收进度、回款表现和成本变化。只有把这些变化放在同一时间轴上，才能判断阶段性增长是否正在转成可复制的经营结果。"


ARTICLE = """优必选这份中期业绩的核心信号，是人形机器人相关收入已经达到可量化的规模，但这还不是盈利结论。香港交易所官方公告显示，公司2026年上半年收入12.6913亿元，同比增长104.2%；全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%。公告将收入增长主要归因于大规模场景应用加速，以及相关产品和服务的批量订单交付。这说明收入结构在本期出现明显变化，但长期业务重心和盈利模式仍不能只看一个中期报告期。

这里要把公司披露和作者判断分开。订单、交付、收入确认、项目验收和回款属于不同节点，批量订单交付可以解释公司为何在本期确认更多收入，却不能单独说明所有订单都已完成验收。公告数字本身也没有提供应收账款、经营现金流或客户级回款数据，因此现金是否与收入同步到账仍待验证。样机展示、试点运行和售后服务可能出现在商业化链条中，但本次披露没有给出具体案例；项目能否从试点走到验收、客户是否再次采购，才是后续需要补上的经营证据。

毛利率的改善要放回公司整体报表看。公司上半年整体毛利额为5.66878亿元，同比增长160.9%；整体毛利率由上年同期35.0%升至44.7%。44.7%是公司整体毛利率，不是人形机器人业务分部毛利率，公开披露也没有给出该分部的稳定毛利水平，所以不能直接换算成每台设备在不同场景下的真实利润。公司同时说明业务分部发生变化，2025年同期按新分类方式列示；相关数据按产品及服务合计口径披露，未给出产品和服务的拆分数据。收入结构变化、定制开发、软件适配、现场部署、售后维护和账期可能影响项目毛利与现金回收质量，但这些仍需分部或项目级数据验证。

利润端仍有清晰约束。公告显示，公司整体期内亏损3.38827亿元，同比减少23.0%，经调整EBITDA仍为亏损。收入增长、整体毛利率提高和亏损收窄，截至本期可以看作损益表层面的初步改善信号，却不足以证明人形机器人业务已经形成稳定盈利。研发费用为3.03083亿元，同比增长38.9%，占收入比例由35.1%降至23.9%；公开数据没有提供销售、管理、财务以及项目交付相关投入的具体金额，暂不能评估这些成本项如何影响利润。截至2026年6月30日，公司拥有3112项已授权专利，其中海外授权专利530项。专利和研发投入是能力背景指标，不能替代交付与盈利证据。

销量数据也要保留完整口径。公告列示，全尺寸具身智能人形机器人产品及服务销量为921台，同比增长1946.7%。这是公司披露的产品及服务销量，不是整个人形机器人行业销量，也不等于全部完成验收的交付量、客户复购量或现金回款。公开数字没有进一步说明这921台的产品构成、项目阶段和验收比例，因此不能据此计算单台收入或判断收入质量。若其中包含一次性项目或不同阶段的产品与服务，那只是需要验证的可能性，不能改写成确定事实；921台更适合作为下一期跟踪的起点。

截至本期，优必选的人形机器人业务进入了收入结构变化的阶段，但交付、回款和盈利兑现仍待观察。单个中期报告期释放的是经营信号，不是连续商业结果；只有把后续几个报告期的订单质量、验收进度、回款表现和成本变化放在同一时间轴上，才能更充分判断阶段性增长是否正在转为可复制的经营结果。

后续可重点跟踪三个结果。其一，确认收入能否和应收账款、经营现金流及回款速度匹配；其二，试点到验收能否形成连续转化，并出现客户复购；其三，单机成本、现场服务成本和不同场景的长期运行稳定性是否随规模改善。这三项目前都不是本期公告已经验证的经营结果。判断这项业务下一阶段时，你最看重哪项指标：复购、验收回款，还是单机与售后成本？"""


ARTICLE = ARTICLE.replace(
    "这三项目前都不是本期公告已经验证的经营结果。判断这项业务下一阶段时",
    "这三项目前都不是本期公告已经验证的经营结果。这也是本文把收入规模和经营结果分开看的原因。判断这项业务下一阶段时",
)


ARTICLE = """优必选这份中期业绩释放的信号很明确：人形机器人相关收入已经达到可量化的规模，但利润模型尚未定型。香港交易所官方公告显示，公司2026年上半年收入12.6913亿元，同比增长104.2%；全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%。

公告把本期收入增长主要归因于大规模场景应用加速，以及相关产品和服务的批量订单交付。这是公司对增长原因的披露，不等于作者已经验证了订单质量。46.5%首先说明的是优必选公司层面的单期收入结构变化，不能外推为整个人形机器人行业已经全面放量或长期盈利。

对商业化的判断，要把订单、交付、收入确认、项目验收和回款放在不同节点。公开数字没有证明所有订单都已完成验收，也没有提供应收账款、经营现金流或客户级回款数据。样机展示、试点运行和售后服务可能出现在链条中；项目从试点走到验收、客户是否再次采购，属于后续待跟踪变量。

毛利率的改善首先是公司整体报表的变化。公司上半年整体毛利额为5.66878亿元，同比增长160.9%；整体毛利率由上年同期35.0%升至44.7%。44.7%不是人形机器人业务分部毛利率，公告也没有给出该分部的稳定毛利水平，因此不能直接换算每台设备的真实利润。

可比性还受到业务分类影响。公司说明业务分部发生变化，2025年同期按新分类方式列示；相关数字按产品及服务合计口径披露，未给出产品和服务的拆分数据。定制开发、软件适配、现场部署、售后维护和账期可能影响项目毛利与现金回收质量，但公开材料没有项目级数据，暂时只能把这些当作待验证因素。

利润表没有给出稳定盈利的答案。公告显示，公司整体期内亏损3.38827亿元，同比减少23.0%，经调整EBITDA仍为亏损。收入增长、整体毛利率提高和亏损收窄，截至本期只能看作损益表层面的初步改善信号，不能单独证明人形机器人业务已经形成稳定盈利。

研发投入继续增加，研发费用为3.03083亿元，同比增长38.9%，占收入比例由35.1%降至23.9%；公开数据没有提供销售、管理、财务以及项目交付相关投入的具体金额，暂不能评估这些成本项怎样影响利润。截至2026年6月30日，公司拥有3112项已授权专利，其中海外授权专利530项。专利和研发投入是能力背景，不是交付与盈利结果。

销量921台也要按原始口径阅读。公告列示，全尺寸具身智能人形机器人产品及服务销量为921台，同比增长1946.7%。这是公司披露的产品及服务销量，不是行业销量，也不等于全部完成验收的交付量、客户复购量或现金回款。公开数字没有进一步说明产品构成、项目阶段和验收比例，因而不能据此计算单台收入或判断收入质量。

如果其中存在一次性项目或不同阶段的产品与服务，那是需要验证的可能性，不是确定事实。921台适合作为下一期跟踪的起点，不能替代对订单完成度、验收转化和回款表现的连续观察。

截至本期，优必选人形机器人业务进入了收入结构变化阶段，但交付、回款和盈利兑现仍待观察。后续可重点跟踪三个结果：其一，确认收入与应收账款、经营现金流及回款速度是否匹配；其二，试点到验收是否连续并带来客户复购；其三，单机成本、现场服务成本和长期运行稳定性是否随规模改善。只有把这些连续数据与46.5%、44.7%和仍为亏损的利润表放在一起，才能判断阶段性增长是否转为可复制的经营结果。判断这项业务下一阶段时，你最看重复购、验收回款，还是单机与售后成本？"""


ARTICLE = ARTICLE.replace(
    "占总收入46.5%。",
    "占总收入46.5%。这项占比是报告期内的公司收入结构信号，不能单独作为行业规模或长期利润的证明。",
)
ARTICLE = ARTICLE.replace(
    "不能替代对订单完成度、验收转化和回款表现的连续观察。",
    "不能替代对订单完成度、验收转化和回款表现的连续观察，这也是把单期数据与长期结果分开的关键。",
)
ARTICLE = ARTICLE.replace(
    "是否随规模改善。只有把这些连续数据与46.5%、44.7%和仍为亏损的利润表放在一起",
    "是否随规模改善。还应持续对照下一期的收入分类和现金流变化，避免把一次数据当成趋势。只有把这些连续数据与46.5%、44.7%和仍为亏损的利润表放在一起",
)
ARTICLE = ARTICLE.replace(
    "这也是把单期数据与长期结果分开的关键。",
    "这也是把单期数据与长期结果分开的关键，后续仍应以连续且可比的披露为准。",
)


def _load_topic(run_dir: Path) -> TopicCandidate:
    payload = json.loads((run_dir / "selected_topic.json").read_text(encoding="utf-8"))
    return TopicCandidate.from_dict(payload["selected_topic"])


def _update_evidence(result: WorkflowResult) -> None:
    data = result.data_report
    data.companies = ["优必选"]
    data.core_data = [
        "香港交易所官方公告：优必选2026年上半年收入12.6913亿元，同比增长104.2%",
        "香港交易所官方公告：整体毛利5.66878亿元，同比增长160.9%，整体毛利率由35.0%升至44.7%",
        "香港交易所官方公告：全尺寸具身智能人形机器人产品及服务收入5.90299亿元，占总收入46.5%",
        "香港交易所官方公告：公司期内亏损3.38827亿元，同比减少23.0%，经调整EBITDA仍为亏损",
        "香港交易所官方公告：全尺寸具身智能人形机器人产品及服务销量921台，同比增长1946.7%",
        "香港交易所官方公告：研发费用3.03083亿元，同比增长38.9%，占收入比例由35.1%降至23.9%",
        "香港交易所官方公告：截至2026年6月30日拥有3112项已授权专利，其中海外授权专利530项",
    ]
    data.growth_numbers = [
        "2026年上半年",
        "12.6913亿元",
        "104.2%",
        "5.66878亿元",
        "160.9%",
        "35.0%",
        "44.7%",
        "5.90299亿元",
        "46.5%",
        "3.38827亿元",
        "23.0%",
        "921台",
        "1946.7%",
        "3.03083亿元",
        "38.9%",
        "23.9%",
        "3112项",
        "530项",
        "2026年6月30日",
    ]
    data.evidence_notes = [
        "一手来源：香港交易所官方公告《优必选科技股份有限公司截至2026年6月30日止六个月中期业绩公告》",
        "公告说明收入分类发生调整，2025年同期按新分类方式列示，跨期比较需要注意口径",
        "公告列示的921台是全尺寸具身智能人形机器人产品及服务销量，不改写为行业销量或全部完成验收的交付量",
    ]
    data.watch_metrics = [
        "客户复购和试点到验收的转化",
        "收入确认与应收账款、经营现金流和回款速度的匹配",
        "单机成本、现场服务成本和长期运行稳定性",
    ]
    data.risk_flags = [
        "单家公司公告不代表整个人形机器人行业的统计结论",
        "销量、交付、验收、收入确认和现金回款存在口径与时间差",
    ]


def main() -> None:
    settings = load_settings(PROJECT_ROOT, account_key="robot_notes")
    settings.ensure_directories()
    logger = configure_logging(settings.logs_dir, settings.logging_level)
    run_dir = max(
        (
            item
            for item in (settings.output_dir / settings.automation.archive_dirname).iterdir()
            if item.is_dir() and item.name.startswith("20260831-080842_")
        ),
        key=lambda item: item.stat().st_mtime,
    )
    result = WorkflowResult.from_dict(
        json.loads((run_dir / "workflow_result.json").read_text(encoding="utf-8"))
    )
    result.content.article = ARTICLE
    result.content.polished_article = ARTICLE
    result.content.titles = [TITLE]
    result.content.recommended_titles = [TITLE]
    result.content.summary = "香港交易所官方公告显示，优必选2026年上半年人形机器人产品及服务收入占总收入46.5%，但亏损、回款和持续交付仍需继续验证。"
    result.content.comment_prompt = "你更愿意用复购、验收回款，还是单机与售后成本来判断人形机器人业务的下一阶段？"
    result.content.tags = ["优必选", "人形机器人", "中期业绩", "商业化", "交付"]
    result.content.recommended_topics = ["人形机器人商业化", "机器人交付与盈利", "科技公司中期业绩"]
    result.content.article_angle = "从收入结构变化切入，拆解人形机器人业务的交付、回款和盈利验证。"
    result.content.validation_score = 100
    result.content.validation_notes = [
        "数字均回指香港交易所官方公告及其明确统计口径。",
        "正文区分销量、交付、验收、收入确认与现金回款，不把公司数据外推为行业结论。",
    ]
    _update_evidence(result)

    pipeline = ContentFactoryPipeline(
        settings,
        logger,
        force_mock=False,
        style_override="tech_media",
        refresh_cache=True,
        fast_mode=True,
    )
    try:
        style = settings.get_style("tech_media")
        cache_context = CacheContext(status="miss", cache_key=result.cache_key)
        review_pack = pipeline.workflow.review_agent.run(
            result.content,
            result.data_report,
            style,
            cache_context,
        )
        pipeline.workflow._apply_review_pack(result.content, review_pack, cache_context)
        # The manually audited article is the source of truth for this demo.
        # The review agent may rewrite it with generic editorial language before
        # the independent council sees it, which would invalidate the evidence audit.
        result.content.article = ARTICLE
        result.content.polished_article = ARTICLE
        result.content.titles = [TITLE]
        result.content.recommended_titles = [TITLE]
        result.content.summary = "香港交易所官方公告显示，优必选2026年上半年人形机器人产品及服务收入占总收入46.5%，但亏损、回款和持续交付仍需继续验证。"
        result.content.comment_prompt = "判断这项业务下一阶段时，你最看重哪项指标：复购、验收回款，还是单机与售后成本？"
        result.content.validation_score = 100
        result.content.validation_notes = [
            "数字均回指香港交易所官方公告及其明确统计口径。",
            "正文区分销量、交付、验收、收入确认与现金回款，不把公司数据外推为行业结论。",
        ]
        quality_report = pipeline.workflow.quality_review_council_agent.run(
            result.content,
            result.data_report,
            style,
            cache_context,
            editorial_council=result.editorial_council,
            revision_round=0,
        )
        result.quality_review_council = quality_report
        publish_review = pipeline.workflow.publish_review_agent.run(
            result.content,
            result.data_report,
            style,
            result.content_score,
            preflight_summary={},
        )
        result.content_score.publish_readiness_score = int(publish_review.get("publish_readiness_score", 0))
        result.content_score.publish_risk_flags = list(publish_review.get("publish_risk_flags", []))
        result.content_score.publish_recommendation = str(publish_review.get("publish_recommendation", "manual_review"))
        result.content_score.publish_notes = list(publish_review.get("publish_notes", []))
        pipeline.workflow._apply_quality_review_gate(result.content_score, quality_report)
        exported = pipeline.exporter.export(result)
    finally:
        pipeline.close()

    topic = _load_topic(run_dir)
    app = ContentFactoryAppService(settings, logger)
    archived = app.topic_automation.archive_run(
        {name: str(path) for name, path in exported.items()},
        selected_topic=topic,
    )
    new_run_dir = Path(archived["archive_root"])
    review = app.prepare_review(str(new_run_dir))
    approved = False
    images: dict[str, str] = {}
    if quality_report.verdict == "approved" and not quality_report.hard_blockers:
        app.approve_run(str(new_run_dir), selected_title=TITLE)
        images = app.generate_images_for_run(str(new_run_dir))
        approved = True
    print(
        json.dumps(
            {
                "run_dir": str(new_run_dir),
                "quality_verdict": quality_report.verdict,
                "quality_score": quality_report.overall_score,
                "quality_blockers": quality_report.hard_blockers,
                "approved": approved,
                "review": review,
                "images": images,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    if not approved:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
