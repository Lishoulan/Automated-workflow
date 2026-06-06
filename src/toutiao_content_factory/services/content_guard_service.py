from __future__ import annotations

import json
import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker


@dataclass(slots=True)
class TrieNode:
    children: dict[str, "TrieNode"] = field(default_factory=dict)
    fail: "TrieNode | None" = None
    outputs: list[str] = field(default_factory=list)


class SensitiveWordTrie:
    def __init__(self, words: list[str] | tuple[str, ...]) -> None:
        self.root = TrieNode()
        self.words = [str(word).strip() for word in words if str(word).strip()]
        self._build()

    def _build(self) -> None:
        for word in self.words:
            node = self.root
            for char in word:
                node = node.children.setdefault(char, TrieNode())
            if word not in node.outputs:
                node.outputs.append(word)

        queue: deque[TrieNode] = deque()
        for child in self.root.children.values():
            child.fail = self.root
            queue.append(child)

        while queue:
            node = queue.popleft()
            for char, child in node.children.items():
                fail = node.fail
                while fail is not None and char not in fail.children:
                    fail = fail.fail
                child.fail = fail.children[char] if fail and char in fail.children else self.root
                if child.fail.outputs:
                    child.outputs.extend(item for item in child.fail.outputs if item not in child.outputs)
                queue.append(child)

    def find_all(self, text: str) -> list[str]:
        node = self.root
        matches: list[str] = []
        for char in text:
            while node is not self.root and char not in node.children:
                node = node.fail or self.root
            if char in node.children:
                node = node.children[char]
            else:
                node = self.root
            if node.outputs:
                matches.extend(node.outputs)
        return matches


class ContentGuardService:
    TOPIC_SIMILARITY_THRESHOLD = 0.70
    RUN_SIMILARITY_THRESHOLD = 0.76
    AI_RISK_THRESHOLD = 0.62
    HUMAN_REVIEW_THRESHOLD = 0.45
    REVIEW_REJECTED_STATUS = "rejected"
    REVIEW_REJECTED_LABEL = "审核驳回（需人工干预）"
    REVIEW_REJECTED_ACTION = "manual_intervention_required"
    DEFAULT_BAD_WORDS = (
        "# 本地敏感词/违禁词词库，1 行 1 个词，支持中文、英文、数字组合",
        "# 以 # 开头的行会被忽略",
        "违禁示例词",
        "敏感示例词",
    )

    HIGH_RISK_CATEGORIES = {
        "medical": {
            "tokens": ("用药", "偏方", "治疗", "诊断", "处方", "药物", "药品", "疗效", "治愈", "秘方", "中医推荐", "保健品"),
            "label": "医疗健康",
        },
        "stock_pick": {
            "tokens": ("荐股", "买入", "卖出", "止损", "目标价", "牛股", "涨停", "抄底", "加仓", "减仓", "满仓", "空仓", "股票推荐", "理财方案"),
            "label": "财经荐股",
        },
        "fortune": {
            "tokens": ("星座", "运势", "风水", "算命", "命理", "塔罗", "占卜", "紫微", "八字", "属相", "生肖运势"),
            "label": "命理预测",
        },
    }

    AI_CLICHE_PHRASES = (
        "在当今快节奏的社会中",
        "不可否认的是",
        "总而言之",
        "综上所述",
        "令人深思的是",
        "发人深省",
        "众所周知",
        "显而易见",
        "毋庸置疑",
        "不难发现",
        "这不禁让人思考",
        "值得关注",
        "需要注意",
        "总体来看",
        "从这个角度看",
        "换句话说",
        "尤为值得关注",
        "需要强调的是",
        "关键在于",
        "核心问题在于",
        "究其原因",
        "深究其因",
        "追根溯源",
        "放眼未来",
        "展望未来",
        "未来可期",
        "前景广阔",
        "大有可为",
        "方兴未艾",
        "如火如荼",
        "蓬勃发展",
    )

    def __init__(self, settings: AppSettings, logger) -> None:
        self.settings = settings
        self.logger = logger
        self.report_path = self.settings.automation_dir / "content_guard_report.json"
        self.bad_words_path = self.settings.project_root / "config" / "bad_words.txt"
        self._ensure_bad_words_file()
        self.bad_words = self._load_bad_words()
        self.bad_words_trie = SensitiveWordTrie(self.bad_words)
        self.quality_checker = ArticleQualityChecker()

    def evaluate_candidate_topic(self, *, topic_title: str, topic_url: str = "") -> dict[str, Any]:
        corpus = self._load_recent_published_corpus()
        raw_text = " ".join(part for part in (topic_title, topic_url) if part)
        target_text = self._normalize_text(raw_text)
        bad_words = self.scan_text(raw_text)
        if bad_words["blocked"]:
            payload = self._empty_report()
            payload.update(
                {
                    "blocked": True,
                    "bad_words_blocked": True,
                    "matched_bad_words": bad_words["matched_words"],
                    "warnings": bad_words["warnings"],
                    "human_review_required": True,
                }
            )
            self._write_report(payload)
            return payload
        if not target_text or not corpus:
            return self._empty_report()

        best_match: dict[str, Any] | None = None
        best_score = 0.0
        for item in corpus:
            score = self._cosine_similarity(target_text, str(item.get("text", "")))
            if score > best_score:
                best_score = score
                best_match = item

        blocked = bool(best_match and best_score >= self.TOPIC_SIMILARITY_THRESHOLD)
        warnings = self._build_similarity_warnings(best_match, best_score, self.TOPIC_SIMILARITY_THRESHOLD)

        high_risk = self._check_high_risk_categories(raw_text)
        if high_risk["blocked"]:
            blocked = True
            warnings.append(
                f"高风险类目拦截：检测到“{high_risk['matched_category']}”相关内容（命中关键词：{', '.join(high_risk['matched_tokens'])}）。"
            )

        payload = {
            "generated_at": self._now(),
            "mode": "topic_candidate",
            "blocked": blocked,
            "similarity": round(best_score, 4),
            "threshold": self.TOPIC_SIMILARITY_THRESHOLD,
            "matched_run_dir": str(best_match.get("run_dir", "")) if best_match else "",
            "matched_title": str(best_match.get("title", "")) if best_match else "",
            "warnings": warnings,
            "human_review_required": bool(best_score >= self.HUMAN_REVIEW_THRESHOLD),
            "bad_words_blocked": False,
            "matched_bad_words": [],
        }
        self._write_report(payload)
        return payload

    def evaluate_run(self, run_dir: Path) -> dict[str, Any]:
        run_dir = Path(run_dir)
        title, article = self._load_title_and_article(run_dir)

        raw_text = "\n".join(part for part in (title, article) if part)
        target_text = self._normalize_text(raw_text)
        corpus = self._load_recent_published_corpus(exclude_run_dir=run_dir)
        bad_words = self.scan_text(raw_text)
        if bad_words["blocked"]:
            report = self._empty_report(mode="run")
            report.update(
                {
                    "blocked": True,
                    "run_dir": str(run_dir),
                    "bad_words_blocked": True,
                    "matched_bad_words": bad_words["matched_words"],
                    "warnings": bad_words["warnings"],
                    "human_review_required": True,
                    "recommendations": ["移除敏感词后再提交，并执行人工复核。"],
                }
            )
            self._write_report(report, run_dir=run_dir)
            self._apply_review_override_from_report(run_dir, report)
            return report
        if not target_text or not corpus:
            report = self._empty_report(mode="run")
            report["run_dir"] = str(run_dir)
            self._write_report(report, run_dir=run_dir)
            return report

        best_match: dict[str, Any] | None = None
        best_score = 0.0
        for item in corpus:
            score = self._cosine_similarity(target_text, str(item.get("text", "")))
            if score > best_score:
                best_score = score
                best_match = item

        ai_risk = self._estimate_ai_giness(title, article)
        blocked = bool(best_match and best_score >= self.RUN_SIMILARITY_THRESHOLD)
        warnings = self._build_similarity_warnings(best_match, best_score, self.RUN_SIMILARITY_THRESHOLD)
        warnings.extend(self._build_ai_risk_warnings(ai_risk))
        # 数据引用校验
        number_warnings = self._check_number_attribution(article, run_dir)
        warnings.extend(number_warnings)
        review_required = bool(best_score >= self.HUMAN_REVIEW_THRESHOLD or ai_risk >= self.HUMAN_REVIEW_THRESHOLD)
        payload = {
            "generated_at": self._now(),
            "mode": "run",
            "blocked": blocked,
            "similarity": round(best_score, 4),
            "threshold": self.RUN_SIMILARITY_THRESHOLD,
            "matched_run_dir": str(best_match.get("run_dir", "")) if best_match else "",
            "matched_title": str(best_match.get("title", "")) if best_match else "",
            "run_dir": str(run_dir),
            "warnings": self._unique_texts(warnings),
            "ai_risk_score": ai_risk,
            "ai_risk_level": self._classify_ai_risk(ai_risk),
            "human_review_required": review_required,
            "recommendations": self._build_recommendations(best_match, best_score, ai_risk),
            "bad_words_blocked": False,
            "matched_bad_words": [],
        }
        self._write_report(payload, run_dir=run_dir)
        return payload

    def build_publish_guard_report(self, run_dir: Path) -> dict[str, Any]:
        report = self.evaluate_run(run_dir)
        warnings = list(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else []
        recommendations = (
            list(report.get("recommendations", []))
            if isinstance(report.get("recommendations"), list)
            else []
        )
        blockers: list[str] = []
        title, article = self._load_title_and_article(run_dir)
        quality_result = None
        if title and article:
            quality_result = self.quality_checker.check_article(title, article)
        if quality_result is not None:
            warnings.extend(
                f"质量检查：{issue}" for issue in quality_result.issues[:5]
            )
            if quality_result.total_score < 60:
                warnings.append(
                    f"内容深度评分 {quality_result.total_score}/100，建议补充数据引用或深度分析"
                )
        if bool(report.get("bad_words_blocked")):
            matched_words = ", ".join(report.get("matched_bad_words", [])[:10])
            blockers.append(f"命中本地敏感词/违禁词，已强制拦截自动发布：{matched_words}")
        if bool(report.get("blocked")) and not bool(report.get("bad_words_blocked")):
            blockers.append("Content is too similar to a recently published run and should be revised before publishing.")
        if float(report.get("ai_risk_score", 0.0) or 0.0) >= self.AI_RISK_THRESHOLD:
            blockers.append("AI-style risk is high. Add more concrete facts, quotes, and human edits before publishing.")
        return {
            **report,
            "blocking_flags": self._unique_texts(blockers),
            "warnings": self._unique_texts(warnings),
            "recommendations": self._unique_texts(recommendations),
            "review_status_override": self._review_override_payload(report) if blockers else {},
            "quality_check": {
                "is_passed": quality_result.is_passed if quality_result else None,
                "total_score": quality_result.total_score if quality_result else None,
                "compliance_score": quality_result.compliance_score if quality_result else None,
                "originality_score": quality_result.originality_score if quality_result else None,
                "readability_score": quality_result.readability_score if quality_result else None,
                "depth_score": quality_result.depth_score if quality_result else None,
                "issues": quality_result.issues if quality_result else [],
                "suggestions": quality_result.suggestions if quality_result else [],
            } if quality_result else {},
        }

    def scan_text(self, text: str) -> dict[str, Any]:
        normalized = self._normalize_text(text)
        if not normalized or not self.bad_words:
            return {"blocked": False, "matched_words": [], "warnings": []}
        matches = self.bad_words_trie.find_all(normalized)
        deduped = self._unique_texts(matches)
        if not deduped:
            return {"blocked": False, "matched_words": [], "warnings": []}
        return {
            "blocked": True,
            "matched_words": deduped,
            "warnings": [f"红线警告：命中本地敏感词/违禁词 -> {', '.join(deduped[:10])}"],
        }

    def build_review_rejection_payload(
        self,
        *,
        matched_words: list[str],
        extra_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        matched = self._unique_texts(matched_words)
        note = f"{self.REVIEW_REJECTED_LABEL}：命中本地敏感词/违禁词 -> {', '.join(matched[:10])}"
        if extra_notes:
            note = f"{note}；{'；'.join(self._unique_texts(extra_notes)[:3])}"
        return {
            "status": self.REVIEW_REJECTED_STATUS,
            "status_label": self.REVIEW_REJECTED_LABEL,
            "recommended_action": self.REVIEW_REJECTED_ACTION,
            "publish_recommendation": "revise_before_publish",
            "reviewer_notes": note,
            "guard_blocked": True,
            "guard_reason": "bad_words",
            "matched_bad_words": matched,
            "highlight_level": "red",
            "updated_at": self._now(),
        }

    def apply_review_rejection(self, run_dir: Path, payload: dict[str, Any]) -> Path:
        run_dir = Path(run_dir)
        status_path = run_dir / "review_status.json"
        status = {}
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                status = {}
        if not status:
            status = {"created_at": self._now()}
        status.update(payload)
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return status_path

    def _review_override_payload(self, report: dict[str, Any]) -> dict[str, Any]:
        if not bool(report.get("bad_words_blocked")):
            return {}
        return self.build_review_rejection_payload(
            matched_words=list(report.get("matched_bad_words", [])),
            extra_notes=list(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else [],
        )

    def _apply_review_override_from_report(self, run_dir: Path, report: dict[str, Any]) -> Path | None:
        override = self._review_override_payload(report)
        if not override:
            return None
        status_path = self.apply_review_rejection(run_dir, override)
        matched_words = ", ".join(list(report.get("matched_bad_words", []))[:10])
        self.logger.error(
            "Sensitive-word red line triggered for run %s: %s",
            run_dir,
            matched_words,
        )
        return status_path

    def _ensure_bad_words_file(self) -> None:
        self.bad_words_path.parent.mkdir(parents=True, exist_ok=True)
        if self.bad_words_path.exists():
            return
        self.bad_words_path.write_text("\n".join(self.DEFAULT_BAD_WORDS) + "\n", encoding="utf-8")

    def _load_bad_words(self) -> list[str]:
        self._ensure_bad_words_file()
        words: list[str] = []
        for line in self.bad_words_path.read_text(encoding="utf-8", errors="replace").splitlines():
            word = str(line).strip()
            if not word or word.startswith("#"):
                continue
            normalized = self._normalize_text(word)
            if normalized:
                words.append(normalized)
        return self._unique_texts(words)

    def _load_recent_published_corpus(self, *, exclude_run_dir: Path | None = None) -> list[dict[str, Any]]:
        runs_root = self.settings.runs_dir
        if not runs_root.exists():
            return []
        cutoff = datetime.now() - timedelta(days=15)
        rows: list[dict[str, Any]] = []
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            if exclude_run_dir is not None and run_dir.resolve() == exclude_run_dir.resolve():
                continue
            review_status_path = run_dir / "review_status.json"
            publish_result_path = run_dir / self.settings.review.publish_dirname / "publish_result.json"
            article_path = run_dir / self.settings.review.publish_dirname / "article.md"
            title_path = run_dir / self.settings.review.publish_dirname / "title.txt"
            if not article_path.exists():
                article_path = run_dir / "article.md"
            if not review_status_path.exists() or not article_path.exists():
                continue
            try:
                status = json.loads(review_status_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(status.get("status", "")).strip() not in {"approved", "published", "auto_publish_success"}:
                continue
            created_at = self._parse_run_time(run_dir.name)
            if created_at is None or created_at < cutoff:
                continue
            if publish_result_path.exists():
                try:
                    publish_payload = json.loads(publish_result_path.read_text(encoding="utf-8"))
                    if str(publish_payload.get("status", "")).strip() not in {"published", "success", "draft_created"}:
                        continue
                except Exception:
                    pass
            title = title_path.read_text(encoding="utf-8", errors="replace").strip() if title_path.exists() else ""
            article = article_path.read_text(encoding="utf-8", errors="replace")
            rows.append(
                {
                    "run_dir": str(run_dir),
                    "title": title,
                    "text": self._normalize_text("\n".join((title, article))),
                }
            )
        return rows

    def _cosine_similarity(self, left: str, right: str) -> float:
        left_tokens = Counter(self._tokenize(left))
        right_tokens = Counter(self._tokenize(right))
        if not left_tokens or not right_tokens:
            return 0.0
        shared = set(left_tokens) & set(right_tokens)
        numerator = sum(left_tokens[token] * right_tokens[token] for token in shared)
        left_norm = math.sqrt(sum(value * value for value in left_tokens.values()))
        right_norm = math.sqrt(sum(value * value for value in right_tokens.values()))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _tokenize(self, text: str) -> list[str]:
        compact = self._normalize_text(text)
        if not compact:
            return []
        tokens: list[str] = []
        ascii_tokens = self._ASCII_TOKEN_PATTERN.findall(compact)
        tokens.extend(ascii_tokens)
        chinese_only = self._NON_WORD_REPLACE_PATTERN.sub("", compact)
        tokens.extend(chinese_only[index : index + 2] for index in range(max(0, len(chinese_only) - 1)))
        return [token for token in tokens if token.strip()]

    def _normalize_text(self, text: str) -> str:
        normalized = self._WHITESPACE_PATTERN.sub("", str(text or "").lower())
        normalized = self._INVALID_CHAR_PATTERN.sub("", normalized)
        return normalized[:12000]

    def _parse_run_time(self, name: str) -> datetime | None:
        raw = str(name or "").split("_", 1)[0]
        try:
            return datetime.strptime(raw, "%Y%m%d-%H%M%S")
        except ValueError:
            return None

    def _estimate_ai_giness(self, title: str, article: str) -> float:
        text = str(article or "")
        full_text = "\n".join(part for part in (title, article) if part)
        normalized = self._normalize_text(full_text)
        if not normalized:
            return 0.0

        paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
        repeated_openers = self._repeated_paragraph_opener_ratio(paragraphs)
        marketing_phrases = self._phrase_density(
            full_text,
            (
                "值得关注",
                "不难发现",
                "需要注意",
                "从这个角度看",
                "总体来看",
                "换句话说",
                "最后",
                "首先",
                "其次",
                "再次",
            ),
        )
        exclamation_ratio = min(text.count("！") / max(len(paragraphs), 1), 1.0)
        number_ratio = min(len(self._SINGLE_DIGIT_PATTERN.findall(full_text)) / max(len(normalized), 1) * 20, 1.0)
        quote_ratio = min((full_text.count("“") + full_text.count("\"")) / max(len(paragraphs), 1), 1.0)
        cliche_density = self._phrase_density(full_text, self.AI_CLICHE_PHRASES)
        risk = 0.34 * repeated_openers + 0.28 * marketing_phrases + 0.20 * exclamation_ratio
        risk += 0.18 * max(0.0, 1.0 - min(1.0, number_ratio + quote_ratio * 0.35))
        risk += 0.18 * cliche_density
        return round(max(0.0, min(risk, 1.0)), 4)

    def _repeated_paragraph_opener_ratio(self, paragraphs: list[str]) -> float:
        if len(paragraphs) < 3:
            return 0.0
        openers = [self._normalize_text(item)[:8] for item in paragraphs if self._normalize_text(item)]
        if len(openers) < 3:
            return 0.0
        counts = Counter(openers)
        repeated = sum(value for value in counts.values() if value > 1)
        return min(repeated / len(openers), 1.0)

    def _phrase_density(self, text: str, phrases: tuple[str, ...]) -> float:
        lowered = str(text or "")
        hits = sum(lowered.count(phrase) for phrase in phrases)
        return min(hits / 6, 1.0)

    def _classify_ai_risk(self, score: float) -> str:
        if score >= self.AI_RISK_THRESHOLD:
            return "high"
        if score >= self.HUMAN_REVIEW_THRESHOLD:
            return "medium"
        return "low"

    def _build_similarity_warnings(
        self,
        best_match: dict[str, Any] | None,
        best_score: float,
        threshold: float,
    ) -> list[str]:
        warnings: list[str] = []
        if not best_match or best_score <= 0:
            return warnings
        warnings.append(f"Similarity to a recent published run reached {best_score:.2f} (threshold {threshold:.2f}).")
        matched_title = str(best_match.get("title", "")).strip()
        if matched_title:
            warnings.append(f"Closest published title: {matched_title}")
        return warnings

    def _build_ai_risk_warnings(self, ai_risk: float) -> list[str]:
        if ai_risk >= self.AI_RISK_THRESHOLD:
            return [f"AI-style risk is high at {ai_risk:.2f}."]
        if ai_risk >= self.HUMAN_REVIEW_THRESHOLD:
            return [f"AI-style risk is elevated at {ai_risk:.2f}; human polish is recommended."]
        return []

    def _check_high_risk_categories(self, text: str) -> dict[str, Any]:
        normalized = self._normalize_text(text)
        for _key, category in self.HIGH_RISK_CATEGORIES.items():
            matched = [token for token in category["tokens"] if token in normalized]
            if matched:
                return {
                    "blocked": True,
                    "matched_category": category["label"],
                    "matched_tokens": matched,
                }
        return {"blocked": False, "matched_category": "", "matched_tokens": []}

    def _build_recommendations(
        self,
        best_match: dict[str, Any] | None,
        best_score: float,
        ai_risk: float,
    ) -> list[str]:
        recommendations: list[str] = []
        if best_match and best_score >= self.HUMAN_REVIEW_THRESHOLD:
            recommendations.append("Replace the lead angle and add new evidence so the article is less repetitive.")
        if ai_risk >= self.HUMAN_REVIEW_THRESHOLD:
            recommendations.append("Inject concrete numbers, direct quotes, and first-hand commentary before publishing.")
        if ai_risk >= self.AI_RISK_THRESHOLD:
            recommendations.append("Pause auto-publish and require manual review for this draft.")
        return recommendations

    def _unique_texts(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in items:
            text = str(raw).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    NUMBER_PATTERN = re.compile(
        r"\d+(?:\.\d+)?(?:%|倍|年|亿元|亿美元|万元|万台|万辆|万套|万吨|GW|MW|kW|GWh|MWh|kWh)"
    )
    # 预编译 tokenize 相关的正则表达式
    _ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9%]{2,}")
    _NON_WORD_REPLACE_PATTERN = re.compile(r"[a-z0-9%]+")
    # 预编译 normalize_text 相关的正则表达式
    _WHITESPACE_PATTERN = re.compile(r"\s+")
    _INVALID_CHAR_PATTERN = re.compile(r"[^\w\u4e00-\u9fff%]")
    # 预编译数字模式
    _SINGLE_DIGIT_PATTERN = re.compile(r"\d")

    def _check_number_attribution(self, article_text: str, run_dir: Path) -> list[str]:
        """检查文章中的数字是否在原始素材中有对应出处"""
        warnings: list[str] = []
        # 尝试读取原始选题/素材
        source_path = run_dir / "selected_topic.json"
        source_text = ""
        if source_path.exists():
            try:
                data = json.loads(source_path.read_text(encoding="utf-8"))
                topic = data.get("selected_topic", data)
                source_text = " ".join([
                    str(topic.get("title", "")),
                    str(topic.get("snippet", "")),
                    str(topic.get("summary", "")),
                ])
            except Exception:
                pass
        if not source_text:
            return warnings
        article_numbers = set(self.NUMBER_PATTERN.findall(article_text))
        source_numbers = set(self.NUMBER_PATTERN.findall(source_text))
        for num in article_numbers:
            if num not in source_numbers:
                warnings.append(f"数字'{num}'在原始素材中未找到对应出处，可能存在引用错误")
        return warnings

    def _write_report(self, payload: dict[str, Any], *, run_dir: Path | None = None) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if run_dir is not None:
            target = run_dir / "content_guard_report.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _empty_report(self, mode: str = "topic_candidate") -> dict[str, Any]:
        return {
            "generated_at": self._now(),
            "mode": mode,
            "blocked": False,
            "similarity": 0.0,
            "threshold": 0.0,
            "matched_run_dir": "",
            "matched_title": "",
            "warnings": [],
            "ai_risk_score": 0.0,
            "ai_risk_level": "low",
            "human_review_required": False,
            "recommendations": [],
            "bad_words_blocked": False,
            "matched_bad_words": [],
        }

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
    
    def _load_title_and_article(self, run_dir: Path) -> tuple[str, str]:
        """从运行目录中加载标题和文章内容"""
        publish_dir = run_dir / self.settings.review.publish_dirname
        article_path = publish_dir / "article.md"
        title_path = publish_dir / "title.txt"
        
        if not article_path.exists():
            article_path = run_dir / "article.md"
        if not title_path.exists():
            title_path = run_dir / "titles.txt"
        
        title = ""
        article = ""
        
        if title_path.exists():
            try:
                title = title_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0].strip()
            except Exception:
                pass
        
        if article_path.exists():
            try:
                article = article_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        
        return title, article
