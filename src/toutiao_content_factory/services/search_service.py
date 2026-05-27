from __future__ import annotations

import json
import logging
from typing import Any

from toutiao_content_factory.config import AppSettings


class SearchService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def init_client(self) -> None:
        try:
            from openai import OpenAI
            api_key = getattr(self.settings, 'openai_api_key', '')
            base_url = getattr(self.settings, 'openai_base_url', '')
            if api_key:
                kwargs = {"api_key": api_key}
                if base_url:
                    kwargs["base_url"] = base_url
                self._client = OpenAI(**kwargs)
        except Exception as exc:
            self.logger.warning("SearchService client init failed: %s", exc)

    def search_and_summarize(self, query: str, max_results: int = 5) -> dict[str, Any]:
        result = {
            "query": query,
            "search_performed": False,
            "summaries": [],
            "key_facts": [],
            "sources": [],
            "error": "",
        }
        if not self.available:
            result["error"] = "SearchService not available"
            return result
        try:
            response = self._client.chat.completions.create(
                model=getattr(self.settings, 'openai_model', 'deepseek-chat'),
                temperature=0.3,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个信息检索助手。根据用户提供的查询，"
                            "请基于你的知识库，提供关于该话题的核心事实、关键数据和主要观点。"
                            "输出格式为JSON，包含以下字段：\n"
                            "- key_facts: 核心事实列表（字符串数组，最多5条）\n"
                            "- key_numbers: 关键数据列表（字符串数组，最多3条）\n"
                            "- main_viewpoints: 主要观点列表（字符串数组，最多3条）\n"
                            "- source_hints: 信息来源提示列表（字符串数组，最多3条）\n"
                            "注意：只提供你有较高确信度的信息，不确定的内容不要编造。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"请提供关于以下话题的核心信息：{query}",
                    },
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
            result["search_performed"] = True
            result["key_facts"] = parsed.get("key_facts", [])
            result["key_numbers"] = parsed.get("key_numbers", [])
            result["main_viewpoints"] = parsed.get("main_viewpoints", [])
            result["sources"] = parsed.get("source_hints", [])
            result["summaries"] = parsed.get("key_facts", [])[:3]
        except Exception as exc:
            self.logger.warning("SearchService search_and_summarize failed: %s", exc)
            result["error"] = str(exc)
        return result

    def build_rag_context(self, query: str) -> str:
        result = self.search_and_summarize(query)
        if not result["search_performed"]:
            return ""
        parts = []
        if result.get("key_facts"):
            facts = "\n".join(f"  - {f}" for f in result["key_facts"][:5])
            parts.append(f"核心事实：\n{facts}")
        if result.get("key_numbers"):
            numbers = "、".join(result["key_numbers"][:3])
            parts.append(f"关键数据：{numbers}")
        if result.get("main_viewpoints"):
            views = "\n".join(f"  - {v}" for v in result["main_viewpoints"][:3])
            parts.append(f"主要观点：\n{views}")
        if result.get("sources"):
            sources = "、".join(result["sources"][:3])
            parts.append(f"信息来源提示：{sources}")
        return "\n\n".join(parts)

    def close(self) -> None:
        self._client = None
