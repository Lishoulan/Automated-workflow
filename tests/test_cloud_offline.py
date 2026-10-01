"""GitHub Actions live publishing must fail closed behind an account-scoped switch."""

from pathlib import Path


def test_daily_run_defaults_to_dry_run_without_toutiao_publish() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")

    assert "dry_run:\n        description: 'Dry run mode (true/false)'\n        required: false\n        default: 'true'" in workflow
    assert "publish_toutiao:\n        description: 'Publish to Toutiao instead of draft-only (true/false)'\n        required: false\n        default: 'false'" in workflow


def test_robot_notes_live_switch_is_account_scoped_and_fails_closed() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")

    assert 'ROBOT_NOTES_ONLINE="$(normalize_bool "${{ vars.CLOUD_ROBOT_NOTES_ONLINE }}")"' in workflow
    assert 'if [ "$RUN_ACCOUNT" != "robot_notes" ] || [ "$ROBOT_NOTES_ONLINE" != "true" ]; then' in workflow
    assert 'RUN_DRY_RUN="true"\n            RUN_PUBLISH_TOUTIAO="false"' in workflow
    assert 'elif [ "$EVENT_NAME" != "workflow_dispatch" ]; then' in workflow
    assert 'RUN_DRY_RUN="false"\n            RUN_PUBLISH_TOUTIAO="true"' in workflow


def test_revenue_and_performance_ledgers_persist_between_cloud_runs() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")

    for path in (
        "output/accounts/*/growth_checkpoints.json",
        "output/accounts/*/performance_metrics.json",
        "output/accounts/*/growth_ops/performance/",
        "output/accounts/*/growth_ops/revenue_attribution/",
    ):
        assert path in workflow
