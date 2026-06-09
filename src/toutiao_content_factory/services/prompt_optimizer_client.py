"""Prompt Optimizer API client — translates casual requirements into professional AI prompts.

Optimizes existing prompts for better AI output quality.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class PromptOptimizerClient:
    """Client for the prompt-optimizer service."""

    def __init__(self, base_url: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("Prompt Optimizer API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("Prompt Optimizer unexpected error: %s", exc)
            return {}

    def optimize(self, prompt: str, target: str = "general") -> str:
        """Optimize a prompt for better AI output.

        Args:
            prompt: Raw prompt text to optimize
            target: Optimization target — "general", "writing", "analysis", "marketing"

        Returns:
            Optimized prompt text
        """
        body = {
            "prompt": prompt,
            "target": target,
            "language": "zh",
        }
        result = self._request("POST", "/optimize", body)
        optimized = result.get("optimized_prompt", "")
        if optimized:
            self.logger.info("Prompt optimized: %d → %d chars", len(prompt), len(optimized))
        return optimized or prompt

    def translate_requirement(self, requirement: str) -> str:
        """Translate a casual user requirement into a professional AI prompt.

        Example: "写小红书爆款" → structured prompt with hooks, tags, tone requirements
        """
        body = {
            "requirement": requirement,
            "mode": "translate",
            "language": "zh",
        }
        result = self._request("POST", "/translate", body)
        translated = result.get("prompt", "")
        if translated:
            self.logger.info("Requirement translated: %s → %d chars", requirement[:30], len(translated))
        return translated or requirement

    def health_check(self) -> bool:
        """Check if prompt-optimizer service is available."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False
