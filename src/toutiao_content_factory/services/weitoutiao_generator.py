from __future__ import annotations

"""独立微头条选题 + 写作 + 发布（微头条高频化，2026-08-27）。

背景：头条涨粉研究表明微头条是涨粉最快入口，每日 2-3 条效果好于 1 条。
现有流程只在图文发布成功后伴生 1 条微头条（内容取自图文）。
本模块新增「独立微头条」能力：从当日选题池独立选题（与图文选题去重）、
独立写作短内容（多个人设轮换）、复用既有 publish_weitoutiao 浏览器自动化发布，
并写入 publish_history（platform=toutiao_weitoutiao）供日报统计。
"""

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import AIRequestError, AIResponseFormatError
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.article_sanitizer import looks_like_html_document
from toutiao_content_factory.services.compliance_service import count_today_published_by_platform
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.publish_service import PublishService

logger = logging.getLogger(__name__)

MIN_WTT_CHARS = 200
MAX_WTT_CHARS = 700
WTT_ACCEPTED_STATUSES = {"published", "submitted", "submitted_for_review", "success"}
WTT_DRAFT_STATUSES = {"draft_saved", "draft_created", "draft_only"}
WTT_FAILURE_STATUSES = {
    "failed",
    "error",
    "blocked",
    "blocked_by_ip",
    "publish_login_required",
    "publish_uncertain",
    "circuit_open",
    "missing",
    "invalid",
}


# ============================================================
# 独立微头条人设：system + user 模板（自包含，不依赖外部 SKILL.md）
# ============================================================

HOT_COMMENT_SYSTEM = (
    "你是今日头条百万粉科技/产业观察账号的主笔，擅长把行业热点写成一条条"
    "「观点锋利、信息增量、适合手机碎片阅读」的微头条。你的内容在信息流里以"
    "短观点 + 话题标签形态获得推荐与涨粉，不做标题党、不编造数据。"
    "账号定位：{positioning}——写内容时用数据拆热点、用产业链视角看趋势兑现。"
)

HOT_COMMENT_USER = """请围绕下面的热点/行业选题，写一条发布在今日头条的微头条。

选题标题：{title}
选题摘要：{summary}
来源：{source}
账号定位：{positioning}（用数据拆热点、用产业链视角看趋势兑现）

写作要求：
1. 第一行直接写一个 15-25 字的观点型标题（反常识 / 判断 / 冲突均可，不要加"标题："前缀）。
2. 正文 250-450 字，2-4 个短段，每段最多 3 行：
   - 开头第一句直接给判断/钩子，不要复述标题；
   - 中段讲清"这件事为什么今天值得看 + 背后的关键变量/争议点"；
   - 只能基于选题提供的摘要做有限推断，区分事实与推测，禁止编造具体数字、人名、时间。
3. 结尾留一个具体的开放式互动问题，引导评论区。
4. 末尾附 2-4 个 #话题标签（从选题关键词里挑）。
5. 全文不要出现 markdown 标题符号（#、##），整体控制在 300-500 字。"""


WARM_SYSTEM = (
    "你是一位拥有千万粉丝的“今日头条”优质情感/正能量创作者。你深谙头条用户的心理，"
    "擅长从任何热点标签中发掘出人性的光辉、生活的美好以及向上的力量。"
)

WARM_USER = """请围绕下面的选题，写一条正能量微头条。

选题标题：{title}
选题摘要：{summary}

写作要求：
1. 第一行直接写一个 15-25 字的标题（反差破防体 / 细节放大体 / 致敬普通人体 / 深度治愈体），
   不要加"标题："前缀。
2. 正文 250-400 字，2-4 个短段，每段最多 3 行：
   - 开头用画面感或情绪饱满的句子先声夺人；
   - 中段用 2-3 句话讲清"最打动人的部分"，再升华到普通人的坚韧 / 善良的传递；
   - 语言真诚接地气，不用假大空口号，可用 1-2 个治愈系金句。
3. 结尾用一句充满祝福或力量的问句引导评论区。
4. 适当添加温暖有力量的 Emoji（❤️ ✨ 🌻 👍），不要杂乱。
5. 全文不要出现 markdown 标题符号，整体控制在 300-450 字。"""


SANHUO_SYSTEM = (
    "你是头条财经区转化率最高的「实战派散户向」微头条创作者，定位是"
    "「和你一起炒股的老大哥/过来人」，受众精准锁定持仓 AI 主线的短线交易者。"
)

SANHUO_USER = """请围绕下面的选题，写一条实战派微头条。

选题标题：{title}
选题摘要：{summary}

写作要求：
1. 第一行直接写一个 15-20 字标题（结论先行 / 痛点共鸣 / 悬念引导 / 操作指令），
   不要加"标题："前缀。
2. 正文 250-400 字，短句为主，一行一个观点：
   - 开头直接抛核心结论，3 秒留住用户；
   - 中段用"我们"复盘盘面/逻辑（昨天说对什么、今天怎么看、板块怎么拆）；
   - 结尾给明确可执行的"保命规则"（如"集体放量下跌就跑，否则做T，心态不好就减仓"）。
3. 末尾附 #今日A股 #股民交流 等话题标签。
4. 全文口语化，像微信群聊，不要学术化表达，整体控制在 300-450 字。"""


SHOULAN_SYSTEM = (
    "你是「手蓝」——一个用金融/交易思维拆解现实生活的头条创作者。"
    "你的风格冷峻、清醒、不讲政治正确，像是一个极其清醒的“局内人”在复盘。"
)

SHOULAN_USER = """请围绕下面的选题，用"交易思维"写一条微头条。

选题标题：{title}
选题摘要：{summary}

写作要求：
1. 第一行直接写一个 20 字左右的标题（反共识 / 周期降维 / 散户vs主力体），
   不要加"标题："前缀，全文不要出现真实身份词，自称"手蓝"。
2. 正文 300-450 字，2-4 个短段，每段最多 3 行：
   - 【硬核开场】前 40 字直接定调，切忌废话和同情；
   - 【现象的金融化拆解】用筹码思维/周期框架/确定性与不确定性拆解困境，指出大多数人逆势加仓或把运气当实力；
   - 【认知升华】真正的正能量是"认清周期、尊重常识、保持纪律"，不熬鸡汤；
   - 【清醒结尾】用一句掷地有声的交易/人生纪律收尾，可带一个互动问句。
3. 多用"说白了""核心逻辑""底层密码"这类词，全文整体控制在 350-500 字。"""


PERSONA_TEMPLATES: dict[str, tuple[str, str]] = {
    "hot_comment": (HOT_COMMENT_SYSTEM, HOT_COMMENT_USER),
    "warm": (WARM_SYSTEM, WARM_USER),
    "sanhuo": (SANHUO_SYSTEM, SANHUO_USER),
    "shoulan": (SHOULAN_SYSTEM, SHOULAN_USER),
}


def _sanitize_slug(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(text or "").strip())
    return re.sub(r"-{2,}", "-", cleaned).strip("-")[:60] or "wtt"


class WeiToutiaoStandaloneGenerator:
    """独立微头条生成器：选题 → 写作 → 构造发布包 → 复用浏览器发布。"""

    BALANCE_FAILURE_MARKERS = (
        "account balance is insufficient",
        "insufficient balance",
        "provider account balance",
        "余额不足",
        "账户余额不足",
    )

    def __init__(self, settings: AppSettings, logger_: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger_
        self.topic_automation = TopicAutomationService(settings, logger_)
        self.openai_service = OpenAIContentService(settings, logger_)
        self.publish_service = PublishService(settings, logger_)

    # ── 选题 ──────────────────────────────────────────────

    def select_topics(self, count: int = 1) -> list[TopicCandidate]:
        """从当日选题池独立选题：账号边界过滤 + 今日已用去重 + 按 rank 取前 N。

        复用主管线 topic_automation 的选题引擎（_load_daily_topics 会回退到常青选题库），
        并通过 _available_topics 排除今日已发布/已生成选题（含图文与微头条）。

        高频化兜底（2026-08-27）：当日池不足 count 时，从 topic_pool / 常青选题库
        补充候选（build_fallback_report 内部：topic_pool pending → evergreen），
        并与已选/已用选题去重，保证每日 2-3 条微头条有足够选题。
        """
        ranked: list[TopicCandidate] = []
        try:
            report = self.topic_automation._load_daily_topics()
            try:
                available = self.topic_automation._available_topics(report.topics)
            except Exception as exc:
                self.logger.warning("WeiToutiao standalone topic filter failed: %s", exc)
                available = self.topic_automation.filter_topics_for_account(report.topics)
            ranked = self._rank_topics(available)
        except Exception as exc:
            self.logger.warning("WeiToutiao standalone topic load failed: %s", exc)

        if len(ranked) < count:
            try:
                fallback_report = self.topic_automation.topic_pool_service.build_fallback_report()
                fallback_available = self.topic_automation._available_topics(fallback_report.topics)
                fallback_ranked = self._rank_topics(fallback_available)
                # 与当日池候选去重（按 title/url 归一化）
                seen_keys = {
                    self._topic_key(topic) for topic in ranked if topic
                }
                for topic in fallback_ranked:
                    key = self._topic_key(topic)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    ranked.append(topic)
                    if len(ranked) >= count:
                        break
                self.logger.info(
                    "WeiToutiao standalone fallback pool used: fallback_available=%d total=%d requested=%d",
                    len(fallback_ranked),
                    len(ranked),
                    count,
                )
            except Exception as exc:
                self.logger.warning("WeiToutiao standalone fallback topic load failed: %s", exc)

        self.logger.info(
            "WeiToutiao standalone topic pool: available=%d requested=%d",
            len(ranked),
            count,
        )
        return ranked[:count]

    @staticmethod
    def _topic_key(topic: TopicCandidate) -> str:
        title = str(topic.title or "").strip().lower()
        url = str(topic.url or "").strip().lower()
        return f"{title}|{url}"

    @staticmethod
    def _rank_topics(topics: list[TopicCandidate]) -> list[TopicCandidate]:
        return sorted(
            [topic for topic in topics if topic],
            key=lambda topic: (
                topic.rank if topic.rank and topic.rank > 0 else 10_000,
                -int(topic.total_score or 0),
            ),
        )

    # ── 写作 ──────────────────────────────────────────────

    def generate_content(self, topic: TopicCandidate, persona: str = "hot_comment") -> str:
        """按人设模板写一条独立微头条（含标题行 + 正文 + 话题标签）。"""
        system_template, user_template = PERSONA_TEMPLATES.get(
            persona, PERSONA_TEMPLATES["hot_comment"]
        )
        positioning = str(self.settings.brand_positioning or "").strip()
        system_prompt = system_template.format(positioning=positioning) if "{positioning}" in system_template else system_template
        summary = str(topic.summary or "").strip() or str(topic.snippet or "").strip() or str(topic.title)
        user_prompt = user_template.format(
            title=str(topic.title or "").strip(),
            summary=summary[:400],
            source=str(topic.source or "").strip() or "公开信息",
            positioning=positioning or "用数据拆热点、用产业链看趋势兑现",
        )
        raw = self.openai_service.create_text_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.85,
            max_tokens=900,
        )
        content = raw.strip()
        if looks_like_html_document(content):
            raise AIResponseFormatError(
                "WeiToutiao model response was an HTML document instead of post text."
            )
        # 兜底：内容过短时补话题标签，避免发布层 200 字门槛拦截
        if len(content.replace("\n", "").replace(" ", "")) < MIN_WTT_CHARS:
            content = content + "\n\n" + self._build_tags(topic)
        return content[:MAX_WTT_CHARS]

    def _build_tags(self, topic: TopicCandidate) -> str:
        tags: list[str] = []
        for keyword in (topic.matched_keywords or [])[:3]:
            keyword = str(keyword).strip()
            if keyword and keyword not in {"热点", "热议"}:
                tags.append(f"#{keyword}")
        if not tags:
            tags = ["#产业观察", "#热点"]
        return " ".join(tags[:3])

    @staticmethod
    def _content_char_count(content: str) -> int:
        return len(str(content or "").replace("\n", "").replace("\r", "").replace(" ", ""))

    def _local_fallback_enabled(self) -> bool:
        publishing = getattr(self.settings, "publishing", None)
        return bool(getattr(publishing, "weitoutiao_local_fallback_enabled", True))

    @staticmethod
    def _is_transient_model_error(exc: Exception) -> bool:
        """Return true only for errors that are reasonable to bypass locally."""
        transient_codes = {429, 500, 502, 503, 504}
        markers = (
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "service unavailable",
            "gateway timeout",
            "bad gateway",
            "rate limit",
            "too many requests",
            "upstream",
        )
        seen: set[int] = set()
        current: Exception | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status_code = getattr(current, "status_code", None)
            try:
                if int(status_code) in transient_codes:
                    return True
            except (TypeError, ValueError):
                pass
            text = f"{type(current).__name__} {current}".lower()
            if isinstance(current, AIRequestError) and "model client is not available" in text:
                return False
            if re.search(r"\b(?:429|500|502|503|504)\b", text):
                return True
            if any(marker in text for marker in markers):
                return True
            next_error = current.__cause__ or current.__context__
            current = next_error if isinstance(next_error, Exception) else None
        return False

    @classmethod
    def _contains_balance_failure(cls, value: object) -> bool:
        text = str(value or "").lower()
        return any(marker in text for marker in cls.BALANCE_FAILURE_MARKERS)

    def _is_local_fallback_eligible(self, exc: Exception) -> bool:
        """Allow evidence-only writing after a provider quota failure.

        OpenAIContentService disables the client after the first 402 response.
        Subsequent agents then receive only ``Model client is not available``;
        consult the service's recorded reason so the later micro-posts do not
        become false generation failures. Authentication errors remain blocked.
        """
        if self._is_transient_model_error(exc):
            return True
        if self._contains_balance_failure(exc):
            return True
        error_text = str(exc or "").lower()
        unavailable_reason = str(
            getattr(self.openai_service, "unavailable_reason", "") or ""
        )
        return (
            "model client is not available" in error_text
            and self._contains_balance_failure(unavailable_reason)
        )

    def build_local_fallback_content(self, topic: TopicCandidate, persona: str = "hot_comment") -> str:
        """Build an evidence-only short post without inventing facts when the model is down."""
        title = re.sub(r"\s+", " ", str(topic.title or "").strip()) or "今日行业观察"
        summary = re.sub(r"\s+", " ", str(topic.summary or "").strip())
        if not summary:
            summary = re.sub(r"\s+", " ", str(topic.snippet or "").strip())
        if not summary:
            summary = title
        summary = summary[:280]
        source = re.sub(r"\s+", " ", str(topic.source or "公开信息").strip()) or "公开信息"
        headline = f"{title[:24]}，真正值得关注的是后续兑现"
        if persona == "warm":
            opening = "热点会过去，但一条公开信息留下的观察线索不会立刻消失。"
            closing = "把事实看清楚，再给自己的判断留一点更新空间，你最想继续追踪哪个变量？"
        elif persona in {"sanhuo", "shoulan"}:
            opening = "先别急着追情绪，真正有价值的是把已确认的事实和自己的推测分开。"
            closing = "先列出观察清单，再等证据兑现，你会继续盯住哪一个信号？"
        else:
            opening = "这条消息值得看，但价值不只在热闹本身，更在它暴露出的后续观察方向。"
            closing = "接下来你最想看到哪一条可验证的新进展？"
        content = (
            f"{headline}\n\n"
            f"{opening}根据{source}目前公开的摘要，能确认的信息是：{summary}。"
            "这部分属于事实，除此之外的延伸结论，都应该等新的公开证据出现后再判断。\n\n"
            "从产业和执行角度，后续可以看三件事：相关主体是否给出新的公开进展，"
            "已经说出的方向是否出现实际动作，上下游是否出现能够相互印证的变化。"
            "如果这些信号还没有出现，就不宜把一次消息直接等同于确定结果。\n\n"
            "对普通读者来说，最稳妥的做法是记录事实、标注推测，并随着新信息更新结论。"
            f"{closing}\n\n{self._build_tags(topic)}"
        )
        return content[:MAX_WTT_CHARS]

    # ── 发布包构造 ────────────────────────────────────────

    def build_run_package(
        self,
        topic: TopicCandidate,
        content: str,
        persona: str,
        *,
        generation_mode: str = "model",
        generation_fallback_reason: str = "",
    ) -> Path:
        """构造独立微头条的 run 包（publish/article.md + publish_meta.json + tags.txt），
        供 publish_weitoutiao 复用浏览器发布流程。
        """
        if looks_like_html_document(content):
            raise AIResponseFormatError(
                "WeiToutiao content was an HTML document instead of post text."
            )
        root = self.settings.output_dir / "wtt_standalone"
        root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = root / f"{timestamp}_{_sanitize_slug(topic.title)}"
        publish_dir = run_dir / self.settings.review.publish_dirname
        publish_dir.mkdir(parents=True, exist_ok=True)

        (publish_dir / "article.md").write_text(content, encoding="utf-8")
        tags = self._extract_tags(content) or self._build_tags(topic)
        (publish_dir / "tags.txt").write_text("\n".join(tags), encoding="utf-8")

        first_line = next((line.strip() for line in content.splitlines() if line.strip()), topic.title)
        publish_meta = {
            "approved_at": datetime.now().isoformat(timespec="seconds"),
            "selected_title": first_line[: self.settings.publishing.title_max_length],
            "selected_topic": asdict(topic),
            "tags": tags,
            "style_key": persona,
            "style_label": persona,
            "weitoutiao_standalone": True,
            "score_snapshot": {},
            "generation_mode": generation_mode,
            "generation_fallback_reason": generation_fallback_reason,
            "content_char_count": self._content_char_count(content),
        }
        (publish_dir / "publish_meta.json").write_text(
            json.dumps(publish_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "selected_topic.json").write_text(
            json.dumps({"selected_topic": asdict(topic)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return run_dir

    @staticmethod
    def _extract_tags(content: str) -> list[str]:
        return re.findall(r"#([^\s#]{1,20})", content)[:4]

    # ── 批量发布 ──────────────────────────────────────────

    def publish_standalone(
        self,
        count: int = 1,
        *,
        draft_only: bool = False,
        exclude_topic_titles: list[str] | None = None,
        purpose: str = "standalone",
    ) -> dict[str, Any]:
        """生成并发布 count 条独立微头条。

        返回 { published: [...], skipped: [...], exports: {...} }。
        """
        requested = max(0, int(count))
        today_before = self.today_weitoutiao_count()
        target = max(1, int(self.settings.publishing.weitoutiao_daily_target or 1))
        if requested == 0:
            return self._finalize_batch_result(
                {
                    "status": "target_reached",
                    "message": "WeiToutiao daily target was already reached; no top-up was needed.",
                    "requested": 0,
                    "selected": 0,
                    "selection_shortfall": 0,
                    "attempted": 0,
                    "generated": 0,
                    "accepted": 0,
                    "drafted": 0,
                    "skipped_count": 0,
                    "fallback_count": 0,
                    "fallback_used": False,
                    "today_count_before": today_before,
                    "today_count_after": today_before,
                    "target": target,
                    "published": [],
                    "skipped": [],
                    "exports": {},
                },
                purpose=purpose,
            )

        exclude_titles = {
            self.topic_automation._normalize_text(str(item).strip())
            for item in (exclude_topic_titles or [])
            if str(item).strip()
        }
        topics = self.select_topics(count=requested)
        personas = list(self.settings.publishing.weitoutiao_personas) or ["hot_comment"]
        published: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        exports: dict[str, str] = {}

        for index, topic in enumerate(topics, start=1):
            normalized = self.topic_automation._normalize_text(topic.title)
            if normalized in exclude_titles:
                self.logger.info("WeiToutiao standalone skipped duplicate topic: %s", topic.title)
                skipped.append(
                    {
                        "topic_title": topic.title,
                        "reason": "duplicate_today",
                        "stage": "selection",
                        "generation_mode": "not_started",
                    }
                )
                continue
            exclude_titles.add(normalized)

            persona = personas[(index - 1) % len(personas)]
            generation_mode = "model"
            fallback_reason = ""
            try:
                content = self.generate_content(topic, persona=persona)
            except Exception as exc:
                if self._local_fallback_enabled() and self._is_local_fallback_eligible(exc):
                    generation_mode = "local_fallback"
                    fallback_reason = self._safe_error(exc)
                    content = self.build_local_fallback_content(topic, persona=persona)
                    self.logger.warning(
                        "WeiToutiao model generation unavailable; using local fallback: %s",
                        fallback_reason,
                    )
                else:
                    self.logger.warning("WeiToutiao standalone generate failed: %s", exc)
                    skipped.append(
                        {
                            "topic_title": topic.title,
                            "reason": "generate_failed",
                            "error": self._safe_error(exc),
                            "stage": "generation",
                            "generation_mode": "model",
                        }
                    )
                    continue

            if looks_like_html_document(content):
                error = "WeiToutiao generated content was an HTML document, so publishing was blocked."
                self.logger.warning(error)
                skipped.append(
                    {
                        "topic_title": topic.title,
                        "reason": "generate_failed",
                        "error": error,
                        "stage": "generation",
                        "generation_mode": generation_mode,
                    }
                )
                continue

            if self._content_char_count(content) < MIN_WTT_CHARS:
                if self._local_fallback_enabled() and generation_mode == "model":
                    generation_mode = "local_fallback"
                    fallback_reason = "model_output_too_short"
                    content = self.build_local_fallback_content(topic, persona=persona)
                    self.logger.warning(
                        "WeiToutiao model output was too short; using local fallback for topic=%s",
                        topic.title,
                    )
                else:
                    skipped.append(
                        {
                            "topic_title": topic.title,
                            "reason": "content_too_short",
                            "stage": "generation",
                            "generation_mode": generation_mode,
                        }
                    )
                    continue

            run_dir: Path | None = None
            try:
                run_dir = self.build_run_package(
                    topic,
                    content,
                    persona,
                    generation_mode=generation_mode,
                    generation_fallback_reason=fallback_reason,
                )
                exports[f"wtt_standalone_{index}"] = str(run_dir)
                result = self.publish_service.publish_weitoutiao(
                    run_dir, draft_only=draft_only, content_override=content
                )
                result_payload = self._resolve_publish_result_payload(run_dir, result)
                status = str(result_payload.get("status", "")).strip().lower() or "unknown"
                if status in WTT_FAILURE_STATUSES:
                    error = self._safe_error(
                        result_payload.get("error")
                        or result_payload.get("message")
                        or f"WeiToutiao publish returned status={status}"
                    )
                    self._write_publish_result_artifact(
                        run_dir,
                        {**result_payload, "status": status, "error": error},
                    )
                    skipped.append(
                        {
                            "topic_title": topic.title,
                            "reason": "publish_failed",
                            "error": error,
                            "stage": "publish",
                            "generation_mode": generation_mode,
                            "run_dir": str(run_dir),
                        }
                    )
                    continue
                self._write_publish_result_artifact(run_dir, {**result_payload, "status": status})
                published.append(
                    {
                        "topic_title": topic.title,
                        "persona": persona,
                        "status": status,
                        "run_dir": str(run_dir),
                        "published_at": str(result_payload.get("published_at", "")),
                        "external_url": str(result_payload.get("external_url", "")),
                        "generation_mode": generation_mode,
                        "fallback_used": generation_mode == "local_fallback",
                        "fallback_reason": fallback_reason,
                        "content_char_count": self._content_char_count(content),
                    }
                )
                self.logger.info(
                    "WeiToutiao standalone published #%d: %s (%s)",
                    index,
                    topic.title,
                    status,
                )
            except Exception as exc:
                self.logger.warning("WeiToutiao standalone publish failed: %s", exc)
                if run_dir is not None:
                    self._write_publish_result_artifact(
                        run_dir,
                        {
                            "status": "failed",
                            "error": self._safe_error(exc),
                            "published_at": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                skipped.append(
                    {
                        "topic_title": topic.title,
                        "reason": "publish_failed",
                        "error": self._safe_error(exc),
                        "stage": "publish",
                        "generation_mode": generation_mode,
                        "run_dir": str(run_dir) if run_dir is not None else "",
                    }
                )

        accepted = sum(
            1
            for item in published
            if str(item.get("status", "")).strip().lower() in WTT_ACCEPTED_STATUSES
        )
        drafted = sum(
            1
            for item in published
            if str(item.get("status", "")).strip().lower() in WTT_DRAFT_STATUSES
        )
        fallback_count = sum(
            1
            for item in [*published, *skipped]
            if str(item.get("generation_mode", "")).strip().lower() == "local_fallback"
        )
        selection_shortfall = max(0, requested - len(topics))
        all_selected_accepted = bool(topics) and accepted == len(topics) and not skipped
        if not topics:
            status = "no_topics"
            message = "No eligible topics were available for the WeiToutiao batch."
        elif all_selected_accepted:
            status = "success"
            if selection_shortfall:
                message = (
                    "WeiToutiao batch completed all eligible candidates without publish failures; "
                    f"requested={requested}, selected={len(topics)}, accepted={accepted}. "
                    "No additional eligible topics were available for this slot."
                )
            else:
                message = "WeiToutiao batch completed successfully."
        elif accepted > 0:
            status = "partial_success"
            message = "WeiToutiao batch partially succeeded; some items need follow-up."
        elif drafted > 0 and not skipped:
            status = "draft_only"
            message = "WeiToutiao batch was saved as drafts; no public submission was made."
        else:
            status = "failed"
            message = "WeiToutiao batch did not produce a public submission."

        return self._finalize_batch_result(
            {
                "status": status,
                "message": message,
                "requested": requested,
                "selected": len(topics),
                "selection_shortfall": selection_shortfall,
                "attempted": len(published) + len(skipped),
                "generated": sum(1 for item in [*published, *skipped] if str(item.get("run_dir", "")).strip()),
                "accepted": accepted,
                "drafted": drafted,
                "skipped_count": len(skipped),
                "fallback_count": fallback_count,
                "fallback_used": fallback_count > 0,
                "today_count_before": today_before,
                "today_count_after": self.today_weitoutiao_count(),
                "target": target,
                "published": published,
                "skipped": skipped,
                "exports": exports,
            },
            purpose=purpose,
        )

    @staticmethod
    def _safe_error(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())[:500]

    def _resolve_publish_result_payload(self, run_dir: Path, result: object) -> dict[str, Any]:
        payload = dict(result) if isinstance(result, dict) else {}
        if str(payload.get("status", "")).strip():
            return payload
        candidates: list[Path] = []
        for key in ("weitoutiao_publish_result", "publish_result", "result_path"):
            value = payload.get(key)
            if not value:
                continue
            candidate = Path(str(value))
            if not candidate.is_absolute():
                candidate = run_dir / candidate
            candidates.append(candidate)
        candidates.append(run_dir / self.settings.review.publish_dirname / "weitoutiao_publish_result.json")
        for candidate in candidates:
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(loaded, dict):
                return loaded
        return payload

    def _write_publish_result_artifact(self, run_dir: Path, payload: dict[str, Any]) -> Path:
        path = run_dir / self.settings.review.publish_dirname / "weitoutiao_publish_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = {**payload, "platform": "toutiao_weitoutiao", "run_dir": str(run_dir)}
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def _finalize_batch_result(self, result: dict[str, Any], *, purpose: str) -> dict[str, Any]:
        result["purpose"] = purpose
        report_dir = self.settings.automation_dir / "weitoutiao"
        report_dir.mkdir(parents=True, exist_ok=True)
        safe_purpose = re.sub(r"[^A-Za-z0-9_-]+", "-", str(purpose or "standalone")).strip("-") or "standalone"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        report_path = report_dir / f"{safe_purpose}_{timestamp}.json"
        latest_path = report_dir / f"last_{safe_purpose}_result.json"
        result["result_path"] = str(report_path)
        result["latest_result_path"] = str(latest_path)
        serialized = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        report_path.write_text(serialized, encoding="utf-8")
        latest_path.write_text(serialized, encoding="utf-8")
        return result

    # ── 每日高频目标 ──────────────────────────────────────

    def today_weitoutiao_count(self) -> int:
        history_path = self.settings.output_dir / self.settings.publishing.history_filename
        return count_today_published_by_platform(history_path, "toutiao_weitoutiao")

    def remaining_for_target(self) -> int:
        target = max(1, int(self.settings.publishing.weitoutiao_daily_target or 1))
        return max(0, target - self.today_weitoutiao_count())
