from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image

from toutiao_content_factory.services.image_generation_service import ImageGenerationService


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
