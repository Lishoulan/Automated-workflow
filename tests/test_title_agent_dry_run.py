from __future__ import annotations

from toutiao_content_factory.agents.title_agent import TitleAgent
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


def test_separate_title_model_preserves_force_mock(
    app_settings, logger, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_TITLE_MODEL", "title-only-model")
    body_service = OpenAIContentService(app_settings, logger, force_mock=True)

    agent = TitleAgent(
        app_settings,
        PromptRepository(app_settings.prompts_dir),
        body_service,
        logger,
    )

    assert agent.openai_service.model == "title-only-model"
    assert agent.openai_service.force_mock is True
    assert agent.openai_service.available is False
