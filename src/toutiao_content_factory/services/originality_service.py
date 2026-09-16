"""原创性服务：量化生成稿相对素材原文的「改写距离」。

为什么需要它
------------
头条原创库做的是**全网比对**：素材原文往往已被其他账号先发（行业媒体普遍
多平台分发，微信公众号尤其常见）。本地原有的查重只比对自己历史发布，查不出
「和素材有多像」，于是一篇与素材连续重合 51.5% 的稿子照发不误，被判
「不原创」扣 5 分（首发激励计划），直接影响收益。

判定指标
--------
用「连续抄录率」而非整体相似度：偶尔共用的专有名词、数字不算问题，真正会
被判搬运的是成段连续的相同文字。实现上对正文与原文取 8 字滑窗，把命中的
窗口拼回最长连续片段，再统计这些片段占正文的比例。
"""
from __future__ import annotations

import re

# 低于此长度的素材原文不足以作为比对基准，结果会系统性偏低。
SOURCE_MATERIAL_MIN_CHARS = 200
# ≥25% 需要人工复核；≥35% 判定为高风险，自动流程应拦截。
REVIEW_THRESHOLD = 25.0
BLOCK_THRESHOLD = 35.0

_WINDOW = 8
_CLEAN_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]")


def clean_for_compare(text: str) -> str:
    return _CLEAN_RE.sub("", str(text or ""))


def _windows(text: str, size: int = _WINDOW) -> set[str]:
    t = clean_for_compare(text)
    return {t[i: i + size] for i in range(len(t) - size + 1)}


def source_copy_ratio(
    article: str, source_text: str, *, strip_file_header: bool = True
) -> dict[str, object]:
    """计算生成稿相对素材原文的连续抄录情况。

    返回的 ``ratio`` 为 None 表示无法判断（原文缺失或过短），调用方必须显式
    处理，不能当成 0（安全）看待。

    ``strip_file_header`` 默认 True，对应 ``source_material_full.txt`` 的
    「三行文件头 + 空行 + 正文」格式。传入的是内存里的素材正文（例如
    ``ExtractionResult.merged_text``）时必须显式置 False，否则首个空行之前
    的正文会被误当成文件头丢掉，导致比对基准残缺、抄录率系统性偏低。
    """
    body = str(source_text or "")
    # 去掉 `source_material_full.txt` 的三行文件头
    if strip_file_header and "\n\n" in body:
        body = body.split("\n\n", 1)[-1]
    if len(body.strip()) < SOURCE_MATERIAL_MIN_CHARS:
        return {
            "ratio": None,
            "copied_chars": 0,
            "article_chars": len(clean_for_compare(article)),
            "source_chars": len(body.strip()),
            "reason": f"素材原文仅 {len(body.strip())} 字，基准不足，查重结果不可信",
            "samples": [],
        }

    gen = _windows(article)
    src = _windows(body)
    if not gen:
        return {
            "ratio": None,
            "copied_chars": 0,
            "article_chars": 0,
            "source_chars": len(body.strip()),
            "reason": "正文为空",
            "samples": [],
        }

    joined = clean_for_compare(body)
    spans: list[str] = []
    for gram in gen & src:
        idx = joined.find(gram)
        if idx < 0:
            continue
        end = idx + len(gram)
        while end < len(joined) and joined[end - _WINDOW: end] in (gen & src):
            end += 1
        span = joined[idx: end]
        if len(span) >= 16:
            spans.append(span)

    # 去掉互相包含的短片段，只保留最长代表
    spans.sort(key=len, reverse=True)
    unique: list[str] = []
    for span in spans:
        if not any(span in kept for kept in unique):
            unique.append(span)

    copied = sum(len(x) for x in unique)
    article_chars = len(clean_for_compare(article))
    return {
        "ratio": round(100 * copied / max(article_chars, 1), 1),
        "copied_chars": copied,
        "article_chars": article_chars,
        "source_chars": len(body.strip()),
        "reason": "",
        "samples": unique[:5],
    }


def originality_flags(ratio: float | None, reason: str = "") -> list[str]:
    """生成供审核报告使用的人类可读结论。"""
    if ratio is None:
        return [f"原创性无法判断：{reason or '缺少素材原文'}，需人工核对"] if reason else []
    flags = []
    if ratio >= BLOCK_THRESHOLD:
        flags.append(
            f"与素材原文连续抄录 {ratio}%（≥{BLOCK_THRESHOLD}%），"
            "大概率被头条原创库判为不原创，发布后会扣分。"
        )
    elif ratio >= REVIEW_THRESHOLD:
        flags.append(
            f"与素材原文连续抄录 {ratio}%（≥{REVIEW_THRESHOLD}%），"
            "存在不原创风险，建议重写重合段落后再发。"
        )
    return flags
