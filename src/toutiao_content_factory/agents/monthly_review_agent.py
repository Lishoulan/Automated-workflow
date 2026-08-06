"""MonthlyReviewAgent - 月度回顾分析 Agent。

读取过去 30 天的发布数据（来自 performance_ledger.json），
生成结构化回顾报告（top_performers / bottom_performers / topic_hit_rate /
time_slot_performance），为内容迭代提供数据支持。

报告输出路径：``.runtime/accounts/{account}/monthly_review_report.json``

可通过命令行运行：

    python -m toutiao_content_factory.agents.monthly_review_agent --account default
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------
# 常量配置
# ------------------------------------------------------------------

LOOKBACK_DAYS = 30
MIN_RECORDS_FOR_ANALYSIS = 5
TOP_K = 5
BOTTOM_K = 5

# 综合得分权重：阅读量 0.4 + 点赞 0.3 + 评论 0.3
WEIGHT_VIEWS = 0.4
WEIGHT_LIKES = 0.3
WEIGHT_COMMENTS = 0.3

LEDGER_FILENAME = "performance_ledger.json"
REPORT_FILENAME = "monthly_review_report.json"
SUGGESTIONS_FILENAME = "iteration_suggestions.json"

# cron 时段定义（参考项目 daily_run_times / weekend_run_times 配置）
# 时段容差：发布时间落在 ±2 小时窗口内即归入对应时段
TIME_SLOT_TOLERANCE_HOURS = 2
TIME_SLOTS: dict[str, dict[str, Any]] = {
    "weekday_07": {
        "label": "工作日 07:00",
        "hour": 7,
        "minute": 0,
        "is_weekend": False,
    },
    "weekday_12_30": {
        "label": "工作日 12:30",
        "hour": 12,
        "minute": 30,
        "is_weekend": False,
    },
    "weekday_18": {
        "label": "工作日 18:00",
        "hour": 18,
        "minute": 0,
        "is_weekend": False,
    },
    "weekend_08": {
        "label": "周末 08:00",
        "hour": 8,
        "minute": 0,
        "is_weekend": True,
    },
}

# 4 种互动话术模板关键词（用于检测 cta_style 或正文中的互动设计）
CTA_TEMPLATES: dict[str, tuple[str, ...]] = {
    "站队式": ("你觉得", "你支持", "你选", "站队", "你更看好", "你怎么看", "你会选"),
    "变量追问式": ("真正值得", "后续要盯", "继续跟踪", "下一步要看", "变量", "值得跟踪"),
    "反事实假设式": ("如果", "假设", "假如", "要是", "要是没有", "万一"),
    "经验补充式": ("你怎么处理", "你遇到过", "你的经验", "欢迎补充", "你也来说", "你来聊聊"),
}

# 常见标题模板句式（用于检测 BOTTOM5 标题模板化问题）
TITLE_TEMPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r".+开始.+背后"),
    re.compile(r".+谁在.+"),
    re.compile(r".+风口.+"),
    re.compile(r".+到底是.+"),
    re.compile(r".+凭什么.+"),
    re.compile(r"^.{0,4}突然.+"),
    re.compile(r".+开始进入.+"),
)

# 公司名正则（参考 BaseAgent.COMPANY_SUFFIX_RE，简化版）
COMPANY_NAME_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{2,12}"
    r"(?:科技|能源|汽车|股份|集团|电气|智能|机器人|半导体|电子|时代|精工|制造|"
    r"材料|电池|重工|工业|光伏|储能|动力|机电|控股|装备|技术)"
)

# 数字正则（用于统计标题/正文中的数据点数量）
DIGIT_RE = re.compile(r"\d+")

# 对立观点关键词
OPPOSITION_KEYWORDS: tuple[str, ...] = (
    "然而", "但是", "不过", "可是", "虽然", "尽管", "反观", "相比之下", "与此相反",
)


class MonthlyReviewAgent:
    """月度回顾 Agent。

    读取 ``performance_ledger.json`` 中过去 30 天的发布记录，计算综合排名，
    分析 TOP5 共性特征与 BOTTOM5 共性问题，统计选题命中率和发布时段表现，
    输出 ``monthly_review_report.json`` 供内容迭代参考。

    构造方式参考 PublishPrecheckService，使用 ``account`` 参数隔离报告输出目录。
    """

    agent_name = "MonthlyReviewAgent"

    def __init__(self, account: str = "default", logger: logging.Logger | None = None) -> None:
        """初始化月度回顾 Agent。

        Args:
            account: 账号标识，用于定位 ``.runtime/accounts/{account}/`` 输出目录。
            logger: 可选日志记录器，未提供时使用本模块默认 logger。
        """
        self.account = account or "default"
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """执行月度回顾分析，输出报告文件并返回回顾结果 dict。

        Returns:
            包含 ``status`` 字段的回顾结果字典。数据不足时返回
            ``{"status": "insufficient_data", "record_count": N}``；
            正常时返回含 ``report_path`` / ``top_performers`` 等字段的字典。
        """
        ledger_path = self._resolve_ledger_path()
        if ledger_path is None:
            self.logger.warning(
                "%s 跳过：未找到 %s。请先运行 growth_ops 流程生成账本。",
                self.agent_name,
                LEDGER_FILENAME,
            )
            return {"status": "missing_ledger", "record_count": 0}

        records = self._load_records(ledger_path)
        if not records:
            self.logger.info("%s 跳过：账本为空或读取失败：%s", self.agent_name, ledger_path)
            return {"status": "empty_ledger", "ledger_path": str(ledger_path), "record_count": 0}

        recent_records = self._filter_recent_records(records, lookback_days=LOOKBACK_DAYS)
        self.logger.info(
            "%s 加载完成：账本共 %d 条，近 %d 天 %d 条",
            self.agent_name,
            len(records),
            LOOKBACK_DAYS,
            len(recent_records),
        )

        if len(recent_records) < MIN_RECORDS_FOR_ANALYSIS:
            self.logger.info(
                "%s 数据不足：近 %d 天仅 %d 条记录（阈值 %d），不生成迭代建议。",
                self.agent_name,
                LOOKBACK_DAYS,
                len(recent_records),
                MIN_RECORDS_FOR_ANALYSIS,
            )
            return {
                "status": "insufficient_data",
                "record_count": len(recent_records),
                "lookback_days": LOOKBACK_DAYS,
                "ledger_path": str(ledger_path),
            }

        scored = [self._attach_composite_score(item) for item in recent_records]
        ranked = sorted(scored, key=lambda item: item["_composite_score"], reverse=True)
        top_records = ranked[:TOP_K]
        bottom_records = list(reversed(ranked[-BOTTOM_K:])) if len(ranked) >= BOTTOM_K else list(reversed(ranked))

        top_performers = self._analyze_top_performers(top_records)
        bottom_performers = self._analyze_bottom_performers(bottom_records)
        topic_hit_rate = self._compute_topic_hit_rate(scored)
        time_slot_performance = self._compute_time_slot_performance(scored)

        report_payload: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account": self.account,
            "lookback_days": LOOKBACK_DAYS,
            "record_count": len(recent_records),
            "top_performers": top_performers,
            "bottom_performers": bottom_performers,
            "topic_hit_rate": topic_hit_rate,
            "time_slot_performance": time_slot_performance,
        }

        report_path = self._write_report(report_payload)

        # 生成数据驱动的迭代建议（SubTask 9.7）
        suggestions = self._generate_suggestions(report_payload)
        suggestions_path = self._write_suggestions(suggestions)
        report_payload["iteration_suggestions_path"] = str(suggestions_path)
        report_payload["iteration_suggestions_count"] = len(suggestions)

        report_payload["status"] = "ok"
        report_payload["report_path"] = str(report_path)
        report_payload["ledger_path"] = str(ledger_path)

        self.logger.info(
            "%s 报告已生成：%s（top=%d, bottom=%d）",
            self.agent_name,
            report_path,
            len(top_records),
            len(bottom_records),
        )
        return report_payload

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def _resolve_ledger_path(self) -> Path | None:
        """按优先级查找 performance_ledger.json 路径。

        候选路径：
            1. ``.runtime/accounts/{account}/performance_ledger.json`` （任务规范主路径）
            2. ``output/growth_ops/performance/performance_ledger.json`` （默认账号实际路径）
            3. ``output/accounts/{account}/growth_ops/performance/performance_ledger.json``
               （非默认账号实际路径）
        """
        project_root = self._find_project_root()
        candidates: list[Path] = [
            project_root / ".runtime" / "accounts" / self.account / LEDGER_FILENAME,
            project_root / "output" / "growth_ops" / "performance" / LEDGER_FILENAME,
            project_root / "output" / "accounts" / self.account / "growth_ops" / "performance" / LEDGER_FILENAME,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _report_path(self) -> Path:
        """返回 monthly_review_report.json 输出路径。"""
        project_root = self._find_project_root()
        report_dir = project_root / ".runtime" / "accounts" / self.account
        report_dir.mkdir(parents=True, exist_ok=True)
        return report_dir / REPORT_FILENAME

    def _find_project_root(self) -> Path:
        """向上查找项目根目录（包含 pyproject.toml 或 src 目录的层级）。"""
        cwd = Path.cwd()
        for candidate in [cwd, *cwd.parents]:
            if (candidate / "pyproject.toml").exists() or (candidate / "src").is_dir():
                return candidate
            if candidate.parent == candidate:  # 到达根目录
                break
        return cwd

    # ------------------------------------------------------------------
    # 数据加载与过滤
    # ------------------------------------------------------------------

    def _load_records(self, path: Path) -> list[dict[str, Any]]:
        """读取账本 JSON 文件并归一化为 list[dict]。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("读取账本失败 %s: %s", path, exc)
            return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("records", "articles", "items"):
                if isinstance(payload.get(key), list):
                    return [item for item in payload[key] if isinstance(item, dict)]
            return [payload]
        return []

    def _filter_recent_records(
        self, records: list[dict[str, Any]], lookback_days: int
    ) -> list[dict[str, Any]]:
        """基于 published_at 字段过滤最近 N 天的记录。

        支持解析 ISO 8601 / ``YYYY-MM-DD`` / ``YYYY-MM-DD HH:MM:SS`` 等格式。
        缺少 published_at 或解析失败的记录将被剔除。
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)
        recent: list[dict[str, Any]] = []
        for item in records:
            published_at = self._parse_datetime(item.get("published_at") or item.get("timestamp"))
            if published_at is None:
                continue
            if published_at >= cutoff:
                recent.append(item)
        return recent

    def _parse_datetime(self, value: Any) -> datetime | None:
        """解析常见日期时间字符串为 datetime 对象。"""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # 兼容 ISO 8601 带时区后缀
        candidate = text.replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # 综合排名（SubTask 8.4）
    # ------------------------------------------------------------------

    def _attach_composite_score(self, record: dict[str, Any]) -> dict[str, Any]:
        """为记录附加 ``_composite_score`` 字段（阅读*0.4 + 点赞*0.3 + 评论*0.3）。

        字段缺失时用 ``.get()`` 取默认 0，避免抛错。
        """
        metrics = record.get("metrics_latest") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        views = self._to_float(metrics.get("read_count") or record.get("views") or record.get("read_count"))
        likes = self._to_float(metrics.get("like_count") or record.get("like_count"))
        comments = self._to_float(metrics.get("comment_count") or record.get("comment_count"))
        composite = views * WEIGHT_VIEWS + likes * WEIGHT_LIKES + comments * WEIGHT_COMMENTS
        enriched = dict(record)
        enriched["_composite_score"] = round(composite, 2)
        enriched["_views"] = views
        enriched["_likes"] = likes
        enriched["_comments"] = comments
        return enriched

    def _to_float(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------
    # TOP5 共性特征分析（SubTask 8.5）
    # ------------------------------------------------------------------

    def _analyze_top_performers(self, top_records: list[dict[str, Any]]) -> dict[str, Any]:
        """分析 TOP5 共性特征：标题模式、话题类型、互动话术、深度要素。"""
        return {
            "records": [self._summarize_record(item) for item in top_records],
            "title_patterns": self._analyze_title_patterns(top_records),
            "topic_type_distribution": self._analyze_topic_type_distribution(top_records),
            "cta_template_distribution": self._analyze_cta_templates(top_records),
            "depth_features": self._analyze_depth_features(top_records),
        }

    def _summarize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """生成记录的精简摘要（用于报告展示）。"""
        metrics = record.get("metrics_latest") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        tags = record.get("experiment_tags") or {}
        if not isinstance(tags, dict):
            tags = {}
        return {
            "title": str(record.get("title", "")),
            "published_at": str(record.get("published_at", "")),
            "platform": str(record.get("platform", "")),
            "composite_score": record.get("_composite_score", 0.0),
            "read_count": self._to_float(metrics.get("read_count")),
            "like_count": self._to_float(metrics.get("like_count")),
            "comment_count": self._to_float(metrics.get("comment_count")),
            "share_count": self._to_float(metrics.get("share_count")),
            "impression_count": self._to_float(metrics.get("impression_count")),
            "topic_type": str(tags.get("topic_bucket") or record.get("topic_type") or ""),
            "cta_style": str(tags.get("cta_style") or ""),
            "title_style": str(tags.get("title_style") or ""),
        }

    def _analyze_title_patterns(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """统计标题模式：含数字 / 含公司名 的比例与示例。"""
        total = len(records)
        with_digit = 0
        with_company = 0
        digit_examples: list[str] = []
        company_examples: list[str] = []
        for record in records:
            title = str(record.get("title", ""))
            if DIGIT_RE.search(title):
                with_digit += 1
                if len(digit_examples) < 3:
                    digit_examples.append(title)
            if COMPANY_NAME_RE.search(title):
                with_company += 1
                if len(company_examples) < 3:
                    company_examples.append(title)
        return {
            "total": total,
            "with_digit_count": with_digit,
            "with_digit_ratio": round(with_digit / total, 2) if total else 0.0,
            "digit_examples": digit_examples,
            "with_company_count": with_company,
            "with_company_ratio": round(with_company / total, 2) if total else 0.0,
            "company_examples": company_examples,
        }

    def _analyze_topic_type_distribution(self, records: list[dict[str, Any]]) -> dict[str, int]:
        """统计 topic_type（topic_bucket）字段分布。"""
        counter: dict[str, int] = {}
        for record in records:
            tags = record.get("experiment_tags") or {}
            topic_type = ""
            if isinstance(tags, dict):
                topic_type = str(tags.get("topic_bucket") or tags.get("topic_type") or "").strip()
            if not topic_type:
                topic_type = str(record.get("topic_type") or "").strip()
            if not topic_type:
                topic_type = "未标注"
            counter[topic_type] = counter.get(topic_type, 0) + 1
        return dict(sorted(counter.items(), key=lambda pair: pair[1], reverse=True))

    def _analyze_cta_templates(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """检测 4 种互动话术模板（站队式/变量追问式/反事实假设式/经验补充式）。

        优先取 experiment_tags.cta_style；若未标注则对标题做关键词匹配兜底。
        """
        distribution: dict[str, int] = {name: 0 for name in CTA_TEMPLATES}
        unmatched = 0
        for record in records:
            tags = record.get("experiment_tags") or {}
            cta_style = ""
            if isinstance(tags, dict):
                cta_style = str(tags.get("cta_style") or "").strip()
            matched = self._match_cta_template(cta_style, str(record.get("title", "")))
            if matched:
                distribution[matched] += 1
            else:
                unmatched += 1
        return {
            "distribution": distribution,
            "unmatched_count": unmatched,
            "total": len(records),
        }

    def _match_cta_template(self, cta_style: str, title: str) -> str | None:
        """匹配记录所属的互动话术模板，未命中返回 None。"""
        # 优先按 cta_style 字段精确匹配
        normalized_style = cta_style.strip()
        for name, keywords in CTA_TEMPLATES.items():
            if normalized_style == name or normalized_style in keywords:
                return name
        # 兜底：在标题里查找关键词
        combined = f"{title} {cta_style}"
        for name, keywords in CTA_TEMPLATES.items():
            if any(keyword in combined for keyword in keywords):
                return name
        return None

    def _analyze_depth_features(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """统计深度要素：数据点数量（数字出现次数）与对立观点数量。"""
        data_point_counts: list[int] = []
        opposition_counts: list[int] = []
        for record in records:
            title = str(record.get("title", ""))
            article = str(record.get("article") or record.get("content") or record.get("body") or "")
            combined = f"{title}\n{article}"
            data_point_counts.append(len(DIGIT_RE.findall(combined)))
            opposition_counts.append(sum(1 for keyword in OPPOSITION_KEYWORDS if keyword in combined))
        total = len(records)
        return {
            "avg_data_points": round(sum(data_point_counts) / total, 2) if total else 0.0,
            "avg_opposition_keywords": round(sum(opposition_counts) / total, 2) if total else 0.0,
            "data_point_distribution": data_point_counts,
            "opposition_distribution": opposition_counts,
        }

    # ------------------------------------------------------------------
    # BOTTOM5 共性问题分析（SubTask 8.6）
    # ------------------------------------------------------------------

    def _analyze_bottom_performers(self, bottom_records: list[dict[str, Any]]) -> dict[str, Any]:
        """分析 BOTTOM5 共性问题：标题模板化、段落重复、缺少数据锚定。"""
        return {
            "records": [self._summarize_record(item) for item in bottom_records],
            "templated_titles": self._detect_templated_titles(bottom_records),
            "paragraph_repetition": self._detect_paragraph_repetition(bottom_records),
            "missing_data_anchor": self._detect_missing_data_anchor(bottom_records),
        }

    def _detect_templated_titles(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """检测标题是否匹配常见模板句式。"""
        matched_records: list[dict[str, Any]] = []
        for record in records:
            title = str(record.get("title", ""))
            matched_patterns = [
                pattern.pattern for pattern in TITLE_TEMPLATE_PATTERNS if pattern.search(title)
            ]
            if matched_patterns:
                matched_records.append({
                    "title": title,
                    "matched_patterns": matched_patterns,
                })
        return {
            "matched_count": len(matched_records),
            "total": len(records),
            "matched_ratio": round(len(matched_records) / len(records), 2) if records else 0.0,
            "examples": matched_records[:3],
        }

    def _detect_paragraph_repetition(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """检查 quality_check 字段中的重复率指标。

        账本记录若内嵌 ``quality_check`` 字段，则取其中的重复率指标；
        否则标记为 ``not_available``。
        """
        repetition_reports: list[dict[str, Any]] = []
        for record in records:
            quality_check = record.get("quality_check") or {}
            if not isinstance(quality_check, dict):
                quality_check = {}
            repetition_rate = quality_check.get("repetition_rate")
            if repetition_rate is None:
                repetition_rate = quality_check.get("duplicate_rate")
            if repetition_rate is None:
                repetition_reports.append({
                    "title": str(record.get("title", "")),
                    "status": "not_available",
                })
                continue
            try:
                rate = float(repetition_rate)
            except (TypeError, ValueError):
                rate = 0.0
            repetition_reports.append({
                "title": str(record.get("title", "")),
                "repetition_rate": round(rate, 4),
                "is_high": rate >= 0.3,
            })
        available = [item for item in repetition_reports if item.get("status") != "not_available"]
        high_count = sum(1 for item in available if item.get("is_high"))
        return {
            "available_count": len(available),
            "high_repetition_count": high_count,
            "reports": repetition_reports[:5],
        }

    def _detect_missing_data_anchor(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """检测缺少数据锚定（数字出现次数 < 3）的记录。"""
        DATA_ANCHOR_THRESHOLD = 3
        missing_records: list[dict[str, Any]] = []
        for record in records:
            title = str(record.get("title", ""))
            article = str(record.get("article") or record.get("content") or record.get("body") or "")
            combined = f"{title}\n{article}"
            count = len(DIGIT_RE.findall(combined))
            if count < DATA_ANCHOR_THRESHOLD:
                missing_records.append({
                    "title": title,
                    "data_point_count": count,
                })
        return {
            "missing_count": len(missing_records),
            "total": len(records),
            "missing_ratio": round(len(missing_records) / len(records), 2) if records else 0.0,
            "threshold": DATA_ANCHOR_THRESHOLD,
            "examples": missing_records[:3],
        }

    # ------------------------------------------------------------------
    # 选题命中率（SubTask 8.7）
    # ------------------------------------------------------------------

    def _compute_topic_hit_rate(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """按 topic_type / industry 分组计算平均阅读量。"""
        by_topic_type = self._group_average(
            records,
            key_extractor=self._extract_topic_type,
            value_extractor=lambda r: r.get("_views", 0.0),
        )
        by_industry = self._group_average(
            records,
            key_extractor=self._extract_industry,
            value_extractor=lambda r: r.get("_views", 0.0),
        )
        return {
            "by_topic_type": by_topic_type,
            "by_industry": by_industry,
        }

    def _extract_topic_type(self, record: dict[str, Any]) -> str:
        tags = record.get("experiment_tags") or {}
        if isinstance(tags, dict):
            value = str(tags.get("topic_bucket") or tags.get("topic_type") or "").strip()
            if value:
                return value
        return str(record.get("topic_type") or "未标注").strip() or "未标注"

    def _extract_industry(self, record: dict[str, Any]) -> str:
        record_industry = str(record.get("industry") or "").strip()
        if record_industry:
            return record_industry
        tags = record.get("experiment_tags") or {}
        if isinstance(tags, dict):
            value = str(tags.get("industry") or tags.get("focus_industry") or "").strip()
            if value:
                return value
        account_key = str(record.get("account_key") or "").strip()
        return account_key or "未标注"

    def _group_average(
        self,
        records: list[dict[str, Any]],
        key_extractor,
        value_extractor,
    ) -> dict[str, Any]:
        """按 key_extractor 分组，计算每组的平均值、记录数、TOP 标题。"""
        groups: dict[str, list[float]] = {}
        group_titles: dict[str, list[str]] = {}
        for record in records:
            key = key_extractor(record)
            value = self._to_float(value_extractor(record))
            groups.setdefault(key, []).append(value)
            group_titles.setdefault(key, []).append(str(record.get("title", "")))
        result: dict[str, Any] = {}
        for key, values in sorted(
            groups.items(), key=lambda pair: sum(pair[1]) / len(pair[1]) if pair[1] else 0, reverse=True
        ):
            avg = sum(values) / len(values) if values else 0.0
            result[key] = {
                "count": len(values),
                "avg_read_count": round(avg, 2),
                "total_read_count": round(sum(values), 2),
                "sample_titles": group_titles[key][:3],
            }
        return result

    # ------------------------------------------------------------------
    # 发布时段表现（SubTask 8.8）
    # ------------------------------------------------------------------

    def _compute_time_slot_performance(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """按 cron 时段分组计算平均展现/阅读/互动率。"""
        slot_stats: dict[str, dict[str, list[float]]] = {
            slot_key: {"impression": [], "read": [], "interaction_rate": []}
            for slot_key in TIME_SLOTS
        }
        slot_stats["other"] = {"impression": [], "read": [], "interaction_rate": []}

        for record in records:
            slot_key = self._classify_time_slot(record)
            metrics = record.get("metrics_latest") or {}
            if not isinstance(metrics, dict):
                metrics = {}
            impression = self._to_float(metrics.get("impression_count"))
            read = self._to_float(metrics.get("read_count") or record.get("_views", 0.0))
            likes = self._to_float(metrics.get("like_count"))
            comments = self._to_float(metrics.get("comment_count"))
            shares = self._to_float(metrics.get("share_count"))
            interaction_rate = (
                (likes + comments + shares) / read if read > 0 else 0.0
            )
            slot_stats[slot_key]["impression"].append(impression)
            slot_stats[slot_key]["read"].append(read)
            slot_stats[slot_key]["interaction_rate"].append(interaction_rate)

        result: dict[str, Any] = {}
        for slot_key, stats in slot_stats.items():
            count = len(stats["read"])
            if count == 0:
                result[slot_key] = {
                    "label": TIME_SLOTS.get(slot_key, {}).get("label", "其他时段"),
                    "count": 0,
                    "avg_impression": 0.0,
                    "avg_read_count": 0.0,
                    "avg_interaction_rate": 0.0,
                }
                continue
            result[slot_key] = {
                "label": TIME_SLOTS.get(slot_key, {}).get("label", "其他时段"),
                "count": count,
                "avg_impression": round(sum(stats["impression"]) / count, 2),
                "avg_read_count": round(sum(stats["read"]) / count, 2),
                "avg_interaction_rate": round(sum(stats["interaction_rate"]) / count, 4),
            }
        return result

    def _classify_time_slot(self, record: dict[str, Any]) -> str:
        """根据 published_at 将记录归入最近的 cron 时段。

        匹配规则：发布时间的「是否周末」与「小时差」需同时满足。
        小时差 <= ``TIME_SLOT_TOLERANCE_HOURS`` 才归入对应时段，否则归入 ``other``。
        """
        published_at = self._parse_datetime(record.get("published_at") or record.get("timestamp"))
        if published_at is None:
            return "other"
        is_weekend = published_at.weekday() >= 5  # 5=周六, 6=周日
        best_slot = "other"
        best_diff = None
        for slot_key, slot in TIME_SLOTS.items():
            if slot["is_weekend"] != is_weekend:
                continue
            slot_minutes = slot["hour"] * 60 + slot["minute"]
            record_minutes = published_at.hour * 60 + published_at.minute
            diff = abs(record_minutes - slot_minutes)
            # 跨午夜容差：超过 12 小时则按 24 小时折算
            if diff > 720:
                diff = 1440 - diff
            if diff > TIME_SLOT_TOLERANCE_HOURS * 60:
                continue
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_slot = slot_key
        return best_slot

    # ------------------------------------------------------------------
    # 报告输出（SubTask 8.9）
    # ------------------------------------------------------------------

    def _write_report(self, payload: dict[str, Any]) -> Path:
        """将回顾报告写入 JSON 文件，返回报告路径。"""
        report_path = self._report_path()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path

    # ------------------------------------------------------------------
    # 数据驱动迭代建议（SubTask 9.1 - 9.6）
    # ------------------------------------------------------------------

    def _generate_suggestions(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """基于回顾报告生成数据驱动的迭代建议。

        支持 4 类建议：
            1. ``title_template`` - 标题模板（含数字 vs 不含数字）
            2. ``topic_priority`` - 话题优先级（某 topic_type 平均阅读量显著领先）
            3. ``engagement_template_weight`` - 互动话术权重（TOP5 集中使用某类话术）
            4. ``publish_time_slot`` - 发布时段（某时段互动率显著更高）

        数据不足时跳过对应建议类型，不抛异常。

        Args:
            report: ``run()`` 方法生成的回顾报告 dict。

        Returns:
            建议列表，每条建议是含 ``category`` / ``current_value`` /
            ``suggested_value`` / ``evidence`` / ``confidence`` 字段的 dict。
        """
        suggestions: list[dict[str, Any]] = []

        for generator in (
            self._suggest_title_template,
            self._suggest_topic_priority,
            self._suggest_engagement_template_weight,
            self._suggest_publish_time_slot,
        ):
            try:
                suggestion = generator(report)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                self.logger.debug(
                    "%s 生成建议失败 (%s): %s",
                    self.agent_name,
                    generator.__name__,
                    exc,
                )
                suggestion = None
            if suggestion:
                suggestions.append(suggestion)

        self.logger.info(
            "%s 共生成 %d 条迭代建议",
            self.agent_name,
            len(suggestions),
        )
        return suggestions

    def _suggest_title_template(self, report: dict[str, Any]) -> dict[str, Any] | None:
        """生成标题模板建议：TOP5 含数字 vs BOTTOM5 不含数字。

        条件：TOP5 中 ≥3 篇含具体数字，BOTTOM5 中 ≥3 篇不含具体数字。
        """
        top_performers = report.get("top_performers") or {}
        bottom_performers = report.get("bottom_performers") or {}

        top_title_patterns = top_performers.get("title_patterns") or {}
        top_with_digit = int(top_title_patterns.get("with_digit_count") or 0)
        top_total = int(top_title_patterns.get("total") or 0)

        # BOTTOM5 没有预计算 title_patterns，需从 records 重新统计
        bottom_records = bottom_performers.get("records") or []
        bottom_without_digit = 0
        bottom_without_digit_examples: list[str] = []
        for record in bottom_records:
            title = str(record.get("title", ""))
            if not DIGIT_RE.search(title):
                bottom_without_digit += 1
                if len(bottom_without_digit_examples) < 3:
                    bottom_without_digit_examples.append(title)
        bottom_total = len(bottom_records)

        if top_with_digit < 3 or bottom_without_digit < 3:
            return None

        # 置信度：基于数据量计算（0.6-0.8）
        data_volume = min(top_total, bottom_total) / TOP_K if TOP_K else 0.0
        confidence = round(0.6 + 0.2 * data_volume, 2)
        confidence = min(0.8, max(0.6, confidence))

        top_digit_examples = list(top_title_patterns.get("digit_examples") or [])[:3]

        return {
            "category": "title_template",
            "current_value": (
                f"TOP5 中 {top_with_digit}/{top_total} 篇含数字；"
                f"BOTTOM5 中 {bottom_without_digit}/{bottom_total} 篇不含数字"
            ),
            "suggested_value": "建议使用含具体数字的标题模板（如『3 大趋势』『增长 47%』）",
            "evidence": (
                f"TOP5 含数字标题示例：{top_digit_examples}；"
                f"BOTTOM5 不含数字标题示例：{bottom_without_digit_examples}"
            ),
            "confidence": confidence,
        }

    def _suggest_topic_priority(self, report: dict[str, Any]) -> dict[str, Any] | None:
        """生成话题优先级建议：某 topic_type 平均阅读量高于其他 2 倍以上。

        条件：在 ``topic_hit_rate.by_topic_type`` 中存在至少 2 个分组，
        且最高分组的平均阅读量 ≥ 其他分组平均值的 2 倍。
        """
        topic_hit_rate = report.get("topic_hit_rate") or {}
        by_topic_type = topic_hit_rate.get("by_topic_type") or {}
        if len(by_topic_type) < 2:
            return None

        # by_topic_type 已按 avg_read_count 倒序排列
        sorted_topics = list(by_topic_type.items())
        top_topic_key, top_topic_data = sorted_topics[0]
        other_topics = sorted_topics[1:]
        if not other_topics:
            return None

        top_avg = self._to_float(top_topic_data.get("avg_read_count"))
        other_avg_values = [self._to_float(t[1].get("avg_read_count")) for t in other_topics]
        other_avg = sum(other_avg_values) / len(other_avg_values) if other_avg_values else 0.0

        if other_avg <= 0 or top_avg <= 0:
            return None
        ratio = top_avg / other_avg
        if ratio < 2:
            return None

        # 置信度：基于差距大小计算，差距越大置信度越高
        confidence = min(0.9, round(0.5 + 0.1 * ratio, 2))
        confidence = max(0.6, confidence)

        top_count = int(top_topic_data.get("count") or 0)
        sample_titles = list(top_topic_data.get("sample_titles") or [])[:3]

        return {
            "category": "topic_priority",
            "current_value": f"topic_type={top_topic_key}（样本数={top_count}）当前未明确提升优先级",
            "suggested_value": f"建议提升 topic_type={top_topic_key} 的内容产出优先级",
            "evidence": (
                f"topic_type={top_topic_key} 平均阅读量={top_avg}（n={top_count}），"
                f"其他 topic_type 平均={round(other_avg, 2)}，"
                f"差距倍数={round(ratio, 2)}x；示例标题：{sample_titles}"
            ),
            "confidence": confidence,
        }

    def _suggest_engagement_template_weight(
        self, report: dict[str, Any]
    ) -> dict[str, Any] | None:
        """生成互动话术权重建议。

        条件：TOP5 中 ≥3 篇使用同一种互动话术，且 BOTTOM5 中 ≥3 篇使用
        通用话术（无匹配）或无互动话术。
        """
        top_performers = report.get("top_performers") or {}
        bottom_performers = report.get("bottom_performers") or {}

        top_cta = top_performers.get("cta_template_distribution") or {}
        top_distribution = top_cta.get("distribution") or {}
        if not top_distribution:
            return None

        top_cta_name, top_cta_count = max(
            top_distribution.items(), key=lambda pair: pair[1]
        )
        if top_cta_count < 3:
            return None

        # BOTTOM5 需从 records 重新匹配话术类型
        bottom_records = bottom_performers.get("records") or []
        bottom_generic_count = 0
        for record in bottom_records:
            cta_style = str(record.get("cta_style") or "").strip()
            matched = self._match_cta_template(cta_style, str(record.get("title", "")))
            if not matched:
                bottom_generic_count += 1

        if bottom_generic_count < 3:
            return None

        # 置信度：基于集中度计算
        top_total = int(top_cta.get("total") or 0) or 1
        top_concentration = top_cta_count / top_total
        confidence = min(0.85, round(0.5 + 0.3 * top_concentration, 2))
        confidence = max(0.6, confidence)

        return {
            "category": "engagement_template_weight",
            "current_value": f"{top_cta_name}:0.25（默认权重）",
            "suggested_value": f"{top_cta_name}:0.5",
            "evidence": (
                f"TOP5 中 {top_cta_count}/{top_total} 篇使用『{top_cta_name}』话术；"
                f"BOTTOM5 中 {bottom_generic_count}/{len(bottom_records)} 篇使用通用话术或无互动话术"
            ),
            "confidence": confidence,
        }

    def _suggest_publish_time_slot(self, report: dict[str, Any]) -> dict[str, Any] | None:
        """生成发布时段建议：某时段平均互动率显著高于其他（≥1.5 倍）。"""
        time_slot_performance = report.get("time_slot_performance") or {}
        if len(time_slot_performance) < 2:
            return None

        # 仅统计有样本的时段
        valid_slots = [
            (key, data)
            for key, data in time_slot_performance.items()
            if int(data.get("count") or 0) > 0
        ]
        if len(valid_slots) < 2:
            return None

        sorted_slots = sorted(
            valid_slots,
            key=lambda pair: self._to_float(pair[1].get("avg_interaction_rate")),
            reverse=True,
        )
        top_slot_key, top_slot_data = sorted_slots[0]
        other_slots = sorted_slots[1:]

        top_avg = self._to_float(top_slot_data.get("avg_interaction_rate"))
        other_avg_values = [
            self._to_float(s[1].get("avg_interaction_rate")) for s in other_slots
        ]
        other_avg = (
            sum(other_avg_values) / len(other_avg_values) if other_avg_values else 0.0
        )

        if other_avg <= 0 or top_avg <= 0:
            return None
        ratio = top_avg / other_avg
        if ratio < 1.5:
            return None

        # 置信度：基于差距计算
        confidence = min(0.9, round(0.5 + 0.1 * ratio, 2))
        confidence = max(0.6, confidence)

        top_label = top_slot_data.get("label", top_slot_key)
        top_count = int(top_slot_data.get("count") or 0)

        return {
            "category": "publish_time_slot",
            "current_value": f"当前各时段发布频次相对均衡，{top_label} 时段表现突出",
            "suggested_value": f"建议增加 {top_label} 时段的发布频次",
            "evidence": (
                f"{top_label} 时段平均互动率={top_avg}（n={top_count}），"
                f"其他时段平均={round(other_avg, 4)}，"
                f"差距倍数={round(ratio, 2)}x"
            ),
            "confidence": confidence,
        }

    # ------------------------------------------------------------------
    # 建议输出（SubTask 9.7）
    # ------------------------------------------------------------------

    def _suggestions_path(self) -> Path:
        """返回 iteration_suggestions.json 输出路径（与 monthly_review_report.json 同目录）。"""
        return self._report_path().parent / SUGGESTIONS_FILENAME

    def _write_suggestions(self, suggestions: list[dict[str, Any]]) -> Path:
        """将迭代建议列表写入 JSON 文件，返回路径。"""
        suggestions_path = self._suggestions_path()
        suggestions_path.parent.mkdir(parents=True, exist_ok=True)
        suggestions_path.write_text(
            json.dumps(suggestions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return suggestions_path


# ------------------------------------------------------------------
# 命令行入口
# ------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MonthlyReviewAgent - 月度回顾分析（生成近 30 天内容回顾报告）"
    )
    parser.add_argument(
        "--account",
        default="default",
        help="账号标识（默认: default），用于隔离 .runtime/accounts/{account}/ 输出目录",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="日志级别（默认: INFO）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    agent = MonthlyReviewAgent(account=args.account)
    result = agent.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    status = str(result.get("status", ""))
    return 0 if status in {"ok", "insufficient_data", "missing_ledger", "empty_ledger"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
