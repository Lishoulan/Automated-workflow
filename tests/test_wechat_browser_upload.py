from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.services.wechat_service import WechatPublishService


@pytest.fixture
def service(app_settings):
    return WechatPublishService(app_settings, logging.getLogger("browser_upload_test"))


@pytest.mark.parametrize("result,expected", [
    ({"base_resp": {"ret": 0}, "content": "123", "cdn_url": "https://cdn/image"}, ("123", "https://cdn/image")),
    ({"base_resp": {"ret": "0"}, "content": 123, "cdn_mid_url": "https://cdn/image"}, ("123", "https://cdn/image")),
    ({"base_resp": {"ret": 200002}}, ("", "")),
    ({"base_resp": {"ret": 0}, "content": None, "cdn_url": None}, ("", "")),
    ({"base_resp": {"ret": "invalid"}}, ("", "")),
    ({"base_resp": []}, ("", "")),
    ([{"base_resp": {"ret": 0}}], ("", "")),
])
def test_upload_requires_platform_confirmation(service, tmp_path, result, expected):
    image = tmp_path / "cover.png"
    image.write_bytes(b"image data")
    page = MagicMock(url="https://mp.weixin.qq.com/cgi-bin/home?token=123")
    page.evaluate.return_value = result
    assert service._upload_image_via_http(page, "123", image) == expected
    page.context.cookies.assert_not_called()


def test_upload_failure_neither_retries_nor_logs_credentials(service, tmp_path, caplog):
    image = tmp_path / "cover.png"
    image.write_bytes(b"image data")
    page = MagicMock(url="https://mp.weixin.qq.com/cgi-bin/home?token=123")
    page.evaluate.side_effect = RuntimeError("private-token cookie-secret")
    with caplog.at_level(logging.WARNING):
        assert service._upload_image_via_http(page, "123", image) == ("", "")
    assert page.evaluate.call_count == 1
    assert "private-token" not in caplog.text and "cookie-secret" not in caplog.text


@pytest.mark.parametrize("url,token", [
    ("https://example.com", "123"), ("https://mp.weixin.qq.com", ""),
    ("http://mp.weixin.qq.com", "123"), ("https://mp.weixin.qq.com:8443", "123"),
])
def test_upload_rejects_other_origins_and_missing_login(service, tmp_path, url, token):
    page = MagicMock(url=url)
    with pytest.raises(RuntimeError, match="authenticated backend"):
        service._upload_image_via_http(page, token, tmp_path / "absent.png")
    page.evaluate.assert_not_called()


@pytest.mark.parametrize("method", ["_browser_create_draft", "_browser_create_draft_via_editor"])
def test_editor_must_be_ready_before_upload_or_save(service, tmp_path, method):
    page = MagicMock()
    page.wait_for_function.side_effect = TimeoutError("Editor unavailable")
    with pytest.raises(TimeoutError):
        getattr(service, method)(page, "123", "title", "summary", "<p>body</p>", tmp_path / "cover.png")
    assert page.goto.call_args.kwargs["wait_until"] == "commit"
    page.evaluate.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Live browser transport check uses installed Edge")
def test_browser_transport_uploads_real_multipart_without_external_network(service, tmp_path):
    from playwright.sync_api import sync_playwright

    image = tmp_path / "cover.png"
    image.write_bytes(b"unique image bytes")
    requests = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "session", "value": "private", "url": "https://mp.weixin.qq.com"}])
            context.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html><body></body></html>"))
            page = context.new_page()
            page.goto("https://mp.weixin.qq.com/home")

            def upload(route):
                requests.append(route.request)
                route.fulfill(content_type="application/json", body='{"base_resp":{"ret":0},"content":"123","cdn_url":"https://cdn/image"}')

            page.route("**/cgi-bin/filetransfer?*", upload)
            assert service._upload_image_via_http(page, "123", image) == ("123", "https://cdn/image")
            assert len(requests) == 1
            request = requests[0]
            assert request.method == "POST"
            assert b"unique image bytes" in request.post_data_buffer
            assert 'filename="cover.png"' in request.post_data
            assert request.headers["cookie"] == "session=private"
        finally:
            browser.close()
