import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from toutiao_content_factory.services.generation_cost_service import BEIJING, GenerationCostAttempt, build_cost_summary


def settings(tmp_path):
    return SimpleNamespace(output_dir=tmp_path, platform="wechat", account_key="default")


def test_estimated_tokens_do_not_prove_actual_cost_or_profit(tmp_path):
    with GenerationCostAttempt(settings(tmp_path), None, kind="text", model="existing") as receipt:
        receipt.complete({"usage": {"prompt_tokens": 1000, "completion_tokens": 2000}, "id": "req_123"}, prompt_rate=.1, completion_rate=.2)
    report = build_cost_summary(tmp_path)
    assert report["estimated_by_currency"] == {"CNY": .5}
    assert report["actual_total"] is None and report["net_profit"] is None
    assert report["status"] == "actual_cost_unverified"
    row = json.loads(receipt.path.read_text(encoding="utf-8"))
    assert row["provider_request_id"] == "req_123"


def test_failure_missing_usage_and_interrupted_call_never_become_free(tmp_path):
    first = GenerationCostAttempt(settings(tmp_path), None, kind="image", model="existing")
    with pytest.raises(RuntimeError):
        with first:
            raise RuntimeError("secret-key private prompt")
    with GenerationCostAttempt(settings(tmp_path), None, kind="free_image", model="free") as second:
        second.complete({"id": "cfut_private_secret", "prompt": "private prompt", "api_key": "secret-key"})
    interrupted = GenerationCostAttempt(settings(tmp_path), None, kind="text", model="existing")
    interrupted.__enter__()
    report = build_cost_summary(tmp_path)
    assert report["recorded_calls"] == 3
    assert report["by_status"] == {"failed": 1, "succeeded": 1, "started": 1}
    assert report["estimated_by_currency"] == {} and report["missing_usage_calls"] == 3
    serialized = "".join(p.read_text(encoding="utf-8") for p in first.path.parent.glob("*.json"))
    assert "private prompt" not in serialized and "secret-key" not in serialized and "cfut_private_secret" not in serialized


def test_receipt_io_failure_does_not_repeat_or_fail_provider_work(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(type(tmp_path), "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with GenerationCostAttempt(settings(tmp_path), logging.getLogger("test"), kind="text", model="m") as receipt:
        calls.append("provider")
        receipt.complete()
    assert calls == ["provider"]


def test_cost_window_handles_naive_clock_old_and_corrupt_rows(tmp_path):
    now = datetime(2026, 10, 9, 18, tzinfo=BEIJING)
    with GenerationCostAttempt(settings(tmp_path), None, kind="text", model="m") as receipt:
        receipt.row["started_at"] = (now - timedelta(days=31)).isoformat()
        receipt.complete()
    receipt.path.with_name("bad.json").write_text("[]", encoding="utf-8")
    report = build_cost_summary(tmp_path, now=now.replace(tzinfo=None))
    assert report["recorded_calls"] == 0 and report["invalid_receipts"] == 1
    assert report["status"] == "untracked" and report["actual_total"] is None


def test_distinct_calls_and_currency_amounts_stay_distinct(tmp_path):
    for currency, amount in (("CNY", 1), ("USD", 2)):
        with GenerationCostAttempt(settings(tmp_path), None, kind="image", model="m") as receipt:
            receipt.complete()
            receipt.row.update(estimated_currency=currency, estimated_amount=amount)
    report = build_cost_summary(tmp_path)
    assert report["recorded_calls"] == 2
    assert report["estimated_by_currency"] == {"CNY": 1, "USD": 2}


def test_bad_usage_does_not_turn_success_into_a_retry_or_zero_cost(tmp_path):
    with GenerationCostAttempt(settings(tmp_path), None, kind="text", model="m") as receipt:
        receipt.complete({"usage": {"prompt_tokens": 10 ** 400, "completion_tokens": True,
                                   "total_tokens": float("nan")}}, prompt_rate=.1, completion_rate=.2)
    report = build_cost_summary(tmp_path)
    assert report["by_status"] == {"succeeded": 1} and report["estimated_by_currency"] == {}


def test_explicit_matched_provider_billing_is_distinct_from_estimates_and_profit(tmp_path):
    with GenerationCostAttempt(settings(tmp_path), None, kind="image", model="m") as receipt:
        receipt.complete({"id": "req_matched", "billing": {"request_id": "req_matched", "status": "charged", "amount": .12, "currency": "USD"}})
    report = build_cost_summary(tmp_path)
    assert report["status"] == "tracked_cost_verified"
    assert report["actual_total"] == .12 and report["actual_currency"] == "USD"
    assert report["actual_verified_by_currency"] == {"USD": .12} and report["net_profit"] is None
    with GenerationCostAttempt(settings(tmp_path), None, kind="text", model="m") as second:
        second.complete()
    report = build_cost_summary(tmp_path)
    assert report["status"] == "actual_cost_partially_verified"
    assert report["actual_total"] is None and report["unverified_cost_calls"] == 1


@pytest.mark.parametrize("changes,sdk_retries", [
    ({"request_id": "other"}, 0), ({"currency": None}, 0),
    ({"amount": True}, 0), ({"status": "estimated"}, 0), ({}, 2),
])
def test_unmatched_or_retry_incomplete_billing_cannot_prove_actual_cost(tmp_path, changes, sdk_retries):
    billing = {"request_id": "req_1", "status": "charged", "amount": .1, "currency": "CNY", **changes}
    with GenerationCostAttempt(settings(tmp_path), None, kind="text", model="m", sdk_retries=sdk_retries) as receipt:
        receipt.complete({"id": "req_1", "billing": billing})
    report = build_cost_summary(tmp_path)
    assert report["actual_total"] is None and report["verified_cost_calls"] == 0
