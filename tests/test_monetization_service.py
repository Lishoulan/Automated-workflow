from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from toutiao_content_factory.services.monetization_service import MonetizationService


def _build_settings(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    output_dir = tmp_path / "output"
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        project_root=project_root,
        output_dir=output_dir,
        brand_name="测试品牌",
    )


def test_append_cta_only_once(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = MonetizationService(settings, logging.getLogger("test"))

    article = "这是一篇正文。"
    first = service.append_cta(article)
    second = service.append_cta(first)

    assert "## 延伸服务" in first
    assert second.count("## 延伸服务") == 1


def test_export_assets_creates_files(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = MonetizationService(settings, logging.getLogger("test"))

    exported = service.export_assets()

    assert Path(exported["lead_magnet"]).exists()
    assert Path(exported["service_menu"]).exists()
    assert Path(exported["sales_playbook"]).exists()
