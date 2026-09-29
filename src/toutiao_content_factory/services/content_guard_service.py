"""内容安全守卫与文本合规组件。

本模块在文章发布前执行三道防线：
1. AC 自动机（Aho-Corasick）敏感词扫描 —— 支持增量更新与上下文语境放行；
2. SimHash + LSH 分桶的轻量查重引擎 —— 将全量比对降至平均 O(1)；
3. 精细化 AI 痕迹评分 —— 多维度词频密度 + 每千字归一化。

仅依赖标准库，保证轻量部署环境可用。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker


# ============================================================================
# 1) AC 自动机（TrieTree）—— 内存与吞吐量优化 + 增量更新 + 上下文语境识别
# ============================================================================


@dataclass(slots=True)
class TrieNode:
    """AC 自动机节点。

    优化点：
    - ``slots=True`` 降低实例内存开销；
    - ``outputs`` 用 ``set`` 存“直接结束”于本节点的敏感词，去重 O(1)；
    - ``matched`` 预计算“直接 + fail 链继承”的命中词元组，搜索时无需回溯 fail 链。
    """

    children: dict[str, "TrieNode"] = field(default_factory=dict)
    fail: "TrieNode | None" = None
    outputs: set[str] = field(default_factory=set)
    matched: tuple[str, ...] = ()


@dataclass(slots=True)
class ExclusionRule:
    """敏感词上下文放行规则。

    当 ``word`` 命中时，若其紧邻上下文满足规则则放行：
    - ``prefixes``: 命中位置**前方**文本以其中任一字符串结尾 → 触发；
    - ``suffixes``: 命中位置**后方**文本以其中任一字符串开头 → 触发；
    - ``mode``: ``"any"``（默认）前/后缀任一命中即放行；``"all"`` 须同时命中。

    示例：``ExclusionRule("自杀", prefixes=("非",))`` 可放行“非自杀式袭击”中的“自杀”。
    """

    word: str
    prefixes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    mode: str = "any"


@dataclass(slots=True)
class MatchHit:
    """一次命中的敏感词及其在文本中的区间 [start, end)。"""

    word: str
    start: int
    end: int


class SensitiveWordTrie:
    """AC 自动机敏感词匹配器。

    相比原实现的关键优化：
    1. 节点 ``__slots__`` + ``set`` outputs，降低内存与去重成本；
    2. ``matched`` 预计算继承输出，搜索为单次 O(N) 遍历，无 fail 链回溯开销；
    3. ``add_word`` / ``remove_word`` 增量更新 —— 仅按需惰性重建 fail 链，
       无需每次重建整棵 Trie（插入/删除本身 O(L)，L 为词长）；
    4. ``find_all_with_positions`` 返回命中位置，配合 ``ExclusionRule`` 实现上下文放行。
    """

    def __init__(self, words: Iterable[str] | None = None) -> None:
        self.root = TrieNode()
        self._word_set: set[str] = set()
        self._fail_dirty: bool = False
        self._lock = threading.RLock()
        cleaned = [str(w).strip() for w in (words or ()) if str(w).strip()]
        with self._lock:
            for w in cleaned:
                self._insert_word(w)
            if cleaned:
                self._rebuild_fail_links()
                self._fail_dirty = False

    # ---- 增量更新 API ----
    def add_word(self, word: str) -> bool:
        """增量新增敏感词，仅标记 fail 链为脏，下次搜索前惰性重建。"""
        word = str(word).strip()
        if not word:
            return False
        with self._lock:
            if word in self._word_set:
                return False
            self._insert_word(word)
            self._word_set.add(word)
            self._fail_dirty = True
            return True

    def remove_word(self, word: str) -> bool:
        """增量移除敏感词并剪枝空分支，仅标记 fail 链为脏。"""
        word = str(word).strip()
        if not word:
            return False
        with self._lock:
            if word not in self._word_set:
                return False
            self._remove_word(word)
            self._word_set.discard(word)
            self._fail_dirty = True
            return True

    def words(self) -> list[str]:
        return sorted(self._word_set)

    def __len__(self) -> int:
        return len(self._word_set)

    def __contains__(self, word: str) -> bool:
        return str(word).strip() in self._word_set

    # ---- 内部插入 / 删除 ----
    def _insert_word(self, word: str) -> None:
        node = self.root
        for char in word:
            node = node.children.setdefault(char, TrieNode())
        node.outputs.add(word)

    def _remove_word(self, word: str) -> None:
        path: list[TrieNode] = [self.root]
        node = self.root
        for char in word:
            if char not in node.children:
                return
            node = node.children[char]
            path.append(node)
        node.outputs.discard(word)
        # 自底向上剪枝：既无子节点也无直接输出的节点可删除
        for i in range(len(word), 0, -1):
            cur = path[i]
            if cur.children or cur.outputs:
                break
            parent = path[i - 1]
            del parent.children[word[i - 1]]

    # ---- fail 链构建（惰性）----
    def _ensure_fail_links(self) -> None:
        if not self._fail_dirty:
            return
        with self._lock:
            if not self._fail_dirty:
                return
            self._rebuild_fail_links()
            self._fail_dirty = False

    def _rebuild_fail_links(self) -> None:
        """重建 fail 链与 ``matched`` 预计算。

        复杂度 O(节点总数)，但仅在词库变更后触发一次；Trie 结构本身保持不变。
        BFS 顺序保证 ``child.fail`` 的 ``matched`` 先于 ``child`` 计算完成。
        """
        root = self.root
        root.fail = None
        root.matched = tuple(root.outputs)
        queue: deque[TrieNode] = deque()
        for child in root.children.values():
            child.fail = root
            child.matched = tuple(child.outputs)
            queue.append(child)
        while queue:
            node = queue.popleft()
            for char, child in node.children.items():
                fail = node.fail
                while fail is not None and char not in fail.children:
                    fail = fail.fail
                child.fail = fail.children[char] if (fail is not None and char in fail.children) else root
                inherited = child.fail.matched
                child.matched = tuple(child.outputs) + inherited if child.outputs else inherited
                queue.append(child)

    # ---- 搜索 ----
    def find_all(self, text: str) -> list[str]:
        """向后兼容：返回所有命中词（含跨位置重复）。"""
        return [hit.word for hit in self.find_all_with_positions(text)]

    def find_all_with_positions(self, text: str) -> list[MatchHit]:
        """返回带位置的命中列表，供上下文放行规则使用。"""
        if not text:
            return []
        self._ensure_fail_links()
        root = self.root
        node = root
        hits: list[MatchHit] = []
        for idx, char in enumerate(text):
            while node is not root and char not in node.children:
                node = node.fail or root
            child = node.children.get(char)
            if child is not None:
                node = child
            else:
                node = root
            if node.matched:
                for word in node.matched:
                    start = idx - len(word) + 1
                    hits.append(MatchHit(word=word, start=start, end=idx + 1))
        return hits


# ============================================================================
# 2) SimHash + LSH 分桶的轻量查重引擎
# ============================================================================


class SimHashIndex:
    """基于 SimHash + LSH 分桶的轻量查重索引。

    - 64 位指纹，8 个 8-bit band。查询时仅在共享至少一个 band 的候选上做精细比对，
      平均检索 O(1)，最坏 O(N)（N 为历史文章数）。
    - 海明距离阈值 ``hamming_threshold`` 用于近重复硬判定。
    - 指纹用 ``hashlib.md5`` 生成，跨进程稳定（不依赖 PYTHONHASHSEED）。
    """

    BITS = 64
    NUM_BANDS = 8
    BAND_BITS = BITS // NUM_BANDS  # 8

    def __init__(self, hamming_threshold: int = 3) -> None:
        self.hamming_threshold = hamming_threshold
        self._band_masks = tuple(
            ((1 << self.BAND_BITS) - 1) << (b * self.BAND_BITS) for b in range(self.NUM_BANDS)
        )
        self._band_shifts = tuple(b * self.BAND_BITS for b in range(self.NUM_BANDS))
        self._buckets: list[dict[int, list[int]]] = [defaultdict(list) for _ in range(self.NUM_BANDS)]
        self._fingerprints: list[int] = []
        self._items: list[dict[str, Any]] = []

    def __len__(self) -> int:
        return len(self._items)

    @staticmethod
    def _hash64(token: str) -> int:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "little")

    def compute(self, tokens: Iterable[str]) -> int:
        """对 token 序列计算 64 位 SimHash 指纹。"""
        v = [0] * self.BITS
        for token in tokens:
            h = self._hash64(token)
            for i in range(self.BITS):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1
        fp = 0
        for i in range(self.BITS):
            if v[i] > 0:
                fp |= (1 << i)
        return fp

    def add(self, tokens: Iterable[str], item: dict[str, Any]) -> int:
        """加入一篇文章，返回其索引。"""
        fp = self.compute(tokens)
        idx = len(self._items)
        self._items.append(item)
        self._fingerprints.append(fp)
        for b in range(self.NUM_BANDS):
            key = (fp & self._band_masks[b]) >> self._band_shifts[b]
            self._buckets[b][key].append(idx)
        return idx

    def item(self, idx: int) -> dict[str, Any]:
        return self._items[idx]

    def fingerprint(self, idx: int) -> int:
        return self._fingerprints[idx]

    def hamming(self, fp_a: int, fp_b: int) -> int:
        return (fp_a ^ fp_b).bit_count()

    def query_candidates(self, fp: int) -> list[int]:
        """返回所有与 ``fp`` 共享至少一个 band 的候选索引（去重）。"""
        seen: set[int] = set()
        for b in range(self.NUM_BANDS):
            key = (fp & self._band_masks[b]) >> self._band_shifts[b]
            bucket = self._buckets[b].get(key)
            if bucket:
                seen.update(bucket)
        return list(seen)


# ============================================================================
# 2.5) LightSimHash —— 纯 Python 轻量 SimHash（模块 B 合并新增）
# ============================================================================


class LightSimHash:
    """纯 Python 实现的轻量 SimHash 指纹。

    使用 hashlib.md5 代替 murmurhash，避免 Windows 下 C 扩展编译依赖。
    用于 ContentGuardService 的 history_hashes 快速近重复判定。
    """

    def __init__(self, text: str, f: int = 64) -> None:
        self.f = f
        self.value = self._simhash(text)

    def _simhash(self, text: str) -> int:
        tokens = re.findall(r"[\u4e00-\u9fa5]+|\w+", text)
        if not tokens:
            return 0
        v = [0] * self.f
        for token in tokens:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            for i in range(self.f):
                if (h >> i) & 1:
                    v[i] += 1
                else:
                    v[i] -= 1
        fingerprint = 0
        for i in range(self.f):
            if v[i] >= 0:
                fingerprint |= 1 << i
        return fingerprint

    def similarity(self, other: "LightSimHash") -> float:
        """计算海明距离并转换为 0.0 ~ 1.0 的相似度。"""
        x = self.value ^ other.value
        ans = 0
        while x:
            ans += 1
            x &= x - 1
        return 1.0 - (ans / self.f)


# ============================================================================
# 3) ContentGuardService —— 整合三道防线
# ============================================================================


class ContentGuardService:
    TOPIC_SIMILARITY_THRESHOLD = 0.70
    RUN_SIMILARITY_THRESHOLD = 0.76
    AI_RISK_THRESHOLD = 0.62
    HUMAN_REVIEW_THRESHOLD = 0.45
    REVIEW_REJECTED_STATUS = "rejected"
    REVIEW_REJECTED_LABEL = "审核驳回（需人工干预）"
    REVIEW_REJECTED_ACTION = "manual_intervention_required"

    # SimHash 近重复硬判定阈值（海明距离 ≤ 此值视为近重复）
    SIMHASH_HAMMING_THRESHOLD = 3
    # 历史库规模 ≤ 此值时走全量精确比对，保证小库零假阴；超过则走 LSH 候选检索
    FULL_SCAN_MAX_N = 64

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

    # AI 陈词模板（兜底，运行时会与 config/ai_cliche_words.txt 合并）
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

    # AI 高频过渡 / 结构词（段落骨架同质化信号）
    AI_TRANSITION_PHRASES = (
        "首先",
        "其次",
        "再次",
        "最后",
        "一方面",
        "另一方面",
        "与此同时",
        "不仅如此",
        "更为重要的是",
        "总的来说",
        "从另一个角度看",
        "换言之",
        "由此看来",
        "由此可见",
    )

    # AI/互联网黑话密度词（空泛抽象信号）
    AI_BUZZWORD_PHRASES = (
        "双刃剑",
        "深入探讨",
        "深度解析",
        "一文读懂",
        "干货满满",
        "赋能",
        "抓手",
        "闭环",
        "链路",
        "颗粒度",
        "底层逻辑",
        "顶层设计",
        "降本增效",
        "破局",
        "重塑",
        "颠覆",
        "赛道",
        "风口",
        "红利",
        "生态",
        "矩阵",
        "沉淀",
        "打磨",
        "迭代",
        "对标",
        "拉齐",
        "对齐",
        "组合拳",
        "护城河",
        "基本盘",
        "第二增长曲线",
    )

    # 上下文放行默认规则（可在 config/exclusion_rules.txt 覆盖/扩展）
    DEFAULT_EXCLUSION_RULES = (
        ExclusionRule(word="自杀", prefixes=("非",), mode="any"),
    )

    NUMBER_PATTERN = re.compile(
        r"\d[\d,]*(?:\.\d+)?(?:%|倍|年|亿元|亿美元|万元|万台|万辆|万套|万吨|GWh|MWh|kWh|GW|MW|kW)"
    )
    _ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9%]{2,}")
    _NON_WORD_REPLACE_PATTERN = re.compile(r"[a-z0-9%]+")
    _WHITESPACE_PATTERN = re.compile(r"\s+")
    _INVALID_CHAR_PATTERN = re.compile(r"[^\w\u4e00-\u9fff%]")
    _SINGLE_DIGIT_PATTERN = re.compile(r"\d")

    def __init__(self, settings: AppSettings, logger) -> None:
        self.settings = settings
        self.logger = logger
        # 模块 B 合并新增：读取 content_guard 配置（若无则使用默认值，向下兼容）
        self.guard_config = getattr(settings, "content_guard", None)
        self.report_path = self.settings.automation_dir / "content_guard_report.json"
        # 敏感词路径优先从 content_guard 配置读取，兜底原有默认路径
        if self.guard_config and getattr(self.guard_config, "sensitive_words_path", ""):
            self.bad_words_path = self.settings.project_root / self.guard_config.sensitive_words_path
        else:
            self.bad_words_path = self.settings.project_root / "config" / "bad_words.txt"
        self._ensure_bad_words_file()
        self.bad_words = self._load_bad_words()
        self.bad_words_trie = SensitiveWordTrie(self.bad_words)
        self.exclusion_rules = self._load_exclusion_rules()
        self.ai_cliche_phrases = self._load_ai_cliche_phrases()
        self.quality_checker = ArticleQualityChecker()
        # 查重索引缓存：signature -> (corpus, SimHashIndex)
        self._corpus_cache: dict[str, Any] = {}
        self._cache_lock = threading.RLock()
        # 模块 B 合并新增：LightSimHash 历史指纹缓存（近15天文章）
        self.history_hashes: list[LightSimHash] = []

    # ----------------------------------------------------------------------
    # 对外主流程
    # ----------------------------------------------------------------------

    def evaluate_candidate_topic(self, *, topic_title: str, topic_url: str = "") -> dict[str, Any]:
        corpus, _ = self._get_corpus_and_index()
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

        best_match, best_score, best_hamming = self._find_best_match(target_text)
        blocked = bool(best_match and best_score >= self.TOPIC_SIMILARITY_THRESHOLD) or (
            best_hamming <= self.SIMHASH_HAMMING_THRESHOLD
        )
        warnings = self._build_similarity_warnings(best_match, best_score, self.TOPIC_SIMILARITY_THRESHOLD)
        if best_hamming <= self.SIMHASH_HAMMING_THRESHOLD:
            warnings.append(
                f"SimHash 海明距离 {best_hamming} ≤ {self.SIMHASH_HAMMING_THRESHOLD}，判定为近重复选题。"
            )

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
            "simhash_hamming_distance": best_hamming,
            "simhash_hamming_threshold": self.SIMHASH_HAMMING_THRESHOLD,
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
        editorial_integrity = self.quality_checker.check_editorial_integrity(
            title,
            article,
            require_body=True,
        )
        editorial_blockers = self._editorial_integrity_flags(editorial_integrity)
        domain_issues = self.quality_checker.check_topic_domain_consistency(
            article, self._selected_topic_type(run_dir)
        )
        editorial_blockers.extend(f"题材一致性硬拦截：{issue}" for issue in domain_issues)

        raw_text = "\n".join(part for part in (title, article) if part)
        target_text = self._normalize_text(raw_text)
        corpus, _ = self._get_corpus_and_index()
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
                    "writing_integrity": editorial_integrity,
                    "blocking_flags": editorial_blockers,
                }
            )
            self._write_report(report, run_dir=run_dir)
            self._apply_review_override_from_report(run_dir, report)
            return report
        if not target_text or not corpus:
            report = self._empty_report(mode="run")
            report["run_dir"] = str(run_dir)
            report["blocked"] = bool(domain_issues)
            report["writing_integrity"] = editorial_integrity
            report["blocking_flags"] = editorial_blockers
            report["human_review_required"] = bool(editorial_blockers)
            self._write_report(report, run_dir=run_dir)
            self._apply_review_override_from_report(run_dir, report)
            return report

        best_match, best_score, best_hamming = self._find_best_match(target_text, exclude_run_dir=run_dir)
        ai_risk = self._estimate_ai_giness(title, article)
        blocked = bool(best_match and best_score >= self.RUN_SIMILARITY_THRESHOLD) or (
            best_hamming <= self.SIMHASH_HAMMING_THRESHOLD
        )
        blocked = blocked or bool(domain_issues)
        warnings = self._build_similarity_warnings(best_match, best_score, self.RUN_SIMILARITY_THRESHOLD)
        if best_hamming <= self.SIMHASH_HAMMING_THRESHOLD:
            warnings.append(
                f"SimHash 海明距离 {best_hamming} ≤ {self.SIMHASH_HAMMING_THRESHOLD}，判定为近重复文章。"
            )
        warnings.extend(self._build_ai_risk_warnings(ai_risk))
        # 数据引用校验
        number_warnings = self._check_number_attribution(article, run_dir)
        warnings.extend(number_warnings)
        # 原创性：与素材原文的连续抄录率。头条原创库全网比对，素材常被别的
        # 账号先发，这一项本地不查就永远发现不了（实测 51.5% 抄录被扣 5 分）。
        originality = self._check_source_originality(run_dir, article)
        warnings.extend(originality["flags"])
        if originality["block"]:
            blocked = True

        review_required = bool(
            best_score >= self.HUMAN_REVIEW_THRESHOLD
            or ai_risk >= self.HUMAN_REVIEW_THRESHOLD
            or editorial_blockers
            or originality["review"]
        )
        payload = {
            "generated_at": self._now(),
            "mode": "run",
            "blocked": blocked,
            "similarity": round(best_score, 4),
            "threshold": self.RUN_SIMILARITY_THRESHOLD,
            "simhash_hamming_distance": best_hamming,
            "simhash_hamming_threshold": self.SIMHASH_HAMMING_THRESHOLD,
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
            "writing_integrity": editorial_integrity,
            "blocking_flags": editorial_blockers,
            "source_copy_ratio": originality["ratio"],
            "source_copy_chars": originality["copied_chars"],
            "source_copy_reason": originality["reason"],
            "source_copy_samples": originality["samples"],
        }
        self._write_report(payload, run_dir=run_dir)
        self._apply_review_override_from_report(run_dir, payload)
        return payload

    def _check_source_originality(self, run_dir: Path, article: str) -> dict[str, object]:
        """量化与素材原文的连续抄录率，返回供报告使用的结构化结果。"""
        from toutiao_content_factory.services.originality_service import (
            BLOCK_THRESHOLD,
            REVIEW_THRESHOLD,
            originality_flags,
            source_copy_ratio,
        )

        result: dict[str, object] = {
            "ratio": None,
            "copied_chars": 0,
            "reason": "",
            "samples": [],
            "flags": [],
            "review": False,
            "block": False,
        }
        source_file = Path(run_dir) / "source_material_full.txt"
        if not source_file.exists():
            result["reason"] = "无素材原文（多为热榜选题），无法查重"
            return result
        try:
            source_text = source_file.read_text(encoding="utf-8")
        except Exception:
            result["reason"] = "素材原文读取失败"
            return result

        outcome = source_copy_ratio(article, source_text)
        ratio = outcome["ratio"]
        result["copied_chars"] = outcome["copied_chars"]
        result["samples"] = outcome["samples"]
        if ratio is None:
            result["reason"] = str(outcome["reason"])
            return result
        result["ratio"] = ratio
        result["flags"] = originality_flags(ratio)
        result["review"] = ratio >= REVIEW_THRESHOLD
        result["block"] = ratio >= BLOCK_THRESHOLD
        return result

    @staticmethod
    def _editorial_integrity_flags(integrity: dict[str, Any]) -> list[str]:
        blockers = integrity.get("hard_blockers", []) if isinstance(integrity, dict) else []
        return list(
            dict.fromkeys(
                f"写作质量硬拦截：{str(item).strip()}"
                for item in blockers
                if str(item).strip()
            )
        )

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
        editorial_integrity = report.get("writing_integrity", {})
        if not isinstance(editorial_integrity, dict):
            editorial_integrity = self.quality_checker.check_editorial_integrity(
                title,
                article,
                require_body=True,
            )
        blockers.extend(self._editorial_integrity_flags(editorial_integrity))
        blockers.extend(
            str(item).strip()
            for item in report.get("blocking_flags", [])
            if str(item).strip()
        )
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
        similarity_threshold = float(report.get("threshold", 0.0) or 0.0)
        similarity_blocked = (
            similarity_threshold > 0
            and float(report.get("similarity", 0.0) or 0.0) >= similarity_threshold
        ) or (
            int(report.get("simhash_hamming_distance", SimHashIndex.BITS))
            <= int(report.get("simhash_hamming_threshold", self.SIMHASH_HAMMING_THRESHOLD))
        )
        if similarity_blocked and not bool(report.get("bad_words_blocked")):
            blockers.append("Content is too similar to a recently published run and should be revised before publishing.")
        if float(report.get("ai_risk_score", 0.0) or 0.0) >= self.AI_RISK_THRESHOLD:
            blockers.append("AI-style risk is high. Add more concrete facts, quotes, and human edits before publishing.")
        payload = {
            **report,
            "blocking_flags": self._unique_texts(blockers),
            "warnings": self._unique_texts(warnings),
            "recommendations": self._unique_texts(recommendations),
            "review_status_override": {},
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
            "writing_integrity": editorial_integrity,
        }
        if payload["blocking_flags"]:
            payload["review_status_override"] = self._review_override_payload(payload)
            if payload["review_status_override"]:
                self.apply_review_rejection(run_dir, payload["review_status_override"])
        self._write_report(payload, run_dir=run_dir)
        return payload

    # ----------------------------------------------------------------------
    # 敏感词扫描（上下文语境放行）
    # ----------------------------------------------------------------------

    def scan_text(self, text: str) -> dict[str, Any]:
        normalized = self._normalize_for_sensitive_scan(text)
        if not normalized or not self.bad_words:
            return {"blocked": False, "matched_words": [], "warnings": []}
        hits = self.bad_words_trie.find_all_with_positions(normalized)
        hits = self._filter_with_exclusions(hits, normalized)
        matched = self._unique_texts([hit.word for hit in hits])
        if not matched:
            return {"blocked": False, "matched_words": [], "warnings": []}
        return {
            "blocked": True,
            "matched_words": matched,
            "warnings": [f"红线警告：命中本地敏感词/违禁词 -> {', '.join(matched[:10])}"],
        }

    def _filter_with_exclusions(self, hits: list[MatchHit], text: str) -> list[MatchHit]:
        """根据 ``exclusion_rules`` 对命中结果做上下文放行。"""
        if not self.exclusion_rules:
            return hits
        kept: list[MatchHit] = []
        for hit in hits:
            rules = self.exclusion_rules.get(hit.word)
            if not rules:
                kept.append(hit)
                continue
            suppressed = False
            for rule in rules:
                prefix_hit = False
                if rule.prefixes:
                    for p in rule.prefixes:
                        if text[max(0, hit.start - len(p)):hit.start] == p:
                            prefix_hit = True
                            break
                suffix_hit = False
                if rule.suffixes:
                    for s in rule.suffixes:
                        if text[hit.end:hit.end + len(s)] == s:
                            suffix_hit = True
                            break
                if rule.mode == "all":
                    if rule.prefixes and rule.suffixes:
                        cond = prefix_hit and suffix_hit
                    elif rule.prefixes:
                        cond = prefix_hit
                    else:
                        cond = suffix_hit
                else:
                    cond = prefix_hit or suffix_hit
                if cond:
                    suppressed = True
                    break
            if not suppressed:
                kept.append(hit)
        return kept

    # ----------------------------------------------------------------------
    # 敏感词词库增量管理
    # ----------------------------------------------------------------------

    def reload_bad_words(self) -> list[str]:
        """重载敏感词文件，对差集做增量增删，避免整树重建。"""
        new_words = self._load_bad_words()
        new_set = set(new_words)
        old_set = set(self.bad_words)
        for word in old_set - new_set:
            self.bad_words_trie.remove_word(word)
        for word in new_set - old_set:
            self.bad_words_trie.add_word(word)
        self.bad_words = new_words
        return self.bad_words

    def add_sensitive_word(self, word: str) -> bool:
        normalized = self._normalize_text(word)
        if not normalized:
            return False
        if self.bad_words_trie.add_word(normalized):
            if normalized not in self.bad_words:
                self.bad_words.append(normalized)
            return True
        return False

    def remove_sensitive_word(self, word: str) -> bool:
        normalized = self._normalize_text(word)
        if not normalized:
            return False
        if self.bad_words_trie.remove_word(normalized):
            if normalized in self.bad_words:
                self.bad_words.remove(normalized)
            return True
        return False

    def add_exclusion_rule(self, rule: ExclusionRule) -> None:
        self.exclusion_rules.setdefault(rule.word, []).append(rule)

    # ----------------------------------------------------------------------
    # 查重：SimHash + LSH 候选检索 + 余弦 rerank
    # ----------------------------------------------------------------------

    def _corpus_signature(self) -> tuple[tuple[str, float], ...]:
        runs_root = self.settings.runs_dir
        if not runs_root.exists():
            return ()
        items: list[tuple[str, float]] = []
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                items.append((run_dir.name, run_dir.stat().st_mtime))
            except OSError:
                continue
        return tuple(sorted(items))

    def _get_corpus_and_index(self) -> tuple[list[dict[str, Any]], SimHashIndex]:
        """加载历史发布语料并构建/复用 SimHash 索引（按目录签名缓存）。"""
        with self._cache_lock:
            sig = self._corpus_signature()
            cached = self._corpus_cache.get("entry")
            if cached is None or cached[0] != sig:
                corpus = self._load_recent_published_corpus()
                index = SimHashIndex(hamming_threshold=self.SIMHASH_HAMMING_THRESHOLD)
                for item in corpus:
                    tokens = self._tokenize(str(item.get("text", "")))
                    index.add(tokens, item)
                self._corpus_cache["entry"] = (sig, corpus, index)
                return corpus, index
            return cached[1], cached[2]

    def _find_best_match(
        self,
        target_text: str,
        *,
        exclude_run_dir: Path | None = None,
    ) -> tuple[dict[str, Any] | None, float, int]:
        """返回 (最佳匹配项, 余弦相似度, 最小海明距离)。

        - 历史库 ≤ ``FULL_SCAN_MAX_N``：全量精确比对，零假阴；
        - 否则：LSH 候选检索 O(1) 平均 + 余弦 rerank，最坏 O(N)。
        """
        corpus, index = self._get_corpus_and_index()
        if not corpus:
            return None, 0.0, index.BITS

        target_tokens = self._tokenize(target_text)
        target_fp = index.compute(target_tokens)
        exclude_resolved = exclude_run_dir.resolve() if exclude_run_dir is not None else None

        best_match: dict[str, Any] | None = None
        best_score = 0.0
        best_hamming = index.BITS

        if len(corpus) <= self.FULL_SCAN_MAX_N:
            indices: Iterable[int] = range(len(corpus))
        else:
            indices = index.query_candidates(target_fp)

        for idx in indices:
            item = index.item(idx)
            if self._is_excluded_item(item, exclude_resolved):
                continue
            score = self._cosine_similarity(target_text, str(item.get("text", "")))
            if score > best_score:
                best_score = score
                best_match = item
            h = index.hamming(target_fp, index.fingerprint(idx))
            if h < best_hamming:
                best_hamming = h
        return best_match, best_score, best_hamming

    def _is_excluded_item(self, item: dict[str, Any], exclude_resolved: Path | None) -> bool:
        if exclude_resolved is None:
            return False
        try:
            return Path(str(item.get("run_dir", ""))).resolve() == exclude_resolved
        except Exception:
            return False

    # ----------------------------------------------------------------------
    # AI 痕迹评分（精细化）
    # ----------------------------------------------------------------------

    def _estimate_ai_giness(self, title: str, article: str) -> float:
        """精细化“去 AI 味”评分。

        维度（权重和为 1.0）：
        - 段落开头重复率 0.22
        - AI 陈词密度      0.25
        - 过渡结构词密度   0.18
        - 互联网黑话密度   0.15
        - 感叹号密度       0.10
        - 缺乏实据（数字/引语越少分越高）0.10

        所有密度按“每千字命中数”归一化，避免长文本被低估。
        """
        text = str(article or "")
        full_text = "\n".join(part for part in (title, article) if part)
        normalized = self._normalize_text(full_text)
        if not normalized:
            return 0.0

        paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
        char_count = max(len(normalized), 1)
        kchars = max(char_count / 1000.0, 1.0)

        repeated_openers = self._repeated_paragraph_opener_ratio(paragraphs)
        cliche_density = self._phrase_density_per_kchars(full_text, self.ai_cliche_phrases, kchars)
        transition_density = self._phrase_density_per_kchars(full_text, self.AI_TRANSITION_PHRASES, kchars)
        buzzword_density = self._phrase_density_per_kchars(full_text, self.AI_BUZZWORD_PHRASES, kchars)
        exclamation_ratio = min(text.count("！") / max(len(paragraphs), 1), 1.0)
        number_ratio = min(len(self._SINGLE_DIGIT_PATTERN.findall(full_text)) / char_count * 20, 1.0)
        quote_ratio = min((full_text.count("“") + full_text.count("\"")) / max(len(paragraphs), 1), 1.0)
        evidence_ratio = min(1.0, number_ratio + quote_ratio * 0.5)

        risk = (
            0.22 * repeated_openers
            + 0.25 * cliche_density
            + 0.18 * transition_density
            + 0.15 * buzzword_density
            + 0.10 * exclamation_ratio
            + 0.10 * max(0.0, 1.0 - evidence_ratio)
        )
        return round(max(0.0, min(risk, 1.0)), 4)

    def _phrase_density_per_kchars(
        self, text: str, phrases: tuple[str, ...], kchars: float
    ) -> float:
        """每千字命中密度，3 次/千字即满档。"""
        if not phrases:
            return 0.0
        lowered = str(text or "")
        hits = sum(lowered.count(phrase) for phrase in phrases)
        return min(hits / kchars / 3.0, 1.0)

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
        """旧版固定阈值密度（保留向后兼容）。"""
        lowered = str(text or "")
        hits = sum(lowered.count(phrase) for phrase in phrases)
        return min(hits / 6, 1.0)

    def _classify_ai_risk(self, score: float) -> str:
        if score >= self.AI_RISK_THRESHOLD:
            return "high"
        if score >= self.HUMAN_REVIEW_THRESHOLD:
            return "medium"
        return "low"

    # ----------------------------------------------------------------------
    # 审核 / 报告辅助
    # ----------------------------------------------------------------------

    def build_review_rejection_payload(
        self,
        *,
        matched_words: list[str],
        extra_notes: list[str] | None = None,
        guard_reason: str = "bad_words",
    ) -> dict[str, Any]:
        matched = self._unique_texts(matched_words)
        if matched:
            note = f"{self.REVIEW_REJECTED_LABEL}：命中本地敏感词/违禁词 -> {', '.join(matched[:10])}"
        else:
            note = f"{self.REVIEW_REJECTED_LABEL}：{guard_reason}硬拦截"
        if extra_notes:
            note = f"{note}；{'；'.join(self._unique_texts(extra_notes)[:3])}"
        return {
            "status": self.REVIEW_REJECTED_STATUS,
            "status_label": self.REVIEW_REJECTED_LABEL,
            "recommended_action": self.REVIEW_REJECTED_ACTION,
            "publish_recommendation": "revise_before_publish",
            "reviewer_notes": note,
            "guard_blocked": True,
            "guard_reason": guard_reason,
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
        bad_words_blocked = bool(report.get("bad_words_blocked"))
        blocking_flags = [
            str(item).strip()
            for item in report.get("blocking_flags", [])
            if str(item).strip()
        ]
        if not bad_words_blocked and not blocking_flags:
            return {}
        return self.build_review_rejection_payload(
            matched_words=list(report.get("matched_bad_words", [])),
            extra_notes=(
                blocking_flags
                + (list(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else [])
            ),
            guard_reason="bad_words" if bad_words_blocked else "writing_quality",
        )

    def _apply_review_override_from_report(self, run_dir: Path, report: dict[str, Any]) -> Path | None:
        override = self._review_override_payload(report)
        if not override:
            return None
        status_path = self.apply_review_rejection(run_dir, override)
        matched_words = ", ".join(list(report.get("matched_bad_words", []))[:10])
        self.logger.error(
            "Content guard rejection triggered for run %s: %s",
            run_dir,
            matched_words or " | ".join(list(report.get("blocking_flags", []))[:3]),
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

    def _load_exclusion_rules(self) -> dict[str, list[ExclusionRule]]:
        rules: dict[str, list[ExclusionRule]] = {}
        for rule in self.DEFAULT_EXCLUSION_RULES:
            rules.setdefault(rule.word, []).append(rule)
        path = self.settings.project_root / "config" / "exclusion_rules.txt"
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # 格式：word|prefix1,prefix2|suffix1,suffix2|mode
                    parts = line.split("|")
                    word = parts[0].strip()
                    if not word:
                        continue
                    prefixes = (
                        tuple(p.strip() for p in parts[1].split(",") if p.strip())
                        if len(parts) > 1 and parts[1].strip()
                        else ()
                    )
                    suffixes = (
                        tuple(s.strip() for s in parts[2].split(",") if s.strip())
                        if len(parts) > 2 and parts[2].strip()
                        else ()
                    )
                    mode = parts[3].strip() if len(parts) > 3 and parts[3].strip() in ("any", "all") else "any"
                    rules.setdefault(word, []).append(
                        ExclusionRule(word=word, prefixes=prefixes, suffixes=suffixes, mode=mode)
                    )
            except Exception:
                pass
        return rules

    def _load_ai_cliche_phrases(self) -> tuple[str, ...]:
        phrases: set[str] = set(self.AI_CLICHE_PHRASES)
        path = self.settings.project_root / "config" / "ai_cliche_words.txt"
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    word = line.strip()
                    if word and not word.startswith("#"):
                        phrases.add(word)
            except Exception:
                pass
        return tuple(phrases)

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

    def _normalize_for_sensitive_scan(self, text: str) -> str:
        """敏感词扫描专用归一化：标点/符号替换为占位分隔符而不是删除。

        修复（2026-08-27 实测误杀）：原 _normalize_text 会把标点一并删除，
        导致「观察窗口。交付能力」被拼成「观察窗口交付能力」，跨标点拼接出
        敏感词「口交」误杀正常文章（今日「谐波减速器 IPO」选题被拦的根因）。
        这里保留词边界，仅把非词字符归一为 \x00 占位，AC 匹配不会跨标点拼词。
        """
        normalized = self._WHITESPACE_PATTERN.sub("", str(text or "").lower())
        normalized = self._INVALID_CHAR_PATTERN.sub("\x00", normalized)
        return normalized[:12000]

    def _parse_run_time(self, name: str) -> datetime | None:
        raw = str(name or "").split("_", 1)[0]
        try:
            return datetime.strptime(raw, "%Y%m%d-%H%M%S")
        except ValueError:
            return None

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
        normalized = self._normalize_for_sensitive_scan(text)
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

        def normalize_number(value: str) -> str:
            return re.sub(r"(?<=\d),(?=\d)", "", str(value or ""))

        normalized_source_numbers = {normalize_number(num) for num in source_numbers}
        for num in article_numbers:
            if normalize_number(num) not in normalized_source_numbers:
                warnings.append(f"数字'{num}'在原始素材中未找到对应出处，可能存在引用错误")
        return warnings

    @staticmethod
    def _selected_topic_type(run_dir: Path) -> str:
        path = Path(run_dir) / "selected_topic.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        topic = payload.get("selected_topic", payload)
        return str(topic.get("topic_type", "")).strip() if isinstance(topic, dict) else ""

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
            "simhash_hamming_distance": SimHashIndex.BITS,
            "simhash_hamming_threshold": self.SIMHASH_HAMMING_THRESHOLD,
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
                title = self._parse_title_file(
                    title_path.read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                pass

        if article_path.exists():
            try:
                article = article_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        return title, article

    @staticmethod
    def _parse_title_file(raw_text: str) -> str:
        """Read either a single title file or the exported titles package."""

        lines = [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
        if not lines:
            return ""

        first_line = lines[0]
        if not first_line.startswith(("全部标题", "推荐标题")):
            return first_line

        first_candidate = ""
        in_recommended_section = False
        for line in lines[1:]:
            if line.startswith("推荐标题"):
                in_recommended_section = True
                continue
            if line.startswith("全部标题"):
                continue
            match = re.match(r"^\d+\s*[.、]\s*(.+?)\s*$", line)
            if not match:
                continue
            candidate = match.group(1).strip()
            if not candidate:
                continue
            if in_recommended_section:
                return candidate
            if not first_candidate:
                first_candidate = candidate
        return first_candidate
