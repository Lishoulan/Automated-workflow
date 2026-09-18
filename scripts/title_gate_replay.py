"""离线回放：把真实标题喂进现有标题规则闸门，检验「规则是否在杀好标题」。

零成本、不联网、不启浏览器。用途：判断「换成免费模型后标题能不能过闸」，
以及「现有 RULE_006/007/009 是否与真实 CTR 数据相矛盾」。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker  # noqa: E402

# 真实数据分组（来自 output/accounts/robot-notes/growth_ops/performance/performance_report.md
# 与 publish_history.json + 云端 publish_history.json）
GROUPS: dict[str, list[tuple[str, str]]] = {
    # (标题, 正文片段) —— 正文用于「标题主体是否在正文出现」这道检查
    "A_高CTR真实爆款": [
        ("关节电机交付量破5万，机器人产业链利润开始分层", "关节电机交付量突破5万台，机器人产业链的利润分配开始出现分层"),
        ("机器人热度之外，谁在拿走真正订单", "机器人赛道的热度之外，真正拿到订单的公司是谁"),
        ("订单验证之后，人形机器人利润开始分化", "订单验证之后，人形机器人的利润开始出现分化"),
        ("人形机器人进工厂后，3个指标决定谁能先拿到订单", "人形机器人进入工厂之后，有三个指标决定谁能先拿到订单"),
    ],
    "B_零阅读实测形态": [
        ("基地10亿：机器人如何实测", "基地投入10亿，机器人产业如何实测"),
        ("月之暗面等大模型1240亿：大模型如何实测", "月之暗面等大模型投入1240亿，大模型如何实测"),
        ("工信部推动机器人80%：具身智能如何实测", "工信部推动机器人80%，具身智能如何实测"),
        ("埃斯顿出货量创新高，机器人市场进入新阶段", "埃斯顿出货量创下新高，机器人市场进入新阶段"),
    ],
    "C_零阅读_代码点名的坏形态": [
        ("涉及哪些公司", "机器人产业链涉及哪些公司"),
        ("技术路线之争决定量产节奏", "技术路线之争决定量产节奏"),
        ("营收57亿利润负122%", "营收57亿，利润负122%"),
    ],
    "D_免费模型典型输出_假设候选": [
        ("机器人量产卡在哪一步，三个环节决定成败", "机器人量产卡在哪一步，三个环节决定成败"),
        ("谁在给机器人公司送钱", "谁在给机器人公司送钱，订单背后的资金流向"),
        ("这轮机器人行情，和上一轮有什么不一样", "这轮机器人行情和上一轮有什么不一样"),
    ],
}


def _fmt(issue) -> str:
    """issue 可能是 dict 也可能是 str，两种都要能显示。"""
    if isinstance(issue, dict):
        return str(
            issue.get("code") or issue.get("rule") or issue.get("message") or issue
        )[:52]
    return str(issue)[:52]


def main() -> int:
    checker = ArticleQualityChecker()
    total = 0
    passed = 0
    print(f"{'组别':<26} {'结果':<6} {'标题':<34} 拒绝原因")
    print("-" * 118)
    for group, items in GROUPS.items():
        for title, body in items:
            total += 1
            try:
                result = checker._check_title(title, body)
                issues = result.get("issues", []) or []
            except Exception as exc:  # pragma: no cover
                issues = [f"ERROR:{type(exc).__name__}:{exc}"]
            ok = not issues
            passed += int(ok)
            codes = "; ".join(_fmt(i) for i in issues)
            print(f"{group:<26} {'PASS' if ok else 'REJECT':<6} {title:<34} {codes}")
    print("-" * 118)
    print(f"合计 {passed}/{total} 通过（{passed / total * 100:.0f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
