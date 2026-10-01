from __future__ import annotations

import pytest

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import DataReport
from toutiao_content_factory.services.prompt_repository import PromptRepository


@pytest.fixture()
def base_agent(app_settings: AppSettings, mock_openai_service, logger) -> BaseAgent:
    prompt_repo = PromptRepository(app_settings)
    return BaseAgent(app_settings, prompt_repo, mock_openai_service, logger)


class TestStripPromptLeakage:
    def test_removes_meta_noise_prefixes(self, base_agent: BaseAgent) -> None:
        text = (
            "账号定位：新能源赛道\n"
            "正常内容行1\n"
            "标题：这是标题\n"
            "正常内容行2\n"
            "来源：某网站\n"
            "正常内容行3"
        )
        result = base_agent.strip_prompt_leakage(text)
        assert "账号定位" not in result
        assert "标题：" not in result
        assert "来源：" not in result
        assert "正常内容行1" in result
        assert "正常内容行2" in result
        assert "正常内容行3" in result

    def test_strips_brand_mentions(self, base_agent: BaseAgent) -> None:
        text = "测试品牌发布新报告"
        result = base_agent.strip_prompt_leakage(text)
        assert "测试品牌" not in result

    def test_empty_input(self, base_agent: BaseAgent) -> None:
        assert base_agent.strip_prompt_leakage("") == ""
        assert base_agent.strip_prompt_leakage(None) == ""  # type: ignore[arg-type]

    def test_collapses_multiple_blank_lines(self, base_agent: BaseAgent) -> None:
        text = "行1\n\n\n\n行2"
        result = base_agent.strip_prompt_leakage(text)
        assert "\n\n\n" not in result

    def test_preserves_single_blank_lines(self, base_agent: BaseAgent) -> None:
        # strip_prompt_leakage normalizes multi-space runs to single space,
        # while preserving the paragraph boundary.
        text = "行1\n\n行2"
        result = base_agent.strip_prompt_leakage(text)
        assert "行1" in result
        assert "行2" in result

    def test_preserves_paragraph_boundaries_when_normalizing_spaces(self, base_agent: BaseAgent) -> None:
        text = "第一段   内容\n\n第二段\t内容"

        assert base_agent.strip_prompt_leakage(text) == "第一段 内容\n\n第二段 内容"


class TestCleanPublishText:
    def test_replaces_internal_labels(self, base_agent: BaseAgent) -> None:
        text = "humanoid_robot领域增长迅速"
        result = base_agent.clean_publish_text(text)
        assert "人形机器人" in result
        assert "humanoid_robot" not in result

    def test_normalizes_whitespace(self, base_agent: BaseAgent) -> None:
        text = "新能源   领域  增长"
        result = base_agent.clean_publish_text(text)
        assert "  " not in result

    def test_strips_brand(self, base_agent: BaseAgent) -> None:
        text = "测试品牌报告"
        result = base_agent.clean_publish_text(text)
        assert "测试品牌" not in result

    def test_empty_input(self, base_agent: BaseAgent) -> None:
        assert base_agent.clean_publish_text("") == ""
        assert base_agent.clean_publish_text(None) == ""  # type: ignore[arg-type]

    def test_all_internal_labels_replaced(self, base_agent: BaseAgent) -> None:
        for raw, label in BaseAgent.INTERNAL_LABEL_MAP.items():
            text = f"关注{raw}赛道"
            result = base_agent.clean_publish_text(text)
            assert raw not in result
            assert label in result


class TestCleanPublishList:
    def test_deduplicates(self, base_agent: BaseAgent) -> None:
        items = ["新能源增长", "新能源增长", "储能增长"]
        result = base_agent.clean_publish_list(items)
        assert len(result) == 2

    def test_respects_limit(self, base_agent: BaseAgent) -> None:
        items = ["a", "b", "c", "d", "e"]
        result = base_agent.clean_publish_list(items, limit=3)
        assert len(result) == 3

    def test_no_limit(self, base_agent: BaseAgent) -> None:
        items = ["a", "b", "c"]
        result = base_agent.clean_publish_list(items)
        assert len(result) == 3

    def test_skips_empty_and_brand(self, base_agent: BaseAgent) -> None:
        items = ["", "测试品牌报告", "有效内容"]
        result = base_agent.clean_publish_list(items)
        assert "有效内容" in result
        # "测试品牌报告" -> after brand stripping becomes "报告"
        assert "测试品牌" not in "".join(result)


class TestCompanyCandidates:
    def test_normalizes_and_filters_company_candidates(self, base_agent: BaseAgent) -> None:
        report = DataReport(
            focus_companies=["特斯拉Optimus机器人", "柏林工厂", "公开材料"],
            companies=["比亚迪", "国瓷材料", "AI机器人", "涉及哪些公司"],
        )

        assert base_agent._company_candidates(report) == ["特斯拉", "比亚迪", "国瓷材料"]

    @pytest.mark.parametrize(
        "candidate",
        [
            "柏林工厂",
            "公开材料",
            "人形机器人",
            "据德国汽车",
            "涉及哪些公司",
            "重点看电池",
            "2026年上半年该公司",
            "其他智能机器人",
        ],
    )
    def test_rejects_non_company_context(self, base_agent: BaseAgent, candidate: str) -> None:
        assert not base_agent._is_valid_company_name(candidate)


def test_topic_subject_label_does_not_reuse_stale_industry_label(base_agent: BaseAgent) -> None:
    report = DataReport(
        topic_title="某港口氢能调度项目进入测试阶段",
        focus_industry="锂电",
        focus_keywords=["锂电"],
        core_data=["2026年某港口完成氢能调度设备测试"],
    )

    assert base_agent._topic_subject_label(report, fallback="相关主题") == "相关主题"


def test_topic_subject_label_prefers_explicit_headline_subject_over_secondary_fact(
    base_agent: BaseAgent,
) -> None:
    report = DataReport(
        topic_title="刚刚，国家能源局连发两文，储能新利好来了！",
        focus_industry="科技制造",
        focus_keywords=["光伏"],
        core_data=["全国光伏累计装机容量约7.9亿千瓦"],
    )

    assert base_agent._topic_subject_label(report) == "储能"


class TestUnique:
    def test_deduplicates_strings(self, base_agent: BaseAgent) -> None:
        items = ["a", "b", "a", "c", "b"]
        result = base_agent.unique(items)
        assert result == ["a", "b", "c"]

    def test_strips_whitespace(self, base_agent: BaseAgent) -> None:
        items = [" a", "a", "b "]
        result = base_agent.unique(items)
        assert result == ["a", "b"]

    def test_skips_empty(self, base_agent: BaseAgent) -> None:
        items = ["a", "", "  ", "b"]
        result = base_agent.unique(items)
        assert result == ["a", "b"]

    def test_empty_list(self, base_agent: BaseAgent) -> None:
        assert base_agent.unique([]) == []


class TestNormalizeTitle:
    def test_removes_whitespace(self, base_agent: BaseAgent) -> None:
        assert base_agent.normalize_title("新 能 源 增长") == "新能源增长"

    def test_strips_trailing_punctuation(self, base_agent: BaseAgent) -> None:
        assert base_agent.normalize_title("新能源增长！") == "新能源增长"
        assert base_agent.normalize_title("新能源增长：") == "新能源增长"

    def test_mixed_punctuation(self, base_agent: BaseAgent) -> None:
        assert base_agent.normalize_title(" 标题 ，。！") == "标题"
