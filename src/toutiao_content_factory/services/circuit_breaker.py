from __future__ import annotations

import logging
import threading
import time


class PlatformCircuitBreaker:
    """简单的内存熔断器，按平台维度跟踪连续失败次数。

    - 连续失败次数达到 failure_threshold 时进入熔断（open）状态
    - 熔断后经过 reset_timeout_seconds 自动进入半开状态，允许重试
    - 成功一次即重置该平台的失败计数
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout_seconds: int = 300,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout_seconds = reset_timeout_seconds
        self._failure_counts: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def record_failure(self, platform: str) -> None:
        platform = str(platform).strip().lower()
        if not platform:
            return
        with self._lock:
            count = self._failure_counts.get(platform, 0) + 1
            self._failure_counts[platform] = count
            if count >= self._failure_threshold:
                self._open_until[platform] = time.time() + self._reset_timeout_seconds

    def record_success(self, platform: str) -> None:
        platform = str(platform).strip().lower()
        if not platform:
            return
        with self._lock:
            self._failure_counts.pop(platform, None)
            self._open_until.pop(platform, None)

    def is_open(self, platform: str) -> bool:
        platform = str(platform).strip().lower()
        if not platform:
            return False
        with self._lock:
            open_until = self._open_until.get(platform, 0)
            if open_until <= 0:
                return False
            if time.time() < open_until:
                return True
            # 超时后自动恢复（半开状态），清除熔断标记
            self._open_until.pop(platform, None)
            self._failure_counts[platform] = 0
            return False
