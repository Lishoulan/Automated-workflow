"""注入样本的体裁匹配：正文必须同体裁，标题可跨体裁借鉴。

设计依据（2026-10-21 生产数据 + 账号配置）：

- `robot_notes` 账号的长文赛道（`social_story` / `consumer_practical`）与
  微头条人设（`story_comment`）**是同一条社会故事线**。微头条标题如
  「业主告物业要停车费账本，法院这么判」的叙事钩子，对长文标题有参考价值
  —— 所以 `title_hints()` **不**强制体裁过滤，否则长文标题会永久失去样本
 （长文 30 天 0 篇，永远没有长文样本可用）。

- 但微头条**没有 1750 字长文的正文结构**。把微头条当正文范本会把长文往
  "短平快"拉，正是 `article_too_short` 的一类风险来源 ——
  所以 `writer_hints()` **必须**按体裁过滤。

本文件锁住这两条不同规则，并确保：
1. 体裁过滤本身可用（别名、大小写、跨源）；
2. 无同体裁样本时返回空串而不是注入错误体裁；
3. 标题路径保持可用，不会因过滤而永久空转。
"""
from __future__ import annotations

import json
from pathlib import Path

from toutiao_content_factory.services import performance_hints


class _Settings:
    def __init__(self, root: Path) -> None:
        self.output_dir = root
        self.automation_dir = root / "automation"
        self.project_root = root.parent


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _metrics(records: list[dict]) -> dict:
    return {"records": records}


def _checkpoints(works: dict) -> dict:
    return {"works": works}


def _work(title: str, impressions: int, reads: int, kind: str) -> dict:
    return {
        "title": title,
        "type": kind,
        "created_at": "2026-10-09T19:28:17+08:00",
        "latest": {"impressions": impressions, "reads": reads, "comments": 0},
    }


def _mixed_fixture(root: Path) -> None:
    _write(root / "growth_checkpoints.json", _checkpoints({
        f"wtt{i}": _work(f"微头条{i}", 5000 + i * 100, 50, "weitoutiao")
        for i in range(5)
    }))
    _write(root / "performance_metrics.json", _metrics([
        {"title": f"长文{i}", "platform": "toutiao",
         "impression_count": 900 + i * 10, "read_count": 20, "content_type": "article"}
        for i in range(5)
    ]))


def test_load_records_can_filter_by_content_type(tmp_path: Path) -> None:
    """按体裁过滤后只返回该体裁的记录。"""
    settings = _Settings(tmp_path)
    _mixed_fixture(tmp_path)

    articles = performance_hints.load_records(settings, content_type="article")
    assert articles, "应能筛出长文样本"
    assert all("长文" in row["title"] for row in articles), articles


def test_checkpoint_fallback_respects_content_type_when_primary_is_thin(
    tmp_path: Path,
) -> None:
    """primary 稀疏触发回退时，体裁过滤仍必须生效。"""
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", _metrics([
        {"title": "唯一长文", "platform": "toutiao",
         "impression_count": 900, "read_count": 20},
    ]))
    _write(tmp_path / "growth_checkpoints.json", _checkpoints({
        f"wtt{i}": _work(f"微头条{i}", 5000 + i * 100, 50, "weitoutiao")
        for i in range(6)
    }))

    microblogs = performance_hints.load_records(settings, content_type="weitoutiao")
    assert len(microblogs) == 6, f"回退后应能筛出 6 条微头条，实际 {len(microblogs)}"
    assert all("微头条" in row["title"] for row in microblogs)


def test_writer_hints_only_use_article_samples(tmp_path: Path) -> None:
    """长文正文注入不得使用微头条样本。"""
    settings = _Settings(tmp_path)
    _mixed_fixture(tmp_path)

    hints = performance_hints.writer_hints(settings, content_type="article")

    assert "微头条" not in hints, f"长文正文注入混入了微头条样本: {hints}"


def test_writer_hints_yields_empty_when_only_microblogs_exist(tmp_path: Path) -> None:
    """只有微头条、但请求长文正文样本时：宁可返回空，也不要注入错体裁。"""
    settings = _Settings(tmp_path)
    _write(tmp_path / "growth_checkpoints.json", _checkpoints({
        f"wtt{i}": _work(f"微头条{i}", 5000, 50, "weitoutiao") for i in range(6)
    }))

    hints = performance_hints.writer_hints(settings, content_type="article")

    assert hints == "", f"无同体裁正文样本时必须返回空串，实际: {hints}"


def test_title_hints_can_borrow_titles_across_genres(tmp_path: Path) -> None:
    """标题路径**不**强制体裁过滤：同赛道微头条标题对长文标题有参考价值。

    生产现状：长文 30 天 0 篇，永远没有长文样本。若标题也强制过滤，
    长文标题将永久失去任何参考 —— 那不是安全，是让闭环重新空转。
    """
    settings = _Settings(tmp_path)
    _write(tmp_path / "growth_checkpoints.json", _checkpoints({
        f"wtt{i}": _work(f"微头条{i}", 5000, 50, "weitoutiao") for i in range(6)
    }))

    # 不传 content_type：应能拿到微头条标题样本
    hints = performance_hints.title_hints(settings)
    assert hints, "标题路径应能跨体裁借鉴，不应为空"
    assert "微头条" in hints


def test_content_type_matching_is_case_insensitive_and_accepts_aliases(
    tmp_path: Path,
) -> None:
    """体裁别名要能互认（article/toutiao/图文），否则生产会静默空转。"""
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", _metrics([
        {"title": f"长文{i}", "platform": "toutiao",
         "impression_count": 900, "read_count": 20}
        for i in range(5)
    ]))

    hints = performance_hints.writer_hints(settings, content_type="article")

    assert hints, "platform=toutiao 的记录应被识别为 article 类长文样本"


def test_unknown_content_type_is_not_silently_dropped(tmp_path: Path) -> None:
    """体裁未知时保守放行，避免因为一个缺字段把闭环清空。"""
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", _metrics([
        {"title": f"未知体裁{i}", "impression_count": 900, "read_count": 20}
        for i in range(5)
    ]))

    hints = performance_hints.writer_hints(settings, content_type="article")

    assert hints, "体裁未知的记录应保守放行，而不是被全部过滤掉"

