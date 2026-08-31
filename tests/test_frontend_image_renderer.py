from __future__ import annotations

from pathlib import Path

from toutiao_content_factory.services.frontend_image_renderer import FrontendImageRenderer


def test_render_infographic_passes_chart_and_provenance_to_template(tmp_path: Path, monkeypatch) -> None:
    renderer = FrontendImageRenderer()
    captured: dict[str, object] = {}

    def fake_screenshot(html: str, output_path: str, width: int, height: int) -> str:
        captured.update(html=html, width=width, height=height)
        Path(output_path).write_bytes(b"preview")
        return output_path

    monkeypatch.setattr(renderer, "_screenshot", fake_screenshot)

    result = renderer.render_infographic(
        "人形机器人收入结构",
        str(tmp_path / "infographic.png"),
        metrics=[{"label": "收入", "value": "12.69", "unit": "亿元"}],
        items=["公司披露收入12.69亿元。"],
        chart={
            "type": "share",
            "label": "人形机器人收入占比",
            "value": 46.5,
            "display_value": "46.5%",
            "reference": "总收入",
            "note": "公司披露，不代表行业份额",
        },
        source_note="香港交易所官方公告",
        period_label="截至2026年6月30日",
        notes=["不外推为行业统计"],
    )

    html = str(captured["html"])
    assert result == str(tmp_path / "infographic.png")
    assert captured["width"] == 800
    assert captured["height"] == 1200
    assert "人形机器人收入占比" in html
    assert "46.5%" in html
    assert "香港交易所官方公告" in html
    assert "不外推为行业统计" in html
