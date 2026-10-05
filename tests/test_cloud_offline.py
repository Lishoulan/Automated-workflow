"""GitHub Actions live publishing must fail closed behind an account-scoped switch."""

from pathlib import Path
import importlib.util
import re
from unittest.mock import MagicMock

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
        "output/accounts/*/growth_ops/source_material/",
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

    resolver = next(step for step in steps if step["name"] == "Resolve cloud runtime flags")["run"]
    assert 'ENGAGEMENT_ENABLED_VALUE="0"' in resolver
    assert '[ "$RUN_DRY_RUN" = "false" ] && [ "${{ vars.CLOUD_ENGAGEMENT_ONLINE }}" = "true" ]' in resolver
    assert 'echo "ENGAGEMENT_ENABLED=$ENGAGEMENT_ENABLED_VALUE"' in resolver


def test_cloud_preview_skips_interactions_but_keeps_read_only_collection(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("cloud_preview_test", "scripts/cloud_daily_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("ENGAGEMENT_ENABLED", "1")
    monkeypatch.setenv("PERFORMANCE_COLLECT_ENABLED", "1")
    for flag in ("MONTHLY_REVIEW_ENABLED", "MONETIZATION_ENABLED", "MATRIX_ENABLED"):
        monkeypatch.setenv(flag, "0")
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(module.subprocess, "run", run)

    module._run_extra_modules("robot_notes", dry_run=True)

    commands = [call.args[0] for call in run.call_args_list]
    assert len(commands) == 1
    assert "--collect-performance" in commands[0]
    assert "--account" in commands[0] and "robot_notes" in commands[0]


def test_probe_never_sends_real_credential_to_other_candidate_providers() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8"))
    probe = next(step for step in workflow["jobs"]["daily-run"]["steps"] if step["name"] == "Probe LLM endpoint")["run"]
    credential_loops = [(targets, body) for targets, body in re.findall(
        r"for candidate in (.*?)\bdo\b(.*?)\bdone\b", probe, re.S
    ) if "Authorization: Bearer ${OPENAI_API_KEY}" in body]
    assert len(credential_loops) == 1
    assert re.findall(r'https?://[^\s"\\]+', credential_loops[0][0]) == []
    assert '"$OPENAI_BASE_URL"' in credential_loops[0][0]


def test_account_state_is_saved_even_when_content_or_publish_gate_fails() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["daily-run"]["steps"]
    restore = next(step for step in steps if step.get("id") == "restore-history-cache")
    save = next(step for step in steps if step.get("name") == "Save publish history and checkpoints")
    assert restore["uses"] == "actions/cache/restore@v4"
    assert save["uses"] == "actions/cache/save@v4"
    assert "always()" in save["if"]
    assert "success()" not in save["if"]
    assert "cache-primary-key != ''" in save["if"]
    assert save["with"]["path"] == restore["with"]["path"]
    assert "growth_checkpoints.json" in save["with"]["path"]
    assert "github.run_attempt" in restore["with"]["key"]
    assert "steps.restore-history-cache.outputs.cache-primary-key" in save["with"]["key"]
    assert steps.index(save) > next(i for i,s in enumerate(steps) if s.get("name") == "Collect performance data")
    assert steps.index(save) < next(i for i,s in enumerate(steps) if s.get("name") == "Enforce publish result")


def test_checkpoint_recovery_downloads_one_artifact_for_selected_account() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8"))
    recovery = next(s for s in workflow["jobs"]["daily-run"]["steps"] if s.get("name") == "Recover earlier checkpoint artifact")
    assert workflow["permissions"]["actions"] == "read"
    assert "workflow_dispatch" in recovery["if"]
    script = recovery["run"]
    assert '"$CHECKPOINT_RECOVERY_RUN" =~ ^[0-9]+$' in script
    assert '--repo "$GITHUB_REPOSITORY"' in script
    assert '--name "$RECOVERY_ARTIFACT"' in script
    assert '--pattern' not in script
    assert '/output/accounts/$ACCOUNT_SLUG/growth_checkpoints.json' in script
    assert '"$ACCOUNT_OUTPUT_DIR/growth_checkpoint_recovery.json"' in script
