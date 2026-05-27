from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    OpenAI = None

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import AIRequestError, AIResponseFormatError


@dataclass
class UsageCostSnapshot:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


class OpenAIContentService:
    def __init__(self, settings: AppSettings, logger: logging.Logger, force_mock: bool = False) -> None:
        self.settings = settings
        self.logger = logger
        self.force_mock = force_mock
        self.client = None
        self.usage_snapshot = UsageCostSnapshot()
        self.prompt_token_rate = self._resolve_rate(
            "OPENAI_PROMPT_TOKEN_RATE",
            self._default_prompt_rate(),
        )
        self.completion_token_rate = self._resolve_rate(
            "OPENAI_COMPLETION_TOKEN_RATE",
            self._default_completion_rate(),
        )

        if not force_mock and settings.openai_api_key:
            if OpenAI is None:
                raise AIRequestError("The openai package is not installed, so the model client cannot be used.")

            client_kwargs = {"api_key": settings.openai_api_key}
            if settings.openai_base_url:
                client_kwargs["base_url"] = settings.openai_base_url
            self.client = OpenAI(**client_kwargs)

    @property
    def available(self) -> bool:
        return self.client is not None

    def create_json_completion(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.available:
            raise AIRequestError("Model client is not available.")

        response = self._request_structured_json(system_prompt=system_prompt, user_prompt=user_prompt)
        self._accumulate_usage(response)
        content = self._extract_message_content(response)

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:  # pragma: no cover - malformed remote output
            raise AIResponseFormatError("The model did not return valid JSON.") from exc

        self.logger.info("Model response parsed as JSON successfully.")
        return payload

    @property
    def estimated_cost(self) -> float:
        return self.usage_snapshot.estimated_cost

    def get_usage_snapshot(self) -> UsageCostSnapshot:
        return UsageCostSnapshot(
            prompt_tokens=self.usage_snapshot.prompt_tokens,
            completion_tokens=self.usage_snapshot.completion_tokens,
            total_tokens=self.usage_snapshot.total_tokens,
            estimated_cost=self.usage_snapshot.estimated_cost,
        )

    def _request_structured_json(self, system_prompt: str, user_prompt: str):
        try:
            return self.client.chat.completions.create(
                model=self.settings.openai_model,
                temperature=self.settings.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:  # pragma: no cover - network call
            self.logger.warning(
                "Structured JSON response failed for model %s, retrying with prompt-only JSON mode: %s",
                self.settings.openai_model,
                exc,
            )
            try:
                return self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    temperature=self.settings.temperature,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                f"{system_prompt}\n\n"
                                "Return exactly one valid JSON object. "
                                "Do not use markdown code fences. "
                                "Do not add any explanation before or after the JSON."
                            ),
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                )
            except Exception as retry_exc:  # pragma: no cover - network call
                raise AIRequestError(f"Model request failed: {retry_exc}") from retry_exc

    def _accumulate_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
        request_cost = self._estimate_request_cost(prompt_tokens, completion_tokens)

        self.usage_snapshot.prompt_tokens += prompt_tokens
        self.usage_snapshot.completion_tokens += completion_tokens
        self.usage_snapshot.total_tokens += total_tokens
        self.usage_snapshot.estimated_cost = round(self.usage_snapshot.estimated_cost + request_cost, 6)

        self.logger.info(
            "Model usage recorded: prompt_tokens=%s completion_tokens=%s estimated_cost=%.6f",
            prompt_tokens,
            completion_tokens,
            request_cost,
        )

    def _estimate_request_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        prompt_cost = (prompt_tokens / 1000.0) * self.prompt_token_rate
        completion_cost = (completion_tokens / 1000.0) * self.completion_token_rate
        return round(prompt_cost + completion_cost, 6)

    def _resolve_rate(self, env_name: str, default_value: float) -> float:
        raw = os.getenv(env_name, "").strip()
        if not raw:
            return default_value
        try:
            return float(raw)
        except ValueError:
            self.logger.warning("Invalid token rate for %s=%s, fallback to %.6f", env_name, raw, default_value)
            return default_value

    def _default_prompt_rate(self) -> float:
        model_name = str(self.settings.openai_model or "").lower()
        if "deepseek-reasoner" in model_name:
            return 0.004
        if "deepseek" in model_name:
            return 0.001
        return 0.001

    def _default_completion_rate(self) -> float:
        model_name = str(self.settings.openai_model or "").lower()
        if "deepseek-reasoner" in model_name:
            return 0.016
        if "deepseek" in model_name:
            return 0.002
        return 0.002

    def _extract_message_content(self, response) -> str:
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return "{}"

        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            if content.lower().startswith("json"):
                content = content[4:].strip()

        return content or "{}"

    def close(self) -> None:
        close_method = getattr(self.client, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self.logger.debug("Failed to close model client cleanly: %s", exc)
