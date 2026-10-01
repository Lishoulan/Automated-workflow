from __future__ import annotations

import re


# Public institutions are valid article subjects, but they are not companies.
# Keep this list explicit so ordinary phrases ending in “局/部/院” do not become
# high-confidence entities through a permissive suffix expression.
KNOWN_ORGANIZATION_NAMES = frozenset(
    {
        "国务院",
        "国家能源局",
        "国家发改委",
        "国家发展改革委",
        "工业和信息化部",
        "工信部",
        "市场监管总局",
        "国家统计局",
        "中国人民银行",
        "证监会",
        "商务部",
        "财政部",
        "住建部",
        "交通运输部",
        "教育部",
        "国网能源研究院",
    }
)


def extract_known_organization_names(text: object) -> list[str]:
    """Return explicitly named public institutions in source order."""

    raw_text = re.sub(r"https?://\S+", " ", str(text or ""), flags=re.IGNORECASE)
    folded = raw_text.casefold()
    matches: list[tuple[int, int, str]] = []
    for name in KNOWN_ORGANIZATION_NAMES:
        position = folded.find(name.casefold())
        if position >= 0:
            matches.append((position, -len(name), name))

    result: list[str] = []
    for _position, _length, name in sorted(matches):
        if any(name != previous and name.casefold() in previous.casefold() for previous in result):
            continue
        result.append(name)
    return result


def is_known_organization_name(value: object) -> bool:
    return str(value or "").strip() in KNOWN_ORGANIZATION_NAMES
