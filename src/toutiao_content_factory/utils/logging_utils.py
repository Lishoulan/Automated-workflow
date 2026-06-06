from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


def configure_logging(
    logs_dir: Path,
    level: str = "INFO",
    logger_name: str = "toutiao_content_factory",
    structured: bool = False,
) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    if structured:
        formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    file_handler = logging.FileHandler(logs_dir / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


class StructuredFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)


class MetricsCollector:
    """Lightweight runtime metrics collector for pipeline runs."""

    def __init__(self) -> None:
        self.metrics: dict[str, Any] = {}
        self._start_times: dict[str, datetime] = {}

    def record(self, key: str, value: Any) -> None:
        self.metrics[key] = value

    def increment(self, key: str, amount: int = 1) -> None:
        self.metrics[key] = self.metrics.get(key, 0) + amount

    def start_timer(self, key: str) -> None:
        self._start_times[key] = datetime.now()

    def stop_timer(self, key: str) -> float:
        if key not in self._start_times:
            return 0.0
        elapsed = (datetime.now() - self._start_times.pop(key)).total_seconds()
        self.metrics[key] = round(elapsed, 3)
        return elapsed

    def to_dict(self) -> dict[str, Any]:
        return dict(self.metrics)

    def reset(self) -> None:
        self.metrics.clear()
        self._start_times.clear()
