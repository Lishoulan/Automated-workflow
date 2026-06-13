from __future__ import annotations

import pytest

from toutiao_content_factory.models import ExtractionResult, SourceDocument
from toutiao_content_factory.services.extractor import SignalExtractor


@pytest.fixture()
def extractor() -> SignalExtractor:
    return SignalExtractor()


class TestExtract:
    def test_full_extraction(self, extractor: SignalExtractor, sample_source_document: SourceDocument) -> None:
        result = extractor.extract([sample_source_document])
        assert isinstance(result, ExtractionResult)
        assert result.merged_text
        assert result.focus_industry == "新能源"
        assert len(result.companies) > 0
        assert len(result.growth_numbers) > 0

    def test_empty_documents(self, extractor: SignalExtractor) -> None:
        doc = SourceDocument(source_type="manual", content="简短")
        result = extractor.extract([doc])
        assert isinstance(result, ExtractionResult)

    def test_multiple_documents(self, extractor: SignalExtractor) -> None:
        docs = [
            SourceDocument(source_type="rss", content="宁德时代储能业务增长67%。", title="储能报告"),
            SourceDocument(source_type="rss", content="比亚迪新能源销量300万辆。", title="新能源报告"),
        ]
        result = extractor.extract(docs)
        assert "宁德时代" in result.merged_text
        assert "比亚迪" in result.merged_text


class TestSplitSentences:
    def test_splits_on_chinese_punctuation(self, extractor: SignalExtractor) -> None:
        text = "第一句。第二句！第三句？第四句"
        result = extractor._split_sentences(text)
        assert len(result) == 4

    def test_splits_on_newlines(self, extractor: SignalExtractor) -> None:
        text = "第一行\n第二行\n第三行"
        result = extractor._split_sentences(text)
        assert len(result) == 3

    def test_skips_empty_chunks(self, extractor: SignalExtractor) -> None:
        text = "第一句。。。第二句"
        result = extractor._split_sentences(text)
        assert all(chunk.strip() for chunk in result)

    def test_empty_text(self, extractor: SignalExtractor) -> None:
        assert extractor._split_sentences("") == []


class TestExtractCompanies:
    def test_extracts_company_with_suffix(self, extractor: SignalExtractor) -> None:
        text = "宁德时代新能源科技股份有限公司发布财报"
        result = extractor._extract_companies(text, "")
        assert any("宁德时代" in c for c in result)

    def test_excludes_generic_names(self, extractor: SignalExtractor) -> None:
        text = "人形机器人产业链增长"
        result = extractor._extract_companies(text, "")
        assert "人形机器人" not in result

    def test_extracts_from_title(self, extractor: SignalExtractor) -> None:
        # Title company candidates must be 2-8 CJK chars; "宁德时代" is 4 chars
        # but needs to appear in analysis text with a company suffix to be valid
        result = extractor._extract_companies(
            "宁德时代新能源科技股份有限公司发布新报告",
            "宁德时代储能突破",
        )
        assert len(result) > 0

    def test_empty_text(self, extractor: SignalExtractor) -> None:
        result = extractor._extract_companies("", "")
        assert result == []


class TestExtractGrowthNumbers:
    def test_extracts_percentages(self, extractor: SignalExtractor) -> None:
        text = "同比增长67%，环比增长23%"
        result = extractor._extract_growth_numbers(text)
        assert any("67%" in item for item in result)
        assert any("23%" in item for item in result)

    def test_extracts_absolute_numbers(self, extractor: SignalExtractor) -> None:
        text = "装机量达50GWh，投资300亿元"
        result = extractor._extract_growth_numbers(text)
        assert len(result) >= 2

    def test_deduplicates(self, extractor: SignalExtractor) -> None:
        text = "增长67%，再增长67%"
        result = extractor._extract_growth_numbers(text)
        count_67 = sum(1 for item in result if "67%" in item)
        assert count_67 == 1

    def test_empty_text(self, extractor: SignalExtractor) -> None:
        assert extractor._extract_growth_numbers("") == []


class TestIsNoiseSentence:
    def test_short_sentence_is_noise(self, extractor: SignalExtractor) -> None:
        assert extractor._is_noise_sentence("短") is True

    def test_meta_marker_is_noise(self, extractor: SignalExtractor) -> None:
        assert extractor._is_noise_sentence("标题：这是标题") is True

    def test_valid_sentence_not_noise(self, extractor: SignalExtractor) -> None:
        assert extractor._is_noise_sentence("宁德时代储能业务同比增长67%，超出市场预期") is False

    def test_known_noise_phrase(self, extractor: SignalExtractor) -> None:
        assert extractor._is_noise_sentence("关键结论") is True

    def test_normal_long_sentence(self, extractor: SignalExtractor) -> None:
        assert extractor._is_noise_sentence("这条产业链的利润兑现节奏取决于下游需求能否持续放量") is False


class TestDeduplicate:
    def test_removes_duplicates(self, extractor: SignalExtractor) -> None:
        items = ["a", "b", "a", "c"]
        result = extractor._deduplicate(items)
        assert result == ["a", "b", "c"]

    def test_strips_whitespace(self, extractor: SignalExtractor) -> None:
        items = [" a", "a ", "b"]
        result = extractor._deduplicate(items)
        assert result == ["a", "b"]

    def test_skips_empty(self, extractor: SignalExtractor) -> None:
        items = ["a", "", "  ", "b"]
        result = extractor._deduplicate(items)
        assert result == ["a", "b"]

    def test_empty_list(self, extractor: SignalExtractor) -> None:
        assert extractor._deduplicate([]) == []
