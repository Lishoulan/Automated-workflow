from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

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
    # 限流韧性增强（2026-08-27）：重试次数 2→4，基础退避 2s→3s，上限 30s→60s；
    # 429 限流单独使用更长退避（优先 Retry-After，否则 30s×attempt），
    # 避免上游限流时整轮 Agent 全部回退本地策略导致内容质量降级。
    MAX_RETRIES = 4
    BASE_DELAY = 3.0
    MAX_DELAY = 60.0
    REQUEST_TIMEOUT = 90.0
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

            # 使用 httpx.Timeout 设置完整的连接/读取/写入超时：
            # 第三方中转站偶发"连接保持打开但无响应"的挂起，仅设置总超时不够，
            # 必须显式设置 read 超时，否则挂起连接可能永远不返回。
            try:
                import httpx
                timeout_value = httpx.Timeout(
                    self.REQUEST_TIMEOUT,
                    connect=10.0,
                    read=self.REQUEST_TIMEOUT,
                    write=30.0,
                    pool=10.0,
                )
            except Exception:
                timeout_value = self.REQUEST_TIMEOUT

            client_kwargs = {
                "api_key": settings.openai_api_key,
                "timeout": timeout_value,
            }
            if settings.openai_base_url:
                client_kwargs["base_url"] = settings.openai_base_url
            self.client = OpenAI(**client_kwargs)

        # 部分第三方中转站不支持 response_format 结构化参数（会返回 502/4xx），
        # 通过环境变量 OPENAI_DISABLE_RESPONSE_FORMAT=1 直接跳过结构化模式，
        # 避免每次请求都先浪费一次失败调用。
        self.disable_response_format = (
            os.getenv("OPENAI_DISABLE_RESPONSE_FORMAT", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )

    @property
    def available(self) -> bool:
        return self.client is not None

    def create_text_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """纯文本补全（非 JSON）。用于独立微头条等短内容写作。

        复用统一的重试/超时/用量统计基础设施，仅不要求 JSON 输出。
        """
        if not self.available:
            raise AIRequestError("Model client is not available.")

        kwargs: dict[str, Any] = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = self._call_with_retry(**kwargs)
        self._accumulate_usage(response)
        return (response.choices[0].message.content or "").strip()

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
        # 部分第三方中转站不支持 response_format 参数（返回 502/4xx）。
        # 环境变量 OPENAI_DISABLE_RESPONSE_FORMAT=1 时直接走 prompt-only JSON 模式，
        # 避免每次请求都先浪费一次失败调用（省时间、省配额）。
        if getattr(self, "disable_response_format", False):
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
        # 先尝试带 response_format 的结构化模式（单次尝试，不重试）：
        # 若中转站不支持则快速回退到 prompt-only JSON 模式，而不是浪费重试次数。
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
        except Exception as exc:
            self.logger.warning(
                "Structured JSON mode failed (response_format not supported or transient error: %s), "
                "trying prompt-only JSON mode.",
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
                if self._is_rate_limit_error(exc):
                    # 429 限流：优先遵循服务端 Retry-After，否则按 30s×attempt 退避，
                    # 让上游配额恢复，避免连续重试继续触发限流。
                    delay = self._retry_after_delay(exc) or min(30.0 * attempt, self.MAX_DELAY)
                else:
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

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        if APIStatusError is not None and isinstance(exc, APIStatusError):
            return exc.status_code == 429
        if RateLimitError is not None and isinstance(exc, RateLimitError):
            return True
        return False

    def _retry_after_delay(self, exc: Exception) -> float | None:
        """读取上游 429 响应的 Retry-After 头；解析失败或缺失返回 None。

        openai 库把响应头放在 exc.response.headers（APIStatusError），
        RateLimitError 也可能直接带 headers，两处都尝试。
        """
        headers = getattr(exc, "headers", None)
        if not isinstance(headers, dict):
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", None)
        if not isinstance(headers, dict):
            return None
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if not raw:
            return None
        try:
            return min(max(float(raw), 1.0), self.MAX_DELAY)
        except (TypeError, ValueError):
            return None

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
