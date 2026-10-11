from types import SimpleNamespace
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


def test_late_upload_or_autosave_is_not_a_micro_publish_receipt():
    publication = {"url": "https://mp.toutiao.com/mp/agw/weitoutiao/publish", "method": "POST"}
    events = [publication, {"url": "https://mp.toutiao.com/mp/agw/image/upload", "method": "POST"},
              {"url": "https://mp.toutiao.com/mp/agw/draft/create", "method": "POST"}]
    assert PublishService._latest_weitoutiao_publish_event(events, start_index=0) is publication
    assert PublishService._latest_weitoutiao_publish_event(events, start_index=1) is None


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
