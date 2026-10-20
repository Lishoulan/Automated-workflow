"""收益归因与内容级经营分析服务。

该模块补齐“流量→线索→成交→复盘”闭环所需的数据基础：

- 为每次发布生成稳定 content_id，串联 run_dir / 平台 / 账号；
- 记录内容生产成本（API、图片、人工）与变现事件（线索、成交、赞助）；
- 支持 1h/6h/24h/72h 多时点表现快照与单位经济性计算；
- 提供选题商业意图评分、内容组合统计与低收益冷却建议；
- 输出可直接用于决策的经营报表，而不是仅展示阅读量。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.services.t1_policy import infer_availability_status


@dataclass(frozen=True)
class ContentIdentity:
    """内容级唯一标识与元数据。"""

    content_id: str
    account_key: str
    platform: str
    run_dir: str
    topic_title: str
    title: str
    published_at: str
    commercial_intent: str = "medium"
    content_mix: str = "professional"
    cta_variant: str = ""
    cta_source_code: str = ""
    title_variant: str = ""
    cover_variant: str = ""
    platform_variant: str = ""
    topic_bucket: str = ""


@dataclass
class CostRecord:
    """单篇内容生产成本。"""

    api_cost: float = 0.0
    image_cost: float = 0.0
    labor_minutes: float = 0.0
    labor_rate_per_hour: float = 0.0
    currency: str = "CNY"

    # Old records and manual amounts are declarations, never proof of actual fees.
    basis: str = "declared"
    evidence_ref: str = ""
    coverage_complete: bool = False

    @property
    def labor_cost(self) -> float:
        return self.labor_minutes * self.labor_rate_per_hour / 60.0

    @property
    def total_cost(self) -> float:
        return round(self.api_cost + self.image_cost + self.labor_cost, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_cost": self.api_cost,
            "image_cost": self.image_cost,
            "labor_minutes": self.labor_minutes,
            "labor_rate_per_hour": self.labor_rate_per_hour,
            "labor_cost": round(self.labor_cost, 4),
            "total_cost": self.total_cost,
            "currency": self.currency,
            "basis": self.basis,
            "evidence_ref": self.evidence_ref,
            "coverage_complete": self.coverage_complete,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CostRecord":
        return cls(
            api_cost=float(payload.get("api_cost", 0.0) or 0.0),
            image_cost=float(payload.get("image_cost", 0.0) or 0.0),
            labor_minutes=float(payload.get("labor_minutes", 0.0) or 0.0),
            labor_rate_per_hour=float(payload.get("labor_rate_per_hour", 0.0) or 0.0),
            currency=str(payload.get("currency", "CNY")),
            basis=str(payload.get("basis", "declared")),
            evidence_ref=str(payload.get("evidence_ref", "")),
            coverage_complete=payload.get("coverage_complete") is True,
        )


@dataclass
class RevenueEvent:
    """单次变现事件。"""

    event_type: str
    amount: float
    currency: str = "CNY"
    source_article_id: str = ""
    lead_id: str = ""
    product_key: str = ""
    channel: str = ""
    note: str = ""
    occurred_at: str = ""
    event_id: str = ""

    def stable_event_id(self) -> str:
        """Return a deterministic key so retries cannot double-count revenue."""
        if self.event_id.strip():
            return self.event_id.strip()
        event_type = str(self.event_type).strip().lower()
        # A payment amount is mutable while a lead/deal identity is not. This
        # lets a retry or a corrected CRM amount replace the original event.
        amount_key = "" if self.lead_id.strip() and event_type in {"lead", "lead_value", "deal"} else str(self.amount)
        raw = "|".join(
            (
                event_type,
                amount_key,
                str(self.currency).strip().upper(),
                str(self.source_article_id).strip(),
                str(self.lead_id).strip(),
                str(self.product_key).strip(),
                str(self.channel).strip(),
            )
        )
        return f"rev_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "amount": self.amount,
            "currency": self.currency,
            "source_article_id": self.source_article_id,
            "lead_id": self.lead_id,
            "product_key": self.product_key,
            "channel": self.channel,
            "note": self.note,
            "occurred_at": self.occurred_at or datetime.now().isoformat(timespec="seconds"),
            "event_id": self.stable_event_id(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RevenueEvent":
        return cls(
            event_type=str(payload.get("event_type", "")),
            amount=float(payload.get("amount", 0.0) or 0.0),
            currency=str(payload.get("currency", "CNY")),
            source_article_id=str(payload.get("source_article_id", "")),
            lead_id=str(payload.get("lead_id", "")),
            product_key=str(payload.get("product_key", "")),
            channel=str(payload.get("channel", "")),
            note=str(payload.get("note", "")),
            occurred_at=str(payload.get("occurred_at", "")),
            event_id=str(payload.get("event_id", "")),
        )


@dataclass
class PerformanceSnapshot:
    """单个时间点的表现快照。"""

    snapshot_label: str
    snapshot_at: str
    collection_date: str = ""
    impression_count: int = 0
    read_count: int = 0
    recommendation_read_count: int = 0
    like_count: int = 0
    share_count: int = 0
    comment_count: int = 0
    follower_gain: int = 0
    lead_count: int = 0
    revenue_amount: float = 0.0
    currency: str = "CNY"
    # Keep publication, observation, and earnings dates separate. A dashboard
    # read today can represent earnings from yesterday.
    published_date: str = ""
    earnings_date: str = ""
    availability_status: str = "available"
    revenue_status: str = "unknown"
    platform_revenue_status: str = "unknown"
    revenue_amount_known: bool = False
    platform_revenue: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_label": self.snapshot_label,
            "snapshot_at": self.snapshot_at,
            "observed_at": self.snapshot_at,
            "collection_date": self.collection_date,
            "published_date": self.published_date,
            "earnings_date": self.earnings_date,
            "data_date": self.earnings_date,
            "availability_status": self.availability_status,
            "revenue_status": self.revenue_status,
            "platform_revenue_status": self.platform_revenue_status,
            "revenue_amount_known": self.revenue_amount_known,
            "platform_revenue": self.platform_revenue,
            "impression_count": self.impression_count,
            "read_count": self.read_count,
            "recommendation_read_count": self.recommendation_read_count,
            "like_count": self.like_count,
            "share_count": self.share_count,
            "comment_count": self.comment_count,
            "follower_gain": self.follower_gain,
            "lead_count": self.lead_count,
            "revenue_amount": self.revenue_amount,
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PerformanceSnapshot":
        return cls(
            snapshot_label=str(payload.get("snapshot_label", "")),
            snapshot_at=str(payload.get("snapshot_at") or payload.get("observed_at", "")),
            collection_date=str(payload.get("collection_date", "")),
            published_date=str(payload.get("published_date", "")),
            earnings_date=str(payload.get("earnings_date") or payload.get("data_date", "")),
            availability_status=str(payload.get("availability_status", "available")),
            revenue_status=str(payload.get("revenue_status", "unknown")),
            platform_revenue_status=str(payload.get("platform_revenue_status", "unknown")),
            revenue_amount_known=bool(payload.get("revenue_amount_known", False)),
            platform_revenue=(
                float(payload.get("platform_revenue"))
                if payload.get("platform_revenue") is not None
                else None
            ),
            impression_count=int(payload.get("impression_count", 0) or 0),
            read_count=int(payload.get("read_count", 0) or 0),
            recommendation_read_count=int(payload.get("recommendation_read_count", 0) or 0),
            like_count=int(payload.get("like_count", 0) or 0),
            share_count=int(payload.get("share_count", 0) or 0),
            comment_count=int(payload.get("comment_count", 0) or 0),
            follower_gain=int(payload.get("follower_gain", 0) or 0),
            lead_count=int(payload.get("lead_count", 0) or 0),
            revenue_amount=float(payload.get("revenue_amount", 0.0) or 0.0),
            currency=str(payload.get("currency", "CNY")),
        )


@dataclass
class UnitEconomics:
    """单篇内容的单位经济性指标。"""

    content_id: str
    total_cost: float
    platform_revenue: float
    lead_revenue: float
    deal_revenue: float
    total_revenue: float
    net_profit: float | None
    rpm: float | None
    lead_cost: float | None
    leads: int
    currency: str = "CNY"
    platform_revenue_status: str = "available"
    revenue_status: str = "available"
    revenue_amount_known: bool = True
    analysis_eligible: bool = True
    revenue_analysis_eligible: bool = True
    cost_status: str = "unknown"
    profit_analysis_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "total_cost": round(self.total_cost, 4),
            "platform_revenue": round(self.platform_revenue, 4),
            "lead_revenue": round(self.lead_revenue, 4),
            "deal_revenue": round(self.deal_revenue, 4),
            "total_revenue": round(self.total_revenue, 4),
            "net_profit": None if self.net_profit is None else round(self.net_profit, 4),
            "rpm": None if self.rpm is None else round(self.rpm, 4),
            "lead_cost": None if self.lead_cost is None else round(self.lead_cost, 4),
            "leads": self.leads,
            "currency": self.currency,
            "platform_revenue_status": self.platform_revenue_status,
            "revenue_status": self.revenue_status,
            "revenue_amount_known": self.revenue_amount_known,
            "analysis_eligible": self.analysis_eligible,
            "revenue_analysis_eligible": self.revenue_analysis_eligible,
            "cost_status": self.cost_status,
            "profit_analysis_eligible": self.profit_analysis_eligible,
        }


class RevenueAttributionService:
    """收益归因与经营分析服务。"""

    SNAPSHOT_LABELS = ("1h", "6h", "24h", "72h")
    COMMERCIAL_INTENTS = ("low", "medium", "high")
    CONTENT_MIXES = ("traffic", "professional", "conversion")
    EXPERIMENT_DIMENSIONS = (
        "platform",
        "topic_bucket",
        "commercial_intent",
        "content_mix",
        "cta_variant",
        "cta_source_code",
        "title_variant",
        "cover_variant",
        "platform_variant",
    )
    TOPIC_BUCKET_RULES = (
        ("industrial_automation", ("工业自动化", "工业机器人", "机器视觉", "PLC", "伺服", "传感器")),
        ("ai_hardware", ("AI硬件", "算力", "GPU", "芯片", "服务器", "CPO", "边缘计算")),
        ("robotics", ("人形机器人", "具身智能", "机器人", "灵巧手", "减速器", "执行器", "机械臂")),
        ("energy_storage", ("储能", "光伏", "风电", "锂电", "电池", "逆变器", "电站", "并网")),
        ("international", ("国际", "美国", "欧洲", "俄罗斯", "乌克兰", "关税", "制裁", "外交")),
        ("sports", ("体育", "比赛", "联赛", "球员", "球队", "决赛", "转会")),
    )

    def __init__(self, output_dir: Path, logger: logging.Logger) -> None:
        self.output_dir = Path(output_dir)
        self.logger = logger
        self.attribution_dir = self.output_dir / "growth_ops" / "revenue_attribution"
        self.content_index_path = self.attribution_dir / "content_index.json"
        self.cost_ledger_path = self.attribution_dir / "cost_ledger.json"
        self.revenue_ledger_path = self.attribution_dir / "revenue_ledger.json"
        self.snapshot_ledger_path = self.attribution_dir / "snapshot_ledger.json"
        self.unit_economics_path = self.attribution_dir / "unit_economics.json"
        self.cooling_report_path = self.attribution_dir / "cooling_report.json"
        self.mix_report_path = self.attribution_dir / "content_mix_report.json"
        self.experiment_report_path = self.attribution_dir / "experiment_report.json"
        self.business_report_path = self.attribution_dir / "business_report.md"
        self.ensure_directories()

    def ensure_directories(self) -> None:
        self.attribution_dir.mkdir(parents=True, exist_ok=True)

    # ── identity ───────────────────────────────────────────────

    def build_content_identity(
        self,
        *,
        account_key: str,
        platform: str,
        run_dir: Path,
        topic_title: str,
        title: str,
        published_at: str,
        commercial_intent: str = "medium",
        content_mix: str = "professional",
        cta_variant: str = "",
        cta_source_code: str = "",
        title_variant: str = "",
        cover_variant: str = "",
        platform_variant: str = "",
        topic_bucket: str = "",
    ) -> ContentIdentity:
        normalized_platform = str(platform or "").strip().lower() or "toutiao"
        normalized_account = str(account_key or "").strip().lower() or "default"
        resolved_run_dir = Path(run_dir).resolve()
        digest_input = f"{normalized_account}|{normalized_platform}|{resolved_run_dir}"
        digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:16]
        content_id = f"ci_{digest}"
        intent = self._normalize_enum(commercial_intent, self.COMMERCIAL_INTENTS, "medium")
        mix = self._normalize_enum(content_mix, self.CONTENT_MIXES, "professional")
        resolved_bucket = self.topic_bucket_for(
            topic_title=topic_title,
            topic_bucket=topic_bucket,
        )
        return ContentIdentity(
            content_id=content_id,
            account_key=normalized_account,
            platform=normalized_platform,
            run_dir=str(resolved_run_dir),
            topic_title=str(topic_title or "").strip(),
            title=str(title or "").strip(),
            published_at=str(published_at or ""),
            commercial_intent=intent,
            content_mix=mix,
            cta_variant=str(cta_variant or "").strip(),
            cta_source_code=str(cta_source_code or "").strip(),
            title_variant=str(title_variant or "").strip(),
            cover_variant=str(cover_variant or "").strip(),
            platform_variant=str(platform_variant or "").strip(),
            topic_bucket=resolved_bucket,
        )

    def register_content(self, identity: ContentIdentity) -> Path:
        index = self._load_json(self.content_index_path, default={})
        if not isinstance(index, dict):
            index = {}
        existing = index.get(identity.content_id, {})
        if not isinstance(existing, dict):
            existing = {}
        def _keep_existing(key: str, value: str) -> str:
            current = str(existing.get(key, "") or "").strip()
            candidate = str(value or "").strip()
            if current and not candidate:
                return current
            if key in {"commercial_intent", "content_mix"} and current and candidate in {
                "medium",
                "professional",
            } and current != candidate:
                return current
            if key == "topic_bucket" and current and candidate == "general" and current != "general":
                return current
            return candidate
        record = {
            **existing,
            "content_id": identity.content_id,
            "account_key": _keep_existing("account_key", identity.account_key),
            "platform": _keep_existing("platform", identity.platform),
            "run_dir": _keep_existing("run_dir", identity.run_dir),
            "topic_title": _keep_existing("topic_title", identity.topic_title),
            "title": _keep_existing("title", identity.title),
            "published_at": _keep_existing("published_at", identity.published_at),
            "commercial_intent": _keep_existing("commercial_intent", identity.commercial_intent),
            "content_mix": _keep_existing("content_mix", identity.content_mix),
            "cta_variant": _keep_existing("cta_variant", identity.cta_variant),
            "cta_source_code": _keep_existing("cta_source_code", identity.cta_source_code),
            "title_variant": _keep_existing("title_variant", identity.title_variant),
            "cover_variant": _keep_existing("cover_variant", identity.cover_variant),
            "platform_variant": _keep_existing("platform_variant", identity.platform_variant),
            "topic_bucket": _keep_existing("topic_bucket", identity.topic_bucket),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        index[identity.content_id] = record
        self._save_json(self.content_index_path, index)
        return self.content_index_path

    def topic_bucket_for(
        self,
        topic_title: str = "",
        *,
        topic_bucket: str = "",
        title: str = "",
        summary: str = "",
    ) -> str:
        """Return a stable, coarse topic bucket for cross-run comparisons."""
        explicit = str(topic_bucket or "").strip().lower()
        aliases = {
            "robot": "robotics",
            "humanoid_robot": "robotics",
            "humanoid": "robotics",
            "new_energy": "energy_storage",
            "storage": "energy_storage",
            "ai": "ai_hardware",
            "chip": "ai_hardware",
            "industrial": "industrial_automation",
            "global": "international",
            "sport": "sports",
        }
        if explicit:
            explicit = aliases.get(explicit, explicit)
            known_buckets = {name for name, _ in self.TOPIC_BUCKET_RULES}
            if explicit in known_buckets:
                return explicit

        text = " ".join(
            str(value or "").strip().lower()
            for value in (topic_title, title, summary)
            if str(value or "").strip()
        )
        for bucket, keywords in self.TOPIC_BUCKET_RULES:
            if any(str(keyword).lower() in text for keyword in keywords):
                return bucket
        if explicit:
            return re.sub(r"[^a-z0-9_]+", "_", explicit).strip("_") or "general"
        return "general"

    # ── cost ───────────────────────────────────────────────────

    def record_cost(self, content_id: str, cost: CostRecord) -> Path:
        self._require_content_id(content_id)
        self._validate_cost(cost)
        ledger = self._load_json(self.cost_ledger_path, default={})
        if not isinstance(ledger, dict):
            ledger = {}
        ledger[content_id] = cost.to_dict()
        self._save_json(self.cost_ledger_path, ledger)
        return self.cost_ledger_path

    def get_cost(self, content_id: str) -> CostRecord:
        ledger = self._load_json(self.cost_ledger_path, default={})
        if not isinstance(ledger, dict):
            return CostRecord(basis="unknown")
        payload = ledger.get(content_id, {})
        if not isinstance(payload, dict) or not payload:
            return CostRecord(basis="unknown")
        return CostRecord.from_dict(payload)

    # ── revenue ────────────────────────────────────────────────

    def record_revenue_event(
        self,
        content_id: str,
        event: RevenueEvent,
        *,
        replace_existing: bool = False,
    ) -> Path:
        self._require_content_id(content_id)
        self._validate_revenue_event(event)
        ledger = self._load_json(self.revenue_ledger_path, default={})
        if not isinstance(ledger, dict):
            ledger = {}
        events = [item for item in ledger.get(content_id, []) if isinstance(item, dict)]
        event_payload = event.to_dict()
        event_id = event.stable_event_id()
        existing_index = next(
            (
                index
                for index, item in enumerate(events)
                if RevenueEvent.from_dict(item).stable_event_id() == event_id
            ),
            None,
        )
        if existing_index is None:
            events.append(event_payload)
        elif replace_existing:
            events[existing_index] = event_payload
        else:
            # Retries from a webhook, CLI command, or payment callback must be harmless.
            self.logger.debug("Duplicate revenue event ignored: content_id=%s event_id=%s", content_id, event_id)
        ledger[content_id] = events
        self._save_json(self.revenue_ledger_path, ledger)
        return self.revenue_ledger_path

    def get_revenue_events(self, content_id: str) -> list[RevenueEvent]:
        ledger = self._load_json(self.revenue_ledger_path, default={})
        if not isinstance(ledger, dict):
            return []
        rows = ledger.get(content_id, [])
        return [RevenueEvent.from_dict(item) for item in rows if isinstance(item, dict)]

    # ── snapshots ──────────────────────────────────────────────

    def record_snapshot(self, content_id: str, snapshot: PerformanceSnapshot) -> Path:
        ledger = self._load_json(self.snapshot_ledger_path, default={})
        if not isinstance(ledger, dict):
            ledger = {}
        snapshots = [item for item in ledger.get(content_id, []) if isinstance(item, dict)]
        incoming = snapshot.to_dict()
        existing = next(
            (item for item in snapshots if str(item.get("snapshot_label", "")) == snapshot.snapshot_label),
            None,
        )
        snapshots = [item for item in snapshots if str(item.get("snapshot_label", "")) != snapshot.snapshot_label]
        snapshots.append(self._merge_snapshot_payload(existing, incoming))
        snapshots.sort(key=self._snapshot_sort_key)
        ledger[content_id] = snapshots
        self._save_json(self.snapshot_ledger_path, ledger)
        return self.snapshot_ledger_path

    def get_snapshots(self, content_id: str) -> list[PerformanceSnapshot]:
        ledger = self._load_json(self.snapshot_ledger_path, default={})
        if not isinstance(ledger, dict):
            return []
        rows = ledger.get(content_id, [])
        parsed = [PerformanceSnapshot.from_dict(item) for item in rows if isinstance(item, dict)]
        return sorted(parsed, key=lambda item: self._snapshot_sort_key(item.to_dict()))

    def find_content_record_by_run_dir(self, run_dir: str | Path) -> dict[str, Any] | None:
        """Find an identity without depending on platform spelling."""
        target = self._normalize_path_value(run_dir)
        if not target:
            return None
        index = self._load_json(self.content_index_path, default={})
        for record in index.values():
            if not isinstance(record, dict):
                continue
            if self._normalize_path_value(record.get("run_dir")) == target:
                return dict(record)
        return None

    def resolve_content_id(
        self,
        source_content_id: str = "",
        cta_source_code: str = "",
        *,
        run_dir: str | Path = "",
        source_article_id: str = "",
        account_key: str = "",
        platform: str = "",
    ) -> str | None:
        """Resolve a lead source without guessing across ambiguous CTA codes.

        An explicit content ID or run directory is authoritative. A CTA source
        code is accepted only when it maps to exactly one registered content;
        this keeps a reused campaign code from silently receiving revenue from
        the wrong article.
        """
        index = self._load_json(self.content_index_path, default={})
        if not isinstance(index, dict):
            return None

        explicit_id = str(source_content_id or source_article_id).strip()
        if explicit_id:
            record = index.get(explicit_id)
            if isinstance(record, dict):
                return explicit_id
            return None

        if str(run_dir or "").strip():
            record = self.find_content_record_by_run_dir(run_dir)
            if isinstance(record, dict):
                return str(record.get("content_id", "")).strip() or None

        normalized_cta = str(cta_source_code or "").strip().casefold()
        if not normalized_cta:
            return None
        candidates: list[str] = []
        for content_id, record in index.items():
            if not isinstance(record, dict):
                continue
            if normalized_cta != str(record.get("cta_source_code", "")).strip().casefold():
                continue
            if account_key and str(record.get("account_key", "")).strip() != str(account_key).strip():
                continue
            if platform and str(record.get("platform", "")).strip().lower() != str(platform).strip().lower():
                continue
            candidates.append(str(content_id))
        return candidates[0] if len(candidates) == 1 else None

    def register_published_run(
        self,
        *,
        account_key: str,
        platform: str,
        run_dir: Path,
        publish_dirname: str = "publish",
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
        topic: dict[str, Any] | None = None,
        result_payload: dict[str, Any] | None = None,
        estimated_cost: float | None = None,
        image_cost: float = 0.0,
        labor_minutes: float = 0.0,
        labor_rate_per_hour: float = 0.0,
        currency: str = "CNY",
    ) -> ContentIdentity:
        """Register one successful run and persist its known production cost.

        ``workflow_result.json`` is the source of truth for the accumulated API
        estimate. Explicit arguments still win, which is useful for backfills
        and tests that do not have a workflow artifact.
        """
        resolved_run_dir = Path(run_dir).resolve()
        publish_dir = resolved_run_dir / (str(publish_dirname or "publish").strip() or "publish")
        publish_meta = self._load_json(publish_dir / "publish_meta.json", default={})
        workflow_payload = self._load_json(resolved_run_dir / "workflow_result.json", default={})
        if not isinstance(publish_meta, dict):
            publish_meta = {}
        if not isinstance(workflow_payload, dict):
            workflow_payload = {}
        result_payload = result_payload if isinstance(result_payload, dict) else {}
        topic_payload = topic if isinstance(topic, dict) else publish_meta.get("selected_topic", {})
        if not isinstance(topic_payload, dict):
            topic_payload = {}

        effective_title = (
            str(title or "").strip()
            or str(publish_meta.get("selected_title", "")).strip()
            or str(result_payload.get("title", "")).strip()
        )
        effective_topic_title = str(topic_title or "").strip() or str(topic_payload.get("title", "")).strip()
        effective_published_at = (
            str(published_at or "").strip()
            or str(result_payload.get("published_at", "")).strip()
            or datetime.now().isoformat(timespec="seconds")
        )
        identity = self.build_content_identity(
            account_key=account_key,
            platform=platform,
            run_dir=resolved_run_dir,
            topic_title=effective_topic_title,
            title=effective_title,
            published_at=effective_published_at,
            commercial_intent=str(topic_payload.get("commercial_intent", commercial_intent) or commercial_intent),
            content_mix=str(topic_payload.get("content_mix", content_mix) or content_mix),
            cta_variant=str(topic_payload.get("cta_variant", cta_variant) or cta_variant),
            cta_source_code=str(topic_payload.get("cta_source_code", cta_source_code) or cta_source_code),
            title_variant=str(topic_payload.get("title_variant", title_variant) or title_variant),
            cover_variant=str(topic_payload.get("cover_variant", cover_variant) or cover_variant),
            platform_variant=str(topic_payload.get("platform_variant", platform_variant) or platform_variant),
            topic_bucket=str(topic_payload.get("topic_bucket", topic_bucket) or topic_bucket),
        )
        self.register_content(identity)

        cost_has_evidence = estimated_cost is not None
        if estimated_cost is None:
            if "estimated_cost" in workflow_payload:
                estimated_cost = workflow_payload.get("estimated_cost", 0.0)
                cost_has_evidence = True
            elif "estimated_cost" in result_payload:
                estimated_cost = result_payload.get("estimated_cost", 0.0)
                cost_has_evidence = True
            else:
                estimated_cost = 0.0
        existing_cost = self.get_cost(identity.content_id)
        cost = CostRecord(
            api_cost=float(estimated_cost or 0.0),
            image_cost=float(image_cost or 0.0),
            labor_minutes=float(labor_minutes or 0.0),
            labor_rate_per_hour=float(labor_rate_per_hour or 0.0),
            currency=currency,
            basis="estimated" if cost_has_evidence else "unknown",
        )
        if existing_cost.basis != "actual" and (cost_has_evidence or cost.total_cost > 0 or existing_cost.total_cost <= 0):
            self.record_cost(identity.content_id, cost)
        return identity

    def summarize_revenue_events(self, content_id: str) -> dict[str, float | int]:
        events = self.get_revenue_events(content_id)
        lead_events = [event for event in events if event.event_type in {"lead", "lead_value"}]
        lead_revenue = sum(event.amount for event in events if event.event_type == "lead_value")
        deal_revenue = sum(event.amount for event in events if event.event_type in {"deal", "sponsorship"})
        platform_revenue = sum(event.amount for event in events if event.event_type == "platform_revenue")
        return {
            "event_count": len(events),
            "lead_count": len(lead_events),
            "lead_revenue": round(lead_revenue, 4),
            "deal_revenue": round(deal_revenue, 4),
            "platform_revenue": round(platform_revenue, 4),
            "platform_revenue_known": any(event.event_type == "platform_revenue" for event in events),
            "revenue_amount": round(lead_revenue + deal_revenue + platform_revenue, 4),
        }

    # ── unit economics ─────────────────────────────────────────

    def _content_availability_status(
        self,
        content_id: str,
        *,
        latest_snapshot: PerformanceSnapshot | None = None,
        content_record: dict[str, Any] | None = None,
    ) -> str:
        if content_record is None:
            content_index = self._load_json(self.content_index_path, default={})
            content_record = (
                content_index.get(content_id, {})
                if isinstance(content_index, dict)
                else {}
            )
        policy_record: dict[str, Any] = dict(content_record) if isinstance(content_record, dict) else {}
        if latest_snapshot is None:
            latest_snapshot = self._latest_snapshot(content_id)
        if latest_snapshot is not None:
            snapshot_payload = latest_snapshot.to_dict()
            policy_record["metrics_latest"] = snapshot_payload
            if not any(
                str(snapshot_payload.get(field, "")).strip()
                for field in ("published_date", "earnings_date", "data_date", "collection_date")
            ):
                policy_record.pop("published_at", None)
                policy_record.pop("published_date", None)
        return infer_availability_status(policy_record)

    def compute_unit_economics(
        self,
        content_id: str,
        *,
        platform_revenue: float = 0.0,
    ) -> UnitEconomics:
        cost = self.get_cost(content_id)
        self._validate_cost(cost)
        revenue_summary = self.summarize_revenue_events(content_id)
        existing_economics = self._load_json(self.unit_economics_path, default={})
        existing_payload = existing_economics.get(content_id, {}) if isinstance(existing_economics, dict) else {}
        if platform_revenue == 0.0:
            platform_revenue = float(revenue_summary.get("platform_revenue", 0.0) or 0.0)
        lead_revenue = float(revenue_summary.get("lead_revenue", 0.0) or 0.0)
        deal_revenue = float(revenue_summary.get("deal_revenue", 0.0) or 0.0)
        latest_snapshot = self._latest_snapshot(content_id)
        content_index = self._load_json(self.content_index_path, default={})
        content_record = (
            content_index.get(content_id, {})
            if isinstance(content_index, dict)
            else {}
        )
        inferred_availability = self._content_availability_status(
            content_id,
            latest_snapshot=latest_snapshot,
            content_record=content_record if isinstance(content_record, dict) else {},
        )
        if platform_revenue == 0.0 and latest_snapshot is not None and latest_snapshot.platform_revenue is not None:
            platform_revenue = float(latest_snapshot.platform_revenue or 0.0)
        if platform_revenue == 0.0 and isinstance(existing_payload, dict):
            platform_revenue = float(existing_payload.get("platform_revenue", 0.0) or 0.0)
        total_revenue = platform_revenue + lead_revenue + deal_revenue
        impressions = latest_snapshot.impression_count if latest_snapshot else 0
        leads = int(revenue_summary.get("lead_count", 0) or 0)
        lead_cost = (cost.total_cost / leads) if leads > 0 else None
        stored_platform_status = (
            str(existing_payload.get("platform_revenue_status", "")).strip()
            if isinstance(existing_payload, dict)
            else ""
        )
        platform_revenue_known = bool(revenue_summary.get("platform_revenue_known")) or platform_revenue > 0
        if stored_platform_status == "available":
            platform_revenue_known = True
        if latest_snapshot is not None and (
            latest_snapshot.platform_revenue_status == "available"
            and latest_snapshot.platform_revenue is not None
        ):
            platform_revenue_known = True
        if inferred_availability == "pending_t1" or (
            latest_snapshot is not None and latest_snapshot.platform_revenue_status == "pending_t1"
        ):
            platform_status = "pending_t1"
        elif inferred_availability == "not_available" or (
            latest_snapshot is not None and latest_snapshot.platform_revenue_status == "not_available"
        ):
            platform_status = "not_available"
        elif platform_revenue_known:
            platform_status = "available"
        else:
            platform_status = "unknown"

        has_other_revenue = lead_revenue > 0 or deal_revenue > 0 or bool(revenue_summary.get("event_count"))
        if platform_status in {"pending_t1", "not_available"}:
            revenue_status = platform_status
        elif platform_revenue_known or has_other_revenue:
            revenue_status = "available" if platform_revenue_known else "partial"
        elif latest_snapshot is not None and latest_snapshot.revenue_status in {"available", "partial"}:
            revenue_status = latest_snapshot.revenue_status
        elif latest_snapshot is not None and latest_snapshot.revenue_status in {"pending_t1", "not_available"}:
            revenue_status = latest_snapshot.revenue_status
        else:
            revenue_status = "unknown"
        # ``partial`` means lead/deal revenue is known while platform earnings
        # are still absent. It must not be used as a complete profit number.
        revenue_amount_known = revenue_status == "available"
        analysis_eligible = bool(
            inferred_availability not in {"pending_t1", "not_available"}
        )
        revenue_analysis_eligible = bool(
            analysis_eligible and platform_status == "available"
        )
        currencies = {event.currency.upper() for event in self.get_revenue_events(content_id)}
        if latest_snapshot is not None and latest_snapshot.platform_revenue is not None:
            currencies.add(latest_snapshot.currency.upper())
        currency_consistent = not currencies or currencies == {cost.currency.upper()}
        if not currency_consistent:
            revenue_analysis_eligible = revenue_amount_known = False
            revenue_status = "currency_mismatch"
        profit_analysis_eligible = bool(
            revenue_analysis_eligible and revenue_amount_known
            and cost.basis == "actual" and cost.evidence_ref.strip() and cost.coverage_complete
        )
        net_profit = total_revenue - cost.total_cost if profit_analysis_eligible else None
        rpm = net_profit / impressions * 1000 if net_profit is not None and impressions > 0 else None
        economics = UnitEconomics(
            content_id=content_id,
            total_cost=cost.total_cost,
            platform_revenue=platform_revenue,
            lead_revenue=lead_revenue,
            deal_revenue=deal_revenue,
            total_revenue=total_revenue,
            net_profit=net_profit,
            rpm=rpm,
            lead_cost=lead_cost,
            leads=leads,
            currency=cost.currency,
            platform_revenue_status=platform_status,
            revenue_status=revenue_status,
            revenue_amount_known=revenue_amount_known,
            analysis_eligible=analysis_eligible,
            revenue_analysis_eligible=revenue_analysis_eligible,
            cost_status=cost.basis,
            profit_analysis_eligible=profit_analysis_eligible,
        )
        ledger = self._load_json(self.unit_economics_path, default={})
        ledger[content_id] = economics.to_dict()
        self._save_json(self.unit_economics_path, ledger)
        return economics

    def build_unit_economics_ranking(
        self,
        *,
        sort_by: str = "net_profit",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to limit per currency, ordered only within each currency."""
        metric_name = str(sort_by or "net_profit").strip()
        if metric_name not in {"net_profit", "rpm", "lead_revenue", "leads", "total_revenue"}:
            metric_name = "net_profit"
        index = self._load_json(self.content_index_path, default={})
        stored_economics = self._load_json(self.unit_economics_path, default={})
        rows: list[dict[str, Any]] = []
        for content_id, record in index.items():
            if not isinstance(record, dict):
                continue
            existing = stored_economics.get(content_id, {})
            stored_platform_revenue = (
                float(existing.get("platform_revenue", 0.0) or 0.0)
                if isinstance(existing, dict)
                else 0.0
            )
            economics = self.compute_unit_economics(
                content_id,
                platform_revenue=stored_platform_revenue,
            )
            revenue_status = str(economics.revenue_status or "unknown").strip()
            if metric_name in {"net_profit", "rpm", "total_revenue"} and not economics.revenue_analysis_eligible:
                continue
            if metric_name in {"net_profit", "rpm"} and not economics.profit_analysis_eligible:
                continue
            if metric_name in {"lead_revenue", "leads"} and revenue_status in {"pending_t1", "unknown", "not_available"}:
                continue
            latest = self._latest_snapshot(content_id)
            revenue_summary = self.summarize_revenue_events(content_id)
            rows.append(
                {
                    **economics.to_dict(),
                    "title": str(record.get("title", "")),
                    "topic_title": str(record.get("topic_title", "")),
                    "platform": str(record.get("platform", "")),
                    "published_at": str(record.get("published_at", "")),
                    "topic_bucket": str(
                        record.get("topic_bucket", "")
                        or self.topic_bucket_for(
                            topic_title=str(record.get("topic_title", "")),
                            title=str(record.get("title", "")),
                        )
                    ),
                    "commercial_intent": str(record.get("commercial_intent", "medium")),
                    "content_mix": str(record.get("content_mix", "professional")),
                    "cta_source_code": str(record.get("cta_source_code", "")),
                    "snapshot_label": latest.snapshot_label if latest else "",
                    "impression_count": latest.impression_count if latest else 0,
                    "read_count": latest.read_count if latest else 0,
                    "read_rate": round(latest.read_count * 100 / latest.impression_count, 2)
                    if latest and latest.impression_count > 0
                    else 0.0,
                    "lead_revenue": float(revenue_summary.get("lead_revenue", 0.0) or 0.0),
                    "revenue_status": economics.revenue_status,
                    "platform_revenue_status": economics.platform_revenue_status,
                    "revenue_analysis_eligible": economics.revenue_analysis_eligible,
                }
            )

        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row["currency"], []).append(row)
        return [item for currency in sorted(groups) for item in sorted(groups[currency], key=lambda item: (
                float(item.get(metric_name, 0.0) or 0.0),
                float(item.get("net_profit", 0.0) or 0.0),
                int(item.get("leads", 0) or 0),
            ), reverse=True)[: max(1, int(limit))]]

    # ── analysis ───────────────────────────────────────────────

    def build_content_mix_report(self) -> dict[str, Any]:
        index = self._load_json(self.content_index_path, default={})
        totals = {"traffic": 0, "professional": 0, "conversion": 0}
        intent_totals = {"low": 0, "medium": 0, "high": 0}
        for record in index.values():
            mix = str(record.get("content_mix", "professional"))
            intent = str(record.get("commercial_intent", "medium"))
            if mix in totals:
                totals[mix] += 1
            if intent in intent_totals:
                intent_totals[intent] += 1
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "content_mix_counts": totals,
            "commercial_intent_counts": intent_totals,
            "total_contents": len(index),
        }
        self._save_json(self.mix_report_path, report)
        return report

    def build_experiment_report(self) -> dict[str, Any]:
        """Aggregate performance and economics for each experiment dimension.

        Rates are weighted by impressions, so a small high-CTR article cannot
        distort a bucket containing materially more distribution. Empty labels
        are retained as ``unassigned`` to make missing instrumentation visible.
        """
        index = self._load_json(self.content_index_path, default={})
        if not isinstance(index, dict):
            index = {}
        economics_ledger = self._load_json(self.unit_economics_path, default={})
        if not isinstance(economics_ledger, dict):
            economics_ledger = {}
        dimensions: dict[str, dict[str, dict[str, Any]]] = {
            dimension: {} for dimension in self.EXPERIMENT_DIMENSIONS
        }
        total_impressions = 0
        total_reads = 0
        total_revenue = 0.0
        total_leads = 0
        pending_t1_count = 0
        not_available_count = 0
        analyzed_content_count = 0
        for content_id, record in index.items():
            if not isinstance(record, dict):
                continue
            snapshot = self._latest_snapshot(str(content_id))
            inferred_availability = self._content_availability_status(
                str(content_id),
                latest_snapshot=snapshot,
                content_record=record,
            )
            if inferred_availability == "pending_t1":
                pending_t1_count += 1
                continue
            if inferred_availability == "not_available":
                not_available_count += 1
                continue
            analyzed_content_count += 1
            published_at = self._parse_datetime(record.get("published_at"))
            impressions = int(snapshot.impression_count if snapshot else 0)
            reads = int(snapshot.read_count if snapshot else 0)
            stored_economics = economics_ledger.get(str(content_id), {})
            stored_platform_revenue = (
                float(stored_economics.get("platform_revenue", 0.0) or 0.0)
                if isinstance(stored_economics, dict)
                else 0.0
            )
            economics = self.compute_unit_economics(
                str(content_id),
                platform_revenue=stored_platform_revenue,
            )
            revenue = economics.total_revenue
            leads = economics.leads
            total_impressions += max(0, impressions)
            total_reads += max(0, reads)
            total_revenue += revenue
            total_leads += max(0, leads)

            normalized_record = dict(record)
            normalized_record["topic_bucket"] = str(
                normalized_record.get("topic_bucket", "")
                or self.topic_bucket_for(
                    topic_title=str(normalized_record.get("topic_title", "")),
                    title=str(normalized_record.get("title", "")),
                )
            )
            for dimension in self.EXPERIMENT_DIMENSIONS:
                value = str(normalized_record.get(dimension, "") or "").strip() or "unassigned"
                stats = dimensions[dimension].setdefault(
                    value,
                    {
                        "dimension": dimension,
                        "value": value,
                        "contents": 0,
                        "samples": 0,
                        "matured_samples": 0,
                        "impressions": 0,
                        "reads": 0,
                        "weighted_read_rate": 0.0,
                        "read_rate": 0.0,
                        "leads": 0,
                        "lead_rate": 0.0,
                        "revenue": 0.0,
                        "revenue_amount": 0.0,
                        "total_cost": 0.0,
                        "net_profit": 0.0,
                        "rpm": 0.0,
                        "profit_samples": 0,
                        "currencies": [],
                        "monetary_scope_error": False,
                    },
                )
                stats["contents"] += 1
                stats["samples"] += 1
                stats["impressions"] += max(0, impressions)
                stats["reads"] += max(0, reads)
                stats["leads"] += max(0, leads)
                stats["revenue"] += revenue
                stats["revenue_amount"] += revenue
                stats["total_cost"] += economics.total_cost
                if economics.currency not in stats["currencies"]:
                    stats["currencies"].append(economics.currency)
                stats["monetary_scope_error"] |= economics.revenue_status == "currency_mismatch"
                if economics.profit_analysis_eligible:
                    stats["net_profit"] += economics.net_profit
                    stats["profit_samples"] += 1
                if snapshot and self._snapshot_is_mature(snapshot, published_at):
                    stats["matured_samples"] += 1

        for buckets in dimensions.values():
            for stats in buckets.values():
                impressions = int(stats["impressions"])
                reads = int(stats["reads"])
                contents = max(1, int(stats["contents"]))
                stats["weighted_read_rate"] = round(reads * 100 / impressions, 4) if impressions else 0.0
                stats["read_rate"] = stats["weighted_read_rate"]
                stats["lead_rate"] = round(stats["leads"] / contents, 4)
                single_currency = len(stats["currencies"]) == 1 and not stats["monetary_scope_error"]
                stats["revenue_per_content"] = round(stats["revenue"] / contents, 4) if single_currency else None
                complete_profit = stats["profit_samples"] == stats["contents"] and stats["contents"] > 0 and single_currency
                stats["rpm"] = round(stats["net_profit"] / impressions * 1000, 4) if complete_profit and impressions else None
                stats["total_cost"] = round(stats["total_cost"], 4) if single_currency else None
                stats["net_profit"] = round(stats["net_profit"], 4) if complete_profit else None
                stats["revenue"] = round(stats["revenue"], 4) if single_currency else None
                stats["revenue_amount"] = stats["revenue"]

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "content_count": len(index),
            "total_impressions": total_impressions,
            "total_reads": total_reads,
            "weighted_read_rate": round(total_reads * 100 / total_impressions, 4)
            if total_impressions
            else 0.0,
            "total_leads": total_leads,
            "total_revenue": round(total_revenue, 4),
            "analyzed_content_count": analyzed_content_count,
            "pending_t1_count": pending_t1_count,
            "not_available_count": not_available_count,
            "revenue_policy": "t_plus_1",
            "dimensions": dimensions,
        }
        self._save_json(self.experiment_report_path, report)
        return report

    def build_cooling_candidates(
        self,
        *,
        lookback_days: int = 30,
        min_samples: int = 10,
        weak_rpm_threshold: float = 0.0,
        weak_read_rate_threshold: float = 3.0,
        cooldown_weak_ratio: float = 0.5,
        stop_weak_ratio: float = 0.8,
        cooldown_penalty: int = 12,
    ) -> dict[str, Any]:
        index = self._load_json(self.content_index_path, default={})
        economics_ledger = self._load_json(self.unit_economics_path, default={})
        snapshot_ledger = self._load_json(self.snapshot_ledger_path, default={})
        cutoff = datetime.now() - timedelta(days=max(1, lookback_days))
        bucket_stats: dict[str, dict[str, Any]] = {}
        for content_id, record in index.items():
            if not isinstance(record, dict):
                continue
            published_at = self._parse_datetime(record.get("published_at"))
            if published_at is None or published_at < cutoff:
                continue
            bucket = str(
                record.get("topic_bucket", "")
                or self.topic_bucket_for(
                    topic_title=str(record.get("topic_title", "")),
                    title=str(record.get("title", "")),
                )
            ).strip()
            if not bucket:
                continue
            stats = bucket_stats.setdefault(
                bucket,
                {
                    "samples": 0,
                    "mature_read_samples": 0,
                    "mature_economics_samples": 0,
                    "weak_reads": 0,
                    "weak_rpm": 0,
                    "rpm_unknown": 0,
                    "protected_samples": 0,
                    "lead_count": 0,
                    "revenue_amount": 0.0,
                    "last_content_id": str(content_id),
                    "commercial_intent": record.get("commercial_intent", "medium"),
                    "content_mix": record.get("content_mix", "professional"),
                },
            )
            latest_snapshot = self._latest_snapshot_from_rows(snapshot_ledger.get(content_id, []))
            is_mature = self._snapshot_is_mature(latest_snapshot, published_at)
            if not is_mature:
                continue
            stats["samples"] += 1
            stats["mature_read_samples"] += 1
            stats["last_content_id"] = str(content_id)
            stats["commercial_intent"] = record.get("commercial_intent", stats["commercial_intent"])
            stats["content_mix"] = record.get("content_mix", stats["content_mix"])
            if latest_snapshot and latest_snapshot.impression_count > 0:
                read_rate = (latest_snapshot.read_count / latest_snapshot.impression_count) * 100
                if read_rate < weak_read_rate_threshold:
                    stats["weak_reads"] += 1
            stored_economics = economics_ledger.get(str(content_id), {})
            stored_platform_revenue = (
                float(stored_economics.get("platform_revenue", 0.0) or 0.0)
                if isinstance(stored_economics, dict)
                else 0.0
            )
            economics = self.compute_unit_economics(
                str(content_id),
                platform_revenue=stored_platform_revenue,
            )
            has_economic_signal = economics.profit_analysis_eligible and economics.rpm is not None and (
                economics.total_cost > 0 or economics.total_revenue > 0
            )
            if has_economic_signal and latest_snapshot and latest_snapshot.impression_count > 0:
                stats["mature_economics_samples"] += 1
                if economics.rpm <= weak_rpm_threshold:
                    stats["weak_rpm"] += 1
            else:
                stats["rpm_unknown"] += 1
            stats["lead_count"] += economics.leads
            stats["revenue_amount"] += economics.total_revenue
            if economics.leads > 0 or economics.total_revenue > 0:
                stats["protected_samples"] += 1

        candidates = []
        for bucket, stats in bucket_stats.items():
            if stats["samples"] < min_samples:
                continue
            weak_ratio = stats["weak_reads"] / max(1, stats["samples"])
            rpm_denominator = max(1, stats["mature_economics_samples"])
            weak_rpm_ratio = stats["weak_rpm"] / rpm_denominator
            if weak_ratio < cooldown_weak_ratio and weak_rpm_ratio < cooldown_weak_ratio:
                continue
            has_protected_content = stats["protected_samples"] > 0
            stop_eligible = not has_protected_content and (
                weak_ratio >= stop_weak_ratio or weak_rpm_ratio >= stop_weak_ratio
            )
            recommendation = "stop" if stop_eligible else "cooldown"
            candidates.append(
                {
                    "bucket": bucket,
                    "samples": stats["samples"],
                    "mature_read_samples": stats["mature_read_samples"],
                    "mature_economics_samples": stats["mature_economics_samples"],
                    "weak_read_ratio": round(weak_ratio, 3),
                    "weak_rpm_ratio": round(weak_rpm_ratio, 3),
                    "rpm_unknown_samples": stats["rpm_unknown"],
                    "protected_samples": stats["protected_samples"],
                    "lead_count": stats["lead_count"],
                    "revenue_amount": round(stats["revenue_amount"], 4),
                    "commercial_intent": stats["commercial_intent"],
                    "content_mix": stats["content_mix"],
                    "recommendation": recommendation,
                    "action": recommendation,
                    "score_penalty": int(cooldown_penalty) if recommendation == "cooldown" else 0,
                }
            )
        candidates.sort(
            key=lambda item: (
                item["recommendation"] == "stop",
                item["weak_read_ratio"],
                item["weak_rpm_ratio"],
            ),
            reverse=True,
        )
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "lookback_days": lookback_days,
            "min_samples": min_samples,
            "cooldown_weak_ratio": cooldown_weak_ratio,
            "stop_weak_ratio": stop_weak_ratio,
            "cooldown_penalty": cooldown_penalty,
            "candidates": candidates,
        }
        self._save_json(self.cooling_report_path, report)
        return report

    def build_business_report(
        self,
        *,
        lookback_days: int = 30,
        min_samples: int = 10,
        weak_rpm_threshold: float = 0.0,
        weak_read_rate_threshold: float = 3.0,
        cooldown_weak_ratio: float = 0.5,
        stop_weak_ratio: float = 0.8,
        cooldown_penalty: int = 12,
    ) -> Path:
        mix_report = self.build_content_mix_report()
        experiment_report = self.build_experiment_report()
        top_by_profit = self.build_unit_economics_ranking(sort_by="net_profit", limit=5)
        top_by_rpm = self.build_unit_economics_ranking(sort_by="rpm", limit=5)
        top_by_leads = self.build_unit_economics_ranking(sort_by="lead_revenue", limit=5)
        economics_ledger = self._load_json(self.unit_economics_path, default={})
        cooling_report = self.build_cooling_candidates(
            lookback_days=lookback_days,
            min_samples=min_samples,
            weak_rpm_threshold=weak_rpm_threshold,
            weak_read_rate_threshold=weak_read_rate_threshold,
            cooldown_weak_ratio=cooldown_weak_ratio,
            stop_weak_ratio=stop_weak_ratio,
            cooldown_penalty=cooldown_penalty,
        )
        recorded_costs: dict[str, float] = {}
        known_revenue: dict[str, float] = {}
        known_contribution: dict[str, float] = {}
        pending_revenue_count = 0
        unknown_revenue_count = 0
        content_index = self._load_json(self.content_index_path, default={})
        for content_id, item in economics_ledger.items():
            if not isinstance(item, dict):
                continue
            cost = self.get_cost(content_id)
            if cost.basis != "unknown":
                recorded_costs[cost.currency] = recorded_costs.get(cost.currency, 0.0) + cost.total_cost
            currency = str(item.get("currency", "CNY"))
            status = str(item.get("revenue_status", "unknown")).strip() or "unknown"
            content_record = (
                content_index.get(content_id, {})
                if isinstance(content_index, dict)
                else {}
            )
            inferred_availability = self._content_availability_status(
                str(content_id),
                content_record=content_record if isinstance(content_record, dict) else {},
            )
            if inferred_availability == "pending_t1" or status == "pending_t1":
                pending_revenue_count += 1
                continue
            if inferred_availability == "not_available":
                unknown_revenue_count += 1
                continue
            elif status not in {"available", "partial"}:
                unknown_revenue_count += 1
            if status in {"available", "partial"}:
                known_revenue[currency] = known_revenue.get(currency, 0.0) + float(item.get("total_revenue", 0.0) or 0.0)
            if bool(item.get("profit_analysis_eligible", False)):
                known_contribution[currency] = known_contribution.get(currency, 0.0) + float(item.get("net_profit", 0.0) or 0.0)
        complete_profit = bool(content_index) and all(
            isinstance(economics_ledger.get(cid), dict)
            and economics_ledger[cid].get("profit_analysis_eligible") is True
            for cid in content_index
        )
        def money_groups(values):
            return json.dumps({key: round(value, 4) for key, value in values.items()}, ensure_ascii=False) if values else "NOT_FOUND"
        contribution = money_groups(known_contribution) if complete_profit else "NOT_FOUND（收入或完整实际成本证据不足）"
        lines = [
            "# 收益优化经营报表",
            "",
            f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
            f"- 已归因内容数：{len(economics_ledger)}",
            f"- 记录成本（含估算及声明，非已核实实扣）：{money_groups(recorded_costs)}",
            f"- 已知口径收入（币种分列，不合计）：{money_groups(known_revenue)}",
            f"- 内容净贡献：{contribution}；不代表扣除全部固定开支后的经营净利润",
            f"- T+1 待结算内容：{pending_revenue_count}",
            f"- 收益未知内容：{unknown_revenue_count}",
            f"- 实验维度：{len(experiment_report.get('dimensions', {}))} 个",
            "",
            "## 内容组合分布",
            f"- traffic: {mix_report['content_mix_counts'].get('traffic', 0)}",
            f"- professional: {mix_report['content_mix_counts'].get('professional', 0)}",
            f"- conversion: {mix_report['content_mix_counts'].get('conversion', 0)}",
            "",
            "## 商业意图分布",
            f"- low: {mix_report['commercial_intent_counts'].get('low', 0)}",
            f"- medium: {mix_report['commercial_intent_counts'].get('medium', 0)}",
            f"- high: {mix_report['commercial_intent_counts'].get('high', 0)}",
            "",
            "## 低收益冷却候选",
        ]
        cooling_heading = lines.pop()
        lines.extend(
            [
                "",
                "## Revenue rankings",
                "",
                "### Net profit",
                *self._build_ranking_lines(top_by_profit, metric="net_profit"),
                "",
                "### Net RPM",
                *self._build_ranking_lines(top_by_rpm, metric="rpm"),
                "",
                "### Lead revenue",
                *self._build_ranking_lines(top_by_leads, metric="lead_revenue"),
                "",
            ]
        )
        lines.append(cooling_heading)
        if not cooling_report.get("candidates"):
            lines.append("- 暂无满足冷却条件的选题桶。")
        else:
            for candidate in cooling_report["candidates"][:10]:
                lines.append(
                    f"- {candidate['bucket']}：样本 {candidate['samples']}，弱阅读率占比 {candidate['weak_read_ratio']:.2f}，弱 RPM 占比 {candidate['weak_rpm_ratio']:.2f}，建议 {candidate['recommendation']}"
                )
        self.business_report_path.write_text("\n".join(lines), encoding="utf-8")
        return self.business_report_path

    def _build_ranking_lines(self, rows: list[dict[str, Any]], *, metric: str) -> list[str]:
        if not rows:
            return ["- No attributed content yet"]
        lines: list[str] = []
        ranks: dict[str, int] = {}
        for row in rows:
            currency = str(row.get("currency", "NOT_FOUND"))
            if currency not in ranks:
                lines.append(f"#### {currency}")
            ranks[currency] = ranks.get(currency, 0) + 1
            value = float(row.get(metric, 0.0) or 0.0)
            unit = f" {currency}" + ("/1k impressions" if metric == "rpm" else "")
            lines.append(
                f"{ranks[currency]}. {row.get('title', '')} | {row.get('content_id', '')} | "
                f"{metric}={value:.2f}{unit} | leads={row.get('leads', 0)}"
            )
        return lines

    # ── helpers ────────────────────────────────────────────────

    def _require_content_id(self, content_id: str) -> str:
        normalized = str(content_id or "").strip()
        if not normalized:
            raise ValueError("content_id is required.")
        return normalized

    def _validate_non_negative_number(self, value: object, field_name: str) -> float:
        try:
            number = float(value or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a finite non-negative number.") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{field_name} must be a finite non-negative number.")
        return number

    def _validate_cost(self, cost: CostRecord) -> None:
        if not isinstance(cost, CostRecord):
            raise TypeError("cost must be a CostRecord.")
        for field_name in ("api_cost", "image_cost", "labor_minutes", "labor_rate_per_hour"):
            self._validate_non_negative_number(getattr(cost, field_name), field_name)
        if not str(cost.currency or "").strip():
            raise ValueError("currency is required for a cost record.")
        if cost.basis not in {"unknown", "declared", "estimated", "actual"}:
            raise ValueError("Unsupported cost evidence basis.")
        if type(cost.coverage_complete) is not bool:
            raise ValueError("Cost coverage must be explicit boolean.")
        if cost.basis == "actual" and not cost.evidence_ref.strip():
            raise ValueError("Actual cost requires a source evidence reference.")

    def _validate_revenue_event(self, event: RevenueEvent) -> None:
        if not isinstance(event, RevenueEvent):
            raise TypeError("event must be a RevenueEvent.")
        if not str(event.event_type or "").strip():
            raise ValueError("event_type is required.")
        self._validate_non_negative_number(event.amount, "amount")
        if not str(event.currency or "").strip():
            raise ValueError("currency is required for a revenue event.")

    def _latest_snapshot(self, content_id: str) -> PerformanceSnapshot | None:
        snapshots = self.get_snapshots(content_id)
        return snapshots[-1] if snapshots else None

    def _snapshot_is_mature(
        self,
        snapshot: PerformanceSnapshot | None,
        published_at: datetime | None,
    ) -> bool:
        if snapshot is None:
            return False
        if snapshot.availability_status in {"pending_t1", "not_available"}:
            return False
        published_date = self._parse_date(snapshot.published_date)
        earnings_date = self._parse_date(snapshot.earnings_date)
        if published_date is not None and earnings_date is not None and earnings_date < published_date:
            return False
        if snapshot.snapshot_label in {"24h", "72h"}:
            return True
        if published_at is None:
            return False
        observed_at = self._parse_datetime(snapshot.snapshot_at)
        return bool(observed_at and (observed_at - published_at).total_seconds() >= 24 * 3600)

    def _parse_date(self, value: object):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text[:10]).date()
        except ValueError:
            return None

    def _latest_snapshot_from_rows(self, rows: list[Any]) -> PerformanceSnapshot | None:
        parsed = [PerformanceSnapshot.from_dict(item) for item in rows if isinstance(item, dict)]
        parsed.sort(key=lambda item: self._snapshot_sort_key(item.to_dict()))
        return parsed[-1] if parsed else None

    def _normalize_enum(self, value: str, allowed: tuple[str, ...], default: str) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in allowed else default

    def _parse_datetime(self, value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    def _snapshot_sort_key(self, item: dict[str, Any]) -> tuple[int, str, str]:
        label = str(item.get("snapshot_label", ""))
        try:
            rank = self.SNAPSHOT_LABELS.index(label)
        except ValueError:
            rank = len(self.SNAPSHOT_LABELS)
        return rank, str(item.get("snapshot_at", "")), label

    def _merge_snapshot_payload(
        self,
        existing: dict[str, Any] | None,
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge retries without turning unknown earnings into zero revenue."""
        if not existing:
            return dict(incoming)

        availability_rank = {"not_available": 0, "pending_t1": 1, "available": 2}
        merged = dict(existing)
        incoming_availability = infer_availability_status(incoming)
        existing_availability = infer_availability_status(existing)
        if incoming_availability == "pending_t1":
            merged["availability_status"] = "pending_t1"
        elif incoming_availability and availability_rank.get(incoming_availability, -1) >= availability_rank.get(existing_availability, -1):
            merged["availability_status"] = incoming_availability

        status_rank = {"unknown": 0, "pending_t1": 1, "not_available": 1, "partial": 2, "available": 3}
        for key in ("revenue_status", "platform_revenue_status"):
            old_status = str(existing.get(key, "unknown")).strip() or "unknown"
            new_status = str(incoming.get(key, "unknown")).strip() or "unknown"
            if incoming_availability == "pending_t1":
                merged[key] = "pending_t1"
            elif status_rank.get(new_status, 0) > status_rank.get(old_status, 0):
                merged[key] = new_status

        for key in (
            "published_date",
            "earnings_date",
            "data_date",
            "observed_at",
            "collection_date",
            "snapshot_at",
            "currency",
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
            "share_count",
            "comment_count",
            "follower_gain",
            "lead_count",
        ):
            try:
                merged[key] = max(int(existing.get(key, 0) or 0), int(incoming.get(key, 0) or 0))
            except (TypeError, ValueError):
                merged[key] = incoming.get(key, existing.get(key, 0))

        old_known = bool(existing.get("revenue_amount_known", False)) or existing.get("platform_revenue") is not None
        new_known = bool(incoming.get("revenue_amount_known", False)) or incoming.get("platform_revenue") is not None
        if incoming_availability == "pending_t1":
            merged["platform_revenue"] = None
            merged["revenue_amount"] = None
        elif new_known and incoming.get("revenue_amount") is not None:
            merged["revenue_amount"] = incoming.get("revenue_amount", 0.0)
        elif old_known:
            merged["revenue_amount"] = existing.get("revenue_amount", 0.0)
        merged["revenue_amount_known"] = False if incoming_availability == "pending_t1" else old_known or new_known
        merged["analysis_eligible"] = incoming_availability == "available"
        return merged

    def _normalize_path_value(self, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(Path(text).resolve()).casefold()
        except OSError:
            return str(Path(text)).casefold()

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def _save_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
