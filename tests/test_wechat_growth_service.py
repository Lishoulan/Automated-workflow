from datetime import datetime, timedelta
from pathlib import Path

import pytest

from toutiao_content_factory.services.generation_cost_service import BEIJING
from toutiao_content_factory.services.wechat_growth_service import article_url, merge_observation, build_growth_report, render_growth_report

START = datetime(2026, 10, 9, 10, tzinfo=BEIJING)
URL = "https://mp.weixin.qq.com/s/test-article"


def observation(hours=0, readers="0", followers=69, **changes):
    row = {"account": "小椰科技", "followers": followers, "observed_at": (START + timedelta(hours=hours)).isoformat(),
           "articles": [{"url": URL, "title": "真实标题", "published_at": START.isoformat(), "readers": readers,
                         "shares": "0", "likes": None, "recommends": "开启留言"}]}
    row.update(changes)
    return row


def test_missing_is_not_zero_or_fake_follower_attribution():
    state = merge_observation({}, observation(), account="小椰科技")
    state = merge_observation(state, observation(6, "1,200", 71), account="小椰科技")
    report = build_growth_report(state)
    article = report["articles"][0]
    assert article["latest"]["readers"] == 1200 and article["latest"]["shares"] == 0
    assert article["latest"]["likes"] is None and article["latest"]["recommends"] is None
    assert article["attributed_followers"] is None and article["read_rate"] is None
    assert report["account_net_followers_since_tracking"] == 2 and report["followers_to_100"] == 29
    assert report["ad_activation"] == "unverified"


def test_late_observations_are_display_only_and_windows_do_not_mix():
    state = merge_observation({}, observation(40, "100"), account="小椰科技")
    report = build_growth_report(state)
    assert report["articles"][0]["windows"] == {"24": None, "72": None}
    state = merge_observation(state, observation(75, "200"), account="小椰科技")
    state = merge_observation(state, observation(25, "50"), account="小椰科技")
    report = build_growth_report(state)
    row = report["articles"][0]
    assert row["windows"]["24"]["readers"] == 50 and row["windows"]["72"]["readers"] == 200
    assert row["latest"]["readers"] == 200 and report["observed_at"] == observation(75)["observed_at"]


def test_account_mismatch_and_invalid_prior_state_cannot_reset_history():
    with pytest.raises(ValueError):
        merge_observation({}, observation(account="其他账号"), account="小椰科技")
    with pytest.raises(ValueError):
        merge_observation({"schema": 2}, observation(), account="小椰科技")
    with pytest.raises(ValueError):
        merge_observation({}, observation(followers=None), account="小椰科技")


def test_unknown_date_different_title_duplicate_and_private_urls():
    first = observation()
    first["articles"][0]["published_at"] = "07月25日"
    state = merge_observation({}, first, account="小椰科技")
    changed = observation(24)
    changed["articles"][0]["title"] = "其他标题"
    state = merge_observation(state, changed, account="小椰科技")
    state = merge_observation(state, first, account="小椰科技")
    assert len(state["articles"][URL]["snapshots"]) == 1
    assert build_growth_report(state)["articles"][0]["windows"]["24"] is None
    assert article_url("https://mp.weixin.qq.com/cgi-bin/home?token=secret") == ""
    assert article_url("https://mp.weixin.qq.com.evil/s/x") == ""
    assert article_url(URL + "?token=secret#fragment") == URL


def test_browser_utc_date_is_verified_and_used_for_windows():
    raw = observation(24, "100")
    raw["articles"][0]["published_at"] = "2026-10-09T02:00:00.000Z"
    report = build_growth_report(merge_observation({}, raw, account="小椰科技"))
    assert report["articles"][0]["published_at"] == START.isoformat()
    assert report["articles"][0]["windows"]["24"]["readers"] == 100


def test_retention_and_old_observations_cannot_restore_stale_snapshots():
    state = merge_observation({}, observation(), account="小椰科技")
    later = observation(91 * 24, followers=70, articles=[])
    state = merge_observation(state, later, account="小椰科技")
    assert state["articles"][URL]["snapshots"] == []
    assert len(state["account_snapshots"]) == 1
    assert merge_observation(state, observation(), account="小椰科技") == state


def test_schedule_and_collector_never_invoke_content_or_publication():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/wechat-content-metrics.yml").read_text(encoding="utf-8")
    assert "37 */6 * * *" in workflow and "group: wechat-default" in workflow
    assert "OPENAI_API_KEY" not in workflow and "IMAGE_API_KEY" not in workflow
    script = (root / "scripts/collect_wechat_growth.py").read_text(encoding="utf-8")
    assert "path=growth.STATE_PATH, strict=True" in script and "if not args.read_only:" in script
    assert "configure_environment" not in script and "load_settings" not in script
    assert "单篇涨粉" in render_growth_report(build_growth_report(merge_observation({}, observation(), account="小椰科技")))
