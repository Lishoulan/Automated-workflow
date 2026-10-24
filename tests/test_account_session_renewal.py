from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Error

ROOT = Path(__file__).resolve().parents[1]


def cookie_seed():
    return {
        "account": "robot_notes",
        "exported_at": "2026-10-25T00:00:00+00:00",
        "platforms": {"toutiao": {"cookie_count": 1, "cookies": [
            {"name": "sessionid", "value": "private-test-value", "domain": ".toutiao.com", "path": "/"}
        ]}},
    }


def restore_seed(tmp_path, seed):
    target = tmp_path / "account" / "exported_cookies.json"
    target.parent.mkdir()
    target.write_text("previous state", encoding="utf-8")
    env = {**os.environ, "TOUTIAO_COOKIE_SEED": seed}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/toutiao_cloud_session.py"),
         "--account", "robot_notes", "--output", str(target)],
        env=env, capture_output=True, text=True,
    )
    assert "private-test-value" not in result.stdout + result.stderr
    return result, target


def test_secret_seed_restores_portable_cookies_without_public_assets(tmp_path):
    result, target = restore_seed(tmp_path, json.dumps(cookie_seed()))
    assert result.returncode == 0, result.stderr
    restored = json.loads(target.read_text(encoding="utf-8"))
    assert restored["account"] == "robot_notes"
    assert restored["platforms"]["toutiao"]["cookies"][0]["value"] == "private-test-value"


def test_cookie_seed_accepts_an_empty_auxiliary_cookie_value(tmp_path):
    seed = cookie_seed()
    seed["platforms"]["toutiao"]["cookies"].append(
        {"name": "optional_flag", "value": "", "domain": ".toutiao.com", "path": "/"}
    )
    result, target = restore_seed(tmp_path, json.dumps(seed))
    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text(encoding="utf-8"))["platforms"]["toutiao"]["cookie_count"] == 2


@pytest.mark.parametrize("change", ["account", "empty", "domain", "malformed"])
def test_invalid_secret_never_overwrites_previous_credentials(tmp_path, change):
    seed = cookie_seed()
    if change == "account":
        seed["account"] = "another_account"
    elif change == "empty":
        seed["platforms"]["toutiao"]["cookies"] = []
    elif change == "domain":
        seed["platforms"]["toutiao"]["cookies"][0]["domain"] = "evil-toutiao.com"
    text = '{"private-test-value": invalid}' if change == "malformed" else json.dumps(seed)
    result, target = restore_seed(tmp_path, text)
    assert result.returncode == 2
    assert "Invalid account-bound Toutiao session" in result.stderr
    assert target.read_text(encoding="utf-8") == "previous state"


@pytest.mark.parametrize("errors", [1, 3])
def test_wechat_scan_survives_navigation_before_confirming_identity(app_settings, monkeypatch, tmp_path, errors):
    import playwright.sync_api
    from scripts import wechat_fixed_login as login

    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    monkeypatch.setenv("WECHAT_FIXED_DEVICE_DIR", str(tmp_path / "fixed"))
    app_settings.platform = "wechat"
    app_settings.wechat = replace(app_settings.wechat, account_name="expected")
    monkeypatch.setattr(login, "load_settings", lambda *a, **kw: app_settings)
    context = MagicMock()
    context.storage_state.return_value = {"cookies": [], "origins": []}
    context.new_page.return_value.evaluate.side_effect = [
        *[Error("Execution context was destroyed, most likely because of a navigation") for _ in range(errors)],
        {"account": "expected", "records": []}, 0,
    ]
    monkeypatch.setattr(login.WechatPublishService, "_launch_persistent_browser", lambda *a, **kw: context)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", MagicMock())
    assert login.main(["--account", "default", "--login", "--wait-seconds", "5"]) == 0
    proof = json.loads((app_settings.account_runtime_dir / "automation/fixed_login/backend-proof.json").read_text(encoding="utf-8"))
    assert proof["account"] == "expected" and proof["read_only"] is True
    status = json.loads((app_settings.account_runtime_dir / "automation/fixed_login/status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ready" and status["publication_attempted"] is False

