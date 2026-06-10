#!/usr/bin/env python3
"""云端定时执行入口脚本 — 适配 Linux 环境（GitHub Actions / PythonAnywhere）。

用法:
    python scripts/cloud_daily_run.py
    python scripts/cloud_daily_run.py --account politics_notes
    python scripts/cloud_daily_run.py --dry-run
    python scripts/cloud_daily_run.py --account default --dry-run

关键适配:
    - Playwright channel 使用 "chromium" 而非 "msedge"
    - TOUTIAO_PUBLISH_HEADLESS=1 强制无头模式
    - 浏览器 profile 持久化到 .runtime/ 目录
    - 不依赖 Windows 特有模块（msvcrt 等）
    - 不执行 Windows 特有命令（taskkill 等）
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
ENV_FILE = PROJECT_ROOT / ".env"

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
LOG_DIR = PROJECT_ROOT / "logs" / "scheduled"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("cloud_daily_run")


def _setup_logging(account_key: str) -> None:
    """配置日志输出到 stdout 和文件。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"cloud_{account_key}_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    _logger.info("日志文件: %s", log_file)


# ---------------------------------------------------------------------------
# 环境变量配置
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    """从 .env 文件加载环境变量（使用 python-dotenv 或手动解析）。"""
    if not ENV_FILE.exists():
        _logger.warning(".env 文件不存在: %s", ENV_FILE)
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
        _logger.info("已通过 python-dotenv 加载 .env")
    except ImportError:
        _logger.info("python-dotenv 不可用，使用手动解析 .env")
        _manual_load_dotenv(ENV_FILE)


def _manual_load_dotenv(env_path: Path) -> None:
    """手动解析 .env 文件，不依赖 python-dotenv。"""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        # 不覆盖已有的环境变量
        if key and key not in os.environ:
            os.environ[key] = value


def _check_and_disable_browser_platform(platform_prefix: str, profile_dir: str) -> None:
    """检查浏览器 profile 是否存在，如果不存在则禁用该平台的浏览器发布。

    云端环境没有浏览器登录 session 时，发布预检查会失败导致整个流程中断。
    只有当 profile 目录存在且包含有效数据时才允许启用浏览器发布。
    """
    enabled_key = f"{platform_prefix}_ENABLED"
    auto_publish_key = f"{platform_prefix}_AUTO_PUBLISH"
    profile_path = PROJECT_ROOT / profile_dir

    # 检查 profile 目录是否存在且非空
    has_profile = profile_path.exists() and any(profile_path.iterdir()) if profile_path.exists() else False

    if has_profile:
        _logger.info("  %s: 浏览器 profile 存在 (%s)，保持启用状态", platform_prefix, profile_dir)
        return

    # 没有 profile，强制禁用
    if os.environ.get(enabled_key, "0") == "1":
        _logger.warning(
            "  %s: 浏览器 profile 不存在 (%s)，强制禁用该平台发布（无登录 session）",
            platform_prefix, profile_dir,
        )
        os.environ[enabled_key] = "0"
        os.environ[auto_publish_key] = "0"
    else:
        _logger.info("  %s: 已禁用（无浏览器 profile）", platform_prefix)


def _is_auth_error() -> bool:
    """检查最近的 app.log 是否包含 PlatformAuthError。"""
    app_log = PROJECT_ROOT / "logs" / "app.log"
    if not app_log.exists():
        return False
    try:
        tail_lines = app_log.read_text(encoding="utf-8", errors="ignore")[-2000:]
        return "PlatformAuthError" in tail_lines or "blocked_platforms=" in tail_lines
    except Exception:
        return False


# 平台前缀到环境变量名的映射
_PLATFORM_ENV_MAP = {
    "wechat": "WECHAT",
    "toutiao": "TOUTIAO",
    "douyin": "DOUYIN",
}


def _disable_blocking_platforms() -> None:
    """从 app.log 中提取 blocked_platforms 并禁用对应平台。"""
    app_log = PROJECT_ROOT / "logs" / "app.log"
    if not app_log.exists():
        _logger.warning("app.log 不存在，无法确定失败平台，禁用所有浏览器发布平台")
        for prefix in _PLATFORM_ENV_MAP.values():
            os.environ[f"{prefix}_ENABLED"] = "0"
            os.environ[f"{prefix}_AUTO_PUBLISH"] = "0"
        return

    try:
        tail_lines = app_log.read_text(encoding="utf-8", errors="ignore")[-4000:]
        # 提取 blocked_platforms=wechat,toutiao 或 blocked_platforms=toutiao, wechat 格式
        import re
        match = re.search(r"blocked_platforms=([^\s;]+(?:,\s*[^\s;]+)*)", tail_lines)
        if match:
            blocked = match.group(1).split(",")
            for platform in blocked:
                platform = platform.strip().lower()
                prefix = _PLATFORM_ENV_MAP.get(platform)
                if prefix:
                    _logger.warning("禁用平台: %s (登录验证失败)", platform)
                    os.environ[f"{prefix}_ENABLED"] = "0"
                    os.environ[f"{prefix}_AUTO_PUBLISH"] = "0"
        else:
            _logger.warning("无法从日志提取 blocked_platforms，禁用所有浏览器发布平台")
            for prefix in _PLATFORM_ENV_MAP.values():
                os.environ[f"{prefix}_ENABLED"] = "0"
                os.environ[f"{prefix}_AUTO_PUBLISH"] = "0"
    except Exception as exc:
        _logger.error("解析 blocked_platforms 失败: %s", exc)


def _configure_cloud_env(account_key: str) -> None:
    """设置云端运行所需的环境变量。"""
    is_linux = platform.system() == "Linux"

    # --- 浏览器配置 ---
    # Linux 上使用 chromium 而非 msedge
    browser_channel = "chromium" if is_linux else os.environ.get("TOUTIAO_PUBLISH_CHANNEL", "msedge")
    os.environ.setdefault("TOUTIAO_PUBLISH_CHANNEL", browser_channel)
    os.environ.setdefault("TOUTIAO_PUBLISH_HEADLESS", "1")
    os.environ.setdefault("TOUTIAO_BROWSER_EXECUTABLE", "")

    # 抖音浏览器同样适配
    douyin_channel = "chromium" if is_linux else os.environ.get("DOUYIN_PUBLISH_CHANNEL", "msedge")
    os.environ.setdefault("DOUYIN_PUBLISH_CHANNEL", douyin_channel)
    os.environ.setdefault("DOUYIN_PUBLISH_HEADLESS", "1")
    os.environ.setdefault("DOUYIN_BROWSER_EXECUTABLE", "")

    # --- 浏览器 profile 持久化到 .runtime/ ---
    os.environ.setdefault("TOUTIAO_BROWSER_PROFILE_DIR", ".runtime/toutiao_profile")
    os.environ.setdefault("DOUYIN_BROWSER_PROFILE_DIR", ".runtime/douyin_profile")
    os.environ.setdefault("WECHAT_EDITOR_135_PROFILE_DIR", ".runtime/editor_135_profile")

    # 确保 .runtime 目录存在
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    # --- 发布配置 ---
    os.environ.setdefault("TOUTIAO_PUBLISH_ENABLED", "1")
    os.environ.setdefault("TOUTIAO_AUTO_PUBLISH", "1")  # 云端自动发布

    # 云端浏览器登录 session 管理：
    # GitHub Actions 通过 cache 持久化 .runtime/ 目录来保持登录状态。
    # 如果 .runtime/ 下没有对应平台的 profile，说明首次运行，没有登录 session，
    # 此时必须禁用该平台的浏览器发布，否则 PlatformAuthError 会中断整个流程。
    # 用户可以通过手动上传 profile 缓存来启用浏览器发布。
    # 微信：如果启用了 135 编辑器，检查 editor_135_profile；否则检查 wechat_profile
    wechat_135_enabled = os.environ.get("WECHAT_EDITOR_135_ENABLED", "0") == "1"
    wechat_profile_dir = ".runtime/editor_135_profile" if wechat_135_enabled else ".runtime/wechat_profile"
    _check_and_disable_browser_platform("WECHAT", wechat_profile_dir)
    _check_and_disable_browser_platform("TOUTIAO", ".runtime/toutiao_profile")
    _check_and_disable_browser_platform("DOUYIN", ".runtime/douyin_profile")

    # --- 账号 ---
    os.environ.setdefault("TOUTIAO_ACCOUNT", account_key)

    # --- PYTHONUTF8 ---
    os.environ.setdefault("PYTHONUTF8", "1")

    _logger.info("云端环境变量已配置:")
    _logger.info("  TOUTIAO_PUBLISH_CHANNEL = %s", os.environ.get("TOUTIAO_PUBLISH_CHANNEL"))
    _logger.info("  TOUTIAO_PUBLISH_HEADLESS = %s", os.environ.get("TOUTIAO_PUBLISH_HEADLESS"))
    _logger.info("  TOUTIAO_BROWSER_PROFILE_DIR = %s", os.environ.get("TOUTIAO_BROWSER_PROFILE_DIR"))
    _logger.info("  DOUYIN_PUBLISH_CHANNEL = %s", os.environ.get("DOUYIN_PUBLISH_CHANNEL"))
    _logger.info("  TOUTIAO_ACCOUNT = %s", os.environ.get("TOUTIAO_ACCOUNT"))


# ---------------------------------------------------------------------------
# PYTHONPATH 配置
# ---------------------------------------------------------------------------
def _configure_pythonpath() -> None:
    """确保 PYTHONPATH 包含 src 目录。"""
    src_str = str(SRC_DIR)
    existing = os.environ.get("PYTHONPATH", "")
    if src_str not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = f"{src_str}{os.pathsep}{existing}" if existing else src_str
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
    _logger.info("PYTHONPATH 已包含: %s", src_str)


# ---------------------------------------------------------------------------
# Playwright 浏览器预检
# ---------------------------------------------------------------------------
def _ensure_playwright_browsers() -> bool:
    """检查并安装 Playwright 浏览器（如果需要）。"""
    try:
        import playwright  # noqa: F401
        _logger.info("playwright 包已安装")
    except ImportError:
        _logger.error("playwright 包未安装，请运行: pip install playwright && playwright install chromium")
        return False

    # 检查 chromium 是否已安装（通过检查 playwright driver 路径）
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        # playwright install 如果已安装会快速返回
        _logger.info("Playwright chromium 浏览器已就绪")
        return True
    except subprocess.TimeoutExpired:
        _logger.error("Playwright chromium 安装超时")
        return False
    except Exception as exc:
        _logger.error("Playwright chromium 安装异常: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="云端定时执行入口脚本（适配 Linux / GitHub Actions / PythonAnywhere）",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="账号 key（对应 config.yaml 中的 accounts 配置），默认使用 TOUTIAO_ACCOUNT 或 default",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，不实际调用模型和发布",
    )
    parser.add_argument(
        "--skip-browser-check",
        action="store_true",
        help="跳过 Playwright 浏览器安装检查（已确认浏览器就绪时使用）",
    )
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help="快速模式，跳过部分非必要步骤",
    )
    return parser


def _run_extra_modules(account_key: str) -> None:
    """运行 AiToEarn 集成模块（engagement / monetization / matrix）。

    在主 pipeline 完成后执行，各模块独立运行，互不影响。
    """
    main_py = str(PROJECT_ROOT / "main.py")

    # --- 互动 Agent ---
    if os.environ.get("ENGAGEMENT_ENABLED", "0") == "1":
        _logger.info("运行互动 Agent...")
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--engagement", "--account", account_key],
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=600,
            )
            _logger.info("互动 Agent 完成 (exit_code=%d)", result.returncode)
        except Exception as exc:
            _logger.warning("互动 Agent 执行异常（非致命）: %s", exc)

        # 评论挖掘
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--engagement-mine-topics", "--account", account_key],
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=300,
            )
            _logger.info("评论挖掘完成 (exit_code=%d)", result.returncode)
        except Exception as exc:
            _logger.warning("评论挖掘执行异常（非致命）: %s", exc)
    else:
        _logger.info("互动 Agent 未启用，跳过")

    # --- 变现同步 ---
    if os.environ.get("MONETIZATION_ENABLED", "0") == "1":
        _logger.info("运行变现数据同步...")
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--monetization-sync"],
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=300,
            )
            _logger.info("变现同步完成 (exit_code=%d)", result.returncode)
        except Exception as exc:
            _logger.warning("变现同步执行异常（非致命）: %s", exc)
    else:
        _logger.info("变现系统未启用，跳过")

    # --- 矩阵看板 ---
    if os.environ.get("MATRIX_ENABLED", "0") == "1":
        _logger.info("输出矩阵运营看板...")
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--matrix-dashboard"],
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=120,
            )
            _logger.info("矩阵看板输出完成 (exit_code=%d)", result.returncode)
        except Exception as exc:
            _logger.warning("矩阵看板执行异常（非致命）: %s", exc)
    else:
        _logger.info("矩阵运营未启用，跳过")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    account_key = args.account or os.environ.get("TOUTIAO_ACCOUNT", "default")

    # 1. 配置日志
    _setup_logging(account_key)

    _logger.info("=" * 60)
    _logger.info("云端定时执行开始")
    _logger.info("  时间: %s", datetime.now().isoformat())
    _logger.info("  平台: %s / %s", platform.system(), platform.machine())
    _logger.info("  Python: %s", sys.version.split()[0])
    _logger.info("  项目根目录: %s", PROJECT_ROOT)
    _logger.info("  账号: %s", account_key)
    _logger.info("  试运行: %s", args.dry_run)
    _logger.info("=" * 60)

    # 2. 加载 .env
    _load_dotenv()

    # 3. 配置云端环境变量
    _configure_cloud_env(account_key)

    # 4. 配置 PYTHONPATH
    _configure_pythonpath()

    # 5. Playwright 浏览器预检
    if not args.skip_browser_check:
        if not _ensure_playwright_browsers():
            _logger.warning("Playwright 浏览器未就绪，继续执行（发布步骤可能会失败）")

    # 6. 构建 main.py 命令参数
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "main.py"),
        "--daily-run",
    ]
    if account_key and account_key != "default":
        cmd.extend(["--account", account_key])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.fast_mode:
        cmd.append("--fast-mode")

    _logger.info("执行命令: %s", " ".join(cmd))

    # 7. 执行（支持 PlatformAuthError 自动降级重试）
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=1800,  # 30 分钟超时
            )
            exit_code = result.returncode
            if exit_code == 0:
                _logger.info("云端定时执行成功完成 (exit_code=%d)", exit_code)
                # 主 pipeline 成功后运行 AiToEarn 集成模块
                _run_extra_modules(account_key)
                return exit_code

            # 检查是否是 PlatformAuthError，如果是则禁用失败平台并重试
            if attempt < max_attempts and _is_auth_error():
                _logger.warning("检测到平台登录验证失败，自动禁用失败平台并重试...")
                _disable_blocking_platforms()
                continue

            _logger.error("云端定时执行失败 (exit_code=%d)", exit_code)
            return exit_code
        except subprocess.TimeoutExpired:
            _logger.error("云端定时执行超时（30 分钟）")
            return 1
        except KeyboardInterrupt:
            _logger.warning("用户中断执行")
            return 130
        except Exception as exc:
            _logger.error("云端定时执行异常: %s", exc)
            _logger.error(traceback.format_exc())
            return 1

    _logger.error("云端定时执行在 %d 次尝试后仍然失败", max_attempts)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
