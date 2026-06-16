#!/usr/bin/env python3
"""PythonAnywhere 定时任务入口脚本（纯 API 模式）

PythonAnywhere 免费版不支持 Playwright/headless browser，
因此本脚本仅运行不需要浏览器的功能：
  - 话题采集（RSS/HTML 抓取）
  - 内容生成（调用 OpenAI/DeepSeek API）
  - 内容审核与自动批准
  - 微信公众号 API 发布（需要 WECHAT_APP_ID/WECHAT_SECRET）
  - 图片生成（renderer 模式，不依赖浏览器）

头条的浏览器自动发布不在此脚本范围内。

使用方法：
  1. 在 PythonAnywhere 上克隆项目
  2. 创建虚拟环境并安装依赖
  3. 上传 .env 文件
  4. 在 PythonAnywhere Scheduled Tasks 中添加：
     cd /home/yourusername/Automated-workflow-main && python scripts/pa_daily_run.py
"""

from __future__ import annotations

import os
import platform
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 项目路径设置
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["PYTHONPATH"] = str(SRC_DIR)
os.environ["PYTHONUTF8"] = "1"

# ---------------------------------------------------------------------------
# Linux 适配
# ---------------------------------------------------------------------------
os.environ["TOUTIAO_PUBLISH_HEADLESS"] = "1"
os.environ["TOUTIAO_PUBLISH_CHANNEL"] = "chromium"
os.environ["TOUTIAO_BROWSER_EXECUTABLE"] = ""

# PythonAnywhere 无法运行浏览器，禁用浏览器发布
os.environ["TOUTIAO_PUBLISH_ENABLED"] = "0"
os.environ["TOUTIAO_AUTO_PUBLISH"] = "0"

# 微信 API 发布不需要浏览器，保持启用
# （前提是 WECHAT_APP_ID 和 WECHAT_APP_SECRET 已配置，
#   且 PythonAnywhere 的 IP 已加入微信公众号白名单）
# 如果微信 API 也不可用，设为 0
# os.environ["WECHAT_ENABLED"] = "0"
# os.environ["WECHAT_AUTO_PUBLISH"] = "0"

# ---------------------------------------------------------------------------
# 加载 .env 文件
# ---------------------------------------------------------------------------
def load_dotenv_file(env_path: Path) -> None:
    """手动解析 .env 文件（不依赖 python-dotenv）"""
    if not env_path.exists():
        print(f"[WARN] .env file not found at {env_path}")
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            # 不覆盖已设置的环境变量
            if key not in os.environ:
                os.environ[key] = value

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    load_dotenv_file(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# 日志设置
# ---------------------------------------------------------------------------
import logging

ACCOUNT_KEY = os.environ.get("TOUTIAO_ACCOUNT", "default")
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_DIR = PROJECT_ROOT / "logs" / "scheduled"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"pa_daily_{ACCOUNT_KEY}_{TIMESTAMP}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger("pa_daily_run")

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("PythonAnywhere Daily Run Started")
    logger.info("Account: %s", ACCOUNT_KEY)
    logger.info("Platform: %s / %s", platform.system(), platform.machine())
    logger.info("Python: %s", sys.version.split()[0])
    logger.info("Project: %s", PROJECT_ROOT)
    logger.info("Log: %s", LOG_FILE)
    logger.info("=" * 60)

    try:
        from toutiao_content_factory.config import load_settings
        from toutiao_content_factory.services.app_service import ContentFactoryAppService

        settings = load_settings(PROJECT_ROOT, account_key=ACCOUNT_KEY)
        app_service = ContentFactoryAppService(settings, logger)

        # Step 1: 话题采集
        logger.info("[1/5] Collecting topics...")
        topic_exports = app_service.collect_topics()
        logger.info("Topics collected: %s", list(topic_exports.keys()))

        # Step 2: 内容生成
        logger.info("[2/5] Generating content (auto-run)...")
        content_exports = app_service.auto_run(
            collect_if_missing=False,
            force_collect=False,
            fast_mode=True,
        )
        logger.info("Content generated: %s", list(content_exports.keys()))

        # Step 3: 找到最新的 run 目录
        runs_root = settings.output_dir / settings.automation.archive_dirname
        if not runs_root.exists():
            logger.error("No runs directory found!")
            return 1

        candidates = sorted(
            [d for d in runs_root.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            logger.error("No run directories found!")
            return 1

        run_dir = candidates[0]
        logger.info("[3/5] Latest run: %s", run_dir.name)

        # Step 4: 自动批准
        logger.info("[4/5] Auto-approving run...")
        try:
            approve_exports = app_service.approve_run(
                str(run_dir),
                selected_title_index=1,
                review_notes="Auto-approved by PythonAnywhere scheduled task",
            )
            logger.info("Approved: %s", list(approve_exports.keys()))
        except Exception as e:
            logger.warning("Auto-approve failed (may already be approved): %s", e)

        # Step 5: 微信 API 发布（如果启用）
        if os.environ.get("WECHAT_ENABLED") == "1":
            logger.info("[5/5] Publishing to WeChat via API...")
            try:
                wechat_exports = app_service.publish_wechat_run(
                    str(run_dir),
                    submit=os.environ.get("WECHAT_AUTO_PUBLISH") == "1",
                )
                logger.info("WeChat publish result: %s", list(wechat_exports.keys()))
            except Exception as e:
                logger.error("WeChat publish failed: %s", e)
                traceback.print_exc()
        else:
            logger.info("[5/5] WeChat publishing is disabled, skipping.")

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info("PythonAnywhere Daily Run COMPLETED in %.1f seconds", elapsed)
        logger.info("=" * 60)
        return 0

    except Exception as exc:
        elapsed = time.time() - start_time
        logger.error("=" * 60)
        logger.error("PythonAnywhere Daily Run FAILED after %.1f seconds", elapsed)
        logger.error("Error: %s", exc)
        traceback.print_exc()
        logger.error("=" * 60)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
