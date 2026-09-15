"""标题长度与语义完整性处理。

平台对标题长度有硬限制（头条 30 字、微信 64 字）。单纯按字符数截断会把
标题切在词中间，产生 "……真正值得关" 这类语义破碎的结果，直接压低点击率。

本模块的截断策略是 **按语义边界回溯**：优先在标点处切断并丢弃不完整的尾句，
只有在找不到任何可用边界时才退回硬切。
"""

from __future__ import annotations

import re

__all__ = ["fit_title_to_limit", "strip_trailing_filler", "normalize_title_punctuation"]

# 分句边界：中文与半角标点都要覆盖，实际标题里两者混用。
SEGMENT_SPLIT_PATTERN = re.compile(r"[：:，,；;！？!?。]")

# 可安全丢弃的尾部套话。这些本身也属于平台判定的"低质套话"，
# 与其截断成半句，不如整段删掉。
TRAILING_FILLER_PATTERNS = (
    re.compile(r"[，,]\s*真正值得关注.*$"),
    re.compile(r"[，,]\s*值得关注.*$"),
    re.compile(r"[，,]\s*值得期待.*$"),
    re.compile(r"[，,]\s*令人期待.*$"),
    re.compile(r"[，,]\s*值得深思.*$"),
    re.compile(r"[，,]\s*你怎么看.*$"),
)

# 成对符号，用于清除因上游清洗导致的残缺引号（例如 "进入“ 算账"）。
PAIRED_MARKS = (("“", "”"), ("《", "》"), ("【", "】"), ("（", "）"), ("(", ")"), ('"', '"'))

# 中文标点，其前后不应残留空格。
CJK_PUNCTUATION = "，。、；：！？（）《》“”‘’【】—…"

_TRAILING_TRIM_CHARS = "，,、:：;；-—~· \t\r\n"


def strip_trailing_filler(title: str) -> str:
    """移除标题尾部的套话后缀，例如 "，真正值得关注"。

    反复匹配以处理多重叠加的套话（"……，值得关注，你怎么看"）。
    """
    result = title
    changed = True
    while changed:
        changed = False
        for pattern in TRAILING_FILLER_PATTERNS:
            updated = pattern.sub("", result)
            if updated != result:
                result = updated.rstrip(_TRAILING_TRIM_CHARS)
                changed = True
    return result


def normalize_title_punctuation(title: str) -> str:
    """清理标题中的残缺成对符号与标点周围的多余空白。"""
    result = title

    for opener, closer in PAIRED_MARKS:
        if opener == closer:
            continue
        if result.count(opener) != result.count(closer):
            result = result.replace(opener, "").replace(closer, "")

    for mark in CJK_PUNCTUATION:
        result = re.sub(rf"\s*{re.escape(mark)}\s*", mark, result)

    return re.sub(r"\s{2,}", " ", result).strip()


def _longest_boundary_prefix(title: str, limit: int) -> str:
    """在所有标点边界里，取长度不超过 limit 的最长前缀。"""
    best = ""
    for match in SEGMENT_SPLIT_PATTERN.finditer(title):
        candidate = title[: match.end()].rstrip(_TRAILING_TRIM_CHARS)
        if len(candidate) <= limit and len(candidate) > len(best):
            best = candidate
    return best


def fit_title_to_limit(title: str, limit: int, min_length: int = 12) -> str:
    """把标题收敛到 limit 字以内，尽量不破坏语义。

    处理顺序：
    1. 清除尾部套话与残缺标点；
    2. 长度已达标则直接返回；
    3. 按标点边界取最长可用前缀（>= min_length 才采用）；
    4. 以上都不成立时才硬切，并去掉切口的悬空标点。
    """
    if limit <= 0:
        return title

    cleaned = normalize_title_punctuation(strip_trailing_filler(title)) or title
    if len(cleaned) <= limit:
        return cleaned

    best = _longest_boundary_prefix(cleaned, limit)
    if len(best) >= min_length:
        return best

    # 边界过早（标题几乎无标点），退回按字符截断，
    # 但至少保证不以悬空标点结尾。
    return cleaned[:limit].rstrip(_TRAILING_TRIM_CHARS)
