from __future__ import annotations

import pytest

from toutiao_content_factory.config import (
    StyleProfile,
    _deep_merge,
    normalize_account_key,
    sanitize_account_slug,
)


class TestDeepMerge:
    def test_simple_merge(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_deeply_nested_merge(self) -> None:
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"d": 99}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 1, "d": 99}}}

    def test_override_replaces_non_dict_with_dict(self) -> None:
        base = {"a": "string"}
        override = {"a": {"nested": True}}
        result = _deep_merge(base, override)
        assert result == {"a": {"nested": True}}

    def test_override_replaces_dict_with_non_dict(self) -> None:
        base = {"a": {"nested": True}}
        override = {"a": "string"}
        result = _deep_merge(base, override)
        assert result == {"a": "string"}

    def test_empty_override(self) -> None:
        base = {"a": 1}
        result = _deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self) -> None:
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_mutates_base(self) -> None:
        base = {"a": 1}
        _deep_merge(base, {"b": 2})
        assert "b" in base


class TestStyleProfileFromDict:
    def test_full_payload(self) -> None:
        payload = {
            "label": "测试风格",
            "tone": "冷静",
            "article_instruction": "突出产业链",
            "title_instruction": "做出判断感",
            "image_instruction": "深色科技风",
            "opening_instruction": "开头先抛出判断",
        }
        profile = StyleProfile.from_dict("test_key", payload)
        assert profile.key == "test_key"
        assert profile.label == "测试风格"
        assert profile.tone == "冷静"

    def test_defaults(self) -> None:
        profile = StyleProfile.from_dict("minimal", {})
        assert profile.key == "minimal"
        assert profile.label == "minimal"
        assert profile.tone == ""
        assert profile.article_instruction == ""

    def test_key_used_as_default_label(self) -> None:
        profile = StyleProfile.from_dict("my_style", {})
        assert profile.label == "my_style"


class TestNormalizeAccountKey:
    def test_normal_value(self) -> None:
        assert normalize_account_key("my_account") == "my_account"

    def test_none_returns_default(self) -> None:
        assert normalize_account_key(None) == "default"

    def test_empty_returns_default(self) -> None:
        assert normalize_account_key("") == "default"

    def test_whitespace_returns_default(self) -> None:
        assert normalize_account_key("   ") == "default"

    def test_strips_whitespace(self) -> None:
        assert normalize_account_key("  my_account  ") == "my_account"


class TestSanitizeAccountSlug:
    def test_normal_value(self) -> None:
        # underscore is replaced by dash via ACCOUNT_SLUG_PATTERN
        assert sanitize_account_slug("my_account") == "my-account"

    def test_replaces_special_chars(self) -> None:
        assert sanitize_account_slug("my account!") == "my-account"

    def test_lowercases(self) -> None:
        assert sanitize_account_slug("MyAccount") == "myaccount"

    def test_strips_dashes(self) -> None:
        assert sanitize_account_slug("-my-account-") == "my-account"

    def test_none_returns_default(self) -> None:
        assert sanitize_account_slug(None) == "default"

    def test_empty_returns_default(self) -> None:
        assert sanitize_account_slug("") == "default"

    def test_all_special_chars_returns_default(self) -> None:
        assert sanitize_account_slug("---") == "default"
