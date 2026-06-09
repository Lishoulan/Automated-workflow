"""DeerFlow API client — deep research agent by ByteDance.

Auto-searches, summarizes, compares, and generates structured research reports.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class DeerFlowClient:
    """Client for the DeerFlow research service."""

    def __init__(self, base_url: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("DeerFlow API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("DeerFlow unexpected error: %s", exc)
            return {}

    def research(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        """Submit a research topic and get a structured report.

        Args:
            topic: Research topic (e.g., "人形机器人产业链分析")
            depth: Research depth — "quick" (5min), "medium" (15min), "deep" (30min)

        Returns:
            Dict with keys: title, summary, sections, references, created_at
        """
        body = {
            "topic": topic,
            "depth": depth,
            "output_format": "structured",
        }
        self.logger.info("DeerFlow research started: %s (depth=%s)", topic[:50], depth)
        result = self._request("POST", "/research", body, timeout=300)
        if result:
            self.logger.info("DeerFlow research completed: %s", result.get("title", "untitled")[:50])
        return result

    def quick_research(self, topic: str) -> str:
        """Quick research — returns a summary string for topic selection."""
        result = self.research(topic, depth="quick")
        if not result:
            return ""
        return result.get("summary", "")

    def deep_research(self, topic: str) -> dict[str, Any]:
        """Deep research — returns full structured report."""
        return self.research(topic, depth="deep")

    def health_check(self) -> bool:
        """Check if DeerFlow service is available."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False
