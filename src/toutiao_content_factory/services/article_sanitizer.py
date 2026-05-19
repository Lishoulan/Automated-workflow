from __future__ import annotations

import re
from typing import Iterable


META_NOISE_PREFIXES = (
    "账号定位：",
    "账号：",
    "账号名：",
    "以下是今日自动选中的热点选题",
    "写作要求：",
    "固定写法：",
    "推荐主结论：",
    "跟踪变量：",
    "入选理由：",
    "请围绕这条产业线索",
    "请重点分析",
    "主赛道：",
    "主关键词：",
    "匹配赛道：",
    "匹配关键词：",
    "可确认数字：",
    "标题：",
    "来源：",
    "发布日期：",
    "原文链接：",
    "补充片段：",
    "摘要：",
)

SECTION_LABELS = {
    "摘要",
    "正文",
    "正文成稿",
    "标题",
    "标签",
    "推荐话题",
    "评论区互动",
    "正文校验备注",
}

SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
COMMA_SPLIT_RE = re.compile(r"(?<=[，、；：:])")
INLINE_HEADING_RE = re.compile(r"(?<!\n)\s+(?=#{1,6}\s*)")


def clean_article_text(article: str, title_candidates: Iterable[str] | None = None) -> str:
    text = str(article or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    text = _normalize_markdown_structure(text)
    text = _strip_markdown_blocks(text)
    blocks = [_clean_block(block) for block in re.split(r"\n\s*\n+", text) if block.strip()]
    blocks = [block for block in blocks if block]
    blocks = _strip_leading_title(blocks, title_candidates or [])

    result: list[str] = []
    for block in blocks:
        if _looks_like_noise(block):
            continue
        result.extend(_split_for_mobile(block))

    result = _merge_tiny_blocks(result)
    result = _rebalance_blocks(result)
    return "\n\n".join(block for block in result if block).strip()


def _normalize_markdown_structure(text: str) -> str:
    normalized = str(text or "")
    normalized = INLINE_HEADING_RE.sub(lambda match: _heading_break_replacement(normalized, match.start()), normalized)
    normalized = re.sub(r"(?m)^\s*#{1,6}\s*", "", normalized)
    normalized = re.sub(r"(?m)^\s*[-*]\s+", "", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _heading_break_replacement(text: str, start_index: int) -> str:
    previous = text[start_index - 1] if start_index > 0 else ""
    if previous and previous not in "。！？!?；;：:\n":
        return "。\n\n"
    return "\n\n"


def _strip_markdown_blocks(text: str) -> str:
    cleaned = re.sub(r"```.*?```", "", text, flags=re.S)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[(.*?)\]\((.*?)\)", "", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = cleaned.replace("~~", "")
    return cleaned


def _clean_block(block: str) -> str:
    lines: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^\d+\.\s*", "", line)
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line in {"--", "---"}:
            continue
        lines.append(line)
    return _repair_cjk_sentence_gaps(" ".join(lines).strip())


def _strip_leading_title(blocks: list[str], title_candidates: Iterable[str]) -> list[str]:
    if not blocks:
        return blocks

    first = blocks[0]
    first = _strip_known_title_candidate(first, title_candidates)
    first = _strip_title_like_first_sentence(first)

    if " " in first:
        prefix, rest = first.split(" ", 1)
        if 8 <= len(prefix) <= 52 and _looks_like_title(prefix) and len(rest.strip()) >= 24:
            first = rest.strip()

    blocks[0] = first.strip()
    return [block for block in blocks if block]


def _strip_known_title_candidate(text: str, title_candidates: Iterable[str]) -> str:
    current = text.strip()
    normalized_current = _normalize_compare_text(current)
    for candidate in title_candidates:
        normalized_candidate = _normalize_compare_text(candidate)
        if not normalized_candidate:
            continue
        if normalized_current.startswith(normalized_candidate):
            remainder = current[len(candidate.strip()) :].lstrip(" ：:-")
            if remainder:
                return remainder
    return current


def _strip_title_like_first_sentence(text: str) -> str:
    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if len(sentences) < 2:
        if " " in text:
            first_chunk, rest = text.split(" ", 1)
            if 10 <= len(first_chunk) <= 52 and _looks_like_title(first_chunk) and len(rest.strip()) >= 24:
                return rest.strip()
        return text
    first_sentence = sentences[0]
    if (
        len(first_sentence) <= 52
        and any(token in first_sentence for token in ("：", ":", "？", "?", "！", "!", "，", ","))
        and len(sentences[1]) >= 18
    ):
        return "".join(sentences[1:]).strip()
    return text


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lstrip("#").strip())


def _looks_like_noise(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in SECTION_LABELS:
        return True
    if re.fullmatch(r"(?:#\S+\s*){2,}", stripped):
        return True
    if any(stripped.startswith(prefix) for prefix in META_NOISE_PREFIXES):
        return True
    lowered = stripped.lower()
    return any(
        marker in lowered
        for marker in (
            "自动生成输入",
            "请围绕这条产业线索",
            "请重点分析",
            "匹配赛道",
            "匹配关键词",
            "可确认数字",
        )
    )


def _looks_like_title(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 56:
        return False
    punctuation_count = sum(stripped.count(char) for char in "。！？!?；;")
    return punctuation_count <= 1


def _split_for_mobile(block: str) -> list[str]:
    cleaned = block.strip()
    if not cleaned:
        return []

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    if len(sentences) <= 1 and len(cleaned) > 140:
        sentences = _split_long_sentence(cleaned)

    if not sentences:
        return []

    paragraphs: list[str] = []
    current = ""
    current_sentence_count = 0

    for sentence in sentences:
        candidate = f"{current}{sentence}" if current else sentence
        if current and (len(candidate) > 150 or current_sentence_count >= 3):
            paragraphs.append(current.strip())
            current = sentence
            current_sentence_count = 1
        else:
            current = candidate
            current_sentence_count += 1

    if current.strip():
        paragraphs.append(current.strip())
    return paragraphs


def _split_long_sentence(text: str) -> list[str]:
    parts = [part.strip() for part in COMMA_SPLIT_RE.split(text) if part.strip()]
    if len(parts) <= 1:
        return [text]

    result: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{part}" if current else part
        if current and len(candidate) > 70:
            result.append(current.strip())
            current = part
        else:
            current = candidate
    if current.strip():
        result.append(current.strip())
    return result


def _merge_tiny_blocks(blocks: list[str]) -> list[str]:
    merged: list[str] = []
    for block in blocks:
        if not block:
            continue
        if merged and len(block) < 30 and len(merged[-1]) + len(block) <= 150 and not _looks_like_title(block):
            merged[-1] = f"{merged[-1]} {block}".strip()
        else:
            merged.append(block)
    return merged


def _rebalance_blocks(blocks: list[str]) -> list[str]:
    balanced = [block.strip() for block in blocks if block.strip()]
    if len(balanced) <= 8:
        return balanced

    while len(balanced) > 8:
        best_index: int | None = None
        best_score: int | None = None
        for index in range(1, len(balanced)):
            left = balanced[index - 1]
            right = balanced[index]
            if index == len(balanced) - 1 and right.endswith(("？", "?")):
                continue
            combined = f"{left} {right}".strip()
            if len(combined) > 185:
                continue
            score = len(left) + len(right)
            if best_score is None or score < best_score:
                best_index = index
                best_score = score

        if best_index is None:
            break

        balanced[best_index - 1] = f"{balanced[best_index - 1]} {balanced[best_index]}".strip()
        del balanced[best_index]

    return balanced


def _repair_cjk_sentence_gaps(text: str) -> str:
    if " " not in text:
        return text

    parts = [part.strip() for part in text.split(" ") if part.strip()]
    if not parts:
        return text

    rebuilt = parts[0]
    previous_part = parts[0]
    for part in parts[1:]:
        if _should_insert_period(previous_part, part, rebuilt):
            rebuilt = f"{rebuilt}。{part}"
        elif _should_drop_space(previous_part, part):
            rebuilt = f"{rebuilt}{part}"
        else:
            rebuilt = f"{rebuilt} {part}"
        previous_part = part
    return rebuilt


def _should_insert_period(previous_part: str, current_part: str, rebuilt_text: str) -> bool:
    if not previous_part or not current_part or not rebuilt_text:
        return False
    if rebuilt_text[-1] in "。！？!?；;：:":
        return False
    if not _starts_with_cjk(previous_part) or not _starts_with_cjk(current_part):
        return False
    return len(previous_part) >= 12 and len(current_part) >= 6


def _should_drop_space(previous_part: str, current_part: str) -> bool:
    return bool(previous_part and current_part and _ends_with_cjk(previous_part) and _starts_with_cjk(current_part))


def _starts_with_cjk(text: str) -> bool:
    return bool(text and "\u4e00" <= text[0] <= "\u9fff")


def _ends_with_cjk(text: str) -> bool:
    return bool(text and "\u4e00" <= text[-1] <= "\u9fff")
