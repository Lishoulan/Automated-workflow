"""兜底发布：无人值守时也要有产出，但安全底线不能破。

背景：流程跑在 GitHub Actions 上，没有人工介入。原设计在所有候选都不合格时
落到 review_required 等人处理，等于当天 0 产出。兜底逻辑在「守得住安全底线」
的前提下放行一篇——放行的只是模型的软指控，确定性硬伤依然否决。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

from toutiao_content_factory.services.app_service import ContentFactoryAppService

# 安全底线中的字数下限是 1750（自动发布模式），所以这里的样例正文必须
# 明显高于该值，否则测出来的失败是「样例太短」而不是「逻辑有问题」。
CLEAN_ARTICLE = (
    "优必选柳州工厂的投产，说明供给侧已经具备万台级量产能力。据公开材料显示，"
    "该工厂按生产节拍每十分钟下线一台，年规划产能超过一万台，主要面向工业与"
    "商用场景的订单交付。这条消息本身并不复杂，却把一个长期停留在概念层面的"
    "问题推到了台前：当产能不再是瓶颈，人形机器人这门生意究竟靠什么赚钱，"
    "又能赚多久。\n\n"
    "但产线只证明供给侧，不证明需求侧。后续仍要看产能利用率、交付之后的实际"
    "开机率、客户第二年是否续单，以及单台设备能留下多少毛利。这些指标才决定"
    "这门生意能否持续。产线剪彩是新闻，产能利用率才是账本，两者之间通常隔着"
    "一到两年的验证周期。\n\n"
    "从产业链分工看，本体制造只是其中一环，而且未必是利润最厚的一环。减速器、"
    "丝杠、力传感器、灵巧手等核心零部件的国产化进度，会直接影响整机的成本结构"
    "和交付节奏，也在很大程度上决定了利润最终在链条中的归属。\n\n"
    "一个可观察的判断依据是：如果下半年公开披露的订单中，来自工业搬运、仓储"
    "分拣等真实商用场景的比例持续上升，那么规模化的说法才有支撑；如果订单仍"
    "以科研院校采购、展厅演示为主，那么所谓量产更接近产能建设，而非市场需求，"
    "两者在商业含义上并不相同。\n\n"
    "价格也是一条线索。当前整机报价与人工成本之间的比值，决定了替代逻辑能否"
    "成立。若单台设备的价格相当于一名工人两年以内的综合用工成本，企业的采购"
    "意愿会明显不同；若相当于十年，那么即便技术指标再好，落地也会慢得多。\n\n"
    "还需要注意的是财务口径。产能规划通常按理想爬坡曲线给出，而实际爬坡受制于"
    "供应链良率、调试周期和客户验收节奏。把规划产能直接等同于销售收入，是这一轮"
    "讨论里最常见的误读，也是最容易在后续财报中被修正的部分。\n\n"
    "另外，机器人本体的销售只是起点。真正具备持续性的收入往往来自后续的维护、"
    "备件、软件授权与产线改造服务。这类收入占比越高，商业模式越稳；如果收入结构"
    "里绝大多数仍是一次性硬件销售，那么每一轮扩张都需要新的资本投入来支撑。\n\n"
    "从资本开支的角度看，产线投入属于重资产。设备折旧、厂房租赁、人员培训都会"
    "先于收入发生。在订单尚未大规模落地的阶段，这些成本会直接压低当期利润率。"
    "因此判断企业健康状况时，比起产线有多先进，更应该关注经营性现金流能否覆盖"
    "扩张所需。\n\n"
    "行业标准也是一个变量。当接口、通信协议、安全规范尚未统一时，客户会担心"
    "今天采购的设备两年后无法融入新的体系。这种顾虑会拉长决策周期，使得订单"
    "集中在少数已经形成生态的供应商手里，中小厂商的机会窗口因此收窄。\n\n"
    "人才结构同样值得留意。整机制造需要机械、电子、控制、算法等多工种协同，"
    "团队的经验积累很难被资本迅速复制。招人速度与项目交付节奏是否匹配，往往是"
    "观察一家企业执行力的窗口。\n\n"
    "再看需求端的替代逻辑。制造业招工难、人工成本逐年上升，是最容易被引用的"
    "理由。但替代并非一步到位，多数工厂会先在单个工位试点，跑通之后再逐步扩线。"
    "试点到扩线之间的比例，也就是常说的复制率，比首单金额更能说明问题。\n\n"
    "所以，把这条消息放进更长的周期里看，它更像是一个起点而不是结论。产能到位"
    "意味着供给约束解除，接下来真正要回答的是谁在用、能用住多久、愿不愿意续单。"
    "这三个问题的答案，会在未来几个季度的公开数据里逐渐浮现，也值得持续关注。\n\n"
    "还有一个容易被忽略的变量是租赁模式。部分厂商开始尝试按月计费的设备租赁，"
    "把一次性采购拆成持续支出。这种模式降低了客户的决策门槛，但对厂商的资金"
    "周转和残值管理提出了更高要求。租赁占比上升究竟是需求好转的信号，还是"
    "客户不愿一次性投入的妥协，需要结合回款周期一起判断。\n\n"
    "产能爬坡的节奏同样会自我暴露。如果企业连续几个季度都不披露实际出货量，"
    "而是反复强调规划产能和在手意向订单，这本身就是一个值得警惕的信号。"
    "意向订单与已确认收入之间的差距，往往在下一份财报里会以存货或应收账款"
    "的形式显现出来。\n\n"
    "海外市场是另一条可能的出口。部分地区的用工成本更高、劳动力缺口更大，"
    "对自动化设备的支付意愿也更强。但出口同时意味着认证、售后网络和汇率"
    "风险，短期很难成为主要收入来源，更适合作为观察长期天花板的参考，而不是"
    "当期业绩的支撑。\n\n"
    "综上，这条消息的价值在于提示产能已经就位，但真正决定长期回报的变量仍未"
    "明朗。需要继续跟踪交付、回款和复购这三个环节，而不是把规划当成已经实现的"
    "结果。对关注这条赛道的读者来说，保持跟踪比急于下结论更有意义。"
)


UNRELATED_SOURCE = "完全不相干的素材内容，讲的是另一件事，与正文没有任何连续重合的段落。" * 30


def _make_run(
    tmp_path: Path,
    article: str = CLEAN_ARTICLE,
    titles: str = "推荐标题：\n1. 优必选柳州工厂投产，万台级量产能不能变成订单\n",
    *,
    source: str | None = UNRELATED_SOURCE,
    ai_risk: int = 18,
    hot_topic: bool = False,
) -> Path:
    """构造一个 run 目录。

    ⚠️ 2026-09-17（D2）：这里原先的默认标题是 `测试标题`（4 个字）。它会被
    `_check_title()` 判「标题过短，信息量不足」，于是 `_fallback_title_for()` 返回空 ——
    在**删掉** `_fallback_title_for` 末尾那句 `or candidates[0]` 的保底之前，
    这些用例是靠那个保底才通过的。也就是说，本文件里「抄录率 / AI 风险是否放行」
    这几条用例其实一直在悄悄依赖「标题校验可以被绕过」这个缺陷。
    现在换成一个真正合格的标题，让每条用例只考察它自己声称考察的那个维度。
    """
    run_dir = tmp_path / "20260915-120000_测试选题"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "article.md").write_text(article, encoding="utf-8")
    (run_dir / "titles.txt").write_text(titles, encoding="utf-8")
    (run_dir / "content_score.json").write_text(
        json.dumps(
            {
                "scores": {
                    "overall_score": 85,
                    "publish_readiness_score": 80,
                    "ai_generated_risk": ai_risk,
                    "validation_score": 95,
                },
                "publish_recommendation": "manual_review",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if source is not None:
        (run_dir / "source_material_full.txt").write_text(source, encoding="utf-8")
    if hot_topic:
        (run_dir / "workflow_result.json").write_text(
            json.dumps({"data_report": {"is_hot_topic": True}}, ensure_ascii=False),
            encoding="utf-8",
        )
    return run_dir


def _service(auto_publish: bool = True) -> ContentFactoryAppService:
    service = ContentFactoryAppService.__new__(ContentFactoryAppService)
    service.logger = logging.getLogger("test_fallback")
    publishing = MagicMock()
    publishing.enabled = True
    publishing.auto_publish = auto_publish
    publishing.draft_only = False
    settings = MagicMock()
    settings.publishing = publishing
    review = MagicMock()
    review.max_auto_publish_ai_risk = 40
    review.hot_topic_max_auto_publish_ai_risk = 50
    settings.review = review
    service.settings = settings
    return service


def test_clean_article_passes_safety_check(tmp_path: Path) -> None:
    service = _service()
    run_dir = _make_run(tmp_path)
    safe, reasons, _notes = service._fallback_safety_check(run_dir)
    assert safe is True, reasons


def test_internal_label_blocks_fallback(tmp_path: Path) -> None:
    service = _service()
    run_dir = _make_run(tmp_path, article=CLEAN_ARTICLE + "\nmanual_paste：素材来源说明")
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("内部标签" in r for r in reasons)


def test_sequence_artifact_blocks_fallback(tmp_path: Path) -> None:
    service = _service()
    run_dir = _make_run(tmp_path, article=CLEAN_ARTICLE + "\n第一句。', '第二句。")
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("序列化残留" in r for r in reasons)


def test_serialized_dict_residue_blocks_fallback(tmp_path: Path) -> None:
    """修复会把 `', '` 换成段落分隔，掩盖原始特征——残留的引号键名必须仍被拦截。"""
    service = _service()
    article = CLEAN_ARTICLE + "\n[{'judgment': '垃圾片段', 'fact': '另一段垃圾'}]\n"
    run_dir = _make_run(tmp_path, article=article)
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("序列化残留" in r for r in reasons)


def test_high_copy_ratio_blocks_fallback(tmp_path: Path) -> None:
    """抄录率用 25% 复核线，与人工审核环节保持一致。"""
    service = _service()
    source = CLEAN_ARTICLE
    run_dir = _make_run(tmp_path, article=CLEAN_ARTICLE)
    (run_dir / "source_material_full.txt").write_text(source, encoding="utf-8")
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("抄录" in r for r in reasons)


def test_low_copy_ratio_passes(tmp_path: Path) -> None:
    service = _service()
    run_dir = _make_run(tmp_path)
    safe, reasons, notes = service._fallback_safety_check(run_dir)
    assert safe is True, reasons
    assert any("抄录" in n for n in notes), notes


def test_missing_source_material_blocks_fallback(tmp_path: Path) -> None:
    """抄录率量不出来 ≠ 没问题：宁可不发，也不赌一次扣 5 分。"""
    service = _service()
    run_dir = _make_run(tmp_path, source=None)
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("原创性无法核验" in r for r in reasons)


def test_short_source_material_blocks_fallback(tmp_path: Path) -> None:
    """素材原文过短（<200 字）时比对基准不可信，同样按无法核验处理。"""
    service = _service()
    run_dir = _make_run(tmp_path, source="素材太短，只有几十个字，不足以做比对基准。")
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("原创性无法核验" in r for r in reasons)


def test_hot_topic_without_source_material_is_allowed(tmp_path: Path) -> None:
    """热点选题天然只有标题、没有原文可比对，此时不按「无法核验」否决。"""
    service = _service()
    run_dir = _make_run(tmp_path, source=None, hot_topic=True)
    safe, reasons, notes = service._fallback_safety_check(run_dir)
    assert safe is True, reasons
    assert any("未量化" in n for n in notes), notes


def test_high_ai_risk_blocks_fallback(tmp_path: Path) -> None:
    """AI 生成风险超过运营方配置上限（非热点 40）时一票否决。"""
    service = _service()
    run_dir = _make_run(tmp_path, ai_risk=80)
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("AI 生成风险" in r for r in reasons)


def test_ai_risk_at_limit_is_allowed(tmp_path: Path) -> None:
    """上限是「高于才拦」，恰好等于 40 应当放行。"""
    service = _service()
    run_dir = _make_run(tmp_path, ai_risk=40)
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is True, reasons


def test_no_title_blocks_fallback(tmp_path: Path) -> None:
    service = _service()
    run_dir = _make_run(tmp_path, titles="")
    (run_dir / "titles.txt").unlink()
    safe, reasons, _ = service._fallback_safety_check(run_dir)
    assert safe is False
    assert any("标题" in r for r in reasons)


def test_fallback_disabled_when_auto_publish_off(tmp_path: Path) -> None:
    service = _service(auto_publish=False)
    run_dir = _make_run(tmp_path)
    assert service._try_fallback_publish([{"run_dir": str(run_dir)}]) is None


def test_fallback_declines_when_no_candidate_is_safe(tmp_path: Path) -> None:
    service = _service()
    run_dir = _make_run(tmp_path, article=CLEAN_ARTICLE + "\nTODO 待补充数据")
    assert service._try_fallback_publish([{"run_dir": str(run_dir)}]) is None


def test_rank_key_prefers_readiness_then_overall(tmp_path: Path) -> None:
    service = _service()
    low = {"score_snapshot": {"publish_readiness_score": 60, "overall_score": 90}}
    high = {"score_snapshot": {"publish_readiness_score": 85, "overall_score": 70}}
    assert service._fallback_rank_key(high) > service._fallback_rank_key(low)


def test_originality_block_reason_matrix() -> None:
    """严格门与兜底门共用的原创判定矩阵。"""
    reason = ContentFactoryAppService._originality_block_reason
    assert reason(None, is_hot_topic=True) == ""
    assert "无法核验" in reason(None, is_hot_topic=False)
    assert reason(12.0, is_hot_topic=False) == ""
    assert "抄录" in reason(30.7, is_hot_topic=False)
    # 热点选题放行「量不出来」，但一旦量得出来，同样守 25% 线。
    assert "抄录" in reason(25.0, is_hot_topic=True)


def test_strict_auto_approval_enforces_the_same_copy_line() -> None:
    """严格门必须和兜底门用同一条 25% 抄录线。

    2026-09-16 02:02 那篇抄录 30.7% 的图文被自动批准并发布，就是栽在这里：
    content_guard 只把 ≥25% 写成 `human_review_required`，而那个字段全项目
    没有任何地方读，于是分数够就直接从「严格」路径放行了。
    """
    service = _service()
    snapshot = {
        "overall_score": 85,
        "publish_readiness_score": 80,
        "ai_generated_risk": 20,
        "validation_score": 95,
        "article_char_count": 2000,
    }
    assert (
        service._qualifies_for_auto_approval(
            snapshot,
            content_guard_report={"blocked": False, "source_copy_ratio": 30.7},
        )
        is False
    )
    assert service._qualifies_for_auto_approval(
        snapshot,
        content_guard_report={"blocked": False, "source_copy_ratio": 18.2},
    )


def test_strict_auto_approval_blocks_unverifiable_originality() -> None:
    """非热点选题量不出抄录率时不放行；热点选题例外。"""
    service = _service()
    snapshot = {
        "overall_score": 85,
        "publish_readiness_score": 80,
        "ai_generated_risk": 20,
        "validation_score": 95,
        "article_char_count": 2000,
    }
    assert (
        service._qualifies_for_auto_approval(
            snapshot, content_guard_report={"source_copy_ratio": None}
        )
        is False
    )
    assert service._qualifies_for_auto_approval(
        {**snapshot, "is_hot_topic": 1},
        content_guard_report={"source_copy_ratio": None},
    )

