from __future__ import annotations

import logging
import importlib
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlencode

from PIL import Image

from toutiao_content_factory.config import PublishSettings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.publish_service import PublishService


publish_service_module = importlib.import_module(
    "toutiao_content_factory.services.publish_service"
)


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


def test_persistent_image_reference_parser_rejects_transient_editor_nodes(
    app_settings,
) -> None:
    service = PublishService(app_settings, logging.getLogger("inline-image-ref-test"))
    markup = """
    <div data-node-key="temporary-1"><img src="blob:https://mp.toutiao.com/temp"></div>
    <div data-node-key="temporary-2"><img src="data:image/png;base64,AAAA"></div>
    <div><img web_uri="tos-cn-i-persisted" src="blob:https://mp.toutiao.com/uploading"></div>
    <div><img src="https://p3-sign.douyinpic.com/persisted.png"></div>
    """

    assert service._extract_persistent_image_refs_from_html(markup) == [
        "tos-cn-i-persisted",
        "https://p3-sign.douyinpic.com/persisted.png",
    ]


def test_publish_media_payload_requires_persistent_refs_and_expected_count(
    app_settings,
) -> None:
    service = PublishService(app_settings, logging.getLogger("inline-image-payload-test"))
    content = (
        '<p>正文</p><img web_uri="tos-cn-i-persisted" src="blob:uploading">'
        '<img src="https://p3-sign.douyinpic.com/persisted.png">'
    )
    post_data = urlencode({"content": content, "save": "1"})

    verified = service._summarize_publish_media_payload(
        post_data,
        expected_inline_images=2,
        expected_refs=["tos-cn-i-persisted", "https://p3-sign.douyinpic.com/persisted.png"],
    )
    missing = service._summarize_publish_media_payload(
        urlencode({"content": '<p>正文</p><img src="blob:uploading">'}),
        expected_inline_images=2,
        expected_refs=["tos-cn-i-persisted", "https://p3-sign.douyinpic.com/persisted.png"],
    )

    assert verified["verified"] is True
    assert verified["payload_count"] == 2
    assert missing["verified"] is False
    assert missing["payload_count"] == 0


def test_save_override_waits_for_persistent_inline_images_before_consuming_once(
    app_settings,
) -> None:
    service = PublishService(app_settings, logging.getLogger("save-override-image-gate-test"))

    class _Request:
        def __init__(self, post_data: str) -> None:
            self.post_data = post_data

    class _Route:
        def __init__(self, post_data: str) -> None:
            self.request = _Request(post_data)
            self.aborted: list[str | None] = []
            self.continued: list[str | None] = []

        def abort(self, *, error_code: str | None = None) -> None:
            self.aborted.append(error_code)

        def continue_(self, *, post_data: str | None = None) -> None:
            self.continued.append(post_data)

    class _Page:
        def __init__(self) -> None:
            self.handler = None

        def route(self, _pattern: str, handler) -> None:
            self.handler = handler

        def unroute(self, _pattern: str) -> None:
            self.handler = None

    page = _Page()
    notes: list[str] = []
    service._install_save_mode_override(page, notes)
    assert page.handler is not None

    early = _Route(urlencode({"content": "<p>正文尚未插图</p>", "save": "0"}))
    page.handler(early)
    assert early.aborted == ["blockedbyclient"]
    assert early.continued == []
    assert service._save_override_state["consumed"] is False

    expected_refs = ["tos-cn-i-persisted", "https://p3-sign.douyinpic.com/persisted.png"]
    service._update_save_mode_override_gate(
        expected_inline_images=2,
        expected_refs=expected_refs,
        ready=True,
    )

    incomplete = _Route(
        urlencode(
            {
                "content": '<p>正文</p><img web_uri="tos-cn-i-persisted">',
                "save": "0",
            }
        )
    )
    page.handler(incomplete)
    assert incomplete.aborted == ["blockedbyclient"]
    assert service._save_override_state["consumed"] is False

    complete = _Route(
        urlencode(
            {
                "content": (
                    '<p>正文</p><img web_uri="tos-cn-i-persisted">'
                    '<img src="https://p3-sign.douyinpic.com/persisted.png">'
                ),
                "save": "0",
            }
        )
    )
    page.handler(complete)
    assert complete.aborted == []
    assert len(complete.continued) == 1
    assert "save=1" in complete.continued[0]
    assert service._save_override_state["consumed"] is True


def test_draft_autosave_without_confirmation_returns_false_for_publish_mode(
    app_settings, monkeypatch
) -> None:
    service = PublishService(app_settings, logging.getLogger("draft-save-gate-test"))

    class _Instant:
        def __init__(self, value: float) -> None:
            self.value = value

        def timestamp(self) -> float:
            return self.value

    class _Clock:
        calls = 0

        @classmethod
        def now(cls):
            cls.calls += 1
            return _Instant(0.0 if cls.calls == 1 else 100.0)

    monkeypatch.setattr(publish_service_module, "datetime", _Clock)
    monkeypatch.setattr(service, "_latest_save_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_read_draft_status_text", lambda _page: "")
    monkeypatch.setattr(service, "_capture_debug_artifacts", lambda *_args, **_kwargs: "debug")

    confirmed = service._wait_for_draft_save(
        MagicMock(),
        [],
        [],
        start_index=0,
        expect_save_mode_1=True,
    )

    assert confirmed is False
