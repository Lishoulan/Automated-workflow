from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Mapping
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
from toutiao_content_factory.services.article_sanitizer import looks_like_html_document


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
    # 2026-09-15：原值 90s，实测当前中转账号所有可用模型（gpt-5.6-luna / gpt-5.5 /
    # gpt-6-astra）对 3000+ token 的中文 prompt 响应需 58-67s，稍大的 Agent prompt
    # 直接撞破 90s → 重试 4 次全超时 → 关键 Agent 回退本地策略 → 内容质量降级被拦。
    # 放宽到 240s 并允许用环境变量覆盖，避免以后再因上游速度波动改源码。
    REQUEST_TIMEOUT = float(os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "240"))
    # 2026-09-15：上游未显式指定 max_tokens 时，部分模型（实测智谱 glm-4-flash）
    # 会按保守默认值输出——同一 prompt 不传仅 1384 字、传 8000 时 2530 字，
    # 直接导致正文撞不上 1750 字门槛而被质量门拦下。这里统一兜底一个足够大的上限。
    DEFAULT_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "8000"))
    RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
    INSUFFICIENT_BALANCE_MARKERS = (
        "account balance is insufficient",
        "insufficient balance",
        "余额不足",
        "账户余额不足",
        "account balance",
    )

    def __init__(
        self,
        settings: AppSettings,
        logger: logging.Logger,
        force_mock: bool = False,
        model_override: str = "",
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.force_mock = force_mock
        self.client = None
        # 2026-09-15：支持按实例覆盖模型，用于「标题用强模型、正文用快模型」的分工。
        # 实测：glm-4.6 生成的标题能通过 RULE_006/007/009 全部校验，但写 1750+ 字长文
        # 会反复超时（45 分钟走不完）；glm-4-flash 反之——写作评分 92 但标题全被过滤。
        # 故标题环节单独使用强模型，其余环节保持快模型。
        self.model_override = str(model_override or "").strip()
        # A payment/quota failure is not transient for the remainder of this
        # workflow.  Marking the client unavailable prevents every downstream
        # agent from repeating the same request before falling back locally.
        self._unavailable_reason = ""
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
        return self.client is not None and not self._unavailable_reason

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

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
            "model": self.model,
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
        content = self._extract_response_content(response)
        if looks_like_html_document(content):
            raise AIResponseFormatError(
                "Model response was an HTML document instead of completion text."
            )
        return content

    def create_json_completion(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.available:
            raise AIRequestError("Model client is not available.")

        response = self._request_structured_json(system_prompt=system_prompt, user_prompt=user_prompt)
        self._accumulate_usage(response)
        content = self._extract_message_content(response)
        if looks_like_html_document(content):
            raise AIResponseFormatError(
                "Model response was an HTML document instead of JSON."
            )

        payload = self._parse_json_object(content)
        if payload is None:
            # 保留一小段原始响应，否则这类失败只能看到「未返回合法 JSON」，
            # 无法判断是尾随逗号、截断还是模型跑了别的格式。
            self.logger.warning(
                "Model did not return valid JSON. response_head=%r",
                str(content or "")[:300],
            )
            raise AIResponseFormatError("The model did not return valid JSON.")

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
                model=self.model,
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
                model=self.model,
                temperature=self.settings.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            if self._is_insufficient_balance_error(exc):
                self._mark_unavailable(exc)
                raise AIRequestError(
                    "Model request disabled for this run because the provider account balance is insufficient."
                ) from exc
            self.logger.warning(
                "Structured JSON mode failed (response_format not supported or transient error: %s), "
                "trying prompt-only JSON mode.",
                exc,
            )
            return self._call_with_retry(
                model=self.model,
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
        # 兜底 max_tokens：调用方显式指定时尊重调用方，否则用一个足够大的默认值，
        # 避免上游按保守默认截短长文（详见 DEFAULT_MAX_TOKENS 处说明）。
        if kwargs.get("max_tokens") is None:
            kwargs["max_tokens"] = self.DEFAULT_MAX_TOKENS
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                last_exc = exc
                if self._is_insufficient_balance_error(exc):
                    self._mark_unavailable(exc)
                    raise AIRequestError(
                        "Model request disabled for this run because the provider account balance is insufficient."
                    ) from exc
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

    def _mark_unavailable(self, exc: Exception) -> None:
        reason = str(exc).strip() or "provider account balance is insufficient"
        if not self._unavailable_reason:
            self._unavailable_reason = reason
            # ⚠️ 2026-09-20：把**模型名与原始错误**一起打出来。
            # 这个标志是"本次运行内永久失效"，而它会让所有后续的"补足篇幅"动作静默跳过
            # （日志只显示 `api_available=False`，看不出是谁触发的）。
            # 实测云端因此连续多轮无法把"差 30~50 字"的稿子补过线。
            self.logger.error(
                "Model client disabled for the remainder of this run: model=%s reason=%s",
                self.model,
                reason[:300],
            )

    def _is_insufficient_balance_error(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
        if str(status_code).strip() == "402":
            return True

        message = str(exc).lower()
        if any(marker in message for marker in self.INSUFFICIENT_BALANCE_MARKERS):
            return True
        return bool(re.search(r"(?:error|status|code)[^0-9]{0,12}402\b", message))

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
        usage = self._response_field(response, "usage")
        if usage is None:
            return

        prompt_tokens = int(self._response_field(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(self._response_field(usage, "completion_tokens", 0) or 0)
        total_tokens = int(
            self._response_field(usage, "total_tokens", prompt_tokens + completion_tokens)
            or 0
        )
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

    @property
    def model(self) -> str:
        """本实例实际使用的模型名。model_override 为空时回落到全局配置。"""
        return self.model_override or str(self.settings.openai_model or "")

    def _get_model_type(self) -> tuple[bool, bool]:
        """获取模型类型，返回 (is_deepseek_reasoner, is_deepseek)"""
        model_name = str(self.model or "").lower()
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

    @staticmethod
    def _response_field(value: Any, field: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(field, default)
        return getattr(value, field, default)

    @classmethod
    def _content_to_text(cls, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace").strip()
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, (list, tuple)):
            return "".join(cls._content_to_text(item) for item in content).strip()
        if isinstance(content, Mapping):
            for field in ("text", "content", "value", "output_text"):
                if field in content and content[field] is not None:
                    return cls._content_to_text(content[field])
            return ""
        for field in ("text", "content", "value", "output_text"):
            nested = getattr(content, field, None)
            if nested is not None:
                return cls._content_to_text(nested)
        return str(content).strip()

    def _extract_response_content(self, response: Any) -> str:
        """Extract content from SDK objects and common proxy response shapes.

        Providers and gateways do not all return the OpenAI SDK object.  Some
        return a mapping, a plain string, a response nested under ``data`` or
        ``response``, or content blocks instead of one string.  Keep this
        adapter at the boundary so every agent sees the same text.
        """
        return self._extract_response_content_nested(response, depth=0)

    def _extract_response_content_nested(self, response: Any, depth: int) -> str:
        if depth > 5:
            return ""
        if isinstance(response, (str, bytes)):
            return self._content_to_text(response)
        if response is None:
            return ""

        choices = self._response_field(response, "choices")
        if choices is not None:
            if isinstance(choices, Mapping):
                choice_items = [choices]
            elif isinstance(choices, (list, tuple)):
                choice_items = choices
            else:
                choice_items = [choices]
            for choice in choice_items:
                message = self._response_field(choice, "message")
                if message is None:
                    message = self._response_field(choice, "delta")
                if isinstance(message, (str, bytes)):
                    content = message
                else:
                    content = self._response_field(message, "content") if message is not None else None
                    if content is None and message is not None:
                        content = self._response_field(message, "text")
                if content is None:
                    content = self._response_field(choice, "content")
                if content is not None:
                    text = self._content_to_text(content)
                    if text:
                        return text

        for field in ("output_text", "text", "content"):
            content = self._response_field(response, field)
            if content is not None:
                text = self._content_to_text(content)
                if text:
                    return text

        # A few gateways wrap the actual completion in one or more envelope
        # fields.  Only recurse after checking the direct SDK-like shapes so a
        # legitimate ``response`` string containing JSON is preserved.
        for field in ("response", "data", "result", "output", "payload"):
            nested = self._response_field(response, field)
            if nested is None or nested is response:
                continue
            text = self._extract_response_content_nested(nested, depth + 1)
            if text:
                return text
        return ""

    def _extract_message_content(self, response) -> str:
        content = self._extract_response_content(response)
        if not content:
            return "{}"

        # DeepSeek-style reasoning tags are not part of the JSON payload.
        content = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", content, flags=re.IGNORECASE)
        content = re.sub(r"<think\b[^>]*>[\s\S]*$", "", content, flags=re.IGNORECASE)
        return content.strip() or "{}"

    _MARKDOWN_JSON_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)```")

    @staticmethod
    def _balanced_json_end(text: str, start: int) -> int | None:
        """Return the end offset of a balanced JSON object.

        Braces inside JSON strings must not affect the depth count.  This is
        intentionally a small scanner instead of a greedy regular expression,
        because model explanations often contain unrelated braces.
        """
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
                if depth < 0:
                    return None
        return None

    @classmethod
    def _parse_json_object(cls, content: str) -> dict | None:
        """Parse a JSON object with tolerant handling of model wrappers."""
        cleaned = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", str(content or ""), flags=re.IGNORECASE).strip().lstrip("\ufeff")
        if not cleaned:
            return None

        try:
            payload = json.loads(cleaned)
            return payload if isinstance(payload, dict) else None
        except (TypeError, json.JSONDecodeError):
            pass

        for start, char in enumerate(cleaned):
            if char != "{":
                continue
            end = cls._balanced_json_end(cleaned, start)
            if end is None:
                continue
            block = cleaned[start:end]
            candidates = [block]
            repaired = cls._strip_trailing_commas(block)
            if repaired != block:
                candidates.append(repaired)
            for candidate in candidates:
                try:
                    payload = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return payload
        return None

    @staticmethod
    def _strip_trailing_commas(text: str) -> str:
        """删除 JSON 结构位置上的尾随逗号，字符串内部一律不动。

        模型生成大段 JSON 时最常见的瑕疵是 ``{"a": 1,}`` 与 ``[1, 2,]``，
        标准 json 解析会直接失败并让整条链路回退到本地策略。这里沿用
        _balanced_json_end 的扫描思路，只在字符串之外判定，因此正文里的
        逗号、引号、花括号都不会被误改。
        """
        out: list[str] = []
        in_string = False
        escaped = False
        comma_index: int | None = None
        for char in text:
            if in_string:
                out.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                comma_index = None
                out.append(char)
                continue
            if char == ",":
                comma_index = len(out)
                out.append(char)
                continue
            if char in "}]":
                if comma_index is not None:
                    del out[comma_index]
                    comma_index = None
                out.append(char)
                continue
            if char not in " \t\r\n":
                comma_index = None
            out.append(char)
        return "".join(out)

    def _extract_json_from_markdown(self, content: str) -> dict | None:
        for match in self._MARKDOWN_JSON_PATTERN.finditer(content):
            candidate = match.group(1).strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            payload = self._parse_json_object(candidate)
            if payload is not None:
                return payload
        return None

    def close(self) -> None:
        close_method = getattr(self.client, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self.logger.debug("Failed to close model client cleanly: %s", exc)
