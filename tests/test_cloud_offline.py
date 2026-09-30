"""GitHub Actions must keep the account offline until explicitly re-enabled in code."""

from pathlib import Path


def test_daily_run_defaults_to_dry_run_without_toutiao_publish() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")

    assert "dry_run:\n        description: 'Dry run mode (true/false)'\n        required: false\n        default: 'true'" in workflow
    assert "publish_toutiao:\n        description: 'Publish to Toutiao instead of draft-only (true/false)'\n        required: false\n        default: 'false'" in workflow


def test_offline_kill_switch_overrides_dispatch_and_schedule_values() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")
    switch = 'RUN_DRY_RUN="true"\n          RUN_PUBLISH_TOUTIAO="false"'

    switch_position = workflow.index(switch)
    publish_gate_position = workflow.index('if [ "$RUN_PUBLISH_TOUTIAO" = "true" ]; then')

    assert switch_position < publish_gate_position
