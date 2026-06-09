"""Ruflo API client — multi-agent orchestration platform.

Decomposes projects into subtasks, assigns AI employees, and auto-verifies results.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class RufloClient:
    """Client for the Ruflo orchestration service."""

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
            self.logger.warning("Ruflo API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("Ruflo unexpected error: %s", exc)
            return {}

    def submit_project(self, description: str, roles: list[str] | None = None) -> dict[str, Any]:
        """Submit a project for Ruflo to decompose and orchestrate.

        Args:
            description: Project description (e.g., "调研人形机器人产业链并生成3篇深度文章")
            roles: List of agency-agents role names to involve

        Returns:
            Dict with keys: project_id, subtasks, status
        """
        body = {
            "description": description,
            "roles": roles or ["marketing-expert", "data-analyst"],
            "callback_url": "",
        }
        self.logger.info("Ruflo project submitted: %s", description[:50])
        return self._request("POST", "/projects", body)

    def get_project_status(self, project_id: str) -> dict[str, Any]:
        """Get the status of a submitted project."""
        return self._request("GET", f"/projects/{project_id}")

    def health_check(self) -> bool:
        """Check if Ruflo service is available."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False
