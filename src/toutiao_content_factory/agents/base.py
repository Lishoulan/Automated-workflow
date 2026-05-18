from __future__ import annotations

import json
import logging
import re

from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import CacheContext
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class BaseAgent:
    agent_name = "BaseAgent"
    INTERNAL_LABEL_MAP = {
        "humanoid_robot": "人形机器人",
        "new_energy": "新能源",
        "energy_storage": "储能",
        "ai_hardware": "AI硬件",
        "industrial_automation": "工业自动化",
    }
    META_NOISE_PREFIXES = (
        "账号定位：",
        "账号：",
        "账号名：",
        "以下是今日自动选中的热点选题",
        "写作要求：",
        "固定写法：",
        "推荐主结论：",
        "跟踪变量：",
        "入选理由：",
        "请围绕这条产业线索",
        "请重点分析",
        "主赛道：",
        "主关键词：",
        "匹配赛道：",
        "匹配关键词：",
        "可确认数字：",
        "标题：",
        "来源：",
        "发布日期：",
        "原文链接：",
        "补充片段：",
        "摘要：",
    )

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.prompt_repository = prompt_repository
        self.openai_service = openai_service
        self.logger = logger

    def render_prompt(self, template_name: str, **kwargs: str) -> str:
        return self.prompt_repository.render(template_name, **kwargs)

    def request_json(self, system_prompt: str, user_prompt: str) -> dict | None:
        if not self.openai_service.available:
            return None

        try:
            return self.openai_service.create_json_completion(system_prompt, user_prompt)
        except Exception as exc:
            self.logger.warning("%s 模型调用失败，回退到本地策略: %s", self.agent_name, exc)
            return None

    def style_digest(self, style: StyleProfile) -> str:
        return (
            f"风格标识: {style.key}\n"
            f"风格名称: {style.label}\n"
            f"语气: {style.tone}\n"
            f"正文要求: {style.article_instruction}\n"
            f"标题要求: {style.title_instruction}\n"
            f"图片要求: {style.image_instruction}\n"
            f"开头要求: {style.opening_instruction}"
        )

    def brand_digest(self) -> str:
        return (
            "写法节奏: 先下判断，再给数据，再拆产业链位置，再落到公司主体和跟踪变量，最后只留一小段风险边界。\n"
            "成文禁忌: 不写泛新闻播报，不堆空形容词，不做无根据拔高。\n"
            "输出禁令: 标题、正文、摘要、评论引导中不得出现账号名、栏目名、作者自称、品牌口号、运营指令。\n"
            f"赛道主线: {self.settings.industry_focus}"
        )

    def cache_note(self, cache_context: CacheContext) -> str:
        if cache_context.exact_hit:
            return "命中精确缓存，本次可以直接复用历史生成结果。"
        if cache_context.similar_hit:
            return (
                f"发现相似历史内容，相似度 {cache_context.similarity:.2f}。"
                f"请避开历史切入角度“{cache_context.avoid_angle}”，并尽量不要复用这些标题："
                f"{'；'.join(cache_context.avoid_titles[:5])}"
            )
        return cache_context.note or "没有历史重复内容约束。"

    def json_text(self, payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def unique(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def normalize_title(self, title: str) -> str:
        return re.sub(r"\s+", "", title).strip("：:，,。.!！？?")

    def clean_publish_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        for raw, label in self.INTERNAL_LABEL_MAP.items():
            cleaned = cleaned.replace(raw, label)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = self._strip_brand_mentions(cleaned)
        return cleaned.strip()

    def strip_prompt_leakage(self, text: str) -> str:
        if not text:
            return ""

        cleaned_lines: list[str] = []
        for raw_line in str(text).splitlines():
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                continue
            if any(line.startswith(prefix) for prefix in self.META_NOISE_PREFIXES):
                continue
            cleaned_lines.append(self._strip_brand_mentions(line))

        cleaned = "\n".join(cleaned_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip()

    def _strip_brand_mentions(self, text: str) -> str:
        cleaned = str(text or "")
        brand = str(self.settings.brand_name or "").strip()
        if brand:
            cleaned = cleaned.replace(brand, "")
        cleaned = re.sub(r"(我们账号|本账号|本号|本栏目|本文作者|笔者认为|作者认为)[:：]?", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"^[，。；：:、\-\s]+", "", cleaned)
        cleaned = re.sub(r"[，。；：:、\-\s]+$", "", cleaned)
        return cleaned.strip()

    def clean_publish_list(self, items: list[str], limit: int | None = None) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            cleaned = self.clean_publish_text(item)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        if limit is None:
            return result
        return result[:limit]

    def is_internal_label(self, text: str) -> bool:
        normalized = self.clean_publish_text(text)
        return normalized in self.INTERNAL_LABEL_MAP.keys()
