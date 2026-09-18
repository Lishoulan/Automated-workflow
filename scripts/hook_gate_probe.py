"""第二道闸门实测：具象锚点 hook 闸门 + 标题-正文一致性。

第一测（free_model_title_probe.py）显示 glm-4-flash 生成的标题能过
ArticleQualityChecker（RULE_006/007/009），但云端实际仍退回模板池，
说明杀标题的是 TitleAgent 自己的那道闸门。这里把它单独拉出来测。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services.concrete_anchors import (  # noqa: E402
    concrete_hook_status,
    extract_concrete_anchors,
)

ENV: dict[str, str] = {}
for _line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        ENV[_k.strip()] = _v.strip()

BODY = (
    "埃斯顿、优必选、宇树等公司陆续披露订单与产能进展，人形机器人从展示阶段进入交付阶段。"
    "关节电机交付量突破5万台，产业链利润开始分层；工信部推动机器人密度目标，但真实产能"
    "与交付能力仍是瓶颈。优必选柳州工厂规划年产万台，而行业年出货量预判仅2万台。"
)

# 第一测里「过 ArticleQualityChecker」的标题，看它们能否过具象锚点闸门
TITLES_PASSED_RULE_GATE = [
    "关节电机突破5万台，人形机器人交付热潮来临？",
    "工信部定80%目标，机器人产业链利润分层，交付能力瓶颈在哪？",
    "从展示到交付，人形机器人产业链盈利几何？",
    "行业年出货量2万台，人形机器人产能瓶颈如何突破？",
    "人形机器人订单井喷，产业链如何应对产能压力？",
    "机器人密度80%，埃斯顿、优必选交付量激增，趋势如何？",
    "5万台关节电机交付，机器人产业链迎来盈利高峰？",
    "从展示到交付，人形机器人产业链面临哪些挑战？",
    "工信部目标引领，机器人行业加速，产能交付能力跟得上吗？",
    "机器人产业链利润分层，谁将受益最大？",
    "优必选等公司产能规划，人形机器人市场前景可期？",
    "人形机器人产业链加速，交付能力提升几何？",
    "5万台关节电机交付，机器人行业盈利高峰来临？",
    "从展示到交付，人形机器人产业链盈利几何？",
]

# 真实发布过的标题（含模板垃圾与真实爆款）
PUBLISHED = [
    "工信部推动机器人80%：具身智能如何实测",
    "埃斯顿出货量创新高，机器人市场进入新阶段",
    "年全球出货预判仅2万台，优必选万台工厂赌对了吗",
    "关节电机交付量破5万，机器人产业链利润开始分层",
    "机器人热度之外，谁在拿走真正订单",
    "拿下小鹏14根丝杠订单，五洲新春靠4折价格抢占市场",
    "优必选柳州工厂年产万台：真正考验是第1万台卖给谁",
]


def anchors(title: str) -> list[str]:
    try:
        result = extract_concrete_anchors(title)
    except Exception as exc:
        return [f"ERROR:{type(exc).__name__}:{exc}"]
    if isinstance(result, dict):
        return [f"<dict keys={list(result)[:6]}>"]
    return [str(v) for v in (result or [])]


def show(label: str, titles: list[str]) -> None:
    print(f"\n{'=' * 82}\n{label}\n{'=' * 82}")
    fail = 0
    for t in titles:
        try:
            status, anchor = concrete_hook_status(t, BODY)
        except Exception as exc:
            status, anchor = f"ERROR:{type(exc).__name__}", None
        if str(status).lower() == "fail":
            fail += 1
        mark = {"pass": "✅", "fail": "❌", "unavailable": "⚠️"}.get(str(status).lower(), "?")
        print(f"  {mark} status={status:<12} anchor={anchor!s:<26} {t}")
    print(f"  → FAIL {fail}/{len(titles)}")


def _body_mentions(title: str) -> bool:
    """标题主体是否在正文出现（粗判：取标题里最长的连续中文片段）。"""
    import re

    for frag in sorted(re.findall(r"[\u4e00-\u9fff]{2,}", title), key=len, reverse=True)[:3]:
        if frag in BODY:
            return True
    return False


if __name__ == "__main__":
    show("A. 过了规则闸门的免费模型标题 → 具象锚点闸门", TITLES_PASSED_RULE_GATE)
    show("B. 真实发布过的标题 → 具象锚点闸门", PUBLISHED)
