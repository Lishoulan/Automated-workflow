"""Guard the cost reconciliation workflow's safety invariants.

Reconciliation rewrites billing evidence, so the dangerous path (``--apply``)
must stay manual-only. These assertions exist because a careless edit can turn
a weekly read-only preview into an unattended writer of cost receipts.
"""
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/cost-reconciliation.yml"


@pytest.fixture(scope="module")
def workflow():
    assert WORKFLOW.is_file(), "cost-reconciliation.yml is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps_of(workflow):
    return workflow["jobs"]["reconcile"]["steps"]


def test_workflow_requests_no_write_permissions(workflow):
    assert workflow["permissions"]["contents"] == "read"
    assert "write" not in {workflow["permissions"].get(key) for key in workflow["permissions"]}


def test_scheduled_runs_never_apply(workflow):
    # 定时触发没有 inputs，env 默认值必须落到 '0'。
    env_block = workflow["jobs"]["reconcile"]["env"]
    assert "inputs.apply == true && '1' || '0'" in str(env_block["RECONCILE_APPLY"])
    applying = [step for step in steps_of(workflow) if "--apply" in str(step.get("run", ""))]
    assert applying, "expected at least one step capable of applying"
    for step in applying:
        # --apply 只允许出现在 RECONCILE_APPLY=1 的 shell 分支里，不能无条件拼上。
        body = step["run"]
        assert 'RECONCILE_APPLY" = \'1\'' in body, f"apply must be branch-gated: {step.get('name')}"
        assert 'FLAGS=""' in body, f"flag variable must start empty: {step.get('name')}"
        assert '$FLAGS' in body.split("--account")[1], f"apply must reach the CLI through the gated variable: {step.get('name')}"


def test_apply_defaults_off(workflow):
    assert workflow[True]["workflow_dispatch"]["inputs"]["apply"]["default"] is False


def test_state_writing_steps_are_manual_only(workflow):
    mutating = [step for step in steps_of(workflow)
                if "cache/save" in str(step.get("uses", "")) or "cp -a reconcile_snapshot" in str(step.get("run", ""))]
    assert mutating, "expected cache write-back steps"
    for step in mutating:
        assert "RECONCILE_APPLY == '1'" in step.get("if", ""), f"mutating step must stay manual: {step.get('name')}"


def test_receipts_cache_prefix_matches_daily_run(workflow):
    restore = [step for step in steps_of(workflow) if "cache/restore" in str(step.get("uses", ""))]
    assert restore
    keys = restore[0]["with"]["restore-keys"]
    assert "daily-publish-history-" in keys


def test_missing_billing_export_exits_zero_without_reconciling(workflow):
    guard = next(step for step in steps_of(workflow) if step.get("id") == "guard")
    assert "exit 0" in guard["run"]
    assert "::notice::" in guard["run"]
    downstream = [step for step in steps_of(workflow) if "reconcile_generation_costs.py" in str(step.get("run", ""))]
    assert all("steps.guard.outputs.ready == '1'" in step.get("if", "") for step in downstream)
