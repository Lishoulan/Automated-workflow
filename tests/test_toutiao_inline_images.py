from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from toutiao_content_factory.config import PublishSettings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.publish_service import PublishService


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), color).save(path, format="PNG")


def test_resolve_inline_assets_prefers_publish_package_and_uses_run_fallback(
    app_settings, tmp_path: Path
) -> None:
    service = PublishService(app_settings, logging.getLogger("inline-assets-resolver-test"))
    run_dir = tmp_path / "run"
    publish_images = run_dir / "publish" / "images"
    run_images = run_dir / "images"
    _write_png(publish_images / "infographic_1.png", (10, 20, 30))
    _write_png(run_images / "infographic_1.png", (40, 50, 60))
    _write_png(run_images / "infographic_2.png", (70, 80, 90))

    notes: list[str] = []
    assets = service._resolve_toutiao_inline_image_assets(run_dir, notes)

    assert assets == [publish_images / "infographic_1.png", run_images / "infographic_2.png"]
    assert "infographic_1.png" in notes[-1]
    assert "infographic_2.png" in notes[-1]


def test_inline_asset_resolution_honors_configuration(app_settings, tmp_path: Path) -> None:
    service = PublishService(app_settings, logging.getLogger("inline-assets-config-test"))
    run_dir = tmp_path / "run"
    _write_png(run_dir / "publish" / "images" / "infographic_1.png", (10, 20, 30))
    object.__setattr__(app_settings.publishing, "inline_images_enabled", False)

    notes: list[str] = []
    assets = service._resolve_toutiao_inline_image_assets(run_dir, notes)

    assert assets == []
    assert any("disabled" in note.lower() for note in notes)


def test_publish_settings_inline_images_default_to_enabled() -> None:
    assert PublishSettings.from_dict({}).inline_images_enabled is True
    assert PublishSettings.from_dict({"inline_images_enabled": False}).inline_images_enabled is False


def test_daily_automation_generates_inline_assets_before_platform_publish(
    app_settings, tmp_path: Path
) -> None:
    app = ContentFactoryAppService(app_settings, logging.getLogger("daily-inline-images-test"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    app.image_generation_service.generate_for_run = MagicMock(return_value={})

    app._run_daily_platform_automation(run_dir, dry_run=False, skip_wait=True)

    app.image_generation_service.generate_for_run.assert_called_once_with(
        run_dir,
        include_infographics=True,
    )


def test_inline_image_upload_deduplicates_assets_and_tracks_inserted_files(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    service = PublishService(app_settings, logging.getLogger("inline-assets-upload-test"))
    first = tmp_path / "infographic_1.png"
    duplicate = tmp_path / "duplicate.png"
    second = tmp_path / "infographic_2.png"
    _write_png(first, (10, 20, 30))
    _write_png(duplicate, (10, 20, 30))
    _write_png(second, (70, 80, 90))

    calls: list[tuple[Path, int]] = []

    def fake_apply(_page, _selector, image_path: Path, _notes, *, target_paragraph: int) -> bool:
        calls.append((image_path, target_paragraph))
        return True

    monkeypatch.setattr(service, "_try_apply_article_image", fake_apply)
    notes: list[str] = []
    article = "Opening context.\n\nRevenue grew 12.5% year on year.\n\n"
    article += "Track delivery quality and customer concentration.\n\nFinal outlook."

    inserted = service._try_apply_article_images(
        page=object(),
        editor_selector=".editor",
        article=article,
        image_paths=[first, duplicate, second],
        notes=notes,
    )

    assert inserted == 2
    assert [path for path, _target in calls] == [first, second]
    assert service._last_inline_image_assets == [first, second]
    assert any("duplicate" in note.lower() for note in notes)
