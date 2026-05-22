from __future__ import annotations

import json
import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService


class TitleAgent(BaseAgent):
    agent_name = "TitleAgent"

    LOW_QUALITY_TOKENS = ("炸裂", "封神", "赢麻", "疯了", "吓人", "彻底变天", "全网都在看")
    PLACEHOLDER_TOKENS = ("这家公司", "某公司", "该公司", "这条赛道")
    CONTRAST_TOKENS = ("不是", "而是", "背后", "真正", "为什么", "谁在", "谁能", "谁会")
    PROFIT_TOKENS = ("利润", "毛利", "盈利", "兑现", "估值", "份额", "红利", "毛利率")
    VALIDATION_TOKENS = ("订单", "交付", "验证", "客户", "量产", "出货", "签约", "开工")
    DEPTH_TOKENS = ("产业链", "卡位", "环节", "壁垒", "拐点", "分化", "重估", "节奏")
    SCENE_TOKENS = ("机场", "电厂", "电站", "港口", "车间", "仓库", "园区", "工厂", "基地", "物流中心", "示范项目")
    BENEFIT_SHIFT_TOKENS = ("系统集成", "零部件", "整机", "运维", "回款", "议价", "利润", "毛利", "份额")
    INTERNATIONAL_TOKENS = ("国际", "美国", "欧洲", "俄罗斯", "乌克兰", "中东", "关税", "制裁", "外交", "局势", "油价", "黄金")
    SPORTS_TOKENS = ("体育", "足球", "篮球", "国足", "男篮", "女篮", "欧冠", "英超", "西甲", "中超", "NBA", "世界杯", "奥运", "乒乓", "羽毛球")
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
        "更该看订单还是毛利",
        "真正先兑现的是谁",
        "先看客户验证和交付",
        "开始分化，先吃到利润的是哪一环",
    )
    WEAK_SUFFIXES = ("现在走到哪一步了", "会怎么走", "还能走多远", "怎么看", "是什么信号")

    MODE_NOISE_TOKENS = ("产业链", "订单", "交付", "毛利", "利润", "兑现", "科技制造", "融资")

    def __init__(self, settings, prompt_repository, openai_service, logger) -> None:
        super().__init__(settings, prompt_repository, openai_service, logger)
        self.growth_ops_service = GrowthOpsService(settings, logger)

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> dict[str, list[str]]:
        playbook_context = self._build_title_playbook_context(data_report)
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
            playbook_track=playbook_context["track"],
            playbook_keywords_json=self.json_text(playbook_context["top_keywords"]),
            title_templates_json=self.json_text(playbook_context["title_templates"]),
            topic_imitation_json=self.json_text(playbook_context["topic_imitation_pool"]),
            benchmark_titles_json=self.json_text(playbook_context["example_titles"]),
            title_playbook_note=playbook_context["note"],
        )

        preferred_from_model: list[str] = []
        payload = self.request_json("你是 TitleAgent，只输出 JSON。", prompt)
        if payload:
            titles = self._dedupe_titles(payload.get("titles"), cache_context)
            preferred_from_model = self._dedupe_titles(payload.get("recommended_titles"), cache_context)
        else:
            titles = []

        fallback_titles = self._fallback_titles(
            content,
            trend_report,
            data_report,
            style,
            cache_context,
            playbook_context=playbook_context,
        )
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
        playbook_context: dict[str, object] | None = None,
    ) -> list[str]:
        mode = self._content_mode(data_report)
        if mode == "sports":
            return self._sports_fallback_titles(content, data_report, cache_context)
        if mode == "international":
            return self._international_fallback_titles(content, data_report, cache_context)
        keyword = self._primary_topic_label(data_report)
        secondary = "关键环节"
        for candidate in self.clean_publish_list(data_report.focus_keywords[1:] + data_report.supply_chain_keywords[1:]):
            if candidate and candidate != keyword:
                secondary = candidate
                break

        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else "这组数据"
        company = self._primary_company(data_report)
        scene = self._primary_scene(content, data_report)
        angle = self.clean_publish_text(content.article_angle or trend_report.recommended_angle).rstrip("。")

        templates: list[str] = []
        if playbook_context:
            templates.extend(self._adapt_playbook_titles(playbook_context, data_report))

        templates.extend(
            [
            f"{scene or keyword}背后，谁先吃到{keyword}红利",
            f"{keyword}不是都能赚钱，先看{secondary}",
            f"{keyword}热起来后，利润先向哪一环转移",
            f"{keyword}走到兑现期，谁在重写分工",
            f"{keyword}升温之后，先突围的不是热门公司",
            f"{scene or keyword}把{keyword}带到了哪一步",
            f"{keyword}这轮重估，先拉开差距的是谁",
            f"{keyword}热度之外，谁在拿走真正订单",
            f"{keyword}景气抬升后，谁先把回款做实",
            f"{keyword}不是比概念，是真在比交付",
            f"{number}之后，{keyword}开始重排利润",
            f"{keyword}爆单之前，谁先卡住{secondary}",
            f"{keyword}走到验证期，谁会先掉队",
            f"{keyword}真正的胜负手，不在整机",
            ]
        )
        if company:
            templates.extend(
                [
                    f"{company}之后，{keyword}利润流向变了",
                    f"{company}背后，谁在重写{keyword}分工",
                    f"{company}拿单后，{keyword}谁最先受益",
                ]
            )
        if scene:
            templates.extend(
                [
                    f"{scene}成了{keyword}新试验场",
                    f"{scene}落地后，{keyword}谁先兑现",
                    f"{scene}订单背后，{keyword}谁在抢跑",
                ]
            )
        if angle and len(angle) <= 26:
            templates.append(angle)

        if style.key == "finance_report":
            templates[:3] = [
                f"{keyword}重估先落在哪一环",
                f"{number}之后，{keyword}利润先给谁",
                f"{keyword}验证期里，谁更容易兑现",
            ]
        elif style.key == "viral_science":
            templates[:3] = [
                f"{keyword}很热，但先赚钱的未必是热门公司",
                f"{number}背后，{keyword}谁才是真赢家",
                f"{keyword}看着热，关键却在交付",
            ]
        elif style.key == "tech_media":
            templates[:3] = [
                f"{keyword}热度回升，真正变化发生在哪",
                f"{keyword}走到验证期，市场开始重选公司",
                f"{keyword}背后，谁在吃交付红利",
            ]

        return self._dedupe_titles(templates, cache_context)

    def _build_title_playbook_context(self, data_report: DataReport) -> dict[str, object]:
        track_label = self._infer_track_label(data_report)
        selected = self.growth_ops_service.get_title_playbook_track_context(track_label)
        benchmark_accounts = [str(item).strip() for item in selected.get("benchmark_accounts", []) if str(item).strip()]
        self_benchmark_titles = [
            str(item).strip() for item in selected.get("self_benchmark_titles", []) if str(item).strip()
        ]
        note_parts = [
            "先学习这些对标账号的标题结构、判断感和主体感，再结合当前文章信息重写",
            f"可参考账号：{'、'.join(benchmark_accounts[:5]) or '当前赛道对标库'}",
        ]
        if self_benchmark_titles:
            note_parts.append(f"优先吸收你自己的已验证样本：{'；'.join(self_benchmark_titles[:2])}")
        note_parts.append("禁止直接照抄样本标题，禁止只替换一个名词。")
        return {
            "track": str(selected.get("track", track_label)).strip() or track_label,
            "top_keywords": [str(item).strip() for item in selected.get("top_keywords", []) if str(item).strip()][:8],
            "title_templates": [str(item).strip() for item in selected.get("title_templates", []) if str(item).strip()][:8],
            "topic_imitation_pool": [
                str(item).strip() for item in selected.get("topic_imitation_pool", []) if str(item).strip()
            ][:8],
            "example_titles": [str(item).strip() for item in selected.get("example_titles", []) if str(item).strip()][:10],
            "self_benchmark_titles": self_benchmark_titles[:5],
            "self_benchmark_patterns": [
                str(item).strip() for item in selected.get("self_benchmark_patterns", []) if str(item).strip()
            ][:5],
            "note": "；".join(note_parts),
        }

    def _load_title_playbook_payload(self) -> dict[str, object]:
        path = self.settings.output_dir / "growth_ops" / "title_playbook.json"
        if not path.exists():
            try:
                self.growth_ops_service.build_benchmark_report()
            except Exception as exc:
                self.logger.warning("无法自动构建标题打法库，回退到默认标题策略: %s", exc)
                return {}
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("读取标题打法库失败，回退到默认标题策略: %s", exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _infer_track_label(self, data_report: DataReport) -> str:
        text = " ".join(
            [
                data_report.focus_industry,
                data_report.topic_title,
                *data_report.focus_keywords[:6],
                *data_report.supply_chain_keywords[:6],
                *data_report.key_points[:4],
            ]
        )
        normalized = self.clean_publish_text(text)
        if any(token in normalized for token in ("机器人", "人形", "具身", "减速器", "灵巧手", "关节", "伺服")):
            return "AI机器人"
        if any(token in normalized for token in ("储能", "光伏", "锂电", "电池", "逆变器", "PCS", "风电", "充电")):
            return "新能源储能"
        if any(token in normalized for token in self.INTERNATIONAL_TOKENS):
            return "国际热点"
        if any(token in normalized for token in self.SPORTS_TOKENS):
            return "体育赛事"
        return "科技商业"

    def _adapt_playbook_titles(self, playbook_context: dict[str, object], data_report: DataReport) -> list[str]:
        keyword = self._playbook_subject_label(data_report, playbook_context)
        result: list[str] = []
        for raw_title in playbook_context.get("title_templates", []):
            title = self._adapt_playbook_title(str(raw_title).strip(), keyword, playbook_context)
            if title:
                result.append(title)
        return result

    def _playbook_subject_label(self, data_report: DataReport, playbook_context: dict[str, object]) -> str:
        track = str(playbook_context.get("track", "")).strip()
        focus_industry = self.clean_publish_text(data_report.focus_industry).strip()
        if track == "AI机器人":
            if "机器人" in focus_industry or "具身" in focus_industry:
                return focus_industry
            return "人形机器人"
        if track == "新能源储能":
            if "储能" in focus_industry or "新能源" in focus_industry or "光伏" in focus_industry:
                return focus_industry
            return "储能"
        if track == "国际热点":
            if "国际" in focus_industry or "地缘" in focus_industry:
                return focus_industry
            return "国际局势"
        if track == "体育赛事":
            if "体育" in focus_industry or "足球" in focus_industry or "篮球" in focus_industry:
                return focus_industry
            return "体育赛事"
        if focus_industry and not self.is_internal_label(focus_industry):
            return focus_industry
        return self._primary_topic_label(data_report)

    def _adapt_playbook_title(
        self,
        title: str,
        keyword: str,
        playbook_context: dict[str, object],
    ) -> str:
        track = str(playbook_context.get("track", "")).strip()
        adapted = title
        if track == "AI机器人":
            if keyword and keyword not in {"人形机器人", "机器人"}:
                adapted = adapted.replace("人形机器人", keyword)
                adapted = adapted.replace("机器人", keyword)
        elif track == "新能源储能":
            if keyword and keyword not in {"储能", "储能项目"}:
                adapted = adapted.replace("储能项目", keyword)
                adapted = adapted.replace("储能", keyword)
        elif track == "国际热点":
            if keyword and keyword not in {"国际局势", "国际热点"}:
                adapted = adapted.replace("国际局势", keyword)
                adapted = adapted.replace("国际热点", keyword)
        elif track == "体育赛事":
            if keyword and keyword not in {"体育赛事", "比赛"}:
                adapted = adapted.replace("体育赛事", keyword)
                adapted = adapted.replace("比赛", keyword)
        else:
            if keyword and keyword not in {"机器人", "科技制造"}:
                adapted = adapted.replace("机器人", keyword)
        return self._fit_title_length(adapted.strip())

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

        if any(token in title for token in self.SCENE_TOKENS):
            click_score += 8
            discussion_score += 4
            brand_fit_score += 5

        if any(token in title for token in self.BENEFIT_SHIFT_TOKENS):
            save_score += 6
            brand_fit_score += 5
        if self._content_mode(data_report) in {"sports", "international"} and any(
            token in title for token in self.MODE_NOISE_TOKENS
        ):
            click_score -= 20
            save_score -= 18
            brand_fit_score -= 24

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

        if title.count("，") >= 2:
            click_score -= 4
            brand_fit_score -= 4

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

    def _primary_scene(self, content: GeneratedContent, data_report: DataReport) -> str:
        candidates = [
            content.summary,
            content.article,
            *data_report.core_data[:3],
            *data_report.evidence_notes[:3],
            data_report.topic_title,
        ]
        text = " ".join(self.clean_publish_text(item) for item in candidates if item)
        for token in self.SCENE_TOKENS:
            if token in text:
                index = text.find(token)
                start = max(0, index - 8)
                snippet = text[start : index + len(token)]
                snippet = re.sub(r"[，。；：:、\s]+", "", snippet)
                if 2 <= len(snippet) <= 14:
                    return snippet
                return token
        return ""

    def _content_mode(self, data_report: DataReport) -> str:
        combined = " ".join(
            str(item)
            for item in (
                data_report.focus_industry,
                data_report.topic_title,
                data_report.selection_reason,
                data_report.writing_brief,
                " ".join(data_report.focus_keywords[:4]),
                " ".join(data_report.supply_chain_keywords[:4]),
            )
            if str(item).strip()
        ).lower()
        if any(token in combined for token in ("体育", "足球", "篮球", "网球", "world cup", "football", "tennis", "french open")):
            return "sports"
        if any(token in combined for token in ("国际", "美国", "欧洲", "关税", "外交", "white house", "tariff")):
            return "international"
        return "generic"

    def _sports_fallback_titles(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> list[str]:
        subject = self._sports_subject_label(data_report)
        templates = [
            f"{subject}名单背后谁最难受",
            f"{subject}这次到底在赌什么",
            f"{subject}最该盯的不是热搜",
            f"{subject}后续走势怎么变",
            f"{subject}争议点到底在哪",
            f"{subject}会改写比赛预期吗",
            f"{subject}关键变量已经变了",
            f"{subject}最影响谁的判断",
        ]
        angle = self.clean_publish_text(content.article_angle).strip()
        if angle and len(angle) <= int(self.settings.publishing.title_max_length):
            templates.append(angle)
        return self._dedupe_titles(templates, cache_context)

    def _international_fallback_titles(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        cache_context: CacheContext,
    ) -> list[str]:
        subject = self._international_subject_label(data_report)
        templates = [
            f"{subject}背后谁在施压",
            f"{subject}下一步会怎么走",
            f"{subject}真正的信号出现了",
            f"{subject}不只是表态那么简单",
            f"{subject}最该盯的变量变了",
            f"{subject}会把局势推向哪边",
            f"{subject}市场为什么先紧张",
            f"{subject}谈判窗口开了吗",
        ]
        angle = self.clean_publish_text(content.article_angle).strip()
        if angle and len(angle) <= int(self.settings.publishing.title_max_length):
            templates.append(angle)
        return self._dedupe_titles(templates, cache_context)

    def _sports_subject_label(self, data_report: DataReport) -> str:
        text = " ".join(
            [
                self.clean_publish_text(data_report.topic_title),
                *self.clean_publish_list(data_report.focus_keywords + data_report.supply_chain_keywords, limit=6),
            ]
        ).lower()
        if "world cup" in text or "世界杯" in text:
            return "世界杯"
        if "football" in text or "足球" in text:
            return "这份名单"
        if "tennis" in text or "法网" in text:
            return "这次签表"
        return "这场比赛"

    def _international_subject_label(self, data_report: DataReport) -> str:
        text = " ".join(
            [
                self.clean_publish_text(data_report.topic_title),
                *self.clean_publish_list(data_report.focus_keywords + data_report.supply_chain_keywords, limit=6),
            ]
        ).lower()
        if "tariff" in text or "关税" in text:
            return "关税风向"
        if "white house" in text or "美国" in text:
            return "美国表态"
        return "这条国际消息"
