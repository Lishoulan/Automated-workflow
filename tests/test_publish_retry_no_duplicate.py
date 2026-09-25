"""发布重试的幂等性：发布按钮点过之后，绝不再点第二次。

背景（2026-09-25 线上实测）
--------------------------
平台上出现 4 组「同一标题 3 条」，时间间隔 106 / 172 秒。

根因在 `_publish_run_locked` 的重试循环：

    max_publish_retries = 2
    for attempt in range(1 + max_publish_retries):   # ← 正好 3 次

`_publish_to_toutiao` 点完「发布」按钮后，会去等提交响应
（`_wait_for_publish_submit`）。**如果只是没捕获到响应**（超时/事件丢失），
响应校验失败就抛异常 —— 但文章其实已经提交到服务端了。
外层于是重试，**再点一次发布按钮**，把同一篇文章又提交一遍。

原有的防重检查读的是「发布历史文件」，而发布历史是整个流程结束才写回的，
重试发生时里面还没有刚提交的那条记录 —— 所以拦不住。

修复：新增「发布按钮已点过」标记，一旦置位就禁止重试。
本文件把这些约束固化成断言，防止以后被改回可重试的形状。

详见 `docs/重复发布根因_20260925.md`
"""
from __future__ import annotations

import inspect

from toutiao_content_factory.services.publish_service import PublishService


def _retry_loop_source() -> str:
    return inspect.getsource(PublishService._publish_run_locked)


def test_button_flag_is_reset_before_retry_loop() -> None:
    """重试循环开始前必须复位标记，否则上一篇的状态会污染下一篇。"""
    src = _retry_loop_source()
    assert "self._publish_button_clicked_this_article = False" in src, (
        "每次发布开始前必须复位「发布按钮已点」标记，"
        "否则上一次发布的置位会让下一次发布跳过重试能力"
    )


def test_retry_refuses_when_button_already_clicked() -> None:
    """重试分支必须存在「按钮已点 → 拒绝重试」的守卫。"""
    src = _retry_loop_source()
    assert "if self._publish_button_clicked_this_article:" in src, (
        "重试前必须检查发布按钮是否已点过 —— 这是防止重复发布的最后一道闸"
    )
    assert "Duplicate publish prevented on retry" in src, (
        "拒绝重试时必须抛出明确错误，让人能从日志看出这是刻意的保护而不是故障"
    )


def test_button_guard_runs_before_history_guard() -> None:
    """按钮守卫必须排在历史文件检查之前。

    历史文件是流程结束才写的，重试时读不到刚提交的记录；只靠它拦不住重复。
    """
    src = _retry_loop_source()
    button_guard = src.index("if self._publish_button_clicked_this_article:")
    # 注意：`_was_article_title_published_recently` 在发布前还有一处调用（发布前去重），
    # 这里必须锚定**重试分支内**的那一处，用它的日志文案定位，避免锚到前面那处。
    history_guard = src.index("Retry aborted: article title was already published")
    assert button_guard < history_guard, (
        "发布按钮守卫必须排在重试分支的发布历史检查之前，否则重试仍会走到点按钮那一步"
    )


def test_flag_is_set_right_after_click_and_before_waiting_response() -> None:
    """置位必须紧跟在点击成功之后、等待提交响应之前。

    「等待提交响应」正是会抛异常的地方；置位若排到它后面就来不及了。
    """
    src = inspect.getsource(PublishService._publish_to_toutiao)
    flag = src.index("self._publish_button_clicked_this_article = True")
    click = src.index("_click_immediate_publish_button")
    wait = src.index("_wait_for_publish_submit", flag)
    assert click < flag < wait, (
        "置位必须位于「点击发布按钮」之后、「等待提交响应」之前；"
        "排在等待之后会导致超时抛出时标记仍是 False，重试照旧发生"
    )
