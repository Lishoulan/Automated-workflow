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


def test_first_late_observation_only_captures_72h() -> None:
    created = datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(hours=120)
    snapshot = build_snapshot([_work(created, 900, 20)], observed, 14)

    ledger, summary = update_checkpoints(snapshot, {}, observed)
    entry = ledger["works"][snapshot["works"][0]["gid"]]

    assert summary["captured_now"] == {"24h": 0, "72h": 1}
    assert set(entry["checkpoints"]) == {"72h"}
    assert entry["checkpoints"]["72h"]["capture_quality"] == "late"
    assert "change_24h_to_72h" not in entry


def test_schema_one_duplicate_backfill_is_repaired() -> None:
    created = datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    observed = created + timedelta(hours=121)
    captured_at = observed.isoformat(timespec="seconds")
    snapshot = build_snapshot([_work(created, 900, 20)], observed, 14)
    gid = snapshot["works"][0]["gid"]
    sample = {
        "captured_at": captured_at,
        "observed_age_hours": 120,
        "capture_delay_hours": 96,
        "impressions": 900,
        "reads": 20,
        "comments": 0,
    }
    previous = {
        "schema": 1,
        "works": {
            gid: {
                "gid": gid,
                "checkpoints": {"24h": dict(sample), "72h": dict(sample)},
                "change_24h_to_72h": {"impressions": 0, "reads": 0, "comments": 0},
            }
        },
    }

    ledger, _ = update_checkpoints(snapshot, previous, observed)
    entry = ledger["works"][gid]

    assert ledger["schema"] == 2
    assert set(entry["checkpoints"]) == {"72h"}
    assert entry["checkpoints"]["72h"]["capture_quality"] == "late"
    assert "change_24h_to_72h" not in entry


def test_late_24h_and_72h_observations_do_not_claim_an_exact_window_delta() -> None:
    created = datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    first_observed = created + timedelta(hours=60)
    first = build_snapshot([_work(created, 300, 5)], first_observed, 14)
    ledger, _ = update_checkpoints(first, {}, first_observed)

    second_observed = created + timedelta(hours=110)
    second = build_snapshot([_work(created, 900, 20)], second_observed, 14)
    ledger, _ = update_checkpoints(second, ledger, second_observed)
    entry = ledger["works"][second["works"][0]["gid"]]

    assert set(entry["checkpoints"]) == {"24h", "72h"}
    assert entry["checkpoints"]["24h"]["capture_quality"] == "late"
    assert entry["checkpoints"]["72h"]["capture_quality"] == "late"
    assert "change_24h_to_72h" not in entry
