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
import json
import logging
import os
import platform
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

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

    # --- 浏览器 profile 持久化到 .runtime/ ---
    os.environ.setdefault("TOUTIAO_BROWSER_PROFILE_DIR", ".runtime/toutiao_profile")
    os.environ.setdefault("WECHAT_EDITOR_135_PROFILE_DIR", ".runtime/editor_135_profile")

    # 确保 .runtime 目录存在
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    # --- 发布配置 ---
    # 云端只生成内容，不发布。发布留给本地 one_click_publish.ps1
    # 如果环境变量已设置为 "0"（来自 workflow），setdefault 不会覆盖
    os.environ.setdefault("TOUTIAO_PUBLISH_ENABLED", "0")
    os.environ.setdefault("TOUTIAO_AUTO_PUBLISH", "0")
    os.environ.setdefault("TOUTIAO_DRAFT_ONLY", "1")
    os.environ.setdefault("WECHAT_AUTO_PUBLISH", "0")

    # 云端浏览器登录 session 管理：
    # GitHub Actions 通过 cache 持久化 .runtime/ 目录来保持登录状态。
    # 如果 .runtime/ 下没有对应平台的 profile，说明首次运行，没有登录 session，
    # 此时必须禁用该平台的浏览器发布，否则 PlatformAuthError 会中断整个流程。
    # 用户可以通过手动上传 profile 缓存来启用浏览器发布。
    # 微信：如果启用了 135 编辑器，检查 editor_135_profile；否则检查 wechat_profile
    wechat_135_enabled = os.environ.get("WECHAT_EDITOR_135_ENABLED", "0") == "1"
    wechat_profile_dir = ".runtime/editor_135_profile" if wechat_135_enabled else ".runtime/wechat_profile"
    _check_and_disable_browser_platform("WECHAT", wechat_profile_dir)

    # 用 account-aware 路径检查 toutiao profile
    # default 账号用 .runtime/toutiao_profile,非 default 账号用 .runtime/accounts/{account}/toutiao_profile
    # 避免硬编码 default 路径导致对 robot_notes 等账号的检查形同虚设
    try:
        from toutiao_content_factory.config import load_settings
        _account_settings = load_settings(PROJECT_ROOT, account_key=account_key)
        _toutiao_profile_path = Path(_account_settings.publishing.user_data_dir)
        if not _toutiao_profile_path.is_absolute():
            _toutiao_profile_path = PROJECT_ROOT / _toutiao_profile_path
        # _check_and_disable_browser_platform 期望相对 PROJECT_ROOT 的路径
        try:
            _toutiao_profile_rel = str(_toutiao_profile_path.relative_to(PROJECT_ROOT))
        except ValueError:
            _toutiao_profile_rel = str(_toutiao_profile_path)
        _check_and_disable_browser_platform("TOUTIAO", _toutiao_profile_rel)
    except Exception as exc:
        _logger.warning("无法解析 account-aware toutiao profile 路径,回退到 default: %s", exc)
        _check_and_disable_browser_platform("TOUTIAO", ".runtime/toutiao_profile")

    # --- 账号 ---
    os.environ.setdefault("TOUTIAO_ACCOUNT", account_key)

    # --- PYTHONUTF8 ---
    os.environ.setdefault("PYTHONUTF8", "1")

    _logger.info("云端环境变量已配置:")
    _logger.info("  TOUTIAO_PUBLISH_CHANNEL = %s", os.environ.get("TOUTIAO_PUBLISH_CHANNEL"))
    _logger.info("  TOUTIAO_PUBLISH_HEADLESS = %s", os.environ.get("TOUTIAO_PUBLISH_HEADLESS"))
    _logger.info("  TOUTIAO_BROWSER_PROFILE_DIR = %s", os.environ.get("TOUTIAO_BROWSER_PROFILE_DIR"))
    _logger.info("  TOUTIAO_ACCOUNT = %s", os.environ.get("TOUTIAO_ACCOUNT"))
    _logger.info("  TOUTIAO_DRAFT_ONLY = %s", os.environ.get("TOUTIAO_DRAFT_ONLY", "1"))


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
# 发布结果摘要输出
# ---------------------------------------------------------------------------
def _resolve_last_daily_run_result_path(account_key: str) -> Path:
    """解析账号对应的 last_daily_run_result.json 路径。

    优先使用账号输出目录下的路径，解析失败时回退到 default 路径。
    """
    try:
        from toutiao_content_factory.config import load_settings

        settings = load_settings(PROJECT_ROOT, account_key=account_key)
        account_output_dir = settings.output_dir
        if not account_output_dir.is_absolute():
            account_output_dir = PROJECT_ROOT / account_output_dir
        return account_output_dir / "automation" / "last_daily_run_result.json"
    except Exception as exc:
        _logger.warning("解析 account-aware last_daily_run_result 路径失败，回退到默认路径: %s", exc)
        return PROJECT_ROOT / "output" / "automation" / "last_daily_run_result.json"


def _print_publish_summary(account_key: str) -> None:
    """读取并输出本次运行的发布结果摘要。"""
    result_path = _resolve_last_daily_run_result_path(account_key)
    if not result_path.exists():
        _logger.info("未找到 last_daily_run_result.json，跳过发布摘要: %s", result_path)
        return

    try:
        import json as _json
        data = _json.loads(result_path.read_text(encoding="utf-8"))
        _logger.info("=" * 60)
        _logger.info("发布结果摘要:")
        _logger.info("  账号: %s", data.get("account_key", "-"))
        _logger.info("  状态: %s", data.get("status", "-"))
        _logger.info("  消息: %s", data.get("message", "-"))
        _logger.info("  选题: %s", data.get("run_name", "-"))
        _logger.info("  标题: %s", data.get("selected_title", "-"))
        _logger.info("  审核: %s", data.get("review_status", "-"))
        _logger.info("  头条: %s", data.get("publish_status", "-") or "-")
        _logger.info("  微信: %s", data.get("wechat_status", "-") or "-")
        if data.get("publish_url"):
            _logger.info("  头条链接: %s", data["publish_url"])
        if data.get("wechat_publish_url"):
            _logger.info("  微信链接: %s", data["wechat_publish_url"])
        if data.get("error"):
            _logger.warning("  错误: %s", data["error"])
        _logger.info("=" * 60)
    except Exception as exc:
        _logger.warning("读取发布结果摘要失败: %s", exc)


# ---------------------------------------------------------------------------
# 健康检查阻塞降级
# ---------------------------------------------------------------------------
def _perform_quick_health_check(account_key: str) -> dict[str, Any] | None:
    """执行轻量级平台健康检查，返回健康检查结果字典；失败时返回 None。

    该函数直接调用 PlatformHealthService.quick_health_check()，用于在调用
    main.py 之前预先检测 toutiao 是否 blocked，以便进入降级流程。
    """
    try:
        from toutiao_content_factory.config import load_settings
        from toutiao_content_factory.services.notification_service import NotificationService
        from toutiao_content_factory.services.platform_health_service import PlatformHealthService
        from toutiao_content_factory.services.review_service import ReviewWorkflowService
        from toutiao_content_factory.services.wechat_service import WechatPublishService
    except ImportError as exc:
        _logger.warning("无法导入健康检查所需模块 (非致命): %s", exc)
        return None

    try:
        settings = load_settings(PROJECT_ROOT, account_key=account_key)
        notification_service = NotificationService(
            dict(os.environ),
            fallback_log_dir=str(settings.output_dir / "automation"),
        )
        review_service = ReviewWorkflowService(settings, _logger)
        wechat_publish_service = WechatPublishService(settings, _logger)
        health_service = PlatformHealthService(
            settings,
            _logger,
            toutiao_publish_service=review_service.publish_service,
            wechat_publish_service=wechat_publish_service,
            notification_service=notification_service,
        )
        return health_service.quick_health_check()
    except Exception as exc:
        _logger.warning("quick_health_check 执行失败 (非致命): %s", exc)
        return None


def _resolve_cookies_file(account_key: str) -> Path:
    """解析 cookie 文件路径。

    优先使用账号运行时目录下的 exported_cookies.json（与 inject_cookies.py 默认路径一致），
    解析失败时回退到 .runtime/automation/exported_cookies.json。
    """
    try:
        from toutiao_content_factory.config import load_settings

        settings = load_settings(PROJECT_ROOT, account_key=account_key)
        return settings.account_runtime_dir / "automation" / "exported_cookies.json"
    except Exception as exc:
        _logger.warning("解析 cookie 文件路径失败，回退到默认路径: %s", exc)
        return RUNTIME_DIR / "automation" / "exported_cookies.json"


def _reinject_cookies(account_key: str, cookies_file: Path) -> bool:
    """调用 inject_cookies.py 重新注入 toutiao cookie，返回是否成功。

    使用 subprocess 调用 scripts/inject_cookies.py，仅注入 toutiao 平台。
    """
    inject_script = PROJECT_ROOT / "scripts" / "inject_cookies.py"
    if not inject_script.exists():
        _logger.warning("inject_cookies.py 不存在，跳过重新注入: %s", inject_script)
        return False
    if not cookies_file.exists():
        _logger.warning("Cookie 文件不存在，跳过重新注入: %s", cookies_file)
        return False

    cmd = [
        sys.executable,
        str(inject_script),
        "--platforms", "toutiao",
        "--input", str(cookies_file),
        "--headless",
        "--account", account_key,
    ]
    _logger.info("重新注入 cookie (优先级 1): %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            timeout=300,
        )
        if result.returncode == 0:
            _logger.info("Cookie 重新注入成功")
            return True
        _logger.warning("Cookie 重新注入失败 (exit_code=%d)", result.returncode)
        return False
    except subprocess.TimeoutExpired:
        _logger.warning("Cookie 重新注入超时（5 分钟）")
        return False
    except Exception as exc:
        _logger.warning("Cookie 重新注入异常: %s", exc)
        return False


def _write_blocked_alert(
    account_key: str,
    blocking_platforms: list[str],
    degraded_to: str,
    reason: str,
) -> None:
    """写入降级告警文件 daily_run_blocked_alert.json。

    路径: .runtime/daily_run_blocked_alert.json
    字段: account / blocking_platforms / degraded_to / timestamp / reason
    """
    alert_path = RUNTIME_DIR / "daily_run_blocked_alert.json"
    alert_payload = {
        "account": account_key,
        "blocking_platforms": blocking_platforms,
        "degraded_to": degraded_to,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
    }
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        alert_path.write_text(
            json.dumps(alert_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _logger.warning("已写入降级告警文件: %s", alert_path)
    except Exception as exc:
        _logger.error("写入降级告警文件失败: %s", exc)


def _handle_health_check_blocked(
    account_key: str,
    cookies_file: Path,
) -> str:
    """处理健康检查 blocked 情况，按优先级降级。

    降级优先级:
        1. 调用 inject_cookies.py 重新注入 cookie 并重试健康检查；
           若重试后 toutiao 不再 blocked，返回 "normal" 继续正常流程。
        2. 若重新注入 cookie 后仍 blocked，切换到草稿模式
           （设置 TOUTIAO_DRAFT_ONLY=1, TOUTIAO_AUTO_PUBLISH=0）。
        3. 写入 daily_run_blocked_alert.json 告警文件。

    返回降级后的模式:
        - "normal": 重新注入 cookie 后恢复，继续正常流程
        - "draft_only": 降级到草稿模式
    """
    # 优先级 1: 重新注入 cookie 并重试健康检查
    _logger.warning("健康检查报告 toutiao blocked，开始优先级 1: 重新注入 cookie")
    if _reinject_cookies(account_key, cookies_file):
        retry_health = _perform_quick_health_check(account_key)
        if retry_health is not None:
            retry_blocking = [
                str(p) for p in retry_health.get("blocking_platforms", []) if str(p).strip()
            ]
            if "toutiao" not in retry_blocking:
                _logger.info("重新注入 cookie 后健康检查通过，继续正常流程")
                return "normal"
            _logger.warning(
                "重新注入 cookie 后健康检查仍 blocked: %s",
                ", ".join(retry_blocking) or "(none)",
            )
        else:
            _logger.warning("重试健康检查失败，按仍 blocked 处理")
    else:
        _logger.warning("重新注入 cookie 失败，进入优先级 2: 草稿模式")

    # 优先级 2: 切换到草稿模式
    _logger.warning("健康检查仍 blocked，开始优先级 2: 切换到草稿模式")
    os.environ["TOUTIAO_DRAFT_ONLY"] = "1"
    os.environ["TOUTIAO_AUTO_PUBLISH"] = "0"
    _logger.info(
        "已设置环境变量降级: TOUTIAO_DRAFT_ONLY=1, TOUTIAO_AUTO_PUBLISH=0 (草稿模式，不自动发布)"
    )

    # 优先级 3: 写入降级告警文件
    reason = (
        "健康检查报告 toutiao blocked，重新注入 cookie 后仍无法恢复，已降级为草稿模式。"
        "每日内容仍会生成草稿，但不会自动发布到头条。请检查 cookie 是否过期或手动重新登录。"
    )
    _write_blocked_alert(
        account_key=account_key,
        blocking_platforms=["toutiao"],
        degraded_to="draft_only",
        reason=reason,
    )
    return "draft_only"


def _mark_degraded_status(account_key: str, reason: str = "health_check_blocked") -> None:
    """如果发生了降级，将 last_daily_run_result.json 的 status 更新为 degraded_to_draft。

    该函数在 main.py 子进程成功退出后调用，用于区分 "blocked" / "success" / "degraded_to_draft"
    三种状态。仅在账号匹配时更新，避免误改其他账号的结果文件。
    """
    result_path = _resolve_last_daily_run_result_path(account_key)
    if not result_path.exists():
        _logger.warning("last_daily_run_result.json 不存在，跳过降级状态更新: %s", result_path)
        return
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        # 仅当账号匹配或未标记账号时更新（避免误改其他账号的结果）
        existing_account = data.get("account_key")
        if existing_account and existing_account != account_key:
            _logger.info(
                "last_daily_run_result.json 账号不匹配 (%s != %s)，跳过状态更新",
                existing_account, account_key,
            )
            return
        previous_status = data.get("status", "")
        data["status"] = "degraded_to_draft"
        data["degraded_from"] = previous_status
        data["degraded_at"] = datetime.now().isoformat(timespec="seconds")
        data["degraded_reason"] = reason
        result_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _logger.info(
            "已将 last_daily_run_result.json 的 status 从 '%s' 更新为 'degraded_to_draft' (reason=%s)",
            previous_status or "(empty)",
            reason,
        )
    except Exception as exc:
        _logger.warning("更新 last_daily_run_result.json 降级状态失败: %s", exc)


def _mark_degraded_if_draft_saved(account_key: str) -> None:
    """当配置要求发布但实际只生成了草稿时，标记为 degraded_to_draft。

    用于捕获 precheck 硬拦截或质量门禁降级的情况——这些降级发生在
    publish_service 内部，cloud_daily_run 的 degraded_mode 不会感知到。
    """
    result_path = _resolve_last_daily_run_result_path(account_key)
    if not result_path.exists():
        return
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        existing_account = data.get("account_key")
        if existing_account and existing_account != account_key:
            return
        publish_status = data.get("publish_status", "")
        if publish_status != "draft_saved":
            return
        previous_status = data.get("status", "")
        data["status"] = "degraded_to_draft"
        data["degraded_from"] = previous_status
        data["degraded_at"] = datetime.now().isoformat(timespec="seconds")
        data["degraded_reason"] = "precheck_blocked_or_quality_gate"
        result_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _logger.warning(
            "配置要求发布但 publish_status=draft_saved，已标记 status=degraded_to_draft (was '%s')",
            previous_status or "(empty)",
        )
    except Exception as exc:
        _logger.warning("检查 draft_saved 降级状态失败: %s", exc)


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

    # --- 性能数据回采 ---
    if os.environ.get("PERFORMANCE_COLLECT_ENABLED", "1") == "1":
        _logger.info("运行性能数据回采...")
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--collect-performance", "--account", account_key],
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=600,
            )
            _logger.info("性能数据回采完成 (exit_code=%d)", result.returncode)
        except Exception as exc:
            _logger.warning("性能数据回采执行异常（非致命）: %s", exc)
    else:
        _logger.info("性能数据回采未启用，跳过")

    # --- 月度回顾与迭代建议 ---
    # 读取 30 天发布数据生成回顾报告、迭代建议，并接入 EvolutionAgent 生成演化提案。
    # 默认开启，可通过环境变量 MONTHLY_REVIEW_ENABLED=0 关闭。
    if os.environ.get("MONTHLY_REVIEW_ENABLED", "1") == "1":
        _logger.info("运行月度回顾与迭代建议...")
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--monthly-review", "--account", account_key],
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy(),
                timeout=900,
            )
            _logger.info("月度回顾完成 (exit_code=%d)", result.returncode)
        except Exception as exc:
            _logger.warning("月度回顾执行异常（非致命）: %s", exc)
    else:
        _logger.info("月度回顾未启用，跳过")

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

    # 4.5 健康检查阻塞降级（在调用 main.py 之前预先检测 toutiao blocked）
    # 降级优先级: 重新注入 cookie → 草稿模式 → 告警
    degraded_mode: str = "normal"
    if not args.dry_run:
        try:
            health = _perform_quick_health_check(account_key)
            if health is not None:
                blocking_platforms = [
                    str(p) for p in health.get("blocking_platforms", []) if str(p).strip()
                ]
                if "toutiao" in blocking_platforms:
                    _logger.warning(
                        "健康检查报告 blocked 平台: %s，进入降级流程",
                        ", ".join(blocking_platforms) or "(none)",
                    )
                    cookies_file = _resolve_cookies_file(account_key)
                    degraded_mode = _handle_health_check_blocked(account_key, cookies_file)
                else:
                    _logger.info(
                        "健康检查通过 (blocking_platforms: %s)",
                        ", ".join(blocking_platforms) or "none",
                    )
        except Exception as exc:
            _logger.warning("健康检查阻塞降级流程异常 (非致命，继续执行): %s", exc)

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
                timeout=2400,  # 40 分钟超时（与 workflow 45 分钟对齐，留 5 分钟缓冲）
            )
            exit_code = result.returncode
            if exit_code == 0:
                _logger.info("云端定时执行成功完成 (exit_code=%d)", exit_code)
                # 如果发生了降级（草稿模式），将 last_daily_run_result.json
                # 的 status 更新为 degraded_to_draft，区分 blocked / success
                if degraded_mode == "draft_only":
                    _mark_degraded_status(account_key)
                elif os.environ.get("TOUTIAO_DRAFT_ONLY", "1") != "1":
                    # 配置要求发布但可能被 precheck/质量门禁内部降级为草稿
                    _mark_degraded_if_draft_saved(account_key)
                # 主 pipeline 成功后运行 AiToEarn 集成模块
                _run_extra_modules(account_key)
                _print_publish_summary(account_key)
                return exit_code

            # 检查是否是 PlatformAuthError，如果是则禁用失败平台并重试
            if attempt < max_attempts and _is_auth_error():
                _logger.warning("检测到平台登录验证失败，自动禁用失败平台并重试...")
                _disable_blocking_platforms()
                continue

            _logger.error("云端定时执行失败 (exit_code=%d)", exit_code)
            return exit_code
        except subprocess.TimeoutExpired:
            _logger.error("云端定时执行超时（40 分钟）")
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
