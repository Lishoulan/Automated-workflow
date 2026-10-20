"""Repeated cloud lifecycle faults; all providers, accounts and state are isolated."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor

import httpx
import openai
import pytest

from scripts import wechat_cloud_run as cloud
from scripts import publication_watchdog as watchdog
from scripts.wechat_cloud_session import encrypt_state, decrypt_state
from cryptography.fernet import Fernet
from toutiao_content_factory.exceptions import AIRequestError, AIResponseFormatError
from toutiao_content_factory.services import notification_service as notifications
from toutiao_content_factory.services import wechat_retry as retry, wechat_metrics as metrics
from toutiao_content_factory.services import wechat_execution as execution
from toutiao_content_factory.services import openai_service as model_module
from toutiao_content_factory.services import wechat_cloud_cover as cover

pytestmark = pytest.mark.skipif(os.environ.get("RUN_PUBLICATION_STRESS") != "1", reason="opt-in fault stress")
SCALE = max(1, min(5, int(os.environ.get("PUBLICATION_STRESS_SCALE", "1"))))
NOW = datetime(2026, 10, 11, 10, tzinfo=notifications.NOTIFICATION_TIMEZONE)


@pytest.mark.parametrize("fault", ["unauthorized", "balance", "rate_limit", "server_error", "html", "timeout"])
def test_model_fault_bursts_have_one_attempt_and_cancel(app_settings, logger, monkeypatch, record_property, fault):
    calls, cancellations = [], []
    async def transport(request):
        calls.append(request)
        if fault == "timeout":
            try:
                await asyncio.sleep(10)
            finally:
                cancellations.append(True)
        if fault == "html":
            return httpx.Response(200, json={"id": "mock", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "<html>maintenance</html>"}}]})
        status = {"unauthorized": 401, "balance": 402, "rate_limit": 429, "server_error": 503}[fault]
        return httpx.Response(status, json={"error": {"message": "isolated provider fault", "type": "error"}})
    original = openai.AsyncOpenAI
    def client(**kwargs):
        assert kwargs["max_retries"] == 0
        return original(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(model_module, "AsyncOpenAI", client)
    for _ in range(8 * SCALE):
        model = model_module.OpenAIContentService(app_settings, logger, force_mock=True)
        model.client = SimpleNamespace(api_key="isolated", base_url="https://model.test/v1")
        with pytest.raises((AIRequestError, AIResponseFormatError)):
            model.create_json_completion("audit", "facts", single_attempt=True,
                                         timeout_seconds=.05 if fault == "timeout" else 2)
    assert len(calls) == 8 * SCALE
    assert len(cancellations) == (8 * SCALE if fault == "timeout" else 0)
    record_property("injected_model_fault", fault)
    record_property("mock_provider_calls", len(calls))
    record_property("cancelled_inflight_calls", len(cancellations))


@pytest.mark.parametrize("fault", [401, 402, 429, 503, "timeout", "malformed", "blank"])
def test_image_fault_bursts_do_not_retry_or_create_cover(tmp_path, monkeypatch, record_property, fault):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "isolated")
    monkeypatch.delenv("WECHAT_RUN_DEADLINE_EPOCH", raising=False)
    calls = []
    def transport(request):
        calls.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("isolated", request=request)
        if fault == "malformed":
            return httpx.Response(200, content=b"<html>maintenance</html>")
        if fault == "blank":
            return httpx.Response(200, json={"success": True, "result": {"image": ""}})
        return httpx.Response(fault, json={"success": False})
    original = httpx.Client
    monkeypatch.setattr(cover.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)))
    for index in range(8 * SCALE):
        output = tmp_path / str(index) / "cover.png"
        with pytest.raises((RuntimeError, ValueError, OSError, httpx.HTTPError)):
            cover.generate_cloud_cover("Verified article topic", output)
        assert not output.exists()
    assert len(calls) == 8 * SCALE
    record_property("injected_image_fault", str(fault))
    record_property("mock_provider_calls", len(calls))


def cloud_state(settings, monkeypatch):
    """Use cloud entrypoint and disk-backed fake GitHub state across fresh runs."""
    settings.wechat = replace(settings.wechat, enabled=True, auto_publish=True, draft_only=True)
    settings.platform = "wechat"
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "cloud")
    monkeypatch.setenv("WECHAT_DELIVERY_MODE", "draft")
    monkeypatch.setenv("WECHAT_SESSION_REFRESH", "0")
    # delenv cannot register an absent key. Own it before simulating a hard
    # cancellation, which exits before the worker can restore its run ID.
    monkeypatch.setenv("WECHAT_EXECUTION_ID", "")
    for key in ("WECHAT_EXECUTION_ID", "WECHAT_RUN_DEADLINE_EPOCH", "WECHAT_GENERATION_DEADLINE_EPOCH",
                "WECHAT_RETRY_PARENT", "WECHAT_RETRY_DAY", "WECHAT_VALIDATION_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a, **kw: None)
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: settings)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "save_session", MagicMock())
    directory = settings.output_dir / "fake-remote"
    directory.mkdir(parents=True)
    def reader(*a, **kw):
        path = directory / Path(kw.get("path", "guard.json")).name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    def writer(state, path, message):
        (directory / Path(path).name).write_text(json.dumps(state))
    monkeypatch.setattr(cloud.xstate, "read_remote_state", reader)
    monkeypatch.setattr(cloud, "write_state_file", writer)
    monkeypatch.setattr(cloud, "verify_pending", lambda *a: pytest.fail("Drafts cannot submit or reconcile publications"))
    monkeypatch.setattr(cloud, "record_publication", lambda *a: pytest.fail("Drafts cannot publish"))
    monkeypatch.setattr(cloud.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0))
    return directory, reader, writer


@pytest.mark.parametrize("fault", ["login", "preparing_timeout", "lost_receipt", "hard_cancel", "metrics_write"])
def test_cloud_restart_faults_preserve_delivery_guards(app_settings, monkeypatch, record_property, fault):
    directory, reader, writer = cloud_state(app_settings, monkeypatch)
    attempts = []
    for index in range(10 * SCALE):
        for path in directory.glob("*.json"):
            path.unlink()  # Independent isolated scenario, never production state.
        monkeypatch.setenv("GITHUB_RUN_ID", str(10000 + index * 2))
        monkeypatch.setattr(cloud, "write_state_file", writer)
        if fault == "login":
            monkeypatch.setattr(cloud.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 2))
        if fault == "metrics_write":
            def fail_metrics(state, path, message):
                if path == metrics.METRICS_PATH:
                    raise OSError("isolated unavailable metrics store")
                writer(state, path, message)
            monkeypatch.setattr(cloud, "write_state_file", fail_metrics)
        def worker(command, **kwargs):
            attempts.append(index)
            assert "--save-draft" in command and "--enable-auto-publish" not in command
            execution.write_phase(app_settings, "preparing" if fault == "preparing_timeout" else "saving_draft")
            if fault == "hard_cancel":
                raise KeyboardInterrupt("isolated runner cancellation")
            if fault in {"preparing_timeout", "lost_receipt"}:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            (app_settings.automation_dir / "wechat_scheduled_result.json").write_text(json.dumps({
                "status": "success", "wechat_status": "draft_saved", "native_exit_code": 0,
                "draft_delivery": {"status": "saved", "app_msg_id": "accepted-" + str(index)}}))
            return subprocess.CompletedProcess(command, 0)
        monkeypatch.setattr(execution, "run_process_tree", worker)
        if fault == "hard_cancel":
            with pytest.raises(KeyboardInterrupt):
                cloud.main(["--mode", "draft"])
        else:
            assert cloud.main(["--mode", "draft"]) != 0
            result = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
            assert result["publication_attempted"] is False and result["execution_host"] == "cloud"
            if fault == "lost_receipt":
                assert result["draft_delivery"]["status"] == "uncertain"
            if fault == "preparing_timeout":
                assert result["draft_delivery"]["status"] == "not_saved"
        if fault in {"lost_receipt", "hard_cancel", "metrics_write"}:
            monkeypatch.setenv("GITHUB_RUN_ID", str(10001 + index * 2))
            monkeypatch.setattr(execution, "run_process_tree", lambda *a, **kw: pytest.fail("Must not retry an accepted or unknown delivery"))
            monkeypatch.setattr(cloud, "write_state_file", writer)
            cloud.main(["--mode", "draft"])
            state = reader(path=retry.STATE_PATH)
            assert len(next(iter(state["days"].values()))["runs"]) == 1
    assert len(attempts) == (0 if fault == "login" else 10 * SCALE)
    record_property("cloud_restart_fault", fault)
    record_property("independent_scenarios", 10 * SCALE)
    record_property("duplicate_delivery_attempts", 0)


@pytest.mark.parametrize("fault", ["state_timeout", "corrupt_retry", "reservation_write"])
def test_unreadable_or_unwritable_remote_guards_prevent_work(app_settings, monkeypatch, record_property, fault):
    directory, reader, writer = cloud_state(app_settings, monkeypatch)
    monkeypatch.setattr(execution, "run_process_tree", lambda *a, **kw: pytest.fail("Unknown remote guard cannot authorize work"))
    for index in range(10 * SCALE):
        monkeypatch.setenv("GITHUB_RUN_ID", str(20000 + index))
        if fault == "state_timeout":
            monkeypatch.setattr(cloud.xstate, "read_remote_state", MagicMock(side_effect=httpx.ReadTimeout("isolated")))
        elif fault == "corrupt_retry":
            (directory / Path(retry.STATE_PATH).name).write_text('{"schema_version":1,"days":[]}')
        else:
            def fail_reservation(state, path, message):
                if path == retry.STATE_PATH:
                    raise OSError("isolated state write failure")
                writer(state, path, message)
            monkeypatch.setattr(cloud, "write_state_file", fail_reservation)
        assert cloud.main(["--mode", "draft"]) == 1
        report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
        assert report["publication_attempted"] is False
    record_property("remote_guard_fault", fault)
    record_property("blocked_before_work", 10 * SCALE)


def test_many_days_budget_and_notification_recovery(tmp_path, monkeypatch, record_property):
    monkeypatch.delenv("NOTIFY_URGENT_QUEUE_ONLY", raising=False)
    state = {}
    channel = MagicMock()
    days = 30 * SCALE
    for index in range(days):
        now = NOW + timedelta(days=index)
        monkeypatch.setattr(notifications, "_notification_now", lambda: now)
        service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path))
        service.channels = [channel]
        channel.send.side_effect = [False, True]
        title = "Login expired"
        service.notify_urgent(title, "isolated failure")
        assert not service._has_pushed_title_today(title)
        service.notify_urgent(title, "recovered channel")
        assert service._has_pushed_title_today(title)
        service.notify_urgent(title, "replay")
        assert channel.send.call_count == (index + 1) * 2
        day = now.date().isoformat()
        for attempt in range(3):
            key = f"{index}:{attempt}"
            state, outcome = retry.reserve(state, day, key, now.isoformat())
            assert outcome == "reserved"
            # Restore serialized state as a fresh runner would.
            state = json.loads(json.dumps(state))
            state = retry.complete(state, day, key, 1400, {"status": "no_topics", "delivery_mode": "draft"}, {})
        blocked, outcome = retry.reserve(state, day, "extra", now.isoformat())
        assert outcome == "daily_budget_exhausted" and blocked == state
        assert len(state["days"]) <= 7
        assert len(watchdog.daily_alerts(now, {}, {}, {})) == 2
    record_property("simulated_days", days)
    record_property("budget_reservations", days * 3)
    record_property("notification_attempts", days * 2)


def test_alert_storm_dedup_quota_and_restarts(tmp_path, monkeypatch, record_property):
    monkeypatch.delenv("NOTIFY_URGENT_QUEUE_ONLY", raising=False)
    monkeypatch.setattr(notifications, "_notification_now", lambda: NOW)
    channel = MagicMock()
    channel.send.return_value = True
    def notify(index):
        service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path))
        service.channels = [channel]
        service.notify_urgent("same expired session", str(index))
    count = 64 * SCALE
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(notify, range(count)))
    assert channel.send.call_count == 1
    service = notifications.NotificationService({}, fallback_log_dir=str(tmp_path))
    service.channels = [channel]
    for index in range(count):
        service.notify_urgent("different incident " + str(index), "isolated")
    assert channel.send.call_count == service.DAILY_SEND_LIMIT - service.DAILY_REPORT_RESERVE
    assert any(row.get("metadata", {}).get("quota_exhausted") for row in service._read_log_entries())
    assert service.send_daily_report(NOW.date())["status"] == "sent"
    assert channel.send.call_count == service.DAILY_SEND_LIMIT
    assert service.send_daily_report(NOW.date())["status"] == "already_sent"
    record_property("concurrent_alert_events", count)
    record_property("distinct_alert_events", count)
    record_property("acknowledged_deliveries", channel.send.call_count)


@pytest.mark.parametrize("fault", [401, 429, 503, "timeout", "business_failure", "malformed"])
def test_notification_transport_failures_are_not_acknowledged(monkeypatch, record_property, fault):
    calls = []
    def transport(request):
        calls.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("isolated", request=request)
        if fault == "business_failure":
            return httpx.Response(200, json={"code": 40001})
        if fault == "malformed":
            return httpx.Response(200, content=b"maintenance")
        return httpx.Response(fault, json={"code": 1})
    original = httpx.Client
    monkeypatch.setattr(notifications.httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(transport)))
    channel = notifications.ServerChanNotifier("isolated-key")
    for _ in range(10 * SCALE):
        assert channel.send("isolated", "no real delivery") is False
    assert len(calls) == 10 * SCALE
    record_property("notification_transport_fault", str(fault))
    record_property("failed_mock_deliveries", len(calls))


def test_corrupt_sessions_and_shared_ledgers_fail_closed(tmp_path, monkeypatch, record_property):
    monkeypatch.setattr(watchdog, "ROOT", tmp_path)
    key = Fernet.generate_key().decode()
    session = encrypt_state({"cookies": [{"name": "login", "value": "isolated",
        "domain": "mp.weixin.qq.com", "path": "/"}], "origins": []}, key, "default")
    for index in range(30 * SCALE):
        mutated = bytearray(session)
        mutated[index % len(mutated)] ^= 1
        with pytest.raises(ValueError):
            decrypt_state(bytes(mutated), key, "default")
        with pytest.raises(ValueError):
            decrypt_state(session, key, "different-account")
    destination = tmp_path / "ledger.jsonl"
    destination.write_text('{"pushed":true}\n')
    before = destination.read_bytes()
    for payload in [b"{", b"null\n", b"[]\n", b'{"pushed":true}\n{']:
        api = MagicMock()
        api.file.return_value = payload
        with pytest.raises(ValueError):
            watchdog.restore_ledger(api, destination)
        assert destination.read_bytes() == before
    record_property("corrupted_or_wrong_account_sessions", 60 * SCALE)
    record_property("corrupt_ledger_payloads", 4)


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "failure"])
def test_failed_or_missing_cloud_runs_always_remain_visible(record_property, conclusion):
    api = MagicMock()
    api.results.return_value = []
    for index in range(100 * SCALE):
        run = {"id": index, "name": "WeChat Daily Run", "head_branch": "main",
            "created_at": (NOW - timedelta(minutes=30)).isoformat(),
            "status": "completed", "conclusion": conclusion, "html_url": "https://github.com/mock/run"}
        assert watchdog.inspect_run(api, run, NOW)
        assert len(watchdog.daily_alerts(NOW, {}, {}, {})) == 2
    record_property("injected_workflow_failures", 100 * SCALE)
    record_property("missing_run_sweeps", 100 * SCALE)


@pytest.mark.parametrize("fault", ["editor_changed", "upload_changed", "missing_confirmation", "login_redirect"])
def test_changed_platform_pages_never_claim_success(app_settings, tmp_path, logger, monkeypatch, record_property, fault):
    from toutiao_content_factory.services import wechat_service as module
    service = module.WechatPublishService(app_settings, logger)
    image = tmp_path / "cover.png"
    image.write_bytes(b"isolated image bytes")
    monkeypatch.setattr(service, "_capture_browser_debug_artifacts", lambda *a: tmp_path)
    for _ in range(100 * SCALE):
        page = MagicMock(url="https://mp.weixin.qq.com/cgi-bin/home?token=isolated")
        page.locator.return_value.first.is_visible.return_value = False
        if fault == "editor_changed":
            page.wait_for_function.side_effect = TimeoutError("Editor selector disappeared")
            with pytest.raises(TimeoutError):
                service._browser_create_draft(page, "isolated", "title", "", "body", image)
            page.evaluate.assert_not_called()
        elif fault == "upload_changed":
            page.evaluate.return_value = {"new_response": {"ok": True}}
            assert service._upload_image_via_http(page, "isolated", image) == ("", "")
            assert page.evaluate.call_count == 1
        elif fault == "login_redirect":
            page.url = "https://mp.weixin.qq.com/"
            page.locator.return_value.first.is_visible.return_value = True
            with pytest.raises(module.SessionExpiredException):
                service._raise_if_session_expired(page)
        else:
            clock = iter([100, 101, 200])
            monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: next(clock)))
            page.get_by_role.return_value.first.is_visible.return_value = False
            page.evaluate.return_value = None
            with pytest.raises(RuntimeError, match="success state"):
                service._wait_for_wechat_publish_success(page)
            page.get_by_role.return_value.first.click.assert_not_called()
    record_property("injected_page_fault", fault)
    record_property("page_fault_scenarios", 100 * SCALE)


@pytest.mark.parametrize("fault", ["missed_hook", "state_unreadable", "archive_corrupt", "delivery_failed", "quota_exhausted"])
def test_watchdog_end_to_end_fault_receipts(tmp_path, monkeypatch, record_property, fault):
    import sys
    monkeypatch.delenv("NOTIFY_URGENT_QUEUE_ONLY", raising=False)
    monkeypatch.setattr(notifications, "_notification_now", lambda: NOW)
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(watchdog, "datetime", FixedDateTime)
    monkeypatch.setattr(watchdog, "ROOT", tmp_path)
    destination = tmp_path / "output/automation/notification_log.jsonl"
    destination.parent.mkdir(parents=True)
    monkeypatch.setattr(watchdog, "restore_ledger", lambda api: None)
    monkeypatch.setattr(watchdog, "save_ledger", lambda api: None)
    event = tmp_path / "event.json"
    event.write_text("{}")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setattr(sys, "argv", ["publication_watchdog.py"])
    api = MagicMock()
    api.request.return_value = {"workflow_runs": []}
    api.state.return_value = {}
    if fault == "state_unreadable":
        api.state.side_effect = RuntimeError("isolated remote state unavailable")
    if fault == "archive_corrupt":
        import zipfile
        api.request.return_value = {"workflow_runs": [{"id": 1, "name": "WeChat Daily Run", "head_branch": "main",
            "created_at": NOW.isoformat(), "status": "completed", "conclusion": "success"}]}
        api.results.side_effect = zipfile.BadZipFile("isolated archive")
    monkeypatch.setattr(watchdog, "GitHub", lambda: api)
    channel = MagicMock()
    channel.send.return_value = fault != "delivery_failed"
    def notifier(*args, **kwargs):
        service = notifications.NotificationService({"NOTIFY_DAILY_LIMIT": 0 if fault == "quota_exhausted" else 5},
            fallback_log_dir=str(destination.parent))
        service.channels = [channel]
        return service
    monkeypatch.setattr(watchdog, "NotificationService", notifier)
    for _ in range(5 * SCALE):
        destination.write_text("")  # Reset only this isolated notification fixture.
        code = watchdog.main()
        report = json.loads((destination.parent / "publication_watchdog_result.json").read_text(encoding="utf-8"))
        assert report["alerts"]
        if fault in {"delivery_failed", "quota_exhausted"}:
            assert code == 1 and all(not row["acknowledged"] for row in report["alerts"])
        else:
            assert all(row["acknowledged"] for row in report["alerts"])
            assert bool(report["inspection_errors"]) == (fault != "missed_hook")
    if fault == "quota_exhausted":
        channel.send.assert_not_called()
    record_property("watchdog_fault", fault)
    record_property("end_to_end_monitor_runs", 5 * SCALE)
