from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from wechat_cloud_session import decrypt_state, encrypt_state, restore_session, save_session
from wechat_cloud_run import configure_environment, latest_accepted_record
import wechat_cloud_run as cloud
from toutiao_content_factory.services import cross_channel_state as xstate


@pytest.fixture
def state():
    return {"cookies": [{"name": "login", "value": "private-value", "domain": "mp.weixin.qq.com", "path": "/"},
                        {"name": "other", "value": "unrelated", "domain": ".bing.com", "path": "/"}],
            "origins": [{"origin": "https://mp.weixin.qq.com", "localStorage": []}]}


def test_session_is_encrypted_and_bound_to_one_account(state):
    key = Fernet.generate_key().decode()
    sealed = encrypt_state(state, key, "default")
    assert b"private-value" not in sealed
    recovered = decrypt_state(sealed, key, "default")
    assert len(recovered["cookies"]) == 1
    with pytest.raises(ValueError, match="different account"):
        decrypt_state(sealed, key, "robot_notes")


def test_tampered_session_is_rejected(state):
    key = Fernet.generate_key().decode()
    encrypted = encrypt_state(state, key, "default")
    with pytest.raises(ValueError, match="Cannot decrypt"):
        decrypt_state(encrypted[:-5] + b"XXXXX", key, "default")


def test_restored_state_and_refresh_survive_a_new_runner(app_settings, tmp_path, state):
    key = Fernet.generate_key().decode()
    encrypted = tmp_path / "session.enc"
    seed = encrypt_state(state, key, "default").decode()
    target = restore_session(app_settings, encrypted, key, seed)
    refreshed = json.loads(target.read_text(encoding="utf-8"))
    refreshed["cookies"][0]["value"] = "refreshed-value"
    target.write_text(json.dumps(refreshed), encoding="utf-8")
    save_session(app_settings, encrypted, key)
    target.unlink()
    restored = restore_session(app_settings, encrypted, key, seed)
    assert json.loads(restored.read_text(encoding="utf-8"))["cookies"][0]["value"] == "refreshed-value"
    assert b"refreshed-value" not in encrypted.read_bytes()


@pytest.mark.parametrize("status, accepted", [("submitted", True), ("published", True), ("draft_saved", False), ("error", False)])
def test_only_current_day_accepted_publications_are_recorded(tmp_path, status, accepted):
    history = tmp_path / "history.json"
    history.write_text(json.dumps([{"platform": "wechat", "status": status, "published_at": "2026-10-06T07:20:00"}]))
    assert bool(latest_accepted_record(history, "2026-10-06")) is accepted
    assert latest_accepted_record(history, "2026-10-07") is None


def test_cloud_is_wechat_only_and_headless(monkeypatch):
    import os
    monkeypatch.setenv("TOUTIAO_PUBLISH_ENABLED", "1")
    configure_environment("default")
    assert os.environ["TOUTIAO_PUBLISH_ENABLED__DEFAULT"] == "0"
    assert os.environ["TOUTIAO_PUBLISH_HEADLESS"] == "1"
    assert os.environ["TARGET_PLATFORM__DEFAULT"] == "wechat"


def test_cloud_guard_failure_stops_instead_of_looking_like_no_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(xstate, "_run_git", lambda *a: MagicMock(returncode=1, stderr="network connection refused"))
    with pytest.raises(RuntimeError, match="remote publication guard"):
        xstate.read_remote_state(tmp_path, strict=True)
    assert xstate.read_remote_state(tmp_path) == {}


def test_wechat_and_toutiao_use_separate_cloud_state_branches(tmp_path, monkeypatch):
    reader = MagicMock(return_value={})
    monkeypatch.setattr(xstate, "read_remote_state", reader)
    xstate.read_cloud_published_today(tmp_path, platform="wechat")
    assert reader.call_args.kwargs["branch"] == xstate.WECHAT_STATE_BRANCH
    xstate.read_cloud_published_today(tmp_path, platform="toutiao")
    assert reader.call_args.kwargs["branch"] == xstate.STATE_BRANCH


def test_workflow_never_uploads_plaintext_profiles_or_sessions():
    workflow = yaml.load((ROOT / ".github/workflows/wechat-daily-run.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    for step in workflow["jobs"]["wechat"]["steps"]:
        if "actions/cache" in step.get("uses", "") or "actions/upload-artifact" in step.get("uses", ""):
            paths = step["with"]["path"]
            assert "browser_state" not in paths
            assert "wechat_profile" not in paths
            assert "storage_state" not in paths


@pytest.mark.parametrize("guard_state, guard_error, expected_code, child_count", [
    ({}, False, 0, 1), ({}, True, 1, 0),
    ({"entries": {"2026-10-06::default": {"wechat": {"status": "published"}}}}, False, 0, 0),
])
def test_cloud_check_never_requests_publication(app_settings, monkeypatch, guard_state, guard_error, expected_code, child_count):
    from datetime import datetime
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda account: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "save_session", MagicMock())
    reader = MagicMock(return_value=guard_state)
    if guard_error:
        reader.side_effect = RuntimeError("network failure")
    monkeypatch.setattr(cloud.xstate, "read_remote_state", reader)
    child = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(cloud.subprocess, "run", child)
    mode = "publish" if guard_state.get("entries") else "check"
    if mode == "publish":
        day = datetime.now().strftime("%Y-%m-%d")
        reader.return_value = {"entries": {day + "::default": {"wechat": {"status": "published"}}}}
    assert cloud.main(["--mode", mode]) == expected_code
    assert child.call_count == child_count
    for call in child.call_args_list:
        assert "--enable-auto-publish" not in call.args[0]
