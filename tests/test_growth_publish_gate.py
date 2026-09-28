from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.collect_growth_snapshot import build_snapshot
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.content_guard_service import ContentGuardService


def test_published_editorial_instruction_is_a_hard_blocker() -> None:
    body = (
        "公开材料能确认，相关权益需要进一步核对。"
        "把素材里能确认的部分列一下：当事人曾到窗口咨询。"
    )

    result = ArticleQualityChecker.check_body_integrity(body)

    assert result.passed is False
    assert any("写作或事实核对指令" in issue for issue in result.issues)


def test_repeated_long_prose_is_blocked_but_short_refrain_is_not() -> None:
    repeated = (
        "当事人先向社区求助，随后联系家属核对情况，工作人员记录了发现时间和处理经过。"
        "社区又联系了急救人员，并安排一名工作人员陪同老人到医院完成检查。"
        "家属在确认情况后，也向社区提供了日常联系人的最新号码。"
    )
    body = repeated + "在此之后，社区安排了后续联系。" + repeated
    checker = ArticleQualityChecker()

    assert any("重复的长段" in issue for issue in checker.check_body_integrity(body).issues)
    assert checker.check_body_integrity("老人先联系社区。社区随后联系家属。老人最终得到帮助。").passed


def test_publish_guard_rejects_editorial_leak(app_settings, logger, tmp_path) -> None:
    run_dir = tmp_path / "run"
    publish_dir = run_dir / "publish"
    publish_dir.mkdir(parents=True)
    (publish_dir / "title.txt").write_text("社区如何帮助独居老人", encoding="utf-8")
    (publish_dir / "article.md").write_text(
        "邻居发现老人后联系了社区。报道没有交代的部分不补。",
        encoding="utf-8",
    )

    report = ContentGuardService(app_settings, logger).build_publish_guard_report(run_dir)

    assert any("写作或事实核对指令" in item for item in report["blocking_flags"])


def _work(created: datetime, title: str, kind: str, impressions: int, reads: int) -> dict:
    return {
        "article_attr": {
            "gid": str(int(created.timestamp())),
            "create_time": int(created.timestamp()),
            "status": 2,
            "article_type": kind,
            "title": title,
        },
        "article_stat": {
            "counters": [
                {"Name": "展现", "Count": impressions},
                {"Name": "阅读", "Count": reads},
            ]
        },
    }


def test_growth_snapshot_separates_formats_and_counts_duplicate_titles() -> None:
    now = datetime(2026, 9, 28, 20, tzinfo=timezone(timedelta(hours=8)))
    contents = [
        _work(now - timedelta(days=1), "同一个标题", "article", 100, 0),
        _work(now - timedelta(days=2), "同一个标题", "article", 50, 0),
        _work(now - timedelta(days=3), "另一件事", "weitoutiao", 300, 12),
        _work(now - timedelta(days=20), "过期作品", "article", 999, 9),
    ]

    snapshot = build_snapshot(contents, now, 14)

    assert snapshot["summary"]["article"] == {
        "works": 2,
        "unique_titles": 1,
        "duplicate_titles": {"同一个标题": 2},
        "impressions": 150,
        "reads": 0,
        "zero_read_works": 2,
        "median_impressions": 75,
    }
    assert snapshot["summary"]["weitoutiao"]["reads"] == 12
    assert all(work["title"] != "过期作品" for work in snapshot["works"])
