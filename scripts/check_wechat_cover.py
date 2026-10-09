"""Exercise the scheduled image service without logging in or saving an article."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.image_generation_service import ImageGenerationService, _format_api_error


def main() -> int:
    started = time.time()
    # Validation shares a five-minute window and never resets daily counters.
    os.environ["WECHAT_RUN_DEADLINE_EPOCH"] = str(started + 300)
    settings = load_settings(ROOT, account_key="default")
    settings.platform = "wechat"
    directory = settings.automation_dir / "wechat_cover_check"
    directory.mkdir(parents=True, exist_ok=True)
    # Each check has its own outputs; an old successful image cannot mask failure.
    run = directory / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    publish = run / settings.review.publish_dirname
    publish.mkdir(parents=True)
    article = (ROOT / "tests/fixtures/wechat_cover_article.md").read_text(encoding="utf-8")
    title, body = article.split("\n", 1)
    (publish / "article.md").write_text(body.strip(), encoding="utf-8")
    (publish / "publish_meta.json").write_text(json.dumps({
        "selected_title": title.lstrip("# ").strip(),
        "approval_mode": "pending_manual_review",
    }, ensure_ascii=False), encoding="utf-8")
    report = {"publication_attempted": False, "draft_saving_attempted": False,
              "run_dir": str(run), "status": "failed"}
    try:
        service = ImageGenerationService(settings, logging.getLogger("wechat_cover_check"))
        if urlsplit(settings.image_generation.model_base_url).hostname in {"www.fhl.mom", "fhl.mom"}:
            client = service._get_image_api_client()
            # This provider's public key-usage page documents GET /v1/usage.
            # Keep only balance/quota data, never the key or user identity.
            usage = client.get("usage", cast_to=dict, options={"timeout": 20})
            report["relay_usage"] = {k: usage[k] for k in
                ("balance", "mode", "status", "isValid", "quota", "subscription") if k in usage}
            models = [item.id for item in client.models.list()]
            report["image_models"] = [model for model in models if "image" in model.lower()]
            if usage.get("isValid") is False or usage.get("status") in {"quota_exhausted", "expired"}:
                raise RuntimeError("Relay key is expired or quota exhausted")
            if usage.get("balance") is not None and float(usage["balance"]) <= 0 and not usage.get("subscription"):
                raise RuntimeError("Relay wallet has no balance; no paid generation attempted")
            if settings.image_generation.model_name not in models:
                raise RuntimeError("Configured image model is not available to this relay key")
        exports = service.generate_for_run(run, include_infographics=False)
        result = json.loads(exports["image_generation_result"].read_text(encoding="utf-8"))
        if not result.get("api_succeeded"):
            raise RuntimeError("Cover check requires a successful image API response")
        report.update(status="success", model=result["model"],
                      cover_style=result["cover_style"], cover_path=str(exports["cover_image"]))
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error_detail=_format_api_error(exc))
    report["processing_seconds"] = round(time.time() - started, 3)
    (directory / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("Cover API check: **" + report["status"] + "**. No WeChat draft or publication attempted.\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
