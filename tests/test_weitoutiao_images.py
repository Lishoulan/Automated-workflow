from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import MagicMock
from pathlib import Path
from urllib.parse import urlencode
import json

import pytest

from toutiao_content_factory.services.publish_service import PublishService


URI = "tos-cn-i-test/verified-image"


@pytest.mark.parametrize("encoded", [False, True])
def test_micro_publish_requires_matching_persistent_image(encoded):
    fields = {"content": "正文", "image_list": [{"uri": URI}]}
    data = urlencode({"content": fields["content"], "image_list": json.dumps(fields["image_list"])}) if encoded else json.dumps(fields)
    images = {"inserted_count": 1, "references": ["https://cdn.test/" + URI + "~preview?size=800"]}
    assert PublishService._weitoutiao_image_request_matches({"post_data": data}, images)
    for wrong in ({"content": URI}, {"image_list": [{"uri": "blob:preview"}]}, {"image_list": [{"uri": "tos-cn-i-test/other"}]}):
        assert not PublishService._weitoutiao_image_request_matches({"post_data": json.dumps(wrong)}, images)
    assert not PublishService._weitoutiao_image_request_matches(None, images)
    assert PublishService._weitoutiao_image_request_matches(None, {"inserted_count": 0})
    assert not PublishService._weitoutiao_image_request_matches({"post_data": data}, {"inserted_count": 0})
    assert not PublishService._weitoutiao_image_request_matches({"post_data": json.dumps({"image_list": [{"uri": URI}, {"uri": "tos-cn-i-test/extra"}]})}, images)


@pytest.mark.parametrize("path", ["/mp/agw/weitoutiao/publish", "/mp/agw/article/wtt"])
def test_late_upload_or_autosave_is_not_a_micro_publish_receipt(path):
    publication = {"url": "https://mp.toutiao.com" + path, "method": "POST"}
    events = [publication, {"url": "https://mp.toutiao.com/mp/agw/image/upload", "method": "POST"},
              {"url": "https://mp.toutiao.com/mp/agw/draft/create", "method": "POST"}]
    assert PublishService._latest_weitoutiao_publish_event(events, start_index=0) is publication
    assert PublishService._latest_weitoutiao_publish_event(events, start_index=1) is None
    assert PublishService._latest_weitoutiao_publish_event(
        [{"url": publication["url"], "method": "GET"}], start_index=0) is None


class UploadPage:
    def __init__(self, mode):
        self.mode = mode
        self.sent = False
        self.listeners = {}
        self.emitted = False

    def evaluate(self, script):
        return {"count": int(self.mode == "existing" or (self.sent and self.mode == "success")),
                "loading": self.sent and self.mode == "timeout", "error": self.sent and self.mode == "error"}

    def locator(self, selector):
        page = self
        class Locator:
            @property
            def first(self): return self
            def count(self): return int("input" in selector and page.mode != "missing")
            def set_input_files(self, path, timeout):
                page.sent = True
                if page.mode == "transfer_error": raise RuntimeError("transfer failed")
        return Locator()

    def on(self, name, callback): self.listeners[name] = callback
    def remove_listener(self, name, callback): self.listeners.pop(name)
    def wait_for_timeout(self, ms):
        if self.mode == "success" and not self.emitted:
            self.emitted = True
            self.listeners["response"](SimpleNamespace(request=SimpleNamespace(method="POST"),
                url="https://mp.toutiao.com/upload/image", status=200,
                json=lambda: {"code": 0, "data": {"uri": URI}}))


def test_micro_upload_has_server_receipt_and_one_preview(app_settings, logger, tmp_path):
    page = UploadPage("success")
    result = PublishService(app_settings, logger)._try_apply_weitoutiao_image(page, tmp_path / "image.png", [])
    assert result["inserted_count"] == 1 and result["references"] == [URI]
    assert result["status"] == "uploaded" and result["request_verified"] is False
    assert not page.listeners


@pytest.mark.parametrize("mode", ["existing", "timeout", "error", "transfer_error"])
def test_unresolved_micro_attachment_stops_before_publication(app_settings, logger, tmp_path, mode):
    page = UploadPage(mode)
    with pytest.raises(RuntimeError):
        PublishService(app_settings, logger)._try_apply_weitoutiao_image(page, tmp_path / "image.png", [])
    assert not page.listeners


def test_missing_micro_upload_control_allows_clean_text_fallback(app_settings, logger, tmp_path):
    page = UploadPage("missing")
    result = PublishService(app_settings, logger)._try_apply_weitoutiao_image(page, tmp_path / "image.png", [])
    assert result["inserted_count"] == 0 and not page.sent and not page.listeners


@pytest.mark.parametrize("matching", [True, False])
@pytest.mark.parametrize("override", [True, False])
def test_micro_publish_image_evidence_controls_success_history(app_settings, logger, tmp_path, monkeypatch, matching, override):
    from toutiao_content_factory.services import publish_service as module
    app_settings.publishing = replace(app_settings.publishing, weitoutiao_enabled=True)
    content = "\n\n".join((Path(__file__).parent / "fixtures/toutiao_body_image_article.md").read_text(encoding="utf-8").split("\n\n")[:4])
    publish = tmp_path / app_settings.review.publish_dirname
    publish.mkdir()
    (publish / "article.md").write_text(content, encoding="utf-8")
    (publish / "publish_meta.json").write_text(json.dumps({"content_experiment": {"protocol_version": 4}}), encoding="utf-8")
    service = PublishService(app_settings, logger)
    page, browser = MagicMock(), MagicMock()
    page.url = "https://mp.toutiao.com/profile_v4/weitoutiao/publish"
    page.locator.return_value.first.inner_text.return_value = content
    browser.pages = [page]
    monkeypatch.setattr(module, "sync_playwright", MagicMock())
    monkeypatch.setattr(module, "prepare_browser_profile", lambda **kw: None)
    monkeypatch.setattr(module, "run_sync_playwright_safe", lambda fn, **kw: fn())
    monkeypatch.setattr(module.ImageGenerationService, "generate_weitoutiao_image", lambda self, run, text: tmp_path / "card.png")
    monkeypatch.setattr(service, "_launch_persistent_browser", lambda *a, **kw: browser)
    for name in ("_ensure_browser_session_ready", "_goto_publish_page", "_check_toutiao_login", "_dismiss_ai_assistant_drawer", "_set_editor_content"):
        monkeypatch.setattr(service, name, lambda *a, **kw: None)
    monkeypatch.setattr(service, "_resolve_editor_selector", lambda *a: "#editor")
    monkeypatch.setattr(service, "_retry_playwright_action", lambda fn, **kw: fn())
    monkeypatch.setattr(service, "_make_popup_guard", lambda *a: MagicMock())
    monkeypatch.setattr(service, "_wait_for_any", lambda *a: None)
    monkeypatch.setattr(service, "_try_apply_weitoutiao_image", lambda *a: {"inserted_count": 1, "references": [URI], "status": "uploaded"})
    events = []
    monkeypatch.setattr(service, "_install_publish_event_capture", lambda p, bucket, **kw: events.append(bucket))
    def click(*a):
        fields = {"content": content, "image_list": [{"uri": URI}] if matching else []}
        events[0].append({"url": "https://mp.toutiao.com/mp/agw/weitoutiao/publish", "method": "POST",
                         "post_data": json.dumps(fields), "response_json": {"code": 0}, "status": 200})
        return True
    monkeypatch.setattr(service, "_click_immediate_publish_button", click)
    monkeypatch.setattr(service, "_load_history", lambda: [])
    saved = []
    monkeypatch.setattr(service, "_save_history", lambda records: saved.extend(records))
    result = service.publish_weitoutiao(tmp_path, content_override=content if override else None)
    expected = service._sanitize_article_for_publish(content, preserve_sentence_endings=True)
    assert result["submitted_opening"] == expected.split("\n\n", 1)[0]
    assert result["status"] == ("submitted_for_review" if matching else "publish_uncertain")
    assert result["images"]["request_verified"] is matching
    assert len(saved) == 1 and saved[0].status == result["status"]
    browser.close.assert_called_once()
