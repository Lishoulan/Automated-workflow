import time
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.exceptions import AIRequestError
from toutiao_content_factory.services.openai_service import OpenAIContentService


def service(settings, logger, wechat):
    if wechat:
        settings.platform = "wechat"
        object.__setattr__(settings.wechat, "enabled", True)
        object.__setattr__(settings.wechat, "auto_publish", True)
    result = OpenAIContentService(settings, logger, force_mock=True)
    result.client = MagicMock()
    result.client.chat.completions.create.return_value = '{"sections": []}'
    result.disable_response_format = False
    return result


@pytest.mark.parametrize("wechat", [True, False])
def test_structured_requests_apply_existing_limits_only_for_wechat(app_settings, logger, monkeypatch, wechat):
    model = service(app_settings, logger, wechat)
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + 30))
    assert model.create_json_completion("system", "user") == {"sections": []}
    kwargs = model.client.chat.completions.create.call_args.kwargs
    assert kwargs['model'] == model.model and kwargs['response_format'] == {'type': 'json_object'}
    if wechat:
        assert kwargs['max_tokens'] == model.DEFAULT_MAX_TOKENS
        assert 0 < kwargs['timeout'] <= 30
    else:
        assert 'max_tokens' not in kwargs and 'timeout' not in kwargs


def test_expired_structured_request_never_contacts_provider(app_settings, logger, monkeypatch):
    model = service(app_settings, logger, True)
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() - 1))
    with pytest.raises(AIRequestError, match="deadline"):
        model.create_json_completion("system", "user")
    model.client.chat.completions.create.assert_not_called()


def test_structured_format_fallback_retains_limits_and_response_validation(app_settings, logger, monkeypatch):
    model = service(app_settings, logger, True)
    model.client.chat.completions.create.side_effect = [RuntimeError('unsupported format'), '{"sections": []}']
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + 30))
    assert model.create_json_completion("system", "user") == {"sections": []}
    assert model.client.chat.completions.create.call_count == 2
    for call in model.client.chat.completions.create.call_args_list:
        assert call.kwargs['max_tokens'] == model.DEFAULT_MAX_TOKENS
        assert 0 < call.kwargs['timeout'] <= 30
