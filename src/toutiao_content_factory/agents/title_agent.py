from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport


class TitleAgent(BaseAgent):
    agent_name = "TitleAgent"

    LOW_QUALITY_TOKENS = ("炸裂", "封神", "赢麻", "疯了", "吓人", "彻底变天", "全网都在看")
    PLACEHOLDER_TOKENS = ("这家公司", "某公司", "该公司", "这条赛道")
    CONTRAST_TOKENS = ("不是", "而是", "背后", "真正", "为什么", "谁在", "谁能", "谁会")
    PROFIT_TOKENS = ("利润", "毛利", "盈利", "兑现", "估值", "份额", "红利", "毛利率")
    VALIDATION_TOKENS = ("订单", "交付", "验证", "客户", "量产", "出货", "签约", "开工")
    DEPTH_TOKENS = ("产业链", "卡位", "环节", "壁垒", "拐点", "分化", "重估", "节奏")
    TEMPLATEY_TOKENS = (
        "看懂",
        "这条线",
        "又火了",
        "别忽视",
        "切入",
        "机会来了",
        "开始进入兑现筛选期",
        "开始进入筛选期",
        "全面起飞",
    )
    WEAK_SUFFIXES = ("现在走到哪一步了", "会怎么走", "还能走多远", "怎么看", "是什么信号")

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> dict[str, list[str]]:
        prompt = self.render_prompt(
            "title_prompt_v2.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            style_digest=self.style_digest(style),
            cache_note=self.cache_note(cache_context),
            avoid_titles_json=self.json_text(cache_context.avoid_titles[:8]),
            summary=content.summary,
            article_angle=content.article_angle,
            key_points_json=self.json_text(data_report.key_points[:6]),
        )

        preferred_from_model: list[str] = []
        payload = self.request_json("你是 TitleAgent，只输出 JSON。", prompt)
        if payload:
            titles = self._dedupe_titles(payload.get("titles"), cache_context)
            preferred_from_model = self._dedupe_titles(payload.get("recommended_titles"), cache_context)
        else:
            titles = []

        fallback_titles = self._fallback_titles(content, trend_report, data_report, style, cache_context)
        titles = self._dedupe_titles(titles + fallback_titles, cache_context)
        ranked_titles = self._rank_titles(titles, data_report, preferred_from_model)
        recommended_titles = self._pick_recommended_titles(ranked_titles, data_report)

        return {
            "titles": ranked_titles[: self.settings.title_count],
            "recommended_titles": recommended_titles[: self.settings.recommended_title_count],
        }

    def _fallback_titles(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> list[str]:
        keyword = self._primary_topic_label(data_report)
        secondary = "关键环节"
        for candidate in self.clean_publish_list(data_report.focus_keywords[1:] + data_report.supply_chain_keywords[1:]):
            if candidate and candidate != keyword:
                secondary = candidate
                break

        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else "这组数据"
        company = self._primary_company(data_report)

        templates = [
            f"{number}背后，{keyword}更该看订单还是毛利",
            f"{keyword}热度再起，真正先兑现的是谁",
            f"{keyword}不缺故事，缺的是交付和验证",
            f"{keyword}开始分化，先吃到利润的是哪一环",
            f"{keyword}这轮重估，为什么先看{secondary}",
            f"{keyword}出海升温，真正拉开差距的不是整机",
            f"{keyword}走到验证阶段，先盯哪几个指标",
            f"{keyword}热度背后，谁在卡位{secondary}",
            f"{keyword}赛道里，谁能先把订单变利润",
            f"{keyword}拐点来了？先看客户验证和交付",
            f"{keyword}景气回升后，谁会先掉队",
            f"{keyword}升温之后，市场重新奖励谁",
            f"{keyword}不是都能赚钱，先看{secondary}",
            f"{keyword}真正要拼的，不是概念而是兑现",
        ]
        if company:
            templates.extend(
                [
                    f"{company}火了之后，{keyword}还能走成业绩吗",
                    f"{company}背后，{keyword}真正的红利在哪",
                    f"{company}所在环节，正在重写{keyword}逻辑",
                ]
            )

        if style.key == "finance_report":
            templates[:3] = [
                f"{keyword}重估的起点，为何先落在{secondary}",
                f"{number}之后，{keyword}更该看利润兑现",
                f"{keyword}走到业绩验证期，谁更容易受益",
            ]
        elif style.key == "viral_science":
            templates[:3] = [
                f"{keyword}又热了，但先赚钱的未必是热门公司",
                f"{number}背后，{keyword}真正的赢家会是谁",
                f"{keyword}看着很热，真正关键的是交付",
            ]
        elif style.key == "tech_media":
            templates[:3] = [
                f"{keyword}热度回升，真正变化发生在哪一环",
                f"{keyword}走到验证阶段，市场开始重选公司",
                f"{keyword}背后，谁在吃交付效率的红利",
            ]

        return self._dedupe_titles(templates, cache_context)

    def _rank_titles(
        self,
        titles: list[str],
        data_report: DataReport,
        preferred_titles: list[str],
    ) -> list[str]:
        preferred_set = {self.normalize_title(item) for item in preferred_titles}
        return sorted(
            titles,
            key=lambda item: self._title_score(item, data_report, preferred_set),
            reverse=True,
        )

    def _pick_recommended_titles(self, titles: list[str], data_report: DataReport) -> list[str]:
        selected: list[str] = []
        seen_buckets: set[str] = set()
        for title in titles:
            bucket = self._title_bucket(title, data_report)
            if bucket in seen_buckets:
                continue
            selected.append(title)
            seen_buckets.add(bucket)
            if len(selected) >= self.settings.recommended_title_count:
                return selected

        for title in titles:
            if title in selected:
                continue
            selected.append(title)
            if len(selected) >= self.settings.recommended_title_count:
                break
        return selected

    def _title_score(
        self,
        title: str,
        data_report: DataReport,
        preferred_titles: set[str],
    ) -> int:
        click_score = 46
        save_score = 48
        discussion_score = 38
        brand_fit_score = 50

        normalized = self.normalize_title(title)
        if normalized in preferred_titles:
            click_score += 5
            brand_fit_score += 4

        if any(char.isdigit() for char in title):
            click_score += 8
            save_score += 4

        if "？" in title or "?" in title:
            click_score += 3
            discussion_score += 6

        if any(token in title for token in self.CONTRAST_TOKENS):
            click_score += 8
            discussion_score += 4

        if any(token in title for token in self.PROFIT_TOKENS):
            save_score += 10
            brand_fit_score += 8

        if any(token in title for token in self.VALIDATION_TOKENS):
            save_score += 8
            brand_fit_score += 6

        if any(token in title for token in self.DEPTH_TOKENS):
            save_score += 5
            brand_fit_score += 7

        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            click_score += 7
            brand_fit_score += 8

        keywords = self.clean_publish_list(data_report.focus_keywords + data_report.supply_chain_keywords)
        if any(keyword and keyword in title for keyword in keywords[:5]):
            brand_fit_score += 8
        else:
            brand_fit_score -= 8

        length = len(title)
        max_length = int(self.settings.publishing.title_max_length)
        if 16 <= length <= 28:
            click_score += 8
            brand_fit_score += 6
        elif 13 <= length <= max_length:
            click_score += 2
        else:
            click_score -= 12
            brand_fit_score -= 10

        if any(token in title for token in self.TEMPLATEY_TOKENS):
            click_score -= 12
            brand_fit_score -= 14

        if any(token in title for token in self.LOW_QUALITY_TOKENS):
            click_score -= 18
            brand_fit_score -= 20

        if any(raw in title for raw in self.INTERNAL_LABEL_MAP.keys()):
            click_score -= 16
            brand_fit_score -= 20

        total = round(
            click_score * 0.36
            + save_score * 0.30
            + discussion_score * 0.14
            + brand_fit_score * 0.20
        )
        return max(1, total)

    def _title_bucket(self, title: str, data_report: DataReport) -> str:
        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            return "company"
        if any(token in title for token in self.PROFIT_TOKENS):
            return "profit"
        if any(token in title for token in self.VALIDATION_TOKENS):
            return "validation"
        if any(token in title for token in self.CONTRAST_TOKENS):
            return "contrast"
        return "general"

    def _dedupe_titles(self, payload: object, cache_context: CacheContext) -> list[str]:
        if isinstance(payload, list):
            raw_titles = [str(item).strip() for item in payload if str(item).strip()]
        else:
            raw_titles = []

        normalized_avoid = {self.normalize_title(item) for item in cache_context.avoid_titles}
        result: list[str] = []
        seen: set[str] = set()
        for title in raw_titles:
            cleaned_title = self._sanitize_title(title)
            normalized = self.normalize_title(cleaned_title)
            if not normalized or normalized in seen or normalized in normalized_avoid:
                continue
            if any(token in cleaned_title for token in self.LOW_QUALITY_TOKENS + self.PLACEHOLDER_TOKENS):
                continue
            if any(token in cleaned_title for token in self.TEMPLATEY_TOKENS):
                continue
            seen.add(normalized)
            result.append(cleaned_title)
        return result

    def _sanitize_title(self, title: str) -> str:
        cleaned = self.strip_prompt_leakage(self.clean_publish_text(title))
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
        cleaned = re.sub(r"^\d{1,2}[\.、]\s+", "", cleaned)
        cleaned = cleaned.replace("“", "").replace("”", "").replace("\"", "")
        cleaned = cleaned.replace("《", "").replace("》", "").replace("【", "").replace("】", "")
        cleaned = re.sub(r"\s+", "", cleaned).strip("，。！？；：:、- ")
        for suffix in self.WEAK_SUFFIXES:
            cleaned = cleaned.replace(suffix, "")
        return self._fit_title_length(cleaned)

    def _fit_title_length(self, title: str) -> str:
        max_length = max(1, int(self.settings.publishing.title_max_length))
        if len(title) <= max_length:
            return title

        segments = [segment.strip() for segment in re.split(r"[：:，,；;！？!?]", title) if segment.strip()]
        if segments:
            candidate = segments[0]
            if 12 <= len(candidate) <= max_length:
                return candidate
            combined = ""
            for segment in segments:
                joined = f"{combined}，{segment}" if combined else segment
                if len(joined) > max_length:
                    break
                combined = joined
            if 12 <= len(combined) <= max_length:
                return combined

        return title[:max_length].rstrip("，。！？；：:、- ")

    def _primary_topic_label(self, data_report: DataReport) -> str:
        preferred = self.clean_publish_text(data_report.focus_industry)
        if preferred and not self.is_internal_label(preferred):
            return preferred

        candidates = self.clean_publish_list(
            data_report.focus_keywords + data_report.supply_chain_keywords,
            limit=10,
        )
        for item in candidates:
            if item and not self.is_internal_label(item):
                return item
        return candidates[0] if candidates else "科技制造"

    def _primary_company(self, data_report: DataReport) -> str:
        for item in self.clean_publish_list(data_report.focus_companies + data_report.companies, limit=4):
            if item and item not in {"某公司", "该公司", "头部公司"}:
                return item
        return ""
