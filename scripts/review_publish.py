#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""半自动发布助手：看稿 → 改标题 → 批准 → 发布。

配合「方案 A」使用：系统负责生成 1800 字分析稿（内容质量已达标），
人只做最后一道判断。本工具把这道判断变成一条命令。

用法::

    python scripts/review_publish.py list              # 列出待审稿件
    python scripts/review_publish.py show 1            # 看第 1 篇的标题与正文开头
    python scripts/review_publish.py title 1 "新标题"   # 改标题
    python scripts/review_publish.py approve 1         # 批准第 1 篇
    python scripts/review_publish.py publish           # 发布最新已批准的一篇

说明：
- 序号来自 `list` 的输出，按生成时间倒序。
- `approve` 等价于把该 run 的 review_status.json 的 status 置为 approved，
  这也是 `main.py --publish-latest-approved` 的判定依据。
- 所有写操作都会先备份原文件为 *.bak。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "output" / "accounts" / "robot-notes" / "runs"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fallback_title(run_dir: Path, status_payload: dict) -> str:
    """标题质量门可能清空 selected_title，这里依次回退到选题名与候选标题。"""
    title = str(status_payload.get("selected_title") or "").strip()
    if title:
        return title
    topic = _read_json(run_dir / "selected_topic.json")
    title = str(topic.get("title") or "").strip()
    if title:
        return title
    titles_file = run_dir / "titles.txt"
    if titles_file.exists():
        for line in titles_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.endswith("：") and line not in ("全部标题", "推荐标题"):
                return line.lstrip("0123456789.、 ")
    article = run_dir / "article.md"
    if article.exists():
        first = article.read_text(encoding="utf-8").strip().splitlines()
        if first:
            return first[0][:40]
    return ""


def _collect_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        print(f"未找到 runs 目录: {RUNS_DIR}")
        return []
    rows = []
    for item in RUNS_DIR.iterdir():
        if not item.is_dir():
            continue
        status_payload = _read_json(item / "review_status.json")
        score_payload = _read_json(item / "content_score.json")
        scores = score_payload.get("scores", {}) or {}
        rows.append(
            {
                "dir": item,
                "name": item.name,
                # 稳定标识：目录名形如 20260915-221632_标题，取时间戳段。
                # 不用列表序号——每次扫描若有新 run 生成，序号会整体漂移，
                # 曾导致 approve 打到了错误的稿子上。
                "short_id": item.name.split("_", 1)[0].replace("-", ""),
                "mtime": item.stat().st_mtime,
                "status": str(status_payload.get("status", "?")),
                "title": _fallback_title(item, status_payload),
                "overall": scores.get("overall_score"),
                "ai_risk": scores.get("ai_generated_risk"),
                "readiness": scores.get("publish_readiness_score"),
                "recommendation": str(score_payload.get("publish_recommendation") or ""),
                "block_reason": _block_reason(item),
                "defects": _scan_article_issues(item),
                "copy": _copy_ratio(item),
            }
        )
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


# 正文硬伤：直接暴露给读者就会出事故的串味内容。
# 逐条说明见 `_scan_article_issues`，这里只放模式串，方便增补。
ARTICLE_DEFECT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Python列表残留", "', '"),
    ("Python列表残留", '", "'),
    ("Python列表残留", "['"),
    ("提示词元叙述", "下面只按现有材料"),
    ("提示词元叙述", "明确保留为推测"),
    ("提示词元叙述", "不把它改写成"),
    ("提示词元叙述", "只按现有材料展开"),
    ("提示词元叙述", "材料里的推测"),
    ("提示词元叙述", "先把主体和时间线放回原位"),
    ("内部标签", "RULE_"),
    ("内部标签", "style_key"),
    ("内部标签", "tech_media"),
    ("占位符", "TODO"),
    ("占位符", "待补充"),
)


def _scan_article_issues(run_dir: Path) -> list[str]:
    """本地扫描正文硬伤，不调用模型，秒出结果。

    目的：把「人工看一眼」从通读 2000 字压缩成看一行标记。
    被这里标记出来的稿子，发布前必须手工修，否则读者会直接看到串味内容。
    """
    article = run_dir / "article.md"
    if not article.exists():
        return ["缺正文"]
    try:
        text = article.read_text(encoding="utf-8")
    except Exception:
        return ["正文读取失败"]

    found: dict[str, int] = {}
    for label, token in ARTICLE_DEFECT_PATTERNS:
        count = text.count(token)
        if count:
            found[label] = found.get(label, 0) + count
    return [f"{label}×{count}" for label, count in sorted(found.items())]


# 原文短于这个字数时，比对基准不足，算出来的抄录率会系统性偏低。
SOURCE_MATERIAL_MIN_CHARS = 200


def _copy_ratio(run_dir: Path) -> tuple[float | None, str]:
    """返回（与素材原文的连续抄录率, 说明）。

    ratio 为 None 表示无法判断（没有原文 / 原文太短 / 读取失败）——这类稿件
    必须显式标注，否则会被误当成“没风险”。≥25% 视为高风险：实测 51.5%
    抄录被头条判不原创扣 5 分。
    """
    src_file = run_dir / "source_material_full.txt"
    article_file = run_dir / "article.md"
    if not article_file.exists():
        return None, "无正文"
    if not src_file.exists():
        # 热榜选题只有标题没有素材源（HOT_LIST_ONLY_SOURCES），本就没有原文可比。
        return None, "无素材原文（多为热榜选题），无法查重，需人工判断"
    try:
        # 与自动流程（content_guard）共用同一实现，避免阈值和口径漂移。
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from toutiao_content_factory.services.originality_service import source_copy_ratio

        result = source_copy_ratio(
            article_file.read_text(encoding="utf-8"),
            src_file.read_text(encoding="utf-8"),
        )
    except Exception:
        return None, "查重计算失败"
    if result["ratio"] is None:
        return None, str(result.get("reason") or "无法判断")
    return result["ratio"], ""


def _block_reason(run_dir: Path) -> str:
    """取一条最能说明问题的阻断原因，便于人工快速判断值不值得放行。"""
    payload = _read_json(run_dir / "content_score.json")
    for item in payload.get("publish_risk_flags", []) or []:
        text = str(item).strip()
        if not text:
            continue
        if text.startswith("质量审核专家团阻断发布："):
            text = text.split("：", 1)[1]
        return text[:22]
    return ""


def _pick(rows: list[dict], token: str) -> dict | None:
    """按稳定 ID（如 221632）或序号定位稿件。

    优先按 short_id / 目录名前缀匹配，避免因新 run 生成导致序号漂移而选错稿。
    """
    text = str(token).strip()
    for row in rows:
        if row["short_id"] == text or row["name"].startswith(text):
            return row
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(rows):
            return rows[index - 1]
    print(f"未找到稿件：{text}（先用 list 查看 ID）")
    return None


def cmd_list(args: argparse.Namespace) -> int:
    rows = _collect_runs()
    if not rows:
        return 1
    if args.today:
        today = datetime.now().strftime("%Y%m%d")
        rows = [r for r in rows if r["name"].startswith(today)]
    print(f"\n共 {len(rows)} 篇（按时间倒序）：\n")
    print(f"{'ID':<17}{'状态':<11}{'总分':<6}{'AI风险':<8}{'就绪':<6}{'建议':<24}标题")
    print("-" * 122)
    for r in rows:
        title = r["title"] or ""
        print(
            f"{r['short_id']:<17}{r['status']:<11}{str(r['overall']):<6}{str(r['ai_risk']):<8}"
            f"{str(r['readiness']):<6}{r['recommendation']:<24}{str(title)[:34]}"
        )
        if r["block_reason"]:
            print(f"{'':<17}└─ 拦截：{r['block_reason']}")
        if r["defects"]:
            print(f"{'':<17}└─ 正文硬伤：{', '.join(r['defects'])}  ← 发布前必须修")
        ratio, note = r["copy"]
        if ratio is not None:
            flag = "⛔ 高风险（≥25%会拦）" if ratio >= 25 else ("⚠ 观察" if ratio >= 15 else "✓ 安全")
            print(f"{'':<17}└─ 原创：与素材连续抄录 {ratio}%  {flag}")
        elif note:
            print(f"{'':<17}└─ 原创：{note}")
    print()
    print('提示：approve <ID> 批准；show <ID> 看详情；title <ID> "新标题" 改标题；publish 发布')
    print("     ID 是稳定的（时间戳），不像序号会漂移，可放心复制。")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    rows = _collect_runs()
    row = _pick(rows, args.index)
    if not row:
        return 1
    run_dir: Path = row["dir"]
    print(f"\n目录: {run_dir.name}")
    print(f"状态: {row['status']}  总分: {row['overall']}  AI风险: {row['ai_risk']}  "
          f"就绪度: {row['readiness']}  建议: {row['recommendation']}")
    titles_file = run_dir / "titles.txt"
    if titles_file.exists():
        print("\n--- 候选标题 ---")
        print(titles_file.read_text(encoding="utf-8").strip()[:900])
    article = run_dir / "article.md"
    if article.exists():
        text = article.read_text(encoding="utf-8")
        print(f"\n--- 正文（共 {len(text)} 字，显示前 600 字）---")
        print(text[:600])
    print()
    return 0


def cmd_title(args: argparse.Namespace) -> int:
    rows = _collect_runs()
    row = _pick(rows, args.index)
    if not row:
        return 1
    new_title = args.title.strip()
    if not new_title:
        print("标题不能为空")
        return 1
    limit = 30
    if len(new_title) > limit:
        print(f"警告：标题 {len(new_title)} 字，超过头条 {limit} 字上限，发布时会被截断。")
    for name in ("review_status.json",):
        path = row["dir"] / name
        payload = _read_json(path)
        if not payload:
            payload = {}
        payload["selected_title"] = new_title
        _write_json(path, payload)
    (row["dir"] / "titles.txt").write_text(
        f"推荐标题：\n1. {new_title}\n", encoding="utf-8"
    )
    print(f"已改标题 -> {new_title}  （原文件备份为 *.bak）")
    return 0


def _run_main(remainder: list[str]) -> int:
    """统一走 main.py，避免绕过它内部的资源导出逻辑。"""
    import subprocess

    python_exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(python_exe).exists():
        python_exe = sys.executable
    cmd = [python_exe, str(ROOT / "main.py"), "--account", "robot_notes", *remainder]
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def cmd_check(args: argparse.Namespace) -> int:
    """把正文硬伤定位到具体句子，方便人工直接改。"""
    rows = _collect_runs()
    row = _pick(rows, args.index)
    if not row:
        return 1
    run_dir: Path = row["dir"]
    print(f"\n目录: {run_dir.name}")
    defects = _scan_article_issues(run_dir)
    if not defects:
        print("正文硬伤：无（未发现列表残留、提示词元叙述、内部标签、占位符）")
    else:
        print(f"正文硬伤：{', '.join(defects)}\n")
        article = run_dir / "article.md"
        text = article.read_text(encoding="utf-8")
        shown = 0
        for line_no, line in enumerate(text.splitlines(), start=1):
            hits = [label for label, token in ARTICLE_DEFECT_PATTERNS if token in line]
            if not hits:
                continue
            shown += 1
            snippet = line.strip()
            if len(snippet) > 110:
                snippet = snippet[:110] + "…"
            print(f"  第{line_no}行 [{', '.join(sorted(set(hits)))}] {snippet}")
            if shown >= 12:
                print("  …（仅显示前 12 处）")
                break

    # 原创风险：与素材原文的连续重合度（头条原创库全网比对，扣分案例见
    # 2026-09-15：与素材 51.5% 连续重合被判「不原创」扣 5 分）。
    src_file = run_dir / "source_material_full.txt"
    article_file = run_dir / "article.md"
    if not src_file.exists() or not article_file.exists():
        ratio, note = _copy_ratio(run_dir)
        print(f"\n原创风险：{note or '无法判断'}")
    else:
        sys.path.insert(0, str(ROOT / "src"))
        from toutiao_content_factory.services.originality_service import (
            BLOCK_THRESHOLD,
            REVIEW_THRESHOLD,
            source_copy_ratio,
        )

        result = source_copy_ratio(article_file.read_text(encoding="utf-8"),
                                   src_file.read_text(encoding="utf-8"))
        ratio = result["ratio"]
        if ratio is None:
            print(f"\n原创风险：{result.get('reason') or '无法判断'}")
        else:
            level = ("高风险" if ratio >= BLOCK_THRESHOLD
                     else ("观察" if ratio >= REVIEW_THRESHOLD else "安全"))
            print(f"\n原创风险：连续抄录约 {result['copied_chars']} 字"
                  f"/ 正文 {result['article_chars']} 字 = {ratio}%（{level}）")
            for span in result.get("samples", [])[:5]:
                show = span if len(span) <= 60 else span[:60] + "…"
                print(f"  · [{len(span)}字] {show}")
            if ratio >= BLOCK_THRESHOLD:
                print("  ⛔ 抄录率过高，大概率被判不原创扣分，建议重写后再发。")
            elif ratio >= REVIEW_THRESHOLD:
                print("  ⚠️  存在不原创风险，建议重写重合段落后再发。")
    print()
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    rows = _collect_runs()
    row = _pick(rows, args.index)
    if not row:
        return 1
    # 必须走 main.py --approve-run：它会导出 publish/ 资源包（封面、正文、标题等）。
    # 直接改 review_status.json 只能改状态，发布时会报 Publish package not found。
    defects = _scan_article_issues(row["dir"])
    if defects:
        print(f"⚠️  正文存在硬伤：{', '.join(defects)}")
        print("   读者会直接看到这些内容。建议先 `check` 定位，改完再批准。")
        if not args.force:
            print("   确认仍要发布请加 --force")
            return 1

    # 原创门控：与素材原文的连续抄录率过高会被头条判「不原创」扣分。
    # 实测 51.5% 抄录被扣 5 分，25% 作为拦截线留出安全余量。
    sys.path.insert(0, str(ROOT / "src"))
    from toutiao_content_factory.services.originality_service import (
        BLOCK_THRESHOLD,
        REVIEW_THRESHOLD,
    )

    ratio, note = _copy_ratio(row["dir"])
    if ratio is None:
        print(f"原创检查：{note or '无法判断'}（未拦截，请自行把关）")
    elif ratio >= REVIEW_THRESHOLD:
        print(f"⛔ 原创风险偏高：与素材原文连续抄录 {ratio}%（≥{REVIEW_THRESHOLD}% 拦截线）")
        print("   头条原创库全网比对，素材往往已被别的账号先发。")
        print("   建议重写重合段落后再发；确认要发请加 --force")
        if not args.force:
            return 1
    else:
        print(f"原创检查：与素材连续抄录 {ratio}%，通过（<{REVIEW_THRESHOLD}%"
              f"；自动流程硬拦线 {BLOCK_THRESHOLD}%）")

    remainder = ["--approve-run", "--run-dir", str(row["dir"])]
    title = str(getattr(args, "title", "") or "").strip()
    if not title:
        status_payload = _read_json(row["dir"] / "review_status.json")
        title = str(status_payload.get("selected_title") or "").strip()
    if title:
        remainder += ["--selected-title", title]
    code = _run_main(remainder)
    if code == 0:
        print(f"\n已批准：{row['dir'].name}")
        print("现在可以执行：python scripts/review_publish.py publish")
    return code


def cmd_publish(args: argparse.Namespace) -> int:
    print("即将调用 main.py --publish-latest-approved ...\n")
    remainder = ["--publish-latest-approved"]
    if args.draft:
        remainder.append("--draft-only")
        print("（draft 模式：仅存草稿，不正式发布）")
    return _run_main(remainder)


def main() -> int:
    parser = argparse.ArgumentParser(description="半自动发布助手")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出待审稿件")
    p_list.add_argument("--today", action="store_true", help="只看今天的")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="查看某一篇")
    p_show.add_argument("index", type=str, help="稿件 ID（如 221632）或序号")
    p_show.set_defaults(func=cmd_show)

    p_title = sub.add_parser("title", help="改标题")
    p_title.add_argument("index", type=str, help="稿件 ID（如 221632）或序号")
    p_title.add_argument("title", type=str)
    p_title.set_defaults(func=cmd_title)

    p_approve = sub.add_parser("approve", help="批准某一篇")
    p_approve.add_argument("index", type=str, help="稿件 ID（如 221632）或序号")
    p_approve.add_argument("--title", type=str, default="", help="批准时一并指定最终标题")
    p_approve.add_argument("--force", action="store_true", help="正文有硬伤时仍强制批准")
    p_approve.set_defaults(func=cmd_approve)

    p_check = sub.add_parser("check", help="扫描正文硬伤（列表残留/提示词/内部标签）")
    p_check.add_argument("index", type=str, help="稿件 ID（如 221632）或序号")
    p_check.set_defaults(func=cmd_check)

    p_publish = sub.add_parser("publish", help="发布最新已批准的一篇")
    p_publish.add_argument("--draft", action="store_true")
    p_publish.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
