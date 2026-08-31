from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.exceptions import AIRequestError
from toutiao_content_factory.services.openai_service import OpenAIContentService


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
