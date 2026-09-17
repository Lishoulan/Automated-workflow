"""Concrete Anchor V2 回归测试（Phase 1B / Blocker 1）。

这一组测试锁的是「锚点提取到底对不对」——因为标题闸门 `missing_concrete_hook`
近 100% 判 FAIL 的根因**不在标题**，而在这里：量化值被截断、垃圾串被当公司名、
真正的公司名提不出来。每一条断言都对应一个在真实语料上量到过的具体错误。
"""

from __future__ import annotations

import pytest

from toutiao_content_factory.services.concrete_anchors import (
    CONF_COMPANY,
    CONF_QUANTITY_STRONG,
    CONF_QUANTITY_WEAK,
    GATE_MIN_ANCHORS,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNAVAILABLE,
    concrete_anchor_summary,
    concrete_hook_status,
    extract_concrete_anchors,
    high_confidence_anchors,
    quantity_token,
)


def _texts(body: str) -> set[str]:
    return {anchor.text for anchor in extract_concrete_anchors(body)}


# ---------------------------------------------------------------------------
# 一、量化值必须是**完整 token**（旧实现会截断）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number,tail,expected",
    [
        # 旧实现：单位表里没有 `个月`，`18个月` 被切成 `18个`
        ("18", "个月，", "18个月"),
        # 旧实现：没有量纲组合，先匹到 `万` 就返回，`460万台` 被切成 `460万`
        ("460", "万台目标", "460万台"),
        ("10", "万元", "10万元"),
        ("5", "亿元", "5亿元"),
        ("2", "万台", "2万台"),
        ("30", "%", "30%"),
        ("5000", "台", "5000台"),
        ("3", "家", "3家"),
        ("2", "倍", "2倍"),
        # 只有数量级、没有量纲，也算信息
        ("2.5", "万", "2.5万"),
        ("10", "亿", "10亿"),
    ],
)
def test_quantified_tokens_are_kept_whole(number: str, tail: str, expected: str) -> None:
    token, _confidence = quantity_token(number, tail)
    assert token == expected


@pytest.mark.parametrize("bare", ["1", "2", "5", "9", "32", "300"])
def test_bare_digits_are_still_not_quantified(bare: str) -> None:
    """单字/裸数字无单位、非四位年份 —— 仍然不算量化信息（D4 的结论不能丢）。"""
    token, _confidence = quantity_token(bare, "")
    assert token == ""


def test_four_digit_years_are_recorded_bare() -> None:
    """年份记成裸数字，标题写 `2027年` 或 `2027` 都能匹配上。"""
    assert quantity_token("2027", "年") == ("", 0.0)
    assert "2027" in _texts("计划2027年扩产")
    assert "2026" in _texts("2026年上半年")


def test_strong_vs_weak_quantity_confidence() -> None:
    """带数量级 / 比例 / 多位数的算强信息；1–2 位 + 普通量词的算弱信息。"""
    assert quantity_token("460", "万台")[1] == CONF_QUANTITY_STRONG
    assert quantity_token("30", "%")[1] == CONF_QUANTITY_STRONG
    assert quantity_token("5000", "台")[1] == CONF_QUANTITY_STRONG
    assert quantity_token("18", "个月")[1] == CONF_QUANTITY_WEAK
    assert quantity_token("9", "秒")[1] == CONF_QUANTITY_WEAK


# ---------------------------------------------------------------------------
# 二、垃圾锚点必须消失（这些曾经让闸门看起来「永远不过」）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        # 地名规则把「二级市场」切成「二级市」、「无法区分」切成「市场无法区」
        "将大幅下降，二级市场的估值面临重估。",
        "意向订单时，市场无法区分这到底是概念还是兑现。",
        # 公司规则把整句话当公司名
        "从公开材料看，进军人形机器人这类公司更值得跟踪。",
        "这将对初创公司不利，暗示美国明星机器人公司估值偏高。",
        "超过多数A股机器人上市公司，半导体公司估值需要重估。",
        "大模型公司弃三星，成熟代工厂价值重估。",
        "九号公司这类公司值得看。",
        # 左侧吞掉虚词
        "真正值得跟踪的是基流科技。",
    ],
)
def test_garbage_anchors_are_not_produced(snippet: str) -> None:
    texts = _texts(snippet)
    for garbage in (
        "二级市",
        "市场无法区",
        "进军人形机器人这类公司",
        "暗示美国明星机器人公司",
        "美国明星机器人公司",
        "超过多数A股机器人上市公司",
        "半导体公司",
        "大模型公司",
        "九号公司这类公司",
        "基流科技类",
    ):
        assert garbage not in texts, (snippet, garbage, texts)
    # 也不许出现「比真实名字多吞一个字」的变体
    assert not any(
        text.endswith("科技") and text not in {"基流科技"} and "趋境" in text
        for text in texts
    )


def test_suffix_route_does_not_drop_the_real_name_for_a_longer_variant() -> None:
    """`如果趋境科技…` 会产出 `果趋境`+`科技`；同篇里干净写法必须赢。"""
    body = "从公开材料看，36氪：趋境科技完成数亿元Pre-A轮融资。如果趋境科技或其他算力厂商跟进，节奏会更快。"

    texts = _texts(body)

    assert "趋境科技" in texts
    assert not any(text in {"果趋境科技", "如果趋境科技"} for text in texts)


# ---------------------------------------------------------------------------
# 三、真实锚点必须保留（否则会误杀正确标题）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("公开材料显示，优必选卖出921台全尺寸机器人，后续仍要看交付。", "优必选"),
        # 词典里没有、靠固定后缀 + 清洗捞出来
        ("石头科技发布了新款扫地机器人。", "石头科技"),
        ("舜宇光学科技在半年报中提到，光学模组出货量同比增长30%。", "舜宇光学科技"),
        ("36氪：趋境科技完成数亿元融资，用于AI Token基础设施。", "趋境科技"),
        # 无后缀简称靠词典
        ("比亚迪进军人形机器人，代号尧舜禹，提出460万台目标。", "比亚迪"),
        ("特斯拉和宁德时代都在跟进。", "特斯拉"),
        ("OpenAI 发布新模型，英伟达同步跟进。", "OpenAI"),
    ],
)
def test_real_anchors_are_retained(body: str, expected: str) -> None:
    assert expected in _texts(body)


def test_company_anchor_is_high_confidence() -> None:
    anchors = extract_concrete_anchors("比亚迪提出460万台目标，同比增长30%。")
    company = next(a for a in anchors if a.text == "比亚迪")
    assert company.kind == "company"
    assert company.confidence == CONF_COMPANY
    assert company.is_high_confidence is True


def test_anchor_carries_source_span() -> None:
    body = "公开材料显示，比亚迪提出460万台目标。"
    anchor = next(a for a in extract_concrete_anchors(body) if a.text == "比亚迪")
    start, end = anchor.source_span
    assert body[start:end] == "比亚迪"


# ---------------------------------------------------------------------------
# 四、匹配必须是整 token（不允许截断匹配）
# ---------------------------------------------------------------------------


def test_matching_does_not_truncate_the_anchor() -> None:
    body = "公开材料显示，比亚迪提出460万台目标。"
    anchor = next(a for a in high_confidence_anchors(body) if a.text == "460万台")

    assert anchor.matches("目标460万台，交付才是关键") is True
    # 标题只写 `460万`（丢掉量纲）不算完整承接
    assert anchor.matches("目标460万，交付才是关键") is False


def test_matching_normalizes_width_and_case() -> None:
    body = "同比增长30%，OpenAI 同步跟进。"
    percent = next(a for a in high_confidence_anchors(body) if a.text == "30%")
    openai = next(a for a in high_confidence_anchors(body) if a.text == "OpenAI")

    assert percent.matches("出货增长30%，兑现节奏加快") is True
    assert percent.matches("出货增长30％，兑现节奏加快") is True   # 全角百分号
    assert openai.matches("openai 的新模型，谁最紧张") is True      # 大小写


# ---------------------------------------------------------------------------
# 五、三态：PASS / FAIL / UNAVAILABLE
# ---------------------------------------------------------------------------


def test_status_pass_when_title_carries_a_high_confidence_anchor() -> None:
    body = "公开材料显示，比亚迪提出460万台目标，同比增长30%。"

    status, anchor = concrete_hook_status("比亚迪定下460万台目标，交付才是关键", body)

    assert status == STATUS_PASS
    assert anchor is not None


def test_status_fail_when_body_has_enough_anchors_but_title_ignores_them() -> None:
    body = "公开材料显示，比亚迪提出460万台目标，同比增长30%。"

    status, anchor = concrete_hook_status("人形机器人利润流向，正从整机转零部件", body)

    assert status == STATUS_FAIL
    assert anchor is None


def test_status_unavailable_when_body_yields_too_few_anchors() -> None:
    """正文太薄时**不判标题有罪** —— 这是旧实现「静默放行」的显式化。"""
    thin_body = "从样机到小批量交付通常需要12到18个月，这个爬坡期是判断量产兑现的关键窗口。"

    assert len(high_confidence_anchors(thin_body)) < GATE_MIN_ANCHORS
    status, anchor = concrete_hook_status("产业链利润分配正在重排，谁先兑现", thin_body)

    assert status == STATUS_UNAVAILABLE
    assert anchor is None


def test_summary_reports_the_three_states_and_counts() -> None:
    summary = concrete_anchor_summary("公开材料显示，比亚迪提出460万台目标，同比增长30%。")

    assert summary["anchor_count"] >= 3
    assert summary["high_confidence_anchor_count"] >= 3
    assert summary["status_if_no_match"] == STATUS_FAIL
    assert all({"text", "kind", "confidence", "source_span"} <= set(a) for a in summary["anchors"])
