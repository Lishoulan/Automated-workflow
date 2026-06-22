from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.wechat_135editor_service import Editor135Service
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.utils.logging_utils import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route WeChat publishing through 135 Editor and sync to the WeChat draft box.",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Account key defined in config.yaml accounts section.",
    )
    parser.add_argument(
        "--run-dir",
        type=str,
        default="",
        help="Absolute or relative run directory to publish.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the newest run directory with a publish package when --run-dir is omitted.",
    )
    parser.add_argument(
        "--keep-alive-only",
        action="store_true",
        help="Only verify and refresh the 135 Editor session without publishing.",
    )
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="Save a 135 draft but do not request WeChat sync submission.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the 135 session check and publish flow in headless mode.",
    )
    parser.add_argument(
        "--init-login",
        action="store_true",
        help="Open the persistent 135 profile for manual login initialization.",
    )
    return parser


def resolve_run_dir(settings, raw_run_dir: str, use_latest: bool) -> Path:
    if raw_run_dir.strip():
        candidate = Path(raw_run_dir)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Run directory does not exist: {candidate}")
        return candidate

    if not use_latest:
        use_latest = True

    candidates: list[Path] = []
    if settings.runs_dir.exists():
        for item in settings.runs_dir.iterdir():
            if not item.is_dir():
                continue
            publish_dir = item / settings.review.publish_dirname
            if not publish_dir.exists():
                continue
            candidates.append(item)
    if not candidates:
        raise FileNotFoundError(f"No publishable runs were found under {settings.runs_dir}")
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def print_json(prefix: str, payload: dict) -> None:
    print(prefix)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(PROJECT_ROOT, account_key=args.account)
    logger = configure_logging(
        settings.logs_dir,
        settings.logging_level,
        logger_name=f"auto_run_wechat_via_135.{settings.account_slug}",
    )
    editor_service = Editor135Service(settings, logger)
    wechat_service = WechatPublishService(settings, logger)

    logger.info("auto_run_wechat_via_135 starting for account=%s", settings.account_key)

    if args.init_login:
        result = editor_service.initialize_login_session()
        print_json("[135 Login Initialized]", result)
        return 0

    keep_alive_result = editor_service.keep_alive(headless=args.headless or None)
    print_json("[135 Session Check]", keep_alive_result)
    if not keep_alive_result.get("ready"):
        logger.error("135 Editor session is not ready. Aborting publish flow.")
        return 2

    if args.keep_alive_only:
        logger.info("Keep-alive completed without publishing.")
        return 0

    run_dir = resolve_run_dir(settings, args.run_dir, args.latest)
    logger.info("Resolved run directory: %s", run_dir)
    print(f"[Run Dir] {run_dir}")

    exports = wechat_service.publish_run_via_135(run_dir, submit=not args.draft_only)
    rendered_exports = {name: str(path) for name, path in exports.items()}
    print_json("[135 Publish Exports]", rendered_exports)

    result_path = Path(exports["wechat_publish_result"])
    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        print_json("[135 Publish Result]", payload)

    logger.info("auto_run_wechat_via_135 finished successfully for run=%s", run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
