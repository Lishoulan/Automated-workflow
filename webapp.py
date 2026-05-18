from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.web import create_app

app = create_app(PROJECT_ROOT, account_key=os.getenv("TOUTIAO_ACCOUNT", "default"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the multi-account Toutiao content factory web console.")
    parser.add_argument(
        "--account",
        default=os.getenv("TOUTIAO_ACCOUNT", "default"),
        help="Default account key when the page first opens. The web console can switch accounts after startup.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("TOUTIAO_WEB_HOST", "127.0.0.1"),
        help="Host interface to bind. Defaults to TOUTIAO_WEB_HOST or 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("TOUTIAO_WEB_PORT", "8001")),
        help="Port to bind. Defaults to TOUTIAO_WEB_PORT or 8000.",
    )
    return parser.parse_args()


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    args = parse_args()
    app = create_app(PROJECT_ROOT, account_key=args.account)
    if not is_port_available(args.host, args.port):
        print(
            f"Port {args.host}:{args.port} is already in use. "
            f"Try `python webapp.py --port 8001` or set TOUTIAO_WEB_PORT.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    uvicorn.run(app, host=args.host, port=args.port)
