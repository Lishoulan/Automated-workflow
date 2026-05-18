from __future__ import annotations

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport


class TitleAgent(BaseAgent):
    agent_name = "TitleAgent"

    LOW_QUALITY_TOKENS = ("重磅", "炸裂", "封神", "赢麻", "疯了", "吓人", "彻底变天")
    CONTRAST_TOKENS = ("背后", "不是", "而是", "真正", "为什么", "先看", "不是都")
    PROFIT_TOKENS = ("利润", "毛利", "环节", "产业链", "份额", "卡位", "红利", "重估")
    VALIDATION_TOKENS = ("订单", "交付", "验证", "量产", "兑现", "客户", "节奏", "筛选期")
    TEMPLATEY_TOKENS = ("看懂", "这条线", "又火了", "火起来之后", "别忽略", "切入", "这轮机会", "逻辑没结束")
    ANALYSIS_TOKENS = ("商业验证", "验证期", "兑现", "分化", "重估", "筛选期", "下半场")

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
            normalized = candidate.strip()
            if normalized and normalized != keyword:
                secondary = normalized
                break
        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else "增长提速"
        company = (
            self.clean_publish_text(data_report.focus_companies[0])
            if data_report.focus_companies
            else (self.clean_publish_text(data_report.companies[0]) if data_report.companies else "龙头公司")
        )

        templates = [
            f"{number}之后，{keyword}产业链开始进入兑现筛选期",
            f"{keyword}走到商业验证阶段，谁会先把利润做出来？",
            f"{keyword}热度回升，真正拉开差距的是哪一环？",
            f"{keyword}不是都能赚钱，先看{secondary}和交付能力",
            f"{keyword}景气上行后，利润为什么先向{secondary}集中？",
            f"{keyword}订单提速之后，市场会先奖励哪类公司？",
            f"{keyword}从讲故事走到算业绩，中间差了哪一步？",
            f"{keyword}扩产之后，真正稀缺的是交付不是产能",
            f"{keyword}升温之后，谁能先把订单兑现到报表里？",
            f"{keyword}到了拼兑现的时候，哪些公司更容易掉队？",
            f"{keyword}这轮分化刚开始，先受益的未必是整机",
            f"{keyword}利润重估的起点，不在热度而在验证进度",
            f"{keyword}行业进入下半场，市场为什么开始重看{secondary}？",
            f"{company}所在环节，正在成为{keyword}这一轮的关键变量",
            f"{keyword}景气继续上行，最先见真章的是订单和毛利",
            f"{keyword}热度背后，真正该跟的是客户验证和交付节奏",
            f"{keyword}开始进入筛选期，哪些环节会先跑出来？",
            f"{keyword}赚钱方式正在变化，先受益的不一定是最热公司",
            f"{keyword}不只看增速，接下来更看谁能把利润留住",
            f"{keyword}热度抬升之后，市场会重新挑选真正能兑现的公司",
        ]

        if style.key == "finance_report":
            templates[0] = f"{keyword}行业判断：景气上行之后，利润先向关键环节集中"
            templates[1] = f"{number}之后，{keyword}开始进入业绩兑现筛选期"
        elif style.key == "viral_science":
            templates[0] = f"{keyword}成了热点，但先赚到钱的可能不是最热那批"
            templates[1] = f"{number}背后，{keyword}真正的赢家会是谁？"
        elif style.key == "tech_media":
            templates[0] = f"{keyword}热度回升，产业链真正的变化发生在哪？"
            templates[1] = f"{keyword}走到商业验证阶段，市场开始重新挑公司"

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
        click_score = 48
        save_score = 46
        discussion_score = 40
        brand_fit_score = 48

        normalized = self.normalize_title(title)
        if normalized in preferred_titles:
            click_score += 4
            brand_fit_score += 4

        if any(char.isdigit() for char in title):
            click_score += 8
            save_score += 5

        if "？" in title or "?" in title:
            click_score += 4
            discussion_score += 8

        if any(token in title for token in self.CONTRAST_TOKENS):
            click_score += 10
            discussion_score += 4

        if any(token in title for token in self.PROFIT_TOKENS):
            save_score += 10
            brand_fit_score += 8

        if any(token in title for token in self.VALIDATION_TOKENS):
            save_score += 8
            brand_fit_score += 6

        if any(token in title for token in self.ANALYSIS_TOKENS):
            save_score += 4
            brand_fit_score += 6

        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            click_score += 6
            brand_fit_score += 8

        keywords = self.clean_publish_list(data_report.focus_keywords + data_report.supply_chain_keywords)
        if any(keyword and keyword in title for keyword in keywords[:5]):
            brand_fit_score += 8

        length = len(title)
        if 16 <= length <= 28:
            click_score += 6
            brand_fit_score += 6
        elif 13 <= length <= 32:
            click_score += 2
        else:
            click_score -= 6
            brand_fit_score -= 4

        if any(token in title for token in self.TEMPLATEY_TOKENS):
            click_score -= 8
            brand_fit_score -= 10

        if any(token in title for token in self.LOW_QUALITY_TOKENS):
            click_score -= 18
            brand_fit_score -= 24

        if any(raw in title for raw in self.INTERNAL_LABEL_MAP.keys()):
            click_score -= 16
            brand_fit_score -= 20

        total = round(
            click_score * 0.35
            + save_score * 0.30
            + discussion_score * 0.15
            + brand_fit_score * 0.20
        )
        return max(1, total)

    def _title_bucket(self, title: str, data_report: DataReport) -> str:
        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            return "company"
        if any(token in title for token in self.PROFIT_TOKENS):
            return "profit_chain"
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
            cleaned_title = self.strip_prompt_leakage(self.clean_publish_text(title))
            normalized = self.normalize_title(cleaned_title)
            if not normalized or normalized in seen or normalized in normalized_avoid:
                continue
            if any(token in cleaned_title for token in self.LOW_QUALITY_TOKENS):
                continue
            seen.add(normalized)
            result.append(cleaned_title)
        return result

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
