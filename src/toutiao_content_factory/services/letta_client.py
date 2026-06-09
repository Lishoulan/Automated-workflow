"""Letta API client — persistent memory layer for AI agents.

Provides cross-conversation memory: user preferences, writing style,
past decisions, and historical context.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class LettaClient:
    """Client for the Letta memory service."""

    def __init__(self, base_url: str, agent_id: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("Letta API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("Letta unexpected error: %s", exc)
            return {}

    def get_memory_context(self, topic: str = "") -> str:
        """Retrieve relevant memory context for a given topic."""
        if not self.agent_id:
            return ""
        path = f"/v1/agents/{self.agent_id}/memory"
        result = self._request("GET", path)
        if not result:
            return ""
        # Extract relevant context from memory blocks
        memories = result.get("core_memory", [])
        if isinstance(memories, list):
            parts = [m.get("content", "") for m in memories if m.get("content")]
            return "\n".join(parts)
        return str(memories) if memories else ""

    def save_observation(self, observation: str) -> bool:
        """Save an observation to the agent's memory."""
        if not self.agent_id:
            return False
        path = f"/v1/agents/{self.agent_id}/messages"
        body = {
            "messages": [{"role": "user", "content": observation}],
        }
        result = self._request("POST", path, body)
        return bool(result)

    def create_agent(self, name: str, description: str = "") -> str:
        """Create a new Letta agent and return its ID."""
        body = {
            "name": name,
            "description": description or "Content factory memory agent",
            "memory_blocks": [
                {"label": "persona", "value": "I am a content factory assistant that remembers user preferences and writing style."},
                {"label": "human", "value": "The user is a tech content creator focusing on AI, robotics, and new energy."},
            ],
            "llm_config": {
                "model": "deepseek-v4-flash",
                "model_endpoint_type": "openai",
            },
        }
        result = self._request("POST", "/v1/agents", body)
        agent_id = result.get("id", "")
        if agent_id:
            self.agent_id = agent_id
            self.logger.info("Created Letta agent: %s", agent_id)
        return agent_id
