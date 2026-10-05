from types import SimpleNamespace

import pytest

from toutiao_content_factory.services.content_income_service import (
    ARTICLE_INCOME_PATH, ContentIncomeCollector, content_income_rows, exact_yuan, platform_item_id, verified_article_income_rows,
)


def test_content_income_does_not_attribute_account_totals_or_ambiguous_units():
    payload = {"total_income": "36.33元", "data": [
        {"item_id": "101", "total_income": "0.02元"},
        {"item_id": "102", "total_income": 100},
        {"item_id": "103", "total_income": 0, "currency": "CNY"},
        {"item_id": "104", "total_income": 2, "unit": "分"},
        {"item_id": "105", "total_income": 0, "unit": "元", "is_income_ready": False},
        {"item_id": "106", "income": "0.50元", "date": "2026-10-04"},
    ]}
    assert content_income_rows(payload) == [{"gid": "101", "amount": 0.02}, {"gid": "103", "amount": 0.0}]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, "待更新", "", None])
def test_unknown_money_never_becomes_zero(value):
    assert exact_yuan(value, "元") is None


def test_response_collection_matches_ids_and_conflicts_fail_closed(tmp_path):
    collector = ContentIncomeCollector()
    response = SimpleNamespace(url="https://mp.toutiao.com/mp/agw/income/test?token=secret",
                               status=200, headers={"content-type": "application/json"},
                               json=lambda: {"data": [{"item_id": "101", "total_income": "0.02元"}]})
    collector.observe_response(response)
    assert collector.amount_for("https://www.toutiao.com/w/101/") == 0.02
    assert collector.amount_for("https://www.toutiao.com/w/102/") is None
    assert platform_item_id("https://unrelated.test/w/101/") == ""
    collector.add("101", 0.03, "income_page_dom")
    assert collector.amount_for("https://www.toutiao.com/w/101/") is None
    path = tmp_path / "report.json"
    collector.write_report(path, matched=0)
    assert "secret" not in path.read_text()


def test_failed_or_unrelated_api_responses_are_ignored():
    collector = ContentIncomeCollector()
    for url, code in [("https://evil.test/income/test", 0), ("https://mp.toutiao.com/income/test", 401)]:
        response = SimpleNamespace(url=url, status=200, headers={"content-type": "application/json"},
                                   json=lambda: {"code": code, "item_id": "101", "total_income": "2元"})
        collector.observe_response(response)
    assert collector.amounts == {}


def test_observed_income_api_uses_fen_not_yuan_and_requires_cumulative_schema():
    payload = {"code": 0, "data": {"columns": [{"key": "total_income", "tip": "单篇内容的累计收益"}],
               "toutiao_article_income": [{"item_id": "1876783431192644", "article_income": 3,
                "income_data": {"total_income": {"amount": "3"}}, "create_time": 1789840155644,
                "genre_type": 3, "title": "真实标题\n正文"}]}}
    rows = verified_article_income_rows(payload)
    assert rows[0]["amount"] == 0.03
    assert rows[0]["gid"] == "1876783431192644"
    payload["data"]["columns"][0]["tip"] = "账号收益"
    assert verified_article_income_rows(payload) == []


def test_current_day_income_is_not_treated_as_settled_zero(tmp_path):
    collector = ContentIncomeCollector()
    collector.ready_date = "2026-10-03"
    collector.work_rows = {
        "101": {"gid": "101", "amount": 0, "created_at": "2026-10-05T09:00:00+08:00"},
        "102": {"gid": "102", "amount": 0, "created_at": "2026-10-02T09:00:00+08:00"},
    }
    path = tmp_path / "report.json"
    collector.write_report(path, matched=0)
    import json
    works = json.loads(path.read_text())["works"]
    assert works[0]["amount"] is None
    assert works[1]["amount"] == 0
