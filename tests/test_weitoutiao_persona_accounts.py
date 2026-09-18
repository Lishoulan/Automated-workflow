"""回归测试：微头条人设必须按账号解析，且社会故事类人设存在。

背景（2026-09-18）：
1. `PERSONA_TEMPLATES` 只有 hot_comment / warm / sanhuo / shoulan 四个写死人设，
   没有一个是社会故事类的。对中老年读者，微头条才是主流量入口，而
   story_notes 用这些人设发出来的会是科技热点评论腔或正能量鸡汤腔。
2. `weitoutiao_personas` 只读全局（环境变量，否则全局 config.yaml），
   账号级 `publishing.weitoutiao_personas` 写了也不生效 ——
   与 `default_style` 是同一个毛病。

本文件同时锁住「人设可配置」和「story_comment 的反虚构红线段落」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.weitoutiao_generator import PERSONA_TEMPLATES


@pytest.fixture()
def clean_persona_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TOUTIAO_WEITOUTIAO_PERSONAS",
        "TOUTIAO_WEITOUTIAO_PERSONAS__STORY_NOTES",
        "TOUTIAO_WEITOUTIAO_PERSONAS__ROBOT_NOTES",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 1. 人设注册
# ---------------------------------------------------------------------------


def test_story_comment_persona_is_registered() -> None:
    assert "story_comment" in PERSONA_TEMPLATES
    system_prompt, user_prompt = PERSONA_TEMPLATES["story_comment"]
    assert system_prompt.strip()
    assert user_prompt.strip()


def test_story_comment_prompt_forbids_fabricated_first_person() -> None:
    """这是合规红线，不能被悄悄改掉。

    头条《内容创作规范》把「编造与事实不符的故事」「以讲述自身经历……的形式
    吸引点击」列为违规，AI 低质内容也在专项治理范围内。
    """
    _system, user_prompt = PERSONA_TEMPLATES["story_comment"]

    assert "严禁编造" in user_prompt
    assert "不得用第一人称说自己亲历了这件事" in user_prompt
    assert "不得编一个" in user_prompt


def test_story_comment_prompt_keeps_the_platform_risk_lines() -> None:
    """不做健康/投资建议、不煽动对立、不替司法下结论。"""
    _system, user_prompt = PERSONA_TEMPLATES["story_comment"]

    assert "不做健康建议" in user_prompt
    assert "不做投资建议" in user_prompt
    assert "不煽动群体对立" in user_prompt


def test_existing_personas_are_untouched() -> None:
    """既有四个人设必须还在 —— 机器人账号依赖它们。"""
    for persona in ("hot_comment", "warm", "sanhuo", "shoulan"):
        assert persona in PERSONA_TEMPLATES


# ---------------------------------------------------------------------------
# 2. 按账号解析
# ---------------------------------------------------------------------------


def test_story_account_uses_the_story_persona(clean_persona_env: None) -> None:
    settings = load_settings(Path.cwd(), account_key="story_notes")

    assert settings.publishing.weitoutiao_personas == ["story_comment"]


def test_robot_account_keeps_its_personas(clean_persona_env: None) -> None:
    """既有账号行为不变 —— 这是这次改动的底线。"""
    settings = load_settings(Path.cwd(), account_key="robot_notes")

    assert settings.publishing.weitoutiao_personas == ["hot_comment", "warm", "sanhuo"]


def test_two_accounts_do_not_share_personas(clean_persona_env: None) -> None:
    robot = load_settings(Path.cwd(), account_key="robot_notes")
    story = load_settings(Path.cwd(), account_key="story_notes")

    assert robot.publishing.weitoutiao_personas != story.publishing.weitoutiao_personas


def test_scoped_env_var_overrides_personas(
    clean_persona_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOUTIAO_WEITOUTIAO_PERSONAS__STORY_NOTES", "warm,story_comment")

    settings = load_settings(Path.cwd(), account_key="story_notes")

    assert settings.publishing.weitoutiao_personas == ["warm", "story_comment"]


def test_blank_scoped_env_var_falls_through_to_config(
    clean_persona_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空字符串不能被当成有效覆盖，否则会把人设清空。"""
    monkeypatch.setenv("TOUTIAO_WEITOUTIAO_PERSONAS__STORY_NOTES", "   ")

    settings = load_settings(Path.cwd(), account_key="story_notes")

    assert settings.publishing.weitoutiao_personas == ["story_comment"]


def test_story_account_gets_a_higher_weitoutiao_target(clean_persona_env: None) -> None:
    """中老年流量的主入口是微头条，故事账号的目标量应高于机器人账号的 1 条。"""
    story = load_settings(Path.cwd(), account_key="story_notes")
    robot = load_settings(Path.cwd(), account_key="robot_notes")

    assert story.publishing.weitoutiao_daily_target > robot.publishing.weitoutiao_daily_target


def test_every_configured_persona_has_a_template(clean_persona_env: None) -> None:
    """配置里写了但模板里没有的人设会被静默回退成 hot_comment。"""
    for account_key in ("robot_notes", "story_notes", "default"):
        settings = load_settings(Path.cwd(), account_key=account_key)
        for persona in settings.publishing.weitoutiao_personas:
            assert persona in PERSONA_TEMPLATES, (
                f"{account_key} 配置了不存在的人设 {persona!r}"
            )
