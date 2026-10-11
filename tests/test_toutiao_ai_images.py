import base64
from io import BytesIO
import json
import logging
from types import SimpleNamespace

from PIL import Image, ImageDraw

from toutiao_content_factory.services.image_generation_service import article_image_fingerprint
from toutiao_content_factory.services.publish_service import PublishService


def test_bound_ai_illustration_precedes_one_fact_card(app_settings, tmp_path):
    run = tmp_path / "ai-run"
    publish = run / "publish"
    (publish / "images").mkdir(parents=True)
    for name in ("illustration_1.png", "infographic_1.png", "infographic_2.png"):
        Image.new("RGB", (20, 20), "teal").save(publish / "images" / name)
    (publish / "article.md").write_text("正文事实与判断。", encoding="utf-8")
    (publish / "publish_meta.json").write_text(json.dumps({"selected_title": "标题"}), encoding="utf-8")
    (publish / "image_generation_result.json").write_text(json.dumps({
        "body_image_fingerprint": article_image_fingerprint("标题", "正文事实与判断。"),
        "publish_artifacts": {"illustration": "illustration_1.png", "infographics": ["infographic_1.png", "infographic_2.png"]}}), encoding="utf-8")
    service = PublishService(app_settings, logging.getLogger("ai-inline"))
    assert [p.name for p in service._resolve_toutiao_inline_image_assets(run, [])] == ["illustration_1.png", "infographic_1.png"]
    (publish / "article.md").write_text("正文已修订。", encoding="utf-8")
    assert service._resolve_toutiao_inline_image_assets(run, []) == []


def test_cloud_free_illustration_retains_artwork_without_cover_typesetting(tmp_path, monkeypatch):
    from toutiao_content_factory.services import wechat_cloud_cover
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "fixture-token")
    monkeypatch.delenv("WECHAT_RUN_DEADLINE_EPOCH", raising=False)
    original = Image.new("RGB", (600, 600), "white")
    ImageDraw.Draw(original).rectangle((100, 150, 500, 450), fill="teal")
    buf = BytesIO()
    original.save(buf, "PNG")
    calls = []
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, **kw):
            calls.append(kw["json"])
            return SimpleNamespace(status_code=200, json=lambda: {"success": True, "result": {"image": base64.b64encode(buf.getvalue()).decode()}})
    monkeypatch.setattr(wechat_cloud_cover.httpx, "Client", Client)
    monkeypatch.setattr(wechat_cloud_cover, "_title_lines", lambda *a: (_ for _ in ()).throw(AssertionError("No cover title typesetting")))
    output = tmp_path / "micro/ai_source.png"
    assert wechat_cloud_cover.generate_cloud_cover("主题", output, prompt_override="A clean conceptual illustration. No text.", illustration_only=True) == str(output)
    assert calls == [{"prompt": "A clean conceptual illustration. No text.", "steps": 8}]
    with Image.open(output) as artwork:
        assert artwork.size == original.size and artwork.tobytes() == original.tobytes()
