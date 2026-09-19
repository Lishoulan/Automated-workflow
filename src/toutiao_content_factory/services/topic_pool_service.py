from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate


class TopicPoolService:
    DEFAULT_EVERGREEN_TOPICS: dict[str, list[dict[str, Any]]] = {
        "default": [
            {
                "title": "一家新兴产业真正进入商业化，最先变化的三个信号是什么",
                "summary": "从订单、毛利率、渠道结构三个维度拆商业化临界点，适合作为常青分析稿。",
                "matched_industries": ["ai_hardware"],
                "matched_keywords": ["商业化", "订单", "毛利率", "渠道"],
                "topic_type": "evergreen",
                "topic_type_label": "常青分析",
                "recommended_format": "deep_analysis",
                "recommended_format_label": "深度分析",
                "title_playbook_track": "科技商业",
            },
            {
                "title": "行业龙头集体降价之后，谁在吃利润谁在抢份额",
                "summary": "围绕价格战、供应链和份额变化做结构化复盘，适合热点空窗期补位。",
                "matched_industries": ["industrial_automation"],
                "matched_keywords": ["降价", "价格战", "份额", "利润"],
                "topic_type": "evergreen",
                "topic_type_label": "产业复盘",
                "recommended_format": "deep_analysis",
                "recommended_format_label": "深度分析",
                "title_playbook_track": "科技商业",
            },
        ],
        "energy_notes": [
            {
                "title": "储能项目真正赚钱，不只看装机量，还要看哪三个运营指标",
                "summary": "从峰谷价差、循环次数和运维成本拆储能项目盈利逻辑。",
                "matched_industries": ["energy_storage"],
                "matched_keywords": ["储能", "装机", "价差", "运维"],
                "topic_type": "evergreen",
                "topic_type_label": "行业方法论",
                "recommended_format": "deep_analysis",
                "recommended_format_label": "深度分析",
                "title_playbook_track": "新能源储能",
            },
            {
                "title": "光伏和储能一体化电站，为什么正在重写项目投资回报率",
                "summary": "把电站收益模型拆成项目投资人能理解的收益链条。",
                "matched_industries": ["new_energy", "energy_storage"],
                "matched_keywords": ["光伏", "储能", "电站", "回报率"],
                "topic_type": "evergreen",
                "topic_type_label": "产业趋势",
                "recommended_format": "deep_analysis",
                "recommended_format_label": "深度分析",
                "title_playbook_track": "新能源储能",
            },
        ],
        # ⚠️ 2026-09-19：这个池子在账号从「机器人」转型成「人间故事」时**漏改了** ——
        # 里面 6 条全是人形机器人/具身智能选题。后果很严重：
        # 热榜当天筛不出社会故事题时，`topic_pool_service` 会启用长青兜底，
        # 而兜底给出的机器人题又被新的赛道边界全部滤掉 → 选题池 0 条 →
        # 云端直接判 `no_topics` 并跳过当天发布（实测：2026-09-19 15:16 那次云端运行
        # 日志 `Automatic publish result accepted: status=no_topics`，
        # 兜底题是「人形机器人从展示走向交付，要先跨过哪三道门槛」）。
        #
        # 现在的条目按三件事挑选，缺一不可：
        #   1. 通过 `_topic_matches_account_boundary`（matched_industries=social_story）
        #   2. 通过 `_has_social_story_signal`（人与关系 / 处境与冲突的词）
        #   3. 标题写成**事件式**（有"谁遇到了什么事"的具体形状），而不是方法论/设问式。
        #      方法论式标题（「…想要回来得先做哪几件事」）在搜索引擎上拿不到结果，
        #      连摘要都拿不到，等于没有素材可写。
        #
        # ⚠️ 但要认清一个**架构性限制**（2026-09-19 实测 13 条候选，全部如此）：
        #
        #   长青题**永远拿不到"正文可抓"的搜索结果**。同一套 `search_web`：
        #       热榜题「男子高空作业绳子被2楼老人剪断」→ 6 条结果**全部**是
        #         sohu / news.qq.com / thepaper / 163 / news.china.com（正文 1000~3800 字）
        #       长青题（13 条 event-shaped 候选）→ **0 条**正文可抓，只有搜狗微信的
        #         跳转链接（落到 antispider）或零散摘要
        #
        #   原因是算法性的：**必应优先返回新鲜新闻**，长青/通用查询不在它的排序里。
        #   所以不要把"提高长青题素材量"当作可以靠改标题解决的问题。
        #   长青题的正确定位是：**热榜一条可用题都没有时的保底**，
        #   它产出的是 550~900 字的短稿（靠 Bing 摘要拼素材 + 模型写作），
        #   能不能过自动发布门槛取决于当天模型状态。
        #
        #   「每天都发得出去」主要靠热榜题那条链路 —— 那条链路已验证可发布
        #   （2026-09-19 23:16 首次真实发布，pgc_id=7687264638244831784）。
        # 全部实测通过，见 tests/test_evergreen_story_pool.py。
        "robot_notes": [
            {
                "title": "老人去世后房产过户给儿子，其他子女起诉要回",
                "summary": "围绕遗嘱效力、法定继承顺序与份额协商，讲清房产继承里最容易谈崩的一步。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["遗产", "房产"],
                "topic_type": "evergreen",
                "topic_type_label": "家庭伦理",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "独居老人家中摔倒两天无人知，邻居报警破门",
                "summary": "独居老人的照护盲区：从日常联系频率、紧急联络人到社区可用的求助渠道。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["摔倒", "养老"],
                "topic_type": "evergreen",
                "topic_type_label": "养老处境",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "老人把房子过户给儿子后被赶出家门，起诉要回居住权",
                "summary": "围绕赠与撤销、居住权约定与举证责任，讲清把房产提前给子女前该留什么。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["房产", "赡养"],
                "topic_type": "evergreen",
                "topic_type_label": "养老处境",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "保姆照看老人期间老人摔伤，家属索赔被拒",
                "summary": "围绕雇佣关系、家政公司责任与保险赔付，讲清请人照护前该确认的几件事。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["保姆", "养老"],
                "topic_type": "evergreen",
                "topic_type_label": "责任边界",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "子女轮流照顾老人起争执，老人搬去养老院",
                "summary": "赡养分工失衡的常见起因：出钱与出力的换算、长期照护的疲惫、缺少书面约定。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["赡养", "养老"],
                "topic_type": "evergreen",
                "topic_type_label": "家庭伦理",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "老人借钱给亲戚没打借条，起诉后法院这样认定",
                "summary": "围绕借条、转账记录、诉讼时效与调解顺序，讲清普通人追回借款的实际路径。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["借钱", "养老"],
                "topic_type": "evergreen",
                "topic_type_label": "生活经验",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "退休后帮子女带孙子，两代人为育儿方式争吵",
                "summary": "隔代育儿里的边界问题：谁来定规矩、钱怎么算、老人累出病之后怎么办。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["带娃", "隔代"],
                "topic_type": "evergreen",
                "topic_type_label": "代际关系",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "业主起诉物业要求公开停车费收支，法院判了",
                "summary": "从业主共有部分收益、物业合同权限和举证责任，讲清这类邻里纠纷为什么难打。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["物业", "官司"],
                "topic_type": "evergreen",
                "topic_type_label": "邻里纠纷",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "再婚老伴去世后，继子女与亲生子女争房产",
                "summary": "再婚家庭的财产与居住权争议：婚前财产怎么算、继子女有没有继承权、居住权能不能主张。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["再婚", "房产"],
                "topic_type": "evergreen",
                "topic_type_label": "家庭伦理",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
            {
                "title": "老人被推销买下高价保健品，子女要求退款被拒",
                "summary": "围绕虚假宣传、老年消费诱导与退货维权路径，讲清家里遇到这类事该留哪些证据。",
                "matched_industries": ["social_story"],
                "matched_keywords": ["诈骗", "养老"],
                "topic_type": "evergreen",
                "topic_type_label": "消费维权",
                "recommended_format": "quick_commentary",
                "recommended_format_label": "快讯点评",
                "title_playbook_track": "社会故事",
            },
        ],
        "intl_notes": [
            {
                "title": "一场国际政策变化，通常如何传导到资本市场和大宗商品",
                "summary": "适用于国际号在缺少强热点时输出可读性较高的解释型文章。",
                "matched_industries": ["international"],
                "matched_keywords": ["国际", "政策", "资本市场", "大宗商品"],
                "topic_type": "evergreen",
                "topic_type_label": "国际观察",
                "recommended_format": "deep_analysis",
                "recommended_format_label": "深度分析",
                "title_playbook_track": "国际热点",
            }
        ],
        "sports_notes": [
            {
                "title": "一支球队突然爆发之前，数据面通常会先出现哪些变化",
                "summary": "用于体育号在非比赛窗口期输出战术/数据分析稿。",
                "matched_industries": ["sports"],
                "matched_keywords": ["体育", "球队", "数据", "爆发"],
                "topic_type": "evergreen",
                "topic_type_label": "赛事分析",
                "recommended_format": "deep_analysis",
                "recommended_format_label": "深度分析",
                "title_playbook_track": "体育赛事",
            }
        ],
    }

    # 类常量定义
    DEFAULT_MAX_AGE_HOURS = 48
    DEFAULT_PENDING_LIMIT = 24
    DEFAULT_FALLBACK_LIMIT = 6
    MAX_NUMBERS_EXTRACTED = 6
    MIN_FRESHNESS_SCORE = 35
    MAX_EVERGREEN_RANK = 12
    DEFAULT_QUALITY_SCORE = 72
    DEFAULT_HEAT_SCORE = 35
    DEFAULT_READ_SCORE = 35
    DEFAULT_SAVE_SCORE = 40
    DEFAULT_DISCUSSION_SCORE = 28
    DEFAULT_TOTAL_SCORE = 138
    DEFAULT_TITLE_POTENTIAL_SCORE = 42
    DEFAULT_EVIDENCE_STRENGTH = 60

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.pool_dir = self.settings.automation_dir / "topic_pool"
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        self.pool_path = self.pool_dir / "topic_pool.jsonl"
        self.summary_path = self.pool_dir / "topic_pool_summary.json"
        self.evergreen_dir = self.settings.project_root / "data" / "evergreen_topics"

    def ingest_report(
        self,
        report: DailyTopicsReport,
        *,
        source_label: str = "daily_topics",
    ) -> dict[str, Path]:
        existing_rows = self._load_rows()
        index = {self._row_key(row): row for row in existing_rows}
        created = 0
        refreshed = 0

        for topic in report.topics:
            row = self._build_row(topic, source_label=source_label, origin="live")
            key = self._row_key(row)
            current = index.get(key)
            if current is None:
                index[key] = row
                created += 1
                continue
            preserved = dict(current)
            preserved.update(row)
            preserved["status"] = str(current.get("status", "pending") or "pending")
            preserved["selected_at"] = current.get("selected_at")
            preserved["discarded_at"] = current.get("discarded_at")
            preserved["last_seen_at"] = row["last_seen_at"]
            index[key] = preserved
            refreshed += 1

        self._write_rows(list(index.values()))
        summary = {
            "updated_at": self._now(),
            "source_label": source_label,
            "created": created,
            "refreshed": refreshed,
            "total_rows": len(index),
        }
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "topic_pool": self.pool_path,
            "topic_pool_summary": self.summary_path,
        }

    def build_fallback_report(self) -> DailyTopicsReport:
        pending = self.list_pending_topics(limit=24)
        evergreen_topics = self._load_evergreen_topics()
        # Merge pending live topics with a bounded evergreen reserve. A
        # partially populated pool must not hide the reserve, otherwise one
        # pending topic can incorrectly cap a top-up batch at one item.
        combined: list[TopicCandidate] = []
        seen: set[str] = set()
        for topic in [*pending, *evergreen_topics[: self.DEFAULT_FALLBACK_LIMIT]]:
            key = self._topic_identity(topic)
            if key in seen:
                continue
            seen.add(key)
            combined.append(topic)

        ranked = self._rank_topics(combined)
        if evergreen_topics and not pending:
            self.logger.warning("[Topic Engine] 今日热点选题不足，已启用长青选题兜底: %s", ranked[0].title)
        elif evergreen_topics and pending:
            self.logger.info(
                "[Topic Engine] 题库候选不足，已追加长青选题兜底: pending=%d evergreen=%d",
                len(pending),
                min(len(evergreen_topics), self.DEFAULT_FALLBACK_LIMIT),
            )
        return DailyTopicsReport(
            generated_at=self._now(),
            lookback_days=0,
            max_topic_age_hours=0,
            focus_industries=list(getattr(self.settings.topic_collection, "focus_industries", []) or []),
            total_raw_items=len(ranked),
            total_selected_items=len(ranked),
            topics=ranked,
            source_stats={
                **({"topic_pool": {"raw_count": len(pending), "status": "cached"}} if pending else {}),
                **({"evergreen": {"raw_count": len(evergreen_topics), "status": "fallback"}} if evergreen_topics else {}),
            },
            failed_sources=[],
            filtered_out={},
        )

    def list_pending_topics(self, *, limit: int = 50) -> list[TopicCandidate]:
        now = datetime.now()
        pending: list[TopicCandidate] = []
        for row in self._load_rows():
            if str(row.get("status", "pending")).strip() != "pending":
                continue
            topic = self._topic_from_row(row)
            if self._is_stale(topic, row, now):
                continue
            pending.append(topic)
        pending = self._rank_topics(pending)
        return pending[: max(1, int(limit))]

    def mark_selected(self, topic: TopicCandidate, *, run_dir: Path | None = None) -> None:
        self._update_status(topic, status="selected", run_dir=run_dir)

    def mark_discarded(self, topic: TopicCandidate, *, reason: str = "") -> None:
        self._update_status(topic, status="discarded", reason=reason)

    def _update_status(
        self,
        topic: TopicCandidate,
        *,
        status: str,
        run_dir: Path | None = None,
        reason: str = "",
    ) -> None:
        rows = self._load_rows()
        changed = False
        target_key = self._topic_identity(topic)
        for row in rows:
            if self._row_key(row) != target_key:
                continue
            row["status"] = status
            row["last_seen_at"] = self._now()
            if status == "selected":
                row["selected_at"] = self._now()
                if run_dir is not None:
                    row["selected_run_dir"] = str(run_dir)
            if status == "discarded":
                row["discarded_at"] = self._now()
                if reason:
                    row["discard_reason"] = reason
            changed = True
            break
        if changed:
            self._write_rows(rows)

    def _is_stale(self, topic: TopicCandidate, row: dict[str, Any], now: datetime) -> bool:
        origin = str(row.get("origin", "live")).strip()
        if origin == "evergreen":
            return False
        raw_reference = str(row.get("published_at", "") or row.get("first_seen_at", "")).strip()
        if not raw_reference:
            return False
        try:
            reference = datetime.fromisoformat(raw_reference.replace("Z", "+00:00").replace(" ", "T"))
            if reference.tzinfo is not None:
                reference = reference.astimezone().replace(tzinfo=None)
        except ValueError:
            return False
        max_age_hours = int(getattr(self.settings.topic_collection, "max_topic_age_hours", self.DEFAULT_MAX_AGE_HOURS) or self.DEFAULT_MAX_AGE_HOURS)
        return reference < (now - timedelta(hours=max(1, max_age_hours)))

    def _load_rows(self) -> list[dict[str, Any]]:
        if not self.pool_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for raw_line in self.pool_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _write_rows(self, rows: list[dict[str, Any]]) -> None:
        ordered = sorted(
            rows,
            key=lambda item: (
                str(item.get("status", "pending")) != "pending",
                str(item.get("published_at", "") or item.get("first_seen_at", "")),
                str(item.get("title", "")),
            ),
            reverse=False,
        )
        serialized = "\n".join(json.dumps(item, ensure_ascii=False) for item in ordered)
        self.pool_path.write_text(serialized + ("\n" if serialized else ""), encoding="utf-8")

    def _build_row(self, topic: TopicCandidate, *, source_label: str, origin: str) -> dict[str, Any]:
        payload = asdict(topic)
        payload.update(
            {
                "status": "pending",
                "origin": origin,
                "source_label": source_label,
                "first_seen_at": self._now(),
                "last_seen_at": self._now(),
                "identity_key": self._topic_identity(topic),
            }
        )
        return payload

    def _row_key(self, row: dict[str, Any]) -> str:
        identity_key = str(row.get("identity_key", "")).strip()
        if identity_key:
            return identity_key
        return self._topic_identity(self._topic_from_row(row))

    def _topic_identity(self, topic: TopicCandidate) -> str:
        raw = (topic.url or topic.title or "").strip().lower()
        if not raw:
            raw = self._now()
        return raw

    def _topic_from_row(self, row: dict[str, Any]) -> TopicCandidate:
        payload = dict(row)
        for key in (
            "status",
            "origin",
            "source_label",
            "first_seen_at",
            "last_seen_at",
            "identity_key",
            "selected_at",
            "discarded_at",
            "selected_run_dir",
            "discard_reason",
        ):
            payload.pop(key, None)
        return TopicCandidate.from_dict(payload)

    def _load_evergreen_topics(self) -> list[TopicCandidate]:
        keys = [self.settings.account_key, self.settings.account_slug, "default"]
        seen: set[str] = set()
        topics: list[TopicCandidate] = []
        history_map = self._load_evergreen_usage_map()

        payloads: list[dict[str, Any]] = []
        for key in keys:
            payloads.extend(self.DEFAULT_EVERGREEN_TOPICS.get(key, []))
            payloads.extend(self._load_external_evergreen_payloads(key))

        for payload in payloads:
            title = str(payload.get("title", "")).strip()
            if not title:
                continue
            digest = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
            identity_url = f"evergreen://{self.settings.account_slug}/{digest}"
            topic = TopicCandidate.from_dict(
                {
                    "title": title,
                    "url": identity_url,
                    "source": "evergreen_pool",
                    "source_type": "evergreen",
                    "published_at": self._now(),
                    "snippet": payload.get("summary", ""),
                    "summary": payload.get("summary", ""),
                    "matched_industries": payload.get("matched_industries", []),
                    "matched_keywords": payload.get("matched_keywords", []),
                    "extracted_numbers": payload.get("extracted_numbers", []),
                    "heat_score": int(payload.get("heat_score", self.DEFAULT_HEAT_SCORE) or self.DEFAULT_HEAT_SCORE),
                    "read_score": int(payload.get("read_score", self.DEFAULT_READ_SCORE) or self.DEFAULT_READ_SCORE),
                    "save_score": int(payload.get("save_score", self.DEFAULT_SAVE_SCORE) or self.DEFAULT_SAVE_SCORE),
                    "discussion_score": int(payload.get("discussion_score", self.DEFAULT_DISCUSSION_SCORE) or self.DEFAULT_DISCUSSION_SCORE),
                    "total_score": int(payload.get("total_score", self.DEFAULT_TOTAL_SCORE) or self.DEFAULT_TOTAL_SCORE),
                    "title_potential_score": int(payload.get("title_potential_score", self.DEFAULT_TITLE_POTENTIAL_SCORE) or self.DEFAULT_TITLE_POTENTIAL_SCORE),
                    "title_playbook_track": payload.get("title_playbook_track", ""),
                    "title_potential_reasons": payload.get("title_potential_reasons", ["Evergreen fallback topic."]),
                    "is_high_read": False,
                    "is_high_save": True,
                    "is_high_discussion": False,
                    "topic_type": payload.get("topic_type", "evergreen"),
                    "topic_type_label": payload.get("topic_type_label", "常青分析"),
                    "evidence_strength": int(payload.get("evidence_strength", self.DEFAULT_EVIDENCE_STRENGTH) or self.DEFAULT_EVIDENCE_STRENGTH),
                    "quality_score": int(payload.get("quality_score", self.DEFAULT_QUALITY_SCORE) or self.DEFAULT_QUALITY_SCORE),
                    "quality_flags": payload.get("quality_flags", ["evergreen_fallback"]),
                    "recommended_format": payload.get("recommended_format", "deep_analysis"),
                    "recommended_format_label": payload.get("recommended_format_label", "深度分析"),
                    "rank": 0,
                }
            )
            identity = self._topic_identity(topic)
            if identity in seen:
                continue
            topics.append(topic)
            seen.add(identity)

        topics.sort(
            key=lambda item: (
                history_map.get(self._topic_identity(item), ""),
                -int(item.quality_score or 0),
                -int(item.total_score or 0),
            )
        )
        return topics

    def _load_external_evergreen_payloads(self, key: str) -> list[dict[str, Any]]:
        candidates = [
            self.evergreen_dir / f"{key}.json",
            self.evergreen_dir / f"{key.replace('-', '_')}.json",
        ]
        payloads: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.warning("Failed to load evergreen topic file %s: %s", candidate, exc)
                continue
            if isinstance(raw, list):
                payloads.extend(item for item in raw if isinstance(item, dict))
            elif isinstance(raw, dict):
                items = raw.get("topics", [])
                if isinstance(items, list):
                    payloads.extend(item for item in items if isinstance(item, dict))
        return payloads

    def _load_evergreen_usage_map(self) -> dict[str, str]:
        usage: dict[str, str] = {}
        for row in self._load_rows():
            if str(row.get("origin", "")).strip() != "evergreen":
                continue
            key = self._row_key(row)
            usage[key] = str(row.get("selected_at", "") or row.get("last_seen_at", "") or "")
        return usage

    def _rank_topics(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        ranked = sorted(
            topics,
            key=lambda item: (
                int(item.total_score or 0),
                int(item.quality_score or 0),
                int(item.title_potential_score or 0),
            ),
            reverse=True,
        )
        for index, topic in enumerate(ranked, start=1):
            topic.rank = index
        return ranked

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
