from __future__ import annotations

import base64
from dataclasses import replace
from io import BytesIO
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from toutiao_content_factory.services.image_generation_service import (
    ImageGenerationAPIUnavailable,
    ImageGenerationService,
)


def _prepare_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    publish_dir = run_dir / "publish"
    publish_dir.mkdir(parents=True)
    (publish_dir / "publish_meta.json").write_text(
        json.dumps(
            {
                "selected_title": "Energy storage orders reveal a changing market",
                "selected_topic": {"title": "Energy storage industry update"},
                "summary": "The latest release shows a measurable change in demand.",
            }
        ),
        encoding="utf-8",
    )
    (publish_dir / "article.md").write_text(
        "The opening explains why the latest market release matters.\n\n"
        "Revenue grew 12.5% year on year while installed capacity reached 50 GWh.\n\n"
        "The supply chain is moving from capacity expansion toward delivery quality.\n\n"
        "Investors should track utilization, customer concentration, and delivery risk.",
        encoding="utf-8",
    )
    (run_dir / "workflow_result.json").write_text(
        json.dumps(
            {
                "data_report": {
                    "core_data": [
                        "Revenue grew 12.5% year on year",
                        "Installed capacity reached 50 GWh",
                    ],
                    "evidence_notes": ["The figures come from the persisted company release."],
                    "key_points": ["Delivery quality is becoming more important than raw capacity."],
                    "watch_metrics": ["Track utilization and customer concentration in the next release."],
                    "growth_numbers": ["12.5%", "50 GWh"],
                    "evidence_strength": 92,
                }
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_generate_for_run_writes_independent_evidence_backed_infographics(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    run_dir = _prepare_run(tmp_path)
    service = ImageGenerationService(app_settings, logging.getLogger("image-generation-test"))
    monkeypatch.setattr(service, "_get_frontend_renderer", lambda: None)

    result = service.generate_for_run(run_dir, include_infographics=True)

    publish_images = run_dir / "publish" / "images"
    cover_path = publish_images / "cover.png"
    infographic_paths = [publish_images / "infographic_1.png", publish_images / "infographic_2.png"]
    assert result["cover_image"] == run_dir / "images" / "cover.png"
    assert cover_path.exists()
    assert all(path.exists() and path.stat().st_size > 0 for path in infographic_paths)
    assert all(path != cover_path for path in infographic_paths)

    with Image.open(cover_path) as cover:
        assert cover.format == "PNG"
        assert cover.size == (900, 383)
    for path in infographic_paths:
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == (800, 1000)

    payload = json.loads(
        (run_dir / "publish" / "image_generation_result.json").read_text(encoding="utf-8")
    )
    assert payload["inline_ready"] is True
    assert [Path(path).name for path in payload["publish_artifacts"]["infographics"]] == [
        "infographic_1.png",
        "infographic_2.png",
    ]
    assert payload["infographic_specs"][0]["items"][0] == "Revenue grew 12.5% year on year"
    assert any(metric["value"] == "12.5" for metric in payload["infographic_specs"][0]["metrics"])
    assert payload["infographic_prompts"]
    assert "Verified metrics" in payload["infographic_prompts"][0]
    assert payload["infographic_specs"][0]["source_note"]


def test_statistical_chart_uses_only_explicit_share_and_same_metric_comparison(app_settings) -> None:
    service = ImageGenerationService(app_settings, logging.getLogger("image-chart-test"))
    values = [
        "香港交易所官方公告：人形机器人产品及服务收入5.90299亿元，占总收入46.5%。",
        "整体毛利率由35.0%升至44.7%。",
    ]

    chart = service._build_statistical_chart(values)

    assert chart["type"] == "share"
    assert chart["display_value"] == "46.5%"
    assert "人形机器人" in chart["label"]
    assert chart["secondary_chart"]["type"] == "comparison"
    assert chart["secondary_chart"]["entries"][0]["display_value"] == "35.0%"
    assert chart["secondary_chart"]["entries"][1]["display_value"] == "44.7%"


def test_statistical_chart_does_not_compare_different_units(app_settings) -> None:
    service = ImageGenerationService(app_settings, logging.getLogger("image-chart-boundary-test"))
    values = ["收入12.5%，装机容量50 GWh。", "另一个指标为3.2亿元。"]

    assert service._build_statistical_chart(values) == {}


def test_generate_for_run_removes_stale_infographics_when_disabled(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    run_dir = _prepare_run(tmp_path)
    service = ImageGenerationService(app_settings, logging.getLogger("image-generation-disabled-test"))
    monkeypatch.setattr(service, "_get_frontend_renderer", lambda: None)
    service.generate_for_run(run_dir, include_infographics=True)

    result = service.generate_for_run(run_dir, include_infographics=False)

    assert not list((run_dir / "images").glob("infographic_*.png"))
    assert not list((run_dir / "publish" / "images").glob("infographic_*.png"))
    payload = json.loads(
        (run_dir / "publish" / "image_generation_result.json").read_text(encoding="utf-8")
    )
    assert result["cover_image"].exists()
    assert payload["inline_ready"] is False
    assert payload["publish_artifacts"]["infographics"] == []


def test_image2_backend_generates_ai_cover_but_keeps_statistical_inserts_local(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    run_dir = _prepare_run(tmp_path)
    app_settings.image_generation = replace(
        app_settings.image_generation,
        backend="image2",
        model_name="gpt-image-2",
        model_api_key="test-image-key",
        cover_size="1536x1024",
    )
    service = ImageGenerationService(app_settings, logging.getLogger("image-api-test"))
    calls: list[dict] = []
    source = Image.new("RGB", (1536, 1024), (28, 74, 96))
    source_buffer = BytesIO()
    source.save(source_buffer, format="PNG")
    encoded = base64.b64encode(source_buffer.getvalue()).decode("ascii")

    class FakeImages:
        def generate(self, **payload):
            calls.append(payload)
            return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])

    monkeypatch.setattr(service, "_get_image_api_client", lambda: SimpleNamespace(images=FakeImages()))
    monkeypatch.setattr(service, "_get_frontend_renderer", lambda: None)

    result = service.generate_for_run(run_dir, include_infographics=True)

    payload = json.loads(
        (run_dir / "publish" / "image_generation_result.json").read_text(encoding="utf-8")
    )
    assert calls and calls[0]["model"] == "gpt-image-2"
    assert calls[0]["size"] == "1536x1024"
    assert "Do not render any text" in calls[0]["prompt"]
    assert result["cover_image"].exists()
    with Image.open(result["cover_image"]) as cover:
        assert cover.size == (1200, 675)
    assert payload["backend"] == "image2"
    assert payload["api_attempted"] is True
    assert payload["api_succeeded"] is True
    assert payload["model"] == "gpt-image-2"
    assert payload["infographic_backend"] == "pillow"
    assert payload["inline_ready"] is True


def test_image2_backend_falls_back_when_api_fails(app_settings, tmp_path: Path, monkeypatch) -> None:
    run_dir = _prepare_run(tmp_path)
    app_settings.image_generation = replace(
        app_settings.image_generation,
        backend="image2",
        model_name="gpt-image-2",
        model_api_key="test-image-key",
    )
    service = ImageGenerationService(app_settings, logging.getLogger("image-api-fallback-test"))
    def fail_generation(*_args):
        raise ImageGenerationAPIUnavailable("provider rejected the request")

    monkeypatch.setattr(service, "_generate_api_image", fail_generation)
    monkeypatch.setattr(service, "_get_frontend_renderer", lambda: None)

    result = service.generate_for_run(run_dir, include_infographics=False)

    payload = json.loads(
        (run_dir / "publish" / "image_generation_result.json").read_text(encoding="utf-8")
    )
    assert result["cover_image"].exists()
    with Image.open(result["cover_image"]) as cover:
        assert cover.size == (900, 383)
    assert payload["backend"] == "pillow"
    assert payload["used_fallback"] is True
    assert payload["api_attempted"] is True
    assert payload["api_succeeded"] is False
    assert payload["api_error_type"] == "ImageGenerationAPIUnavailable"


def test_image_api_key_resolution_accepts_openai_key_fallback(app_settings, monkeypatch) -> None:
    service = ImageGenerationService(app_settings, logging.getLogger("image-key-test"))
    app_settings.image_generation = replace(app_settings.image_generation, model_api_key="")
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")

    assert service._resolve_image_api_key() == "fallback-key"
