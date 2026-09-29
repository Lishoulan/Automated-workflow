from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from toutiao_content_factory.services.monetization_service import MonetizationService


def _build_settings(
    tmp_path: Path,
    *,
    monetization_enabled: bool = False,
    content_domain: str = "industry",
) -> SimpleNamespace:
    project_root = tmp_path / "project"
    output_dir = tmp_path / "output"
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        project_root=project_root,
        output_dir=output_dir,
        brand_name="测试品牌",
        monetization_enabled=monetization_enabled,
        content_domain=content_domain,
    )


def test_append_cta_only_once(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path, monetization_enabled=True)
    service = MonetizationService(settings, logging.getLogger("test"))

    article = "这是一篇正文。"
    first = service.append_cta(article)
    second = service.append_cta(first)

    assert "## 延伸服务" in first
    assert second.count("## 延伸服务") == 1


def test_append_cta_disabled_by_default(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path, monetization_enabled=False)
    service = MonetizationService(settings, logging.getLogger("test"))

    article = "这是一篇正文。"
    rendered = service.append_cta(article)

    assert rendered.strip() == article


def test_append_cta_is_skipped_for_narrative_account(tmp_path: Path) -> None:
    settings = _build_settings(
        tmp_path,
        monetization_enabled=True,
        content_domain="narrative",
    )
    service = MonetizationService(settings, logging.getLogger("test"))

    rendered = service.append_cta("这是一篇社会故事正文。")

    assert rendered.strip() == "这是一篇社会故事正文。"
    assert "## 延伸服务" not in rendered


def test_export_assets_creates_files(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = MonetizationService(settings, logging.getLogger("test"))

    exported = service.export_assets()

    assert Path(exported["lead_magnet"]).exists()
    assert Path(exported["service_menu"]).exists()
    assert Path(exported["sales_playbook"]).exists()


def test_get_cta_payload_prefers_platform_block(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    config_path = settings.project_root / "config" / "company_revenue.yaml"
    config_path.write_text(
        "\n".join(
            [
                "cta_blocks:",
                "  default:",
                "    heading: 默认标题",
                "    bullets:",
                "      - 默认要点",
                "  wechat:",
                "    heading: 微信标题",
                "    bullets:",
                "      - 微信要点",
                "    closing: 微信收尾",
            ]
        ),
        encoding="utf-8",
    )
    service = MonetizationService(settings, logging.getLogger("test"))

    payload = service.get_cta_payload(platform_key="wechat")

    assert payload["heading"] == "微信标题"
    assert payload["bullets"] == ["微信要点"]
    assert payload["closing"] == "微信收尾"
