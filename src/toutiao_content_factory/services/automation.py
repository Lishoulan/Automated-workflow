from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from toutiao_content_factory.config import AppSettings, sanitize_account_slug
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.schema_guard_service import SchemaGuardService
from toutiao_content_factory.services.topic_pool_service import TopicPoolService


# 内部选题池标识 → 面向 LLM 的人类可读来源名
# 避免把机器标识(如 evergreen_pool、benchmark_hotspot)直接喂给 LLM 导致泄漏到正文
SOURCE_DISPLAY_NAMES = {
    "evergreen_pool": "常青选题库",
    "benchmark_hotspot": "公开报道",
    "manual_paste": "公开材料",
    "topic_pool": "选题库",
}


def _humanize_source(raw_source: str | None) -> str:
    """把内部选题池标识映射为人类可读的来源名,未知标识统一回退为"公开材料"。"""
    if not raw_source:
        return "公开材料"
    key = str(raw_source).strip().lower()
    return SOURCE_DISPLAY_NAMES.get(key, "公开材料")


@dataclass
class TopicSelectionResult:
    report: DailyTopicsReport
    topic: TopicCandidate
    input_text: str
    exported_files: dict[str, Path]


class TopicAutomationService:
    NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    ROBOT_TOKENS = (
        "机器人",
        "人形",
        "具身",
        "灵巧手",
        "减速器",
        "执行器",
        "关节",
        "伺服",
        "机械臂",
        "智元",
        "宇树",
        "优必选",
    )
    ENERGY_TOKENS = (
        "储能",
        "光伏",
        "风电",
        "锂电",
        "电池",
        "逆变器",
        "并网",
        "电站",
        "PCS",
        "EMS",
        "液流电池",
        "超级电容",
        "绿电",
    )
    INTERNATIONAL_TOKENS = (
        "国际",
        "美国",
        "欧洲",
        "俄罗斯",
        "乌克兰",
        "中东",
        "关税",
        "制裁",
        "外交",
        "局势",
        "油价",
        "黄金",
        "停火",
        "谈判",
    )
    INTERNATIONAL_FALSE_POSITIVE_TOKENS = (
        "迈阿密国际",
        "天职国际",
        "国际关系与战略研究所",
        "国际关系学院",
        "国际学校",
        "国际版",
        "国际赛",
    )
    SPORTS_TOKENS = (
        "体育",
        "足球",
        "篮球",
        "国足",
        "男篮",
        "女篮",
        "欧冠",
        "英超",
        "西甲",
        "中超",
        "NBA",
        "世界杯",
        "奥运",
        "乒乓",
        "羽毛球",
        "比分",
        "联赛",
        "转会",
        "季后赛",
        "点球",
        "红牌",
        "football",
        "tennis",
        "transfer",
        "premier league",
        "world cup",
        "wimbledon",
        "french open",
        "grand slam",
        "champion",
        "squad",
        "manager",
    )
    ROBOT_INDUSTRIES = {"humanoid_robot", "ai_hardware", "industrial_automation"}
    ENERGY_INDUSTRIES = {"energy_storage", "new_energy"}
    INTERNATIONAL_INDUSTRIES = {"international"}
    SPORTS_INDUSTRIES = {"sports"}
    INDUSTRY_LABELS = {
        "humanoid_robot": "人形机器人",
        "new_energy": "新能源",
        "energy_storage": "储能",
        "ai_hardware": "AI硬件",
        "industrial_automation": "工业自动化",
        "international": "国际热点",
        "sports": "体育赛事",
    }

    ROBOT_TOKENS = (
        "机器人",
        "人形",
        "具身",
        "灵巧手",
        "减速器",
        "执行器",
        "关节",
        "伺服",
        "机械臂",
        "智元",
        "宇树",
        "优必选",
    )
    ENERGY_TOKENS = (
        "储能",
        "光伏",
        "风电",
        "锂电",
        "电池",
        "逆变器",
        "并网",
        "电站",
        "PCS",
        "EMS",
        "液流电池",
        "超级电容",
        "绿电",
    )
    INTERNATIONAL_TOKENS = (
        "国际",
        "美国",
        "欧洲",
        "俄罗斯",
        "乌克兰",
        "中东",
        "关税",
        "制裁",
        "外交",
        "局势",
        "油价",
        "黄金",
        "停火",
        "谈判",
        "白宫",
        "北约",
    )
    INTERNATIONAL_FALSE_POSITIVE_TOKENS = (
        "迈阿密国际",
        "天职国际",
        "国际关系与战略研究所",
        "国际关系学院",
        "国际学校",
        "国际版",
        "国际足球",
    )
    SPORTS_TOKENS = (
        "体育",
        "足球",
        "篮球",
        "国足",
        "男篮",
        "女篮",
        "欧冠",
        "英超",
        "西甲",
        "中超",
        "NBA",
        "世界杯",
        "奥运",
        "乒乓",
        "羽毛球",
        "比分",
        "联赛",
        "转会",
        "季后赛",
        "点球",
        "红牌",
        "football",
        "tennis",
        "transfer",
        "premier league",
        "world cup",
        "wimbledon",
        "french open",
        "grand slam",
        "champion",
        "squad",
        "manager",
    )
    INDUSTRY_LABELS = {
        "humanoid_robot": "人形机器人",
        "new_energy": "新能源",
        "energy_storage": "储能",
        "ai_hardware": "AI硬件",
        "industrial_automation": "工业自动化",
        "international": "国际热点",
        "sports": "体育赛事",
    }

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.topic_pool_service = TopicPoolService(settings, logger)

    def prepare_auto_run(
        self,
        rank: int | None = None,
        title_query: str | None = None,
        allow_generated_duplicates: bool = False,
    ) -> TopicSelectionResult | None:
        report = self._load_daily_topics()
        topic = self._select_topic(
            report,
            rank=rank,
            title_query=title_query,
            allow_generated_duplicates=allow_generated_duplicates,
        )
        if topic is None:
            self.logger.warning("Auto topic selection failed: no eligible topics found.")
            return None
        input_text = self._build_input_text(topic)
        exported_files = self._write_selection_snapshot(topic, report, input_text)
        self.logger.info("Auto topic selection complete: rank=%s title=%s", topic.rank, topic.title)
        return TopicSelectionResult(
            report=report,
            topic=topic,
            input_text=input_text,
            exported_files=exported_files,
        )

    def archive_run(
        self,
        exported_files: dict[str, str],
        selected_topic: TopicCandidate | None = None,
    ) -> dict[str, Path]:
        archive_root = self._build_archive_root(selected_topic)
        archive_root.mkdir(parents=True, exist_ok=True)
        image_dir = archive_root / "images"
        image_dir.mkdir(parents=True, exist_ok=True)

        archived: dict[str, Path] = {}
        for name, raw_path in exported_files.items():
            source_path = Path(raw_path)
            if not source_path.exists():
                continue
            target_path = self._resolve_archive_target(archive_root, image_dir, source_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            archived[f"archived_{name}"] = target_path

        manifest_path = archive_root / "run_manifest.json"
        manifest_payload = {
            "archived_at": datetime.now().isoformat(timespec="seconds"),
            "selected_topic": asdict(selected_topic) if selected_topic is not None else None,
            "exported_files": exported_files,
            "archived_files": {name: str(path) for name, path in archived.items()},
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if selected_topic is not None:
            self.topic_pool_service.mark_selected(selected_topic, run_dir=archive_root)
        archived["archive_root"] = archive_root
        archived["run_manifest"] = manifest_path
        self.logger.info("Run archived: %s", archive_root)
        return archived

    def _load_daily_topics(self) -> DailyTopicsReport:
        report_path = self.settings.output_dir / "daily_topics.json"
        report: DailyTopicsReport | None = None
        if report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            report = SchemaGuardService.validate_daily_topics_report(payload)
            if report.topics:
                return report

        fallback_report = self.topic_pool_service.build_fallback_report()
        if fallback_report.topics:
            self.logger.warning(
                "daily_topics.json is missing or empty for account=%s. Falling back to local topic pool / evergreen topics.",
                self.settings.account_key,
            )
            return fallback_report

        if not report_path.exists():
            raise FileNotFoundError(
                f"daily_topics.json not found at {report_path}. Run --collect-topics first."
            )
        raise ValueError("daily_topics.json contains no available topics.")

    def _select_topic(
        self,
        report: DailyTopicsReport,
        rank: int | None,
        title_query: str | None,
        allow_generated_duplicates: bool = False,
    ) -> TopicCandidate:
        available_topics = self._available_topics(
            report.topics,
            allow_generated_duplicates=allow_generated_duplicates,
        )
        if not available_topics:
            available_topics = self._available_topics_soft(
                report.topics,
                allow_generated_duplicates=allow_generated_duplicates,
            )
            if available_topics:
                self.logger.warning(
                    "[Topic Engine] Strict filtering yielded no topics for account=%s. A softer fallback pass was activated.",
                    self.settings.account_key,
                )
        if not available_topics:
            self.logger.warning(
                "[Topic Engine] No unpublished topics available for account=%s.",
                self.settings.account_key,
            )
            return None

        if title_query:
            normalized_query = self._normalize_text(title_query)
            for topic in available_topics:
                if normalized_query in self._normalize_text(topic.title):
                    return topic
            self.logger.warning(
                "[Topic Engine] No unpublished topic matched title query: %s. Falling back to rank selection.",
                title_query,
            )

        target_rank = rank or self.settings.automation.default_topic_rank
        if target_rank < 1:
            raise ValueError("--topic-rank must be greater than or equal to 1.")
        if target_rank <= len(available_topics):
            return available_topics[target_rank - 1]
        raise ValueError(
            f"Only {len(available_topics)} unpublished topics remain, cannot select rank {target_rank}."
        )

    def _build_input_text(self, topic: TopicCandidate) -> str:
        humanized_industries = self._humanize_industries(topic.matched_industries)
        industries = "、".join(humanized_industries) if humanized_industries else "相关赛道待补充"
        keywords = "、".join(topic.matched_keywords) if topic.matched_keywords else "关键词待补充"
        numbers = "、".join(topic.extracted_numbers) if topic.extracted_numbers else "暂无明确数字"
        summary = self._preferred_topic_summary(topic)
        snippet = self._preferred_topic_snippet(topic, summary)
        published_at = topic.published_at or "时间未标注"
        primary_industry = humanized_industries[0] if humanized_industries else "相关赛道"
        primary_keywords = "、".join(topic.matched_keywords[:4]) if topic.matched_keywords else keywords
        topic_mode = self._topic_mode(topic)
        selection_reason = self._build_selection_reason(topic, primary_industry, primary_keywords, numbers)
        suggested_angle = self._build_suggested_angle(topic, primary_industry, primary_keywords, numbers)
        follow_up_focus = self._build_follow_up_focus(topic, primary_industry, primary_keywords)
        writing_brief = self._build_writing_brief(topic, primary_industry)
        content_format_label = topic.recommended_format_label or "深度分析"
        format_brief = self._build_content_format_brief(topic, primary_industry)
        paragraph_instruction = self._build_paragraph_instruction(topic)
        quality_flags = "、".join(topic.quality_flags) if topic.quality_flags else "无"
        title_potential_reasons = "、".join(topic.title_potential_reasons) if topic.title_potential_reasons else "无"
        word_target = self._build_word_target(topic)
        prompt_track = self._resolve_prompt_track(topic, primary_industry)
        closing_instruction = self._build_closing_instruction(
            topic,
            topic_mode=topic_mode,
            primary_industry=primary_industry,
        )

        return (
            f"标题：{topic.title}\n"
            f"来源：{_humanize_source(topic.source)}\n"
            f"发布日期：{published_at}\n"
            f"原文链接：{topic.url}\n\n"
            f"主赛道：{primary_industry}\n"
            f"主关键词：{primary_keywords}\n"
            f"匹配赛道：{industries}\n"
            f"匹配关键词：{keywords}\n"
            f"可确认数字：{numbers}\n\n"
            f"选题类型：{topic.topic_type}\n"
            f"内容体裁：{topic.recommended_format}\n"
            f"体裁要求：{format_brief}\n"
            f"段落要求：{paragraph_instruction}\n"
            f"证据强度：{topic.evidence_strength}\n"
            f"选题质量分：{topic.quality_score}\n"
            f"标题潜力分：{topic.title_potential_score}\n"
            f"标题打法赛道：{prompt_track}\n"
            f"标题抓手：{title_potential_reasons}\n"
            f"质量标记：{quality_flags}\n"
            f"建议字数：{word_target}\n\n"
            "素材摘要：\n"
            f"- 摘要：{summary}\n"
            f"- 补充片段：{snippet}\n"
            f"- 热度判断：阅读分 {topic.read_score} / 收藏分 {topic.save_score} / 讨论分 {topic.discussion_score} / 标题潜力 {topic.title_potential_score}\n\n"
            f"入选理由：{selection_reason}\n"
            f"推荐主结论：{suggested_angle}\n"
            f"跟踪变量：{follow_up_focus}\n"
            f"固定写法：{writing_brief}\n\n"
            f"{closing_instruction}"
        ).strip()

    def _preferred_topic_summary(self, topic: TopicCandidate) -> str:
        summary = str(topic.summary or "").strip()
        snippet = self._clean_topic_snippet_for_prompt(topic.snippet or "", topic.title)
        if (
            not summary
            or summary == topic.title
            or self._looks_like_mojibake(summary)
            or self._summary_uses_wrong_domain_template(topic, summary)
        ):
            if snippet and not self._looks_like_mojibake(snippet):
                return snippet
            return topic.title
        return summary[:180]

    def _preferred_topic_snippet(self, topic: TopicCandidate, fallback: str) -> str:
        snippet = self._clean_topic_snippet_for_prompt(topic.snippet or "", fallback)
        if self._looks_like_mojibake(snippet):
            return fallback
        return snippet

    def _looks_like_mojibake(self, text: str) -> bool:
        candidate = str(text or "").strip()
        if not candidate:
            return False
        markers = ("锛", "銆", "鐨", "鏈", "鍙", "璇", "鏂", "顒", "姹", "闃", "", "�")
        score = sum(candidate.count(marker) for marker in markers)
        return score >= 2

    def _summary_uses_wrong_domain_template(self, topic: TopicCandidate, text: str) -> bool:
        mode = self._topic_mode(topic)
        candidate = str(text or "").strip()
        if mode not in {"sports", "international"} or not candidate:
            return False
        wrong_markers = ("项目落地", "订单节奏", "产业链", "融资", "交付", "客户验证", "利润兑现")
        return any(marker in candidate for marker in wrong_markers)

    def _topic_mode(self, topic: TopicCandidate) -> str:
        if self._is_sports_topic(topic):
            return "sports"
        if self._is_international_topic(topic):
            return "international"
        return "generic"

    def _resolve_prompt_track(self, topic: TopicCandidate, primary_industry: str) -> str:
        mode = self._topic_mode(topic)
        if mode == "sports":
            return "体育赛事"
        if mode == "international":
            return "国际热点"
        return str(topic.title_playbook_track or primary_industry).strip() or primary_industry

    def _build_closing_instruction(
        self,
        topic: TopicCandidate,
        *,
        topic_mode: str,
        primary_industry: str,
    ) -> str:
        if topic_mode == "sports":
            return (
                "请先准确理解并消化素材里的英文信息，再写成自然中文深度分析稿。"
                "不要逐句硬翻，不要保留英文新闻腔，不要写成产业链、订单、融资、公司主体分析。"
                "重点写清：这件事为什么今天值得看、关键企业或人物是谁、真正的转折点或争议点是什么、"
                "它会怎样影响后续产业格局、技术路线、舆论走向或市场预期。"
                "如果素材是初步消息、政策信号、行业动态或争议事件，必须解释它对产业走势意味着什么。"
                "全文自然分成4到6段，每段2到3句，不要出现 #、##、项目符号或英文小标题；"
                "如果事实不足，不要编造比分、伤病、采访原话或内幕。"
                "结尾只留一个开放式讨论问题。"
            )
        if topic_mode == "international":
            return (
                "请先准确理解并消化素材里的英文信息，再写成自然中文国际热点解读稿。"
                "不要逐句直译，不要写成产业链、订单、融资或公司主体分析稿。"
                "重点写清：事件本身发生了什么、核心博弈方是谁、各自的利益和压力在哪里、"
                "这件事接下来最值得跟踪的政策变量或市场变量是什么，以及它对中国、全球市场或地缘局势可能意味着什么。"
                "全文自然分成5到6段，每段2到3句，不要出现 #、##、项目符号或空泛口号。"
                "如果事实不足，就明确写到已知边界，不要补造细节。"
            )
        return (
            "请围绕这条产业线索，重点分析："
            "这条线索目前处于什么阶段、涉及哪些公司/项目/订单主体、融资或订单意味着什么、它在产业链里卡住了哪一段、"
            "后续最值得跟踪的变量是什么，以及哪些风险会影响兑现节奏。"
        )

    def _clean_topic_snippet_for_prompt(self, snippet: str, fallback: str) -> str:
        candidate = str(snippet or "").strip()
        if not candidate:
            return fallback
        if "<a " in candidate.lower():
            return fallback
        lowered = candidate.lower()
        if "copyright" in lowered or "all rights reserved" in lowered or "in-en.com" in lowered:
            return fallback
        if "»" in candidate or "当前位置" in candidate or candidate.endswith("正文"):
            return fallback
        if len(re.findall(r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d+\s*(?:分钟前|小时前|天前))", candidate)) >= 2:
            return fallback
        if len(candidate) > 120 and candidate.count(" ") >= 6:
            return fallback
        if len(candidate) > 140 and sum(candidate.count(token) for token in ("，", "、", " | ", " / ")) >= 6:
            return fallback
        if sum(candidate.count(marker) for marker in ("鍥", "鏂", "鐢", "鑳", "缃", "鍌", "姹")) >= 6:
            return fallback
        return candidate[:160]

    def _write_selection_snapshot(
        self,
        topic: TopicCandidate,
        report: DailyTopicsReport,
        input_text: str,
    ) -> dict[str, Path]:
        basename = self.settings.automation.selected_topic_basename
        json_path = self.settings.output_dir / f"{basename}.json"
        md_path = self.settings.output_dir / f"{basename}.md"
        json_payload = {
            "selected_at": datetime.now().isoformat(timespec="seconds"),
            "generated_topics_at": report.generated_at,
            "selected_topic": asdict(topic),
            "auto_input_text": input_text,
        }
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        humanized_industries = self._humanize_industries(topic.matched_industries)
        primary_industry = humanized_industries[0] if humanized_industries else "相关赛道"
        primary_keywords = "、".join(topic.matched_keywords[:4]) if topic.matched_keywords else "关键词待补充"
        prompt_track = self._resolve_prompt_track(topic, primary_industry)
        selection_reason = self._build_selection_reason(
            topic,
            primary_industry,
            primary_keywords,
            "、".join(topic.extracted_numbers) if topic.extracted_numbers else "暂无明确数字",
        )
        suggested_angle = self._build_suggested_angle(
            topic,
            primary_industry,
            primary_keywords,
            "、".join(topic.extracted_numbers) if topic.extracted_numbers else "暂无明确数字",
        )
        follow_up_focus = self._build_follow_up_focus(topic, primary_industry, primary_keywords)
        content_format_label = topic.recommended_format_label or "深度分析"
        format_brief = self._build_content_format_brief(topic, primary_industry)
        paragraph_instruction = self._build_paragraph_instruction(topic)

        markdown = (
            "# 今日自动选题\n\n"
            f"- 标题：{topic.title}\n"
            f"- 排名：{topic.rank or 1}\n"
            f"- 来源：{_humanize_source(topic.source)}\n"
            f"- 发布时间：{topic.published_at or '未知'}\n"
            f"- 原文链接：{topic.url}\n"
            f"- 匹配赛道：{'、'.join(humanized_industries) or '无'}\n"
            f"- 关键词：{'、'.join(topic.matched_keywords) or '无'}\n"
            f"- 关键数字：{'、'.join(topic.extracted_numbers) or '无'}\n"
            f"- 评分：总分 {topic.total_score} / 阅读 {topic.read_score} / 收藏 {topic.save_score} / 讨论 {topic.discussion_score} / 标题潜力 {topic.title_potential_score}\n\n"
            "## 质量判断\n\n"
            f"- 选题类型：{topic.topic_type_label or topic.topic_type or '行业事件'}\n"
            f"- 推荐体裁：{content_format_label}\n"
            f"- 证据强度：{topic.evidence_strength}\n"
            f"- 选题质量分：{topic.quality_score}\n"
            f"- 标题打法赛道：{prompt_track}\n"
            f"- 标题抓手：{'、'.join(topic.title_potential_reasons) or '无'}\n"
            f"- 质量标记：{'、'.join(topic.quality_flags) or '无'}\n"
            f"- 体裁要求：{format_brief}\n"
            f"- 段落要求：{paragraph_instruction}\n\n"
            "## 选题判断\n\n"
            f"- 入选理由：{selection_reason}\n"
            f"- 推荐主结论：{suggested_angle}\n"
            f"- 跟踪变量：{follow_up_focus}\n\n"
            "## 摘要\n\n"
            f"{topic.summary or topic.snippet or topic.title}\n\n"
            "## 自动生成输入\n\n"
            "```text\n"
            f"{input_text}\n"
            "```\n"
        )
        md_path.write_text(markdown, encoding="utf-8")
        return {
            "selected_topic_json": json_path,
            "selected_topic_markdown": md_path,
        }

    def _humanize_industries(self, industries: list[str]) -> list[str]:
        result: list[str] = []
        for item in industries:
            normalized = self.INDUSTRY_LABELS.get(item, item)
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _build_selection_reason(
        self,
        topic: TopicCandidate,
        primary_industry: str,
        primary_keywords: str,
        numbers: str,
    ) -> str:
        topic_mode = self._topic_mode(topic)
        reasons: list[str] = []
        if topic.is_high_read or topic.read_score >= 60:
            reasons.append("具备较强阅读潜力")
        if topic.is_high_save or topic.save_score >= 55:
            reasons.append("更容易形成收藏型内容")
        if topic.title_potential_score >= 72:
            reasons.append("标题改写空间大，容易起判断型标题")
        if topic.is_high_discussion or topic.discussion_score >= 50:
            reasons.append("讨论度有扩散空间")
        if numbers != "暂无明确数字":
            reasons.append("自带数字抓手")
        if primary_keywords and primary_keywords != "关键词待补充":
            if topic_mode == "sports":
                reasons.append(f"与{primary_keywords.split('、')[0]}这类体育话题直接相关")
            elif topic_mode == "international":
                reasons.append(f"与{primary_keywords.split('、')[0]}这类国际议题直接相关")
            else:
                reasons.append(f"与{primary_industry}里的{primary_keywords.split('、')[0]}环节相关")
        if not reasons:
            if topic_mode == "sports":
                reasons.append("题材处在趋势预期、事后复盘或争议扩散的高关注区间")
            elif topic_mode == "international":
                reasons.append("题材处在国际博弈和市场预期变化的高关注区间")
            else:
                reasons.append(f"题材与{primary_industry}主线相关，适合写成产业链分析稿")
        return "，".join(reasons[:3]) + "。"

    def _build_suggested_angle(
        self,
        topic: TopicCandidate,
        primary_industry: str,
        primary_keywords: str,
        numbers: str,
    ) -> str:
        topic_mode = self._topic_mode(topic)
        primary_keyword = primary_keywords.split("、")[0] if primary_keywords and primary_keywords != "关键词待补充" else primary_industry
        if topic_mode == "sports":
            return (
                f"这条体育热点真正值得写的，不是把新闻复述一遍，而是解释{primary_keyword}背后的胜负手、争议点和后续走势。"
                "读者真正关心的是：这会不会改变产业格局、市场预期、舆论风向，或者直接影响后续走势。"
            )
        if topic_mode == "international":
            return (
                f"这条国际热点真正值得写的，不是事件表面，而是{primary_keyword}背后的利益重新分配和下一步博弈。"
                "读者真正关心的是：谁在施压、谁在让步、接下来最可能出现什么变化。"
            )
        keyword_phrase = self._keyword_angle_phrase(primary_industry, primary_keyword)
        title_text = f"{topic.title} {topic.summary} {topic.snippet}"
        if any(token in title_text for token in ("融资", "押注", "投资", "募资")):
            return (
                f"这不只是融资新闻，更像是资本开始提前卡位{primary_industry}{keyword_phrase}的下一轮兑现机会，"
                "真正要看的不是估值故事，而是客户验证、量产或订单能否接上。"
            )
        if any(token in title_text for token in ("订单", "签约", "中标", "拿下", "合作")):
            return (
                f"这条线真正值得写的，不是单一订单事件，而是{primary_industry}{keyword_phrase}的需求开始外溢，"
                "后面要看交付节奏和利润率能否同步跟上。"
            )
        if numbers != "暂无明确数字":
            return (
                f"{primary_industry}这轮升温不是空转，{self._keyword_signal_phrase(primary_industry, primary_keyword)}已经出现可验证信号，"
                "接下来要看订单、交付和利润谁先落地。"
            )
        return (
            f"这不是一条孤立热点，而是{primary_industry}{keyword_phrase}出现新的卡位信号，"
            "真正要看的还是客户验证、交付进度和后续盈利能力。"
        )

    def _build_follow_up_focus(
        self,
        topic: TopicCandidate,
        primary_industry: str,
        primary_keywords: str,
    ) -> str:
        topic_mode = self._topic_mode(topic)
        title_text = f"{topic.title} {topic.summary} {topic.snippet}"
        if topic_mode == "sports":
            return "技术路线变化、市场份额变化、关键企业动态、舆论争议是否继续发酵"
        if topic_mode == "international":
            return "政策表态、谈判进展、市场反馈、相关国家是否跟进动作"
        if primary_industry == "AI硬件" or "芯片" in primary_keywords:
            return "客户导入、流片或量产进度、单机价值量、毛利率变化"
        if primary_industry == "储能":
            return "新增订单、项目交付、本地化服务、关键环节毛利率"
        if primary_industry == "人形机器人":
            return "样机验证、量产节奏、单机成本、核心零部件卡位"
        if any(token in title_text for token in ("融资", "押注", "投资")):
            return "融资之后的客户验证、产品落地、量产爬坡、后续订单"
        return "客户验证、订单落地、交付节奏、毛利率变化"

    def _build_writing_brief(self, topic: TopicCandidate, primary_industry: str) -> str:
        topic_mode = self._topic_mode(topic)
        if topic_mode == "sports":
            return (
                "写成体育解读稿：先点出判断，再解释关键人物、关键节点和争议点，"
                "把事件为什么影响后续比赛说透，不要堆背景资料，更不要写成生硬直译稿。"
            )
        if topic_mode == "international":
            return (
                "写成国际热点解读稿：先交代变化，再拆博弈双方，再说后续变量和外溢影响，"
                "避免新闻播报腔，也不要空喊立场。"
            )
        if topic.recommended_format == "quick_commentary":
            return (
                "写成快讯点评稿：先下判断，再用一到两个关键事实解释为什么现在值得看，"
                "最后只留最重要的跟踪变量和风险边界，不要硬拉成长篇深度稿。"
            )
        if topic.recommended_format == "project_breakdown":
            return (
                f"写成项目拆解稿：先交代项目或主体的重要性，再拆{primary_industry}产业链位置、订单兑现路径和交付节奏，"
                "最后补一小段风险边界。"
            )
        if topic.recommended_format == "data_recap":
            return (
                "写成数据复盘稿：先说数据意味着什么，再解释哪些环节受益、哪些环节还没兑现，"
                "避免把统计材料硬写成空泛行业长文。"
            )
        return (
            f"写成产业分析稿：先下判断，再给数据，再拆{primary_industry}产业链位置，"
            "再落到公司主体和跟踪变量，最后只留一小段风险边界，不写新闻播报腔。"
        )

    def _build_content_format_brief(self, topic: TopicCandidate, primary_industry: str) -> str:
        topic_mode = self._topic_mode(topic)
        if topic_mode == "sports":
            return "适合写成深度分析稿，重点解释影响产业格局的关键变量、争议点和后续走势。"
        if topic_mode == "international":
            return "适合写成国际局势解读稿，重点解释博弈关系、变量变化和后续影响。"
        if topic.recommended_format == "quick_commentary":
            return "适合写成短而硬的事件点评，重点回答为什么值得看、谁会先受益、哪些变量还未验证。"
        if topic.recommended_format == "project_breakdown":
            return f"适合写成{primary_industry}项目或订单拆解稿，重点写主体、环节位置、交付路径和利润兑现。"
        if topic.recommended_format == "data_recap":
            return "适合写成数据复盘稿，重点解释数字变化背后的产业链含义，不要硬凑长篇评论。"
        return f"适合写成{primary_industry}深度分析稿，重点拆产业链位置、订单兑现和利润分化。"

    def _build_paragraph_instruction(self, topic: TopicCandidate) -> str:
        topic_mode = self._topic_mode(topic)
        if topic_mode == "sports":
            return "4到6段，每段3到5句，每段不少于120字，优先保证段落自然，图片应放在完整段落之间，不要切断句子。"
        if topic_mode == "international":
            return "5到6段，每段3到5句，每段不少于120字，先事件、再博弈、再影响、再变量，结尾不要喊口号。"
        if topic.recommended_format == "quick_commentary":
            return "5到6段，每段3到4句，每段不少于120字，首段先下判断，结尾只留一个分歧问题。"
        if topic.recommended_format == "project_breakdown":
            return "5到7段，每段3到5句，每段不少于120字，围绕项目主体、环节位置、交付路径、兑现风险展开。"
        if topic.recommended_format == "data_recap":
            return "5到6段，每段3到4句，每段不少于120字，重点解释数字变化、受益环节和后续验证点。"
        return "5到7段，每段3到5句，每段不少于120字，先判断、再数据、再产业链、再主体、再风险边界。"

    def _build_word_target(self, topic: TopicCandidate) -> int:
        topic_mode = self._topic_mode(topic)
        if topic_mode == "sports":
            return max(900, 800)
        if topic_mode == "international":
            return max(1000, 800)
        if topic.recommended_format == "quick_commentary":
            return max(800, 800)
        if topic.recommended_format == "data_recap":
            return max(900, 800)
        if topic.recommended_format == "project_breakdown":
            return max(1100, 800)
        return max(1200, 800)

    def _build_archive_root(self, selected_topic: TopicCandidate | None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        title = selected_topic.title if selected_topic is not None else "manual_run"
        slug = self._slugify(title)
        return self.settings.output_dir / self.settings.automation.archive_dirname / f"{timestamp}_{slug}"

    def _resolve_archive_target(self, archive_root: Path, image_dir: Path, source_path: Path) -> Path:
        if source_path.parent.name == "images" or source_path.suffix.lower() in {".png", ".svg", ".html"}:
            return image_dir / source_path.name
        return archive_root / source_path.name

    def _slugify(self, text: str) -> str:
        normalized = self.NORMALIZE_PATTERN.sub("-", text.strip()).strip("-").lower()
        return normalized[:48] or "untitled"

    def _normalize_text(self, text: str) -> str:
        return self.NORMALIZE_PATTERN.sub("", text.lower())

    def _keyword_angle_phrase(self, primary_industry: str, primary_keyword: str) -> str:
        industry = str(primary_industry or "").strip()
        keyword = str(primary_keyword or "").strip()
        if not keyword or keyword == industry:
            return "核心项目"
        if keyword in industry or industry in keyword:
            return f"里与{keyword}相关的关键环节"
        return f"在{keyword}环节"

    def _keyword_signal_phrase(self, primary_industry: str, primary_keyword: str) -> str:
        industry = str(primary_industry or "").strip()
        keyword = str(primary_keyword or "").strip()
        if not keyword or keyword == industry:
            return f"{industry}核心环节"
        if keyword in industry or industry in keyword:
            return keyword
        return f"{keyword}等环节"

    def _available_topics(
        self,
        topics: list[TopicCandidate],
        allow_generated_duplicates: bool = False,
    ) -> list[TopicCandidate]:
        if self.settings.publishing.allow_duplicate_topics:
            return list(topics)

        published_keys = self._load_published_keys_for_today()
        generated_keys = (
            {"titles": set(), "urls": set()}
            if allow_generated_duplicates
            else self._load_generated_keys_for_today()
        )
        cross_account_published_keys = self._load_cross_account_published_keys_for_today()
        cross_account_generated_keys = (
            {"titles": set(), "urls": set()}
            if allow_generated_duplicates
            else self._load_cross_account_generated_keys_for_today()
        )
        available: list[TopicCandidate] = []
        for topic in topics:
            if not self._topic_matches_account_boundary(topic):
                continue
            if topic.url and topic.url in published_keys["urls"]:
                continue
            if topic.url and topic.url in generated_keys["urls"]:
                continue
            if topic.url and topic.url in cross_account_published_keys["urls"]:
                continue
            if topic.url and topic.url in cross_account_generated_keys["urls"]:
                continue
            normalized_title = self._normalize_text(topic.title)
            if normalized_title and normalized_title in published_keys["titles"]:
                continue
            if normalized_title and normalized_title in generated_keys["titles"]:
                continue
            if normalized_title and normalized_title in cross_account_published_keys["titles"]:
                continue
            if normalized_title and normalized_title in cross_account_generated_keys["titles"]:
                continue
            if self._is_similar_to_generated(normalized_title, generated_keys["titles"], cross_account_generated_keys["titles"]):
                continue
            available.append(topic)
        return available

    def _available_topics_soft(
        self,
        topics: list[TopicCandidate],
        allow_generated_duplicates: bool = False,
    ) -> list[TopicCandidate]:
        if self.settings.publishing.allow_duplicate_topics:
            return list(topics)

        published_keys = self._load_published_keys_for_today()
        generated_keys = (
            {"titles": set(), "urls": set()}
            if allow_generated_duplicates
            else self._load_generated_keys_for_today()
        )
        available: list[TopicCandidate] = []
        for topic in topics:
            if not self._topic_matches_account_boundary(topic):
                continue
            if topic.url and topic.url in published_keys["urls"]:
                continue
            normalized_title = self._normalize_text(topic.title)
            if normalized_title and normalized_title in published_keys["titles"]:
                continue
            if topic.url and topic.url in generated_keys["urls"]:
                continue
            if normalized_title and normalized_title in generated_keys["titles"]:
                continue
            available.append(topic)
        return available

    _SIMILARITY_THRESHOLD = 0.6

    def _is_similar_to_generated(
        self,
        normalized_title: str | None,
        *title_sets: set[str],
    ) -> bool:
        if not normalized_title:
            return False
        for title_set in title_sets:
            for existing in title_set:
                if SequenceMatcher(None, normalized_title, existing).ratio() >= self._SIMILARITY_THRESHOLD:
                    return True
        return False

    def _load_published_keys_for_today(self) -> dict[str, set[str]]:
        history_path = self.settings.output_dir / self.settings.publishing.history_filename
        result = {"titles": set(), "urls": set()}
        if not history_path.exists():
            return result
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return result
        today = datetime.now().date().isoformat()
        for item in payload:
            if not isinstance(item, dict):
                continue
            published_at = str(item.get("published_at", ""))
            if not published_at.startswith(today):
                continue
            topic_title = str(item.get("topic_title", ""))
            topic_url = str(item.get("topic_url", ""))
            if topic_title:
                result["titles"].add(self._normalize_text(topic_title))
            if topic_url:
                result["urls"].add(topic_url)
        return result

    def _load_generated_keys_for_today(self) -> dict[str, set[str]]:
        result = {"titles": set(), "urls": set()}
        runs_root = self.settings.output_dir / self.settings.automation.archive_dirname
        if not runs_root.exists():
            return result

        today = datetime.now().strftime("%Y%m%d")
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir() or not run_dir.name.startswith(today):
                continue
            topic_path = run_dir / f"{self.settings.automation.selected_topic_basename}.json"
            if not topic_path.exists():
                continue
            try:
                payload = json.loads(topic_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            topic = payload.get("selected_topic")
            if not isinstance(topic, dict):
                continue
            topic_title = str(topic.get("title", "")).strip()
            topic_url = str(topic.get("url", "")).strip()
            if topic_title:
                result["titles"].add(self._normalize_text(topic_title))
            if topic_url:
                result["urls"].add(topic_url)
        return result

    def _load_cross_account_published_keys_for_today(self) -> dict[str, set[str]]:
        result = {"titles": set(), "urls": set()}
        today = datetime.now().date().isoformat()
        for account_key in self._iter_other_enabled_account_keys():
            history_path = self._resolve_output_dir_for_account(account_key) / self.settings.publishing.history_filename
            if not history_path.exists():
                continue
            try:
                payload = json.loads(history_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                published_at = str(item.get("published_at", "")).strip()
                if not published_at.startswith(today):
                    continue
                topic_title = str(item.get("topic_title", "")).strip()
                topic_url = str(item.get("topic_url", "")).strip()
                if topic_title:
                    result["titles"].add(self._normalize_text(topic_title))
                if topic_url:
                    result["urls"].add(topic_url)
        return result

    def _load_cross_account_generated_keys_for_today(self) -> dict[str, set[str]]:
        result = {"titles": set(), "urls": set()}
        today = datetime.now().strftime("%Y%m%d")
        for account_key in self._iter_other_enabled_account_keys():
            runs_root = self._resolve_output_dir_for_account(account_key) / self.settings.automation.archive_dirname
            if not runs_root.exists():
                continue
            for run_dir in runs_root.iterdir():
                if not run_dir.is_dir() or not run_dir.name.startswith(today):
                    continue
                topic_path = run_dir / f"{self.settings.automation.selected_topic_basename}.json"
                if not topic_path.exists():
                    continue
                try:
                    payload = json.loads(topic_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                topic = payload.get("selected_topic")
                if not isinstance(topic, dict):
                    continue
                topic_title = str(topic.get("title", "")).strip()
                topic_url = str(topic.get("url", "")).strip()
                if topic_title:
                    result["titles"].add(self._normalize_text(topic_title))
                if topic_url:
                    result["urls"].add(topic_url)
        return result

    def _iter_other_enabled_account_keys(self) -> list[str]:
        keys: list[str] = []
        for account_key, profile in self.settings.account_profiles.items():
            if account_key == self.settings.account_key:
                continue
            if not getattr(profile, "enabled", True):
                continue
            keys.append(account_key)
        return keys

    def _resolve_output_dir_for_account(self, account_key: str) -> Path:
        profile = self.settings.account_profiles.get(account_key)
        if profile is not None and str(profile.output_dir).strip():
            candidate = Path(str(profile.output_dir).strip())
            return candidate if candidate.is_absolute() else (self.settings.project_root / candidate)
        slug = sanitize_account_slug(account_key)
        if slug == "default":
            return self.settings.project_root / "output"
        return self.settings.project_root / "output" / "accounts" / slug

    def _topic_matches_account_boundary(self, topic: TopicCandidate) -> bool:
        account_key = self.settings.account_key
        if account_key == "robot_notes":
            return self._is_robot_topic(topic)
        if account_key == "energy_notes":
            return self._is_energy_topic(topic)
        if account_key == "default":
            return not self._is_reserved_for_specialist_account(topic)
        return True

    def filter_topics_for_account(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        return [topic for topic in topics if self._topic_matches_account_boundary(topic)]

    def _is_reserved_for_specialist_account(self, topic: TopicCandidate) -> bool:
        robot_enabled = bool(self.settings.account_profiles.get("robot_notes")) and bool(
            self.settings.account_profiles["robot_notes"].enabled
        )
        energy_enabled = bool(self.settings.account_profiles.get("energy_notes")) and bool(
            self.settings.account_profiles["energy_notes"].enabled
        )
        if robot_enabled and self._is_robot_topic(topic):
            return True
        if energy_enabled and self._is_energy_topic(topic):
            return True
        return False

    def _is_robot_topic(self, topic: TopicCandidate) -> bool:
        robot_score, energy_score, international_score, sports_score = self._topic_domain_scores(topic)
        industries = {str(item).strip() for item in topic.matched_industries if str(item).strip()}
        track = str(topic.title_playbook_track or "").strip()
        if track == "AI机器人" and energy_score == 0 and international_score == 0 and sports_score == 0:
            return True
        if robot_score >= 2 and energy_score == 0 and international_score == 0 and sports_score == 0:
            return True
        if industries and industries.issubset(self.ROBOT_INDUSTRIES) and robot_score >= 1 and energy_score == 0 and international_score == 0 and sports_score == 0:
            return True
        return False

    def _is_energy_topic(self, topic: TopicCandidate) -> bool:
        robot_score, energy_score, international_score, sports_score = self._topic_domain_scores(topic)
        industries = {str(item).strip() for item in topic.matched_industries if str(item).strip()}
        track = str(topic.title_playbook_track or "").strip()
        if track == "新能源储能" and robot_score == 0 and international_score == 0 and sports_score == 0:
            return True
        if energy_score >= 2 and robot_score == 0 and international_score == 0 and sports_score == 0:
            return True
        if industries and industries.issubset(self.ENERGY_INDUSTRIES) and energy_score >= 1 and robot_score == 0 and international_score == 0 and sports_score == 0:
            return True
        return False

    def _is_international_topic(self, topic: TopicCandidate) -> bool:
        _, _, international_score, sports_score = self._topic_domain_scores(topic)
        industries = {str(item).strip() for item in topic.matched_industries if str(item).strip()}
        track = str(topic.title_playbook_track or "").strip()
        text = "\n".join(
            item
            for item in (
                topic.title,
                topic.summary,
                topic.snippet,
                " ".join(topic.matched_keywords),
            )
            if item
        )
        if any(token in text for token in self.INTERNATIONAL_FALSE_POSITIVE_TOKENS):
            return False
        if track == "国际热点" and sports_score == 0:
            return True
        if international_score >= 2 and sports_score == 0:
            return True
        if industries and industries.issubset(self.INTERNATIONAL_INDUSTRIES) and international_score >= 2 and sports_score == 0:
            return True
        return False

    def _is_sports_topic(self, topic: TopicCandidate) -> bool:
        _, _, international_score, sports_score = self._topic_domain_scores(topic)
        industries = {str(item).strip() for item in topic.matched_industries if str(item).strip()}
        track = str(topic.title_playbook_track or "").strip()
        if "sports" in industries and international_score == 0:
            return True
        if track == "体育赛事" and international_score == 0:
            return True
        if sports_score >= 2 and international_score == 0:
            return True
        if industries and industries.issubset(self.SPORTS_INDUSTRIES) and sports_score >= 1 and international_score == 0:
            return True
        return False

    def _topic_domain_scores(self, topic: TopicCandidate) -> tuple[int, int, int, int]:
        text = "\n".join(
            item
            for item in (
                topic.title,
                topic.summary,
                topic.snippet,
                " ".join(topic.matched_keywords),
            )
            if item
        )
        robot_score = sum(1 for token in self.ROBOT_TOKENS if token and token in text)
        energy_score = sum(1 for token in self.ENERGY_TOKENS if token and token in text)
        international_score = sum(1 for token in self.INTERNATIONAL_TOKENS if token and token in text)
        sports_score = sum(1 for token in self.SPORTS_TOKENS if token and token in text)
        return robot_score, energy_score, international_score, sports_score

def daily_run(
    settings: AppSettings,
    logger: logging.Logger,
    style_override: str | None = None,
    dry_run: bool = False,
    refresh_cache: bool = False,
    topic_rank: int | None = None,
    topic_title: str | None = None,
    no_archive: bool = False,
    fast_mode: bool = False,
) -> dict[str, str]:
    from toutiao_content_factory.services.app_service import ContentFactoryAppService

    service = ContentFactoryAppService(settings, logger)
    return service.daily_run(
        style_override=style_override,
        dry_run=dry_run,
        refresh_cache=refresh_cache,
        topic_rank=topic_rank,
        topic_title=topic_title,
        no_archive=no_archive,
        fast_mode=fast_mode,
    )

def collect_performance_data(
    settings: AppSettings,
    logger: logging.Logger,
) -> dict[str, object]:
    from toutiao_content_factory.services.performance_service import collect_performance_data as collect_job

    return collect_job(settings, logger)
