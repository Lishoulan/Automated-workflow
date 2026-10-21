"""相对档不得把「高展现零点击」的作品当成正面范本。

背景：`select_examples()` 的相对档原先只按展现量排序：

    ranked = sorted(records, key=lambda row: (_impressions(row), _reads(row)))

生产数据实测（2026-10-21 artifact，30 条作品）会因此选出：

    1. 展现 7144 阅读 92 点击率 1.29%   ✅ 可接受
    2. 展现 3724 阅读 13 点击率 0.35%   ⚠️ 平台给了流量但几乎没人点
    6. 展现 3014 阅读  2 点击率 0.07%   ❌ 典型「有曝光无吸引力」
    8. 展现 2710 阅读  1 点击率 0.04%   ❌

把这些当「相对高表现作品」注入标题/正文生成提示词，等于让模型模仿
"平台愿意推但读者不看" 的写法 —— 这正是 2026-09-04 自污染事故的同一类风险
（当时是 0 阅读标题被当成高点击率范本）。

本文件锁住：相对档必须在「有展现」的前提下，优先考虑点击率/阅读量，
而不是纯展现量。
"""
from __future__ import annotations

import pytest

from toutiao_content_factory.services import performance_hints


def _rec(title: str, impressions: int, reads: int) -> dict:
    return {"title": title, "impression_count": impressions, "read_count": reads}


def test_relative_tier_prefers_engagement_over_raw_impressions() -> None:
    """两个候选：高展现零点击 vs 中展现有点击，应选后者。

    注意两个候选的阅读量都低于绝对档 100，否则会走 absolute 分支。
    填充项的点击率刻意设得比目标项低，避免把测试变成纯排序对照。
    """
    high_impression_zero_click = _rec("高展现零点击", impressions=100000, reads=1)
    moderate_impression_with_clicks = _rec("中展现有点击", impressions=8000, reads=80)  # CTR 1%
    filler = [_rec(f"填充{i}", 900, 5) for i in range(5)]  # CTR ≈ 0.56%

    examples, mode = performance_hints.select_examples(
        [high_impression_zero_click, moderate_impression_with_clicks, *filler]
    )

    assert mode == "relative"
    titles = [row["title"] for row in examples]
    assert "中展现有点击" in titles, f"应优先选有点击的作品，实际: {titles}"
    assert "高展现零点击" not in titles, f"高展现零点击不得入选，实际: {titles}"


def test_relative_tier_rejects_samples_with_near_zero_click_through() -> None:
    """点击率低于地板值的作品不能当范本，哪怕展现量最高。"""
    junk = _rec("展现很高但没人点", impressions=50000, reads=2)  # CTR = 0.004%
    good = _rec("正常作品", impressions=3000, reads=60)          # CTR = 2%
    filler = [_rec(f"填充{i}", 800, 12) for i in range(5)]

    examples, mode = performance_hints.select_examples([junk, good, *filler])

    assert mode == "relative"
    titles = [row["title"] for row in examples]
    assert "展现很高但没人点" not in titles, (
        f"接近零点击的作品不得作为范本，实际选中: {titles}"
    )


def test_relative_tier_still_works_when_no_record_has_reads() -> None:
    """全部零阅读时不能直接判 insufficient —— 仍可回退到展现排序。

    这是冷启动场景：账号刚起步，所有作品都没阅读。此时「有展现」本身就是
    唯一可用信号，但注入文案必须保持「样本不足」的警示。
    """
    records = [_rec(f"零阅读{i}", impressions=1000 + i * 100, reads=0) for i in range(6)]

    examples, mode = performance_hints.select_examples(records)

    assert mode == "relative"
    assert examples, "全零阅读时仍应给出展现最高的样本，而不是完全不注入"


def test_absolute_tier_is_unchanged() -> None:
    """绝对档仍然只看阅读量，不受本次调整影响。"""
    records = [
        _rec("高阅读", impressions=5000, reads=150),
        _rec("更高阅读", impressions=8000, reads=300),
    ]
    examples, mode = performance_hints.select_examples(records)

    assert mode == "absolute"
    assert [row["title"] for row in examples][0] == "更高阅读"


def test_relative_floor_constant_is_exposed() -> None:
    """点击率地板值必须是可读常量，方便审计与调整。"""
    assert hasattr(performance_hints, "MIN_CTR_FOR_RELATIVE")
    assert 0 < performance_hints.MIN_CTR_FOR_RELATIVE < 1


@pytest.mark.parametrize("impressions,reads,expected_ctr", [
    (1000, 20, 0.02),
    (0, 0, 0.0),
    (1000, 0, 0.0),
])
def test_ctr_helper_matches_impression_and_read_semantics(
    impressions: int, reads: int, expected_ctr: float
) -> None:
    record = _rec("x", impressions=impressions, reads=reads)
    assert performance_hints._ctr(record) == pytest.approx(expected_ctr, abs=1e-9)

def test_relative_tier_rejects_tiny_sample_high_ctr() -> None:
    """展现量过低时点击率不可信，不得因为比率高就入选。

    生产实测出现过「展现 17 / 阅读 1 = 5.88%」，若只按点击率排序，
    会挤掉「展现 7144 / 阅读 92」这类真正有量的作品。
    """
    tiny_high_ctr = _rec("展现17阅读1", impressions=17, reads=1)      # 5.88%
    real_volume = _rec("展现7144阅读92", impressions=7144, reads=92)  # 1.29%
    filler = [_rec(f"填充{i}", 900, 9) for i in range(5)]             # CTR 1%

    examples, mode = performance_hints.select_examples([tiny_high_ctr, real_volume, *filler])

    assert mode == "relative"
    titles = [row["title"] for row in examples]
    assert "展现17阅读1" not in titles, (
        f"样本量过低的记录不得入选，实际: {titles}"
    )


def test_relative_volume_floor_constant_is_exposed() -> None:
    assert hasattr(performance_hints, "MIN_IMPRESSIONS_FOR_RELATIVE")
    assert performance_hints.MIN_IMPRESSIONS_FOR_RELATIVE > 0
