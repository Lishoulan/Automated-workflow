from __future__ import annotations

import pytest

from toutiao_content_factory.services.content_guard_service import (
    ContentGuardService,
    SensitiveWordTrie,
)


class TestSensitiveWordTrie:
    def test_single_word_match(self) -> None:
        trie = SensitiveWordTrie(["违禁词"])
        result = trie.find_all("这是违禁词测试")
        assert "违禁词" in result

    def test_multiple_words_match(self) -> None:
        trie = SensitiveWordTrie(["敏感", "违禁"])
        result = trie.find_all("包含敏感和违禁的文本")
        assert "敏感" in result
        assert "违禁" in result

    def test_no_match(self) -> None:
        trie = SensitiveWordTrie(["违禁词"])
        result = trie.find_all("这是一段正常文本")
        assert result == []

    def test_overlapping_patterns(self) -> None:
        trie = SensitiveWordTrie(["abc", "bc"])
        result = trie.find_all("abc")
        assert "abc" in result
        assert "bc" in result

    def test_empty_text(self) -> None:
        trie = SensitiveWordTrie(["违禁词"])
        assert trie.find_all("") == []

    def test_empty_word_list(self) -> None:
        trie = SensitiveWordTrie([])
        assert trie.find_all("任意文本") == []

    def test_duplicate_words_in_list(self) -> None:
        trie = SensitiveWordTrie(["违禁", "违禁"])
        result = trie.find_all("违禁内容")
        # Should find the word (may appear once or twice in output)
        assert "违禁" in result

    def test_longer_pattern_match(self) -> None:
        trie = SensitiveWordTrie(["敏感词库", "敏感"])
        result = trie.find_all("检测敏感词库")
        assert "敏感词库" in result
        assert "敏感" in result


class TestContentGuardScanText:
    def test_scan_clean_text(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard.scan_text("这是一段正常文本")
        assert result["blocked"] is False
        assert result["matched_words"] == []

    def test_scan_text_with_bad_word(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        # Write a known bad word to the file
        guard.bad_words = ["测试违禁词"]
        guard.bad_words_trie = SensitiveWordTrie(guard.bad_words)
        result = guard.scan_text("包含测试违禁词的文本")
        assert result["blocked"] is True
        assert "测试违禁词" in result["matched_words"]

    def test_scan_empty_text(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard.scan_text("")
        assert result["blocked"] is False

    def test_scan_text_produces_warnings(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        guard.bad_words = ["违禁测试词"]
        guard.bad_words_trie = SensitiveWordTrie(guard.bad_words)
        result = guard.scan_text("违禁测试词出现")
        assert len(result["warnings"]) > 0


class TestNormalizeText:
    def test_lowercases(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._normalize_text("ABC")
        assert result == "abc"

    def test_removes_whitespace(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._normalize_text("a b c")
        assert result == "abc"

    def test_removes_special_chars(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._normalize_text("a!b@c#")
        assert result == "abc"

    def test_preserves_chinese(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._normalize_text("中文 测试")
        assert result == "中文测试"

    def test_preserves_numbers_and_percent(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._normalize_text("增长50%")
        assert "50%" in result

    def test_truncates_long_text(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        long_text = "a" * 20000
        result = guard._normalize_text(long_text)
        assert len(result) <= 12000

    def test_empty_input(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._normalize_text("")
        assert result == ""


class TestCheckHighRiskCategories:
    def test_medical_category(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._check_high_risk_categories("推荐用药偏方治疗")
        assert result["blocked"] is True
        assert result["matched_category"] == "医疗健康"
        assert len(result["matched_tokens"]) > 0

    def test_stock_pick_category(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._check_high_risk_categories("荐股买入涨停")
        assert result["blocked"] is True
        assert result["matched_category"] == "财经荐股"

    def test_fortune_category(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._check_high_risk_categories("星座运势预测")
        assert result["blocked"] is True
        assert result["matched_category"] == "命理预测"

    def test_clean_text(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)
        result = guard._check_high_risk_categories("新能源储能增长")
        assert result["blocked"] is False
        assert result["matched_category"] == ""
        assert result["matched_tokens"] == []
