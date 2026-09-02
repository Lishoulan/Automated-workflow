import sys, os, json, time, traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

ACCOUNT_KEY = os.environ.get("ACCOUNT_KEY", "default")

lock_file = PROJECT_ROOT / "output" / ".publish_session.lock"
if lock_file.exists():
    try: lock_file.unlink()
    except: pass
os.system("taskkill /f /im msedge.exe >nul 2>nul")
os.system("taskkill /f /im chrome.exe >nul 2>nul")

import logging
log_path = PROJECT_ROOT / "logs" / "scheduled" / ("pipe_" + ACCOUNT_KEY + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(str(log_path), encoding="utf-8")])
logger = logging.getLogger("standalone")

os.environ.setdefault("TOUTIAO_PUBLISH_CHANNEL", "")
os.environ.setdefault("TOUTIAO_PUBLISH_HEADLESS", "1")
os.environ.setdefault("WECHAT_ENABLED", "1")
os.environ.setdefault("WECHAT_AUTO_PUBLISH", "1")
os.environ["WECHAT_EDITOR_135_ENABLED"] = "0"
os.environ.setdefault("TOUTIAO_AUTO_PUBLISH", "1")
os.environ.setdefault("TOUTIAO_PUBLISH_ENABLED", "1")

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.app_service import ContentFactoryAppService

logger.info("=== Pipe Start === Account=%s Time=%s", ACCOUNT_KEY, datetime.now().isoformat())
try:
    settings = load_settings(PROJECT_ROOT, account_key=ACCOUNT_KEY)
    service = ContentFactoryAppService(settings, logger)
    logger.info("[1] Topics...")
    exports = service.collect_topics()
    logger.info("[2] Content generation...")
    content_exports = service.auto_run(topic_title=None, collect_if_missing=False, force_collect=False, fast_mode=True)
    logger.info("Content OK")
    runs_root = settings.output_dir / settings.automation.archive_dirname
    candidates = sorted([d for d in runs_root.iterdir() if d.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates: logger.error("No run dir!"); sys.exit(1)
    run_dir = candidates[0]
    logger.info("[3] Run: %s", run_dir.name)
    logger.info("[4] Approve...")
    service.approve_run(str(run_dir), selected_title_index=1, review_notes="Auto-approved")
    logger.info("Approved OK")
    logger.info("[5] Generate cover images...")
    try:
        from toutiao_content_factory.services.image_generation_service import ImageGenerationService
        img_svc = ImageGenerationService(settings, logger)
        img_svc.generate_for_run(run_dir, include_infographics=True)
        logger.info("Covers OK")
    except Exception as e:
        logger.warning("Covers skipped: %s", e)
    logger.info("[6] Toutiao publish...")
    try:
        r = service.publish_run(str(run_dir), draft_only=False)
        logger.info("Toutiao OK: %s", list(r.keys()))
    except Exception as e:
        logger.error("Toutiao failed: %s", e)
        traceback.print_exc()
    if os.environ.get("WECHAT_ENABLED") == "1":
        logger.info("[7] WeChat...")
        try:
            service.publish_wechat_run(str(run_dir), submit=True)
            logger.info("WeChat OK")
        except Exception as e:
            logger.warning("WeChat skipped: %s", e)
    logger.info("=== Pipeline OK ===")
except Exception as e:
    logger.error("FAILED: %s", e)
    traceback.print_exc()
    sys.exit(1)
