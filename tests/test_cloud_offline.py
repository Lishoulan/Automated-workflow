"""GitHub Actions live publishing must fail closed behind an account-scoped switch."""

from pathlib import Path

import yaml


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


def test_publish_enforcement_accepts_successful_weitoutiao_only_run() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")

    assert "WEITOUTIAO_STATUS=$(python -c" in workflow
    assert 'echo "RUN_WEITOUTIAO_STATUS=$WEITOUTIAO_STATUS" >> "$GITHUB_ENV"' in workflow
    assert 'success|published|submitted|submitted_for_review|target_reached)' in workflow
    assert '[ "${RUN_STATUS:-}" = "no_topics" ] || [ "${RUN_STATUS:-}" = "already_published" ]' in workflow
    assert "neither an article submission with pgc_id nor an accepted WeiToutiao-only result" in workflow


def test_engagement_requires_live_mode_and_separate_explicit_switch() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["daily-run"]["steps"]
    mutating_steps = [step for step in steps if "--engagement " in step.get("run", "")]

    assert mutating_steps
    for step in mutating_steps:
        conditions = set(step["if"].split(" && "))
        assert "env.RUN_DRY_RUN == 'false'" in conditions
        assert "vars.CLOUD_ENGAGEMENT_ONLINE == 'true'" in conditions
