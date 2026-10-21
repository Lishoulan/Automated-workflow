"""用户决策 C1（2026-10-21）：开启公众号正式发表。

此前三重开关同时关闭，公众号 30 天 25 次运行、0 篇正式发表：

1. ``config.yaml`` 的 ``wechat.draft_only: true`` —— ``wechat_cloud_run.py`` 在
   ``--mode publish`` 时会检查它，为真就把模式降级成 draft；
2. 工作流 ``WECHAT_DELIVERY_MODE: draft`` —— 同上，也会把 publish 降级；
3. 工作流 ``WECHAT_OFFICIAL_PUBLISH_ENABLED: '0'`` —— 即使走到发布步骤也不会真正群发。

另外定时运行的 ``RUN_MODE`` 默认是 ``draft``，所以即使前三个开关打开，scheduled
运行仍然不会尝试发表。本文件锁住这四处都切到"允许发表"，同时保证
``workflow_dispatch`` 仍可用 ``inputs.mode`` 覆盖。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from toutiao_content_factory.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "wechat-daily-run.yml"


def _workflow_env() -> dict:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = payload["jobs"]
    job = next(iter(jobs.values()))
    return job["env"]


def test_config_allows_formal_publication() -> None:
    settings = load_settings(ROOT, account_key="default")
    assert settings.wechat.draft_only is False, "draft_only 会把 publish 模式静默降级"
    assert settings.wechat.enabled is True


def test_workflow_switches_allow_formal_publication() -> None:
    env = _workflow_env()
    assert str(env["WECHAT_DELIVERY_MODE"]).strip() == "publish"
    assert str(env["WECHAT_OFFICIAL_PUBLISH_ENABLED"]).strip() == "1"
    run_mode = str(env["RUN_MODE"])
    assert "|| 'publish'" in run_mode, "定时运行默认必须是 publish（可被 inputs.mode 覆盖）"
    assert "|| 'draft'" not in run_mode


def test_wechat_run_script_downgrade_gate_is_consistent() -> None:
    """脚本里的降级条件必须仍然存在（不是被绕过），只是默认值已允许发表。"""
    script = (ROOT / "scripts" / "wechat_cloud_run.py").read_text(encoding="utf-8")
    assert 'os.environ.get("WECHAT_DELIVERY_MODE") == "draft"' in script
    assert "wechat.draft_only" in script
