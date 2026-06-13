import os
import logging
import subprocess
import sys
import signal
from pathlib import Path
from datetime import datetime
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
INTERVAL_SECONDS = 3 * 60 * 60

stop_event = Event()


def run_publish():
    logger.info("Starting publish script...")
    try:
        result = subprocess.run(
            [PYTHON_PATH, str(SCRIPT_PATH)],
            cwd=str(SCRIPT_PATH.resolve().parent.parent),
            capture_output=True,
            text=True,
            timeout=600,
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
        logger.error("Script timed out after 600s")
        return False
    except Exception as e:
        logger.error("Script error: %s", e)
        return False


def main():
    import msvcrt

    try:
        lock_fd = open(str(LOCK_FILE), "w")
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (IOError, OSError):
        logger.warning("Another daemon instance is already running. Exiting.")
        return

    logger.info("=== WeChat Publish Daemon Started (PID: %d) ===", os.getpid())
    logger.info("Interval: %d seconds (%d hours)", INTERVAL_SECONDS, INTERVAL_SECONDS // 3600)
    logger.info("Script: %s", SCRIPT_PATH)
    logger.info("Python: %s", PYTHON_PATH)

    def handle_signal(signum, frame):
        logger.info("Received signal %s, stopping...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stop_event.is_set():
        now = datetime.now()
        logger.info("--- Run at %s ---", now.strftime("%Y-%m-%d %H:%M:%S"))

        success = run_publish()
        status = "SUCCESS" if success else "FAILED"
        logger.info("--- Run %s ---", status)

        logger.info("Sleeping %d seconds until next run...", INTERVAL_SECONDS)
        stop_event.wait(INTERVAL_SECONDS)

    try:
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        lock_fd.close()
        LOCK_FILE.unlink(missing_ok=True)
    except:
        pass
    logger.info("Daemon stopped.")


if __name__ == "__main__":
    main()
