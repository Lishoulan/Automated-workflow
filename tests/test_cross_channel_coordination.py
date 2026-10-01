"""跨通道「当日只发一篇」与账号级收益/权益采集的回归测试。

覆盖 2026-09-16 修的三件事：
1. 账号级收益金额采集（首页看板）→ 报告里不再是「待回流」；
2. 创作权益接口给出的分成资格（`status_code == 3` 且与 `activated_benefit_num` 对齐）；
3. 本地通道与云端通道的当日互斥（共享状态 + 本地兜底窗口）。
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace  # noqa: F401

import pytest

from toutiao_content_factory.services import creator_account_service as account_service
from toutiao_content_factory.services import cross_channel_state as xstate
from toutiao_content_factory.services.app_service import ContentFactoryAppService


# ---------------------------------------------------------------------------
# 真实返回的最小复刻（取自 2026-09-16 实测）
# ---------------------------------------------------------------------------

BENEFIT_PAYLOAD = {
    "benefit_page": {
        "status": 200,
        "body": {
            "code": 0,
            "data": {
                "activated_benefit_num": 7,
                "fans_num": 16,
                "is_low_quality": False,
                "credit_info": {"score": 95, "score_change": 0},
                "benefit_level_info_list": [
                    {
                        "describe": "基础权益",
                        "level": 100,
                        "benefit_list": [
                            {"name": "微头条创作收益", "status_code": 3, "type": 5, "describe": "发布微头条可获得创作收益"},
                            {"name": "文章创作收益", "status_code": 3, "type": 1, "describe": "发布文章并选择投放广告"},
                            {"name": "视频创作收益", "status_code": 3, "type": 26},
                            {"name": "小视频创作收益", "status_code": 3, "type": 27},
                            {"name": "热点图库", "status_code": 3, "type": 15},
                        ],
                    },
                    {
                        "describe": "千粉权益",
                        "level": 200,
                        "benefit_list": [
                            {"name": "图文赞赏", "status_code": 3, "type": 3},
                            {"name": "视频赞赏", "status_code": 3, "type": 16},
                        ],
                    },
                    {
                        "describe": "万粉权益",
                        "level": 300,
                        "benefit_list": [{"name": "头条抽奖", "status_code": 0, "type": 8}],
                    },
                ],
            },
        },
    },
    "user_info": {
        "status": 200,
        "body": {
            "code": 0,
            "name": "手蓝数据笔记",
            "media_id": 1710793348651021,
            "is_creator": False,
            "auth_type": -1,
            "extra": {"claim_origin_permission": "1"},
            "welcome_msg": "在头条创作的第 1829 天",
        },
    },
}

BOARD = {
    "粉丝数": ["16", "昨日无变化"],
    "总阅读(播放)量": ["14,960", "昨日", "51"],
    "累计收益": ["36.07元", "昨日无变化"],
}


# ---------------------------------------------------------------------------
# 1. 账号级收益采集
# ---------------------------------------------------------------------------


def test_parse_amount_reads_cny_text() -> None:
    assert account_service.parse_amount("36.07元") == 36.07
    assert account_service.parse_amount("1,234.5 元") == 1234.5
    assert account_service.parse_amount("暂无") is None


def test_parse_yesterday_delta_distinguishes_zero_from_unknown() -> None:
    # 「无变化」是明确的 0；读不出来必须是 None，不能当 0。
    assert account_service.parse_yesterday_delta("昨日无变化") == 0.0
    assert account_service.parse_yesterday_delta("昨日 +1.23") == 1.23
    assert account_service.parse_yesterday_delta("") is None
    assert account_service.parse_yesterday_delta("昨日") is None


def test_build_account_snapshot_pulls_revenue_from_board(app_settings) -> None:
    snapshot = account_service.build_account_snapshot(
        app_settings, board=BOARD, api_payload=BENEFIT_PAYLOAD
    )
    assert snapshot["revenue"]["total"] == 36.07
    assert snapshot["revenue"]["yesterday_delta"] == 0.0
    assert snapshot["account"]["fans"] == 16
    assert snapshot["account"]["total_reads"] == 14960
    assert snapshot["account"]["name"] == "手蓝数据笔记"
    assert snapshot["quality"]["credit_score"] == 95
    assert snapshot["quality"]["is_low_quality"] is False


def test_account_snapshot_keeps_revenue_unknown_when_board_missing(app_settings) -> None:
    snapshot = account_service.build_account_snapshot(app_settings, board={}, api_payload={})
    # 采不到金额时必须是 None（未知），不能退化成 0。
    assert snapshot["revenue"]["total"] is None
    assert snapshot["revenue"]["yesterday_delta"] is None


def test_save_account_snapshot_computes_delta_against_earlier_day(app_settings, tmp_path) -> None:
    logger = logging.getLogger("test")
    day_one = datetime(2026, 9, 15, 21, 0, 0)
    day_two = datetime(2026, 9, 16, 21, 0, 0)

    first = account_service.build_account_snapshot(
        app_settings, board={**BOARD, "累计收益": ["36.05元"]}, api_payload=BENEFIT_PAYLOAD, observed_at=day_one
    )
    account_service.save_account_snapshot(app_settings, logger, first)
    second = account_service.build_account_snapshot(
        app_settings, board=BOARD, api_payload=BENEFIT_PAYLOAD, observed_at=day_two
    )
    path = account_service.save_account_snapshot(app_settings, logger, second)

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["revenue_total"] == 36.07
    assert payload["revenue_delta_vs_last_observation"] == pytest.approx(0.02)
    assert payload["previous_observation_date"] == "2026-09-15"
    assert [row["observed_date"] for row in payload["history"]] == ["2026-09-15", "2026-09-16"]


def test_same_day_recollection_does_not_fake_a_delta(app_settings) -> None:
    logger = logging.getLogger("test")
    observed = datetime(2026, 9, 16, 9, 0, 0)
    snapshot = account_service.build_account_snapshot(
        app_settings, board=BOARD, api_payload=BENEFIT_PAYLOAD, observed_at=observed
    )
    account_service.save_account_snapshot(app_settings, logger, snapshot)
    path = account_service.save_account_snapshot(app_settings, logger, snapshot)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    # 同一天重复采集不能和自己比出 0，只能是 None（与上次更早的观测比）。
    assert payload["revenue_delta_vs_last_observation"] is None
    assert len(payload["history"]) == 1


# ---------------------------------------------------------------------------
# 2. 创作权益（分成资格）判定
# ---------------------------------------------------------------------------


def test_summarize_benefits_cross_checks_status_code_semantics() -> None:
    benefits = account_service.summarize_benefits(BENEFIT_PAYLOAD)
    assert benefits["activated_benefit_num_declared"] == 7
    assert benefits["derived_active_count"] == 7
    assert benefits["semantics_ok"] is True
    assert benefits["can_earn_article_revenue"] is True
    assert benefits["revenue_benefits"]["微头条创作收益"] is True


def test_summarize_benefits_flags_semantics_drift() -> None:
    drifted = json.loads(json.dumps(BENEFIT_PAYLOAD))
    drifted["benefit_page"]["body"]["data"]["activated_benefit_num"] = 99
    benefits = account_service.summarize_benefits(drifted)
    assert benefits["semantics_ok"] is False


def test_report_summary_exposes_account_revenue(app_settings) -> None:
    from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

    service = GrowthOpsService(app_settings, logging.getLogger("test"))
    service.ensure_directories()
    snapshot = account_service.build_account_snapshot(
        app_settings, board=BOARD, api_payload=BENEFIT_PAYLOAD
    )
    account_service.save_account_snapshot(app_settings, logging.getLogger("test"), snapshot)

    summary = service._build_account_summary()
    assert summary["revenue_total"] == 36.07
    assert summary["can_earn_article_revenue"] is True
    rendered = "\n".join(service._account_markdown_lines({"account": summary}))
    assert "36.07 CNY" in rendered
    assert "文章创作收益权益：已开通" in rendered


# ---------------------------------------------------------------------------
# 3. 跨通道当日互斥
# ---------------------------------------------------------------------------


def _state_for(day: str, platform: str = "toutiao", status: str = "submitted_for_review") -> dict:
    return xstate.merge_entry(
        None,
        day=day,
        platform=platform,
        record={"channel": "cloud", "status": status, "title": "标题"},
        updated_at="2026-09-16T21:00:00",
    )


def test_published_on_matches_only_same_day_and_success_status() -> None:
    state = _state_for("2026-09-16")
    assert xstate.published_on(state, "2026-09-16") is not None
    assert xstate.published_on(state, "2026-09-15") is None
    assert xstate.published_on(state, "2026-09-16", platform="toutiao_weitoutiao") is None


def test_published_on_ignores_unsuccessful_statuses() -> None:
    for status in ("failed", "draft_saved", ""):
        state = _state_for("2026-09-16", status=status)
        # 未提交的失败或草稿不占用当天名额。
        assert xstate.published_on(state, "2026-09-16") is None
    for status in ("submitted_for_review", "published", "success", "publish_uncertain"):
        assert xstate.published_on(_state_for("2026-09-16", status=status), "2026-09-16") is not None


def test_merge_entry_overwrites_same_day_and_caps_history() -> None:
    state = _state_for("2026-09-16")
    state = xstate.merge_entry(
        state,
        day="2026-09-16",
        platform="toutiao",
        record={"status": "published", "title": "更新的标题"},
        updated_at="2026-09-16T22:00:00",
    )
    assert state["entries"]["2026-09-16"]["toutiao"]["title"] == "更新的标题"
    assert len(state["entries"]) == 1

    for offset in range(200):
        state = xstate.merge_entry(
            state,
            day=(date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
            platform="toutiao",
            record={"status": "published"},
            updated_at="x",
        )
    assert len(state["entries"]) <= 120


def test_read_remote_state_degrades_to_empty(app_settings, monkeypatch) -> None:
    # git 不可用 / 分支不存在时必须是「不知道」，由调用方退回本地判断。
    monkeypatch.setattr(xstate, "_run_git", lambda *a, **k: None)
    assert xstate.read_remote_state(app_settings.project_root) == {}


def test_read_remote_state_uses_a_shallow_fetch(app_settings, monkeypatch) -> None:
    """守卫必须用 `--depth=1` 取状态，不能用全量 fetch。

    ⚠️ 2026-09-21：这个仓库全量 fetch 要 12 秒以上，而调用方是每次 `daily_run`
    开头的**同步**守卫 —— 网络稍慢就超过 `GIT_TIMEOUT_SECONDS`，
    `read_remote_state` 返回 `{}`，`_cloud_published_today()` 拿到 None，
    守卫**静默失效**。实测后果（2026-09-20）：云端 07:00 已发一篇，
    本地 21:35 那次没读到云端状态，又发了一篇 —— 同一天两条图文。

    我们只要一个文件的当前版本，浅取一条提交即可。
    """
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = '{"schema": 1, "entries": {}}'
        stderr = ""

    def _fake_git(_root, args):
        calls.append(list(args))
        return _Result()

    monkeypatch.setattr(xstate, "_run_git", _fake_git)
    xstate.read_remote_state(app_settings.project_root, refresh=True)

    fetch_calls = [args for args in calls if args and args[0] == "fetch"]
    assert fetch_calls, "read_remote_state 应该发起一次 fetch"
    fetch_args = fetch_calls[0]
    assert "--depth=1" in fetch_args, (
        f"守卫仍在做全量 fetch（{fetch_args}），会因超时而静默失效"
    )


# ---------------------------------------------------------------------------
# 3b. 多账号不得串号（2026-09-18）
# ---------------------------------------------------------------------------


def test_two_accounts_do_not_overwrite_each_other() -> None:
    """同一天、同平台、不同账号必须各占一条，不能互相覆盖。

    修复前 entries 只按日期索引，第二个账号写进去会把第一个账号的记录顶掉；
    反过来，第一个账号「今天发过了」又会让第二个账号的本地兜底整天不发。
    """
    state = xstate.merge_entry(
        None,
        day="2026-09-18",
        platform="toutiao",
        record={"status": "submitted_for_review", "title": "机器人稿"},
        updated_at="t1",
        account="robot-notes",
    )
    state = xstate.merge_entry(
        state,
        day="2026-09-18",
        platform="toutiao",
        record={"status": "submitted_for_review", "title": "养老稿"},
        updated_at="t2",
        account="story-notes",
    )

    robot = xstate.published_on(state, "2026-09-18", account="robot-notes")
    story = xstate.published_on(state, "2026-09-18", account="story-notes")
    assert robot is not None and robot["title"] == "机器人稿"
    assert story is not None and story["title"] == "养老稿"


def test_one_account_publishing_does_not_mark_another_as_published() -> None:
    """这是最要命的那种失败：A 发过 → B 以为自己也发过 → B 整天不发。"""
    state = xstate.merge_entry(
        None,
        day="2026-09-18",
        platform="toutiao",
        record={"status": "submitted_for_review", "title": "机器人稿"},
        updated_at="t1",
        account="robot-notes",
    )

    assert xstate.published_on(state, "2026-09-18", account="story-notes") is None


def test_account_scoped_read_falls_back_to_the_legacy_key() -> None:
    """改造前写入的旧键（无账号）仍然要读得到，避免重复发布。"""
    legacy = xstate.merge_entry(
        None,
        day="2026-09-18",
        platform="toutiao",
        record={"status": "submitted_for_review", "title": "旧记录"},
        updated_at="t1",
    )
    assert "2026-09-18" in legacy["entries"]

    record = xstate.published_on(legacy, "2026-09-18", account="story-notes")
    assert record is not None and record["title"] == "旧记录"


def test_unscoped_read_still_matches_the_legacy_key() -> None:
    """不传 account 的既有调用方语义不变。"""
    state = _state_for("2026-09-18")
    assert xstate.published_on(state, "2026-09-18") is not None


def test_unscoped_read_ignores_account_scoped_entries() -> None:
    """不传 account 时只读旧键，不会被某个账号的专属记录误伤。"""
    state = xstate.merge_entry(
        None,
        day="2026-09-18",
        platform="toutiao",
        record={"status": "submitted_for_review"},
        updated_at="t1",
        account="story-notes",
    )
    assert xstate.published_on(state, "2026-09-18") is None


def _guard_stub(app_settings, extra_settings=None):
    settings = app_settings
    if extra_settings is not None:
        settings = dataclasses.replace(settings, automation=extra_settings)

    # 这两处逻辑只碰 settings / logger，用一个最小替身调用真实方法本体，
    # 避免为了两个判定去构造整个 ContentFactoryAppService。
    class _Stub:
        _cloud_published_today = ContentFactoryAppService._cloud_published_today
        _local_fallback_window_block = ContentFactoryAppService._local_fallback_window_block

        def __init__(self) -> None:
            self.settings = settings
            self.logger = logging.getLogger("test")

    return _Stub()


def test_cloud_guard_reads_shared_state(app_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        xstate,
        "read_remote_state",
        lambda *a, **k: _state_for(date.today().isoformat()),
    )
    stub = _guard_stub(app_settings)
    record = stub._cloud_published_today()
    assert record is not None
    assert record["title"] == "标题"


def test_cloud_guard_returns_none_for_other_days(app_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        xstate,
        "read_remote_state",
        lambda *a, **k: _state_for("2020-01-01"),
    )
    assert _guard_stub(app_settings)._cloud_published_today() is None


def test_daily_runtime_defers_before_the_local_window(app_settings, monkeypatch) -> None:
    from toutiao_content_factory.services import app_service as app_service_module

    automation = dataclasses.replace(app_settings.automation, local_fallback_after_hour=21)
    stub = _guard_stub(app_settings, extra_settings=automation)
    monkeypatch.setattr(
        app_service_module, "notification_now", lambda: datetime(2026, 9, 16, 9, 30, 0)
    )
    result = ContentFactoryAppService._daily_run_impl(stub, dry_run=False)
    assert result["status"] == "deferred_to_cloud"


def test_daily_run_impl_does_not_defer_inside_the_window(app_settings, monkeypatch) -> None:
    from toutiao_content_factory.services import app_service as app_service_module

    automation = dataclasses.replace(app_settings.automation, local_fallback_after_hour=21)
    stub = _guard_stub(app_settings, extra_settings=automation)
    monkeypatch.setattr(
        app_service_module, "notification_now", lambda: datetime(2026, 9, 16, 21, 30, 0)
    )
    # 过了窗口就不该返回 deferred_to_cloud（后续流程在此 stub 上会失败，属预期）。
    assert stub._local_fallback_window_block() is None


def test_local_fallback_gate_disabled_by_default(app_settings) -> None:
    assert app_settings.automation.local_fallback_after_hour == -1
    assert _guard_stub(app_settings)._local_fallback_window_block() is None


# ---------------------------------------------------------------------------
# 微头条也要跨通道互斥（2026-09-16 补：原先只查图文平台）
# ---------------------------------------------------------------------------


def _weitoutiao_stub(app_settings, cloud_record):
    publishing = dataclasses.replace(
        app_settings.publishing,
        weitoutiao_enabled=True,
        weitoutiao_standalone_enabled=True,
        weitoutiao_daily_target=2,
    )
    settings = dataclasses.replace(app_settings, publishing=publishing)
    calls: list[object] = []

    class _Stub:
        _top_up_standalone_weitoutiao = ContentFactoryAppService._top_up_standalone_weitoutiao

        def __init__(self) -> None:
            self.settings = settings
            self.logger = logging.getLogger("test")

        def _cloud_published_today(self, platform=None):
            calls.append(platform)
            return cloud_record

    return _Stub(), calls


def test_weitoutiao_topup_skips_when_cloud_already_published_one(app_settings) -> None:
    """9-16 实况：图文收口了，但微头条两边各补一条（本地 1 + 云端 1）。"""
    stub, calls = _weitoutiao_stub(app_settings, {"title": "PS5模拟器突破！", "status": "submitted_for_review"})

    result = stub._top_up_standalone_weitoutiao(max_count=1)

    assert result["standalone_topup_status"] == "skipped_cloud_already_published"
    assert "PS5模拟器突破！" in result["standalone_topup_summary"]
    # 查的是微头条平台，而不是图文平台。
    assert calls == ["toutiao_weitoutiao"]


def test_weitoutiao_topup_still_runs_when_cloud_has_not_published(app_settings) -> None:
    stub, calls = _weitoutiao_stub(app_settings, None)
    # 云端没发过时不能短路；真实生成器在这里会因为缺依赖失败，所以只断言它确实往下走了。
    try:
        result = stub._top_up_standalone_weitoutiao(max_count=0)
    except Exception as exc:  # pragma: no cover - 依赖真实生成器
        assert calls == ["toutiao_weitoutiao"]
        return
    assert result.get("standalone_topup_status") != "skipped_cloud_already_published"
    assert calls == ["toutiao_weitoutiao"]


def test_cloud_runner_stops_when_remote_state_exists_but_local_cache_is_empty(
    app_settings, monkeypatch
) -> None:
    stub, calls = _weitoutiao_stub(
        app_settings,
        {"title": "Earlier cloud WeiToutiao", "status": "submitted_for_review"},
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    result = stub._top_up_standalone_weitoutiao(max_count=0)

    assert result["standalone_topup_status"] == "skipped_cloud_already_published"
    assert calls == ["toutiao_weitoutiao"]


def test_cloud_runner_can_continue_when_local_history_proves_first_post(
    app_settings, monkeypatch
) -> None:
    stub, calls = _weitoutiao_stub(
        app_settings,
        {
            "title": "Earlier cloud WeiToutiao",
            "status": "submitted_for_review",
            "daily_count": 1,
        },
    )
    stub.settings.output_dir.mkdir(parents=True, exist_ok=True)
    history_path = stub.settings.output_dir / stub.settings.publishing.history_filename
    history_path.write_text(
        json.dumps(
            [
                {
                    "published_at": datetime.now().isoformat(timespec="seconds"),
                    "platform": "toutiao_weitoutiao",
                    "title": "Earlier cloud WeiToutiao",
                    "status": "submitted_for_review",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    result = stub._top_up_standalone_weitoutiao(max_count=0)

    assert result["standalone_topup_status"] == "target_reached"
    assert calls == ["toutiao_weitoutiao"]


def test_cloud_runner_stops_when_shared_daily_count_reaches_target(
    app_settings, monkeypatch
) -> None:
    stub, calls = _weitoutiao_stub(
        app_settings,
        {
            "title": "Second cloud WeiToutiao",
            "status": "submitted_for_review",
            "daily_count": 2,
        },
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    result = stub._top_up_standalone_weitoutiao(max_count=1)

    assert result["standalone_topup_status"] == "target_reached"
    assert "remote_count=2" in result["standalone_topup_summary"]
    assert calls == ["toutiao_weitoutiao"]


def test_cloud_guard_forwards_explicit_platform(app_settings, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        xstate,
        "read_remote_state",
        lambda *a, **k: _state_for(date.today().isoformat(), platform="toutiao_weitoutiao"),
    )
    monkeypatch.setattr(
        xstate,
        "read_cloud_published_today",
        lambda root, platform="toutiao", today=None, refresh=True, account=None: (
            seen.append(platform) or None
        ),
    )
    stub = _guard_stub(app_settings)
    stub._cloud_published_today(platform="toutiao_weitoutiao")
    assert seen == ["toutiao_weitoutiao"]
