"""把头条号旧作品批量「设为仅我可见」（不删除、可逆）。

为什么用「仅我可见」而不是删除：
  - 一次性删除上百篇在平台看是异常操作，有风控风险；
  - 已发布作品数骤降对账号权重的影响未知；
  - 「仅我可见」已能达到目的：旧内容从公域与主页消失，不再干扰新定位。

实现要点（都来自实机踩坑）：
  1. **必须用元素句柄点击，不能用坐标**。页面会重渲染，坐标点会失效且静默无反应
     （实测：坐标点击后菜单完全不出现，脚本却以为成功）。
  2. 「更多」菜单项为：分享 / 设为仅我可见 / 删除作品；点「设为仅我可见」后
     会弹确认框，按钮是「取消 / 确定」。
  3. **每处理一条都要复核页面状态**。实测出现过「报成功但实际没生效」，所以
     以行文本里是否出现「仅我可见」为唯一判据。

进度写在 .runtime/visibility_progress.json，只有复核通过才记为 private；
中断后重跑会跳过已完成、重试失败的。

用法：
    python scripts/set_works_private.py --dry-run          # 只列出会处理哪些
    python scripts/set_works_private.py --limit 5          # 先小批量
    python scripts/set_works_private.py                    # 全量（当前页 10 条/页）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

MANAGE_URL = "https://mp.toutiao.com/profile_v4/manage/content/all"
PROGRESS = ROOT / ".runtime" / "visibility_progress.json"

ROW_TITLES_JS = """() => {
    const vis = el => { const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0; };
    const mores = [...document.querySelectorAll('*')].filter(el =>
        el.children.length === 0 && (el.textContent || '').trim() === '更多' && vis(el));
    const out = [];
    for (const m of mores) {
        let row = m, found = null;
        for (let i = 0; i < 10 && row; i++) {
            row = row.parentElement; if (!row) break;
            const cls = (row.className || '').toString();
            if (cls.includes('post-item') || cls.includes('article-card-bone')
                || cls.includes('article-card-wrap')) { found = row; break; }
        }
        if (!found) continue;
        const t = (found.innerText || '').split('\\n')[0].trim().slice(0, 90);
        if (t) out.push({title: t, isPrivate: (found.innerText || '').includes('仅我可见')});
    }
    return out;
}"""


def load_progress() -> dict:
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_progress(done: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps({"done": done}, ensure_ascii=False, indent=2), encoding="utf-8")


def more_buttons(page):
    """返回作品行里的「更多」按钮（精确匹配，排除头像区的「更多信息」）。

    ⚠️ 不能用 `page.locator("text=更多")`：它是子串匹配，会把头像区那个
    688x72 的「**更多信息**」按钮也算进来，于是 `text=更多` 匹配到 11 个元素，
    而真实作品行只有 10 个 —— 索引整体错位，导致列表底部的条目点不开菜单
    （实测：PS5 / 工信部 / 俄无人机 三条反复失败，就是这个原因）。
    用 Playwright 的精确文本匹配 `text="更多"` 即可排除「更多信息」。
    """
    return page.locator('text="更多"')


def go_to_page(page, page_no: int) -> bool:
    """点页码跳到指定页。分页控件是 `li.fake-pagination-item`，当前页带 `active`。

    实测共 17 页（167 条，每页 10 条），页码 1..17 都直接可见。
    """
    clicked = page.evaluate(
        """(n) => {
            const items = [...document.querySelectorAll('li.fake-pagination-item, [class*=pagination-item]')];
            for (const it of items) {
                if ((it.textContent || '').trim() === String(n)) {
                    it.click();
                    return true;
                }
            }
            return false;
        }""",
        page_no,
    )
    if not clicked:
        return False
    # ⚠️ 翻页后必须等渲染稳定。实测等待不足时，翻页后的前几条会「点击无反应」
    # （DOM 还在重建，句柄指向旧节点），表现为每页开头几条连续失败。
    page.wait_for_timeout(6000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    return True


def current_page_no(page) -> int | None:
    return page.evaluate(
        """() => {
            const a = document.querySelector('[class*=pagination-item].active');
            if (!a) return null;
            const n = parseInt((a.textContent || '').trim(), 10);
            return Number.isFinite(n) ? n : null;
        }"""
    )


def snapshot(page) -> list[dict]:
    """一次 DOM 快照：同时给出每行的标题、可见状态、以及该行『更多』的句柄定位用索引。

    关键：标题与点击索引**必须来自同一次快照**。之前分成两次取会出现
    「标题与目标行不匹配」——实测同一条被重复处理而真正的目标被跳过。
    """
    return page.evaluate(ROW_TITLES_JS)


def current_private_titles(page) -> set[str]:
    return {r["title"] for r in snapshot(page) if r.get("isPrivate")}


def set_one_private(page, index: int) -> tuple[bool, str]:
    """把第 index 行设为仅我可见。

    返回 (是否复核确认生效, 该行的标题)。

    ⚠️ 标题**必须在点击前、用同一次 DOM 快照按同一个 index 取**。
    最初版本先从一次快照算出 pending 索引、再在另一次快照上按索引取标题，
    两次之间列表会变（已处理的条目状态变化），导致「标题与目标行不匹配」：
    实测同一条被重复处理、而真正的目标被跳过。
    """
    mores = more_buttons(page)
    total = mores.count()
    if index >= total:
        return False, ""

    rows_before = snapshot(page)
    if index >= len(rows_before):
        return False, ""
    title = rows_before[index]["title"]

    target = mores.nth(index)
    try:
        # ⚠️ 滚动必须可靠。实测列表靠下的行（y≈1984，远超视口高度）在
        # `scroll_into_view_if_needed()` 之后仍可能不在可点位置，点击静默无效。
        # 先滚回顶部，把目标滚到**视口中间**，并确认坐标确实落在视口内再点击。
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        target.scroll_into_view_if_needed()
        page.wait_for_timeout(700)
        box = target.bounding_box()
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        if not box or box["y"] < 0 or box["y"] > viewport["height"] - 40:
            target.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
            page.wait_for_timeout(900)
            box = target.bounding_box()
        if not box or box["y"] < 0 or box["y"] > viewport["height"] - 20:
            return False, title
        target.click(timeout=15000)
        page.wait_for_timeout(2500)

        item = page.locator("text=设为仅我可见").first
        if item.count() == 0 or not item.is_visible():
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            return False, title
        item.click(timeout=15000)
        page.wait_for_timeout(2000)

        confirm = page.locator("button:has-text('确定')").first
        try:
            if confirm.count() and confirm.is_visible():
                confirm.click(timeout=10000)
                page.wait_for_timeout(2800)
        except Exception:
            pass

        # 复核：该标题现在是否带「仅我可见」
        privates = current_private_titles(page)
        return title in privates, title
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False, title


def main() -> int:
    parser = argparse.ArgumentParser(description="批量把作品设为仅我可见")
    parser.add_argument("--account", default="robot_notes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理多少条（0=不限）")
    parser.add_argument("--start-page", type=int, default=1, help="从第几页开始")
    parser.add_argument("--min-delay", type=float, default=2.5)
    parser.add_argument("--max-delay", type=float, default=5.0)
    args = parser.parse_args()

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)

    from toutiao_content_factory.config import load_settings
    from playwright.sync_api import sync_playwright

    settings = load_settings(ROOT, account_key=args.account)
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = ROOT / profile_dir

    done = load_progress().get("done", {})
    already = sum(1 for v in done.values() if v == "private")
    print(f"进度：已确认设为仅我可见 {already} 条")

    processed = 0
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=True,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        page = ctx.new_page()
        page.goto(MANAGE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(7000)

        if "/auth/page/login" in page.url:
            print("ERROR: 未登录")
            ctx.close()
            return 2

        total_pages = page.evaluate(
            """() => {
                const items = [...document.querySelectorAll('li.fake-pagination-item, [class*=pagination-item]')];
                let mx = 1;
                for (const it of items) {
                    const n = parseInt((it.textContent || '').trim(), 10);
                    if (Number.isFinite(n) && n > mx) mx = n;
                }
                return mx;
            }"""
        )
        print(f"共 {total_pages} 页\n")

        # ⚠️ 必须有「多次失败就跳过」的保护。实测有一条微头条始终处理不了，
        # 最初版本会一直重试它，把整批都阻塞在这一条上，后面几十条一条都碰不到。
        MAX_ATTEMPTS_PER_TITLE = 3
        attempts: dict[str, int] = {}
        start_page = args.start_page

        for page_no in range(start_page, total_pages + 1):
            if args.limit and processed >= args.limit:
                print(f"已达本次上限 {args.limit}，停止。")
                break
            if page_no != current_page_no(page):
                if not go_to_page(page, page_no):
                    print(f"无法跳到第 {page_no} 页，停止。")
                    break
            rows = snapshot(page)
            print(f"\n=== 第 {page_no}/{total_pages} 页：{len(rows)} 行 ===")

            # 行索引会随状态变化漂移，所以每轮重新取列表与索引
            for _ in range(200):
                rows = snapshot(page)
                pending = [
                    i
                    for i, r in enumerate(rows)
                    if not r.get("isPrivate")
                    and attempts.get(r["title"], 0) < MAX_ATTEMPTS_PER_TITLE
                ]
                if not pending:
                    stuck = [r["title"] for r in rows if not r.get("isPrivate")]
                    if stuck:
                        print(f"  本页剩余 {len(stuck)} 条重试 {MAX_ATTEMPTS_PER_TITLE} 次仍失败，跳过。")
                    else:
                        print("  本页已全部处理。")
                    break
                if args.dry_run:
                    for i in pending:
                        print(f"  [将处理] {rows[i]['title']}")
                    break
                if args.limit and processed >= args.limit:
                    print(f"已达本次上限 {args.limit}，停止。")
                    break

                idx = pending[0]
                ok, title = set_one_private(page, idx)
                if not title:
                    title = rows[idx]["title"]
                attempts[title] = attempts.get(title, 0) + 1
                done[title] = "private" if ok else "failed"
                save_progress(done)
                processed += 1
                suffix = ""
                if not ok and attempts[title] >= MAX_ATTEMPTS_PER_TITLE:
                    suffix = f"  -> 已失败 {attempts[title]} 次，跳过该条"
                print(f"  [{'OK ' if ok else 'FAIL'}] {title[:44]}  (累计 {processed}){suffix}")
                time.sleep(random.uniform(args.min_delay, args.max_delay))
            if args.dry_run:
                break

        save_progress(done)
        ctx.close()

    stats: dict = {}
    for v in done.values():
        stats[v] = stats.get(v, 0) + 1
    print(f"\n=== 汇总 ===\n  {stats}\n  进度文件: {PROGRESS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
