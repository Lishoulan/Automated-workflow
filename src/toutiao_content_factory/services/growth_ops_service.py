from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.t1_policy import infer_availability_status


class GrowthOpsService:
    BENCHMARK_PLATFORMS = ("toutiao", "wechat")
    BENCHMARK_TEMPLATE = """# 公众号对标库
# 用法：
# 1. 每个账号填一条 accounts 记录
# 2. observation_articles 里填你最近观察到的文章
# 3. 有数据就填 reads / likes / shares / comments / followers_estimate
# 4. 运行 `python .\\main.py --build-benchmark-report` 生成对标报告

version: 1
updated_at: ""
accounts: []
"""

    SELF_BENCHMARK_TEMPLATE = """# 自有高表现样本库
# 用法：
# 1. articles 里记录你自己已经跑出来的高表现文章
# 2. enabled: true 表示真实有效样本；enabled: false 表示模板占位，不参与策略学习
# 3. status: template / draft / pending_metrics 也会被视为占位样本
# 4. observed_metrics 至少补 impression_count / read_count
# 5. inferred_patterns 写你确认有效的写法线索
# 6. 运行 `python .\\main.py --build-benchmark-report` 重新生成增长策略

version: 1
updated_at: ""
articles:
  - key: japan_airport_robot
    enabled: true
    track: "AI机器人"
    account_key: default
    platform: toutiao
    published_at: "2026-05-16"
    title: "当日本工业机器人连续衰退，中国人形机器人却在东京机场撕开缺口"
    run_dir: "output/runs/20260516-112815_曾经全球第一的日本机器人-迎来-黄昏时刻"
    observed_metrics:
      impression_count: 100000
      read_count: 2000
    keywords:
      - "日本机器人"
      - "东京机场"
      - "人形机器人"
      - "产业链利润迁移"
    notes:
      - "强场景锚点：日本机场 / 东京 / 羽田"
      - "冲突感强：日本下滑 vs 中国切入"
      - "产业链利润迁移明确"
      - "无图片也有高展现，说明标题与开头更关键"
    inferred_patterns:
      - "场景先行开头"
      - "跨国对比冲突"
      - "利润迁移框架"
      - "具体数字配具体主体"
      - "结尾保留跟踪变量"
  - key: robot_notes_template
    enabled: false
    status: template
    track: "国际热点"
    account_key: robot_notes
    platform: toutiao
    published_at: ""
    title: "国际账号样本占位"
    run_dir: ""
    observed_metrics:
      impression_count: 0
      read_count: 0
    keywords:
      - "国际"
      - "关税"
      - "中东"
      - "外交"
    notes:
      - "先放真实跑出来的高表现稿件，再把这条改成 enabled: true"
      - "重点观察标题冲突感、事件主体、时间窗口和中国关联度"
    inferred_patterns:
      - "结论先行开头"
      - "跨国对比冲突"
      - "结尾保留跟踪变量"
  - key: energy_notes_template
    enabled: false
    status: template
    track: "体育赛事"
    account_key: energy_notes
    platform: toutiao
    published_at: ""
    title: "体育账号样本占位"
    run_dir: ""
    observed_metrics:
      impression_count: 0
      read_count: 0
    keywords:
      - "足球"
      - "篮球"
      - "比赛"
      - "联赛"
    notes:
      - "先收集真实爆款再启用，不要把空模板纳入策略学习"
      - "重点观察趋势判断、事后复盘、关键人物和争议节点"
    inferred_patterns:
      - "结果先行开头"
      - "具体数字配具体主体"
      - "争议或转折节点"
"""

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.root_dir = settings.output_dir / "growth_ops"
        self.performance_dir = self.root_dir / "performance"
        self.benchmark_source_path = settings.project_root / "branding" / "benchmark_accounts.yaml"
        self.benchmark_source_paths = {
            "toutiao": settings.project_root / "branding" / "benchmark_accounts_toutiao.yaml",
            "wechat": settings.project_root / "branding" / "benchmark_accounts_wechat.yaml",
        }
        self.self_benchmark_source_path = settings.project_root / "branding" / "self_benchmark_articles.yaml"
        self.benchmark_report_path = self.root_dir / "benchmark_report.md"
        self.benchmark_json_path = self.root_dir / "benchmark_report.json"
        self.self_benchmark_report_path = self.root_dir / "self_benchmark_report.md"
        self.self_benchmark_json_path = self.root_dir / "self_benchmark_report.json"
        self.title_playbook_path = self.root_dir / "title_playbook.md"
        self.title_playbook_json_path = self.root_dir / "title_playbook.json"
        self.performance_ledger_path = self.performance_dir / "performance_ledger.json"
        self.performance_csv_path = self.performance_dir / "performance_ledger.csv"
        self.performance_report_path = self.performance_dir / "performance_report.md"
        self.performance_report_json_path = self.performance_dir / "performance_report.json"
        self.self_benchmark_collection_path = self.root_dir / "self_benchmark_collection.md"
        self.self_benchmark_collection_json_path = self.root_dir / "self_benchmark_collection.json"

    def ensure_directories(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.performance_dir.mkdir(parents=True, exist_ok=True)
        self.benchmark_source_path.parent.mkdir(parents=True, exist_ok=True)
        self.self_benchmark_source_path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_benchmark_template(self) -> Path:
        self.ensure_directories()
        if not self.benchmark_source_path.exists():
            self.benchmark_source_path.write_text(self.BENCHMARK_TEMPLATE, encoding="utf-8")
        return self.benchmark_source_path

    def ensure_self_benchmark_template(self) -> Path:
        self.ensure_directories()
        if not self.self_benchmark_source_path.exists():
            self.self_benchmark_source_path.write_text(self.SELF_BENCHMARK_TEMPLATE, encoding="utf-8")
        return self.self_benchmark_source_path

    def resolve_benchmark_platform(self, platform: str | None = None) -> str:
        raw_value = str(platform or self.settings.platform or "").strip().lower()
        if raw_value in {"wechat", "公众号", "official_account", "weixin"}:
            return "wechat"
        return "toutiao"

    def benchmark_source_path_for_platform(self, platform: str | None = None) -> Path:
        platform_key = self.resolve_benchmark_platform(platform)
        return self.benchmark_source_paths.get(platform_key, self.benchmark_source_path)

    def benchmark_report_paths_for_platform(self, platform: str | None = None) -> tuple[Path, Path]:
        platform_key = self.resolve_benchmark_platform(platform)
        return (
            self.root_dir / f"benchmark_report_{platform_key}.md",
            self.root_dir / f"benchmark_report_{platform_key}.json",
        )

    def title_playbook_paths_for_platform(self, platform: str | None = None) -> tuple[Path, Path]:
        platform_key = self.resolve_benchmark_platform(platform)
        return (
            self.root_dir / f"title_playbook_{platform_key}.md",
            self.root_dir / f"title_playbook_{platform_key}.json",
        )

    def ensure_benchmark_template_for_platform(self, platform: str | None = None) -> Path:
        source_path = self.benchmark_source_path_for_platform(platform)
        self.ensure_directories()
        if not source_path.exists():
            source_path.write_text(self.BENCHMARK_TEMPLATE, encoding="utf-8")
        return source_path

    def load_benchmark_accounts(self, platform: str | None = None) -> list[dict[str, Any]]:
        platform_key = self.resolve_benchmark_platform(platform)
        source_path = self.benchmark_source_path_for_platform(platform_key)
        if source_path.exists():
            try:
                payload = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                payload = {}
            accounts = payload.get("accounts", [])
            if isinstance(accounts, list):
                return [item for item in accounts if isinstance(item, dict)]

        legacy_path = self.ensure_benchmark_template()
        try:
            payload = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            payload = {}
        accounts = payload.get("accounts", [])
        if not isinstance(accounts, list):
            return []
        filtered = [
            item
            for item in accounts
            if isinstance(item, dict)
            and self.resolve_benchmark_platform(str(item.get("platform", "")).strip() or platform_key) == platform_key
        ]
        return filtered or [item for item in accounts if isinstance(item, dict)]

    def build_benchmark_report(self) -> dict[str, Path]:
        self_benchmark_source = self.ensure_self_benchmark_template()
        self_benchmark_library = self.load_self_benchmark_library()
        self_benchmark_payload = self._build_self_benchmark_report_payload(
            source_path=self_benchmark_source,
            articles=self_benchmark_library,
        )
        self.self_benchmark_json_path.write_text(
            json.dumps(self_benchmark_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.self_benchmark_report_path.write_text(
            self._build_self_benchmark_markdown(self_benchmark_payload),
            encoding="utf-8",
        )
        exported: dict[str, Path] = {
            "self_benchmark_source": self_benchmark_source,
            "self_benchmark_report": self.self_benchmark_report_path,
            "self_benchmark_report_json": self.self_benchmark_json_path,
        }

        current_platform = self.resolve_benchmark_platform()
        for platform_key in self.BENCHMARK_PLATFORMS:
            source_path = self.ensure_benchmark_template_for_platform(platform_key)
            accounts = self.load_benchmark_accounts(platform_key)

            normalized_accounts: list[dict[str, Any]] = []
            article_rows: list[dict[str, Any]] = []
            title_style_counter: Counter[str] = Counter()
            hook_counter: Counter[str] = Counter()
            keyword_counter: Counter[str] = Counter()
            track_counter: Counter[str] = Counter()

            for account in accounts:
                if not isinstance(account, dict):
                    continue
                normalized = self._normalize_benchmark_account(account)
                normalized_accounts.append(normalized)
                if normalized["track"]:
                    track_counter[normalized["track"]] += 1
                for article in normalized["observation_articles"]:
                    article_rows.append(article)
                    title_style_counter[str(article.get("title_style", ""))] += 1
                    hook_counter[str(article.get("hook_pattern", ""))] += 1
                    for keyword in article.get("keywords", []):
                        keyword_counter[str(keyword)] += 1

            summary = {
                "platform": platform_key,
                "account_count": len(normalized_accounts),
                "article_count": len(article_rows),
                "track_distribution": dict(track_counter.most_common()),
                "top_title_styles": dict(title_style_counter.most_common(8)),
                "top_hook_patterns": dict(hook_counter.most_common(8)),
                "top_keywords": dict(keyword_counter.most_common(12)),
            }

            report_payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "platform": platform_key,
                "source_path": str(source_path),
                "summary": summary,
                "accounts": normalized_accounts,
            }
            platform_report_path, platform_report_json_path = self.benchmark_report_paths_for_platform(platform_key)
            platform_report_json_path.write_text(
                json.dumps(report_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            platform_report_path.write_text(
                self._build_benchmark_markdown(report_payload),
                encoding="utf-8",
            )

            title_playbook_payload = self._build_title_playbook_payload(normalized_accounts, self_benchmark_library)
            title_playbook_payload["platform"] = platform_key
            title_playbook_payload["source_path"] = str(source_path)
            platform_title_playbook_path, platform_title_playbook_json_path = self.title_playbook_paths_for_platform(platform_key)
            platform_title_playbook_json_path.write_text(
                json.dumps(title_playbook_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            platform_title_playbook_path.write_text(
                self._build_title_playbook_markdown(title_playbook_payload),
                encoding="utf-8",
            )

            exported[f"benchmark_source_{platform_key}"] = source_path
            exported[f"benchmark_report_{platform_key}"] = platform_report_path
            exported[f"benchmark_report_{platform_key}_json"] = platform_report_json_path
            exported[f"title_playbook_{platform_key}"] = platform_title_playbook_path
            exported[f"title_playbook_{platform_key}_json"] = platform_title_playbook_json_path

            if platform_key == current_platform:
                self.benchmark_json_path.write_text(
                    json.dumps(report_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.benchmark_report_path.write_text(
                    self._build_benchmark_markdown(report_payload),
                    encoding="utf-8",
                )
                self.title_playbook_json_path.write_text(
                    json.dumps(title_playbook_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.title_playbook_path.write_text(
                    self._build_title_playbook_markdown(title_playbook_payload),
                    encoding="utf-8",
                )

        exported["benchmark_report"] = self.benchmark_report_path
        exported["benchmark_report_json"] = self.benchmark_json_path
        exported["title_playbook"] = self.title_playbook_path
        exported["title_playbook_json"] = self.title_playbook_json_path
        return exported

    def load_self_benchmark_library(self) -> list[dict[str, Any]]:
        source_path = self.ensure_self_benchmark_template()
        try:
            payload = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            payload = {}

        manual_articles = payload.get("articles", [])
        normalized_articles: list[dict[str, Any]] = []
        for item in manual_articles if isinstance(manual_articles, list) else []:
            if not isinstance(item, dict):
                continue
            if not self._is_active_self_benchmark_entry(item):
                continue
            normalized = self._normalize_self_benchmark_article(item, source_type="manual")
            if normalized:
                normalized_articles.append(normalized)

        normalized_articles.extend(self._load_auto_self_benchmark_hits())
        deduped: dict[str, dict[str, Any]] = {}
        for article in normalized_articles:
            dedupe_key = str(article.get("run_dir", "")).strip() or str(article.get("title", "")).strip()
            if not dedupe_key:
                continue
            existing = deduped.get(dedupe_key)
            if existing is None or int(article.get("benchmark_score", 0)) > int(existing.get("benchmark_score", 0)):
                deduped[dedupe_key] = article

        return sorted(
            deduped.values(),
            key=lambda item: (
                int(item.get("benchmark_score", 0)),
                int(item.get("read_count", 0)),
                int(item.get("impression_count", 0)),
                str(item.get("published_at", "")),
            ),
            reverse=True,
        )

    def get_self_benchmark_track_context(self, track: str) -> dict[str, Any]:
        requested_track = str(track).strip()
        resolved_track = self.infer_title_playbook_track(requested_track) if requested_track else ""
        library = [*self.load_self_benchmark_library(), *self._load_collected_reference_samples()]
        deduped_library: dict[str, dict[str, Any]] = {}
        for item in library:
            identity = self._self_benchmark_identity(item)
            if not identity:
                continue
            existing = deduped_library.get(identity)
            if existing is None or int(item.get("benchmark_score", 0) or 0) > int(existing.get("benchmark_score", 0) or 0):
                deduped_library[identity] = item
        filtered = [
            item
            for item in deduped_library.values()
            if str(item.get("track", "")).strip() == (resolved_track or requested_track)
        ]

        pattern_counter: Counter[str] = Counter()
        notes: list[str] = []
        top_hits: list[dict[str, Any]] = []
        concrete_hook_counter: Counter[str] = Counter()
        for item in filtered:
            for pattern in item.get("inferred_patterns", []):
                pattern_counter[str(pattern).strip()] += 1
            for hook in self._coerce_str_list(item.get("concrete_hooks", [])):
                concrete_hook_counter[str(hook).strip()] += 1
            notes.extend(str(note).strip() for note in item.get("notes", []) if str(note).strip())
            top_hits.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "published_at": str(item.get("published_at", "")).strip(),
                    "platform": str(item.get("platform", "")).strip(),
                    "account_key": str(item.get("account_key", "")).strip(),
                    "run_dir": str(item.get("run_dir", "")).strip(),
                    "external_url": str(item.get("external_url", "")).strip(),
                    "benchmark_score": int(item.get("benchmark_score", 0) or 0),
                    "impression_count": int(item.get("impression_count", 0) or 0),
                    "read_count": int(item.get("read_count", 0) or 0),
                    "read_rate": float(item.get("read_rate", 0) or 0),
                }
            )

        return {
            "track": resolved_track or requested_track or "科技商业",
            "titles": [str(item.get("title", "")).strip() for item in filtered if str(item.get("title", "")).strip()][:5],
            "patterns": [name for name, _count in pattern_counter.most_common(6)],
            "concrete_hooks": [name for name, _count in concrete_hook_counter.most_common(8)],
            "notes": self._unique_texts(notes)[:5],
            "hits": top_hits[:3],
            "summary": {
                "count": len(filtered),
                "manual_count": sum(1 for item in filtered if str(item.get("source_type", "")).strip() == "manual"),
                "auto_count": sum(1 for item in filtered if str(item.get("source_type", "")).strip() == "performance_ledger"),
                "online_count": sum(1 for item in filtered if str(item.get("source_type", "")).strip() == "online_reference"),
                "average_read_rate": self._safe_average(item.get("read_rate", 0) for item in filtered),
            },
        }

    def load_title_playbook(self, platform: str | None = None) -> dict[str, Any]:
        self.ensure_directories()
        _, platform_title_playbook_json_path = self.title_playbook_paths_for_platform(platform)
        target_path = platform_title_playbook_json_path
        if not target_path.exists() and self.title_playbook_json_path.exists():
            target_path = self.title_playbook_json_path
        if not target_path.exists():
            self.build_benchmark_report()
        if not target_path.exists():
            return {}
        try:
            payload = json.loads(target_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def infer_title_playbook_track(self, text: str) -> str:
        normalized = str(text or "")
        robot_tokens = ("机器人", "人形", "具身", "减速器", "灵巧手", "伺服", "关节电机")
        chip_tokens = ("芯片", "半导体", "封测", "晶圆", "EDA", "光刻", "代工", "算力", "光刻机")
        energy_tokens = ("储能", "光伏", "锂电", "电池", "逆变器", "PCS", "风电", "并网", "充电")
        ai_tokens = ("大模型", "LLM", "AI应用", "AI", "人工智能")
        industry_automation_tokens = ("智能制造", "工业自动化", "PLC", "MES", "数控", "工业互联网", "工控")
        international_tokens = ("国际", "美国", "欧洲", "俄罗斯", "乌克兰", "中东", "关税", "制裁", "外交", "局势", "油价", "黄金")
        sports_tokens = ("体育", "足球", "篮球", "国足", "男篮", "女篮", "欧冠", "英超", "西甲", "中超", "NBA", "世界杯", "奥运", "乒乓", "羽毛球")
        
        # 优先判断科技相关行业（优先级更高）
        if any(token in normalized for token in robot_tokens):
            return "AI机器人"
        if any(token in normalized for token in chip_tokens):
            return "半导体芯片"
        if any(token in normalized for token in energy_tokens):
            return "新能源储能"
        if any(token in normalized for token in ai_tokens):
            return "AI应用"
        if any(token in normalized for token in industry_automation_tokens):
            return "工业自动化"
        
        # 检查是否有任何科技相关词汇（优先级更高）
        tech_tokens = ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "AI", "大模型", "智能制造", "工业自动化", "人形", "具身", "减速器", "灵巧手", "伺服", "算力", "光刻机", "风电", "逆变器"]
        tech_tokens_count = sum(1 for token in tech_tokens if token in normalized)
        if tech_tokens_count > 0:
            return "科技商业"
        
        # 只有当没有科技相关词汇时，才判断为国际热点或体育赛事
        if any(token in normalized for token in international_tokens):
            return "国际热点"
        if any(token in normalized for token in sports_tokens):
            return "体育赛事"
        return "科技商业"

    def get_title_playbook_track_context(self, track: str, platform: str | None = None) -> dict[str, Any]:
        payload = self.load_title_playbook(platform=platform)
        tracks = [item for item in payload.get("tracks", []) if isinstance(item, dict)]
        requested_track = str(track).strip()
        normalized_track = self.infer_title_playbook_track(requested_track) if requested_track else ""
        selected = next((item for item in tracks if str(item.get("track", "")).strip() == requested_track), None)
        if selected is None and normalized_track:
            selected = next((item for item in tracks if str(item.get("track", "")).strip() == normalized_track), None)
        if selected is None:
            # 如果找不到，优先找科技商业
            selected = next((item for item in tracks if "科技" in str(item.get("track", ""))), None)
            if selected is None and tracks:
                # 最后才回退到第一个
                selected = tracks[0]
        selected = selected or {}
        resolved_track = (
            str(selected.get("track", normalized_track or requested_track)).strip()
            or normalized_track
            or requested_track
            or "科技商业"
        )
        self_benchmark_context = self.get_self_benchmark_track_context(resolved_track)
        example_titles = self._unique_texts(
            [
                *[str(item).strip() for item in selected.get("example_titles", []) if str(item).strip()],
                *[str(item).strip() for item in self_benchmark_context.get("titles", []) if str(item).strip()],
            ]
        )
        return {
            "track": resolved_track,
            "benchmark_accounts": [
                str(item).strip() for item in selected.get("benchmark_accounts", []) if str(item).strip()
            ],
            "top_keywords": [str(item).strip() for item in selected.get("top_keywords", []) if str(item).strip()],
            "title_templates": [str(item).strip() for item in selected.get("title_templates", []) if str(item).strip()],
            "topic_imitation_pool": [
                str(item).strip() for item in selected.get("topic_imitation_pool", []) if str(item).strip()
            ],
            "example_titles": example_titles,
            "self_benchmark_titles": [
                str(item).strip() for item in self_benchmark_context.get("titles", []) if str(item).strip()
            ],
            "self_benchmark_patterns": [
                str(item).strip() for item in self_benchmark_context.get("patterns", []) if str(item).strip()
            ],
            "self_benchmark_concrete_hooks": [
                str(item).strip() for item in self_benchmark_context.get("concrete_hooks", []) if str(item).strip()
            ],
            "self_benchmark_notes": [
                str(item).strip() for item in self_benchmark_context.get("notes", []) if str(item).strip()
            ],
            "self_benchmark_hits": list(self_benchmark_context.get("hits", [])),
        }

    def get_benchmark_strategy_context(self, track: str, platform: str | None = None) -> dict[str, Any]:
        playbook = self.get_title_playbook_track_context(track, platform=platform)
        resolved_track = str(playbook.get("track", track)).strip() or str(track).strip() or "科技商业"
        defaults = self._strategy_defaults_for_track(resolved_track)
        self_benchmark_context = self.get_self_benchmark_track_context(resolved_track)
        return {
            "track": resolved_track,
            "benchmark_accounts": [
                str(item).strip() for item in playbook.get("benchmark_accounts", []) if str(item).strip()
            ],
            "top_keywords": [str(item).strip() for item in playbook.get("top_keywords", []) if str(item).strip()],
            "title_templates": [str(item).strip() for item in playbook.get("title_templates", []) if str(item).strip()],
            "topic_imitation_pool": [
                str(item).strip() for item in playbook.get("topic_imitation_pool", []) if str(item).strip()
            ],
            "example_titles": [str(item).strip() for item in playbook.get("example_titles", []) if str(item).strip()],
            "voice_summary": str(defaults.get("voice_summary", "")).strip(),
            "reader_value": str(defaults.get("reader_value", "")).strip(),
            "opening_rules": [str(item).strip() for item in defaults.get("opening_rules", []) if str(item).strip()],
            "structure_rules": [str(item).strip() for item in defaults.get("structure_rules", []) if str(item).strip()],
            "angle_rules": [str(item).strip() for item in defaults.get("angle_rules", []) if str(item).strip()],
            "evidence_rules": [str(item).strip() for item in defaults.get("evidence_rules", []) if str(item).strip()],
            "review_focus": [str(item).strip() for item in defaults.get("review_focus", []) if str(item).strip()],
            "avoid_patterns": [str(item).strip() for item in defaults.get("avoid_patterns", []) if str(item).strip()],
            "self_benchmark_titles": [
                str(item).strip() for item in self_benchmark_context.get("titles", []) if str(item).strip()
            ],
            "self_benchmark_patterns": [
                str(item).strip() for item in self_benchmark_context.get("patterns", []) if str(item).strip()
            ],
            "self_benchmark_concrete_hooks": [
                str(item).strip() for item in self_benchmark_context.get("concrete_hooks", []) if str(item).strip()
            ],
            "self_benchmark_notes": [
                str(item).strip() for item in self_benchmark_context.get("notes", []) if str(item).strip()
            ],
            "self_benchmark_hits": list(self_benchmark_context.get("hits", [])),
            "self_benchmark_summary": dict(self_benchmark_context.get("summary", {})),
        }

    def build_benchmark_digest(self, track: str, purpose: str = "writer", platform: str | None = None) -> str:
        context = self.get_benchmark_strategy_context(track, platform=platform)
        benchmark_accounts = "、".join(context.get("benchmark_accounts", [])[:4]) or "当前赛道高质量分析号"
        top_keywords = "、".join(context.get("top_keywords", [])[:6]) or "暂无"
        lines = [
            f"对标赛道：{context.get('track', track)}",
            f"参考账号：{benchmark_accounts}",
            f"核心写法：{context.get('voice_summary', '')}",
            f"读者价值：{context.get('reader_value', '')}",
            f"高频关键词：{top_keywords}",
        ]
        self_titles = [str(item).strip() for item in context.get("self_benchmark_titles", []) if str(item).strip()]
        self_patterns = [str(item).strip() for item in context.get("self_benchmark_patterns", []) if str(item).strip()]
        self_notes = [str(item).strip() for item in context.get("self_benchmark_notes", []) if str(item).strip()]
        if self_titles:
            lines.extend(
                [
                    "你自己的已验证样本：",
                    *[f"- {item}" for item in self_titles[:2]],
                ]
            )
        if self_patterns:
            lines.extend(
                [
                    "内部复用线索：",
                    *[f"- {item}" for item in self_patterns[:4]],
                ]
            )
        self_hooks = [str(item).strip() for item in context.get("self_benchmark_concrete_hooks", []) if str(item).strip()]
        if self_hooks:
            lines.extend(
                [
                    "已验证具象钩子要素（优先复用）：",
                    *[f"- {item}" for item in self_hooks[:8]],
                ]
            )
        if self_notes:
            lines.append(f"内部提醒：{'；'.join(self_notes[:3])}")

        if purpose == "trend":
            lines.extend(
                [
                    "对标选题原则：",
                    *[f"- {item}" for item in context.get("angle_rules", [])[:4]],
                    "热点转化提醒：",
                    *[f"- {item}" for item in context.get("topic_imitation_pool", [])[:4]],
                ]
            )
        elif purpose == "data":
            lines.extend(
                [
                    "对标信息抽取重点：",
                    *[f"- {item}" for item in context.get("evidence_rules", [])[:4]],
                    "写作落点优先级：",
                    *[f"- {item}" for item in context.get("angle_rules", [])[:4]],
                ]
            )
        elif purpose == "review":
            lines.extend(
                [
                    "审核时优先检查：",
                    *[f"- {item}" for item in context.get("review_focus", [])[:5]],
                    "必须规避：",
                    *[f"- {item}" for item in context.get("avoid_patterns", [])[:5]],
                ]
            )
        else:
            lines.extend(
                [
                    "开头规则：",
                    *[f"- {item}" for item in context.get("opening_rules", [])[:4]],
                    "正文结构：",
                    *[f"- {item}" for item in context.get("structure_rules", [])[:5]],
                    "论证角度：",
                    *[f"- {item}" for item in context.get("angle_rules", [])[:4]],
                    "必须规避：",
                    *[f"- {item}" for item in context.get("avoid_patterns", [])[:5]],
                ]
            )

        examples = [str(item).strip() for item in context.get("example_titles", []) if str(item).strip()]
        if examples:
            lines.append("样本标题节奏参考：")
            lines.extend(f"- {item}" for item in examples[:3])
        return "\n".join(lines).strip()

    def score_topic_title_potential(
        self,
        *,
        title: str,
        summary: str = "",
        snippet: str = "",
        matched_keywords: list[str] | None = None,
        extracted_numbers: list[str] | None = None,
        matched_industries: list[str] | None = None,
    ) -> dict[str, Any]:
        combined = "\n".join(
            part for part in (str(title).strip(), str(summary).strip(), str(snippet).strip()) if str(part).strip()
        )
        keyword_list = [str(item).strip() for item in (matched_keywords or []) if str(item).strip()]
        number_list = [str(item).strip() for item in (extracted_numbers or []) if str(item).strip()]
        industry_list = [str(item).strip() for item in (matched_industries or []) if str(item).strip()]
        normalized_track = self.infer_title_playbook_track(
            " ".join([combined, *keyword_list, *industry_list])
        )
        track_context = self.get_title_playbook_track_context(normalized_track)
        title_style = self._infer_title_style(title)
        hook_pattern = self._infer_hook_pattern(title)
        notice_style_title = self._is_notice_style_headline(title, keyword_list)

        score = 42
        reasons: list[str] = []

        if title_style in {"contrast_hook", "data_hook", "question_hook"}:
            score += 8
            reasons.append("标题自带判断或冲突抓手")
        elif title_style == "insight_hook":
            score += 5
            reasons.append("标题适合改写成观点型分析题")

        if hook_pattern in {"curiosity_gap", "ask_reader", "emotion_trigger"}:
            score += 6
            reasons.append("具备吸引点击的标题钩子")

        if number_list or re.search(r"\d", combined):
            score += 8
            reasons.append("有数字抓手，容易起数据型标题")

        if any(token in combined for token in ("订单", "签约", "中标", "交付", "量产", "客户", "并网", "装机", "出货")):
            score += 10
            reasons.append("有订单或交付信号，适合写兑现逻辑")

        if any(token in combined for token in ("利润", "毛利", "回款", "议价", "份额", "红利", "价格战", "分化", "重估")):
            score += 10
            reasons.append("有利润分化线索，适合写判断型标题")

        if any(token in combined for token in ("机场", "工厂", "车间", "仓库", "电站", "电厂", "港口", "物流中心")):
            score += 7
            reasons.append("有明确场景锚点，标题更容易具体")

        if len(keyword_list) >= 2:
            score += 6
            reasons.append("关键词颗粒度足够，方便压缩成主副线")

        playbook_keywords = [str(item).strip() for item in track_context.get("top_keywords", []) if str(item).strip()]
        keyword_hits = sum(1 for item in playbook_keywords[:6] if item and item in combined)
        if keyword_hits >= 2:
            score += 6
            reasons.append("和赛道高频选题词重合度高")
        elif keyword_hits == 1:
            score += 3
            reasons.append("和赛道对标打法有一定重合")

        if notice_style_title:
            score -= 24
            reasons.append("原始标题更像公告或会议通稿，需要重写")
        elif not self._has_subject_anchor_in_title(title, keyword_list):
            score -= 8
            reasons.append("原始标题主体感偏弱，需要补主语")

        if len(str(title).strip()) < 10:
            score -= 8
        if len(str(title).strip()) > 32:
            score -= 6
        if any(token in combined for token in ("统计数据表", "统计公报", "发布会", "论坛", "峰会", "收官")):
            score -= 10
            reasons.append("原始题材偏通稿，需要强改写")

        score = max(18, min(98, score))
        if notice_style_title:
            reasons = [
                *[item for item in reasons if "公告或会议通稿" in item],
                *[item for item in reasons if "公告或会议通稿" not in item],
            ]
        cleaned_reasons = self._unique_reason_list(reasons)[:4]
        if not cleaned_reasons:
            cleaned_reasons = ["题材可改写，但需要更强主体和结论"]

        return {
            "track": normalized_track,
            "score": score,
            "reasons": cleaned_reasons,
            "title_style": title_style,
            "hook_pattern": hook_pattern,
        }

    def build_experiment_tags(
        self,
        *,
        title: str,
        article: str,
        summary: str = "",
        topic_title: str = "",
        platform: str = "toutiao",
        recommended_topics: list[str] | None = None,
        raw_tags: list[str] | None = None,
        commercial_intent: str = "medium",
        content_mix: str = "professional",
        cta_variant: str = "",
        cta_source_code: str = "",
        title_variant: str = "",
        cover_variant: str = "",
        platform_variant: str = "",
        topic_bucket: str = "",
    ) -> dict[str, Any]:
        title_text = str(title or "").strip()
        article_text = str(article or "").strip()
        summary_text = str(summary or "").strip()
        combined = "\n".join(part for part in (title_text, summary_text, article_text, topic_title) if part)

        topic_bucket = str(topic_bucket or "").strip() or self._infer_topic_bucket(combined)
        title_style = self._infer_title_style(title_text)
        hook_pattern = self._infer_hook_pattern(title_text)
        content_angle = self._infer_content_angle(summary_text or article_text or topic_title)
        structure_pattern = self._infer_structure_pattern(article_text, title_text)
        cta_style = self._infer_cta_style(article_text)
        evidence_density = self._infer_evidence_density(article_text)
        emotion_tone = self._infer_emotion_tone(title_text, article_text)
        content_length = len(re.sub(r"\s+", "", article_text))

        test_focus = self._suggest_test_focus(
            title_style=title_style,
            evidence_density=evidence_density,
            cta_style=cta_style,
            structure_pattern=structure_pattern,
        )

        normalized_intent = self._normalize_revenue_enum(
            commercial_intent, ("low", "medium", "high"), "medium"
        )
        normalized_mix = self._normalize_revenue_enum(
            content_mix, ("traffic", "professional", "conversion"), "professional"
        )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "platform": platform,
            "topic_bucket": topic_bucket,
            "title_style": title_style,
            "hook_pattern": hook_pattern,
            "content_angle": content_angle,
            "structure_pattern": structure_pattern,
            "cta_style": cta_style,
            "evidence_density": evidence_density,
            "emotion_tone": emotion_tone,
            "title_length": len(title_text),
            "content_length": content_length,
            "recommended_topics": list(recommended_topics or []),
            "raw_tags": list(raw_tags or []),
            "test_focus": test_focus,
            "commercial_intent": normalized_intent,
            "content_mix": normalized_mix,
            "cta_variant": str(cta_variant or "").strip(),
            "cta_source_code": str(cta_source_code or "").strip(),
            "title_variant": str(title_variant or "").strip(),
            "cover_variant": str(cover_variant or "").strip(),
            "platform_variant": str(platform_variant or "").strip(),
        }

    @staticmethod
    def _normalize_revenue_enum(value: str, allowed: tuple[str, ...], default: str) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in allowed else default

    def ensure_run_experiment_tags(self, run_dir: Path, platform: str = "toutiao") -> tuple[Path, dict[str, Any]]:
        publish_dir = run_dir / self.settings.review.publish_dirname
        publish_dir.mkdir(parents=True, exist_ok=True)
        tags_path = publish_dir / "experiment_tags.json"
        if tags_path.exists():
            payload = json.loads(tags_path.read_text(encoding="utf-8"))
            if str(payload.get("platform", "")).strip() != platform:
                payload["platform"] = platform
                tags_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return tags_path, payload

        publish_meta = self._read_json(publish_dir / "publish_meta.json")
        title = str(publish_meta.get("selected_title", "")).strip() or self._read_text(publish_dir / "title.txt")
        summary = str(publish_meta.get("summary", "")).strip() or self._read_text(publish_dir / "summary.txt")
        article = self._read_text(publish_dir / "article.md") or self._read_text(run_dir / "article.md")
        selected_topic = publish_meta.get("selected_topic") or {}
        topic_title = str(selected_topic.get("title", "")).strip()
        payload = self.build_experiment_tags(
            title=title,
            article=article,
            summary=summary,
            topic_title=topic_title,
            platform=platform,
            recommended_topics=list(publish_meta.get("recommended_topics", [])),
            raw_tags=list(publish_meta.get("tags", [])),
            commercial_intent=str(selected_topic.get("commercial_intent", "medium")),
            content_mix=str(selected_topic.get("content_mix", "professional")),
            cta_variant=str(selected_topic.get("cta_variant", "")),
            cta_source_code=str(selected_topic.get("cta_source_code", "")),
            title_variant=str(selected_topic.get("title_variant", "")),
            cover_variant=str(selected_topic.get("cover_variant", "")),
            platform_variant=str(selected_topic.get("platform_variant", "")),
            topic_bucket=str(selected_topic.get("topic_bucket", "")),
        )
        tags_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if publish_meta:
            publish_meta["experiment_tags"] = payload
            (publish_dir / "publish_meta.json").write_text(
                json.dumps(publish_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return tags_path, payload

    def register_publish_event(
        self,
        *,
        platform: str,
        run_dir: Path,
        result_path: Path | None = None,
    ) -> dict[str, Path]:
        self.ensure_directories()
        tags_path, experiment_tags = self.ensure_run_experiment_tags(run_dir, platform=platform)
        ledger = self._load_ledger()
        publish_dir = run_dir / self.settings.review.publish_dirname
        publish_meta = self._read_json(publish_dir / "publish_meta.json")
        review_status = self._read_json(run_dir / "review_status.json")
        result_payload = self._read_json(result_path) if result_path is not None else {}
        title = str(publish_meta.get("selected_title", "")).strip() or str(review_status.get("selected_title", "")).strip()
        topic_payload = publish_meta.get("selected_topic") or {}
        selected_topic_title = str(topic_payload.get("title", "")).strip()
        record_id = self._build_record_id(platform=platform, run_dir=run_dir)
        existing = ledger.get(record_id, {})

        publish_url = ""
        if platform == "wechat":
            publish_url = str(result_payload.get("publish_response", {}).get("publish_url", "")).strip()
        else:
            publish_url = str(result_payload.get("external_url", "")).strip()

        record = {
            **existing,
            "record_id": record_id,
            "account_key": self.settings.account_key,
            "account_label": self.settings.account_label,
            "platform": platform,
            "run_dir": str(run_dir),
            "title": title,
            "selected_topic_title": selected_topic_title,
            "published_at": str(
                result_payload.get("published_at")
                or result_payload.get("created_at")
                or existing.get("published_at")
                or datetime.now().isoformat(timespec="seconds")
            ),
            "publish_url": publish_url or str(existing.get("publish_url", "")),
            "publish_status": str(result_payload.get("status") or existing.get("publish_status") or "published"),
            "result_path": str(result_path) if result_path else str(existing.get("result_path", "")),
            "publish_meta_path": str(publish_dir / "publish_meta.json"),
            "experiment_tags": experiment_tags,
            "experiment_tags_path": str(tags_path),
            "metrics_latest": existing.get("metrics_latest", {}),
            "performance_snapshots": list(existing.get("performance_snapshots", [])),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        ledger[record_id] = record
        self._save_ledger(ledger)
        return {
            "experiment_tags": tags_path,
            "performance_ledger": self.performance_ledger_path,
            "performance_ledger_csv": self.performance_csv_path,
        }

    def backfill_performance_ledger(self) -> dict[str, Path]:
        self.ensure_directories()
        publish_history_path = self.settings.output_dir / self.settings.publishing.history_filename
        wechat_history_path = self.settings.output_dir / self.settings.wechat.history_filename

        backfilled = 0
        for platform, history_path, relative_result in (
            ("toutiao", publish_history_path, Path(self.settings.review.publish_dirname) / "publish_result.json"),
            (
                "wechat",
                wechat_history_path,
                Path(self.settings.review.publish_dirname) / "wechat" / "wechat_publish_result.json",
            ),
        ):
            history_payload = self._read_json_list(history_path)
            for item in history_payload:
                run_dir_value = str(item.get("run_dir", "")).strip()
                if not run_dir_value:
                    continue
                run_dir = Path(run_dir_value)
                if not run_dir.exists():
                    continue
                result_path = run_dir / relative_result
                if not result_path.exists():
                    continue
                self.register_publish_event(platform=platform, run_dir=run_dir, result_path=result_path)
                backfilled += 1

        metadata_path = self.performance_dir / "backfill_result.json"
        metadata_path.write_text(
            json.dumps(
                {"backfilled_records": backfilled, "generated_at": datetime.now().isoformat(timespec="seconds")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "performance_ledger": self.performance_ledger_path,
            "performance_ledger_csv": self.performance_csv_path,
            "performance_backfill_result": metadata_path,
        }

    def record_performance(
        self,
        *,
        run_dir: Path,
        platform: str,
        snapshot_date: str | None = None,
        snapshot_label: str | None = None,
        snapshot_at: str | None = None,
        collection_date: str | None = None,
        published_date: str | None = None,
        earnings_date: str | None = None,
        availability_status: str = "available",
        revenue_status: str = "unknown",
        platform_revenue_status: str = "unknown",
        revenue_amount_known: bool | None = False,
        platform_revenue: float | None = None,
        content_id: str | None = None,
        impression_count: int | None = None,
        read_count: int | None = None,
        recommendation_read_count: int | None = None,
        like_count: int | None = None,
        wow_count: int | None = None,
        share_count: int | None = None,
        comment_count: int | None = None,
        follower_gain: int | None = None,
        lead_count: int | None = None,
        revenue_amount: float | None = None,
    ) -> dict[str, Path]:
        self.ensure_directories()
        ledger = self._load_ledger()
        record_id = self._build_record_id(platform=platform, run_dir=run_dir)
        if record_id not in ledger:
            result_path = run_dir / self.settings.review.publish_dirname / (
                "wechat/wechat_publish_result.json" if platform == "wechat" else "publish_result.json"
            )
            self.register_publish_event(platform=platform, run_dir=run_dir, result_path=result_path if result_path.exists() else None)
            ledger = self._load_ledger()

        if record_id not in ledger:
            raise FileNotFoundError(f"No published record found for {platform}: {run_dir}")

        resolved_snapshot_at = str(snapshot_at or datetime.now().isoformat(timespec="seconds"))
        resolved_collection_date = self._date_portion(collection_date) or self._date_portion(resolved_snapshot_at)
        if not resolved_collection_date:
            resolved_collection_date = self._date_portion(snapshot_date) or date.today().isoformat()
        try:
            collection_day = date.fromisoformat(resolved_collection_date)
        except ValueError:
            collection_day = date.today()
            resolved_collection_date = collection_day.isoformat()
        resolved_published_date = self._date_portion(
            published_date or ledger[record_id].get("published_at", "")
        )
        resolved_earnings_date = self._date_portion(earnings_date)
        if not resolved_earnings_date:
            resolved_earnings_date = (
                (collection_day - timedelta(days=1)).isoformat()
                if str(platform).strip().lower() in {"toutiao", "toutiao_weitoutiao"}
                else collection_day.isoformat()
            )

        normalized_availability = str(availability_status or "available").strip() or "available"
        if resolved_published_date:
            if resolved_published_date > resolved_collection_date:
                normalized_availability = "not_available"
            elif (
                str(platform).strip().lower() in {"toutiao", "toutiao_weitoutiao"}
                and resolved_published_date > resolved_earnings_date
                and normalized_availability == "available"
            ):
                normalized_availability = "pending_t1"
        known_revenue = (
            bool(revenue_amount_known)
            or platform_revenue is not None
            or (
                revenue_amount is not None
                and str(revenue_status or "").strip() in {"available", "partial"}
            )
        )
        if normalized_availability in {"pending_t1", "not_available"}:
            known_revenue = False
        resolved_platform_status = str(platform_revenue_status or "unknown").strip()
        resolved_revenue_status = str(revenue_status or "unknown").strip()
        if normalized_availability == "pending_t1":
            resolved_platform_status = "pending_t1"
            resolved_revenue_status = "pending_t1"
        elif normalized_availability == "not_available":
            resolved_platform_status = "not_available"
            resolved_revenue_status = "not_available"
        if platform_revenue is not None and resolved_platform_status == "unknown":
            resolved_platform_status = "available"
        if known_revenue and resolved_revenue_status == "unknown":
            resolved_revenue_status = "available"
        snapshot_payload = {
            "snapshot_date": self._date_portion(snapshot_date) or resolved_collection_date,
            "snapshot_label": str(snapshot_label or "").strip(),
            "snapshot_at": resolved_snapshot_at,
            "observed_at": resolved_snapshot_at,
            "collection_date": resolved_collection_date,
            "published_date": resolved_published_date,
            "earnings_date": resolved_earnings_date,
            "data_date": resolved_earnings_date,
            "availability_status": normalized_availability,
            "revenue_status": resolved_revenue_status,
            "platform_revenue_status": resolved_platform_status,
            "revenue_amount_known": known_revenue,
            "analysis_eligible": normalized_availability == "available",
            "platform_revenue": (
                float(platform_revenue)
                if platform_revenue is not None and known_revenue
                else None
            ),
            "content_id": str(content_id or ledger[record_id].get("content_id", "")).strip(),
            "impression_count": int(impression_count or 0),
            "read_count": int(read_count or 0),
            "recommendation_read_count": int(recommendation_read_count or 0),
            "like_count": int(like_count or 0),
            "wow_count": int(wow_count or 0),
            "share_count": int(share_count or 0),
            "comment_count": int(comment_count or 0),
            "follower_gain": int(follower_gain or 0),
            "lead_count": int(lead_count or 0),
            "revenue_amount": float(revenue_amount) if known_revenue and revenue_amount is not None else None,
        }
        snapshot_payload.update(self._derive_metrics(snapshot_payload))

        record = dict(ledger[record_id])
        snapshots = [item for item in record.get("performance_snapshots", []) if isinstance(item, dict)]
        if snapshot_payload["snapshot_label"]:
            existing_snapshot = next(
                (
                    item
                    for item in snapshots
                    if str(item.get("snapshot_label", "")) == snapshot_payload["snapshot_label"]
                ),
                None,
            )
            snapshots = [
                item for item in snapshots
                if str(item.get("snapshot_label", "")) != snapshot_payload["snapshot_label"]
            ]
            snapshot_payload = self._merge_performance_snapshot(existing_snapshot, snapshot_payload)
        else:
            existing_snapshot = next(
                (
                    item
                    for item in snapshots
                    if str(item.get("snapshot_date", "")) == snapshot_payload["snapshot_date"]
                ),
                None,
            )
            snapshots = [
                item
                for item in snapshots
                if str(item.get("snapshot_date", "")) != snapshot_payload["snapshot_date"]
            ]
            snapshot_payload = self._merge_performance_snapshot(existing_snapshot, snapshot_payload)
        snapshot_payload.update(self._derive_metrics(snapshot_payload))
        snapshots.append(snapshot_payload)
        snapshots.sort(key=self._performance_snapshot_sort_key)

        record["content_id"] = snapshot_payload["content_id"] or record.get("content_id", "")
        record["metrics_latest"] = max(snapshots, key=self._metrics_latest_sort_key)
        record["performance_snapshots"] = snapshots
        record["updated_at"] = datetime.now().isoformat(timespec="seconds")
        ledger[record_id] = record
        self._save_ledger(ledger)

        latest_snapshot_path = self.performance_dir / "latest_snapshot.json"
        latest_snapshot_path.write_text(
            json.dumps({"record_id": record_id, "snapshot": snapshot_payload}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "performance_ledger": self.performance_ledger_path,
            "performance_ledger_csv": self.performance_csv_path,
            "performance_snapshot": latest_snapshot_path,
        }

    def _merge_performance_snapshot(
        self,
        existing: dict[str, Any] | None,
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep reliable values when a later retry has unknown T+1 earnings."""
        if not existing:
            return dict(incoming)
        merged = dict(existing)
        availability_rank = {"not_available": 0, "pending_t1": 1, "available": 2}
        old_availability = infer_availability_status(existing)
        new_availability = infer_availability_status(incoming)
        if new_availability == "pending_t1":
            merged["availability_status"] = "pending_t1"
        elif new_availability and availability_rank.get(new_availability, -1) >= availability_rank.get(old_availability, -1):
            merged["availability_status"] = new_availability

        status_rank = {"unknown": 0, "pending_t1": 1, "not_available": 1, "partial": 2, "available": 3}
        for key in ("revenue_status", "platform_revenue_status"):
            old_status = str(existing.get(key, "unknown")).strip() or "unknown"
            new_status = str(incoming.get(key, "unknown")).strip() or "unknown"
            if new_availability == "pending_t1":
                merged[key] = "pending_t1"
            elif status_rank.get(new_status, 0) > status_rank.get(old_status, 0):
                merged[key] = new_status

        for key in (
            "snapshot_date",
            "snapshot_at",
            "observed_at",
            "collection_date",
            "published_date",
            "earnings_date",
            "data_date",
            "content_id",
        ):
            if str(incoming.get(key, "")).strip():
                merged[key] = incoming[key]
        if incoming.get("platform_revenue") is not None:
            merged["platform_revenue"] = incoming["platform_revenue"]

        for key in (
            "impression_count",
            "read_count",
            "recommendation_read_count",
            "like_count",
            "wow_count",
            "share_count",
            "comment_count",
            "follower_gain",
            "lead_count",
        ):
            try:
                merged[key] = max(int(existing.get(key, 0) or 0), int(incoming.get(key, 0) or 0))
            except (TypeError, ValueError):
                merged[key] = incoming.get(key, existing.get(key, 0))

        old_platform_revenue = existing.get("platform_revenue")
        new_platform_revenue = incoming.get("platform_revenue")
        old_platform_status = str(existing.get("platform_revenue_status", "")).strip()
        new_platform_status = str(incoming.get("platform_revenue_status", "")).strip()
        old_known = bool(existing.get("revenue_amount_known", False)) or (
            old_platform_revenue is not None and old_platform_status in {"", "available"}
        )
        new_known = bool(incoming.get("revenue_amount_known", False)) or (
            new_platform_revenue is not None and new_platform_status in {"", "available"}
        )
        if new_availability == "pending_t1":
            merged["platform_revenue"] = None
            merged["revenue_amount"] = None
        else:
            merged["revenue_amount"] = (
                incoming.get("revenue_amount", 0.0)
                if new_known or not old_known
                else existing.get("revenue_amount", 0.0)
            )
        merged["revenue_amount_known"] = False if new_availability == "pending_t1" else old_known or new_known
        merged["analysis_eligible"] = new_availability == "available"
        return merged

    def build_performance_report(self) -> dict[str, Path]:
        self.ensure_directories()
        ledger = self._attach_attribution_metrics(list(self._load_ledger().values()))
        summary = self._summarize_performance(ledger)
        report_payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": summary,
            "records": ledger,
        }
        self.performance_report_json_path.write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.performance_report_path.write_text(
            self._build_performance_markdown(report_payload),
            encoding="utf-8",
        )
        return {
            "performance_ledger": self.performance_ledger_path,
            "performance_ledger_csv": self.performance_csv_path,
            "performance_report": self.performance_report_path,
            "performance_report_json": self.performance_report_json_path,
        }

    def _attach_attribution_metrics(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Join the operational performance ledger with content-level economics."""
        try:
            from toutiao_content_factory.services.revenue_attribution_service import RevenueAttributionService

            attribution = RevenueAttributionService(self.settings.output_dir, self.logger)
            stored_economics = attribution._load_json(attribution.unit_economics_path, default={})
        except Exception as exc:  # pragma: no cover - optional legacy data path
            self.logger.debug("Revenue attribution join skipped: %s", exc)
            return rows

        enriched_rows: list[dict[str, Any]] = []
        for row in rows:
            enriched = dict(row)
            content_id = str(enriched.get("content_id", "")).strip()
            if not content_id:
                identity = attribution.find_content_record_by_run_dir(enriched.get("run_dir", ""))
                content_id = str(identity.get("content_id", "")).strip() if identity else ""
            if content_id:
                existing = stored_economics.get(content_id, {})
                platform_revenue = (
                    float(existing.get("platform_revenue", 0.0) or 0.0)
                    if isinstance(existing, dict)
                    else 0.0
                )
                economics = attribution.compute_unit_economics(
                    content_id,
                    platform_revenue=platform_revenue,
                )
                enriched["content_id"] = content_id
                enriched["unit_economics"] = economics.to_dict()
                revenue_summary = attribution.summarize_revenue_events(content_id)
                metrics = dict(enriched.get("metrics_latest", {}))
                metrics.setdefault("lead_count", revenue_summary.get("lead_count", 0))
                metrics.setdefault("revenue_amount", revenue_summary.get("revenue_amount", 0.0))
                enriched["metrics_latest"] = metrics
            enriched_rows.append(enriched)
        return enriched_rows

    def collect_self_benchmark_samples(
        self,
        *,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Path]:
        online_result = self._collect_online_self_benchmark_samples(
            account_key=account_key,
            track=track,
            top_n=top_n,
        )
        if online_result is not None:
            return online_result
        return self._collect_local_self_benchmark_samples(
            account_key=account_key,
            track=track,
            top_n=top_n,
        )

    def collect_self_benchmark_samples_from_report(
        self,
        topic_report: DailyTopicsReport,
        *,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
        source_path: str | None = None,
    ) -> dict[str, Path] | None:
        if not topic_report.topics:
            return None
        return self._collect_online_self_benchmark_samples_from_topics(
            topic_report.topics,
            account_key=account_key,
            track=track,
            top_n=top_n,
            source_path=source_path or str(self.settings.output_dir / "daily_topics.json"),
            source_summary=dict(topic_report.source_stats),
        )

    def _collect_local_self_benchmark_samples(
        self,
        *,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Path]:
        self.ensure_directories()
        self.backfill_performance_ledger()
        ledger_rows = list(self._load_ledger().values())
        existing_library = self.load_self_benchmark_library()
        existing_keys = {
            self._self_benchmark_identity(article)
            for article in existing_library
            if self._self_benchmark_identity(article)
        }
        requested_account_key = str(account_key or "").strip()
        requested_track = str(track or "").strip()
        normalized_track = self.infer_title_playbook_track(requested_track) if requested_track else ""

        all_candidates: list[dict[str, Any]] = []
        for row in ledger_rows:
            candidate = self._build_self_benchmark_candidate(row)
            if not candidate:
                continue
            if requested_account_key and str(candidate.get("account_key", "")).strip() != requested_account_key:
                continue
            if normalized_track and str(candidate.get("track", "")).strip() != normalized_track:
                continue
            identity = self._self_benchmark_identity(candidate)
            candidate["already_promoted"] = bool(identity and identity in existing_keys)
            candidate["qualified"] = bool(candidate.get("qualified", False))
            all_candidates.append(candidate)

        sorted_candidates = sorted(
            all_candidates,
            key=lambda item: (
                int(item.get("benchmark_score", 0)),
                int(item.get("read_count", 0)),
                int(item.get("impression_count", 0)),
                float(item.get("read_rate", 0) or 0),
            ),
            reverse=True,
        )
        selected_candidates = [item for item in sorted_candidates if item.get("qualified") and not item.get("already_promoted")]
        if not selected_candidates:
            selected_candidates = [item for item in sorted_candidates if not item.get("already_promoted")]
        selected_candidates = selected_candidates[: max(1, int(top_n))]
        for index, candidate in enumerate(selected_candidates, start=1):
            candidate["rank"] = index

        collection_payload = self._build_self_benchmark_collection_payload(
            account_key=requested_account_key or self.settings.account_key,
            requested_track=normalized_track or requested_track or self.settings.industry_focus,
            selected_candidates=selected_candidates,
            all_candidates=sorted_candidates,
        )
        self.self_benchmark_collection_json_path.write_text(
            json.dumps(collection_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.self_benchmark_collection_path.write_text(
            self._build_self_benchmark_collection_markdown(collection_payload),
            encoding="utf-8",
        )
        return {
            "self_benchmark_collection": self.self_benchmark_collection_path,
            "self_benchmark_collection_json": self.self_benchmark_collection_json_path,
        }

    def _collect_online_self_benchmark_samples(
        self,
        *,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Path] | None:
        self.ensure_directories()
        try:
            from toutiao_content_factory.services.topic_collection import TopicCollectionService
        except Exception as exc:
            self.logger.warning("Online sample collection unavailable, fallback to local ledger: %s", exc)
            return None

        try:
            topic_report = TopicCollectionService(self.settings, self.logger).collect_sync()
        except Exception as exc:
            self.logger.warning("Online topic collection failed, fallback to local ledger: %s", exc)
            return None
        return self._collect_online_self_benchmark_samples_from_topics(
            topic_report.topics,
            account_key=account_key,
            track=track,
            top_n=top_n,
            source_path="live_topic_collection",
            source_summary=dict(topic_report.source_stats),
        )

    def _collect_online_self_benchmark_samples_from_topics(
        self,
        topics: list[TopicCandidate] | list[Any],
        *,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
        source_path: str = "",
        source_summary: dict[str, Any] | None = None,
    ) -> dict[str, Path] | None:
        self.ensure_directories()

        requested_account_key = str(account_key or "").strip() or self.settings.account_key
        requested_track = str(track or "").strip()
        normalized_track = self.infer_title_playbook_track(requested_track) if requested_track else ""

        existing_library = [*self.load_self_benchmark_library(), *self._load_collected_reference_samples()]
        existing_keys = {
            self._self_benchmark_identity(article)
            for article in existing_library
            if self._self_benchmark_identity(article)
        }
        all_candidates: list[dict[str, Any]] = []
        for topic in topics:
            candidate = self._build_online_reference_candidate(topic, account_key=requested_account_key)
            if not candidate:
                continue
            if normalized_track and str(candidate.get("track", "")).strip() != normalized_track:
                continue
            identity = self._self_benchmark_identity(candidate)
            candidate["already_promoted"] = bool(identity and identity in existing_keys)
            all_candidates.append(candidate)

        sorted_candidates = sorted(
            all_candidates,
            key=lambda item: (
                int(item.get("benchmark_score", 0)),
                int(item.get("online_scores", {}).get("total_score", 0)),
                int(item.get("online_scores", {}).get("title_potential_score", 0)),
                int(item.get("online_scores", {}).get("quality_score", 0)),
            ),
            reverse=True,
        )
        selected_candidates = [item for item in sorted_candidates if item.get("qualified") and not item.get("already_promoted")]
        if not selected_candidates:
            selected_candidates = [item for item in sorted_candidates if not item.get("already_promoted")]
        if not selected_candidates:
            selected_candidates = list(sorted_candidates[: max(1, int(top_n))])
        selected_candidates = selected_candidates[: max(1, int(top_n))]
        if not selected_candidates:
            return None
        for index, candidate in enumerate(selected_candidates, start=1):
            candidate["rank"] = index

        collection_payload = self._build_self_benchmark_collection_payload(
            account_key=requested_account_key,
            requested_track=normalized_track or requested_track or self.settings.industry_focus,
            selected_candidates=selected_candidates,
            all_candidates=sorted_candidates,
        )
        collection_payload["mode"] = "online_reference"
        collection_payload["source_path"] = source_path or "live_topic_collection"
        if source_summary is not None:
            collection_payload["source_summary"] = dict(source_summary)
        collection_payload["generated_from"] = "topic_collection"
        collection_payload["selection_mode"] = (
            "fresh_online_reference"
            if any(not bool(item.get("already_promoted")) for item in selected_candidates)
            else "reused_online_reference"
        )
        self.self_benchmark_collection_json_path.write_text(
            json.dumps(collection_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.self_benchmark_collection_path.write_text(
            self._build_self_benchmark_collection_markdown(collection_payload),
            encoding="utf-8",
        )
        return {
            "self_benchmark_collection": self.self_benchmark_collection_path,
            "self_benchmark_collection_json": self.self_benchmark_collection_json_path,
        }

    def _build_title_playbook_payload(
        self,
        accounts: list[dict[str, Any]],
        self_benchmarks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        track_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for account in accounts:
            track = str(account.get("track", "")).strip() or "未分类"
            track_groups[track].append(account)
        self_benchmark_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for article in self_benchmarks or []:
            track = str(article.get("track", "")).strip() or "未分类"
            self_benchmark_groups[track].append(article)
            track_groups.setdefault(track, [])

        tracks_payload: list[dict[str, Any]] = []
        overall_title_styles: Counter[str] = Counter()
        overall_hooks: Counter[str] = Counter()
        overall_self_benchmark_count = 0

        for track, group_accounts in sorted(track_groups.items()):
            track_self_benchmarks = self_benchmark_groups.get(track, [])
            keyword_counter: Counter[str] = Counter()
            title_style_counter: Counter[str] = Counter()
            hook_counter: Counter[str] = Counter()
            example_titles: list[str] = []
            self_pattern_counter: Counter[str] = Counter()
            self_titles: list[str] = []

            for account in group_accounts:
                for pillar in account.get("content_pillars", []):
                    keyword_counter[str(pillar)] += 1
                for article in account.get("observation_articles", []):
                    title = str(article.get("title", "")).strip()
                    if title:
                        example_titles.append(title)
                    title_style = str(article.get("title_style", "")).strip()
                    hook_pattern = str(article.get("hook_pattern", "")).strip()
                    if title_style:
                        title_style_counter[title_style] += 1
                        overall_title_styles[title_style] += 1
                    if hook_pattern:
                        hook_counter[hook_pattern] += 1
                        overall_hooks[hook_pattern] += 1
                    for keyword in article.get("keywords", []):
                        keyword_counter[str(keyword)] += 1

            for article in track_self_benchmarks:
                title = str(article.get("title", "")).strip()
                if title:
                    example_titles.append(title)
                    self_titles.append(title)
                for keyword in article.get("keywords", []):
                    keyword_counter[str(keyword)] += 1
                for pattern in article.get("inferred_patterns", []):
                    pattern_name = str(pattern).strip()
                    if pattern_name:
                        self_pattern_counter[pattern_name] += 1

            overall_self_benchmark_count += len(track_self_benchmarks)

            top_keywords = [item[0] for item in keyword_counter.most_common(8)]
            tracks_payload.append(
                {
                    "track": track,
                    "benchmark_accounts": [str(item.get("name", "")).strip() for item in group_accounts],
                    "top_keywords": top_keywords,
                    "top_title_styles": dict(title_style_counter.most_common(6)),
                    "top_hook_patterns": dict(hook_counter.most_common(6)),
                    "example_titles": self._unique_texts(example_titles)[:10],
                    "self_benchmark_titles": self._unique_texts(self_titles)[:5],
                    "self_benchmark_patterns": [name for name, _count in self_pattern_counter.most_common(6)],
                    "self_benchmark_count": len(track_self_benchmarks),
                    "title_templates": self._recommend_title_templates(track, top_keywords),
                    "topic_imitation_pool": self._recommend_topic_imitation_pool(track, top_keywords),
                }
            )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_path": str(self.benchmark_source_path),
            "summary": {
                "track_count": len(tracks_payload),
                "account_count": len(accounts),
                "self_benchmark_count": overall_self_benchmark_count,
                "top_title_styles": dict(overall_title_styles.most_common(8)),
                "top_hook_patterns": dict(overall_hooks.most_common(8)),
            },
            "tracks": tracks_payload,
        }

    def _build_title_playbook_markdown(self, payload: dict[str, Any]) -> str:
        summary = dict(payload.get("summary", {}))
        lines = [
            "# 标题打法库",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 对标赛道数：{summary.get('track_count', 0)}",
            f"- 对标账号数：{summary.get('account_count', 0)}",
            f"- 自有高表现样本数：{summary.get('self_benchmark_count', 0)}",
            "",
            "## 全局高频模式",
            "",
        ]
        for label, block in (
            ("标题风格", summary.get("top_title_styles", {})),
            ("标题钩子", summary.get("top_hook_patterns", {})),
        ):
            lines.append(f"### {label}")
            if not block:
                lines.append("- 暂无数据")
            else:
                for key, value in block.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

        for track in payload.get("tracks", []):
            lines.extend(
                [
                    f"## {track.get('track', '未分类')}",
                    "",
                    f"- 对标账号：{', '.join(track.get('benchmark_accounts', []))}",
                    f"- 高频关键词：{', '.join(track.get('top_keywords', [])) or '暂无'}",
                    f"- 自有高表现样本数：{track.get('self_benchmark_count', 0)}",
                    "",
                    "### 可复用标题模版",
                ]
            )
            for item in track.get("title_templates", []):
                lines.append(f"- {item}")
            lines.extend(["", "### 选题模仿池"])
            for item in track.get("topic_imitation_pool", []):
                lines.append(f"- {item}")
            if track.get("self_benchmark_titles"):
                lines.extend(["", "### 自有高表现样本"])
                for item in track.get("self_benchmark_titles", []):
                    lines.append(f"- {item}")
            if track.get("self_benchmark_patterns"):
                lines.extend(["", "### 已验证写法线索"])
                for item in track.get("self_benchmark_patterns", []):
                    lines.append(f"- {item}")
            lines.extend(["", "### 样本标题"])
            for item in track.get("example_titles", []):
                lines.append(f"- {item}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _recommend_title_templates(self, track: str, keywords: list[str]) -> list[str]:
        """按赛道返回一批「句式骨架」——⚠️ 这些是**程序手工合成的**，没有任何实测依据。

        它们只是把一个固定句型里的关键词槽位填上赛道关键词，因此**不能当作
        「好标题范例」**。历史教训（2026-09-16）：`title_prompt_v2.md` 曾把这批
        结果标成「**可模仿标题模版**」交给模型，直接教模型套骨架 —— 一次运行
        13 个候选里 11 个是同一骨架，最后发布的就是同族的硬编码兜底模板。
        现在提示词已改成「禁止套用结构」，并在 `agent_workflow` 加了同骨架数量上限。
        **若要恢复"可模仿"的定位，必须先把这里的合成句式换成真实高 CTR 样本**
        （数据来源：`growth_ops/performance/performance_ledger.json` 里阅读量达标的作品）。
        """
        primary = keywords[0] if keywords else "这个赛道"
        if "机器人" in track:
            return [
                f"{primary}热闹起来后，真正先赚钱的是减速器还是传感器",
                f"{primary}进入落地期，先看交付能力而不是演示效果",
                f"一笔订单背后，{primary}产业链开始重新定价",
                f"{primary}还没全面爆发，但上游零部件已经先变了",
                f"{primary}从展示走向交付，分水岭到底在哪",
            ]
        if "储能" in track or "新能源" in track:
            return [
                f"{primary}项目越签越多，真正卡利润的是PCS还是电芯",
                f"{primary}看上去很热，先兑现的可能不是整站",
                f"{primary}进入拼交付阶段，谁会先吃到红利",
                f"一轮涨价之后，{primary}产业链的利润开始重排",
                f"{primary}不缺故事，缺的是并网和回款速度",
            ]
        if "国际" in track:
            return [
                f"{primary}升级之后，真正要看的不是表态而是下一步动作",
                f"{primary}再起波澜，市场为什么先盯上黄金和油价",
                f"{primary}看起来突然，背后其实早有预兆",
                f"{primary}发酵后，真正被改写的是哪条利益链",
                f"{primary}升温之后，谁在施压，谁在试探底线",
            ]
        if "体育" in track:
            return [
                f"{primary}赢了，但真正决定比赛的是这次转折",
                f"{primary}输了之后，争议不只在比分",
                f"{primary}这场球，最该复盘的不是结果本身",
                f"{primary}赛后最大的信号，可能不是那粒进球",
                f"{primary}看着突然，比赛其实从这里就被改写了",
            ]
        return [
            f"{primary}又有新动作，但真正该盯的是盈利拐点",
            "表面上是热点升级，实际上是产业链重新分配利润",
            f"{primary}进入新阶段后，谁会先受益",
            "一个新闻背后，行业分水岭正在出现",
            f"{primary}看起来很火，真正值钱的是哪一层",
        ]

    def _recommend_topic_imitation_pool(self, track: str, keywords: list[str]) -> list[str]:
        primary = keywords[0] if keywords else "核心赛道"
        if "机器人" in track:
            return [
                f"{primary}订单增长后，哪些零部件会比整机先兑现",
                f"{primary}出海提速，哪些中国供应链公司最先受益",
                f"当{primary}进入量产验证期，最该跟踪的三个指标是什么",
                f"{primary}价格往下走之后，谁的利润会先被压缩",
                f"从机场、物流到工厂，{primary}会先在哪个场景跑通",
            ]
        if "储能" in track or "新能源" in track:
            return [
                f"{primary}项目密集开工后，PCS和电芯谁会先涨价",
                f"{primary}出口走强，真正值得看的不是装机而是盈利能力",
                f"一轮政策刺激之后，{primary}行业会不会提前进入淘汰赛",
                f"{primary}招投标加速，哪些环节最容易出现价格战",
                f"从并网和回款节奏看，{primary}景气度还能走多久",
            ]
        if "国际" in track:
            return [
                f"{primary}升级后，最值得跟踪的下一步变量是什么",
                f"如果{primary}继续升温，哪个市场会先有反应",
                f"{primary}背后，真正推动局势变化的是哪几方利益",
                f"从表态到行动，{primary}还差哪一步",
                f"{primary}看似突发，但为什么偏偏发生在现在",
            ]
        if "体育" in track:
            return [
                f"{primary}这件事，真正改变走势的是哪个关键节点",
                f"{primary}事后最值得讨论的争议点到底是什么",
                f"如果复盘{primary}，最该盯住的是哪个关键企业或哪次技术转向",
                f"{primary}结果出来后，市场格局和后续走势会怎么变",
                f"{primary}落地之后，下一个最大的压力给到了谁",
            ]
        return [
            "一个热点走热以后，真正有议价权的是哪一层",
            f"这轮行业变化背后，{primary}公司的竞争逻辑变了吗",
            f"从订单和盈利开始放量看，{primary}进入兑现期了吗",
            f"{primary}新闻很多，但真正重要的信号只有哪几个",
            f"如果{primary}继续升温，谁会先吃到第一波红利",
        ]

    def _strategy_defaults_for_track(self, track: str) -> dict[str, Any]:
        if "机器人" in track:
            return {
                "voice_summary": "像量子位、高工机器人这类账号那样，用热点切题，但必须落到场景、订单、交付和供应链卡位。",
                "reader_value": "读者想看到的不是机器人又热了，而是谁先量产、谁先拿订单、利润先流向哪一环。",
                "opening_rules": [
                    "首句直接下判断，不从背景百科讲起。",
                    "第二句给场景或订单锚点，例如机场、物流、工厂、示范项目。",
                    "第三句点出利润迁移或验证分水岭。",
                ],
                "structure_rules": [
                    "先写场景与变化，再写产业链分工和利润归属。",
                    "中段必须出现公司、项目、客户、交付或量产主体。",
                    "后段写跟踪指标，优先 MTBF、交付周期、客户验收、订单转化。",
                    "结尾只收束到 1 到 2 个关键变量，不写空泛大总结。",
                ],
                "angle_rules": [
                    "热点事件只是入口，重点解释产业链重新定价。",
                    "优先写谁先受益、谁还在验证、谁可能被价格战挤压。",
                    "把技术进展翻译成商业化节奏，不停留在参数展示。",
                ],
                "evidence_rules": [
                    "优先抽取订单、交付、客户验证、单台成本、毛利和产能。",
                    "没有数字时也要至少落到明确主体和场景。",
                    "避免扩写没有证据的市场份额、增速和行业均值。",
                ],
                "review_focus": [
                    "开头有没有判断感，而不是复述新闻。",
                    "正文有没有具体场景和主体。",
                    "有没有说明利润流向、环节分化或验证门槛。",
                    "结尾有没有留下可跟踪变量。",
                ],
                "avoid_patterns": [
                    "不要把人形机器人写成泛科技新闻。",
                    "不要大段描述演示效果却没有商业化信息。",
                    "不要只写整机，不写零部件、系统集成和运维。",
                ],
            }
        if "国际" in track:
            return {
                "voice_summary": "像国际评论型头条号那样，用最新事件切题，但必须落到国家行为、利益分歧、时间窗口和后续变量。",
                "reader_value": "读者想知道的不是新闻复述，而是这件事为什么现在发生、对中国意味着什么、下一步会如何演化。",
                "opening_rules": [
                    "首句直接下判断，不从事件时间线流水账讲起。",
                    "第二句给国家、人物、关税、制裁、表态或市场反应中的关键锚点。",
                    "第三句点出真正值得跟踪的变量，例如谈判节奏、地缘扩散、油价或资产价格。",
                ],
                "structure_rules": [
                    "先写事件变化，再写各方利益和动作逻辑。",
                    "中段必须出现国家主体、政策动作或市场反应中的至少两个。",
                    "后段写下一步演化路径和中国视角下的影响。",
                    "结尾只收束到 1 到 2 个最关键变量，不写空泛大国叙事。",
                ],
                "angle_rules": [
                    "不要做国际新闻摘要，要做因果和利益拆解。",
                    "优先写谁在施压、谁在让步、谁在重新定价。",
                    "把表态新闻翻译成局势变化，不停留在字面消息。",
                ],
                "evidence_rules": [
                    "优先抽取国家、人物、时间点、税率、制裁范围、市场价格反应。",
                    "没有数据时也要至少落到明确主体和动作。",
                    "避免补写未经证实的内幕和立场化判断。",
                ],
                "review_focus": [
                    "开头有没有结论，而不是事件播报。",
                    "有没有国家主体和利益关系拆解。",
                    "有没有对中国或市场的实际影响。",
                    "结尾有没有可跟踪变量。",
                ],
                "avoid_patterns": [
                    "不要写成立场口号文。",
                    "不要只堆时间线，不做因果拆解。",
                    "不要使用未经证实的传闻和阴谋论表达。",
                ],
            }
        if "体育" in track:
            return {
                "voice_summary": "像头条高阅读深度号那样，抓事件、争议和关键变量，但一定落到结果、转折、数据和后续走势。",
                "reader_value": "读者更关心发生了什么、为什么、谁受影响、后续会怎样。",
                "opening_rules": [
                    "首句先给判断或结论，不从背景资料讲起。",
                    "第二句给关键数据或关键企业表现。",
                    "第三句点出最值得讨论的转折、争议或后续影响。",
                ],
                "structure_rules": [
                    "先写结果和转折，再写关键变量和数据支撑。",
                    "中段必须出现数据、技术路线、市场份额或企业动态中的至少两个。",
                    "后段补产业影响、格局变化或舆论争议。",
                    "结尾回到后续变量和跟踪方向。",
                ],
                "angle_rules": [
                    "不要只写表面新闻，要解释事件怎么改变格局。",
                    "优先从关键企业、关键节点、关键数据切入。",
                    "把热度翻译成分析价值，不停留在情绪宣泄。",
                ],
                "evidence_rules": [
                    "优先抽取市场规模、增长率、份额、融资额、订单量等硬数据。",
                    "没有完整数据时也要落到明确企业、产品和转折节点。",
                    "避免伪造数据和未出现的内幕消息。",
                ],
                "review_focus": [
                    "有没有判断先行和转折感。",
                    "有没有关键变量与数据支撑。",
                    "有没有产业格局或舆论层面的后续影响。",
                    "结尾有没有留下下一步看点。",
                ],
                "avoid_patterns": [
                    "不要写成纯情绪帖。",
                    "不要只堆事件过程，不提关键转折。",
                    "不要把未经证实的传闻写成事实。",
                ],
            }
        if "储能" in track or "新能源" in track:
            return {
                "voice_summary": "像储能网、北极星电力网、SMM 新能源那样，从项目、招投标或政策切入，但最后一定落到并网、回款、毛利和价格战。",
                "reader_value": "读者更关心项目是否能并网、回款是否顺畅、哪个环节先涨价或先掉毛利。",
                "opening_rules": [
                    "首句先下行业判断，优先点明景气不等于普涨。",
                    "第二句给项目、招标、装机、并网或出货数字。",
                    "第三句明确利润卡点，例如 PCS、电芯、逆变器、回款。",
                ],
                "structure_rules": [
                    "先写项目或订单变化，再写产业链利润重排。",
                    "中段拆招标、并网、交付、回款四个兑现环节中的至少两个。",
                    "后段补价格战、政策节奏或海外竞争风险。",
                    "结尾回到跟踪变量，例如中标价、并网节奏、回款周期、海外需求。",
                ],
                "angle_rules": [
                    "不要只写签约数量，要解释谁真正赚到钱。",
                    "优先从现金流、毛利、并网和出货兑现切入。",
                    "政策和项目只是催化剂，核心是盈利能力与供需变化。",
                ],
                "evidence_rules": [
                    "优先抽取装机、并网、项目规模、中标价、回款和出货口径。",
                    "尽量给出项目主体、区域、电站类型或设备环节。",
                    "没有明确口径时，不要杜撰行业均价和利润率。",
                ],
                "review_focus": [
                    "有没有解释景气和盈利并不等价。",
                    "有没有项目主体、招标/并网/回款等兑现信息。",
                    "有没有交代价格战、产能和政策风险。",
                    "结尾有没有留下读者愿意收藏的跟踪指标。",
                ],
                "avoid_patterns": [
                    "不要写成政策快讯摘抄。",
                    "不要只谈装机，不谈并网、回款和毛利。",
                    "不要用空泛的新能源大逻辑替代具体兑现链路。",
                ],
            }
        return {
            "voice_summary": "像 36 氪、虎嗅这类账号那样，把新闻事件转成行业判断，强调商业模式变化和利润分配。",
            "reader_value": "读者想快速知道这个新闻真正意味着什么、谁受益、后续看什么。",
            "opening_rules": [
                "第一句必须给结论，不做冗长铺垫。",
                "第二句给最关键的数据或主体变化。",
                "第三句交代为什么现在值得看。",
            ],
            "structure_rules": [
                "先讲变化，再讲原因，再讲谁受益和谁承压。",
                "至少要有一个产业链、利润或竞争格局视角。",
                "结尾收束到读者可以继续跟踪的变量。",
            ],
            "angle_rules": [
                "不要停留在事件复述，要解释背后的行业结构变化。",
                "优先写利润、订单、交付、估值重排或竞争格局变化。",
                "尽量让每一段都服务于同一个主判断。",
            ],
            "evidence_rules": [
                "优先抽取主体、数字、行业位置和后续变量。",
                "如果证据弱，就写保守判断，不要虚构趋势斜率。",
            ],
            "review_focus": [
                "是否先给结论，再给证据。",
                "是否有主体、有变量、有分化判断。",
                "是否避免了新闻播报腔。",
            ],
            "avoid_patterns": [
                "不要写成媒体通稿摘要。",
                "不要段段都像套话总结。",
                "不要只讲热度，不讲兑现。",
            ],
        }

    def _load_ledger(self) -> dict[str, dict[str, Any]]:
        if not self.performance_ledger_path.exists():
            return {}
        payload = json.loads(self.performance_ledger_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("record_id", "")).strip()
            if not record_id:
                continue
            result[record_id] = item
        return result

    def _save_ledger(self, ledger: dict[str, dict[str, Any]]) -> None:
        rows = sorted(
            list(ledger.values()),
            key=lambda item: (str(item.get("published_at", "")), str(item.get("record_id", ""))),
            reverse=True,
        )
        self.performance_ledger_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_ledger_csv(rows)

    def _write_ledger_csv(self, rows: list[dict[str, Any]]) -> None:
        fieldnames = [
            "record_id",
            "content_id",
            "account_key",
            "platform",
            "published_at",
            "title",
            "selected_topic_title",
            "publish_url",
            "publish_status",
            "commercial_intent",
            "content_mix",
            "cta_source_code",
            "snapshot_label",
            "snapshot_at",
            "collection_date",
            "published_date",
            "earnings_date",
            "availability_status",
            "revenue_status",
            "platform_revenue_status",
            "revenue_amount_known",
            "topic_bucket",
            "title_style",
            "hook_pattern",
            "content_angle",
            "structure_pattern",
            "cta_style",
            "evidence_density",
            "emotion_tone",
            "test_focus",
            "impression_count",
            "read_count",
            "recommendation_read_count",
            "like_count",
            "wow_count",
            "share_count",
            "comment_count",
            "follower_gain",
            "read_rate",
            "recommendation_ratio",
            "interaction_rate",
            "lead_count",
            "revenue_amount",
        ]
        with self.performance_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for item in rows:
                metrics = dict(item.get("metrics_latest", {}))
                tags = dict(item.get("experiment_tags", {}))
                writer.writerow(
                    {
                        "record_id": item.get("record_id", ""),
                        "content_id": item.get("content_id", "") or metrics.get("content_id", ""),
                        "account_key": item.get("account_key", ""),
                        "platform": item.get("platform", ""),
                        "published_at": item.get("published_at", ""),
                        "title": item.get("title", ""),
                        "selected_topic_title": item.get("selected_topic_title", ""),
                        "publish_url": item.get("publish_url", ""),
                        "publish_status": item.get("publish_status", ""),
                        "commercial_intent": tags.get("commercial_intent", ""),
                        "content_mix": tags.get("content_mix", ""),
                        "cta_source_code": tags.get("cta_source_code", ""),
                        "snapshot_label": metrics.get("snapshot_label", ""),
                        "snapshot_at": metrics.get("snapshot_at", ""),
                        "collection_date": metrics.get("collection_date", ""),
                        "published_date": metrics.get("published_date", "") or item.get("published_at", "")[:10],
                        "earnings_date": metrics.get("earnings_date", "") or metrics.get("data_date", ""),
                        "availability_status": metrics.get("availability_status", ""),
                        "revenue_status": metrics.get("revenue_status", "unknown"),
                        "platform_revenue_status": metrics.get("platform_revenue_status", "unknown"),
                        "revenue_amount_known": metrics.get("revenue_amount_known", False),
                        "topic_bucket": tags.get("topic_bucket", ""),
                        "title_style": tags.get("title_style", ""),
                        "hook_pattern": tags.get("hook_pattern", ""),
                        "content_angle": tags.get("content_angle", ""),
                        "structure_pattern": tags.get("structure_pattern", ""),
                        "cta_style": tags.get("cta_style", ""),
                        "evidence_density": tags.get("evidence_density", ""),
                        "emotion_tone": tags.get("emotion_tone", ""),
                        "test_focus": tags.get("test_focus", ""),
                        "impression_count": metrics.get("impression_count", 0),
                        "read_count": metrics.get("read_count", 0),
                        "recommendation_read_count": metrics.get("recommendation_read_count", 0),
                        "like_count": metrics.get("like_count", 0),
                        "wow_count": metrics.get("wow_count", 0),
                        "share_count": metrics.get("share_count", 0),
                        "comment_count": metrics.get("comment_count", 0),
                        "follower_gain": metrics.get("follower_gain", 0),
                        "read_rate": metrics.get("read_rate", 0),
                        "recommendation_ratio": metrics.get("recommendation_ratio", 0),
                        "interaction_rate": metrics.get("interaction_rate", 0),
                        "lead_count": metrics.get("lead_count", 0),
                        "revenue_amount": (
                            metrics.get("revenue_amount")
                            if metrics.get("revenue_amount_known", False)
                            else ""
                        ),
                    }
                )

    def _summarize_performance(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        published_count = len(rows)
        with_metrics = [item for item in rows if item.get("metrics_latest")]
        analysis_rows = [item for item in with_metrics if self._metric_row_is_analysis_eligible(item)]
        pending_t1_count = sum(
            1
            for item in with_metrics
            if self._metric_row_is_pending_t1(item)
        )
        collection_dates = [
            str(item.get("metrics_latest", {}).get("collection_date", "")).strip()
            for item in with_metrics
            if str(item.get("metrics_latest", {}).get("collection_date", "")).strip()
        ]
        latest_collection_date = max(collection_dates, default="")
        latest_earnings_dates = [
            str(
                item.get("metrics_latest", {}).get("earnings_date")
                or item.get("metrics_latest", {}).get("data_date", "")
            ).strip()
            for item in with_metrics
            if str(item.get("metrics_latest", {}).get("collection_date", "")).strip()
            == latest_collection_date
            and str(
                item.get("metrics_latest", {}).get("earnings_date")
                or item.get("metrics_latest", {}).get("data_date", "")
            ).strip()
        ]
        latest_earnings_date = max(latest_earnings_dates, default="")
        known_platform_revenue = 0.0
        platform_revenue_known_count = 0
        for item in with_metrics:
            metrics = item.get("metrics_latest", {})
            if latest_collection_date and str(metrics.get("collection_date", "")).strip() != latest_collection_date:
                continue
            if latest_earnings_date and str(metrics.get("earnings_date") or metrics.get("data_date", "")).strip() != latest_earnings_date:
                continue
            if infer_availability_status(item) != "available":
                continue
            if str(metrics.get("platform_revenue_status", "")).strip() != "available":
                continue
            platform_revenue = metrics.get("platform_revenue")
            if platform_revenue is None:
                continue
            try:
                known_platform_revenue += float(platform_revenue or 0.0)
                platform_revenue_known_count += 1
            except (TypeError, ValueError):
                continue
        top_by_reads = sorted(
            analysis_rows,
            key=lambda item: int(item.get("metrics_latest", {}).get("read_count", 0)),
            reverse=True,
        )[:5]
        with_economics = [item for item in analysis_rows if item.get("unit_economics")]

        def top_by_economics(metric: str) -> list[dict[str, Any]]:
            eligible = []
            for item in with_economics:
                economics = dict(item.get("unit_economics", {}))
                if metric in {"net_profit", "rpm", "total_revenue"} and not economics.get(
                    "revenue_analysis_eligible", False
                ):
                    continue
                if metric == "lead_revenue" and str(economics.get("revenue_status", "unknown")) in {
                    "pending_t1",
                    "unknown",
                    "not_available",
                }:
                    continue
                eligible.append(item)
            selected = sorted(
                eligible,
                key=lambda item: float(item.get("unit_economics", {}).get(metric, 0.0) or 0.0),
                reverse=True,
            )[:5]
            return [self._economics_summary_item(item, metric) for item in selected]

        tag_metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for item in analysis_rows:
            metrics = dict(item.get("metrics_latest", {}))
            tags = dict(item.get("experiment_tags", {}))
            for tag_name in ("topic_bucket", "title_style", "hook_pattern", "content_angle", "cta_style", "test_focus"):
                tag_value = str(tags.get(tag_name, "")).strip()
                if not tag_value:
                    continue
                tag_metrics[tag_name][tag_value].append(float(metrics.get("read_rate", 0) or 0))

        avg_read_rate_by_tag = {
            tag_name: {
                tag_value: round(sum(values) / len(values), 2)
                for tag_value, values in sorted(tag_values.items(), key=lambda pair: sum(pair[1]) / len(pair[1]), reverse=True)
            }
            for tag_name, tag_values in tag_metrics.items()
        }

        averages = {
            "read_rate": self._safe_average(item.get("metrics_latest", {}).get("read_rate", 0) for item in analysis_rows),
            "interaction_rate": self._safe_average(
                item.get("metrics_latest", {}).get("interaction_rate", 0) for item in analysis_rows
            ),
            "recommendation_ratio": self._safe_average(
                item.get("metrics_latest", {}).get("recommendation_ratio", 0) for item in analysis_rows
            ),
        }

        return {
            "published_count": published_count,
            "with_metrics_count": len(with_metrics),
            "analysis_eligible_count": len(analysis_rows),
            "pending_t1_count": pending_t1_count,
            "collection_date": latest_collection_date,
            "earnings_date": latest_earnings_date,
            "earnings_policy": "t_plus_1",
            "known_platform_revenue": round(known_platform_revenue, 4),
            "platform_revenue_known_count": platform_revenue_known_count,
            "account": self._build_account_summary(),
            "attribution_count": len(with_economics),
            "averages": averages,
            "top_by_reads": [
                {
                    "title": item.get("title", ""),
                    "platform": item.get("platform", ""),
                    "read_count": int(item.get("metrics_latest", {}).get("read_count", 0)),
                    "read_rate": float(item.get("metrics_latest", {}).get("read_rate", 0)),
                    "interaction_rate": float(item.get("metrics_latest", {}).get("interaction_rate", 0)),
                    "title_style": item.get("experiment_tags", {}).get("title_style", ""),
                    "content_angle": item.get("experiment_tags", {}).get("content_angle", ""),
                }
                for item in top_by_reads
            ],
            "top_by_net_profit": top_by_economics("net_profit"),
            "top_by_rpm": top_by_economics("rpm"),
            "top_by_lead_revenue": top_by_economics("lead_revenue"),
            "avg_read_rate_by_tag": avg_read_rate_by_tag,
        }

    def _metric_row_is_analysis_eligible(self, item: dict[str, Any]) -> bool:
        metrics = item.get("metrics_latest", {})
        if not isinstance(metrics, dict):
            return False
        if infer_availability_status(item) in {"pending_t1", "not_available"}:
            return False
        return bool(metrics.get("analysis_eligible", True))

    def _metric_row_is_pending_t1(self, item: dict[str, Any]) -> bool:
        return infer_availability_status(item) == "pending_t1"

    def _economics_summary_item(self, item: dict[str, Any], metric: str) -> dict[str, Any]:
        economics = dict(item.get("unit_economics", {}))
        return {
            "content_id": item.get("content_id", ""),
            "title": item.get("title", ""),
            "platform": item.get("platform", ""),
            "commercial_intent": item.get("experiment_tags", {}).get("commercial_intent", ""),
            "content_mix": item.get("experiment_tags", {}).get("content_mix", ""),
            "metric": metric,
            "value": float(economics.get(metric, 0.0) or 0.0),
            "net_profit": float(economics.get("net_profit", 0.0) or 0.0),
            "rpm": float(economics.get("rpm", 0.0) or 0.0),
            "lead_revenue": float(economics.get("lead_revenue", 0.0) or 0.0),
            "leads": int(economics.get("leads", 0) or 0),
            "revenue_status": economics.get("revenue_status", "unknown"),
            "platform_revenue_status": economics.get("platform_revenue_status", "unknown"),
            "revenue_analysis_eligible": bool(economics.get("revenue_analysis_eligible", False)),
        }

    def _build_benchmark_markdown(self, payload: dict[str, Any]) -> str:
        summary = dict(payload.get("summary", {}))
        accounts = list(payload.get("accounts", []))
        lines = [
            "# 对标库周报",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 对标账号数：{summary.get('account_count', 0)}",
            f"- 已记录观察文章：{summary.get('article_count', 0)}",
            "",
        ]
        if not accounts:
            lines.extend(
                [
                    "## 目前状态",
                    "",
                    f"当前对标库还是空的，请先补充 [benchmark_accounts.yaml]({self.benchmark_source_path.as_posix()})。",
                    "",
                    "建议先补 6 个账号：",
                    "- 机器人赛道 3 个：低粉高读、深度拆解、周报型各 1 个",
                    "- 储能/新能源赛道 3 个：行业判断、项目案例、公司跟踪各 1 个",
                    "",
                    "每个账号先补最近 5 篇文章标题、发布时间、关键词、已知阅读/互动数据。",
                ]
            )
            return "\n".join(lines) + "\n"

        lines.extend(["## 高频模式", ""])
        for label, block in (
            ("赛道分布", summary.get("track_distribution", {})),
            ("高频标题风格", summary.get("top_title_styles", {})),
            ("高频钩子", summary.get("top_hook_patterns", {})),
            ("高频关键词", summary.get("top_keywords", {})),
        ):
            lines.append(f"### {label}")
            if not block:
                lines.append("- 暂无数据")
            else:
                for key, value in block.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

        lines.extend(["## 账号清单", ""])
        for account in accounts:
            lines.append(f"### {account.get('name', '未命名账号')}")
            lines.append(f"- 赛道：{account.get('track', '') or '未填写'}")
            lines.append(f"- 定位：{account.get('positioning', '') or '未填写'}")
            lines.append(f"- 观察文章数：{len(account.get('observation_articles', []))}")
            lines.append(f"- 内容支柱：{', '.join(account.get('content_pillars', [])) or '未填写'}")
            top_titles = account.get("observation_articles", [])[:3]
            for article in top_titles:
                lines.append(
                    f"- 样本：{article.get('published_at', '')}｜{article.get('title', '')}｜{article.get('title_style', '')}"
                )
            lines.append("")
        return "\n".join(lines) + "\n"

    def _build_account_summary(self) -> dict[str, Any]:
        """账号级快照：累计收益 / 粉丝 / 总阅读 / 信用分 / 收益权益是否开通。

        数据来自 `creator_account_service`（头条首页看板 + 创作权益接口）。
        以前这里恒为「待回流」，是因为作品级采集根本覆盖不到账号级收益。
        """
        path = self.performance_dir / "account_snapshot.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
        account = latest.get("account") if isinstance(latest.get("account"), dict) else {}
        quality = latest.get("quality") if isinstance(latest.get("quality"), dict) else {}
        benefits = latest.get("benefits") if isinstance(latest.get("benefits"), dict) else {}
        revenue = latest.get("revenue") if isinstance(latest.get("revenue"), dict) else {}
        return {
            "observed_date": latest.get("observed_date", ""),
            "revenue_total": payload.get("revenue_total"),
            "revenue_delta_vs_last_observation": payload.get("revenue_delta_vs_last_observation"),
            "previous_observation_date": payload.get("previous_observation_date", ""),
            "revenue_yesterday_hint": revenue.get("yesterday_hint", ""),
            "revenue_source": revenue.get("total_source") or revenue.get("source", ""),
            # 收益接口给的、看板拿不到的口径（2026-09-17 接入）
            "income_window_days": (revenue.get("income") or {}).get("window_days"),
            "income_window": (revenue.get("income") or {}).get("window_income"),
            "income_month": (revenue.get("income") or {}).get("month_income"),
            "income_can_withdraw": (revenue.get("income") or {}).get("can_withdraw"),
            "income_daily_days_with_income": (revenue.get("income") or {}).get("daily_days_with_income"),
            "income_settle_breakdown": (revenue.get("income") or {}).get("settle_breakdown") or {},
            "fans": account.get("fans"),
            "total_reads": account.get("total_reads"),
            "credit_score": quality.get("credit_score"),
            "is_low_quality": quality.get("is_low_quality"),
            "can_earn_article_revenue": benefits.get("can_earn_article_revenue"),
            "activated_benefit_num": benefits.get("activated_benefit_num_declared"),
            "benefit_semantics_ok": benefits.get("semantics_ok"),
        }

    def _account_markdown_lines(self, summary: dict[str, Any]) -> list[str]:
        account = summary.get("account") if isinstance(summary.get("account"), dict) else {}
        if not account:
            return ["- 账号累计收益：未采集（account_snapshot.json 缺失，不按 0 处理）"]
        total = account.get("revenue_total")
        delta = account.get("revenue_delta_vs_last_observation")
        previous = account.get("previous_observation_date") or "上次观测"
        if isinstance(total, (int, float)):
            if isinstance(delta, (int, float)):
                delta_text = f"，较上次({previous}) {'+' if float(delta) >= 0 else ''}{float(delta):.2f} 元"
            else:
                delta_text = ""
            revenue_line = (
                f"- 账号累计收益：{float(total):.2f} CNY{delta_text}"
                f"（来源：{account.get('revenue_source', '未知')}）"
            )
        else:
            revenue_line = "- 账号累计收益：未采集到（不按 0 处理）"
        benefit = account.get("can_earn_article_revenue")
        benefit_text = "已开通" if benefit else ("未开通" if benefit is False else "未知")
        lines = [
            revenue_line,
            (
                f"- 账号看板：粉丝 {account.get('fans') if account.get('fans') is not None else '未知'}"
                f"｜总阅读(播放) {account.get('total_reads') if account.get('total_reads') is not None else '未知'}"
                f"｜信用分 {account.get('credit_score') if account.get('credit_score') is not None else '未知'}"
                f"｜低质量标记 {account.get('is_low_quality')}"
            ),
            f"- 文章创作收益权益：{benefit_text}（已开通权益数 {account.get('activated_benefit_num')}）",
        ]
        # 收益接口口径（看板没有）：这是判断"钱到底有没有在动"的真正指标。
        window_days = account.get("income_window_days")
        window_income = account.get("income_window")
        month_income = account.get("income_month")
        withdraw = account.get("income_can_withdraw")
        if window_income is not None or month_income is not None:
            def _money(value: object) -> str:
                return f"{float(value):.2f} 元" if isinstance(value, (int, float)) else "未知"

            lines.append(
                f"- 收益结构：近{window_days or 30}日 {_money(window_income)}"
                f"｜本月 {_money(month_income)}"
                f"｜可提现 {_money(withdraw)}"
            )
        breakdown = account.get("income_settle_breakdown") or {}
        if breakdown:
            parts = "｜".join(
                f"{name} {value}" for name, value in breakdown.items() if isinstance(value, (int, float))
            )
            if parts:
                lines.append(f"- 可提现构成：{parts}")
        days_with_income = account.get("income_daily_days_with_income")
        if isinstance(days_with_income, int):
            lines.append(
                f"- 近{window_days or 30}日里有收益的天数：{days_with_income}"
                "（配合逐日明细可判断收益是否在动；单篇归因头条不提供）"
            )
        return lines

    def _build_performance_markdown(self, payload: dict[str, Any]) -> str:
        summary = dict(payload.get("summary", {}))
        lines = [
            "# 内容增长复盘",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 已登记发布数：{summary.get('published_count', 0)}",
            f"- 已补齐数据文章数：{summary.get('with_metrics_count', 0)}",
            f"- 可纳入分析文章数：{summary.get('analysis_eligible_count', 0)}",
            f"- T+1 待结算文章数：{summary.get('pending_t1_count', 0)}",
            f"- 最近采集日：{summary.get('collection_date', '') or '未知'}",
            f"- 收益归属日：{summary.get('earnings_date', '') or '昨日'}",
            (
                f"- 平台收益：{float(summary.get('known_platform_revenue', 0.0) or 0.0):.2f} CNY"
                if int(summary.get('platform_revenue_known_count', 0) or 0) > 0
                else "- 平台收益：待回流（未知金额不按 0 CNY 处理）"
            ),
            *self._account_markdown_lines(summary),
            f"- 平均阅读率：{summary.get('averages', {}).get('read_rate', 0)}%",
            f"- 平均互动率：{summary.get('averages', {}).get('interaction_rate', 0)}%",
            f"- 平均推荐占比：{summary.get('averages', {}).get('recommendation_ratio', 0)}%",
            "",
            "## 表现最好的文章",
            "",
        ]
        top_by_reads = summary.get("top_by_reads", [])
        if not top_by_reads:
            lines.append("- 还没有补录任何发布后数据。")
        else:
            for item in top_by_reads:
                lines.append(
                    f"- {item.get('title', '')}｜{item.get('platform', '')}｜阅读 {item.get('read_count', 0)}｜阅读率 {item.get('read_rate', 0)}%｜互动率 {item.get('interaction_rate', 0)}%｜标题风格 {item.get('title_style', '')}"
                )
        lines.append("")
        lines.append("## 标签平均阅读率")
        lines.append("")
        tag_summary = summary.get("avg_read_rate_by_tag", {})
        if not tag_summary:
            lines.append("- 暂无可分析标签数据。")
        else:
            for tag_name, block in tag_summary.items():
                lines.append(f"### {tag_name}")
                for key, value in block.items():
                    lines.append(f"- {key}: {value}%")
                lines.append("")
        lines.append("## Revenue-oriented rankings")
        lines.append("")
        for heading, key, unit in (
            ("Net profit", "top_by_net_profit", " CNY"),
            ("Net RPM", "top_by_rpm", " CNY/1k impressions"),
            ("Lead revenue", "top_by_lead_revenue", " CNY"),
        ):
            lines.append(f"### {heading}")
            ranking = summary.get(key, [])
            if not ranking:
                lines.append("- No attributed content yet")
            else:
                for index, item in enumerate(ranking, start=1):
                    lines.append(
                        f"{index}. {item.get('title', '')} | {item.get('content_id', '')} | "
                        f"{float(item.get('value', 0.0) or 0.0):.2f}{unit} | leads={item.get('leads', 0)}"
                    )
            lines.append("")
        return "\n".join(lines) + "\n"

    def _normalize_benchmark_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        observation_articles = payload.get("observation_articles", [])
        source_feeds = payload.get("source_feeds", [])
        normalized_articles: list[dict[str, Any]] = []
        normalized_source_feeds: list[dict[str, Any]] = []
        for article in observation_articles if isinstance(observation_articles, list) else []:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title", "")).strip()
            hook_pattern = str(article.get("hook", "")).strip() or self._infer_hook_pattern(title)
            title_style = str(article.get("title_style", "")).strip() or self._infer_title_style(title)
            normalized_articles.append(
                {
                    "title": title,
                    "published_at": str(article.get("published_at", "")).strip(),
                    "keywords": [str(item).strip() for item in article.get("keywords", []) if str(item).strip()],
                    "hook_pattern": hook_pattern,
                    "title_style": title_style,
                    "angle": str(article.get("angle", "")).strip() or self._infer_content_angle(title),
                    "reads": int(article.get("reads", 0) or 0),
                    "likes": int(article.get("likes", 0) or 0),
                    "shares": int(article.get("shares", 0) or 0),
                    "comments": int(article.get("comments", 0) or 0),
                }
            )
        for source_feed in source_feeds if isinstance(source_feeds, list) else []:
            if not isinstance(source_feed, dict):
                continue
            urls = source_feed.get("urls")
            if isinstance(urls, str):
                urls = [urls]
            normalized_source_feeds.append(
                {
                    "label": str(source_feed.get("label", "")).strip(),
                    "type": str(source_feed.get("type", "html")).strip() or "html",
                    "collector_name": str(source_feed.get("collector_name", "")).strip(),
                    "same_domain_only": bool(source_feed.get("same_domain_only", False)),
                    "urls": [str(item).strip() for item in urls or [] if str(item).strip()],
                }
            )
        normalized_articles.sort(key=lambda item: item.get("published_at", ""), reverse=True)
        return {
            "key": str(payload.get("key", "")).strip(),
            "name": str(payload.get("name", "")).strip(),
            "platform": str(payload.get("platform", "wechat")).strip(),
            "track": str(payload.get("track", "")).strip(),
            "stage": str(payload.get("stage", "")).strip(),
            "followers_estimate": int(payload.get("followers_estimate", 0) or 0),
            "positioning": str(payload.get("positioning", "")).strip(),
            "content_pillars": [str(item).strip() for item in payload.get("content_pillars", []) if str(item).strip()],
            "notes": str(payload.get("notes", "")).strip(),
            "monitor_enabled": bool(payload.get("monitor_enabled", True)),
            "monitor_priority": int(payload.get("monitor_priority", 0) or 0),
            "source_feeds": normalized_source_feeds,
            "observation_articles": normalized_articles,
        }

    def _build_self_benchmark_report_payload(
        self,
        *,
        source_path: Path,
        articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        track_counter: Counter[str] = Counter()
        pattern_counter: Counter[str] = Counter()
        source_counter: Counter[str] = Counter()
        account_counter: Counter[str] = Counter()

        for article in articles:
            track = str(article.get("track", "")).strip() or "未分类"
            track_counter[track] += 1
            source_counter[str(article.get("source_type", "")).strip() or "unknown"] += 1
            account_key = str(article.get("account_key", "")).strip()
            if account_key:
                account_counter[account_key] += 1
            for pattern in article.get("inferred_patterns", []):
                pattern_name = str(pattern).strip()
                if pattern_name:
                    pattern_counter[pattern_name] += 1

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_path": str(source_path),
            "performance_ledger_path": str(self.performance_ledger_path),
            "summary": {
                "article_count": len(articles),
                "manual_count": int(source_counter.get("manual", 0)),
                "auto_count": int(source_counter.get("performance_ledger", 0)),
                "track_distribution": dict(track_counter.most_common()),
                "top_patterns": dict(pattern_counter.most_common(10)),
                "top_accounts": dict(account_counter.most_common(6)),
            },
            "articles": articles,
        }

    def _build_self_benchmark_markdown(self, payload: dict[str, Any]) -> str:
        summary = dict(payload.get("summary", {}))
        articles = list(payload.get("articles", []))
        lines = [
            "# 自有高表现样本库",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 样本总数：{summary.get('article_count', 0)}",
            f"- 手动样本数：{summary.get('manual_count', 0)}",
            f"- 自动回填样本数：{summary.get('auto_count', 0)}",
            "",
        ]
        for label, block in (
            ("赛道分布", summary.get("track_distribution", {})),
            ("高频有效模式", summary.get("top_patterns", {})),
            ("高频命中账号", summary.get("top_accounts", {})),
        ):
            lines.append(f"## {label}")
            lines.append("")
            if not block:
                lines.append("- 暂无数据")
            else:
                for key, value in block.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

        lines.extend(["## 样本清单", ""])
        if not articles:
            lines.append(f"- 当前为空，请先补充 [{self.self_benchmark_source_path.name}]({self.self_benchmark_source_path.as_posix()})。")
            lines.append("")
            return "\n".join(lines) + "\n"

        for article in articles[:12]:
            metrics_text = " / ".join(
                item
                for item in (
                    f"展现 {article.get('impression_count', 0)}" if int(article.get("impression_count", 0) or 0) else "",
                    f"阅读 {article.get('read_count', 0)}" if int(article.get("read_count", 0) or 0) else "",
                    f"阅读率 {article.get('read_rate', 0)}%" if float(article.get("read_rate", 0) or 0) else "",
                )
                if item
            ) or "暂无指标"
            lines.extend(
                [
                    f"### {article.get('title', '未命名样本')}",
                    f"- 赛道：{article.get('track', '') or '未分类'}",
                    f"- 来源：{article.get('source_type', '') or 'unknown'}",
                    f"- 账号：{article.get('account_key', '') or '未填写'}",
                    f"- 指标：{metrics_text}",
                ]
            )
            if article.get("inferred_patterns"):
                lines.append(f"- 可复用模式：{'；'.join(article.get('inferred_patterns', [])[:5])}")
            if article.get("notes"):
                lines.append(f"- 备注：{'；'.join(article.get('notes', [])[:4])}")
            if article.get("run_dir"):
                lines.append(f"- 批次目录：{article.get('run_dir', '')}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _load_auto_self_benchmark_hits(self) -> list[dict[str, Any]]:
        rows = list(self._load_ledger().values())
        normalized_articles: list[dict[str, Any]] = []
        for row in rows:
            candidate = self._build_self_benchmark_candidate(row)
            if not candidate or not candidate.get("qualified"):
                continue
            normalized_articles.append(candidate["normalized_article"])
        return normalized_articles

    def _load_collected_reference_samples(self) -> list[dict[str, Any]]:
        if not self.self_benchmark_collection_json_path.exists():
            return []
        try:
            payload = json.loads(self.self_benchmark_collection_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []
        selected = payload.get("selected_candidates", [])
        if not isinstance(selected, list):
            return []
        result: list[dict[str, Any]] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            result.append(item)
        return result

    def _build_self_benchmark_candidate(self, row: dict[str, Any]) -> dict[str, Any]:
        metrics = dict(row.get("metrics_latest", {}))
        if not any(
            int(metrics.get(field, 0) or 0) > 0
            for field in ("impression_count", "read_count", "recommendation_read_count", "like_count", "share_count")
        ):
            return {}
        title = str(row.get("title", "")).strip()
        if not title:
            return {}
        experiment_tags = dict(row.get("experiment_tags", {}))
        raw_tags = self._coerce_str_list(experiment_tags.get("raw_tags", []))
        selected_topic_title = str(row.get("selected_topic_title", "")).strip()
        topic_bucket = str(experiment_tags.get("topic_bucket", "")).strip() or selected_topic_title
        notes = []
        if int(metrics.get("impression_count", 0) or 0):
            notes.append(f"展现 {int(metrics.get('impression_count', 0) or 0)}")
        if int(metrics.get("read_count", 0) or 0):
            notes.append(f"阅读 {int(metrics.get('read_count', 0) or 0)}")
        if float(metrics.get("read_rate", 0) or 0):
            notes.append(f"阅读率 {round(float(metrics.get('read_rate', 0) or 0), 2)}%")
        if int(metrics.get("share_count", 0) or 0):
            notes.append(f"分享 {int(metrics.get('share_count', 0) or 0)}")
        if int(metrics.get("comment_count", 0) or 0):
            notes.append(f"评论 {int(metrics.get('comment_count', 0) or 0)}")
        high_performance_flags = self._self_benchmark_high_performance_flags(metrics)
        qualified = bool(high_performance_flags)
        inferred_patterns = self._infer_self_benchmark_patterns(
            title=title,
            track=topic_bucket,
            keywords=raw_tags,
            notes=notes,
        )
        normalized_article = self._normalize_self_benchmark_article(
            {
                "key": row.get("record_id", ""),
                "track": self.infer_title_playbook_track(
                    " ".join([title, selected_topic_title, *raw_tags])
                ),
                "account_key": row.get("account_key", ""),
                "platform": row.get("platform", ""),
                "published_at": str(row.get("published_at", "")).strip()[:10],
                "title": title,
                "run_dir": row.get("run_dir", ""),
                "keywords": raw_tags,
                "notes": notes,
                "inferred_patterns": inferred_patterns,
                "observed_metrics": metrics,
            },
            source_type="performance_ledger",
        )
        if not normalized_article:
            return {}
        normalized_article["qualified"] = qualified
        normalized_article["high_performance_flags"] = high_performance_flags
        normalized_article["record_id"] = str(row.get("record_id", "")).strip()
        normalized_article["selected_topic_title"] = selected_topic_title
        normalized_article["publish_url"] = str(row.get("publish_url", "")).strip()
        normalized_article["publish_status"] = str(row.get("publish_status", "")).strip()
        normalized_article["experiment_tags"] = experiment_tags
        return {
            "qualified": qualified,
            "normalized_article": normalized_article,
            "record_id": normalized_article["record_id"],
            "account_key": str(normalized_article.get("account_key", "")).strip(),
            "track": str(normalized_article.get("track", "")).strip(),
            "benchmark_score": int(normalized_article.get("benchmark_score", 0) or 0),
        }

    def _build_online_reference_candidate(self, topic: Any, *, account_key: str) -> dict[str, Any]:
        title = str(getattr(topic, "title", "") or "").strip()
        if not title:
            return {}
        summary_text = str(getattr(topic, "summary", "") or "").strip()
        snippet_text = str(getattr(topic, "snippet", "") or "").strip()
        matched_keywords = self._coerce_str_list(list(getattr(topic, "matched_keywords", []) or []))
        extracted_numbers = self._coerce_str_list(list(getattr(topic, "extracted_numbers", []) or []))
        matched_industries = self._coerce_str_list(list(getattr(topic, "matched_industries", []) or []))
        resolved_track = str(getattr(topic, "title_playbook_track", "") or "").strip() or self.infer_title_playbook_track(
            " ".join([title, summary_text, snippet_text, *matched_keywords, *matched_industries])
        )
        notes = []
        source_name = str(getattr(topic, "source", "") or "").strip()
        if source_name:
            notes.append(f"线上来源 {source_name}")
        if summary_text:
            notes.append(summary_text[:120])
        if extracted_numbers:
            notes.append(f"公开数字线索：{'、'.join(extracted_numbers[:4])}")
        high_performance_flags = self._online_reference_high_performance_flags(topic)
        inferred_patterns = self._infer_self_benchmark_patterns(
            title=title,
            track=resolved_track,
            keywords=[*matched_keywords[:6], *matched_industries[:3]],
            notes=[*notes, *high_performance_flags],
        )
        normalized_article = self._normalize_self_benchmark_article(
            {
                "key": hashlib.md5(f"{source_name}|{title}|{getattr(topic, 'url', '')}".encode("utf-8")).hexdigest()[:12],
                "track": resolved_track,
                "account_key": account_key,
                "platform": "web",
                "published_at": str(getattr(topic, "published_at", "") or "").strip()[:10],
                "title": title,
                "run_dir": "",
                "keywords": self._unique_texts([*matched_keywords[:6], *matched_industries[:3]]),
                "notes": notes[:5],
                "inferred_patterns": inferred_patterns,
                "observed_metrics": {},
            },
            source_type="online_reference",
        )
        if not normalized_article:
            return {}
        online_scores = {
            "total_score": int(getattr(topic, "total_score", 0) or 0),
            "read_score": int(getattr(topic, "read_score", 0) or 0),
            "save_score": int(getattr(topic, "save_score", 0) or 0),
            "discussion_score": int(getattr(topic, "discussion_score", 0) or 0),
            "heat_score": int(getattr(topic, "heat_score", 0) or 0),
            "quality_score": int(getattr(topic, "quality_score", 0) or 0),
            "title_potential_score": int(getattr(topic, "title_potential_score", 0) or 0),
            "evidence_strength": int(getattr(topic, "evidence_strength", 0) or 0),
        }
        benchmark_score = (
            online_scores["total_score"] * 8
            + online_scores["title_potential_score"] * 4
            + online_scores["quality_score"] * 3
            + online_scores["evidence_strength"] * 2
            + (12 if bool(getattr(topic, "is_high_read", False)) else 0)
            + (10 if bool(getattr(topic, "is_high_save", False)) else 0)
            + (8 if bool(getattr(topic, "is_high_discussion", False)) else 0)
        )
        normalized_article.update(
            {
                "external_url": str(getattr(topic, "url", "") or "").strip(),
                "source_name": source_name,
                "summary_text": summary_text,
                "snippet_text": snippet_text,
                "online_scores": online_scores,
                "qualified": bool(high_performance_flags),
                "high_performance_flags": high_performance_flags,
                "benchmark_score": int(benchmark_score),
            }
        )
        return normalized_article

    def _online_reference_high_performance_flags(self, topic: Any) -> list[str]:
        total_score = int(getattr(topic, "total_score", 0) or 0)
        title_potential_score = int(getattr(topic, "title_potential_score", 0) or 0)
        quality_score = int(getattr(topic, "quality_score", 0) or 0)
        evidence_strength = int(getattr(topic, "evidence_strength", 0) or 0)
        read_score = int(getattr(topic, "read_score", 0) or 0)
        save_score = int(getattr(topic, "save_score", 0) or 0)
        discussion_score = int(getattr(topic, "discussion_score", 0) or 0)
        flags: list[str] = []
        if total_score >= 80:
            flags.append(f"在线综合热度高（{total_score}）")
        elif total_score >= 72:
            flags.append(f"在线综合热度达标（{total_score}）")
        if bool(getattr(topic, "is_high_read", False)) or read_score >= 72:
            flags.append(f"在线阅读潜力高（{read_score}）")
        if bool(getattr(topic, "is_high_save", False)) or save_score >= 72:
            flags.append(f"在线收藏潜力高（{save_score}）")
        if bool(getattr(topic, "is_high_discussion", False)) or discussion_score >= 72:
            flags.append(f"在线讨论潜力高（{discussion_score}）")
        if title_potential_score >= 80:
            flags.append(f"标题势能高（{title_potential_score}）")
        if quality_score >= 80:
            flags.append(f"样本质量高（{quality_score}）")
        if evidence_strength >= 80:
            flags.append(f"证据强度高（{evidence_strength}）")
        return self._unique_texts(flags)

    def _build_self_benchmark_collection_payload(
        self,
        *,
        account_key: str,
        requested_track: str,
        selected_candidates: list[dict[str, Any]],
        all_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        track_counter: Counter[str] = Counter()
        account_counter: Counter[str] = Counter()
        qualified_count = 0
        promoted_count = 0
        for item in all_candidates:
            track = str(item.get("track", "")).strip() or "未分类"
            account = str(item.get("account_key", "")).strip() or "未填写"
            track_counter[track] += 1
            account_counter[account] += 1
            if item.get("qualified"):
                qualified_count += 1
            if item.get("already_promoted"):
                promoted_count += 1

        summary = {
            "candidate_count": len(all_candidates),
            "qualified_count": qualified_count,
            "selected_count": len(selected_candidates),
            "already_promoted_count": promoted_count,
            "track_distribution": dict(track_counter.most_common()),
            "account_distribution": dict(account_counter.most_common()),
        }
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": account_key,
            "requested_track": requested_track,
            "source_path": str(self.performance_ledger_path),
            "summary": summary,
            "selected_candidates": selected_candidates,
            "all_candidates": all_candidates[:30],
        }

    def _build_self_benchmark_collection_markdown(self, payload: dict[str, Any]) -> str:
        summary = dict(payload.get("summary", {}))
        selected_candidates = list(payload.get("selected_candidates", []))
        lines = [
            "# 高表现参考样本采集报告",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 当前账号：{payload.get('account_key', '') or '全部'}",
            f"- 当前赛道：{payload.get('requested_track', '') or '全部'}",
            f"- 采集模式：{payload.get('mode', 'local_performance')}",
            f"- 候选总数：{summary.get('candidate_count', 0)}",
            f"- 命中高表现样本：{summary.get('qualified_count', 0)}",
            f"- 已在样本库中：{summary.get('already_promoted_count', 0)}",
            "",
            "## 采集结果",
            "",
        ]
        if not selected_candidates:
            lines.append("- 暂无符合条件的高表现样本。")
            return "\n".join(lines) + "\n"
        for candidate in selected_candidates:
            metrics = candidate.get("observed_metrics", {})
            online_scores = dict(candidate.get("online_scores", {}))
            metrics_text = " / ".join(
                item
                for item in (
                    f"展现 {int(metrics.get('impression_count', 0) or 0)}",
                    f"阅读 {int(metrics.get('read_count', 0) or 0)}",
                    f"阅读率 {float(metrics.get('read_rate', 0) or 0)}%",
                )
                if item
            )
            if not metrics_text and online_scores:
                metrics_text = " / ".join(
                    item
                    for item in (
                        f"综合分 {int(online_scores.get('total_score', 0) or 0)}",
                        f"读潜 {int(online_scores.get('read_score', 0) or 0)}",
                        f"藏潜 {int(online_scores.get('save_score', 0) or 0)}",
                        f"议潜 {int(online_scores.get('discussion_score', 0) or 0)}",
                    )
                    if item
                )
            lines.extend(
                [
                    f"### {candidate.get('title', '未命名样本')}",
                    f"- 账号：{candidate.get('account_key', '') or '未填写'}",
                    f"- 赛道：{candidate.get('track', '') or '未分类'}",
                    f"- 指标：{metrics_text}",
                    f"- 得分：{candidate.get('benchmark_score', 0)}",
                    f"- 命中理由：{'；'.join(candidate.get('high_performance_flags', [])[:5]) or '综合分较高'}",
                    f"- 可复用模式：{'；'.join(candidate.get('inferred_patterns', [])[:5]) or '暂无'}",
                ]
            )
            if candidate.get("source_name"):
                lines.append(f"- 来源：{candidate.get('source_name', '')}")
            if candidate.get("external_url"):
                lines.append(f"- 链接：{candidate.get('external_url', '')}")
            if candidate.get("run_dir"):
                lines.append(f"- 批次目录：{candidate.get('run_dir', '')}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _self_benchmark_identity(self, payload: dict[str, Any]) -> str:
        return (
            str(payload.get("external_url", "")).strip()
            or str(payload.get("run_dir", "")).strip()
            or str(payload.get("title", "")).strip()
        )

    def _self_benchmark_high_performance_flags(self, metrics: dict[str, Any]) -> list[str]:
        impression_count = self._metric_int(metrics, "impression_count")
        read_count = self._metric_int(metrics, "read_count")
        recommendation_read_count = self._metric_int(metrics, "recommendation_read_count")
        like_count = self._metric_int(metrics, "like_count")
        wow_count = self._metric_int(metrics, "wow_count")
        share_count = self._metric_int(metrics, "share_count")
        comment_count = self._metric_int(metrics, "comment_count")
        follower_gain = self._metric_int(metrics, "follower_gain")
        read_rate = float(metrics.get("read_rate", 0) or 0)
        interaction_rate = float(metrics.get("interaction_rate", 0) or 0)
        flags: list[str] = []
        if impression_count >= 50000:
            flags.append(f"展现破5万（{impression_count}）")
        elif impression_count >= 10000:
            flags.append(f"展现破1万（{impression_count}）")
        if read_count >= 1000:
            flags.append(f"阅读破1000（{read_count}）")
        elif read_count >= 300:
            flags.append(f"阅读破300（{read_count}）")
        if read_rate >= 8.0 and read_count >= 100:
            flags.append(f"阅读率显著偏高（{round(read_rate, 2)}%）")
        if recommendation_read_count >= 300:
            flags.append(f"推荐流读数较高（{recommendation_read_count}）")
        if like_count >= 20 or share_count >= 10 or comment_count >= 10 or wow_count >= 10:
            flags.append("互动指标较强")
        if follower_gain >= 10:
            flags.append(f"涨粉明显（{follower_gain}）")
        if interaction_rate >= 3.0 and read_count >= 100:
            flags.append(f"互动率较高（{round(interaction_rate, 2)}%）")
        return self._unique_texts(flags)

    @staticmethod
    def _extract_concrete_hooks(title: str) -> list[str]:
        """从标题中提取具象钩子要素（数字/量化、公司机构、具体地名/场景）。

        用于「高展现标题要素回灌」：把历史高展现标题里已被验证的具象要素
        沉淀进 title_playbook，供新一轮标题生成优先复用，避免再产出
        全盘空洞的情绪模板标题。正则口径与 title_agent 保持一致。
        """
        element_set: set[str] = set()
        text = str(title or "")
        # 数字与量化短语（如 6.2亿元、460万台、10倍、300%）——量化短语优先于裸数字
        for match in re.finditer(
            r"\d+(?:\.\d+)?\s*(?:亿|万|倍|个|家|款|台|条)|\d+(?:\.\d+)?\s*%?",
            text,
        ):
            token = match.group(0).strip()
            if 1 <= len(token) <= 8:
                element_set.add(token)
        # 公司/机构名（含常见后缀）——非贪婪，避免吞并整句连词
        for match in re.finditer(
            r"[\u4e00-\u9fa5A-Za-z]{2,14}?(?:公司|集团|科技|证券|银行|研究院|工厂|基地|机场|园区|实验室|机器人|人形)",
            text,
        ):
            token = match.group(0).strip()
            if 2 <= len(token) <= 14:
                element_set.add(token)
        # 具体地名（省/市/县/高新技术区等 + 常见后缀）
        for match in re.finditer(
            r"[\u4e00-\u9fa5]{2,6}(?:省|市|县|区|高新区|经开区|国家的|地区)",
            text,
        ):
            token = match.group(0).strip()
            if 2 <= len(token) <= 10:
                element_set.add(token)
        return [token for token in element_set]

    def _normalize_self_benchmark_article(
        self,
        payload: dict[str, Any],
        *,
        source_type: str,
    ) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        if not title:
            return {}

        metrics = dict(payload.get("observed_metrics", {}))
        impression_count = self._metric_int(metrics, "impression_count")
        read_count = self._metric_int(metrics, "read_count")
        recommendation_read_count = self._metric_int(metrics, "recommendation_read_count")
        like_count = self._metric_int(metrics, "like_count")
        wow_count = self._metric_int(metrics, "wow_count")
        share_count = self._metric_int(metrics, "share_count")
        comment_count = self._metric_int(metrics, "comment_count")
        follower_gain = self._metric_int(metrics, "follower_gain")
        read_rate = float(metrics.get("read_rate", 0) or 0)
        if impression_count > 0 and read_count > 0 and read_rate <= 0:
            read_rate = round(read_count * 100 / impression_count, 2)

        keywords = self._coerce_str_list(payload.get("keywords", []))
        notes = self._coerce_str_list(payload.get("notes", []))
        run_dir = self._resolve_run_dir_value(str(payload.get("run_dir", "")).strip())
        track = str(payload.get("track", "")).strip() or self.infer_title_playbook_track(
            " ".join([title, *keywords, *notes])
        )
        inferred_patterns = self._coerce_str_list(payload.get("inferred_patterns", []))
        if not inferred_patterns:
            inferred_patterns = self._infer_self_benchmark_patterns(
                title=title,
                track=track,
                keywords=keywords,
                notes=notes,
            )
        benchmark_score = self._score_self_benchmark_article(
            impression_count=impression_count,
            read_count=read_count,
            recommendation_read_count=recommendation_read_count,
            like_count=like_count,
            wow_count=wow_count,
            share_count=share_count,
            comment_count=comment_count,
            follower_gain=follower_gain,
            read_rate=read_rate,
        )
        observed_metrics = {
            "impression_count": impression_count,
            "read_count": read_count,
            "recommendation_read_count": recommendation_read_count,
            "like_count": like_count,
            "wow_count": wow_count,
            "share_count": share_count,
            "comment_count": comment_count,
            "follower_gain": follower_gain,
            "read_rate": read_rate,
        }
        enabled = self._normalize_self_benchmark_enabled(payload.get("enabled", True))
        status = str(payload.get("status", "")).strip().lower() or ("active" if enabled else "disabled")
        return {
            "key": str(payload.get("key", "")).strip() or hashlib.md5(title.encode("utf-8")).hexdigest()[:12],
            "enabled": enabled,
            "status": status,
            "source_type": source_type,
            "track": track or "科技商业",
            "account_key": str(payload.get("account_key", "")).strip(),
            "platform": str(payload.get("platform", "toutiao")).strip() or "toutiao",
            "published_at": str(payload.get("published_at", "")).strip(),
            "title": title,
            "run_dir": run_dir,
            "keywords": keywords,
            "notes": notes,
            "inferred_patterns": inferred_patterns,
            "concrete_hooks": self._extract_concrete_hooks(title),
            "observed_metrics": observed_metrics,
            "impression_count": impression_count,
            "read_count": read_count,
            "recommendation_read_count": recommendation_read_count,
            "like_count": like_count,
            "wow_count": wow_count,
            "share_count": share_count,
            "comment_count": comment_count,
            "follower_gain": follower_gain,
            "read_rate": read_rate,
            "benchmark_score": benchmark_score,
        }

    def _is_active_self_benchmark_entry(self, payload: dict[str, Any]) -> bool:
        enabled = self._normalize_self_benchmark_enabled(payload.get("enabled", True))
        if not enabled:
            return False
        status = str(payload.get("status", "")).strip().lower()
        return status not in {"draft", "template", "pending_metrics", "disabled", "inactive"}

    def _normalize_self_benchmark_enabled(self, value: Any) -> bool:
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized not in {"", "0", "false", "no", "off", "disabled"}
        return bool(value)

    def _score_self_benchmark_article(
        self,
        *,
        impression_count: int,
        read_count: int,
        recommendation_read_count: int,
        like_count: int,
        wow_count: int,
        share_count: int,
        comment_count: int,
        follower_gain: int,
        read_rate: float,
    ) -> int:
        return int(
            read_count * 2
            + recommendation_read_count
            + impression_count / 200
            + like_count * 6
            + wow_count * 5
            + share_count * 9
            + comment_count * 8
            + follower_gain * 15
            + read_rate * 20
        )

    def _infer_self_benchmark_patterns(
        self,
        *,
        title: str,
        track: str,
        keywords: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> list[str]:
        normalized = " ".join([str(title).strip(), str(track).strip(), *self._coerce_str_list(keywords or []), *self._coerce_str_list(notes or [])])
        patterns: list[str] = []
        if any(token in normalized for token in ("机场", "工厂", "车间", "仓库", "电站", "港口", "项目")):
            patterns.append("场景先行开头")
        if any(token in normalized for token in ("日本", "美国", "欧洲", "海外", "东京", "中国")):
            patterns.append("跨区域或跨国对比")
        if any(token in normalized for token in ("利润", "毛利", "回款", "兑现", "订单", "交付", "分化")):
            patterns.append("利润迁移或兑现判断")
        if re.search(r"\d", normalized):
            patterns.append("具体数字配具体主体")
        if any(token in normalized for token in ("变量", "后续", "跟踪", "接下来", "真正要看")):
            patterns.append("结尾保留跟踪变量")
        if "储能" in normalized or "新能源" in normalized:
            patterns.append("项目进度连接现金流判断")
        if "机器人" in normalized or "具身" in normalized:
            patterns.append("从演示转向交付验证")
        return self._unique_texts(patterns)

    def _resolve_run_dir_value(self, run_dir_value: str) -> str:
        value = str(run_dir_value or "").strip()
        if not value:
            return ""
        path = Path(value)
        if not path.is_absolute():
            path = self.settings.project_root / value
        return str(path)

    def _coerce_str_list(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]

    def _metric_int(self, payload: dict[str, Any], key: str) -> int:
        return int(payload.get(key, 0) or 0)

    def _unique_texts(self, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = str(value).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _infer_topic_bucket(self, text: str) -> str:
        normalized = str(text or "")
        rules = [
            ("robot", ("机器人", "具身", "谐波", "减速器", "人形")),
            ("energy_storage", ("储能", "电芯", "PCS", "逆变器", "电站", "GWh")),
            ("new_energy", ("光伏", "风电", "新能源车", "锂电", "充电桩")),
            ("smart_equipment", ("自动化", "工业母机", "数控", "智能装备", "产线")),
            ("ai_hardware", ("算力", "芯片", "服务器", "边缘AI", "机器人芯片")),
            ("international", ("国际", "美国", "欧洲", "俄罗斯", "乌克兰", "中东", "关税", "制裁", "外交", "局势")),
            ("sports", ("体育", "足球", "篮球", "国足", "英超", "欧冠", "西甲", "NBA", "世界杯", "奥运")),
        ]
        for bucket, keywords in rules:
            if any(keyword in normalized for keyword in keywords):
                return bucket
        return "general_tech"

    def _infer_title_style(self, title: str) -> str:
        normalized = str(title or "")
        if re.search(r"[？?]", normalized):
            return "question_hook"
        if any(token in normalized for token in ("周报", "日报", "月报", "复盘", "回顾")):
            return "digest_hook"
        if re.search(r"\d", normalized):
            return "data_hook"
        if any(token in normalized for token in ("不是", "而是", "但", "却", "真相", "背后")):
            return "contrast_hook"
        return "insight_hook"

    def _infer_hook_pattern(self, title: str) -> str:
        normalized = str(title or "")
        if re.search(r"[？?]", normalized):
            return "ask_reader"
        if any(token in normalized for token in ("为什么", "为何", "怎么", "谁在")):
            return "curiosity_gap"
        if any(token in normalized for token in ("暴涨", "爆发", "巨变", "反转", "黄昏")):
            return "emotion_trigger"
        if any(token in normalized for token in ("回顾", "复盘", "周报")):
            return "periodic_summary"
        return "direct_judgment"

    def _infer_content_angle(self, text: str) -> str:
        normalized = str(text or "")
        rules = [
            ("industry_chain", ("产业链", "零部件", "上游", "中游", "下游")),
            ("company_breakdown", ("公司", "企业", "厂商", "龙头", "巨头")),
            ("event_interpretation", ("发布会", "大会", "政策", "财报", "融资", "订单")),
            ("profit_logic", ("利润", "毛利", "盈利", "变现", "现金流")),
            ("trend_judgment", ("趋势", "判断", "拐点", "节奏", "窗口")),
        ]
        for angle, keywords in rules:
            if any(keyword in normalized for keyword in keywords):
                return angle
        return "industry_observation"

    def _infer_structure_pattern(self, article: str, title: str) -> str:
        normalized = str(article or "")
        if any(token in title for token in ("周报", "日报", "月报")):
            return "weekly_digest"
        heading_count = len(re.findall(r"(?m)^\s*#{1,6}\s+", normalized))
        if heading_count >= 3:
            return "sectioned_analysis"
        paragraph_count = len([part for part in re.split(r"\n\s*\n+", normalized) if part.strip()])
        if paragraph_count >= 7:
            return "multi_paragraph_analysis"
        return "compact_commentary"

    def _infer_cta_style(self, article: str) -> str:
        normalized = str(article or "")
        if "回复关键词" in normalized:
            return "keyword_reply"
        if any(token in normalized for token in ("欢迎留言", "欢迎评论", "你怎么看", "欢迎分享")):
            return "discussion_prompt"
        if "关注" in normalized:
            return "follow_prompt"
        return "soft_close"

    def _infer_evidence_density(self, article: str) -> str:
        normalized = str(article or "")
        if not normalized:
            return "low"
        numeric_hits = len(re.findall(r"\d+(?:\.\d+)?(?:%|％|万|亿|GWh|MWh|MW|亿元|亿美元|台|家|次)?", normalized))
        length = max(1, len(re.sub(r"\s+", "", normalized)))
        ratio = numeric_hits / length * 1000
        if ratio >= 8:
            return "high"
        if ratio >= 3:
            return "medium"
        return "low"

    def _infer_emotion_tone(self, title: str, article: str) -> str:
        normalized = f"{title}\n{article}"
        if any(token in normalized for token in ("风险", "黄昏", "卷不动", "警惕", "回调")):
            return "risk_alert"
        if any(token in normalized for token in ("机会", "加速", "爆发", "拐点", "突破")):
            return "opportunity_forward"
        return "calm_analysis"

    def _suggest_test_focus(
        self,
        *,
        title_style: str,
        evidence_density: str,
        cta_style: str,
        structure_pattern: str,
    ) -> str:
        if title_style == "insight_hook":
            return "test stronger title contrast"
        if evidence_density == "low":
            return "add more verified numbers"
        if cta_style == "soft_close":
            return "test clearer comment CTA"
        if structure_pattern == "compact_commentary":
            return "test stronger subhead structure"
        return "keep current pattern and compare cover/title variants"

    def _derive_metrics(self, payload: dict[str, int]) -> dict[str, float | str]:
        impressions = int(payload.get("impression_count", 0) or 0)
        reads = int(payload.get("read_count", 0) or 0)
        recommendation_reads = int(payload.get("recommendation_read_count", 0) or 0)
        likes = int(payload.get("like_count", 0) or 0)
        wows = int(payload.get("wow_count", 0) or 0)
        shares = int(payload.get("share_count", 0) or 0)
        comments = int(payload.get("comment_count", 0) or 0)
        interaction_total = likes + wows + shares + comments
        read_rate = round((reads / impressions) * 100, 2) if impressions > 0 else 0.0
        recommendation_ratio = round((recommendation_reads / reads) * 100, 2) if reads > 0 else 0.0
        interaction_rate = round((interaction_total / reads) * 100, 2) if reads > 0 else 0.0
        return {
            "interaction_total": interaction_total,
            "read_rate": read_rate,
            "recommendation_ratio": recommendation_ratio,
            "interaction_rate": interaction_rate,
            "read_rate_status": self._rate_status(read_rate, 5.0),
            "interaction_rate_status": self._rate_status(interaction_rate, 3.0),
        }

    def _performance_snapshot_sort_key(self, item: dict[str, Any]) -> tuple[int, str, str]:
        label_order = {"1h": 0, "6h": 1, "24h": 2, "72h": 3}
        label = str(item.get("snapshot_label", "")).strip()
        return (
            label_order.get(label, 4),
            str(item.get("snapshot_at", "")),
            str(item.get("snapshot_date", "")),
        )

    def _metrics_latest_sort_key(self, item: dict[str, Any]) -> str:
        return str(item.get("snapshot_at") or item.get("snapshot_date") or "")

    def _date_portion(self, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                return ""

    def _rate_status(self, value: float, good_threshold: float) -> str:
        if value >= good_threshold * 1.4:
            return "strong"
        if value >= good_threshold:
            return "qualified"
        return "weak"

    def _safe_average(self, values: Any) -> float:
        items = [float(value or 0) for value in values]
        if not items:
            return 0.0
        return round(sum(items) / len(items), 2)

    def _is_notice_style_headline(self, title: str, keywords: list[str]) -> bool:
        text = str(title or "").strip()
        if not text:
            return False
        notice_tokens = (
            "中标",
            "公示",
            "公告",
            "回顾",
            "发布会",
            "论坛",
            "峰会",
            "大会",
            "成功举办",
            "举行",
        )
        if re.search(r"^(?:中标|招标|成交|开标|公示|公告|回顾|发布会|论坛|峰会|大会)[\s|｜:：\-]", text):
            return True
        if ("|" in text or "｜" in text) and re.search(r"\d+(?:\.\d+)?\s*(?:元/Wh|元/W|万元|亿元|MW|GW|GWh|MWh)", text):
            return True
        if any(token in text for token in notice_tokens):
            if any(token in text for token in ("背后", "谁", "为什么", "真正", "先受益", "先赚钱")):
                return False
            if self._has_subject_anchor_in_title(text, keywords):
                return False
            return True
        return False

    def _has_subject_anchor_in_title(self, title: str, keywords: list[str]) -> bool:
        text = str(title or "").strip()
        if not text:
            return False
        if re.search(
            r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:科技|能源|汽车|股份|集团|电气|智能|机器人|半导体|电子|时代|精工|制造|材料|电池|重工|工业|光伏|储能|动力|机电|控股|装备|技术)",
            text,
        ):
            return True
        if any(token in text for token in ("机场", "电站", "电厂", "工厂", "仓库", "车间", "港口", "物流中心")):
            return True
        hits = 0
        for keyword in keywords[:4]:
            token = str(keyword or "").strip()
            if token and token in text:
                hits += 1
        return hits >= 2

    def _unique_reason_list(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _build_record_id(self, *, platform: str, run_dir: Path) -> str:
        digest = hashlib.sha1(f"{platform}|{run_dir.resolve()}".encode("utf-8")).hexdigest()[:16]
        return f"{platform}_{digest}"

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_json_list(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def build_content_identity_payload(
        self,
        *,
        platform: str,
        run_dir: Path,
        title: str = "",
        topic_title: str = "",
        published_at: str = "",
        commercial_intent: str = "medium",
        content_mix: str = "professional",
        cta_variant: str = "",
        cta_source_code: str = "",
        title_variant: str = "",
        cover_variant: str = "",
        platform_variant: str = "",
        topic_bucket: str = "",
    ) -> dict[str, Any]:
        """构建内容级归因所需的 identity payload，供上层服务注册。"""
        from toutiao_content_factory.services.revenue_attribution_service import (
            RevenueAttributionService,
        )

        service = RevenueAttributionService(self.settings.output_dir, self.logger)
        identity = service.build_content_identity(
            account_key=self.settings.account_key,
            platform=platform,
            run_dir=run_dir,
            topic_title=topic_title,
            title=title,
            published_at=published_at or datetime.now().isoformat(timespec="seconds"),
            commercial_intent=commercial_intent,
            content_mix=content_mix,
            cta_variant=cta_variant,
            cta_source_code=cta_source_code,
            title_variant=title_variant,
            cover_variant=cover_variant,
            platform_variant=platform_variant,
            topic_bucket=topic_bucket,
        )
        return {
            "content_id": identity.content_id,
            "commercial_intent": identity.commercial_intent,
            "content_mix": identity.content_mix,
            "cta_variant": identity.cta_variant,
            "cta_source_code": identity.cta_source_code,
            "title_variant": identity.title_variant,
            "cover_variant": identity.cover_variant,
            "platform_variant": identity.platform_variant,
            "topic_bucket": identity.topic_bucket,
        }
