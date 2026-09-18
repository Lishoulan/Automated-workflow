"""回归测试：写作风格必须按账号解析。

背景（2026-09-18）：`load_settings` 原来算 `default_style` 时**只读全局**
`writing.default_style`，而本项目的 `.env` 又设了 `DEFAULT_STYLE=tech_media`
且环境变量无条件压过配置。结果是 config.yaml 里每个账号自带的
`writing.default_style` 被静默忽略 —— 所有账号其实共用同一个写作风格，
「按账号换风格」这件事根本不存在。

修好后优先级为：账号级 `DEFAULT_STYLE__<SLUG>` 环境变量 >
账号级 `writing.default_style` > 裸 `DEFAULT_STYLE` > 全局 `writing.default_style`。

这些用例直接读仓库里的 config.yaml（与 test_wechat_135_disabled.py 同样的做法），
但会先清掉环境变量，避免本机 `.env` 干扰断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings


@pytest.fixture()
def clean_style_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉所有可能影响风格判定的环境变量，只留 config.yaml 说话。"""
    for name in (
        "DEFAULT_STYLE",
        "DEFAULT_STYLE__ROBOT_NOTES",
        "DEFAULT_STYLE__SPORTS_NOTES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_account_level_style_is_used(clean_style_env: None) -> None:
    """robot_notes 在 config.yaml 里写了 family_story，就必须是 family_story。"""
    settings = load_settings(Path.cwd(), account_key="robot_notes")

    assert settings.default_style == "family_story"


def test_two_accounts_do_not_share_one_style(clean_style_env: None) -> None:
    """不同账号必须拿到各自的风格 —— 这正是修复前做不到的事。

    2026-09-18 换赛道后：robot_notes 就是那个做社会故事的账号（它登录的头条号是
    手蓝数据笔记），sports_notes 仍是体育号。
    """
    story = load_settings(Path.cwd(), account_key="robot_notes")
    sports = load_settings(Path.cwd(), account_key="sports_notes")

    assert story.default_style != sports.default_style


def test_account_style_beats_the_bare_env_var(
    clean_style_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """账号级风格必须压过裸 DEFAULT_STYLE —— 这正是修复的核心。

    修复前 `.env` 的 `DEFAULT_STYLE` 会无条件覆盖一切；现在它退到账号级之后。
    """
    monkeypatch.setenv("DEFAULT_STYLE", "finance_report")

    settings = load_settings(Path.cwd(), account_key="robot_notes")

    assert settings.default_style == "family_story"


def test_scoped_env_var_beats_the_account_style(
    clean_style_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """想临时强制换风格时，账号级环境变量仍然是最高优先级。"""
    monkeypatch.setenv("DEFAULT_STYLE__ROBOT_NOTES", "finance_report")

    settings = load_settings(Path.cwd(), account_key="robot_notes")

    assert settings.default_style == "finance_report"


def test_family_story_style_profile_is_registered(clean_style_env: None) -> None:
    """family_story 必须真的在 style_profiles 里，否则会被静默回退成默认风格。"""
    settings = load_settings(Path.cwd(), account_key="robot_notes")

    assert "family_story" in settings.style_profiles
    profile = settings.get_style()
    assert profile.key == "family_story"
    assert profile.label == "人间故事观察风"


def test_family_story_style_forbids_fabricated_first_person(clean_style_env: None) -> None:
    """合规约束必须写进风格指令里：禁止编造第一人称亲历故事。

    头条《内容创作规范》把「编造与事实不符的故事」「以讲述自身经历的形式吸引
    点击」列为违规，这条指令是该赛道唯一的自动化合规闸门，不能被悄悄改掉。
    """
    settings = load_settings(Path.cwd(), account_key="robot_notes")
    instruction = settings.get_style().article_instruction

    assert "禁止编造第一人称亲历故事" in instruction
    assert "素材来源" in instruction
