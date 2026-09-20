"""选题层与发布层的去重窗口必须一致。

## 为什么单独有这个测试

去重判据在生产代码里曾经存在**两份、且窗口不同**：

- 选题层 `TopicAutomationService._load_published_keys_for_today`：只看**当天**
- 发布层 `PublishService._was_topic_published`：看**近 7 天**

各写各的、都"看起来合理"，但语义上互相矛盾。实测后果（云端 run 35523301070）：

    07:00 定时运行发布「独居老人摔倒」→ 成功
    次日选题层：当天记录里没有它 → 照样把它排在第一位，模型跑完全套写作与评分
    发布层：7 天记录里有它 → "Duplicate publish blocked"
    → 当天一篇都发不出去，而且是**写完才被拦**，整套模型调用全部白烧

这类"两份判据各自演化"的 bug 在本项目里反复出现（同源判据分布在
`TitleAgent.validate_title_candidate`、`AgentWorkflowService._filter_titles_by_quality`、
`ReviewWorkflowService.select_auto_title` 三处，见 `test_all_three_title_gate_copies_agree`）。
所以这里不只测行为，还**直接测两个模块引用的是同一个常量** —— 让下次改一处忘另一处时，
测试立刻变红。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.automation import (
    TOPIC_DEDUP_WINDOW_DAYS,
    TopicAutomationService,
)
from toutiao_content_factory.services import publish_service as publish_module

ACCOUNT = "robot_notes"


def _write_history(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def _entry(title: str, *, days_ago: int, status: str = "submitted_for_review") -> dict:
    stamp = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {
        "platform": "toutiao",
        "status": status,
        "title": title,
        "topic_title": title,
        "topic_url": f"evergreen://{ACCOUNT}/deadbeef",
        "published_at": stamp,
    }


def test_layers_share_one_window_constant() -> None:
    """发布层必须 import 选题层那个常量，而不是自己再写一个 7。"""
    assert publish_module.TOPIC_DEDUP_WINDOW_DAYS is TOPIC_DEDUP_WINDOW_DAYS


def test_publish_service_cutoff_is_derived_from_the_shared_constant() -> None:
    """发布层不能再出现硬编码的 `timedelta(days=7)`。"""
    source = Path(publish_module.__file__).read_text(encoding="utf-8")
    assert "timedelta(days=7)" not in source, (
        "发布层又出现了硬编码的 7 天窗口，请改用 TOPIC_DEDUP_WINDOW_DAYS"
    )
    assert source.count("TOPIC_DEDUP_WINDOW_DAYS") >= 4


@pytest.fixture()
def automation(tmp_path: Path) -> TopicAutomationService:
    settings = load_settings(Path.cwd(), account_key=ACCOUNT)
    # 把输出目录指到临时目录，避免读到真实 publish_history.json。
    object.__setattr__(settings, "output_dir", tmp_path)
    return TopicAutomationService(settings, logging.getLogger("test_dedup_window"))


def test_topic_published_yesterday_is_filtered_out(
    automation: TopicAutomationService, tmp_path: Path
) -> None:
    """**核心回归**：昨天发过的选题，今天不许再进候选。

    这正是 run 35523301070 翻车的那一步 —— 旧实现（只看当天）会把它放行。
    """
    history = tmp_path / automation.settings.publishing.history_filename
    _write_history(history, [_entry("独居老人家中摔倒两天无人知，邻居报警破门", days_ago=1)])

    keys = automation._load_published_keys_for_today()

    assert automation._normalize_text("独居老人家中摔倒两天无人知，邻居报警破门") in keys["titles"]


def test_topic_published_beyond_the_window_is_allowed_again(
    automation: TopicAutomationService, tmp_path: Path
) -> None:
    """窗口边界：第 8 天之前发过的题应重新可用（否则池子会被永久吃掉）。"""
    history = tmp_path / automation.settings.publishing.history_filename
    _write_history(
        history,
        [_entry("很久以前发过的题", days_ago=TOPIC_DEDUP_WINDOW_DAYS)],
    )

    keys = automation._load_published_keys_for_today()

    assert automation._normalize_text("很久以前发过的题") not in keys["titles"]


def test_topic_published_on_the_last_day_of_the_window_is_still_blocked(
    automation: TopicAutomationService, tmp_path: Path
) -> None:
    """窗口内最后一天（第 7 天）仍须拦截 —— 边界取 `>=` 而不是 `>`。"""
    history = tmp_path / automation.settings.publishing.history_filename
    _write_history(
        history,
        [_entry("窗口最后一天的题", days_ago=TOPIC_DEDUP_WINDOW_DAYS - 1)],
    )

    keys = automation._load_published_keys_for_today()

    assert automation._normalize_text("窗口最后一天的题") in keys["titles"]


def test_entries_without_timestamp_are_ignored(
    automation: TopicAutomationService, tmp_path: Path
) -> None:
    """历史记录里有缺 `published_at` 的旧条目，不能因此炸掉或误拦。"""
    history = tmp_path / automation.settings.publishing.history_filename
    _write_history(
        history,
        [
            {"platform": "toutiao", "status": "submitted_for_review", "topic_title": "没有时间的旧记录"},
            _entry("有时间的近期记录", days_ago=2),
        ],
    )

    keys = automation._load_published_keys_for_today()

    assert automation._normalize_text("有时间的近期记录") in keys["titles"]
    assert automation._normalize_text("没有时间的旧记录") not in keys["titles"]


def test_generated_runs_across_the_window_are_filtered(
    automation: TopicAutomationService, tmp_path: Path
) -> None:
    """近 7 天内**生成过**稿件的选题也不许重选（含没发出去的那些）。

    没发出去的选题如果明天还能再选一次，就会出现"同一选题反复写、反复发不出去"的空转。
    """
    archive = tmp_path / automation.settings.automation.archive_dirname
    for days_ago, title in ((1, "昨天生成过的题"), (3, "三天前生成过的题")):
        stamp = datetime.now() - timedelta(days=days_ago)
        run_dir = archive / f"{stamp.strftime('%Y%m%d')}-120000_测试"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"{automation.settings.automation.selected_topic_basename}.json").write_text(
            json.dumps({"selected_topic": {"title": title, "url": ""}}, ensure_ascii=False),
            encoding="utf-8",
        )

    # 窗口外的运行目录（第 8 天）不应计入
    old_stamp = datetime.now() - timedelta(days=TOPIC_DEDUP_WINDOW_DAYS)
    old_dir = archive / f"{old_stamp.strftime('%Y%m%d')}-120000_测试"
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / f"{automation.settings.automation.selected_topic_basename}.json").write_text(
        json.dumps({"selected_topic": {"title": "窗口外生成过的题", "url": ""}}, ensure_ascii=False),
        encoding="utf-8",
    )

    keys = automation._load_generated_keys_for_today()

    assert automation._normalize_text("昨天生成过的题") in keys["titles"]
    assert automation._normalize_text("三天前生成过的题") in keys["titles"]
    assert automation._normalize_text("窗口外生成过的题") not in keys["titles"]


def test_run_name_prefixes_cover_the_whole_window(automation: TopicAutomationService) -> None:
    prefixes = automation._recent_run_name_prefixes(TOPIC_DEDUP_WINDOW_DAYS)
    assert len(prefixes) == TOPIC_DEDUP_WINDOW_DAYS
    assert prefixes[0] == datetime.now().strftime("%Y%m%d")
    assert len(set(prefixes)) == len(prefixes)
