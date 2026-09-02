from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.exceptions import AIRequestError, AIResponseFormatError
from toutiao_content_factory.services.openai_service import OpenAIContentService


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("plain proxy response", "plain proxy response"),
        (
            {"choices": [{"message": {"content": "dictionary response"}}]},
            "dictionary response",
        ),
        (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="SDK response"))]
            ),
            "SDK response",
        ),
        (
            {"choices": [{"message": {"content": [{"type": "text", "text": "content block"}]}}]},
            "content block",
        ),
        (
            {"response": {"data": {"choices": [{"message": {"content": "nested response"}}]}}},
            "nested response",
        ),
    ],
)
def test_text_completion_accepts_proxy_and_sdk_response_shapes(
    app_settings,
    logger,
    response,
    expected: str,
) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.return_value = response
    service.client = client

    assert service.create_text_completion("system", "user") == expected


@pytest.mark.parametrize(
    "response",
    [
        "<!doctype html><html><body>gateway page</body></html>",
        "<html lang='zh-CN'><body>login page</body></html>",
        "<div class='error-page'>gateway page</div>",
    ],
)
def test_text_completion_rejects_html_page_response(app_settings, logger, response) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.return_value = response
    service.client = client

    with pytest.raises(AIResponseFormatError, match="HTML document"):
        service.create_text_completion("system", "user")


def test_json_completion_accepts_plain_string_from_proxy(app_settings, logger) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.return_value = '{"result": "proxy"}'
    service.client = client

    assert service.create_json_completion("system", "user") == {"result": "proxy"}


def test_json_completion_accepts_dictionary_response_and_usage(app_settings, logger) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.return_value = {
        "choices": [{"message": {"content": '{"result": "dictionary"}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    service.client = client

    assert service.create_json_completion("system", "user") == {"result": "dictionary"}
    usage = service.get_usage_snapshot()
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 15


@pytest.mark.parametrize(
    "response",
    [
        '```json\n{"result": "fenced"}\n```',
        'Here is the JSON payload:\n{"result": "explained"}\nEnd.',
        '<think>先分析返回格式 {not_json}</think>\n{"result": "after think"}',
        '{"result": "brace } inside string", "nested": {"ok": true}} trailing text',
    ],
)
def test_json_completion_extracts_wrapped_and_reasoned_json(app_settings, logger, response) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.return_value = response
    service.client = client

    payload = service.create_json_completion("system", "user")

    assert payload["result"] in {"fenced", "explained", "after think", "brace } inside string"}


def test_json_completion_rejects_response_without_a_json_object(app_settings, logger) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.return_value = "模型只返回了无法解析的说明"
    service.client = client

    with pytest.raises(AIResponseFormatError, match="valid JSON"):
        service.create_json_completion("system", "user")


def test_insufficient_balance_disables_client_for_remainder_of_run(
    app_settings,
    logger,
) -> None:
    service = OpenAIContentService(app_settings, logger, force_mock=True)
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError(
        "Error code: 402 - account balance is insufficient"
    )
    service.client = client

    with pytest.raises(AIRequestError, match="balance is insufficient"):
        service.create_json_completion("system", "user")

    assert service.available is False
    assert service.unavailable_reason
    assert client.chat.completions.create.call_count == 1

    with pytest.raises(AIRequestError, match="not available"):
        service.create_text_completion("system", "user")
    assert client.chat.completions.create.call_count == 1
