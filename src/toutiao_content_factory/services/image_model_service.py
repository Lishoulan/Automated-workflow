from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from urllib.request import urlopen

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    OpenAI = None

from toutiao_content_factory.config import ImageGenerationSettings
from toutiao_content_factory.models import ImageArtifacts, VisualBrief


class ModelImageGenerationService:
    def __init__(self, settings: ImageGenerationSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.client = self._build_client()

    @property
    def available(self) -> bool:
        return self.client is not None

    def generate(self, brief: VisualBrief, image_dir: Path) -> ImageArtifacts:
        if not self.available:
            raise RuntimeError("Image model client is not available.")

        image_dir.mkdir(parents=True, exist_ok=True)
        cover_png = image_dir / "cover.png"
        infographic_1 = image_dir / "infographic_1.png"
        infographic_2 = image_dir / "infographic_2.png"
        max_assets = max(1, min(self.settings.max_generated_assets, 3))

        cover_prompt = self._build_cover_prompt(brief)
        infographic_metrics_prompt = self._build_metrics_prompt(brief)
        infographic_story_prompt = self._build_story_prompt(brief)

        self._generate_image(cover_prompt, self.settings.cover_size, cover_png)
        infographic_pngs: list[Path] = []
        if max_assets >= 2:
            self._generate_image(infographic_metrics_prompt, self.settings.infographic_size, infographic_1)
            infographic_pngs.append(infographic_1)
        else:
            self._remove_if_exists(infographic_1)

        if max_assets >= 3:
            self._generate_image(infographic_story_prompt, self.settings.infographic_size, infographic_2)
            infographic_pngs.append(infographic_2)
        else:
            self._remove_if_exists(infographic_2)
            self._remove_if_exists(infographic_2.with_suffix(".svg"))

        if max_assets < 2:
            self._remove_if_exists(infographic_1)
            self._remove_if_exists(infographic_1.with_suffix(".svg"))
        else:
            self._remove_if_exists(infographic_1.with_suffix(".svg"))

        self._remove_if_exists(cover_png.with_suffix(".svg"))
        if max_assets < 3:
            self._remove_if_exists(infographic_2.with_suffix(".svg"))

        return ImageArtifacts(
            cover_png=cover_png,
            infographic_pngs=infographic_pngs,
        )

    def _remove_if_exists(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            self.logger.warning("Failed to remove stale image artifact: %s", path)

    def _build_client(self):
        if OpenAI is None:
            return None
        api_key = self.settings.model_api_key.strip()
        if not api_key:
            return None
        client_kwargs = {"api_key": api_key}
        if self.settings.model_base_url.strip():
            client_kwargs["base_url"] = self.settings.model_base_url.strip()
        return OpenAI(**client_kwargs)

    def _generate_image(self, prompt: str, size: str, path: Path) -> None:
        response = self._request_image(prompt=prompt, size=size)
        image_bytes = self._extract_image_bytes(response)
        if not image_bytes:
            raise RuntimeError("Image API returned neither base64 data nor a downloadable image URL.")
        path.write_bytes(image_bytes)
        self.logger.info("Generated image via model backend: %s", path)

    def _request_image(self, prompt: str, size: str) -> Any:
        api_mode = (self.settings.api_mode or "images").strip().lower()
        model_name = self.settings.model_name.strip()
        if api_mode == "auto":
            try:
                return self.client.responses.create(
                    model=model_name,
                    input=prompt,
                    tools=[
                        {
                            "type": "image_generation",
                            "size": size,
                            "quality": self.settings.quality,
                            "background": self.settings.background,
                        }
                    ],
                )
            except Exception as exc:
                self.logger.warning("Auto image mode failed on responses API, falling back to images API: %s", exc)
                api_mode = "images"

        if api_mode == "responses":
            return self.client.responses.create(
                model=model_name,
                input=prompt,
                tools=[
                    {
                        "type": "image_generation",
                        "size": size,
                        "quality": self.settings.quality,
                        "background": self.settings.background,
                    }
                ],
            )
        request_kwargs = {
            "model": model_name,
            "prompt": prompt,
            "size": size,
            "quality": self.settings.quality,
            "style": self.settings.style,
            "background": self.settings.background,
            "output_format": "png",
            "response_format": "b64_json",
        }
        try:
            return self.client.images.generate(**request_kwargs)
        except Exception as exc:
            self.logger.warning(
                "Image generation with response_format=b64_json failed, retrying without strict response_format: %s",
                exc,
            )
            request_kwargs.pop("response_format", None)
            return self.client.images.generate(**request_kwargs)

    def _extract_image_bytes(self, response: Any) -> bytes | None:
        b64_data = self._extract_b64_payload(response)
        if b64_data:
            return base64.b64decode(b64_data)

        image_url = self._extract_image_url(response)
        if image_url:
            return self._download_image_bytes(image_url)
        return None

    def _extract_b64_payload(self, response: Any) -> str | None:
        data = getattr(response, "data", None)
        if data:
            item = data[0]
            return getattr(item, "b64_json", None)

        output = getattr(response, "output", None) or []
        for item in output:
            result = getattr(item, "result", None)
            if result:
                if isinstance(result, str):
                    return result
                for entry in result:
                    image_base64 = getattr(entry, "image_base64", None)
                    if image_base64:
                        return image_base64
                    if isinstance(entry, dict):
                        if entry.get("image_base64"):
                            return entry["image_base64"]
                        if entry.get("b64_json"):
                            return entry["b64_json"]
            content = getattr(item, "content", None)
            if content:
                for entry in content:
                    image_base64 = getattr(entry, "image_base64", None)
                    if image_base64:
                        return image_base64
                    b64_json = getattr(entry, "b64_json", None)
                    if b64_json:
                        return b64_json
        if isinstance(response, dict):
            payload = response.get("data") or []
            if payload:
                return payload[0].get("b64_json")
            for item in response.get("output", []):
                result = item.get("result")
                if isinstance(result, str):
                    return result
                for entry in result or []:
                    if entry.get("image_base64"):
                        return entry["image_base64"]
                    if entry.get("b64_json"):
                        return entry["b64_json"]
                for entry in item.get("content", []) or []:
                    if entry.get("image_base64"):
                        return entry["image_base64"]
                    if entry.get("b64_json"):
                        return entry["b64_json"]
        return None

    def _extract_image_url(self, response: Any) -> str | None:
        data = getattr(response, "data", None)
        if data:
            item = data[0]
            for attr in ("url", "image_url"):
                value = getattr(item, attr, None)
                if value:
                    return str(value)

        output = getattr(response, "output", None) or []
        for item in output:
            result = getattr(item, "result", None)
            if result:
                for entry in result:
                    for attr in ("url", "image_url"):
                        value = getattr(entry, attr, None)
                        if value:
                            return str(value)
            content = getattr(item, "content", None)
            if content:
                for entry in content:
                    for attr in ("url", "image_url"):
                        value = getattr(entry, attr, None)
                        if value:
                            return str(value)

        if isinstance(response, dict):
            payload = response.get("data") or []
            if payload:
                item = payload[0]
                if isinstance(item, dict):
                    for key in ("url", "image_url"):
                        if item.get(key):
                            return str(item[key])
            for item in response.get("output", []):
                if not isinstance(item, dict):
                    continue
                for group_key in ("result", "content"):
                    for entry in item.get(group_key, []) or []:
                        if not isinstance(entry, dict):
                            continue
                        for key in ("url", "image_url"):
                            if entry.get(key):
                                return str(entry[key])
        return None

    def _download_image_bytes(self, image_url: str) -> bytes:
        with urlopen(image_url, timeout=60) as response:
            return response.read()

    def _build_cover_prompt(self, brief: VisualBrief) -> str:
        metrics = "；".join(f"{item.label}{item.value}{item.unit}" for item in brief.key_metrics[:3])
        tags = "、".join(brief.chain_nodes[:5])
        return (
            "生成一张中文科技媒体封面图，适合今日头条文章首图。"
            f"主题：{brief.cover_title}。"
            f"副标题：{brief.cover_subtitle}。"
            f"摘要关键词：{brief.summary}。"
            f"关键数据：{metrics}。"
            f"产业链关键词：{tags}。"
            "画面要求：高级科技感、深色背景、冷蓝青配色、强对比、清晰中文排版、移动端易读、杂志封面级别。"
            "避免过度卡通，避免英文大标题，避免错字，避免信息拥挤，保留足够留白。"
        )

    def _build_metrics_prompt(self, brief: VisualBrief) -> str:
        metrics = "；".join(f"{item.label}{item.value}{item.unit}" for item in brief.key_metrics[:4])
        trends = "；".join(brief.trend_labels[:4])
        return (
            "生成一张竖版中文数据信息图，适合今日头条配图。"
            f"主标题：{brief.article_title}。"
            f"摘要：{brief.summary}。"
            f"核心指标：{metrics}。"
            f"趋势观察：{trends}。"
            "设计要求：科技财经信息图风格、深色底、数据卡片、图表感、分区清晰、中文字体规整、信息层次强、适合手机阅读。"
            "不要生成真人，不要插画海报风，不要英文字母堆砌。"
        )

    def _build_story_prompt(self, brief: VisualBrief) -> str:
        timeline = "；".join(brief.timeline_points[:5])
        highlights = "；".join(brief.topic_highlights[:4])
        return (
            "生成一张竖版中文趋势解读信息图，适合今日头条科技行业分析文章。"
            f"主标题：{brief.cover_title}。"
            f"趋势时间线：{timeline}。"
            f"热点高光：{highlights}。"
            "设计要求：高级深色科技风、时间线布局、模块化卡片、局部发光、图表与标签融合、中文排版自然、画面整洁。"
            "不要杂乱拼贴，不要低质AI海报感，不要把文字做成难以识别的小字。"
        )
