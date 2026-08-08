from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

try:
    from openai import APIStatusError, APITimeoutError, APIConnectionError, RateLimitError
except Exception:
    APIStatusError = None
    APITimeoutError = None
    APIConnectionError = None
    RateLimitError = None

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
    # 类常量定义
    MAX_RETRIES = 2
    BASE_DELAY = 2.0
    MAX_DELAY = 30.0
    REQUEST_TIMEOUT = 120.0
    RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

    def __init__(self, settings: AppSettings, logger: logging.Logger, force_mock: bool = False) -> None:
        self.settings = settings
        self.logger = logger
        self.force_mock = force_mock
        self.client = None
        self.usage_snapshot = UsageCostSnapshot()
        
        # 预计算模型类型以避免重复调用
        self._is_deepseek_reasoner, self._is_deepseek = self._get_model_type()
        
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

            client_kwargs = {
                "api_key": settings.openai_api_key,
                "timeout": self.REQUEST_TIMEOUT,
            }
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
        except json.JSONDecodeError:
            # 尝试从 markdown 代码块中提取 JSON
            fallback_payload = self._extract_json_from_markdown(content)
            if fallback_payload is not None:
                self.logger.warning("JSON 解析失败，但成功从 markdown 代码块中提取到有效 JSON。")
                return fallback_payload
            raise AIResponseFormatError("The model did not return valid JSON.") from None

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
            return self._call_with_retry(
                model=self.settings.openai_model,
                temperature=self.settings.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except AIRequestError:
            raise
        except Exception as exc:
            self.logger.warning(
                "Structured JSON mode failed for model %s after retries, trying prompt-only JSON mode: %s",
                self.settings.openai_model,
                exc,
            )
            return self._call_with_retry(
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

    def _call_with_retry(self, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable_error(exc):
                    raise AIRequestError(f"Model request failed (non-retryable): {exc}") from exc
                if attempt >= self.MAX_RETRIES:
                    break
                delay = min(self.BASE_DELAY * (2 ** (attempt - 1)), self.MAX_DELAY)
                self.logger.warning(
                    "Model request attempt %d/%d failed (retryable): %s. Retrying in %.1fs.",
                    attempt,
                    self.MAX_RETRIES,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise AIRequestError(
            f"Model request failed after {self.MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc

    def _is_retryable_error(self, exc: Exception) -> bool:
        if APIStatusError is not None and isinstance(exc, APIStatusError):
            return exc.status_code in self.RETRYABLE_HTTP_CODES
        if RateLimitError is not None and isinstance(exc, RateLimitError):
            return True
        if APITimeoutError is not None and isinstance(exc, APITimeoutError):
            return True
        if APIConnectionError is not None and isinstance(exc, APIConnectionError):
            return True
        # Non-retryable: authentication and bad request errors
        try:
            from openai import AuthenticationError as _AuthErr
            if isinstance(exc, _AuthErr):
                return False
        except ImportError:
            pass
        try:
            from openai import BadRequestError as _BadReqErr
            if isinstance(exc, _BadReqErr):
                return False
        except ImportError:
            pass
        msg = str(exc).lower()
        if any(kw in msg for kw in ("rate limit", "too many requests", "请稍后重试", "请求失败")):
            return True
        if any(kw in msg for kw in ("timeout", "timed out", "connection", "连接")):
            return True
        return False

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

    def _get_model_type(self) -> tuple[bool, bool]:
        """获取模型类型，返回 (is_deepseek_reasoner, is_deepseek)"""
        model_name = str(self.settings.openai_model or "").lower()
        is_deepseek_reasoner = "deepseek-reasoner" in model_name
        is_deepseek = "deepseek" in model_name
        return is_deepseek_reasoner, is_deepseek

    def _default_prompt_rate(self) -> float:
        if self._is_deepseek_reasoner:
            return 0.004
        return 0.001

    def _default_completion_rate(self) -> float:
        if self._is_deepseek_reasoner:
            return 0.016
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

    _MARKDOWN_JSON_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)```")

    def _extract_json_from_markdown(self, content: str) -> dict | None:
        for match in self._MARKDOWN_JSON_PATTERN.finditer(content):
            candidate = match.group(1).strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def close(self) -> None:
        close_method = getattr(self.client, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self.logger.debug("Failed to close model client cleanly: %s", exc)
