"""Render an archived article's body-image preview without login or publication."""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sys
import argparse
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.image_generation_service import ImageGenerationService
from toutiao_content_factory.services.publish_service import PublishService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["render", "inspect-upload", "upload-preview"], default="render")
    args = parser.parse_args()
    settings = load_settings(ROOT, account_key="robot_notes")
    # This verification has no API credentials, paid calls, or publishing.
    settings.image_generation = replace(settings.image_generation, backend="frontend", max_generated_assets=2)
    destination = ROOT / "output/automation/toutiao_body_image_check"
    run = destination / "run"
    publish = run / settings.review.publish_dirname
    publish.mkdir(parents=True, exist_ok=True)
    if args.mode in {"inspect-upload", "upload-preview"}:
        from playwright.sync_api import sync_playwright
        service = PublishService(settings, logging.getLogger("upload-inspection"))
        with sync_playwright() as playwright:
            browser = service._launch_persistent_browser(playwright, profile_dir=service._resolve_profile_dir())
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                url = "https://mp.toutiao.com/profile_v4/weitoutiao/publish"
                service._ensure_browser_session_ready(browser, page, url)
                service._goto_publish_page(page, url)
                page.wait_for_timeout(3000)
                service._check_toutiao_login(page, [])
                upload_events = []
                def capture(response):
                    if response.request.method != "POST":
                        return
                    try:
                        payload = response.json()
                        data = payload.get("data", {}) if isinstance(payload, dict) else {}
                        upload_events.append({"path": urlsplit(response.url).path, "status": response.status,
                          "code": payload.get("code", payload.get("err_no")) if isinstance(payload, dict) else None,
                          "keys": list(payload) if isinstance(payload, dict) else [],
                          "data_keys": list(data) if isinstance(data, dict) else [],
                          "image_reference_count": len(service._weitoutiao_image_references(data))})
                    except Exception:
                        pass
                page.on("response", capture)
                upload_summary = None
                upload_error = None
                if args.mode == "upload-preview":
                    article = (ROOT / "tests/fixtures/toutiao_body_image_article.md").read_text(encoding="utf-8")
                    content = "\n\n".join(ImageGenerationService._article_paragraphs(article)[:4])
                    asset = ImageGenerationService(settings, logging.getLogger("micro-preview")).generate_weitoutiao_image(run, content)
                    try:
                        upload_summary = service._try_apply_weitoutiao_image(page, asset, [])
                    except Exception as exc:
                        upload_error = type(exc).__name__
                inspection = page.evaluate("""() => ({
                  files: Array.from(document.querySelectorAll('input[type=file]')).map(e => ({
                    accept: e.accept, multiple: e.multiple, name: e.name,
                    id: e.id, class: e.className,
                    ancestors: [e.parentElement, e.parentElement?.parentElement, e.parentElement?.parentElement?.parentElement].map(a => a?.className || '')
                  })),
                  imageControls: Array.from(document.querySelectorAll('[class]')).filter(e => /upload|image|picture|photo/.test(e.className) && e.children.length < 3).slice(0,60).map(e => ({tag: e.tagName, class: e.className, text: (e.innerText || '').slice(0,45)}))
                })""")
                inspection.update(publication_attempted=False, upload_attempted=args.mode == "upload-preview",
                                  upload_events=upload_events, upload_error=upload_error, upload_summary=upload_summary,
                                  image_state=service._weitoutiao_image_state(page))
                (destination / "upload-inspection.json").write_text(json.dumps(inspection, ensure_ascii=False, indent=2), encoding="utf-8")
                page.screenshot(path=str(destination / "micro-editor.png"), full_page=False)
                print(json.dumps({"mode": args.mode, "publication_attempted": False,
                    "image_count": (upload_summary or {}).get("inserted_count", 0), "upload_error": upload_error}, ensure_ascii=False))
                if args.mode == "upload-preview" and (upload_summary or {}).get("inserted_count") != 1:
                    raise RuntimeError("Cloud micro-post image upload preview was not confirmed")
            finally:
                browser.close()
        return
    article = (ROOT / "tests/fixtures/toutiao_body_image_article.md").read_text(encoding="utf-8")
    title = "高速移动充电站：储电量、同时服务和累计服务分别说明什么"
    (publish / "article.md").write_text(article, encoding="utf-8")
    (publish / "publish_meta.json").write_text(json.dumps({"selected_title": title}, ensure_ascii=False), encoding="utf-8")
    logger = logging.getLogger("toutiao-body-image-check")
    generator = ImageGenerationService(settings, logger)
    try:
        exports = generator.generate_for_run(run, include_infographics=True)
        report = json.loads(exports["image_generation_result"].read_text(encoding="utf-8"))
        assets = PublishService(settings, logger)._resolve_toutiao_inline_image_assets(run, [])
        micro_content = "\n\n".join(generator._article_paragraphs(article)[:4])
        micro_asset = generator.generate_weitoutiao_image(run, micro_content)
        if micro_asset is None:
            raise RuntimeError("Micro-post image preview was not generated")
        if len(assets) != 2 or report["api_attempted"] is not False:
            raise RuntimeError("Body-image preview did not produce two bound local-rendered images")
        result = {"status": "preview_ready", "image_count": len(assets), "backend": report["infographic_backend"],
                  "api_attempted": False, "publication_attempted": False, "login_attempted": False,
                  "article_fixture": "archived_20261008", "body_image_fingerprint": report["body_image_fingerprint"]}
        (destination / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
                handle.write("已为归档稿件生成两张正文配图，仅验收图片，不登录、不发表、不调用图片API。\n")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if generator._frontend_renderer is not None:
            generator._frontend_renderer.close()


if __name__ == "__main__":
    main()
