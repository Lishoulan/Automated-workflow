"""标题选择回归：把 2026-09-16 云端那次真实候选作为基准。

背景（那次的因果链，三处都已被证据钉死）：
1. `title_agent._fallback_fact_titles` 硬编码兜底模板「{主体}{数字}：{细节}如何实测」；
2. 该兜底被 `fact_titles + templates` **塞进候选列表最前面**；
3. `review_service.select_auto_title` 取「**第一个**通过闸门的候选」。
→ 结果：一个已确证的零阅读模板必然胜出，2026-09-16 云端发布的就是它
  （「工信部推动机器人80%：具身智能如何实测」；同句式历史实绩 10–21 阅读）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.review_service import ReviewWorkflowService

# 2026-09-16 云端 run 的 titles.txt 原文（顺序与内容都照抄，含两条兜底模板）
REAL_CANDIDATES_20260916 = [
    "工信部推动机器人80%：具身智能如何实测",
    "工信部推动机器人80%：具身智能项目实测",
    "灵巧手产业：产业链分化，2025年谁将占据利润高地",
    "下降80%的增速里，具身智能先回款的是哪一环",
    "灵巧手产业：月出货4000台，2026年目标5-10万台",
    "灵巧手产业：从概念验证到规模化落地，2025年谁将受益",
    "灵巧手产业：2025年出货量突破万台，谁将实现业绩爆发",
    "灵巧手产业：技术突破，市场增长，2025年规模将翻番",
    "灵巧手产业：2025年市场规模三位数增长，谁将抢占先机",
    "灵巧手产业：2025年，谁将引领灵巧手产业的新一轮增长",
    "灵巧手产业：技术成熟，市场增长，2025年谁是最大赢家",
    "灵巧手产业：2025年出货量第一，交付近2000台，格局已定",
    "灵巧手产业：订单验证，交付效率，盈利兑现，2025年谁将领先",
]

POISON_FALLBACK_TITLES = [
    "工信部推动机器人80%：具身智能如何实测",
    "工信部推动机器人80%：具身智能项目实测",
    "充电站项目实测：虚拟电厂下一步看什么",
    "充电站项目实测：虚拟电厂如何验证",
]


@pytest.mark.parametrize("title", POISON_FALLBACK_TITLES)
def test_confirmed_zero_read_skeletons_are_rejected(title: str) -> None:
    result = ArticleQualityChecker()._check_title(title, "")
    assert any("RULE_007" in str(issue) for issue in result["issues"])


def test_real_20260916_fallback_titles_would_now_be_blocked() -> None:
    checker = ArticleQualityChecker()
    for title in REAL_CANDIDATES_20260916[:2]:
        assert checker._check_title(title, "")["issues"]


def test_click_preference_prefers_short_judgment_over_colon_enumeration() -> None:
    checker = ArticleQualityChecker()
    # 实证：头部高 CTR 是「下判断/设问 + 短」；「冒号 + 数字堆砌 + 未来承诺」是零阅读模式。
    judgment = "下降80%的增速里，具身智能先回款的是哪一环"
    enumeration = "灵巧手产业：订单验证，交付效率，盈利兑现，2025年谁将领先"
    assert checker.title_click_preference(judgment) > checker.title_click_preference(enumeration)


def test_click_preference_picks_a_judgment_title_out_of_the_real_candidates() -> None:
    """排序只负责在「已通过闸门」的候选里挑最优，不该挑到「冒号+枚举」那一族。

    注意：兜底句式是靠**闸门**（RULE_007）淘汰的，不是靠分数 —— 它长度合规
    （20 字），偏好分并不低。别把两层保护混为一谈。
    """
    checker = ArticleQualityChecker()
    model_titles = [
        title for title in REAL_CANDIDATES_20260916 if title not in REAL_CANDIDATES_20260916[:2]
    ]
    passing = [title for title in model_titles if not checker._check_title(title, "")["issues"]]
    best = max(passing, key=checker.title_click_preference)
    assert best == "下降80%的增速里，具身智能先回款的是哪一环"
    # 这一族（11/13 都是同一个骨架）不该赢过它。
    colon_family = [title for title in passing if title.startswith("灵巧手产业：")]
    assert colon_family
    assert all(checker.title_click_preference(best) > checker.title_click_preference(title) for title in colon_family)


def _write_run_dir(run_dir: Path, *, article: str, titles: list[str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "article.md").write_text(article, encoding="utf-8")
    lines = ["全部标题：", *[f"{index}. {title}" for index, title in enumerate(titles, start=1)], ""]
    lines.append("推荐标题：")
    lines.extend(f"{index}. {title}" for index, title in enumerate(titles[:2], start=1))
    (run_dir / "titles.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run_dir / "content_score.json").write_text(
        json.dumps({"scores": {}, "generated_at": "2026-09-16T17:57:00"}, ensure_ascii=False),
        encoding="utf-8",
    )


def _service(app_settings) -> ReviewWorkflowService:
    service = ReviewWorkflowService(app_settings, logging.getLogger("test"))
    # 让去重守卫与发布历史解耦，测试只关心标题闸门与排序。
    service.publish_service._was_article_title_published_recently = lambda *a, **k: False  # type: ignore[method-assign]
    service.publish_service._blocked_by_template_dedup = lambda *a, **k: False  # type: ignore[method-assign]
    return service


def test_select_auto_title_picks_best_passing_candidate_not_the_first(app_settings, tmp_path) -> None:
    article = (
        "2025年灵巧手产业进入交付阶段。工信部提出机器人密度目标80%，"
        "行业出货量接近4000台，交付进度成为关键变量。"
    )
    titles = [
        # 排第一、能过闸门，但属于「冒号+枚举+未来承诺」的零阅读形态
        "灵巧手产业：订单验证，交付效率，盈利兑现，2025年谁将领先",
        # 排在后面，但是实证偏好的「短 + 设问」
        "灵巧手产业先看交付，2025年不是拼概念",
    ]
    run_dir = tmp_path / "run"
    _write_run_dir(run_dir, article=article, titles=titles)

    selection = _service(app_settings).select_auto_title(run_dir)
    assert selection["selected_title"] == titles[1]
    assert selection["selected_title_index"] == 2


def test_select_auto_title_marks_poison_candidates_as_failed(app_settings, tmp_path) -> None:
    article = "工信部提出机器人密度目标80%，具身智能进入交付验证阶段。"
    titles = ["工信部推动机器人80%：具身智能如何实测", "具身智能交付进度才是关键"]
    run_dir = tmp_path / "run"
    _write_run_dir(run_dir, article=article, titles=titles)

    selection = _service(app_settings).select_auto_title(run_dir)
    evaluations = {item["title"]: item for item in selection["evaluations"]}
    poison = evaluations["工信部推动机器人80%：具身智能如何实测"]
    assert poison["passed"] is False
    assert any("RULE_007" in str(issue) for issue in poison["issues"])
    assert selection["selected_title"] == "具身智能交付进度才是关键"
    assert selection["passed_count"] == 1


def test_select_auto_title_still_reports_when_nothing_passes(app_settings, tmp_path) -> None:
    article = "工信部提出机器人密度目标80%。"
    titles = ["工信部推动机器人80%：具身智能如何实测"]
    run_dir = tmp_path / "run"
    _write_run_dir(run_dir, article=article, titles=titles)

    selection = _service(app_settings).select_auto_title(run_dir)
    assert selection["selected_title"] == ""
    assert selection["selected_title_index"] == 0
    assert selection["passed_count"] == 0
    assert "No candidate title passed" in str(selection.get("reason", ""))
