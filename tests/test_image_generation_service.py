from __future__ import annotations

import base64
from dataclasses import replace
from io import BytesIO
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from PIL import Image

from toutiao_content_factory.services.image_generation_service import (
    ImageGenerationAPIUnavailable,
    ImageGenerationService,
    _format_api_error,
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
    assert payload["creative_score"]["cover"]["width"] == 900
    assert (run_dir / "publish" / "title_cover_score.json").exists()


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
    assert "provider rejected the request" in payload["api_error_detail"]


def test_api_error_detail_redacts_credentials() -> None:
    error = ImageGenerationAPIUnavailable(
        "image API request failed: api_key=secret-value"
    )

    detail = _format_api_error(error)

    assert "secret-value" not in detail
    assert "api_key=<redacted>" in detail


def test_api_error_detail_redacts_provider_key_echo() -> None:
    detail = _format_api_error(RuntimeError("Incorrect API key provided: sk-private****suffix."))
    assert "sk-private" not in detail and "suffix" not in detail
    assert "<redacted-key>" in detail


def test_image_api_key_resolution_accepts_openai_key_fallback(app_settings, monkeypatch) -> None:
    service = ImageGenerationService(app_settings, logging.getLogger("image-key-test"))
    app_settings.image_generation = replace(app_settings.image_generation, model_api_key="")
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")

    assert service._resolve_image_api_key() == "fallback-key"


def test_wechat_cover_uses_magazine_prompt_and_preserves_generated_layout(app_settings, tmp_path, monkeypatch):
    run = _prepare_run(tmp_path)
    app_settings.platform = "wechat"
    title = "奇瑞8月出口增52.1%，星途前7月销量降39.2%：看懂两种口径"
    (run / "publish/publish_meta.json").write_text(json.dumps({"selected_title": title}, ensure_ascii=False), encoding="utf-8")
    service = ImageGenerationService(app_settings, logging.getLogger("wechat-cover-test"))
    app_settings.image_generation = replace(app_settings.image_generation, backend="image2", cover_size="1536x656")
    calls = []
    def generate(prompt, path, size):
        calls.append((prompt, size))
        Image.new("RGB", (1536, 656), (240, 230, 215)).save(path)
    monkeypatch.setattr(service, "_generate_api_image", generate)
    monkeypatch.setattr(service, "_compose_api_cover", lambda *a: pytest.fail("Must preserve magazine typography"))
    exported = service.generate_for_run(run, include_infographics=False)
    with Image.open(exported["cover_image"]) as image:
        assert image.size == (1200, 511)
        assert image.getpixel((80, 150)) == (240, 230, 215)
    assert len(calls) == 1 and calls[0][1] == "1536x656"
    prompt = calls[0][0]
    assert json.dumps(title, ensure_ascii=False) in prompt
    assert "暖象牙白" in prompt and "不得所有主题都画成汽车港口" in prompt
    assert "30分钟" not in prompt and "196,984" not in prompt
    assert not list((run / "publish/images").glob("infographic_*.png"))
    result = json.loads(exported["image_generation_result"].read_text(encoding="utf-8"))
    assert result["cover_style"] == "warm_editorial" and result["api_succeeded"] is True


def test_wechat_image_credentials_do_not_reuse_text_model_key(app_settings, monkeypatch):
    app_settings.platform = "wechat"
    app_settings.image_generation = replace(app_settings.image_generation, model_api_key="")
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "text-model-only")
    service = ImageGenerationService(app_settings, logging.getLogger("wechat-cover-key"))
    assert service._resolve_image_api_key() == ""
    with pytest.raises(ImageGenerationAPIUnavailable, match="not configured"):
        service._get_image_api_client()


def test_wechat_api_failure_uses_one_real_model_fallback(app_settings, tmp_path, monkeypatch):
    from toutiao_content_factory.services import wechat_cloud_cover
    run = _prepare_run(tmp_path)
    app_settings.platform = "wechat"
    app_settings.image_generation = replace(app_settings.image_generation, backend="image2", fallback_to_renderer=False)
    service = ImageGenerationService(app_settings, logging.getLogger("wechat-free-fallback"))
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "fixture-token")
    monkeypatch.delenv("WECHAT_FREE_COVER_ONLY", raising=False)
    calls = []
    def paid(*a):
        calls.append("paid")
        raise ImageGenerationAPIUnavailable("Relay quota exhausted")
    def free(title, output):
        calls.append("free")
        Image.new("RGB", (1200, 511), (230, 220, 210)).save(output)
    monkeypatch.setattr(service, "_generate_api_image", paid)
    monkeypatch.setattr(wechat_cloud_cover, "generate_cloud_cover", free)
    exported = service.generate_for_run(run, include_infographics=False)
    result = json.loads(exported["image_generation_result"].read_text(encoding="utf-8"))
    assert calls == ["paid", "free"]
    assert result["cover_backend"] == "cloudflare_flux" and result["ai_generated"] is True
    assert result["api_succeeded"] is False and result["used_fallback"] is True
    assert result["free_api_attempted"] is True and result["free_api_succeeded"] is True
    assert "quota exhausted" in result["api_error_detail"]


def test_wechat_free_only_never_calls_paid_provider(app_settings, tmp_path, monkeypatch):
    from toutiao_content_factory.services import wechat_cloud_cover
    run = _prepare_run(tmp_path)
    app_settings.platform = "wechat"
    service = ImageGenerationService(app_settings, logging.getLogger("wechat-free-only"))
    monkeypatch.setenv("WECHAT_FREE_COVER_ONLY", "1")
    monkeypatch.setattr(service, "_generate_api_image", lambda *a: pytest.fail("Must not spend relay credits"))
    monkeypatch.setattr(wechat_cloud_cover, "generate_cloud_cover",
        lambda title, path: Image.new("RGB", (1200, 511), (240, 230, 220)).save(path))
    exported = service.generate_for_run(run, include_infographics=False)
    result = json.loads(exported["image_generation_result"].read_text(encoding="utf-8"))
    assert result["ai_generated"] is True and result["api_attempted"] is False


@pytest.mark.parametrize("free_only", [True, False])
def test_failed_cloud_fallback_retains_each_provider_diagnostic(app_settings, tmp_path, monkeypatch, free_only):
    from toutiao_content_factory.services import wechat_cloud_cover
    run = _prepare_run(tmp_path)
    app_settings.platform = "wechat"
    app_settings.image_generation = replace(app_settings.image_generation, backend="image2", fallback_to_renderer=False)
    service = ImageGenerationService(app_settings, logging.getLogger("failed-cloud-cover"))
    monkeypatch.setenv("WECHAT_FREE_COVER_ONLY", "1" if free_only else "0")
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "fixture-token")
    calls = []
    def paid(*args):
        calls.append("paid")
        raise ImageGenerationAPIUnavailable("Relay unavailable sk-REDACTME")
    def free(*args):
        calls.append("free")
        raise RuntimeError("Cloud quota exhausted cfut_REDACTMETOO")
    monkeypatch.setattr(service, "_generate_api_image", paid)
    monkeypatch.setattr(wechat_cloud_cover, "generate_cloud_cover", free)
    with pytest.raises(RuntimeError):
        service.generate_for_run(run, include_infographics=False)
    serialized = (run / "publish/image_generation_result.json").read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert calls == (["free"] if free_only else ["paid", "free"])
    assert report["free_api_attempted"] is True and report["free_api_succeeded"] is False
    assert report["api_attempted"] is not free_only
    assert "Cloud quota exhausted" in report["free_api_error_detail"]
    assert "REDACTME" not in serialized


@pytest.mark.parametrize("response_kind", ["success", "blank", "quota", "invalid", "unconfirmed"])
def test_cloud_cover_validates_response_and_preserves_full_title(tmp_path, monkeypatch, response_kind):
    import base64
    from io import BytesIO
    from PIL import ImageDraw
    from toutiao_content_factory.services import wechat_cloud_cover
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "private-fixture-token")
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + 170))
    title = "奇瑞8月出口增52.1%，星途前7月销量降39.2%：看懂两种口径"
    output = tmp_path / "images/cover.png"
    image = Image.new("RGB", (1024, 1024), "white")
    if response_kind != "blank":
        ImageDraw.Draw(image).rectangle((300, 300, 900, 850), fill="black")
    data = BytesIO()
    image.save(data, "PNG")
    payload = {"success": response_kind != "unconfirmed", "result": {
        "image": base64.b64encode(data.getvalue()).decode() if response_kind != "invalid" else ""}}
    calls = []
    class FakeClient:
        def __init__(self, *, timeout, follow_redirects):
            assert 40 < timeout.read < 51 and timeout.connect == 10
            assert follow_redirects is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, headers, json):
            calls.append(url)
            assert url == "https://api.cloudflare.com/client/v4/accounts/" + "a" * 32 + "/ai/run/" + wechat_cloud_cover.MODEL
            assert headers == {"Authorization": "Bearer private-fixture-token"}
            assert json["steps"] == 8 and title in json["prompt"]
            return SimpleNamespace(status_code=429 if response_kind == "quota" else 200, json=lambda: payload)
    monkeypatch.setattr(wechat_cloud_cover.httpx, "Client", FakeClient)
    if response_kind != "success":
        with pytest.raises((RuntimeError, OSError)):
            wechat_cloud_cover.generate_cloud_cover(title, output)
        assert not output.exists()
    else:
        assert wechat_cloud_cover.generate_cloud_cover(title, output) == str(output)
        with Image.open(output) as image:
            assert image.size == (1200, 511)
        prompt = (output.parent / "cover_free_prompt.txt").read_text(encoding="utf-8")
        assert title in prompt and "No text" in prompt
        assert "private-fixture-token" not in prompt
        assert (output.parent / "cover_free_ai.png").is_file()
    assert len(calls) == 1


@pytest.mark.parametrize("blocked", ["missing_token", "invalid_account", "budget"])
def test_cloud_cover_refuses_invalid_configuration_or_insufficient_budget(tmp_path, monkeypatch, blocked):
    from toutiao_content_factory.services import wechat_cloud_cover
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32 if blocked != "invalid_account" else "../invalid")
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "fixture-token" if blocked != "missing_token" else "")
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + (125 if blocked == "budget" else 300)))
    monkeypatch.setattr(wechat_cloud_cover.httpx, "Client", lambda **kwargs: pytest.fail("Must not call API"))
    with pytest.raises(RuntimeError):
        wechat_cloud_cover.generate_cloud_cover("真实标题", tmp_path / "cover.png")


def test_strict_image_failure_preserves_diagnostics_without_local_fallback(app_settings, tmp_path, monkeypatch):
    run = _prepare_run(tmp_path)
    app_settings.platform = "wechat"
    app_settings.image_generation = replace(app_settings.image_generation, backend="image2", fallback_to_renderer=False)
    service = ImageGenerationService(app_settings, logging.getLogger("wechat-cover-failed"))
    def fail(*a):
        raise ImageGenerationAPIUnavailable("provider unavailable")
    monkeypatch.setattr(service, "_generate_api_image", fail)
    monkeypatch.setattr(service, "_get_frontend_renderer", lambda: pytest.fail("No silent fallback"))
    with pytest.raises(ImageGenerationAPIUnavailable):
        service.generate_for_run(run, include_infographics=False)
    report = json.loads((run / "publish/image_generation_result.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["api_succeeded"] is False
    assert report["cover_style"] == "warm_editorial"
    assert not (run / "publish/images/cover.png").exists()
    assert (run / "publish/article.md").exists()


@pytest.mark.parametrize("remaining,expected", [(185, 65), (125, None)])
def test_wechat_image_call_respects_shared_deadline(app_settings, monkeypatch, remaining, expected):
    import openai
    app_settings.platform = "wechat"
    app_settings.image_generation = replace(app_settings.image_generation, model_api_key="image-only")
    monkeypatch.setattr(time, "time", lambda: 1000)
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(1000 + remaining))
    calls = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: calls.append(kw) or SimpleNamespace())
    service = ImageGenerationService(app_settings, logging.getLogger("wechat-cover-budget"))
    app_settings.image_generation = replace(app_settings.image_generation, request_timeout_seconds=180)
    if expected is None:
        with pytest.raises(ImageGenerationAPIUnavailable, match="budget"):
            service._get_image_api_client()
        assert calls == []
    else:
        service._get_image_api_client()
        assert calls[0]["timeout"] == expected
        assert calls[0]["max_retries"] == 0
        assert calls[0]["base_url"] == "https://api.openai.com/v1"
