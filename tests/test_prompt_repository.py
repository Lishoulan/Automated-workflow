from pathlib import Path

from toutiao_content_factory.services.prompt_repository import PromptRepository


def test_render_supports_legacy_braced_placeholders(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "legacy.md").write_text(
        "平台={{platform}}\n主题=$topic\n缺失={{missing}}",
        encoding="utf-8",
    )

    rendered = PromptRepository(prompts_dir).render(
        "legacy.md",
        platform="今日头条",
        topic="机器人交付",
    )

    assert "平台=今日头条" in rendered
    assert "主题=机器人交付" in rendered
    assert "缺失={{missing}}" in rendered
