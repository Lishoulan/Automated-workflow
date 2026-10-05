from __future__ import annotations

import json
import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from scripts import scheduled_wechat_publish as scheduler
from main import _write_daily_run_success_result
from argparse import Namespace
from toutiao_content_factory.services import wechat_service as module
from toutiao_content_factory.services.wechat_service import WechatPublishService


@pytest.fixture
def browser_service(app_settings, monkeypatch):
    service = WechatPublishService(app_settings, logging.getLogger("wechat_recovery"))
    browser = MagicMock()
    page = browser.pages[0]
    page.url = "https://mp.weixin.qq.com/cgi-bin/home?token=123"
    monkeypatch.setattr(module, "sync_playwright", MagicMock())
    monkeypatch.setattr(module, "prepare_browser_profile", MagicMock())
    monkeypatch.setattr(module, "run_sync_playwright_safe", lambda fn, **kw: fn())
    monkeypatch.setattr(service, "_launch_persistent_browser", MagicMock(return_value=browser))
    for name in (
        "_ensure_browser_session_ready", "_raise_if_session_expired",
        "_click_publish_button_on_wechat_page", "_wait_for_wechat_publish_success",
        "_capture_browser_debug_artifacts",
    ):
        monkeypatch.setattr(service, name, MagicMock())
    monkeypatch.setattr(service, "_browser_create_draft", MagicMock(return_value="987"))
    monkeypatch.setattr(service, "_browser_create_draft_via_editor", MagicMock(return_value=""))
    monkeypatch.setattr(service, "_confirm_wechat_publish_dialog", MagicMock(return_value=False))
    return service, browser, page


@pytest.mark.parametrize("submit", [True, False])
def test_browser_draft_and_submission_are_distinct(browser_service, tmp_path, submit):
    service, browser, page = browser_service
    result = service._publish_via_browser_only(
        title="Same title", summary="summary", article_html="<p>Reviewed article</p>",
        cover_path=tmp_path / "cover.png", submit=submit,
    )
    assert result["status"] == ("submitted" if submit else "draft_saved")
    assert service._click_publish_button_on_wechat_page.call_count == int(submit)
    assert service._wait_for_wechat_publish_success.call_count == int(submit)
    if submit:
        assert "appmsgid=987" in page.goto.call_args.args[0]
    browser.close.assert_called_once()


def test_unconfirmed_publish_does_not_return_success(browser_service, tmp_path):
    service, browser, _page = browser_service
    service._wait_for_wechat_publish_success.side_effect = RuntimeError("No publish confirmation")
    with pytest.raises(RuntimeError, match="No publish confirmation"):
        service._publish_via_browser_only(
            title="title", summary="", article_html="<p>body</p>", cover_path=tmp_path / "cover.png",
        )
    browser.close.assert_called_once()


def test_missing_draft_id_does_not_publish(browser_service, tmp_path):
    service, browser, _page = browser_service
    service._browser_create_draft.return_value = ""
    with pytest.raises(RuntimeError, match="did not return an appMsgId"):
        service._publish_via_browser_only(
            title="title", summary="", article_html="<p>body</p>", cover_path=tmp_path / "cover.png",
        )
    service._click_publish_button_on_wechat_page.assert_not_called()
    browser.close.assert_called_once()


@pytest.mark.parametrize("saved", [True, False])
def test_manual_qr_login_must_save_a_recoverable_session(browser_service, monkeypatch, saved):
    service, browser, _page = browser_service
    monkeypatch.setattr(module, "ensure_browser_session_ready", lambda **kw: {"session_ready": False})
    monkeypatch.setattr(service, "_browser_session_ready", lambda page: True)
    backup = MagicMock(return_value={"saved": saved})
    monkeypatch.setattr(module, "backup_browser_storage_state", backup)
    if saved:
        assert service.initialize_login_session(headless=True)["state"] == "ready"
    else:
        with pytest.raises(RuntimeError, match="backup could not be saved"):
            service.initialize_login_session(headless=True)
        assert not (service.settings.automation_dir / "wechat_publish_preflight.json").exists()
    backup.assert_called_once()
    assert backup.call_args.kwargs["account_runtime_dir"] == service.settings.account_runtime_dir
    browser.close.assert_called_once()


def test_publish_preflight_allows_backup_restore_before_expiry_error(app_settings, monkeypatch):
    service = WechatPublishService(app_settings, logging.getLogger("wechat_restore"))
    guard = MagicMock(return_value={"session_ready": True, "login_detected": False, "healed": True})
    monkeypatch.setattr(module, "ensure_browser_session_ready", guard)
    result = service._ensure_browser_session_ready(MagicMock(), MagicMock(), app_settings.wechat.base_url)
    assert result["healed"] is True
    assert "navigate_fn" not in guard.call_args.kwargs


@pytest.mark.parametrize("url, ready", [
    ("https://mp.weixin.qq.com/", False),
    ("https://mp.weixin.qq.com/cgi-bin/home?token=123", True),
    ("https://example.com/cgi-bin/home?token=123", False),
])
def test_backend_host_alone_is_not_a_login(app_settings, monkeypatch, url, ready):
    service = WechatPublishService(app_settings, logging.getLogger("wechat_login_test"))
    monkeypatch.setattr(service, "_is_login_page", lambda page: False)
    page = MagicMock(url=url)
    page.locator.return_value.inner_text.return_value = ""
    assert service._browser_session_ready(page) is ready


def test_api_draft_submit_reaches_real_submission(app_settings, monkeypatch):
    service = WechatPublishService(app_settings, logging.getLogger("wechat_api_test"))
    original = MagicMock(return_value={"status": "submitted"})
    monkeypatch.setattr(service, "_submit_draft_via_browser_original", original)
    assert service._submit_draft_via_browser(draft_media_id="media-id", title="title")["status"] == "submitted"
    original.assert_called_once_with(draft_media_id="media-id", title="title")


@pytest.mark.parametrize("preflight_status, exit_code", [("publish_login_required", 2), ("error", 1)])
def test_scheduler_blocks_before_generation(app_settings, monkeypatch, preflight_status, exit_code):
    monkeypatch.setattr(scheduler, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(scheduler, "check_login", lambda settings: {"status": preflight_status})
    child = MagicMock()
    monkeypatch.setattr(scheduler.subprocess, "run", child)
    assert scheduler.main(["--enable-auto-publish"]) == exit_code
    child.assert_not_called()
    report = json.loads((app_settings.automation_dir / "wechat_scheduled_result.json").read_text())
    assert report["status"] == preflight_status


def test_scheduler_propagates_failed_workflow(app_settings, monkeypatch):
    monkeypatch.setattr(scheduler, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(scheduler, "check_login", lambda settings: {"status": "ready"})
    child = MagicMock(return_value=MagicMock(returncode=1))
    monkeypatch.setattr(scheduler.subprocess, "run", child)
    assert scheduler.main(["--enable-auto-publish"]) == 1
    env = child.call_args.kwargs["env"]
    assert env["WECHAT_AUTO_PUBLISH__DEFAULT"] == "1"
    assert env["TOUTIAO_PUBLISH_ENABLED__DEFAULT"] == "0"
    assert env["TARGET_PLATFORM__DEFAULT"] == "wechat"


def test_login_probe_preserves_the_last_publication_result(app_settings, monkeypatch):
    monkeypatch.setattr(scheduler, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(scheduler, "check_login", lambda settings: {"status": "ready"})
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    result_path = app_settings.automation_dir / "wechat_scheduled_result.json"
    result_path.write_text('{"status":"review_required","exit_code":3}', encoding="utf-8")
    child = MagicMock()
    monkeypatch.setattr(scheduler.subprocess, "run", child)
    assert scheduler.main(["--check-login"]) == 0
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "review_required"
    assert json.loads((app_settings.automation_dir / "wechat_login_check.json").read_text(encoding="utf-8"))["status"] == "ready"
    child.assert_not_called()


@pytest.mark.parametrize("status, expected_code", [("draft_saved", 3), ("submitted", 0)])
def test_scheduler_uses_current_publish_outcome_not_process_success(app_settings, monkeypatch, status, expected_code):
    monkeypatch.setattr(scheduler, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(scheduler, "check_login", lambda settings: {"status": "ready"})
    def run(*args, **kwargs):
        (app_settings.automation_dir / "last_daily_run_result.json").write_text(
            json.dumps({"status": "success", "wechat_status": status}), encoding="utf-8",
        )
        return MagicMock(returncode=0)
    monkeypatch.setattr(scheduler.subprocess, "run", run)
    assert scheduler.main(["--enable-auto-publish"]) == expected_code


def test_wechat_draft_is_not_a_published_daily_run(app_settings, monkeypatch):
    import main as entry
    monkeypatch.setattr(entry, "_build_daily_run_summary", lambda *a: {"wechat_status": "draft_saved"})
    status, _message = _write_daily_run_success_result(app_settings, Namespace(daily_run=True), {})
    assert status == "draft_only"
    scheduled = json.loads((app_settings.automation_dir / "scheduled_status.json").read_text(encoding="utf-8"))
    assert scheduled["platforms"]["wechat"]["status"] == "draft_only"


@pytest.mark.parametrize("publish_status, expected", [("submitted", "already_published"), ("draft_saved", "blocked")])
def test_daily_wechat_limit_uses_submitted_wechat_history(app_settings, monkeypatch, publish_status, expected):
    from toutiao_content_factory.services.app_service import ContentFactoryAppService
    service = ContentFactoryAppService(app_settings, logging.getLogger("wechat_daily_limit"))
    object.__setattr__(app_settings.wechat, "enabled", True)
    object.__setattr__(app_settings.publishing, "enabled", False)
    app_settings.output_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.output_dir / app_settings.wechat.history_filename).write_text(json.dumps([
        {"platform": "wechat", "published_at": datetime.now().isoformat(), "status": publish_status},
    ]), encoding="utf-8")
    monkeypatch.setattr(service, "_local_fallback_window_block", lambda: None)
    cloud = MagicMock(return_value=None)
    monkeypatch.setattr(service, "_cloud_published_today", cloud)
    monkeypatch.setattr(service.platform_health_service, "quick_health_check", lambda: {"blocking_platforms": ["wechat"]})
    topup = MagicMock()
    monkeypatch.setattr(service, "_top_up_standalone_weitoutiao", topup)
    result = service._daily_run_impl()
    assert result["status"] == expected
    cloud.assert_called_once_with("wechat")
    topup.assert_not_called()


@pytest.mark.parametrize("hour, expected_day", [(6, 5), (7, 6), (18, 6)])
def test_daemon_schedules_once_at_seven_without_immediate_publish(hour, expected_day):
    from scripts.wechat_publish_daemon import next_publish_time
    scheduled = next_publish_time(datetime(2026, 10, 5, hour, 30))
    assert scheduled == datetime(2026, 10, expected_day, 7, 0)
