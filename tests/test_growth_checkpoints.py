from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.collect_growth_snapshot import build_snapshot, update_checkpoints


def _work(created: datetime, impressions: int, reads: int) -> dict:
    return {
        "article_attr": {
            "gid": str(int(created.timestamp())),
            "create_time": int(created.timestamp()),
            "status": 2,
            "article_type": "weitoutiao",
            "title": "老人拒交物业费，法院最后怎么判",
        },
        "article_stat": {
            "counters": [
                {"Name": "展示", "Count": impressions},
                {"Name": "阅读", "Count": reads},
            ]
        },
    }


def test_growth_checkpoints_capture_24h_then_72h_and_compute_delta() -> None:
    created = datetime(2026, 9, 25, 8, tzinfo=timezone(timedelta(hours=8)))
    first_observed = created + timedelta(hours=25)
    first = build_snapshot([_work(created, 1000, 25)], first_observed, 14)

    ledger, first_summary = update_checkpoints(first, {}, first_observed)
    gid = first["works"][0]["gid"]
    assert first_summary["captured_now"] == {"24h": 1, "72h": 0}
    assert ledger["works"][gid]["checkpoints"]["24h"]["capture_delay_hours"] == 1.0

    second_observed = created + timedelta(hours=74)
    second = build_snapshot([_work(created, 1800, 55)], second_observed, 14)
    ledger, second_summary = update_checkpoints(second, ledger, second_observed)

    entry = ledger["works"][gid]
    assert second_summary["captured_now"] == {"24h": 0, "72h": 1}
    assert entry["checkpoints"]["24h"]["impressions"] == 1000
    assert entry["checkpoints"]["72h"]["impressions"] == 1800
    assert entry["change_24h_to_72h"] == {
        "impressions": 800,
        "reads": 30,
        "comments": 0,
    }
