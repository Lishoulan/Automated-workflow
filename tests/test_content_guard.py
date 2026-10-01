from __future__ import annotations

import json

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


class TestNumberAttribution:
    def test_number_attribution_normalizes_thousands_separators(
        self,
        app_settings,
        logger,
        tmp_path,
    ) -> None:
        guard = ContentGuardService(app_settings, logger)
        (tmp_path / "selected_topic.json").write_text(
            json.dumps(
                {"selected_topic": {"title": "Revenue grew 1445.0%"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        warnings = guard._check_number_attribution("Revenue grew 1,445.0%", tmp_path)

        assert warnings == []

    def test_number_pattern_keeps_kw_unit(self, app_settings, logger) -> None:
        guard = ContentGuardService(app_settings, logger)

        assert guard.NUMBER_PATTERN.findall("峰值功率1kW") == ["1kW"]


def test_publish_guard_persists_writing_quality_blockers(
    app_settings,
    logger,
    tmp_path,
) -> None:
    run_dir = tmp_path / "run"
    publish_dir = run_dir / "publish"
    publish_dir.mkdir(parents=True)
    (run_dir / "review_status.json").write_text(
        json.dumps({"status": "pending"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "titles.txt").write_text(
        "全部标题：\n1. 其他智能12.7亿：机器人如何实测\n\n"
        "推荐标题：\n1. 其他智能12.7亿：机器人如何实测\n",
        encoding="utf-8",
    )
    (publish_dir / "article.md").write_text(
        "公开材料显示，优必选、为最主要的增长驱动力出现在现有材料中。",
        encoding="utf-8",
    )

    guard = ContentGuardService(app_settings, logger)
    report = guard.build_publish_guard_report(run_dir)

    assert report["blocking_flags"]
    assert any("模糊主体" in item for item in report["blocking_flags"])
    assert any("残句" in item for item in report["blocking_flags"])
    persisted = json.loads(
        (run_dir / "content_guard_report.json").read_text(encoding="utf-8")
    )
    assert persisted["blocking_flags"] == report["blocking_flags"]
    status = json.loads(
        (run_dir / "review_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "rejected"
    assert status["guard_reason"] == "writing_quality"


def test_empty_history_still_checks_source_originality(
    app_settings,
    logger,
    tmp_path,
    monkeypatch,
) -> None:
    """A first publication has no history corpus but still has source material."""
    run_dir = tmp_path / "first-run"
    run_dir.mkdir()
    article = (
        "国家能源局发布两份电网安全文件，明确新型并网主体也要纳入风险管控。"
        "文件要求新能源和新型储能严格接受统一调度，并完善风险分级标准。"
        "公开数据还显示，全国光伏装机规模继续增长，电网调节需求同步上升。"
    ) * 8
    (run_dir / "article.md").write_text(article, encoding="utf-8")
    (run_dir / "titles.txt").write_text(
        "国家能源局连发两文：储能安全管理出现三项变化",
        encoding="utf-8",
    )
    (run_dir / "source_material_full.txt").write_text(
        "素材来源：公开报道\n原文标题：测试\n原文链接：https://example.test/source\n\n"
        + article,
        encoding="utf-8",
    )

    guard = ContentGuardService(app_settings, logger)
    monkeypatch.setattr(guard, "_get_corpus_and_index", lambda: ([], object()))

    report = guard.evaluate_run(run_dir)

    assert report["source_copy_ratio"] is not None
    assert report["source_copy_ratio"] >= 35.0
    assert report["source_copy_chars"] > 0
    assert report["blocked"] is True
    persisted = json.loads(
        (run_dir / "content_guard_report.json").read_text(encoding="utf-8")
    )
    assert persisted["source_copy_ratio"] == report["source_copy_ratio"]
