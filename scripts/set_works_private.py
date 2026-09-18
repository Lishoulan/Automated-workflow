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
    // 先清掉上一轮打的标记，避免残留
    document.querySelectorAll('[data-dsh-row]').forEach(el => el.removeAttribute('data-dsh-row'));
    const mores = [...document.querySelectorAll('*')].filter(el =>
        el.children.length === 0 && (el.textContent || '').trim() === '更多' && vis(el));
    const out = [];
    let n = 0;
    for (const m of mores) {
        let row = m, found = null;
        for (let i = 0; i < 10 && row; i++) {
            row = row.parentElement; if (!row) break;
            const cls = (row.className || '').toString();
            if (cls.includes('post-item') || cls.includes('article-card-bone')
                || cls.includes('article-card-wrap')) { found = row; break; }
        }
        if (!found) continue;
        // ⚠️ 给行打标记，后续按标记点击，彻底消除「快照与 locator 选取集合不一致」
        // 造成的索引错位（这是低成功率的主因）
        found.setAttribute('data-dsh-row', String(n));
        const t = (found.innerText || '').split('\\n')[0].trim().slice(0, 90);
        if (t) out.push({index: n, title: t,
                         isPrivate: (found.innerText || '').includes('仅我可见')});
        n += 1;
    }
    return out;
}"""

#: 按标记点击该行的「更多」，返回是否点到了
CLICK_MORE_BY_INDEX_JS = """(n) => {
    const row = document.querySelector(`[data-dsh-row="${n}"]`);
    if (!row) return 'no-row';
    const m = [...row.querySelectorAll('*')].find(el =>
        el.children.length === 0 && (el.textContent || '').trim() === '更多');
    if (!m) return 'no-more';
    const r = m.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return 'not-visible';
    m.scrollIntoView({block: 'center', inline: 'center'});
    m.click();
    return 'ok';
}"""


def load_progress() -> dict:
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_progress(done: dict, titles: dict | None = None) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    payload = {"done": done}
    if titles:
        payload["titles"] = titles
    PROGRESS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
    """把标记为 `data-dsh-row=index` 的那一行设为仅我可见。

    返回 (是否复核确认生效, 该行的标题)。

    实现要点：
      - 用 `snapshot()` 给每行打 `data-dsh-row` 标记，点击时按**标记**找行，
        不再依赖 Playwright locator 的 `.nth(i)`。之前两者选取集合的规则不同
        （`text="更多"` 是子串匹配，且会命中容器元素），索引会对不上，
        表现为「点错行」和大量莫名失败。
      - 复核以**该行自己的文本**里是否出现「仅我可见」为唯一判据。
    """
    rows_before = snapshot(page)
    match = next((r for r in rows_before if r["index"] == index), None)
    if match is None:
        return False, ""
    title = match["title"]

    try:
        # ⚠️ 必须用 Playwright 的真实点击，**不能用 JS 的 element.click()**。
        # 实测对照（见 commit 说明）：
        #   A) JS click         -> 菜单不弹出
        #   B) Playwright click -> 菜单弹出 ✅
        # 这个下拉组件只响应真实鼠标事件序列，JS 派发的 click 它不处理，
        # 表现为「点击无反应、随后误判为失败」。曾为「消除索引歧义」改成 JS 点击，
        # 结果整页条目全部失败。
        #
        # 定位用「标记行内部的『更多』」这个 CSS 选择器：既拿到真实点击，
        # 又不依赖 locator 的 .nth(i)（那个集合与快照不一致，会点错行）。
        more = page.locator(f'[data-dsh-row="{index}"] span:has-text("更多")').last
        more.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        more.click(timeout=15000)
        page.wait_for_timeout(2200)

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

        # 复核：重新快照，看该标记行是否已变为「仅我可见」
        for _ in range(3):
            after = snapshot(page)
            row = next((r for r in after if r["index"] == index), None)
            if row is not None:
                if row.get("isPrivate"):
                    return True, title
                if row["title"] == title:
                    # 同一行仍在且还没变私密，再等一会儿
                    page.wait_for_timeout(1500)
                    continue
            # 行不在了（可能因状态变化被过滤），改按标题全局查
            if title in current_private_titles(page):
                return True, title
            break
        return title in current_private_titles(page), title
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

    progress = load_progress()
    done = progress.get("done", {})
    done_titles = progress.get("titles", {})
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

        # ⚠️ 两个设计要点，都是实测踩出来的：
        #
        # 1) 进度**按「页码 + 行号」记，不能按标题记**。实测存在完全同名的重复作品
        #    （例如两条「工厂1.91万台：机器人如何实测」），按标题记会让它们互相干扰：
        #    第一条被标为 private 后，第二条也被当成已完成而漏掉。
        # 2) **多轮重试**。单轮里每条最多试 MAX_ATTEMPTS 次，失败就跳过继续；
        #    整轮跑完后再从头扫一遍，把仍失败的再试。实测有些条目换个时刻就能成功
        #    （页面渲染状态不同），单轮跳过会永久漏掉它们。
        MAX_ATTEMPTS = 3
        MAX_PASSES = 3
        attempts: dict[str, int] = {}
        start_page = args.start_page

        def do_page(page_no: int) -> None:
            """处理一页；每轮只对同一条最多试 MAX_ATTEMPTS 次，失败则跳过继续。"""
            nonlocal processed
            if args.limit and processed >= args.limit:
                return
            if page_no != current_page_no(page):
                if not go_to_page(page, page_no):
                    print(f"无法跳到第 {page_no} 页，跳过。")
                    return
            rows = snapshot(page)
            print(f"\n=== 第 {page_no}/{total_pages} 页：{len(rows)} 行 ===")
            for _ in range(200):
                rows = snapshot(page)
                by_index = {r["index"]: r for r in rows}
                pending = [
                    r["index"]
                    for r in rows
                    if not r.get("isPrivate")
                    and attempts.get(f"p{page_no}-{r['index']}", 0) < MAX_ATTEMPTS
                ]
                if not pending:
                    left = [r["title"] for r in rows if not r.get("isPrivate")]
                    print(f"  本页结束（剩余未处理 {len(left)} 条）")
                    return
                if args.dry_run:
                    for i in pending:
                        print(f"  [将处理] {by_index[i]['title']}")
                    return
                if args.limit and processed >= args.limit:
                    print(f"  已达本次上限 {args.limit}，停止。")
                    return

                idx = pending[0]
                key = f"p{page_no}-{idx}"
                ok, title = set_one_private(page, idx)
                if not title:
                    title = by_index.get(idx, {}).get("title", f"<row {idx}>")
                attempts[key] = attempts.get(key, 0) + 1
                done[key] = "private" if ok else "failed"
                done_titles[key] = title
                save_progress(done, done_titles)
                processed += 1
                suffix = ""
                if not ok and attempts[key] >= MAX_ATTEMPTS:
                    suffix = f"  -> 本条失败 {attempts[key]} 次，本轮跳过"
                print(f"  [{'OK ' if ok else 'FAIL'}] {title[:40]}  (累计 {processed}){suffix}")
                time.sleep(random.uniform(args.min_delay, args.max_delay))

        for pass_no in range(1, MAX_PASSES + 1):
            if args.limit and processed >= args.limit:
                break
            print(f"\n########## 第 {pass_no}/{MAX_PASSES} 轮 ##########")
            for page_no in range(start_page, total_pages + 1):
                if args.limit and processed >= args.limit:
                    break
                do_page(page_no)
            if args.dry_run:
                break

        save_progress(done, done_titles)
        ctx.close()

    stats: dict = {}
    for v in done.values():
        stats[v] = stats.get(v, 0) + 1
    print(f"\n=== 汇总 ===\n  {stats}\n  进度文件: {PROGRESS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
