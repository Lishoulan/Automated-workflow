from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate


@dataclass
class TopicSelectionResult:
    report: DailyTopicsReport
    topic: TopicCandidate
    input_text: str
    exported_files: dict[str, Path]


class TopicAutomationService:
    NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    INDUSTRY_LABELS = {
        "humanoid_robot": "人形机器人",
        "new_energy": "新能源",
        "energy_storage": "储能",
        "ai_hardware": "AI硬件",
        "industrial_automation": "工业自动化",
    }

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    def prepare_auto_run(
        self,
        rank: int | None = None,
        title_query: str | None = None,
    ) -> TopicSelectionResult:
        report = self._load_daily_topics()
        topic = self._select_topic(report, rank=rank, title_query=title_query)
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
        archived["archive_root"] = archive_root
        archived["run_manifest"] = manifest_path
        self.logger.info("Run archived: %s", archive_root)
        return archived

    def _load_daily_topics(self) -> DailyTopicsReport:
        report_path = self.settings.output_dir / "daily_topics.json"
        if not report_path.exists():
            raise FileNotFoundError(
                f"daily_topics.json not found at {report_path}. Run --collect-topics first."
            )
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report = DailyTopicsReport.from_dict(payload)
        if not report.topics:
            raise ValueError("daily_topics.json contains no available topics.")
        return report

    def _select_topic(
        self,
        report: DailyTopicsReport,
        rank: int | None,
        title_query: str | None,
    ) -> TopicCandidate:
        available_topics = self._available_topics(report.topics)
        if not available_topics:
            raise ValueError("No unpublished topics are available for today.")

        if title_query:
            normalized_query = self._normalize_text(title_query)
            for topic in available_topics:
                if normalized_query in self._normalize_text(topic.title):
                    return topic
            raise ValueError(f"No unpublished topic matched title query: {title_query}")

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
        summary = topic.summary or topic.title
        snippet = topic.snippet or summary
        published_at = topic.published_at or "时间未标注"
        primary_industry = humanized_industries[0] if humanized_industries else "相关赛道"
        primary_keywords = "、".join(topic.matched_keywords[:4]) if topic.matched_keywords else keywords
        selection_reason = self._build_selection_reason(topic, primary_industry, primary_keywords, numbers)
        suggested_angle = self._build_suggested_angle(topic, primary_industry, primary_keywords, numbers)
        follow_up_focus = self._build_follow_up_focus(topic, primary_industry, primary_keywords)
        writing_brief = self._build_writing_brief(primary_industry)

        return (
            f"标题：{topic.title}\n"
            f"来源：{topic.source}\n"
            f"发布日期：{published_at}\n"
            f"原文链接：{topic.url}\n\n"
            f"主赛道：{primary_industry}\n"
            f"主关键词：{primary_keywords}\n"
            f"匹配赛道：{industries}\n"
            f"匹配关键词：{keywords}\n"
            f"可确认数字：{numbers}\n\n"
            "素材正文：\n"
            f"{summary}\n"
            f"{snippet}\n\n"
            f"入选理由：{selection_reason}\n"
            f"推荐主结论：{suggested_angle}\n"
            f"跟踪变量：{follow_up_focus}\n"
            f"固定写法：{writing_brief}\n\n"
            "请围绕这条产业线索，重点分析："
            "这家公司现在处于什么阶段、融资或订单意味着什么、它在产业链里卡住了哪一段、"
            "后续最值得跟踪的变量是什么，以及哪些风险会影响兑现节奏。"
        ).strip()

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

        markdown = (
            "# 今日自动选题\n\n"
            f"- 标题：{topic.title}\n"
            f"- 排名：{topic.rank or 1}\n"
            f"- 来源：{topic.source}\n"
            f"- 发布时间：{topic.published_at or '未知'}\n"
            f"- 原文链接：{topic.url}\n"
            f"- 匹配赛道：{'、'.join(humanized_industries) or '无'}\n"
            f"- 关键词：{'、'.join(topic.matched_keywords) or '无'}\n"
            f"- 关键数字：{'、'.join(topic.extracted_numbers) or '无'}\n"
            f"- 评分：总分 {topic.total_score} / 阅读 {topic.read_score} / 收藏 {topic.save_score} / 讨论 {topic.discussion_score}\n\n"
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
        reasons: list[str] = []
        if topic.is_high_read or topic.read_score >= 60:
            reasons.append("具备较强阅读潜力")
        if topic.is_high_save or topic.save_score >= 55:
            reasons.append("更容易形成收藏型内容")
        if topic.is_high_discussion or topic.discussion_score >= 50:
            reasons.append("讨论度有扩散空间")
        if numbers != "暂无明确数字":
            reasons.append("自带数字抓手")
        if primary_keywords and primary_keywords != "关键词待补充":
            reasons.append(f"与{primary_industry}里的{primary_keywords.split('、')[0]}环节相关")
        if not reasons:
            reasons.append(f"题材与{primary_industry}主线相关，适合写成产业链分析稿")
        return "，".join(reasons[:3]) + "。"

    def _build_suggested_angle(
        self,
        topic: TopicCandidate,
        primary_industry: str,
        primary_keywords: str,
        numbers: str,
    ) -> str:
        primary_keyword = primary_keywords.split("、")[0] if primary_keywords and primary_keywords != "关键词待补充" else primary_industry
        title_text = f"{topic.title} {topic.summary} {topic.snippet}"
        if any(token in title_text for token in ("融资", "押注", "投资", "募资")):
            return (
                f"这不只是融资新闻，更像是资本开始提前卡位{primary_industry}在{primary_keyword}环节的下一轮兑现机会，"
                "真正要看的不是估值故事，而是客户验证、量产或订单能否接上。"
            )
        if any(token in title_text for token in ("订单", "签约", "中标", "拿下", "合作")):
            return (
                f"这条线真正值得写的，不是单一订单事件，而是{primary_industry}在{primary_keyword}环节的需求开始外溢，"
                "后面要看交付节奏和利润率能否同步跟上。"
            )
        if numbers != "暂无明确数字":
            return (
                f"{primary_industry}这轮升温不是空转，{primary_keyword}等环节已经出现可验证信号，"
                "接下来要看订单、交付和利润谁先落地。"
            )
        return (
            f"这不是一条孤立热点，而是{primary_industry}在{primary_keyword}环节出现新的卡位信号，"
            "真正要看的还是客户验证、交付进度和后续盈利能力。"
        )

    def _build_follow_up_focus(
        self,
        topic: TopicCandidate,
        primary_industry: str,
        primary_keywords: str,
    ) -> str:
        title_text = f"{topic.title} {topic.summary} {topic.snippet}"
        if primary_industry == "AI硬件" or "芯片" in primary_keywords:
            return "客户导入、流片或量产进度、单机价值量、毛利率变化"
        if primary_industry == "储能":
            return "新增订单、项目交付、本地化服务、关键环节毛利率"
        if primary_industry == "人形机器人":
            return "样机验证、量产节奏、单机成本、核心零部件卡位"
        if any(token in title_text for token in ("融资", "押注", "投资")):
            return "融资之后的客户验证、产品落地、量产爬坡、后续订单"
        return "客户验证、订单落地、交付节奏、毛利率变化"

    def _build_writing_brief(self, primary_industry: str) -> str:
        return (
            f"写成产业分析稿：先下判断，再给数据，再拆{primary_industry}产业链位置，"
            "再落到公司主体和跟踪变量，最后只留一小段风险边界，不写新闻播报腔。"
        )

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

    def _available_topics(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        if self.settings.publishing.allow_duplicate_topics:
            return list(topics)

        published_keys = self._load_published_keys_for_today()
        generated_keys = self._load_generated_keys_for_today()
        available: list[TopicCandidate] = []
        for topic in topics:
            if topic.url and topic.url in published_keys["urls"]:
                continue
            if topic.url and topic.url in generated_keys["urls"]:
                continue
            normalized_title = self._normalize_text(topic.title)
            if normalized_title and normalized_title in published_keys["titles"]:
                continue
            if normalized_title and normalized_title in generated_keys["titles"]:
                continue
            available.append(topic)
        return available

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
