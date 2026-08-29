from __future__ import annotations

from pathlib import Path

import pytest

from toutiao_content_factory.services.publish_precheck_service import (
    PublishPrecheckService,
)


# 一段干净、不含任何违规项的正文，用于"通过"场景。
_CLEAN_ARTICLE = (
    "宁德时代发布2024年财报，储能业务同比增长67%。\n\n"
    "比亚迪新能源销量突破300万辆，产业链上下游景气度持续提升。\n\n"
    "从交付端看，目前多数厂商仍处于小批量试产阶段，产线良率是关键指标。"
)


class TestPublishPrecheckService:
    """PublishPrecheckService 单元测试。"""

    @pytest.fixture()
    def service(self) -> PublishPrecheckService:
        return PublishPrecheckService(account="test_account")

    def test_cta_marker_blocked(self, service: PublishPrecheckService, tmp_path: Path) -> None:
        """含 ## 延伸服务 CTA 段落应被拦截。"""
        article = _CLEAN_ARTICLE + "\n\n## 延伸服务\n如果你想把信息差变成结果，欢迎交流。"
        result = service.run_precheck(article, project_root=tmp_path)

        assert result["status"] == "blocked"
        rules = {v["rule"] for v in result["violations"]}
        assert "cta_marker" in rules
        matched = {v["matched"] for v in result["violations"] if v["rule"] == "cta_marker"}
        assert "## 延伸服务" in matched

    def test_internal_identifier_blocked(
        self, service: PublishPrecheckService, tmp_path: Path
    ) -> None:
        """含 evergreen_pool 内部标识应被拦截。"""
        article = _CLEAN_ARTICLE + "\n\n来源：evergreen_pool/abc"
        result = service.run_precheck(article, project_root=tmp_path)

        assert result["status"] == "blocked"
        rules = {v["rule"] for v in result["violations"]}
        assert "internal_identifier" in rules
        matched = {v["matched"] for v in result["violations"] if v["rule"] == "internal_identifier"}
        assert "evergreen_pool" in matched

    def test_repeated_phrase_blocked(
        self, service: PublishPrecheckService, tmp_path: Path
    ) -> None:
        """同一指标短语出现 3 次应被拦截。

        指标短语需出现在非中文边界处（句首/句号前），以便正则能精确切分出
        形如"词、词、词"的短语本身。
        """
        phrase = "订单量、量产节奏、单机成本"
        article = (
            f"{phrase}。第一节关注这些核心指标。"
            f"{phrase}。第二节拆解相关环比数据。"
            f"{phrase}。第三节对比历史水平。"
        )
        result = service.run_precheck(article, project_root=tmp_path)

        assert result["status"] == "blocked"
        rules = {v["rule"] for v in result["violations"]}
        assert "repeated_phrase" in rules
        repeated = [v for v in result["violations"] if v["rule"] == "repeated_phrase"]
        assert any(v["matched"] == phrase and v["count"] >= 3 for v in repeated)

    def test_internal_url_blocked(self, service: PublishPrecheckService, tmp_path: Path) -> None:
        """含 evergreen:// 内部 URL 应被拦截。"""
        article = _CLEAN_ARTICLE + "\n\n详见 evergreen://topic/123"
        result = service.run_precheck(article, project_root=tmp_path)

        assert result["status"] == "blocked"
        rules = {v["rule"] for v in result["violations"]}
        assert "internal_url" in rules
        matched = [v["matched"] for v in result["violations"] if v["rule"] == "internal_url"]
        assert any(m.startswith("evergreen://") for m in matched)

    def test_clean_article_passed(
        self, service: PublishPrecheckService, tmp_path: Path
    ) -> None:
        """干净文章应通过预检。"""
        result = service.run_precheck(_CLEAN_ARTICLE, project_root=tmp_path)

        assert result["status"] == "passed"
        assert result["violations"] == []
        assert result["article_length"] == len(_CLEAN_ARTICLE)
        # 报告文件应实际落盘
        report_path = Path(result["report_path"])
        assert report_path.exists()
        assert report_path.name == "precheck_report.json"
