from __future__ import annotations

import pytest

from toutiao_content_factory.services.publish_service import PublishService


def test_weitoutiao_publish_service_blocks_html_article_before_browser(app_settings, logger) -> None:
    service = PublishService(app_settings, logger)

    with pytest.raises(RuntimeError, match="HTML document"):
        service._sanitize_article_for_publish(
            "<!doctype html><html><body>gateway page</body></html>"
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": "<!doctype html><html>gateway</html>"},
        {"err_no": 0, "data": {"body": "<div class='error-page'>gateway</div>"}},
    ],
)
def test_html_publish_response_is_not_success(payload, app_settings, logger) -> None:
    service = PublishService(app_settings, logger)

    assert service._publish_response_is_success(payload) is False


def test_valid_weitoutiao_publish_response_remains_success(app_settings, logger) -> None:
    service = PublishService(app_settings, logger)

    assert service._publish_response_is_success(
        {"code": 0, "err_no": 0, "message": "提交成功", "data": {"id": "123"}}
    ) is True
