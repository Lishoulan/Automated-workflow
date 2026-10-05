import os
import logging
import subprocess
import sys
import signal
from pathlib import Path
from datetime import datetime, timedelta
from threading import Event

LOCK_FILE = Path(__file__).resolve().parent.parent / "output" / ".wechat_daemon.lock"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.FileHandler(
            str(Path(__file__).resolve().parent.parent / "output" / "wechat_daemon.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("wechat_daemon")

SCRIPT_PATH = Path(__file__).resolve().parent / "scheduled_wechat_publish.py"
PYTHON_PATH = sys.executable.replace("pythonw.exe", "python.exe")
PUBLISH_HOUR = 7

stop_event = Event()


def run_publish():
    logger.info("Starting publish script...")
    try:
        result = subprocess.run(
            [PYTHON_PATH, str(SCRIPT_PATH), "--account", "default", "--enable-auto-publish"],
            cwd=str(SCRIPT_PATH.resolve().parent.parent),
            capture_output=True,
            text=True,
            timeout=2700,
            encoding="utf-8",
            errors="replace",
        )
        logger.info("Script exit code: %s", result.returncode)
        if result.stdout:
            for line in result.stdout.strip().splitlines()[-5:]:
                logger.info("STDOUT: %s", line)
        if result.stderr:
            for line in result.stderr.strip().splitlines()[-5:]:
                logger.warning("STDERR: %s", line)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("Script timed out after 2700s")
        return False
    except Exception as e:
        logger.error("Script error: %s", e)
        return False


def next_publish_time(now: datetime) -> datetime:
    scheduled = now.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
    return scheduled if now < scheduled else scheduled + timedelta(days=1)


def main() -> None:
    import msvcrt

    try:
        lock_fd = open(str(LOCK_FILE), "a+b")
        lock_fd.seek(0)
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (IOError, OSError):
        logger.warning("Another daemon instance is already running. Exiting.")
        return

    logger.info("=== WeChat Publish Daemon Started (PID: %d) ===", os.getpid())
    logger.info("Daily publish time: %02d:00 (system local time)", PUBLISH_HOUR)
    logger.info("Script: %s", SCRIPT_PATH)
    logger.info("Python: %s", PYTHON_PATH)

    def handle_signal(signum, frame):
        logger.info("Received signal %s, stopping...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    next_run = next_publish_time(datetime.now())
    logger.info("Next run: %s", next_run.isoformat())
    while not stop_event.is_set():
        now = datetime.now()
        if now >= next_run:
            logger.info("--- Run at %s ---", now.isoformat())
            success = run_publish()
            logger.info("--- Run %s ---", "SUCCESS" if success else "FAILED")
            next_run = next_publish_time(datetime.now())
            logger.info("Next run: %s", next_run.isoformat())
        stop_event.wait(max(1, min(60, (next_run - datetime.now()).total_seconds())))

    try:
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        lock_fd.close()
    except:
        pass
    logger.info("Daemon stopped.")


if __name__ == "__main__":
    main()
