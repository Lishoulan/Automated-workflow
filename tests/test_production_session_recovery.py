"""Production regressions: expired sessions must be actionable and fail closed."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scripts import wechat_cloud_run as cloud
from toutiao_content_factory.exceptions import PlatformAuthError
from toutiao_content_factory.services import wechat_publication_verifier as verifier
from toutiao_content_factory.services.performance_service import PerformanceService


@pytest.mark.parametrize("errors, expected_code", [
    ([{"platform": "toutiao", "code": "login_required", "message": "Login expired"}], 2),
    ([{"platform": "toutiao", "message": "Network unavailable"}], 1),
    ([], 0),
])
def test_collection_cli_reports_failed_collection_as_nonzero(app_settings, monkeypatch, errors, expected_code):
    import main as entry
    from toutiao_content_factory.services import performance_service

    monkeypatch.setattr(entry, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(entry.sys, "argv", ["main.py", "--collect-performance"])
    monkeypatch.setattr(performance_service, "PerformanceService", lambda *a: SimpleNamespace(
        collect_performance_data=lambda: {"records": [], "errors": errors}))

    assert entry.main() == expected_code


def test_performance_login_redirect_is_not_an_empty_success(app_settings, monkeypatch, logger):
    service = PerformanceService(app_settings, logger)

    class LoginPage:
        url = "https://mp.toutiao.com/auth/page/login?redirect_url=private"

        def goto(self, *a, **kw):
            pass

    monkeypatch.setattr(service, "_wait_for_dashboard_render", lambda *a, **kw:
        pytest.fail("A login page must not be retried as a loading dashboard"))
    monkeypatch.setattr(service, "_collect_toutiao_metrics", lambda **kw:
        service._open_target_with_retry(page=LoginPage(), platform="toutiao",
            url="https://mp.toutiao.com/profile_v4/manage/content/all", timeout_ms=100))
    monkeypatch.setattr(service, "_sync_attribution_snapshots", lambda *a, **kw: {})
    monkeypatch.setattr(service, "_build_automatic_reports", lambda: {})
    app_settings.output_dir.mkdir(parents=True)

    result = service.collect_performance_data()

    assert result["records"] == []
    assert result["errors"][0]["code"] == "login_required"
    assert result["errors"][0]["platform"] == "toutiao"
    assert "private" not in result["errors"][0]["message"]
    saved = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
    assert saved["errors"][0]["code"] == "login_required"


def test_delayed_login_redirect_takes_precedence_over_dashboard_text(app_settings, monkeypatch, logger):
    service = PerformanceService(app_settings, logger)
    page = SimpleNamespace(url="https://mp.toutiao.com/profile_v4/manage/content/all", goto=lambda *a, **kw: None)

    def render(current, **kw):
        current.url = "https://mp.toutiao.com/auth/page/login"

    monkeypatch.setattr(service, "_wait_for_dashboard_render", render)
    monkeypatch.setattr(service, "_page_has_metric_signal", lambda current: True)

    with pytest.raises(PlatformAuthError):
        service._open_target_with_retry(page=page, platform="toutiao",
            url="https://mp.toutiao.com/profile_v4/manage/content/all", timeout_ms=100)


@pytest.mark.parametrize("operation", ["inspect", "verify"])
def test_wechat_login_qr_does_not_wait_for_an_account_nickname(app_settings, monkeypatch, operation):
    import playwright.sync_api

    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    page.url = "https://mp.weixin.qq.com/"
    page.locator.return_value.first.is_visible.return_value = True
    page.locator.return_value.first.wait_for.side_effect = AssertionError(
        "Waiting for an account nickname on an already visible login page")
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: manager)

    with pytest.raises(PlatformAuthError) as failure:
        if operation == "inspect":
            verifier.inspect_backend(app_settings)
        else:
            verifier.verify_pending(app_settings, [{"run_key": "old:1", "account": "小椰科技"}])

    assert failure.value.blocking_platforms == ["wechat"]
    browser.close.assert_called_once()
    page.click.assert_not_called()


def test_wechat_unknown_page_timeout_is_not_misreported_as_login_expiry(app_settings, monkeypatch):
    import playwright.sync_api

    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    page.url = "https://mp.weixin.qq.com/cgi-bin/home"
    page.locator.return_value.first.is_visible.return_value = False
    page.locator.return_value.first.wait_for.side_effect = PlaywrightTimeoutError("Backend did not render")
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: manager)

    with pytest.raises(PlaywrightTimeoutError):
        verifier.verify_pending(app_settings, [{"run_key": "old:1", "account": "小椰科技"}])

    browser.close.assert_called_once()


@pytest.fixture
def recovering_cloud(app_settings, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    app_settings.wechat = replace(app_settings.wechat, draft_only=False)
    state = {"schema": 1, "runs": [{"run_key": "old:1", "mode": "draft", "started_at": now,
        "outcome": "draft", "draft_delivery": {"status": "saved", "app_msg_id": "saved-id",
            "handoff": {"account": "小椰科技", "title": "Saved article", "body_hash": "bound-hash", "saved_at": now}}}]}
    writes = []
    monkeypatch.setenv("GITHUB_RUN_ID", "new")
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "cloud")
    monkeypatch.setenv("WECHAT_DELIVERY_MODE", "publish")
    monkeypatch.setenv("WECHAT_SESSION_REFRESH", "0")
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a, **kw: None)
    monkeypatch.setattr(cloud, "restore_session", lambda *a, **kw: None)
    monkeypatch.setattr(cloud, "save_session", lambda *a, **kw: None)
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw:
        deepcopy(state) if kw.get("path") == cloud.metrics.METRICS_PATH else {})
    monkeypatch.setattr(cloud, "write_state_file", lambda value, path, message:
        writes.append((path, deepcopy(value))))
    return app_settings, state, writes


def test_login_recovery_check_is_independent_of_historical_draft_verification(recovering_cloud, monkeypatch):
    settings, state, writes = recovering_cloud
    monkeypatch.setattr(cloud, "verify_pending", lambda *a, **kw:
        pytest.fail("A login recovery check must not reconcile old publications"))

    def check_only(command, **kw):
        assert "--check-login" in command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cloud.subprocess, "run", check_only)

    assert cloud.main(["--mode", "check"]) == 0
    result = json.loads((settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "ready"
    assert result["publication_requested"] is False
    original = next(row for path, data in writes if path == cloud.metrics.METRICS_PATH
        for row in data["runs"] if row["run_key"] == "old:1")
    assert original == state["runs"][0]


@pytest.mark.parametrize("login_expired", [True, False])
def test_reconciliation_failure_reports_phase_without_clearing_old_state(recovering_cloud, monkeypatch, login_expired):
    settings, state, writes = recovering_cloud
    error = (PlatformAuthError("WeChat login expired", blocking_platforms=["wechat"])
        if login_expired else PlaywrightTimeoutError("Page timed out at https://mp.weixin.qq.com/?token=private"))

    def unavailable(*a, **kw):
        raise error

    monkeypatch.setattr(cloud, "verify_pending", unavailable)
    monkeypatch.setattr(cloud.subprocess, "run", lambda *a, **kw:
        pytest.fail("No generation or submission after unavailable reconciliation"))

    assert cloud.main(["--mode", "publish"]) == (2 if login_expired else 1)
    result = json.loads((settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert result["status"] == ("publish_login_required" if login_expired else "error")
    assert result["failed_phase"] == "publication_reconciliation"
    assert result["publication_attempted"] is False
    assert "private" not in result["error_message"]
    original = next(row for path, data in writes if path == cloud.metrics.METRICS_PATH
        for row in data["runs"] if row["run_key"] == "old:1")
    assert original == state["runs"][0]
