from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings, sanitize_account_slug
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.schema_guard_service import SchemaGuardService
from toutiao_content_factory.services.topic_pool_service import TopicPoolService


# 选题去重窗口（天）。必须与 `PublishService._was_topic_published` 的 7 天窗口一致：
# 选题层允许的选题，发布层也必须允许；否则会出现"写完才被重复拦截、当天报废"。
TOPIC_DEDUP_WINDOW_DAYS = 7


# 内部选题池标识 → 面向 LLM 的人类可读来源名
# 避免把机器标识(如 evergreen_pool、benchmark_hotspot)直接喂给 LLM 导致泄漏到正文
SOURCE_DISPLAY_NAMES = {
    "evergreen_pool": "常青选题库",
    "benchmark_hotspot": "公开报道",
    "manual_paste": "公开材料",
    "topic_pool": "选题库",
    "36kr": "36氪",
    "ithome": "IT之家",
    "ofweek": "OFweek",
    "newenergy_ofweek": "OFweek新能源",
    "chuneng_ofweek": "OFweek储能",
    "zhidx": "智财社",
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
    #: 会被 document_loader 当作素材解析的文本（**不含**写作指令）
    input_text: str
    exported_files: dict[str, Path]
    #: 含写作指令的完整文本；仅用于排查，不可当素材喂给 loader
    prompt_text: str = ""


class TopicAutomationService:
    NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    PROMPT_SUMMARY_LENGTH = 180
    PROMPT_EVIDENCE_CONTEXT_LENGTH = 1600
    PROMPT_SOURCE_CONTEXT_TYPES = frozenset({"html", "rss", "google_rss"})
    PROMPT_SOURCE_CONTEXT_MIN_LENGTH = 220
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
        "hot_topic": "实时热点",
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
        "hot_topic": "实时热点",
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
        allow_material_retry: bool = False,
    ) -> TopicSelectionResult | None:
        """选定本次要写的选题，并补齐真实素材。

        `allow_material_retry`：抓不到素材时**是否允许换成别的选题**。
        调用方要按"这条题是谁选的"来决定：

        - 用户直接给的（CLI `--topic-title` / `--topic-rank`）：**不要**打开这个开关，
          尊重人的选择，抓不到就放弃。
        - 机器自己挑的（`AppService` 的候选循环，逐条把 `candidate["title"]` 传进来）：
          **必须**打开。否则每条候选都被当成"用户指定"，**换题逻辑永远不生效**。

        ⚠️ 2026-09-19 实测（云端 run 35430893194）：候选循环传的就是 `topic_title=`，
        于是 `requested_title` 恒为真，榜首那条抓不到素材就整次运行放弃 ——
        云端当天一篇都发不出去。这个参数的默认值 `False` 是刻意选的：
        默认保持"尊重显式指定"的语义，只有明确知道自己在做候选择优的调用方才打开。
        """
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
        # 热榜类选题只有标题、没有正文，必须先补真实素材。
        #
        # ⚠️ 2026-09-19（第一次修）：这里原来**忽略返回值**，抓不到素材也照样往下写、
        # 往下发。实测后果：Bing 偶发抓取失败时，模型只拿到 28 个字的标题，
        # 硬编出 855 字「安全意识」「应急处理能力」这类万能话术，正文里还漏出上游的
        # 产业模板句，标题池也塌成 1 条兜底模板。
        #
        # ⚠️ 2026-09-19（第二次修）：第一次修的时候把"抓不到就放弃"的范围划得**太宽** ——
        # 只要是自动选题（rank/title_query 都没传）就换题，但换题逻辑只在
        # `_select_topic` 返回 None 时才走到。实测云端（run 35430296347）：
        #     [素材] 未能为选题抓到正文…: 张雪回应打车被认出"忽悠"司机（尝试 0 个来源）
        #     ERROR [素材] 指定选题抓不到正文素材，放弃本次运行（不发布）
        #     → Daily workflow did not produce any runnable candidates
        # 云端只有一次机会，榜首那条抓不到就**整天不发**。
        #
        # 现在的规则分两种情况：
        #   - **用户显式指定了选题**（--topic-rank / --topic-title）：抓不到就放弃，
        #     尊重人的选择，不擅自换成别的题。
        #   - **自动选题**：抓不到就**继续试后面几条**，全试完才放弃。
        #     `_select_topic` 返回的只是"排序第一"，它不代表"唯一可用的题"。
        requested_title = bool(str(title_query or "").strip())
        retry_allowed = allow_material_retry or not requested_title
        # 长青题豁免素材门槛：它们本来就不是新闻事件，搜不到报道是正常的。
        # 详见 `_is_evergreen_topic`。
        evergreen_topic = self._is_evergreen_topic(topic)
        if evergreen_topic:
            self.logger.info(
                "[素材] 长青题不要求抓到报道正文（非新闻事件）: %s", topic.title
            )
        elif not self._hydrate_topic_material(topic):
            if not retry_allowed:
                self.logger.error(
                    "[素材] 指定选题抓不到正文素材，放弃本次运行（不发布）: %s",
                    topic.title,
                )
                return None
            if rank is not None and not allow_material_retry:
                self.logger.warning(
                    "[素材] 指定序号（rank=%s）的选题抓不到正文素材，按用户指定处理，不换题: %s",
                    rank,
                    topic.title,
                )
            retried = self._hydrate_with_fallback(report, topic)
            if retried is None:
                self.logger.error(
                    "[素材] 所有候选选题都抓不到正文素材，放弃本次运行（不发布）。"
                )
                return None
            topic = retried
        input_text = self._build_input_text(topic)
        # ⚠️ 2026-09-18：`input_text` 末尾挂着一段写给模型的写作指令（closing_instruction），
        # 而这份文本正是 document_loader 当作 **paste 素材**解析的东西 ——
        # 于是「请围绕这件真实发生的事，写一篇给普通读者看的人情故事观察稿」
        # 「不要拆产业链、不要写订单与产能」这些**指令本身**被当成素材事实，
        # 模型又原样复述进了正文（实测正文里出现「公开信息还提到：请围绕这件真实发生的事…」）。
        # 所以：写给 writer 的完整文本保留指令，但**存进快照、会被当素材解析的那份必须剥掉指令**。
        trace_text = self._strip_prompt_instructions(input_text)
        exported_files = self._write_selection_snapshot(topic, report, trace_text)
        self.logger.info("Auto topic selection complete: rank=%s title=%s", topic.rank, topic.title)
        return TopicSelectionResult(
            report=report,
            topic=topic,
            input_text=trace_text,
            prompt_text=input_text,
            exported_files=exported_files,
        )

    #: 素材抓取的备选尝试上限（每次尝试都会真实联网检索，不宜过多）
    MATERIAL_FALLBACK_ATTEMPTS = 5

    @staticmethod
    def _is_evergreen_topic(topic: TopicCandidate) -> bool:
        """这条题是不是长青题（常青选题池来的，没有对应的真实热点事件）。

        ⚠️ 2026-09-19：长青题**搜不到真实报道** —— 它们本来就是"方法论 / 生活经验"
        型选题，不是新闻事件。实测：
            「老人在家摔倒后，子女才发现没人知道他的日常情况」→ Bing 只返回百度百科
            「子女轮流照顾老人，为什么最后总是闹到翻脸」      → 同上
        而事件式热榜题能抓到 3800+ 字真实报道。

        所以素材门槛对长青题不适用：非要"抓到正文才写"，等于把兜底选题全部作废，
        当天一条都发不出去（这正是 2026-09-19 云端连续失败的成因之一）。
        长青稿的内容安全由 writer 侧的反虚构约束保证（只能写常识与公开背景、
        不许编造具体人物与案情），而不是靠"必须有报道正文"。
        """
        source = str(getattr(topic, "source", "") or "").strip()
        source_type = str(getattr(topic, "source_type", "") or "").strip()
        return source == "evergreen_pool" or source_type == "evergreen"

    def _hydrate_with_fallback(
        self,
        report: DailyTopicsReport,
        primary: TopicCandidate,
    ) -> TopicCandidate | None:
        """首选选题抓不到素材时，按同一优先级顺序继续试后面几条。"""
        candidates = self._material_retry_candidates(report)
        primary_key = (primary.url or "", self._normalize_text(primary.title))
        tried = 0
        for candidate in candidates:
            if (candidate.url or "", self._normalize_text(candidate.title)) == primary_key:
                continue  # 首选刚试过，跳过
            if tried >= self.MATERIAL_FALLBACK_ATTEMPTS:
                break
            tried += 1
            self.logger.warning(
                "[素材] 换选题重试（第 %d 条）: %s", tried, candidate.title
            )
            if self._hydrate_topic_material(candidate):
                return candidate
        return None

    def _material_retry_candidates(
        self, report: DailyTopicsReport
    ) -> list[TopicCandidate]:
        """可继续尝试补素材的选题，顺序与 `_select_topic` 的自动选题一致。

        ⚠️ 2026-09-19：这里必须**自己再过一遍账号边界**。
        第一版直接返回 `_available_topics(...)` 的结果，漏了 `_topic_matches_account_boundary`
        —— 而选题池里混着别的赛道的历史条目，于是"换题重试"把一条
        **机器人**选题塞进了社会故事号（实测 verify11：重试选中
        「机器人首秀网球场，银河通用突破具身智能「AstraTennis时刻」」）。
        范围只许比 `_select_topic` 更窄，不许更宽。
        """
        available = self._available_topics(report.topics, allow_generated_duplicates=True)
        if not available:
            available = self._available_topics_soft(
                report.topics, allow_generated_duplicates=True
            )
        in_scope = [topic for topic in available if self._topic_matches_account_boundary(topic)]
        return self._prioritize_evidence_rich_topics(in_scope)

    #: 写作指令段落的分隔标记：`_build_input_text` 用空行把它与素材字段隔开
    _INSTRUCTION_SENTINEL = "请围绕"

    def _hydrate_topic_material(self, topic: TopicCandidate) -> bool:
        """为「只有标题」的选题补上真实报道正文。

        为什么必须补：热榜类选题（百度热搜 / 头条热榜）采集时只有标题，实测 snippet
        长度全部为 0、多数连原文链接都没有。让模型用 28 个字的标题写 1100 字文章，
        它只能靠行业套话填空并复述提示词 —— 实测正文里出现「订单、验收、回款、
        利润率承压」这类与社会新闻毫无关系的内容。

        素材来源：`material_search_service`（Bing 搜索 → 抓真实报道 → 抽正文）。

        ⚠️ 2026-09-19：拿不到素材**会阻断本次运行**（由 `prepare_auto_run` 判定）。
        这里本身只返回 False 并留明确日志，是否放弃由调用方决定 ——
        实测「抓不到素材也照样发」的后果见 `prepare_auto_run` 里的说明。

        返回是否补上了素材。
        """
        title = str(topic.title or "").strip()
        if not title:
            return False
        existing = str(topic.snippet or "").strip()
        try:
            from toutiao_content_factory.services.material_search_service import (
                MaterialBundle,
                MaterialSource,
                assess_material_evidence,
                collect_material,
            )

            # Existing non-hot snippets may already contain a fetched article.
            # Apply the same evidence gate instead of accepting them by length alone.
            if (
                len(existing) >= self.MATERIAL_MIN_CHARS
                and topic.source_type not in {"baidu_hot", "hot_board"}
            ):
                existing_bundle = MaterialBundle(
                    query=title,
                    sources=[
                        MaterialSource(
                            title=str(getattr(topic, "source", "") or title),
                            url=str(topic.url or ""),
                            body=existing,
                            ok=True,
                            evidence_type="article",
                        )
                    ],
                )
                existing_assessment = assess_material_evidence(existing_bundle)
                if existing_assessment.passed:
                    topic._verified_source_material = True
                    topic._material_evidence_assessment = asdict(existing_assessment)
                    return True

            bundle = collect_material(
                title,
                max_sources=self.MATERIAL_MAX_SOURCES,
                min_body_chars=self.MATERIAL_MIN_BODY_CHARS,
                cache_dir=self.settings.output_dir / "growth_ops" / "source_material",
            )
        except Exception as exc:
            self.logger.warning("选题素材抓取失败（非阻断）: %s | %s", title, exc)
            return False
        if not bundle.usable:
            self.logger.warning(
                "[素材] 未能为选题抓到正文，写作将缺少事实支撑: %s（尝试 %d 个来源）",
                title,
                len(bundle.sources),
            )
            return False
        assessment = assess_material_evidence(bundle)
        topic._material_evidence_assessment = asdict(assessment)
        if not assessment.passed:
            self.logger.warning(
                "[素材] 证据不足，改试下一题：%s | %s | 独立来源 %d 个 | 事实锚点 %d 个 | 共 %d 字",
                title,
                "；".join(assessment.reasons),
                assessment.independent_source_count,
                assessment.factual_anchor_count,
                assessment.total_chars,
            )
            return False
        body = bundle.combined_text.strip()
        topic.snippet = body
        # Keep this run-local marker separate from persisted search digests.
        topic._verified_source_material = True
        # 摘要直接取自素材正文的首段（`_summarize_topic` 属于 TopicCollectionService，
        # 本类没有该方法；而且这里已经有真实正文，用正文开头的句子比模板句更贴题）。
        topic.summary = self._material_lead_sentence(body) or topic.summary
        self.logger.info(
            "[素材] 已为选题补齐正文：%s | 来源 %d 个 | 共 %d 字",
            title,
            assessment.independent_source_count,
            assessment.total_chars,
        )
        return True

    #: 素材补齐的门槛与上限
    MATERIAL_MIN_CHARS = 600
    MATERIAL_MIN_BODY_CHARS = 400
    MATERIAL_MAX_SOURCES = 3
    MATERIAL_LEAD_MAX = 160

    def _material_lead_sentence(self, material: str, max_len: int | None = None) -> str:
        """从抓来的素材里取一句「导语」，用作选题摘要。

        跳过【来源：…】标记行与过短的行，取第一句像正文的话。
        """
        limit = max_len or self.MATERIAL_LEAD_MAX
        for raw_line in str(material or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("【来源"):
                continue
            if len(line) < 20:
                continue
            sentence = re.split(r"[。！？；]", line)[0].strip()
            if len(sentence) >= 15:
                return sentence[:limit]
        return ""

    def _strip_prompt_instructions(self, input_text: str) -> str:
        """剥掉素材文本末尾的写作指令段落，避免它被当成素材事实。

        指令段落由 `_build_closing_instruction` 生成，特征是：与素材字段之间隔一个空行，
        且以「请围绕」开头（各分支的文案都以它起句）。这里按「最后一个空行块且以指令起句」
        来切，切不掉时原样返回（宁可保留也不能误删素材）。
        """
        text = str(input_text or "")
        marker = f"\n\n{self._INSTRUCTION_SENTINEL}"
        idx = text.find(marker)
        if idx <= 0:
            return text
        body = text[:idx].rstrip()
        # 安全阀：剥完不能把素材削得太短，否则说明切错了
        if len(body) < 200:
            return text
        return body

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
                # ⚠️ 2026-09-19：软兜底**必须再过一遍账号边界**。
                # `_available_topics_soft` 只放宽"当天是否已发过"这类去重条件，
                # 它不看赛道；而选题池里存着别的赛道的历史条目。
                # 实测后果：社会故事号被软兜底塞进
                # 「海尔发布全自主家用 AI 烹饪机器人…」（matched_industries=
                # ['humanoid_robot','ai_hardware']），并真的抓素材写稿发出去了。
                # 边界是账号定位的底线，任何兜底都不许跨过去。
                in_scope = [
                    topic
                    for topic in available_topics
                    if self._topic_matches_account_boundary(topic)
                ]
                if len(in_scope) != len(available_topics):
                    self.logger.warning(
                        "[Topic Engine] Soft fallback dropped %d out-of-scope topics for account=%s.",
                        len(available_topics) - len(in_scope),
                        self.settings.account_key,
                    )
                available_topics = in_scope
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

        # 自动选题（未显式指定 --topic-rank）时，优先选带完整正文素材的行业媒体话题。
        # 背景：热榜源（toutiao_hot / baidu_hot）只有标题摘要，素材量不足以支撑
        # 自动发布所需的正文长度，会被字数门槛拦下并转入人工审核；
        # 而行业媒体源（ofweek / 36kr / zhidx 等）带完整文章，可正常走完自动发布链路。
        if rank is None:
            available_topics = self._prioritize_evidence_rich_topics(available_topics)

        target_rank = rank or self.settings.automation.default_topic_rank
        if target_rank < 1:
            raise ValueError("--topic-rank must be greater than or equal to 1.")
        if target_rank <= len(available_topics):
            return available_topics[target_rank - 1]
        raise ValueError(
            f"Only {len(available_topics)} unpublished topics remain, cannot select rank {target_rank}."
        )

    # 仅含标题/摘要、缺少正文素材的热榜源。其余源默认视为带完整文章的行业媒体源。
    HOT_LIST_ONLY_SOURCES = ("toutiao_hot", "baidu_hot")

    def _prioritize_evidence_rich_topics(
        self,
        topics: list[TopicCandidate],
    ) -> list[TopicCandidate]:
        """把带完整正文素材的行业媒体话题排到热榜话题之前。

        只做稳定分组（rich + hot_list），组内保持原热度顺序、不改动话题评分，
        避免影响热榜话题在需要时作为兜底被选中。
        """
        rich: list[TopicCandidate] = []
        hot_list: list[TopicCandidate] = []
        for topic in topics:
            source = str(getattr(topic, "source", "") or "").strip().lower()
            if source in self.HOT_LIST_ONLY_SOURCES:
                hot_list.append(topic)
            else:
                rich.append(topic)
        return rich + hot_list

    def _build_input_text(self, topic: TopicCandidate) -> str:
        humanized_industries = self._humanize_industries(topic.matched_industries)
        industries = "、".join(humanized_industries) if humanized_industries else "相关赛道待补充"
        keywords = "、".join(topic.matched_keywords) if topic.matched_keywords else "关键词待补充"
        numbers = "、".join(topic.extracted_numbers) if topic.extracted_numbers else "暂无明确数字"
        summary, snippet = self._build_prompt_evidence(topic)
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
        evidence_block = f"- 摘要：{summary}\n"
        if snippet:
            evidence_block += f"- 补充片段：{snippet}\n"

        return (
            f"标题：{topic.title}\n"
            f"来源：{_humanize_source(topic.source)}\n"
            f"发布日期：{published_at}\n"
            f"原文链接：{topic.url}\n\n"
            f"主赛道：{primary_industry}\n"
            f"主关键词：{primary_keywords}\n"
            f"匹配赛道：{industries}\n"
            f"匹配关键词：{keywords}\n"
            f"可确认数字：{numbers}\n"
            f"平台热度值：{topic.heat_value}\n\n"
            f"选题类型：{'social_story' if topic_mode == 'social_story' else topic.topic_type}\n"
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
            f"{evidence_block}"
            f"- 热度判断：阅读分 {topic.read_score} / 收藏分 {topic.save_score} / 讨论分 {topic.discussion_score} / 标题潜力 {topic.title_potential_score}\n\n"
            f"入选理由：{selection_reason}\n"
            f"推荐主结论：{suggested_angle}\n"
            f"跟踪变量：{follow_up_focus}\n"
            f"固定写法：{writing_brief}\n\n"
            f"{closing_instruction}"
        ).strip()

    def _preferred_topic_summary(self, topic: TopicCandidate) -> str:
        summary = str(topic.summary or "").strip()
        snippet = self._clean_topic_snippet_for_prompt(
            topic.snippet or "",
            topic.title,
            source_type=topic.source_type,
        )
        if (
            not summary
            or summary == topic.title
            # ⚠️ 2026-09-19：裸标题**不算证据**。摘要回退到标题时，提示词的「素材摘要」
            # 会变成一句和标题一模一样的话，模型没有任何事实可写，只能拿标题里的词
            # 编出「安全意识」「应急处理」这类万能话术（实测 855 字正文全是套话，
            # 标题池也因此只剩 1 条兜底模板）。宁可让上层判定「无可用素材」。
            or len(summary) < self.MIN_EVIDENCE_CHARS
            or self._looks_like_mojibake(summary)
            or self._summary_uses_wrong_domain_template(topic, summary)
        ):
            if snippet and not self._looks_like_mojibake(snippet):
                if len(snippet) >= self.MIN_EVIDENCE_CHARS or self._is_evidence_rich_topic_text(snippet):
                    return snippet[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH]
            return ""
        if self._is_evidence_rich_topic_text(summary):
            return summary[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH]
        return summary[: self.PROMPT_SUMMARY_LENGTH]

    #: 少于这个字数的「摘要」视为没有证据（裸标题一般 10-30 字）
    MIN_EVIDENCE_CHARS = 40

    def _preferred_topic_snippet(self, topic: TopicCandidate, fallback: str) -> str:
        snippet = self._clean_topic_snippet_for_prompt(
            topic.snippet or "",
            fallback,
            source_type=topic.source_type,
        )
        if self._looks_like_mojibake(snippet):
            return fallback
        if self._is_evidence_rich_topic_text(snippet):
            return snippet[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH]
        return snippet

    def _build_prompt_evidence(self, topic: TopicCandidate) -> tuple[str, str]:
        """Keep useful source context while avoiding duplicated summary blocks."""
        if getattr(topic, "_verified_source_material", False):
            material = re.sub(r"(?m)^【来源：[^】]+】\s*$", "", topic.snippet or "").strip()
            return material[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH], ""
        summary = self._preferred_topic_summary(topic).strip()
        snippet = self._preferred_topic_snippet(topic, summary).strip()
        if not snippet:
            return summary, ""

        normalized_summary = self._normalize_prompt_evidence(summary)
        normalized_snippet = self._normalize_prompt_evidence(snippet)
        if normalized_summary and normalized_summary in normalized_snippet:
            return snippet[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH], ""
        if normalized_snippet and normalized_snippet in normalized_summary:
            return summary[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH], ""

        if not summary:
            return snippet[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH], ""
        remaining = self.PROMPT_EVIDENCE_CONTEXT_LENGTH - len(summary) - 1
        if remaining <= 0:
            return summary[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH], ""
        return summary, snippet[:remaining]

    @staticmethod
    def _normalize_prompt_evidence(text: str) -> str:
        return re.sub(r"[\s\W_]+", "", str(text or "")).lower()

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
        if not candidate:
            return False
        # 叙事类赛道（社会故事）以外的模式不判串味 —— 产业稿摘要里本来就会出现
        # 「订单节奏」这类词，判错会把合格的产业摘要也丢掉。
        #
        # ⚠️ 2026-09-19：这里原来只放行 {"sports", "international"}，**漏了 social_story** ——
        # 于是社会故事选题带着产业模板摘要「…重点看老人对应的项目落地和订单节奏」
        # 一路走到 writer，被原样照抄进正文（实测 publish/article.md 第 13 段）。
        narrative_modes = {"sports", "international", "social_story"}
        if mode not in narrative_modes:
            return False
        wrong_markers = ("项目落地", "订单节奏", "产业链", "融资", "交付", "客户验证", "利润兑现")
        return any(marker in candidate for marker in wrong_markers)

    def _topic_mode(self, topic: TopicCandidate) -> str:
        """这条选题属于哪种「写法模式」。

        ⚠️ 2026-09-18 新增 social_story：账号换成社会故事赛道后，这里也必须跟着分模式。
        否则所有「角度 / 入选理由 / 跟踪变量 / 写法要求 / 结尾」都走产业分析那套默认文案，
        实测后果是把「男子高空作业绳子被2楼老人剪断」写成
        「重点看老人对应的项目落地和订单节奏」「毛利率可能会先承压」——
        writer 是忠实照抄这些上游字段的，口吻跑偏的根因就在这里，不在 writer。

        ⚠️ 2026-09-19 第二个坑：模式判据原来只按**题目内容**决定，不看账号赛道。
        实测「男子高空作业绳子被2楼老人剪断」被判成了 **international**：
        `_hydrate_topic_material` 把抓来的 3702 字正文写进了 `topic.snippet`，
        而国际判据正是在 snippet 上做关键词匹配，正文里一个「美国/欧洲」就让它抢在
        social_story 之前命中。后果是故事号的正文里出现国际模式的写作指令
        （「请先准确理解并消化素材里的英文信息，再写成自然中文国际热点解读稿」）。
        所以：**账号只做 social_story 时，体育/国际模式一律不适用** ——
        一个只发人间故事的号，不存在「这条要按体育稿写」的情况。
        """
        focus = self._account_focus_industries()
        story_account = "social_story" in focus
        if self._is_social_story_topic(topic):
            return "social_story"
        if story_account:
            # 故事号：跳过体育/国际判定，避免正文里的偶然词把体裁带跑
            if topic.source_type in {"hot_board", "baidu_hot"}:
                return "social_story" if "social_story" in focus else "hot_topic"
            return "generic"
        if self._is_sports_topic(topic):
            return "sports"
        if self._is_international_topic(topic):
            return "international"
        if topic.source_type in {"hot_board", "baidu_hot"} and "hot_topic" in (
            topic.matched_industries or []
        ):
            return "hot_topic"
        return "generic"

    def _account_focus_industries(self) -> set[str]:
        return {
            str(item).strip()
            for item in self.settings.topic_collection.focus_industries
            if str(item).strip()
        }

    def _is_social_story_topic(self, topic: TopicCandidate) -> bool:
        """账号是否在做社会故事赛道，且这条题确实通过了社会故事判据。

        判据与 topic_collection 共用同一份实现（那里是 classmethod），避免两处漂移。
        """
        focus = {
            str(item).strip()
            for item in self.settings.topic_collection.focus_industries
            if str(item).strip()
        }
        if "social_story" not in focus:
            return False
        from toutiao_content_factory.services.topic_collection import (
            TopicCollectionService,
        )

        return TopicCollectionService._has_social_story_signal(topic)

    def _resolve_prompt_track(self, topic: TopicCandidate, primary_industry: str) -> str:
        mode = self._topic_mode(topic)
        if mode == "social_story":
            return "社会故事"
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
        if topic_mode == "social_story":
            return (
                "请围绕这件真实发生的事，写一篇给普通读者看的人情故事观察稿。"
                "写作要求："
                "1. 只写素材里明确有的事实，缺的地方就留白；"
                "严禁编造当事人姓名、身份、对话、心理活动或现场细节；"
                "严禁写成第一人称亲历，也不要编「我认识的人」的故事。"
                "2. 把当事人的处境讲清楚：他当时在什么位置上、面对什么约束、能怎么做、结果如何，"
                "让读者能代入。"
                "3. 不要拆产业链、不要写订单与产能、不要做行业趋势或投资判断、不要出现毛利率这类词。"
                "4. 结尾给读者一句具体可用的提醒（涉及钱、合同、赡养、就医时该留什么证据、找谁、走什么流程），"
                "不要写成空泛的「引人深思」。"
                "5. 全文700到1000字，4到6个短段，每段2到3句，不要用 #、## 小标题。"
            )
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
        if topic_mode == "hot_topic":
            return (
                "这是实时热点二创：你拿到的素材只有热点标题和热度值，没有完整原文。"
                "请围绕热点标题展开，写成一篇有观点、有信息量的头条快评。"
                "写作要求："
                "1. 前两句直接给判断/钩子，不要铺垫，不要复述标题本身。"
                "2. 中段展开：这个热点为什么今天值得看、背后的原因/影响/争议点是什么、谁最关心它。"
                "3. 只能基于常识和公开背景做有限推断，明确区分事实与推测，禁止编造具体数字、人名、时间、公司或细节。"
                "4. 结尾留一个具体的开放式互动问题，引导评论区表达立场。"
                "5. 全文600到900字，5到7个短段，不要使用 #、## 小标题，不要写成行业研报。"
            )
        if topic_mode == "social_story":
            return (
                "请围绕这件真实发生的事，写清楚：发生了什么、涉及谁、结果如何；"
                "当事人当时面临什么处境和选择；这件事对读者有什么具体可用的提醒。"
                "只写素材里有的事实，不要编造人物、对话、心理活动或现场细节，不要写成第一人称亲历。"
            )
        return (
            "请围绕这条产业线索，重点分析："
            "这条线索目前处于什么阶段、涉及哪些公司/项目/订单主体、融资或订单意味着什么、它在产业链里卡住了哪一段、"
            "后续最值得跟踪的变量是什么，以及哪些风险会影响兑现节奏。"
        )

    def _clean_topic_snippet_for_prompt(
        self,
        snippet: str,
        fallback: str,
        *,
        source_type: str = "",
    ) -> str:
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
        if self._is_source_context_snippet(candidate, source_type):
            return candidate[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH]
        if self._is_evidence_rich_topic_text(candidate):
            return candidate[: self.PROMPT_EVIDENCE_CONTEXT_LENGTH]
        if len(re.findall(r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d+\s*(?:分钟前|小时前|天前))", candidate)) >= 2:
            return fallback
        if len(candidate) > 120 and candidate.count(" ") >= 6:
            return fallback
        if len(candidate) > 140 and sum(candidate.count(token) for token in ("，", "、", " | ", " / ")) >= 6:
            return fallback
        if sum(candidate.count(marker) for marker in ("鍥", "鏂", "鐢", "鑳", "缃", "鍌", "姹")) >= 6:
            return fallback
        return candidate[:160]

    def _is_source_context_snippet(self, text: str, source_type: str) -> bool:
        normalized_source_type = str(source_type or "").strip().lower()
        return (
            normalized_source_type in self.PROMPT_SOURCE_CONTEXT_TYPES
            and len(text) >= self.PROMPT_SOURCE_CONTEXT_MIN_LENGTH
        )

    @staticmethod
    def _is_evidence_rich_topic_text(text: str) -> bool:
        candidate = str(text or "")
        if not candidate:
            return False
        number_count = len(
            re.findall(
                r"\d+(?:\.\d+)?\s*(?:%|亿|亿元|万|万元|万台|万辆|万套|万吨|GWh|MWh|GW|MW|元)",
                candidate,
                flags=re.IGNORECASE,
            )
        )
        evidence_tokens = (
            "财报",
            "半年报",
            "年报",
            "营业总收入",
            "营收",
            "归母净利润",
            "净利润",
            "经营现金流",
            "现金流",
            "每股收益",
            "净资产收益率",
            "订单",
            "交付",
            "中标",
            "并网",
            "量产",
        )
        return number_count >= 2 and sum(token in candidate for token in evidence_tokens) >= 1

    def _write_selection_snapshot(
        self,
        topic: TopicCandidate,
        report: DailyTopicsReport,
        input_text: str,
    ) -> dict[str, Path]:
        enriched_topic = self._enrich_topic_revenue_metadata(topic)
        basename = self.settings.automation.selected_topic_basename
        json_path = self.settings.output_dir / f"{basename}.json"
        md_path = self.settings.output_dir / f"{basename}.md"
        material_evidence = dict(
            getattr(topic, "_material_evidence_assessment", {}) or {}
        )
        json_payload = {
            "selected_at": datetime.now().isoformat(timespec="seconds"),
            "generated_topics_at": report.generated_at,
            "selected_topic": asdict(enriched_topic),
            "material_evidence": material_evidence,
            "auto_input_text": input_text,
        }
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        humanized_industries = self._humanize_industries(enriched_topic.matched_industries)
        primary_industry = humanized_industries[0] if humanized_industries else "相关赛道"
        primary_keywords = "、".join(enriched_topic.matched_keywords[:4]) if enriched_topic.matched_keywords else "关键词待补充"
        prompt_track = self._resolve_prompt_track(enriched_topic, primary_industry)
        selection_reason = self._build_selection_reason(
            enriched_topic,
            primary_industry,
            primary_keywords,
            "、".join(enriched_topic.extracted_numbers) if enriched_topic.extracted_numbers else "暂无明确数字",
        )
        suggested_angle = self._build_suggested_angle(
            enriched_topic,
            primary_industry,
            primary_keywords,
            "、".join(enriched_topic.extracted_numbers) if enriched_topic.extracted_numbers else "暂无明确数字",
        )
        follow_up_focus = self._build_follow_up_focus(enriched_topic, primary_industry, primary_keywords)
        content_format_label = enriched_topic.recommended_format_label or "深度分析"
        format_brief = self._build_content_format_brief(enriched_topic, primary_industry)
        paragraph_instruction = self._build_paragraph_instruction(enriched_topic)

        markdown = (
            "# 今日自动选题\n\n"
            f"- 标题：{enriched_topic.title}\n"
            f"- 排名：{enriched_topic.rank or 1}\n"
            f"- 来源：{_humanize_source(enriched_topic.source)}\n"
            f"- 发布时间：{enriched_topic.published_at or '未知'}\n"
            f"- 原文链接：{enriched_topic.url}\n"
            f"- 匹配赛道：{'、'.join(humanized_industries) or '无'}\n"
            f"- 关键词：{'、'.join(enriched_topic.matched_keywords) or '无'}\n"
            f"- 关键数字：{'、'.join(enriched_topic.extracted_numbers) or '无'}\n"
            f"- 评分：总分 {enriched_topic.total_score} / 阅读 {enriched_topic.read_score} / 收藏 {enriched_topic.save_score} / 讨论 {enriched_topic.discussion_score} / 标题潜力 {enriched_topic.title_potential_score}\n"
            f"- 商业意图：{enriched_topic.commercial_intent}\n"
            f"- 内容组合：{enriched_topic.content_mix}\n"
            f"- CTA 变体：{enriched_topic.cta_variant or '未指定'}\n"
            f"- CTA 来源码：{enriched_topic.cta_source_code or '未指定'}\n"
            f"- 标题变体：{enriched_topic.title_variant or '未指定'}\n"
            f"- 封面变体：{enriched_topic.cover_variant or '未指定'}\n"
            f"- 平台改写：{enriched_topic.platform_variant or '未指定'}\n\n"
            "## 质量判断\n\n"
            f"- 选题类型：{enriched_topic.topic_type_label or enriched_topic.topic_type or '行业事件'}\n"
            f"- 推荐体裁：{content_format_label}\n"
            f"- 证据强度：{enriched_topic.evidence_strength}\n"
            f"- 选题质量分：{enriched_topic.quality_score}\n"
            f"- 素材独立来源：{material_evidence.get('independent_source_count', '未评估')}\n"
            f"- 素材事实锚点：{material_evidence.get('factual_anchor_count', '未评估')}\n"
            f"- 素材总字数：{material_evidence.get('total_chars', '未评估')}\n"
            f"- 标题打法赛道：{prompt_track}\n"
            f"- 标题抓手：{'、'.join(enriched_topic.title_potential_reasons) or '无'}\n"
            f"- 质量标记：{'、'.join(enriched_topic.quality_flags) or '无'}\n"
            f"- 体裁要求：{format_brief}\n"
            f"- 段落要求：{paragraph_instruction}\n\n"
            "## 选题判断\n\n"
            f"- 入选理由：{selection_reason}\n"
            f"- 推荐主结论：{suggested_angle}\n"
            f"- 跟踪变量：{follow_up_focus}\n\n"
            "## 摘要\n\n"
            f"{enriched_topic.summary or enriched_topic.snippet or enriched_topic.title}\n\n"
            "## 自动生成输入\n\n"
            "```text\n"
            f"{input_text}\n"
            "```\n"
        )
        md_path.write_text(markdown, encoding="utf-8")
        exported: dict[str, Path] = {
            "selected_topic_json": json_path,
            "selected_topic_markdown": md_path,
        }
        material_path = self._capture_source_material(topic)
        if material_path is not None:
            exported["source_material_full"] = material_path
        return exported

    def _capture_source_material(self, topic: TopicCandidate) -> Path | None:
        """把被选中素材的原文全文落盘，作为原创查重的比对基准。

        背景：头条原创库做全网比对，素材原文常已被其他账号先发。本地查重
        原先只比自家历史发布，查不出「与素材有多像」，导致 51.5% 连续抄录
        的稿子照发并被判不原创扣分。存下原文后，发布前即可自动量化抄录率。

        只抓被选中的这一条，且全程兜底：抓不到就退回摘要，绝不阻断主流程。
        """
        target = self.settings.output_dir / "source_material_full.txt"
        if getattr(topic, "_verified_source_material", False):
            target.write_text(topic.snippet, encoding="utf-8")
            return target
        url = str(getattr(topic, "url", "") or "").strip()
        if not url.startswith(("http://", "https://")):
            # 热榜选题没有素材源。必须清掉上一轮残留，否则归档时会把
            # 无关的旧素材当成这一篇的比对基准，算出的抄录率毫无意义。
            target.unlink(missing_ok=True)
            return None
        fallback_text = "\n".join(
            part for part in (str(getattr(topic, "summary", "") or "").strip(),
                              str(getattr(topic, "snippet", "") or "").strip())
            if part
        ).strip()
        body = ""
        try:
            # 延迟导入，避免与采集模块形成循环依赖。
            from toutiao_content_factory.services.topic_collection import TopicCollectionService

            collector = TopicCollectionService(self.settings, self.logger)
            html_text = collector._fetch_text_sync(url)
            if html_text:
                body = collector._extract_article_context(html_text, topic.title)
        except Exception as exc:  # 抓全文是增强项，失败不能影响主流程
            self.logger.warning("Source material capture failed for %s: %s", url, exc)

        content = body.strip() or fallback_text
        if not content:
            return None
        header = f"素材来源：{getattr(topic, 'source', '') or '未知'}\n原文标题：{topic.title}\n原文链接：{url}\n\n"
        target.write_text(header + content, encoding="utf-8")
        return target

    def _enrich_topic_revenue_metadata(self, topic: TopicCandidate) -> TopicCandidate:
        """为选题补齐收益优化所需的元数据。"""
        story_mode = self._topic_mode(topic) == "social_story"
        commercial_intent = str(getattr(topic, "commercial_intent", "")).strip()
        if not commercial_intent:
            commercial_intent = self._infer_commercial_intent(topic)
        content_mix_value = str(getattr(topic, "content_mix", "")).strip()
        if not content_mix_value:
            content_mix_value = self._infer_content_mix(topic, commercial_intent)
        cta_variant = str(getattr(topic, "cta_variant", "")).strip()
        if not cta_variant:
            cta_variant = self._infer_cta_variant(content_mix_value)
        if story_mode:
            content_mix_value = "traffic"
            cta_variant = "discussion"
        cta_source_code = str(getattr(topic, "cta_source_code", "")).strip()
        if story_mode:
            cta_source_code = f"社会故事_{commercial_intent}_{cta_variant}"
        elif not cta_source_code:
            cta_source_code = self._build_cta_source_code(topic, cta_variant)
        title_variant = str(getattr(topic, "title_variant", "")).strip()
        if not title_variant:
            title_variant = self._infer_title_variant(topic)
        cover_variant = str(getattr(topic, "cover_variant", "")).strip()
        if not cover_variant:
            cover_variant = self._infer_cover_variant(topic)
        platform_variant = str(getattr(topic, "platform_variant", "")).strip()
        if not platform_variant:
            platform_variant = "multi_platform_rewrite"
        topic_bucket = str(getattr(topic, "topic_bucket", "")).strip()
        if not topic_bucket:
            from toutiao_content_factory.services.revenue_attribution_service import (
                RevenueAttributionService,
            )

            topic_bucket = RevenueAttributionService(self.settings.output_dir, self.logger).topic_bucket_for(
                topic_title=topic.title,
                title=topic.title,
                summary=topic.summary or topic.snippet,
            )
        return TopicCandidate(
            title=topic.title,
            url=topic.url,
            source=topic.source,
            source_type=topic.source_type,
            published_at=topic.published_at,
            snippet=topic.snippet,
            summary=topic.summary,
            matched_industries=list(topic.matched_industries),
            matched_keywords=list(topic.matched_keywords),
            extracted_numbers=list(topic.extracted_numbers),
            heat_value=topic.heat_value,
            heat_score=topic.heat_score,
            read_score=topic.read_score,
            save_score=topic.save_score,
            discussion_score=topic.discussion_score,
            total_score=topic.total_score,
            title_potential_score=topic.title_potential_score,
            title_playbook_track="社会故事" if story_mode else topic.title_playbook_track,
            title_potential_reasons=list(topic.title_potential_reasons),
            is_high_read=topic.is_high_read,
            is_high_save=topic.is_high_save,
            is_high_discussion=topic.is_high_discussion,
            topic_type="social_story" if story_mode else topic.topic_type,
            topic_type_label="社会故事" if story_mode else topic.topic_type_label,
            evidence_strength=topic.evidence_strength,
            quality_score=topic.quality_score,
            quality_flags=list(topic.quality_flags),
            recommended_format=topic.recommended_format,
            recommended_format_label=topic.recommended_format_label,
            rank=topic.rank,
            commercial_intent=commercial_intent,
            content_mix=content_mix_value,
            cta_variant=cta_variant,
            cta_source_code=cta_source_code,
            title_variant=title_variant,
            cover_variant=cover_variant,
            platform_variant=platform_variant,
            topic_bucket=topic_bucket,
        )

    def _infer_commercial_intent(self, topic: TopicCandidate) -> str:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        high_tokens = ("融资", "订单", "中标", "签约", "交付", "量产", "毛利率", "利润", "客户验证")
        medium_tokens = ("产业链", "芯片", "储能", "机器人", "AI硬件", "电站", "光伏", "风电")
        if any(token in text for token in high_tokens):
            return "high"
        if any(token in text for token in medium_tokens):
            return "medium"
        return "low"

    def _infer_content_mix(self, topic: TopicCandidate, commercial_intent: str) -> str:
        mode = self._topic_mode(topic)
        if mode in {"sports", "hot_topic"}:
            return "traffic"
        if commercial_intent == "high":
            return "conversion"
        if topic.is_high_save or topic.save_score >= 60:
            return "professional"
        return "traffic"

    def _infer_cta_variant(self, content_mix: str) -> str:
        if content_mix == "conversion":
            return "consultation"
        if content_mix == "professional":
            return "newsletter"
        return "discussion"

    def _build_cta_source_code(self, topic: TopicCandidate, cta_variant: str) -> str:
        track = str(topic.title_playbook_track or "").strip() or "general"
        intent = str(getattr(topic, "commercial_intent", "medium")).strip()
        return f"{track}_{intent}_{cta_variant}"

    def _infer_title_variant(self, topic: TopicCandidate) -> str:
        reasons = list(topic.title_potential_reasons or [])
        if any("对比" in reason for reason in reasons):
            return "contrast_hook"
        if any("数字" in reason for reason in reasons) or topic.extracted_numbers:
            return "data_hook"
        return "insight_hook"

    def _infer_cover_variant(self, topic: TopicCandidate) -> str:
        if topic.extracted_numbers:
            return "data_anchor"
        if any(token in topic.title for token in ("机场", "工厂", "车间", "港口", "电站")):
            return "scene_anchor"
        return "industry_contrast"

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
            elif topic_mode == "social_story":
                reasons.append(f"围绕{primary_keywords.split('、')[0]}这个生活场景，读者容易有话可说")
            else:
                reasons.append(f"与{primary_industry}里的{primary_keywords.split('、')[0]}环节相关")
        if not reasons:
            if topic_mode == "social_story":
                reasons.append("是普通人能代入的生活处境，适合讲成有人情味的观察稿")
            elif topic_mode == "sports":
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
        if topic_mode == "social_story":
            story_subject = (
                primary_keywords.split("、")[0]
                if primary_keywords and primary_keywords != "关键词待补充"
                else str(topic.title or "这件事").strip()
            )
            return (
                f"把{story_subject}的公开经过和人物处境讲清楚："
                "当事人当时能怎么选、哪一步本可以不一样、结果给普通人什么提醒。"
                "读者更关心的是，换成自己遇到类似情况时可以怎么做。"
            )
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
        if topic_mode == "social_story":
            return "事情后续是否有了处理结果、当事人的处境有没有变化、同类情况是否还在发生"
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
        if topic_mode == "social_story":
            return (
                "写成人情故事观察稿：先把事情本身讲清楚，再讲当事人的处境和选择，"
                "最后给读者一句具体可用的提醒。不要套用与事件无关的商业分析框架，"
                "也不要编造人物对话或写成第一人称亲历。"
            )
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
        if topic_mode == "social_story":
            return "适合写成人情故事观察稿：把事情讲明白、把处境讲透、给读者一句实在提醒，篇幅中等即可。"
        if topic_mode == "hot_topic":
            return "适合写成热点快评：有观点、有信息量、适合移动端快速阅读，不要强行拉成长篇行业分析。"
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
        if topic_mode == "social_story":
            return "4到6段，每段2到4句，首段先把事情说清楚，中段讲处境，结尾给提醒并留一个具体的互动问题。"
        if topic_mode == "hot_topic":
            return "5到7段，每段2到3句，首段直接给判断，结尾只留一个互动问题，段落短平快。"
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
        if topic_mode == "social_story":
            # 保留 1700 字的内部质量目标，让故事稿有空间交代事实、来源、
            # 原创判断、信息增量和具体提醒；平台首发资格本身只要求正文超过 100 字。
            return max(1700, 800)
        if topic_mode == "hot_topic":
            return max(800, 600)
        if topic_mode == "sports":
            return max(900, 800)
        if topic_mode == "international":
            return max(1000, 800)
        if topic.recommended_format == "quick_commentary":
            return max(800, 800)
        if topic.recommended_format == "data_recap":
            return max(900, 800)
        if topic.recommended_format == "project_breakdown":
            return max(1800, 800)
        return max(1800, 800)

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
            return self._apply_revenue_policy(
                [self._enrich_topic_revenue_metadata(topic) for topic in topics]
            )

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
            available.append(self._enrich_topic_revenue_metadata(topic))
        return self._apply_revenue_policy(available)

    def _available_topics_soft(
        self,
        topics: list[TopicCandidate],
        allow_generated_duplicates: bool = False,
    ) -> list[TopicCandidate]:
        if self.settings.publishing.allow_duplicate_topics:
            return self._apply_revenue_policy(
                [self._enrich_topic_revenue_metadata(topic) for topic in topics]
            )

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
            available.append(self._enrich_topic_revenue_metadata(topic))
        return self._apply_revenue_policy(available)

    def _revenue_optimization_config(self) -> dict[str, Any]:
        config = getattr(self.settings, "revenue_optimization", None)
        defaults = {
            "enabled": True,
            "lookback_days": 30,
            "min_samples": 10,
            "weak_rpm_threshold": 0.0,
            "weak_read_rate_threshold": 3.0,
            "cooldown_weak_ratio": 0.5,
            "stop_weak_ratio": 0.8,
            "cooldown_penalty": 12,
        }
        if config is None:
            return defaults
        for key in defaults:
            defaults[key] = getattr(config, key, defaults[key])
        return defaults

    def _load_revenue_cooling_candidates(self) -> dict[str, dict[str, Any]]:
        config = self._revenue_optimization_config()
        if not bool(config.get("enabled", True)):
            return {}
        try:
            from toutiao_content_factory.services.revenue_attribution_service import (
                RevenueAttributionService,
            )

            service = RevenueAttributionService(self.settings.output_dir, self.logger)
            report = service.build_cooling_candidates(
                lookback_days=int(config["lookback_days"]),
                min_samples=int(config["min_samples"]),
                weak_rpm_threshold=float(config["weak_rpm_threshold"]),
                weak_read_rate_threshold=float(config["weak_read_rate_threshold"]),
                cooldown_weak_ratio=float(config["cooldown_weak_ratio"]),
                stop_weak_ratio=float(config["stop_weak_ratio"]),
                cooldown_penalty=int(config["cooldown_penalty"]),
            )
            return {
                str(item.get("bucket", "")): item
                for item in report.get("candidates", [])
                if isinstance(item, dict) and str(item.get("bucket", "")).strip()
            }
        except Exception as exc:  # pragma: no cover - legacy/partial ledgers must not block selection
            self.logger.warning("Revenue cooling policy unavailable; keeping topic selection unchanged: %s", exc)
            return {}

    def _apply_revenue_policy(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        """Remove only hard-stop buckets and move cooldown buckets behind healthy ones."""
        candidates = self._load_revenue_cooling_candidates()
        if not candidates:
            return topics
        config = self._revenue_optimization_config()
        result: list[tuple[bool, int, TopicCandidate]] = []
        for topic in topics:
            bucket = str(getattr(topic, "topic_bucket", "")).strip() or "general"
            policy = candidates.get(bucket, {})
            if str(policy.get("recommendation", policy.get("action", ""))).strip().lower() == "stop":
                self.logger.info("[Topic Engine] Revenue policy stopped bucket=%s title=%s", bucket, topic.title)
                continue
            penalty = int(policy.get("score_penalty", config.get("cooldown_penalty", 12)) or 0)
            if policy:
                topic = replace(
                    topic,
                    total_score=max(0, int(topic.total_score or 0) - penalty),
                )
            result.append((bool(policy), -int(topic.total_score or 0), topic))
        result.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in result]

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

    def _recent_run_name_prefixes(self, days: int) -> tuple[str, ...]:
        """近 `days` 天（含今天）的运行目录名前缀，形如 ``("20260920", "20260919", ...)``。

        运行目录名以 ``YYYYMMDD-HHMMSS_选题`` 开头，用前缀匹配即可按天筛选。
        """
        today = datetime.now().date()
        return tuple(
            (today - timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(max(1, days))
        )

    def _load_published_keys_for_today(self) -> dict[str, set[str]]:
        """近期已发布选题的标题/链接指纹。

        ⚠️ 2026-09-21：窗口从"仅当天"放宽到 `TOPIC_DEDUP_WINDOW_DAYS` 天。
        原先这里只看当天，而发布层 `PublishService._was_topic_published` 看 **7 天** ——
        两层判据不一致的后果实测如下（云端 run 35523301070）：

            07:00 定时运行发布「独居老人摔倒」→ 成功
            选题层：当天记录里没有它 → 明天照样把它排在第一
            发布层：7 天记录里有它   → Duplicate publish blocked
            → 整天一篇都发不出去，而且是"写完了才被拦"，白烧一遍模型调用

        去重的目的（RULE_009：同一选题 6 天内发 3 次，单篇展现从 11.7 万掉到 548）
        只有在**选题阶段**生效才有意义 —— 发布阶段才拦等于当天报废。
        所以这里必须与发布层取同一个窗口，宁可早一点换题，不要写完才发现发不出去。
        """
        history_path = self.settings.output_dir / self.settings.publishing.history_filename
        result = {"titles": set(), "urls": set()}
        if not history_path.exists():
            return result
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return result
        cutoff = (
            datetime.now().date() - timedelta(days=TOPIC_DEDUP_WINDOW_DAYS - 1)
        ).isoformat()
        for item in payload:
            if not isinstance(item, dict):
                continue
            published_at = str(item.get("published_at", ""))
            # `published_at` 是 ISO 时间戳，按日期部分做**字典序**比较即可；
            # 空值视为未知、不参与去重（旧记录里存在缺字段的历史条目）。
            if not published_at or published_at[:10] < cutoff:
                continue
            topic_title = str(item.get("topic_title", ""))
            topic_url = str(item.get("topic_url", ""))
            if topic_title:
                result["titles"].add(self._normalize_text(topic_title))
            if topic_url:
                result["urls"].add(topic_url)
        return result

    def _load_generated_keys_for_today(self) -> dict[str, set[str]]:
        """近期**已经生成过稿件**的选题指纹（无论最终是否发布）。

        窗口同样取 `TOPIC_DEDUP_WINDOW_DAYS` 天。已生成但没发出去的选题
        （被字数门槛/评分拦下、或被去重拦下的那些）如果不计入，明天还会被再选一次，
        于是"同一选题反复写、反复发不出去"会一直空转。
        """
        result = {"titles": set(), "urls": set()}
        runs_root = self.settings.output_dir / self.settings.automation.archive_dirname
        if not runs_root.exists():
            return result

        prefixes = self._recent_run_name_prefixes(TOPIC_DEDUP_WINDOW_DAYS)
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir() or not run_dir.name.startswith(prefixes):
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
        cutoff = (
            datetime.now().date() - timedelta(days=TOPIC_DEDUP_WINDOW_DAYS - 1)
        ).isoformat()
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
                if not published_at or published_at[:10] < cutoff:
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
        prefixes = self._recent_run_name_prefixes(TOPIC_DEDUP_WINDOW_DAYS)
        for account_key in self._iter_other_enabled_account_keys():
            runs_root = self._resolve_output_dir_for_account(account_key) / self.settings.automation.archive_dirname
            if not runs_root.exists():
                continue
            for run_dir in runs_root.iterdir():
                if not run_dir.is_dir() or not run_dir.name.startswith(prefixes):
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
        """这道题归不归这个账号。

        ⚠️ 2026-09-18：这里原来把账号**硬编码**成垂直号：

            if account_key == "robot_notes":
                return self._is_robot_topic(topic)

        于是 `robot_notes` 无论配置成什么赛道，都只收机器人题 —— 它的本意是
        「防止机器人号发无关社会热点导致定位漂移」，但账号换了赛道之后，
        这段硬编码反过来把新赛道的题全部拦掉：实测社会故事类账号的
        6 条候选被这道边界过滤清成 0 条，选题池彻底为空。

        现在改为**按账号声明的赛道**判断，而不是按账号 key：
          - 声明了 hot_topic 的账号：热榜题放行（热点二创是涨浏览量的核心路径）；
          - 声明了 social_story 的账号：只收通过社会故事判据的题；
          - 声明了垂直赛道（机器人/能源/国际/体育）的账号：照旧只收本赛道题。
        这样换赛道只需要改配置，不会再被硬编码挡住。
        """
        account_key = self.settings.account_key
        focus = {str(item).strip() for item in self.settings.topic_collection.focus_industries}
        is_hot_source = topic.source_type in {"hot_board", "baidu_hot"}

        # 社会故事类：以专题判据为准，不看机器人/能源等垂直分数。
        if "social_story" in focus:
            from toutiao_content_factory.services.topic_collection import (
                TopicCollectionService,
            )

            return TopicCollectionService._has_social_story_signal(topic)

        # 声明了热点赛道的垂直账号：热榜题必须命中自己的赛道关键词。
        if is_hot_source and "hot_topic" in focus:
            vertical = {
                "robot_notes": ("robot", "energy"),
                "energy_notes": ("energy",),
                "politics_notes": ("international",),
                "sports_notes": ("sports",),
            }.get(account_key)
            if vertical is not None:
                robot_score, energy_score, international_score, sports_score = (
                    self._topic_domain_scores(topic)
                )
                scores = {
                    "robot": robot_score,
                    "energy": energy_score,
                    "international": international_score,
                    "sports": sports_score,
                }
                if any(scores.get(name, 0) >= 1 for name in vertical):
                    return True
                # 未命中垂直赛道关键词的热榜话题，交给默认账号消化
                return False
            return True

        # 垂直账号（未声明 hot_topic 时）：只收本赛道题。
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
