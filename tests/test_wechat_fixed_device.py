from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import wechat_cloud_run as cloud
from wechat_fixed_login import confirmed_identity
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.services.wechat_publication_verifier import _open_backend_context


def test_fixed_profiles_survive_checkout_and_isolate_accounts(app_settings, monkeypatch, tmp_path):
    app_settings.platform = "wechat"
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    root = tmp_path / "fixed"
    monkeypatch.setenv("WECHAT_FIXED_DEVICE_DIR", str(root))
    first = app_settings.account_runtime_dir
    app_settings.project_root = tmp_path / "another-checkout"
    assert app_settings.account_runtime_dir == first == root / "default"
    app_settings.account_slug = "other"
    assert app_settings.account_runtime_dir == root / "other"
    app_settings.platform = "toutiao"
    assert app_settings.account_runtime_dir == app_settings.project_root / ".runtime"


def test_fixed_mode_rejects_relative_or_missing_directory(app_settings, monkeypatch):
    app_settings.platform = "wechat"
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    monkeypatch.setenv("WECHAT_FIXED_DEVICE_DIR", "relative")
    with pytest.raises(ValueError, match="absolute"):
        _ = app_settings.account_runtime_dir
    monkeypatch.delenv("WECHAT_FIXED_DEVICE_DIR")
    with pytest.raises(ValueError, match="absolute"):
        _ = app_settings.account_runtime_dir


def test_fixed_login_rejects_wrong_account_before_saving():
    assert not confirmed_identity({"account": ""}, "expected")
    assert confirmed_identity({"account": "expected"}, "expected")
    with pytest.raises(RuntimeError, match="does not match"):
        confirmed_identity({"account": "other"}, "expected")
    with pytest.raises(ValueError, match="expected"):
        confirmed_identity({"account": "expected"}, "")


def test_fixed_verification_uses_the_publisher_profile(app_settings, monkeypatch):
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    launch = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(WechatPublishService, "_launch_persistent_browser", launch)
    monkeypatch.setattr(WechatPublishService, "_resolve_browser_profile_dir", lambda self: Path("fixed-profile"))
    playwright = MagicMock()
    owner, context = _open_backend_context(playwright, app_settings, lambda: 1000)
    assert owner is context is launch.return_value
    assert launch.call_args.kwargs["profile_dir"] == Path("fixed-profile")
    playwright.chromium.launch.assert_not_called()


def test_ephemeral_context_failure_closes_browser(app_settings, monkeypatch):
    monkeypatch.delenv("WECHAT_EXECUTION_HOST", raising=False)
    playwright = MagicMock()
    browser = playwright.chromium.launch.return_value
    browser.new_context.side_effect = RuntimeError("invalid state")
    with pytest.raises(RuntimeError, match="invalid state"):
        _open_backend_context(playwright, app_settings, lambda: 1000)
    browser.close.assert_called_once()


@pytest.mark.parametrize("refresh", [False, True])
def test_fixed_run_does_not_restore_cloud_cookies(app_settings, monkeypatch, refresh):
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    monkeypatch.setenv("WECHAT_SESSION_REFRESH", "1" if refresh else "0")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    restore = MagicMock()
    save = MagicMock()
    monkeypatch.setattr(cloud, "restore_session", restore)
    monkeypatch.setattr(cloud, "save_session", save)
    monkeypatch.setattr(cloud.xstate, "read_remote_state", MagicMock(return_value={}))
    child = MagicMock(return_value=MagicMock(returncode=2))
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.main(["--mode", "check"]) == (1 if refresh else 2)
    restore.assert_not_called()
    save.assert_not_called()
    assert child.call_count == (0 if refresh else 1)
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text())
    assert report["execution_host"] == "fixed"
    assert not report.get("publication_attempted")


def test_workflow_is_cloud_only_and_keeps_shared_concurrency():
    workflow = yaml.load((ROOT / ".github/workflows/wechat-daily-run.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["wechat"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["env"]["WECHAT_EXECUTION_HOST"] == "cloud"
    assert "WECHAT_FIXED_DEVICE_DIR" not in job["env"]
    assert workflow["concurrency"]["group"] == "wechat-default"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    for step in job["steps"]:
        if "actions/cache" in step.get("uses", ""):
            if step.get("name") == "Import existing accepted history":
                assert set(step["with"]["path"].splitlines()) == {
                    "output/wechat_publish_history.json", "output/growth_ops/generation_costs/"}
            else:
                assert ".runtime/automation/wechat_cloud_session.enc" in step["with"]["path"]
                assert "WECHAT_SESSION_CACHE_VERSION" in step["with"]["key"]


def test_fixed_manual_publish_cannot_bypass_shared_budget(app_settings, monkeypatch):
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    reader = MagicMock()
    child = MagicMock()
    monkeypatch.setattr(cloud.xstate, "read_remote_state", reader)
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.main(["--mode", "publish"]) == 1
    reader.assert_not_called()
    child.assert_not_called()


def test_login_check_rejects_wrong_live_account(app_settings, monkeypatch):
    from dataclasses import replace
    import playwright.sync_api
    import scheduled_wechat_publish as scheduled
    app_settings.wechat = replace(app_settings.wechat, account_name="expected")
    profile = app_settings.account_runtime_dir / "wechat_profile"
    profile.mkdir(parents=True)
    browser = MagicMock()
    browser.new_page.return_value.evaluate.return_value = {"account": "different"}
    monkeypatch.setattr(WechatPublishService, "_launch_persistent_browser", MagicMock(return_value=browser))
    monkeypatch.setattr(scheduled, "ensure_browser_session_ready", MagicMock(return_value={"session_ready": True}))
    with pytest.raises(RuntimeError, match="different or unverifiable"):
        scheduled.check_login(app_settings)
    browser.close.assert_called_once()
