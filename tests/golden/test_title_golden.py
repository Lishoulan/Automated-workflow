"""标题 Golden 回归（离线，不调用 LLM、不启动 Playwright）。

目的：给后续 Title Pipeline 改造（multi-batch / archetype / 语义去重 / 塌缩重生成）
钉住**当前已验证的安全行为与形态基线**，避免重构时悄悄改变。

数据集来源（全部是真实历史产物，非人工伪造）：
- ``golden_success_20260917/``   —— 2026-09-17 13:05 成功发布的云端 run
                                    （run 35183027828，3 次 attempt，候选 8/3/3）
- ``golden_title_policy_block/`` —— 2026-09-17 12:30 因标题含「押注」被本地敏感词
                                    闸门拦下的真实 run（run 35182207971）
- ``golden_historical_titles.json`` —— 从 243 个历史 run 中挑选的真实标题，
                                    覆盖 10 种 archetype

⚠️ 关于标题长度：代码里存在**两处不一致**
（``ArticleQualityChecker._check_title`` 用 ``>40`` 才算 issue；
``TOUTIAO_TITLE_MAX_LENGTH`` 默认 30）。本阶段**不裁定哪个正确**，
只用测试把现状钉住，防止后续改动悄悄改变它。
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_titles import (  # noqa: E402
    CLUSTER_THRESHOLD,
    classify_archetype,
    largest_cluster_ratio,
    normalize_title,
    normalized_title_similarity,
    parse_titles_file,
)

GOLDEN_DIR = Path(__file__).resolve().parent
SUCCESS_DIR = GOLDEN_DIR / "golden_success_20260917"
POLICY_DIR = GOLDEN_DIR / "golden_title_policy_block"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# A. 历史正常标题不应被判成完全重复
# ---------------------------------------------------------------------------


def test_historical_titles_are_not_all_duplicates() -> None:
    """A：覆盖 10 种 archetype 的真实历史标题，不应被相似度机制判成高度同构。"""
    payload = json.loads((GOLDEN_DIR / "golden_historical_titles.json").read_text(encoding="utf-8"))
    samples = payload["samples"]
    assert len(samples) >= 3, "历史样本至少 3 条"

    titles = [item["title"] for item in samples]
    # 不同 archetype 的真实标题，归一化后必须互不相同
    assert len({normalize_title(t) for t in titles}) == len(titles), (
        "真实历史标题被判成完全重复，说明归一化过于激进"
    )

    pairwise = [
        normalized_title_similarity(a, b)
        for i, a in enumerate(titles)
        for b in titles[i + 1 :]
    ]
    assert max(pairwise) < 0.72, f"不同 archetype 的真实标题相似度过高: max={max(pairwise)}"
    assert largest_cluster_ratio(titles) < 0.5, "历史正常标题不应聚成一个大簇"


def test_historical_titles_cover_multiple_archetypes() -> None:
    """A2：历史样本确实覆盖多种 archetype（保证上面那条测试有意义）。"""
    payload = json.loads((GOLDEN_DIR / "golden_historical_titles.json").read_text(encoding="utf-8"))
    archetypes = {classify_archetype(item["title"]) for item in payload["samples"]}
    assert len(archetypes) >= 5, f"archetype 覆盖不足: {archetypes}"


# ---------------------------------------------------------------------------
# B. 高度同构标题必须能被识别为高相似
# ---------------------------------------------------------------------------


def test_isomorphic_cluster_is_detected() -> None:
    """B：9-17 成功 run 里肉眼可见的「埃斯顿出货量…机器人市场…」塌缩必须被量化出来。"""
    candidates, _ = parse_titles_file(SUCCESS_DIR / "titles.txt")
    assert len(candidates) == 8, f"golden 候选数应与真实产物一致（8），实际 {len(candidates)}"

    # 该簇两两相似度实测落在 0.54–0.63，故用 0.5 作为可识别下限
    cluster = [t for t in candidates if "埃斯顿" in t]
    assert len(cluster) >= 5, f"应至少有 5 条埃斯顿同构标题，实际 {len(cluster)}"

    pairwise = [
        normalized_title_similarity(a, b)
        for i, a in enumerate(cluster)
        for b in cluster[i + 1 :]
    ]
    # 注意：簇内有一条短变体「埃斯顿登顶，机器人市场分化新局」（11 字），
    # 与其余 5 条的相似度只有 0.36——所以**不能**断言「所有两两 >=0.5」，
    # 只能断言「多数两两 >=0.5」。短变体正是长度差异导致的天然低分。
    strong_pairs = [p for p in pairwise if p >= 0.5]
    assert len(strong_pairs) >= 3, (
        f"同构簇内应有至少 3 对相似度 >=0.5，实际 {len(strong_pairs)}（共 {len(pairwise)} 对）"
    )
    assert max(pairwise) >= 0.6, f"同构簇最大相似度应 >=0.6，实际 {max(pairwise)}"
    assert largest_cluster_ratio(candidates) >= 0.5, (
        f"塌缩检测失效：{CLUSTER_THRESHOLD} 阈值下最大簇应 >=0.5，"
        f"实际 {largest_cluster_ratio(candidates)}"
    )


def test_collapse_metric_names_carry_the_threshold() -> None:
    """B2：指标名必须带阈值，避免以后换阈值后数值不可比（写成黑盒）。"""
    assert CLUSTER_THRESHOLD == 0.50
    # 阈值校准依据：9-17 样本在 0.72 下只有 0.125（低估），0.50 下为 0.625（与肉眼一致）
    candidates, _ = parse_titles_file(SUCCESS_DIR / "titles.txt")
    assert largest_cluster_ratio(candidates, 0.72) < 0.3
    assert largest_cluster_ratio(candidates, 0.50) >= 0.6


# ---------------------------------------------------------------------------
# C. 敏感词「押注」必须仍然被拦截
# ---------------------------------------------------------------------------


def test_bad_word_ya_zhu_is_still_blocked(app_settings, logger) -> None:
    """C：真实敏感词表下，含「押注」的标题必须被 policy gate 拦截。

    用**项目真实的** ``config/bad_words.txt``（不是测试用空词表），
    避免出现「测试通过但生产不拦」的假阴性。
    """
    # 延迟导入：services 包必须先于 agents 初始化（PEP 562 惰性导出，见 d6af0e8）
    from toutiao_content_factory.services.content_guard_service import ContentGuardService

    # AppSettings 是 dataclass，用 replace 换掉 project_root 以指向**真实**敏感词表
    settings = replace(app_settings, project_root=_project_root())
    service = ContentGuardService(settings, logger)

    assert "押注" in service.bad_words, (
        "真实敏感词表里应包含「押注」（config/bad_words.txt 赌博毒品词段）"
    )
    result = service.scan_text("拟斥资23个亿！市值百亿叉车龙头，押注机器人")
    assert result["blocked"] is True
    assert "押注" in result["matched_words"]


def test_policy_block_golden_report_is_preserved() -> None:
    """C2：golden 里保存的真实拦截报告必须与「押注」一致（防止数据集被改坏）。"""
    report = json.loads((POLICY_DIR / "content_guard_report.json").read_text(encoding="utf-8"))
    assert report["blocked"] is True
    assert report["bad_words_blocked"] is True
    assert "押注" in report["matched_bad_words"]
    # 同时钉住「这不是相似度拦截」——历史 bug 就出在这里（报错张冠李戴）
    assert report["matched_title"] == ""
    assert report["similarity"] == 0.0
    assert report["simhash_hamming_distance"] > report["simhash_hamming_threshold"]


# ---------------------------------------------------------------------------
# D. 标题长度：钉住现状，不裁定哪个正确
# ---------------------------------------------------------------------------


def test_title_length_rules_are_pinned_but_not_decided(app_settings) -> None:
    """D：把两处不一致的长度规则现状钉住，不擅自决定哪个正确。

    - ``ArticleQualityChecker._check_title``：``>40`` 才算 issue
    - ``TOUTIAO_TITLE_MAX_LENGTH``（title_agent 用）：默认 30
    """
    from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker

    checker = ArticleQualityChecker()
    over = checker._check_title("机" * 41)
    at_limit = checker._check_title("机" * 40)
    assert any("过长" in issue for issue in over["issues"])
    assert not any("过长" in issue for issue in at_limit["issues"])

    title_max = int(app_settings.publishing.title_max_length)
    assert title_max == 30, f"TOUTIAO_TITLE_MAX_LENGTH 默认应为 30，实际 {title_max}"
    # 不一致本身被记录：checker(40) != settings(30)
    assert title_max != 40


def test_golden_final_title_respects_both_length_rules() -> None:
    """D2：golden 里实际发布的标题必须同时满足 30 与 40 两道口径。"""
    _, recommended = parse_titles_file(SUCCESS_DIR / "titles.txt")
    assert recommended, "golden 应含推荐标题"
    for title in recommended:
        assert len(title) <= 30, f"推荐标题超过 30 字口径: {len(title)} {title}"
        assert len(title) <= 40, f"推荐标题超过 40 字口径: {len(title)} {title}"


# ---------------------------------------------------------------------------
# E. golden 数据集自身的完整性（防止产物被误删/改坏）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "golden_success_20260917/titles.txt",
        "golden_success_20260917/article.md",
        "golden_success_20260917/content_guard_report.json",
        "golden_title_policy_block/content_guard_report.json",
        "golden_historical_titles.json",
    ],
)
def test_golden_files_exist(relative: str) -> None:
    assert (GOLDEN_DIR / relative).exists(), f"golden 文件缺失: {relative}"


def test_success_golden_guard_report_is_not_blocked() -> None:
    """E2：成功 run 的文章级闸门报告必须是「未拦截」，否则 golden 选错了样本。"""
    report = json.loads((SUCCESS_DIR / "content_guard_report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "run"
    assert report["blocked"] is False
