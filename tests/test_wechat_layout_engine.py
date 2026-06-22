from __future__ import annotations

from toutiao_content_factory.services.wechat_layout_engine import (
    MarkdownToWechatConverter,
    THEME_PRESETS,
    convert_markdown_to_wechat_html,
)


def test_converter_highlights_keywords_metrics_and_footer() -> None:
    html = convert_markdown_to_wechat_html(
        "AI 工具的留存率提升到 35%。\n\n## 延伸服务\n\n这里是原始 CTA。",
        theme=THEME_PRESETS["tech_blue"],
        brand_name="测试品牌",
        cta_payload={
            "heading": "想继续深聊这条赛道？",
            "bullets": ["回复“周报”，领取样刊。", "回复“咨询”，获取合作说明。"],
            "closing": "把注意力沉淀成资产，再把资产变成收入。",
        },
    )

    assert "## 延伸服务" not in html
    assert "测试品牌 出品" in html
    assert "原创" in html
    assert "35%" in html
    assert "想继续深聊这条赛道？" in html
    assert "border-radius: 999px" in html
    assert "<strong style=" in html


def test_converter_falls_back_to_basic_html(monkeypatch) -> None:
    converter = MarkdownToWechatConverter(
        theme=THEME_PRESETS["tech_blue"],
        brand_name="测试品牌",
        cta_payload={"heading": "继续交流"},
    )
    monkeypatch.setattr(
        converter,
        "_safe_main_render",
        lambda _markdown: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    html = converter.convert("普通文本，转化率 42%。")

    assert "<section style=" in html
    assert "42%" in html
    assert "原创" in html
