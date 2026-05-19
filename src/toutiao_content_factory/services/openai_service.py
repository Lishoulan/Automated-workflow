from __future__ import annotations

import json
import logging

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    OpenAI = None

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import AIRequestError, AIResponseFormatError


class OpenAIContentService:
    def __init__(self, settings: AppSettings, logger: logging.Logger, force_mock: bool = False) -> None:
        self.settings = settings
        self.logger = logger
        self.force_mock = force_mock
        self.client = None

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
        content = self._extract_message_content(response)

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:  # pragma: no cover - malformed remote output
            raise AIResponseFormatError("The model did not return valid JSON.") from exc

        self.logger.info("Model response parsed as JSON successfully.")
        return payload

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
