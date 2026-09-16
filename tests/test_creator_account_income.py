"""收益接口解析回归。

2026-09-17 才发现「收益数据」页背后有真接口（`/profile_v4/analysis/income-overview`
调用 `/mp/agw/income/...`），比首页看板上的累计值精确得多：逐日、分作品类型、
可提现、本月、区间合计都有。下面用的就是当天的**真实返回**。
"""
from __future__ import annotations

import json
from datetime import date

from toutiao_content_factory.services import creator_account_service as cas

# 真实返回（2026-09-17 实测，已裁到必要字段）
ABSTRACT_PAYLOAD = {
    "income_abstract": {
        "status": 200,
        "body": {
            "code": 0,
            "data": [
                {
                    "is_yesterday_income_ready": False,
                    "lastday": 0,
                    "period": 0.16,
                    "title": "昨日收益",
                    "type": "period_income",
                },
                {"title": "本月收益", "total": 0.02, "type": "monthly_income"},
                {
                    "settle_info": {
                        "settle_abstract": {"扣税额": 0, "税前收益": 2.05, "税后收益": 2.05},
                        "settle_detail": {
                            "其他收益（含税）": 0.04,
                            "图文收益（含税）": 1.97,
                            "微头条收益（含税）": 0.04,
                        },
                    },
                    "title": "可提现金额",
                    "total": 2.05,
                    "type": "can_withdraw_amount",
                },
                {"title": "累计收益", "total": 36.07, "type": "total_income"},
            ],
        },
    },
    "income_overview": {
        "status": 200,
        "body": {
            "code": 0,
            "data": {
                "columns": [
                    {"key": "day", "title": "日期"},
                    {"key": "total", "title": "总计"},
                    {"key": "tuwen_income", "title": "创作收益"},
                ],
                "detail": [
                    {
                        "columns_detail": {
                            "other_income": {"amount": "0", "composition": []},
                            "tuwen_income": {
                                "amount": "0",
                                "composition": [
                                    {"amount": "0", "name": "文章收益"},
                                    {"amount": "0", "name": "视频收益"},
                                    {"amount": "0", "name": "微头条收益"},
                                ],
                            },
                        },
                        "day": "2026-08-25",
                        "total": "0",
                    },
                    {
                        "columns_detail": {
                            "other_income": {"amount": "0", "composition": []},
                            "tuwen_income": {
                                "amount": "0.08",
                                "composition": [
                                    {"amount": "0.08", "name": "文章收益"},
                                    {"amount": "0", "name": "视频收益"},
                                    {"amount": "0", "name": "微头条收益"},
                                ],
                            },
                        },
                        "day": "2026-08-31",
                        "total": "0.08",
                    },
                    {
                        "columns_detail": {
                            "other_income": {"amount": "0", "composition": []},
                            "tuwen_income": {
                                "amount": "待更新",
                                "composition": [
                                    {"amount": "待更新", "name": "文章收益"},
                                    {"amount": "待更新", "name": "视频收益"},
                                    {"amount": "待更新", "name": "微头条收益"},
                                ],
                            },
                        },
                        "day": "2026-09-16",
                        "total": "0",
                    },
                ],
            },
        },
    },
}


def test_pending_income_is_unknown_not_zero() -> None:
    # 平台把未结算写成「待更新」，这是**未知**；当成 0 会让人误以为那天没赚钱。
    assert cas._income_amount("待更新") is None
    assert cas._income_amount("更新中") is None
    assert cas._income_amount("") is None
    assert cas._income_amount(None) is None
    assert cas._income_amount("0") == 0.0
    assert cas._income_amount("0.08") == 0.08


def test_summarize_income_reads_the_real_abstract() -> None:
    income = cas.summarize_income(ABSTRACT_PAYLOAD)
    assert income["total"] == 36.07
    assert income["can_withdraw"] == 2.05
    assert income["month_income"] == 0.02
    # 同一条 period_income 里：lastday = 昨日，period = 所选区间（30 天）合计
    assert income["yesterday_income"] == 0.0
    assert income["window_income"] == 0.16
    assert income["yesterday_ready"] is False
    assert income["settle_breakdown"] == {
        "其他收益（含税）": 0.04,
        "图文收益（含税）": 1.97,
        "微头条收益（含税）": 0.04,
    }


def test_summarize_income_builds_daily_rows_and_skips_pending_days() -> None:
    income = cas.summarize_income(ABSTRACT_PAYLOAD)
    daily = {row["day"]: row for row in income["daily"]}
    assert daily["2026-08-31"]["total"] == 0.08
    assert daily["2026-08-31"]["article"] == 0.08
    assert daily["2026-08-31"]["weitoutiao"] == 0.0
    # 未结算那天三项都必须是 None，而不是 0
    assert daily["2026-09-16"]["total"] is None
    assert daily["2026-09-16"]["article"] is None
    # 只有真有钱进账的天数才计数
    assert income["daily_days_with_income"] == 1


def test_summarize_income_is_empty_safe() -> None:
    empty = cas.summarize_income({})
    assert empty["total"] is None
    assert empty["daily"] == []
    assert empty["daily_days_with_income"] == 0


def test_snapshot_prefers_the_income_api_total_over_the_dashboard(app_settings) -> None:
    snapshot = cas.build_account_snapshot(
        app_settings,
        board={"累计收益": ["36.07元", "昨日无变化"]},
        api_payload={},
        income_payload=ABSTRACT_PAYLOAD,
    )
    assert snapshot["revenue"]["total"] == 36.07
    assert snapshot["revenue"]["total_source"] == "mp/agw/income/income_statement_abstract"
    assert snapshot["revenue"]["income"]["month_income"] == 0.02


def test_snapshot_falls_back_to_the_dashboard_when_income_api_is_missing(app_settings) -> None:
    snapshot = cas.build_account_snapshot(
        app_settings,
        board={"累计收益": ["36.07元", "昨日无变化"]},
        api_payload={},
        income_payload={},
    )
    assert snapshot["revenue"]["total"] == 36.07
    assert "首页看板" in snapshot["revenue"]["total_source"]


def test_unsettled_day_row_is_not_reported_as_zero_income() -> None:
    """未结算那天行级 `total` 字面是 "0"，composition 写「待更新」。

    直接采信 total 会把它读成"那天没赚到钱"，和"还不知道"完全是两回事。
    """
    income = cas.summarize_income(ABSTRACT_PAYLOAD)
    row = next(item for item in income["daily"] if item["day"] == "2026-09-16")
    assert row["pending"] is True
    assert row["total"] is None
    assert row["article"] is None
    # 已结算的天不受影响
    settled = next(item for item in income["daily"] if item["day"] == "2026-08-31")
    assert settled["pending"] is False
    assert settled["total"] == 0.08


def test_window_and_month_are_derived_when_the_abstract_endpoint_fails() -> None:
    """`income_statement_abstract` 实测会间歇性 404，不能把区间/本月押在它上面。

    这里只给逐日明细（模拟 abstract 失败），区间与本月应当自己加总出来；
    且**未结算的天不能计入**（否则会把「待更新」当 0 拉低数字）。
    """
    only_daily = {"income_overview": ABSTRACT_PAYLOAD["income_overview"]}
    income = cas.summarize_income(only_daily, today=date(2026, 9, 17))
    # abstract 缺席 → 可提现/结算构成只能是未知
    assert income["total"] is None
    assert income["can_withdraw"] is None
    # 但区间自己算得出来
    assert income["window_income"] == 0.08
    assert income["window_income_source"] == "derived_from_daily"
    assert income["settled_days"] == 2
    # 本月仍是「未知」：9 月唯一那天未结算，**不能**当成本月赚了 0
    assert income["month_income"] is None


def test_month_income_derives_once_a_settled_day_exists_in_that_month() -> None:
    payload = json.loads(json.dumps(ABSTRACT_PAYLOAD))
    rows = payload["income_overview"]["body"]["data"]["detail"]
    rows.append(
        {
            "columns_detail": {
                "other_income": {"amount": "0", "composition": []},
                "tuwen_income": {
                    "amount": "0.10",
                    "composition": [
                        {"amount": "0.10", "name": "文章收益"},
                        {"amount": "0", "name": "视频收益"},
                        {"amount": "0", "name": "微头条收益"},
                    ],
                },
            },
            "day": "2026-09-10",
            "total": "0.10",
        }
    )
    only_daily = {"income_overview": payload["income_overview"]}
    income = cas.summarize_income(only_daily, today=date(2026, 9, 17))
    assert income["month_income"] == 0.10
    assert income["month_income_source"] == "derived_from_daily"
