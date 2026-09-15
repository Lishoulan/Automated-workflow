"""自动修复服务：在审核前把「可修复的确定性问题」直接改掉。

为什么需要它
------------
跑在 GitHub Actions 上，没有人工介入的环节。原来的流程是「发现问题 →
拦截 → 等人来处理」，结果就是每天跑到最后 0 篇可发（2026-09-15 实测：
23 篇全部被拦）。自动化的正确姿势是「发现问题 → 能自动修的就修 → 再审核」。

这里只处理**确定性、可机械修复**的问题：
- 绝对化表述（「完全」「绝对」「必然」这类硬断言）→ 换成留有余地的说法
- 内部流程标签 / 占位符残留 → 删除
- 提示词元叙述（段落开头的「下面只按现有材料展开」等）→ 剥离
- Python 列表序列化残留 → 还原成段落（正文清洗层已做，这里兜一层）

不做的事：不补写事实、不改动数字、不重构观点。那些属于创作，机械改写只会
引入新的错误。
"""
from __future__ import annotations

import re

# 绝对化表述 → 留有余地的替代说法。
# 只收录「断言式」用法，避免误伤「一定程度上」「一定的条件」这类正常表达。
ABSOLUTE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("完全没有", "基本没有"),
    ("完全不能", "很难"),
    ("完全不会", "基本不会"),
    ("完全可以", "基本可以"),
    ("完全改变", "明显改变"),
    ("完全", "基本"),
    ("绝对是", "通常是"),
    ("绝对能", "很有可能"),
    ("绝对不会", "通常不会"),
    ("绝对", "通常"),
    ("必然会", "很可能会"),
    ("必然", "大概率"),
    ("肯定是", "很可能是"),
    ("一定能", "很有可能"),
    ("一定会", "很可能会"),
    ("一定是", "很可能是"),
    ("一定可以", "很有可能"),
    ("无疑会", "很可能会"),
    ("毫无疑问", "大体上"),
    ("无疑", "大体上"),
    ("保证收益", "有望获得回报"),
    ("必然上涨", "存在上涨可能"),
    ("必然成功", "有成功机会"),
    ("首创", "较早推出"),
    ("独家", "较早"),
)

# 内部流程标签 / 占位符，出现在正文里就是事故（读者会看到）。
INTERNAL_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:manual_paste|evergreen_pool|benchmark_hotspot|source_document|"
               r"source_material|editorial_council|quality_review)[：:]?", re.IGNORECASE),
    re.compile(r"(?:TODO|FIXME|待补充|待填写|此处省略|\{\{.*?\}\})", re.IGNORECASE),
    re.compile(r"(?:已跳过缓存|强制重新生成|质量审核专家团|发布就绪|DataReport)"),
)

# Python 序列化残留（素材列表被 str() 拼进正文）
_SEQUENCE_ARTIFACT_RE = re.compile(r"['\"]\s*,\s*['\"]")

# 序列化**残留**的检测模式。注意 `repair_sequence_artifacts` 会把 `', '` 换成
# 段落分隔，这会顺手抹掉原始分隔符特征——一篇 list/dict dump 修复之后看起来
# 就「干净」了，实测 2026-09-15 的 15:38 run 就是这样：修完仍是
# `[{'judgment': '...` 的垃圾，却不再命中 `', '`。所以检测必须覆盖「引号键名
# + 值」与「括号字面量」，否则兜底会把序列化垃圾当成正常稿子发出去。
SEQUENCE_RESIDUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"['\"]\s*,\s*['\"]"),
    re.compile(r"['\"]\w+['\"]\s*:\s*['\"]"),
    re.compile(r"\[\s*\{|\}\s*\]|\{\s*['\"]"),
)

# 提示词元叙述：模型把「我打算怎么写」的说明写进了正文。读者看到的是写作
# 指令而不是新闻，观感极差，也容易被判低质/AI 生成。
#
# 只在**段落开头**按完整片段剥离，因此不会把正常句子改坏；片段之间用逗号
# 或句号相连时也一并吃掉分隔符。按长度倒序匹配，避免短片段先命中留下残渣。
PROMPT_NARRATION_FRAGMENTS: tuple[str, ...] = (
    "下面只按现有材料展开",
    "下面只按现有材料",
    "只按现有材料展开",
    "材料里的推测会明确保留为推测",
    "不把它改写成已经确认的事实",
    "明确保留为推测",
    "下面按现有材料",
)
_NARRATION_SEPARATORS = "，,、；;：:。！？!? \t"


def repair_prompt_narration(text: str) -> tuple[str, list[str]]:
    """剥离段落开头的提示词元叙述片段。"""
    result = str(text or "")
    if not result:
        return result, []
    fragments = sorted(PROMPT_NARRATION_FRAGMENTS, key=len, reverse=True)
    removed = 0
    paragraphs: list[str] = []
    for raw in result.split("\n\n"):
        paragraph = raw.strip()
        if not paragraph:
            continue
        stripped = True
        while stripped:
            stripped = False
            for fragment in fragments:
                if paragraph.startswith(fragment):
                    paragraph = paragraph[len(fragment) :].lstrip(_NARRATION_SEPARATORS)
                    removed += 1
                    stripped = True
                    break
        if paragraph:
            paragraphs.append(paragraph)
    if not removed or not paragraphs:
        return result, []
    return "\n\n".join(paragraphs), [f"剥离提示词元叙述 {removed} 处"]


def repair_absolute_claims(text: str) -> tuple[str, list[str]]:
    """把绝对化断言改成留有余地的表达，返回 (新文本, 修复记录)。"""
    fixed: list[str] = []
    result = str(text or "")
    for original, replacement in ABSOLUTE_REPLACEMENTS:
        if original not in result:
            continue
        count = result.count(original)
        result = result.replace(original, replacement)
        fixed.append(f"{original}→{replacement}（{count}处）")
    return result, fixed


def repair_internal_leaks(text: str) -> tuple[str, list[str]]:
    """删除正文里的内部标签与占位符。"""
    fixed: list[str] = []
    result = str(text or "")
    for pattern in INTERNAL_LEAK_PATTERNS:
        matches = pattern.findall(result)
        if not matches:
            continue
        result = pattern.sub("", result)
        fixed.append(f"清除内部标签 {len(matches)} 处")
    return result, fixed


def repair_sequence_artifacts(text: str) -> tuple[str, list[str]]:
    """把序列化残留还原成段落分隔。"""
    result = str(text or "")
    count = len(_SEQUENCE_ARTIFACT_RE.findall(result))
    if not count:
        return result, []
    result = _SEQUENCE_ARTIFACT_RE.sub("\n\n", result)
    return result, [f"还原段落分隔 {count} 处"]


def _tidy(text: str) -> str:
    """修复后清理副作用：多余空格、重复标点、空行压缩。"""
    result = text
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"[，,]{2,}", "，", result)
    result = re.sub(r"。{2,}", "。", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return result.strip()


def repair_article(article: str) -> dict[str, object]:
    """对正文跑一轮可机械执行的修复。

    返回 ``{"article": 修复后正文, "changes": [...], "changed": bool}``。
    """
    text = str(article or "")
    changes: list[str] = []
    for handler in (
        repair_internal_leaks,
        repair_prompt_narration,
        repair_absolute_claims,
        repair_sequence_artifacts,
    ):
        text, fixed = handler(text)
        changes.extend(fixed)
    text = _tidy(text)
    return {
        "article": text,
        "changes": changes,
        "changed": bool(changes),
    }
