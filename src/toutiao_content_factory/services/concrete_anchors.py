"""具象锚点（Concrete Anchor）V2。

## 为什么要有这一层

标题闸门里有一道「标题必须承接正文的具象信息」的检查，用来挡住
「情绪模板 + 空泛主题词」的零点击标题（典型：`{主体}{数字}：{细节}如何实测`）。
但它近 100% 判 FAIL，实测 108 个历史 run 里 63.9% 是「一条候选都过不了」。

复盘发现**不是标题的问题，是锚点提取的问题**：

1. **量化值被截断**。单位表按长度倒序做最长匹配，但表里没有 `个月` / 量纲组合，
   于是 `18个月` → `18个`、`460万台` → `460万`。标题写 `460万台目标` 反而匹配不上。
2. **公司名只认后缀**。旧实现用 `[\\u4e00-\\u9fa5A-Za-z]{2,}(?:公司|集团|科技|…)`，
   于是 `进军人形机器人这类公司` 被整段当公司名，而正文里真正的 `比亚迪`
   （无后缀）**根本没被提取** —— 该选题所有提到比亚迪的标题全部判「缺具象钩子」。
3. **地名规则完全不可用**。`[\\u4e00-\\u9fa5]{2,6}(?:省|市|县|区)` 在 457 篇真实正文上
   命中 644 次，`市` 后面接 `场` 就占 333 次（`资本市场` → `资本市`）；
   加上「后缀后不能紧跟中文字」的守卫后仍有 42 次，约 85% 还是 `上市` / `节省` /
   `评论区` 的碎片。所以这里**整条去掉**地名通道 —— 宁可少一类锚点，也不要脏锚点。
4. **兜底被当成通过**。旧实现 `if not body_elements: return True`：正文提不出元素时
   静默放行，既不报错也不留痕，我们根本不知道闸门有没有生效。

## 三态

`concrete_hook_status(title, body)` 返回：

- `PASS`        —— 标题命中了正文里的**高置信度**锚点
- `FAIL`        —— 正文**有**足够的高置信度锚点，但这条标题一个都没承接
- `UNAVAILABLE` —— 正文提不出足够的高置信度锚点。此时**不判标题有罪**，但必须
                  **如实记录**（`concrete_hook_unavailable`），因为它暴露的是
                  **正文太薄**，而不是标题不好。旧实现把这一支静默当成 PASS。

## 置信度

- `company`（词典命中，或带后缀且通过 `is_valid_company_name`）→ 0.9
- `quantity`（带数量级 万/亿/千，或 `%`/`倍`，或 ≥3 位数字）→ 0.8
- `quantity`（1–2 位数字 + 普通量词，如 `18个月` / `9秒`）→ 0.45
- `year`（四位年份）→ 0.5

闸门只认 ≥ `GATE_MIN_CONFIDENCE` 的锚点。年份与零散小数字**不构成**标题必须承接的
义务 —— 否则「标题必须引用正文里某一个顺带提到的数字」会退化成抽签。
"""

from __future__ import annotations

import re
import unicodedata

from toutiao_content_factory.services.company_entities import (
    extract_known_company_names,
    is_valid_company_name,
)

KIND_COMPANY = "company"
KIND_QUANTITY = "quantity"
KIND_YEAR = "year"

CONF_COMPANY = 0.9
CONF_QUANTITY_STRONG = 0.8
CONF_QUANTITY_WEAK = 0.45
CONF_YEAR = 0.5

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNAVAILABLE = "UNAVAILABLE"

#: 闸门只认这个置信度以上的锚点。
GATE_MIN_CONFIDENCE = 0.7
#: 正文至少要提供这么多高置信度锚点，闸门才算「有意义」；否则记 UNAVAILABLE。
#: 阈值由 108 个历史 run 的锚点分布定（见 docs/TITLE_GATE_BLOCKER_REANALYSIS_20260917.md）：
#: 锚点 0–1 个的 run 里，候选通过率为 0；≥2 个后才开始有候选通过。
GATE_MIN_ANCHORS = 2

#: 数量级（万/亿/千）—— 单独出现也算信息（`2.5万`）。
QUANTITY_SCALES = ("万", "亿", "千")

#: 量纲单位，**按长度倒序**做最长匹配（`个月` 不能被切成 `个`）。
QUANTITY_UNITS = tuple(
    sorted(
        {
            "万美元", "亿美元", "平方米", "平方公里", "个百分点",
            "美元", "亿元", "万元", "个月", "人次", "公里", "小时", "分钟",
            "元", "台", "套", "件", "个", "家", "名", "次", "条", "款", "辆",
            "吨", "米", "秒", "天", "周", "月", "年", "度", "倍", "瓦", "亩",
            "艘", "架", "部", "人", "%", "％",
        },
        key=len,
        reverse=True,
    )
)

#: 这些单位的数字即使很小也算「强」信息（比例/倍数）。
_STRONG_UNITS = frozenset({"%", "％", "倍"})

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
#: 公司名兜底通道的**后缀**。这里刻意不用 `company_entities.COMPANY_SUFFIX_RE`：
#: 它的 `技术` / `材料` 等后缀会把 `随着技术`、`从公开材料` 判成公司名。
_COMPANY_SUFFIX_RE = re.compile(
    r"股份有限公司|有限责任公司|有限公司|集团|科技|证券|银行|研究院|实验室"
)

#: 品牌部分里出现这些**虚词/助动词**字符，说明它是从一句话里截出来的，不是公司名。
#: 例：`是基流科技`、`这将对初创公司`、`商汤这类公司`、`真正值得跟踪的是基流科技`。
_BRAND_FUNCTION_CHARS = frozenset(
    "的是这那该其此和与以及或并对为在把被将若等就都也还才则而且从向由于至到会能要可已未很太更最"
)

#: 常见虚词**词组**。单字表盖不住 `如果`（`如`/`果` 单独看都不是虚词），
#: 但 `如果趋境科技` 这种「左侧恰好是句号」的写法会让左侧边界检查误判为干净，
#: 于是它比真正的 `趋境科技` 更长、优先级更高，把正确候选挤掉。
_BRAND_FUNCTION_SEQUENCES = (
    "如果", "因为", "由于", "虽然", "尽管", "但是", "不过", "而且", "并且",
    "所以", "因此", "以及", "特别", "尤其", "对于", "关于", "随着", "按照",
    "根据", "包括", "其中", "那些", "这类", "等等", "一条", "一种", "一个",
    "正在", "已经", "可能", "是否", "能否",
)

#: 品牌部分含这些**行业通名**，是品类不是公司（`半导体公司`、`大模型公司`、`明星机器人公司`）。
_BRAND_GENERIC_WORDS = (
    "半导体", "机器人", "大模型", "人工智能", "智能", "新能源", "国产",
    "核心", "整机", "零部件", "上市", "初创", "明星", "行业", "产业",
    "赛道", "芯片", "光伏", "储能", "技术", "材料", "产品", "方案",
)

#: 中国公司简称的品牌部分基本落在 2–4 字（石头科技 / 基流科技 / 舜宇光学科技）。
_BRAND_LENGTHS = (4, 3, 2)
_BRAND_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]{2,4}")

MAX_TOKEN_LENGTH = 10


def normalize_for_match(text: object) -> str:
    """匹配用归一化：NFKC + 去空白 + 全角百分号统一 + 转小写。"""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"\s+", "", value)
    return value.replace("％", "%").casefold()


class _Anchor:
    """轻量的具名元组式锚点（避免为每个锚点分配 dataclass 的开销）。"""

    __slots__ = ("text", "normalized", "kind", "confidence", "source_span")

    def __init__(
        self, text: str, kind: str, confidence: float, source_span: tuple[int, int]
    ) -> None:
        self.text = text
        self.normalized = normalize_for_match(text)
        self.kind = kind
        self.confidence = confidence
        self.source_span = source_span

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= GATE_MIN_CONFIDENCE

    def matches(self, title: str) -> bool:
        """标题是否**完整**承接了这个锚点（不做任何截断匹配）。"""
        if not self.normalized:
            return False
        return self.normalized in normalize_for_match(title)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"ConcreteAnchor({self.text!r}, kind={self.kind!r}, "
            f"confidence={self.confidence})"
        )


#: 对外别名（用户规格里叫 ConcreteAnchor）。
ConcreteAnchor = _Anchor


def quantity_token(number: str, tail: str) -> tuple[str, float]:
    """把 `数字 + 后续文本` 归一成一个**完整**量化 token。

    返回 `(token, confidence)`；token 为空串表示这个数字不算量化信息。
    这是量化提取的**唯一实现**，`TitleAgent._is_quantified_number` 也复用它。
    """
    # 四位年份交给 year 通道，记成裸数字（标题写 `2027年` / `2027` 都能匹配）。
    if len(number) == 4 and number.startswith(("19", "20")):
        return "", 0.0

    scale = ""
    rest = tail
    if rest[:1] in QUANTITY_SCALES:
        scale = rest[:1]
        rest = rest[1:]

    unit = ""
    for candidate in QUANTITY_UNITS:
        if rest.startswith(candidate):
            unit = candidate
            break

    if not unit and not scale:
        return "", 0.0

    token = f"{number}{scale}{unit}"
    if not 2 <= len(token) <= MAX_TOKEN_LENGTH:
        return "", 0.0

    strong = bool(scale) or unit in _STRONG_UNITS or len(number) >= 3
    return token, (CONF_QUANTITY_STRONG if strong else CONF_QUANTITY_WEAK)


def _quantity_anchors(text: str) -> list[ConcreteAnchor]:
    anchors: list[ConcreteAnchor] = []
    for match in _NUMBER_RE.finditer(text):
        token, confidence = quantity_token(
            match.group(0), text[match.end() : match.end() + 4]
        )
        if not token:
            continue
        # token 一定是从数字开头、在正文里**连续**的一段（数量级/单位都紧跟在数字后面）。
        span = (match.start(), match.start() + len(token))
        anchors.append(ConcreteAnchor(token, KIND_QUANTITY, confidence, span))
    return anchors


def _year_anchors(text: str) -> list[ConcreteAnchor]:
    return [
        ConcreteAnchor(match.group(1), KIND_YEAR, CONF_YEAR, match.span(1))
        for match in _YEAR_RE.finditer(text)
    ]


def _is_cjk(char: str) -> bool:
    return bool(char) and "\u4e00" <= char <= "\u9fa9"


def _has_clean_left_boundary(text: str, position: int) -> bool:
    """品牌部分左侧是不是一个「干净的边界」（标点 / 空白 / 行首）。

    `36氪：趋境科技` → `趋境` 左边是 `：`，干净；
    `如果趋境科技`   → `趋境` 左边是 `果`，说明它可能是从 `如果…` 里截出来的。
    """
    if position <= 0:
        return True
    previous = text[position - 1]
    if _is_cjk(previous):
        return False
    return not (previous.isascii() and previous.isalnum())


def _brand_candidates(text: str, suffix_start: int, suffix_text: str) -> list[tuple[str, bool, int]]:
    """给定后缀的出现位置，向左取 2–4 字品牌部分，返回通过清洗的候选。

    返回 `(候选名, 左侧是否干净, 品牌起点)`。

    旧实现用 `[\\u4e00-\\u9fa5A-Za-z]{2,}(?:公司|科技|…)` 贪婪匹配，于是把
    `进军人形机器人这类公司`、`超过多数A股机器人上市公司`、`真正值得跟踪的是基流科技`
    整段当成公司名 —— 真是公司名的那部分（`基流科技`）反而被吞掉了，标题里写
    `基流科技` 也匹配不上。改成「先定位后缀，再向左取有限长度、逐个清洗」。
    """
    candidates: list[tuple[str, bool, int]] = []
    for size in _BRAND_LENGTHS:
        start = suffix_start - size
        if start < 0:
            continue
        brand = text[start:suffix_start]
        if not _BRAND_RE.fullmatch(brand):
            continue
        if any(char in _BRAND_FUNCTION_CHARS for char in brand):
            continue
        if any(sequence in brand for sequence in _BRAND_FUNCTION_SEQUENCES):
            continue
        if any(word in brand for word in _BRAND_GENERIC_WORDS):
            continue
        candidates.append(
            (brand + suffix_text, _has_clean_left_boundary(text, start), start)
        )
    return candidates


def _company_anchors(text: str) -> list[ConcreteAnchor]:
    names: list[str] = []
    # ① 词典通道：精度最高（`比亚迪` / `优必选` / `商汤` 这类无后缀简称）。
    for name in extract_known_company_names(text):
        if name and name not in names:
            names.append(name)

    # ② 后缀通道：捞词典里没有、但写法规范的（`石头科技` / `基流科技` / `舜宇光学科技`）。
    #
    # ⚠️ 同一次「如果趋境科技…」会产出 `果趋境`+`科技` 这种多吞一个字的候选；而同一实体
    # 在别处（`36氪：趋境科技`）又会出现干净写法。所以不能简单地「谁长留谁」——
    # 那会把**正确**的 `趋境科技` 删掉、留下垃圾的 `果趋境科技`，标题里写
    # `趋境科技` 反而匹配不上（实测一次 run 影响 12 条候选）。
    # 优先级 = (左侧是否干净, 候选长度)：clean 的优先，其次才是更长。
    suffix_candidates: list[tuple[str, bool, int]] = []
    for match in _COMPANY_SUFFIX_RE.finditer(text):
        for candidate, clean, start in _brand_candidates(
            text, match.start(), match.group(0)
        ):
            if candidate in names:
                continue
            if not is_valid_company_name(candidate):
                continue
            suffix_candidates.append((candidate, clean, start))

    confirmed = {candidate for candidate, clean, _ in suffix_candidates if clean}
    # 去重：同名保留「干净」的那次出现，便于后面算 source_span。
    unique: dict[str, tuple[bool, int]] = {}
    for candidate, clean, start in suffix_candidates:
        previous = unique.get(candidate)
        if previous is None or (clean and not previous[0]):
            unique[candidate] = (clean, start)

    ordered = sorted(
        unique.items(),
        key=lambda item: (1 if item[0] in confirmed else 0, len(item[0])),
        reverse=True,
    )
    selected: list[str] = []
    for candidate, _info in ordered:
        # 已按「优先级从高到低」排序，所以走到这里时**已选中的一定不比它差**：
        # 只要两者存在包含关系（`光学科技` ⊂ `舜宇光学科技`、
        # `趋境科技` ⊂ `果趋境科技`），就跳过这个优先级更低的。
        if any(candidate in other or other in candidate for other in selected):
            continue
        selected.append(candidate)
    names.extend(selected)

    anchors: list[ConcreteAnchor] = []
    for name in names:
        if len(name) > 14:      # 过长的「公司名」多半是把整句话收进来了
            continue
        position = text.find(name)
        if position < 0:
            continue
        anchors.append(
            ConcreteAnchor(name, KIND_COMPANY, CONF_COMPANY, (position, position + len(name)))
        )
    return anchors


def extract_concrete_anchors(text: object) -> list[ConcreteAnchor]:
    """从正文里抽出具象锚点（公司名 / 量化值 / 年份），按出现顺序返回。

    只保留**高精度**通道：公司名（词典或固定后缀 + 有效性校验）、量化值（完整 token）、
    四位年份。地名通道已整体移除（见模块 docstring 第 3 条）。
    """
    raw = str(text or "")
    if not raw.strip():
        return []

    anchors = [*_company_anchors(raw), *_quantity_anchors(raw), *_year_anchors(raw)]
    anchors.sort(key=lambda anchor: (anchor.source_span[0], -len(anchor.text)))

    deduped: list[ConcreteAnchor] = []
    seen: set[tuple[str, str]] = set()
    for anchor in anchors:
        key = (anchor.kind, anchor.normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(anchor)
    return deduped


def high_confidence_anchors(text: object) -> list[ConcreteAnchor]:
    return [a for a in extract_concrete_anchors(text) if a.is_high_confidence]


def concrete_hook_status(title: object, body: object) -> tuple[str, ConcreteAnchor | None]:
    """标题与正文具象锚点的三态关系（见模块 docstring）。"""
    gate_anchors = high_confidence_anchors(body)
    if len(gate_anchors) < GATE_MIN_ANCHORS:
        return STATUS_UNAVAILABLE, None
    for anchor in gate_anchors:
        if anchor.matches(str(title or "")):
            return STATUS_PASS, anchor
    return STATUS_FAIL, None


def concrete_anchor_summary(body: object) -> dict[str, object]:
    """给诊断/回放用的结构化摘要。"""
    anchors = extract_concrete_anchors(body)
    gate = [a for a in anchors if a.is_high_confidence]
    return {
        "anchor_count": len(anchors),
        "high_confidence_anchor_count": len(gate),
        "status_if_no_match": (
            STATUS_UNAVAILABLE if len(gate) < GATE_MIN_ANCHORS else STATUS_FAIL
        ),
        "anchors": [
            {
                "text": a.text,
                "kind": a.kind,
                "confidence": a.confidence,
                "source_span": list(a.source_span),
            }
            for a in anchors
        ],
    }
