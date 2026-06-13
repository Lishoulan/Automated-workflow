from __future__ import annotations

import json
import re
import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


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
        "更该看订单还是毛利",
        "真正先兑现的是谁",
        "先看客户验证和交付",
        "开始分化，先吃到利润的是哪一环",
    )
    WEAK_SUFFIXES = ("现在走到哪一步了", "会怎么走", "还能走多远", "怎么看", "是什么信号")

    DISCOURAGED_TITLE_PATTERNS = (
        "竟然", "万万没想到", "背后原因", "原来是因为", "天理难容",
        "大变局", "出事了", "震惊", "背后真相", "令人震惊",
        "不敢相信", "看哭了", "太可怕了", "细思极恐",
    )

    CURIOSITY_TOKENS = ("不是", "而是", "背后", "真正", "为什么", "谁在", "谁能", "谁会", "到底", "竟然", "难道", "居然", "反常", "意外", "颠覆", "反转", "没想到", "想不到", "秘密", "真相")
    RATIONAL_TOKENS = ("利润", "毛利", "盈利", "兑现", "估值", "份额", "红利", "毛利率", "订单", "交付", "验证", "客户", "量产", "出货", "签约", "开工", "数据", "增长", "下滑", "同比", "环比", "占比", "规模", "增速", "收入", "营收", "成本", "拐点", "分化", "重估")

    MODE_NOISE_TOKENS = ("产业链", "订单", "交付", "毛利", "利润", "兑现", "科技制造", "融资")

    # 高频同质化模式词组：当标题同时包含同一组内的多个词时，说明结构雷同
    HOMOGENEOUS_PATTERNS = (
        ("利润", "兑现"),
        ("利润", "分化"),
        ("利润", "交付"),
        ("利润", "零部件"),
        ("兑现", "交付"),
        ("兑现", "零部件"),
        ("兑现", "量产"),
        ("分化", "零部件"),
        ("分化", "交付"),
        ("利润", "集中"),
        ("利润", "迁移"),
        ("利润", "转移"),
    )

    # 语义骨架提取用的替换词（标题去重时忽略这些词的具体差异）
    SKELETON_FILLERS = (
        "人形机器人", "机器人", "AI芯片", "芯片", "半导体", "储能", "光伏",
        "新能源", "零部件", "整机", "执行器", "减速器", "传感器", "大脑",
        "核心件", "产业链", "环节",
    )

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        super().__init__(settings, prompt_repository, openai_service, logger)

    def validate_title_compliance(self, title: str) -> bool:
        if len(title) > 30:
            return False
        for pattern in self.DISCOURAGED_TITLE_PATTERNS:
            if pattern in title:
                return False
        return True

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> dict[str, list[str] | str]:
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
        model_title_a = ""
        model_title_b = ""
        system_prompt = f"你是 TitleAgent，只输出 JSON。{self.best_practices_instruction()}"
        payload = self.request_json(system_prompt, prompt)
        if payload:
            titles = self._dedupe_titles(payload.get("titles"), cache_context)
            preferred_from_model = self._dedupe_titles(payload.get("recommended_titles"), cache_context)
            model_title_a = str(payload.get("title_a", "") or "").strip()
            model_title_b = str(payload.get("title_b", "") or "").strip()
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

        dual = self._pick_dual_titles(ranked_titles, data_report, model_title_a, model_title_b, cache_context)

        compliant_titles = [t for t in ranked_titles if self.validate_title_compliance(t)]
        if compliant_titles:
            ranked_titles = compliant_titles

        return {
            "titles": ranked_titles[: self.settings.title_count],
            "recommended_titles": recommended_titles[: self.settings.recommended_title_count],
            "title_a": dual["title_a"],
            "title_b": dual["title_b"],
            "alt_title": dual["title_b"],
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
        keyword = self._primary_topic_label(data_report)
        secondary = "关键环节"
        for candidate in self.clean_publish_list(data_report.focus_keywords[1:] + data_report.supply_chain_keywords[1:]):
            if candidate and candidate != keyword and candidate not in keyword and keyword not in candidate:
                secondary = candidate
                break

        number = self.clean_publish_text(data_report.growth_numbers[0]) if data_report.growth_numbers else "这组数据"
        company = self._primary_company(data_report)
        scene = self._primary_scene(content, data_report)
        angle = self.clean_publish_text(content.article_angle or trend_report.recommended_angle).rstrip("。")

        templates: list[str] = []
        if playbook_context:
            templates.extend(self._adapt_playbook_titles(playbook_context, data_report))

        # 利润/兑现类（控制在 4 条以内）
        templates.extend(
            [
            f"{keyword}热起来后，利润先向哪一环转移",
            f"{number}之后，{keyword}开始重排利润",
            f"{keyword}景气抬升后，谁先把回款做实",
            f"{keyword}真正的胜负手，不在整机",
            ]
        )
        # 技术突破/产品迭代类
        templates.extend(
            [
            f"{keyword}技术路线之争，谁选对了方向",
            f"{keyword}迭代加速，旧方案正在被淘汰",
            f"{keyword}下一代方案已定型，谁在领跑",
            f"{keyword}技术拐点到了，窗口期有多长",
            ]
        )
        # 竞争格局/公司博弈类
        templates.extend(
            [
            f"{keyword}升温之后，先突围的不是热门公司",
            f"{keyword}这轮重估，先拉开差距的是谁",
            f"{keyword}不是比概念，是比谁先跑通",
            f"{keyword}走到验证期，谁会先掉队",
            ]
        )
        # 市场/需求/政策类
        templates.extend(
            [
            f"{keyword}需求端变了，供给还没跟上",
            f"{scene or keyword}把{keyword}带到了哪一步",
            f"{keyword}热度之外，谁在拿走真正订单",
            f"{keyword}爆单之前，谁先卡住{secondary if secondary not in keyword else '关键环节'}",
            ]
        )
        if company:
            templates.extend(
                [
                    f"{company}之后，{keyword}格局变了",
                    f"{company}入场，{keyword}谁最紧张",
                    f"{company}押注{secondary if secondary not in company else '新路线'}，赌对了吗",
                ]
            )
        if scene:
            templates.extend(
                [
                    f"{scene}成了{keyword}新试验场",
                    f"{scene}落地后，{keyword}谁先跑通",
                    f"{scene}背后，{keyword}谁在抢跑",
                ]
            )
        if angle and len(angle) <= 26:
            templates.append(angle)

        if style.key == "finance_report":
            templates[:3] = [
                f"{keyword}重估先落在哪一环",
                f"{number}之后，{keyword}谁先拿到真金白银",
                f"{keyword}验证期里，谁更容易跑出来",
            ]
        elif style.key == "viral_science":
            templates[:3] = [
                f"{keyword}很热，但先跑出来的未必是热门公司",
                f"{number}背后，{keyword}谁才是真赢家",
                f"{keyword}看着热，关键却在{secondary}",
            ]
        elif style.key == "tech_media":
            templates[:3] = [
                f"{keyword}热度回升，真正变化发生在哪",
                f"{keyword}走到验证期，市场开始重选公司",
                f"{keyword}背后，谁在改写竞争规则",
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
        # 首先判断是否是科技相关行业（优先级更高）
        if any(token in normalized for token in ("机器人", "人形", "具身", "减速器", "灵巧手", "关节", "伺服")):
            return "AI机器人"
        if any(token in normalized for token in ("芯片", "半导体", "封测", "晶圆", "EDA", "光刻", "代工", "封测")):
            return "半导体芯片"
        if any(token in normalized for token in ("储能", "光伏", "锂电", "电池", "逆变器", "PCS", "风电", "充电")):
            return "新能源储能"
        if any(token in normalized for token in ("AI应用", "大模型", "LLM", "GPT", "AI搜索", "AI助手", "AI体检", "AI教育", "AI医疗")):
            return "AI应用"
        if any(token in normalized for token in ("智能制造", "工业自动化", "PLC", "MES", "数控", "工业互联网", "工控")):
            return "工业自动化"
        # 只有当只有国际相关词汇，且没有科技相关词汇时，才判断为国际热点
        tech_tokens_count = sum(1 for token in ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "AI", "大模型", "智能制造", "工业自动化"] if token in normalized)
        if tech_tokens_count == 0:
            # 只有国际相关词汇时
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
        if track == "半导体芯片":
            if "芯片" in focus_industry or "半导体" in focus_industry:
                return focus_industry
            return "半导体"
        if track == "新能源储能":
            if "储能" in focus_industry or "新能源" in focus_industry or "光伏" in focus_industry:
                return focus_industry
            return "储能"
        if track == "AI应用":
            if "AI" in focus_industry or "大模型" in focus_industry:
                return focus_industry
            return "AI应用"
        if track == "工业自动化":
            if "自动化" in focus_industry or "制造" in focus_industry:
                return focus_industry
            return "工业自动化"
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
        elif track == "半导体芯片":
            if keyword and keyword not in {"半导体", "芯片"}:
                adapted = adapted.replace("半导体", keyword)
                adapted = adapted.replace("芯片", keyword)
        elif track == "新能源储能":
            if keyword and keyword not in {"储能", "储能项目"}:
                adapted = adapted.replace("储能项目", keyword)
                adapted = adapted.replace("储能", keyword)
        elif track == "AI应用":
            if keyword and keyword not in {"AI应用", "AI"}:
                adapted = adapted.replace("AI应用", keyword)
        elif track == "工业自动化":
            if keyword and keyword not in {"工业自动化", "智能制造"}:
                adapted = adapted.replace("工业自动化", keyword)
                adapted = adapted.replace("智能制造", keyword)
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

    def _pick_dual_titles(
        self,
        ranked_titles: list[str],
        data_report: DataReport,
        model_title_a: str,
        model_title_b: str,
        cache_context: CacheContext,
    ) -> dict[str, str]:
        curiosity_title = ""
        rational_title = ""

        if model_title_a:
            sanitized = self._sanitize_title(model_title_a)
            if sanitized and self.normalize_title(sanitized) not in {self.normalize_title(t) for t in cache_context.avoid_titles}:
                curiosity_title = sanitized
        if model_title_b:
            sanitized = self._sanitize_title(model_title_b)
            if sanitized and self.normalize_title(sanitized) not in {self.normalize_title(t) for t in cache_context.avoid_titles}:
                rational_title = sanitized

        if not curiosity_title or not rational_title:
            for title in ranked_titles:
                is_curiosity = self._is_curiosity_title(title)
                is_rational = self._is_rational_title(title)
                if not curiosity_title and is_curiosity:
                    curiosity_title = title
                if not rational_title and is_rational:
                    rational_title = title
                if curiosity_title and rational_title:
                    break

        if not curiosity_title and ranked_titles:
            curiosity_title = ranked_titles[0]
        if not rational_title and len(ranked_titles) > 1:
            for title in ranked_titles[1:]:
                if title != curiosity_title:
                    rational_title = title
                    break
        if not rational_title and len(ranked_titles) == 1:
            rational_title = ranked_titles[0]

        return {"title_a": curiosity_title, "title_b": rational_title}

    def _is_curiosity_title(self, title: str) -> bool:
        if "？" in title or "?" in title:
            return True
        return any(token in title for token in self.CURIOSITY_TOKENS)

    def _is_rational_title(self, title: str) -> bool:
        if any(char.isdigit() for char in title):
            if any(token in title for token in self.RATIONAL_TOKENS):
                return True
        return any(token in title for token in self.RATIONAL_TOKENS[:14])

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
        seen_skeletons: set[str] = set()
        for title in titles:
            bucket = self._title_bucket(title, data_report)
            skeleton = self._title_skeleton(title)
            if bucket in seen_buckets:
                continue
            if skeleton in seen_skeletons:
                continue
            selected.append(title)
            seen_buckets.add(bucket)
            seen_skeletons.add(skeleton)
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
            save_score += 6
            brand_fit_score += 5

        if any(token in title for token in self.VALIDATION_TOKENS):
            save_score += 5
            brand_fit_score += 4

        if any(token in title for token in self.DEPTH_TOKENS):
            save_score += 4
            brand_fit_score += 5

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

        # 同质化模式惩罚：标题同时包含高频同质词组时大幅降分
        homogeneous_hits = sum(
            1 for pair in self.HOMOGENEOUS_PATTERNS
            if pair[0] in title and pair[1] in title
        )
        if homogeneous_hits >= 2:
            click_score -= 14
            save_score -= 12
            brand_fit_score -= 16
        elif homogeneous_hits == 1:
            click_score -= 6
            save_score -= 5
            brand_fit_score -= 7

        total = round(
            click_score * 0.36
            + save_score * 0.30
            + discussion_score * 0.14
            + brand_fit_score * 0.20
        )
        return max(1, total)

    TECH_TOKENS = ("技术", "迭代", "方案", "路线", "架构", "突破", "拐点", "下一代", "换代", "升级")

    def _title_bucket(self, title: str, data_report: DataReport) -> str:
        companies = self.clean_publish_list(data_report.focus_companies + data_report.companies)
        if any(company and company in title for company in companies[:5]):
            return "company"
        if any(token in title for token in self.TECH_TOKENS):
            return "tech"
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
        seen_skeletons: set[str] = set()
        for title in raw_titles:
            cleaned_title = self._sanitize_title(title)
            normalized = self.normalize_title(cleaned_title)
            if not normalized or normalized in seen or normalized in normalized_avoid:
                continue
            if any(token in cleaned_title for token in self.LOW_QUALITY_TOKENS + self.PLACEHOLDER_TOKENS):
                continue
            if any(token in cleaned_title for token in self.TEMPLATEY_TOKENS):
                continue
            skeleton = self._title_skeleton(cleaned_title)
            if skeleton in seen_skeletons:
                continue
            seen.add(normalized)
            seen_skeletons.add(skeleton)
            result.append(cleaned_title)
        return result

    def _title_skeleton(self, title: str) -> str:
        """提取标题语义骨架：将具体行业/产品词替换为占位符，保留结构模式。

        例如 "人形机器人利润向零部件集中" 和 "AI芯片利润向核心件集中"
        都会变成 "{SUBJ}利润向{SUBJ}集中"，视为同一骨架。
        """
        skeleton = title
        for filler in self.SKELETON_FILLERS:
            if filler in skeleton:
                skeleton = skeleton.replace(filler, "{S}")
        # 压缩连续占位符
        while "{S}{S}" in skeleton:
            skeleton = skeleton.replace("{S}{S}", "{S}")
        return skeleton

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
                snippet = re.sub(r"[（()）\[\]【】，。；：:、\s]+", "", snippet)
                if 2 <= len(snippet) <= 14:
                    return snippet
                return token
        return ""




