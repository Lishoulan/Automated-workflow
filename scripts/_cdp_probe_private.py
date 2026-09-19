"""连通性/结构探针：启动 Chrome(detached) -> CDP 连上 -> 打开管理页 -> dump 行结构。

只读，不改任何作品状态。用于确认：
  1. 无管道环境下 CDP 方案可行；
  2. 登录态是否有效；
  3. 「更多」菜单是否响应 Input.dispatchMouseEvent（真实鼠标事件）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _cdp_ws import CDP, http_get_json, mouse_click  # noqa: E402

PORT = int(os.environ.get("DSH_CDP_PORT", "9223"))
PROFILE = ROOT / ".runtime" / "accounts" / "robot-notes" / "toutiao_profile"
_MP = Path(os.path.expanduser(r"~\AppData\Local\ms-playwright"))
# 默认用 headless shell：它没有 Chrome 那套完整 mojo/进程沙箱，IPC 更轻，
# 在禁止跨进程管道句柄的环境下更容易起来。
CHROME = Path(os.environ.get(
    "DSH_CHROME",
    str(_MP / "chromium_headless_shell-1234" / "chrome-headless-shell-win64"
        / "chrome-headless-shell.exe")))
URL = "https://mp.toutiao.com/profile_v4/manage/content/all"
LOG = ROOT / ".runtime" / "cdp_chrome.log"


def ensure_chrome() -> None:
    try:
        v = http_get_json(f"http://127.0.0.1:{PORT}/json/version", timeout=3)
        print(f"[chrome] 已在运行: {v.get('Browser')}")
        return
    except Exception:
        pass
    print("[chrome] 启动中 ...")
    with open(LOG, "ab") as out:
        subprocess.Popen(
            [str(CHROME), f"--remote-debugging-port={PORT}",
             f"--user-data-dir={PROFILE}", "--no-first-run",
             "--no-default-browser-check", "--disable-session-crashed-bubble",
             # 关键：本机沙箱拒绝跨进程管道句柄 + 拒绝为网络服务进程授权 profile 目录，
             # 网络服务因此起不来并把浏览器一起带崩。把网络服务并回浏览器进程，单进程直连。
             "--no-sandbox", "--disable-gpu-sandbox",
             "--enable-features=NetworkServiceInProcess2",
             "--disable-features=NetworkServiceSandbox,NetworkServiceInProcess",
             "--disable-dev-shm-usage",
             "about:blank"],
            stdout=out, stderr=out, stdin=subprocess.DEVNULL,
            creationflags=0x00000008 | 0x00000200,
        )
    for _ in range(60):
        time.sleep(1)
        try:
            v = http_get_json(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
            print(f"[chrome] 就绪: {v.get('Browser')}")
            return
        except Exception:
            continue
    raise SystemExit("Chrome 启动超时")


def page_ws() -> str:
    targets = http_get_json(f"http://127.0.0.1:{PORT}/json/list")
    for t in targets:
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"]
    raise SystemExit("no page target")


def alive() -> bool:
    try:
        http_get_json(f"http://127.0.0.1:{PORT}/json/version", timeout=3)
        return True
    except Exception:
        return False


def main() -> int:
    ensure_chrome()
    cdp = CDP(page_ws())
    for method in ("Page.enable", "Runtime.enable", "Network.enable"):
        try:
            cdp.call(method, timeout=12)
            print(f"[cdp] {method} ok")
        except Exception as e:
            print(f"[cdp] {method} FAIL {type(e).__name__}: {e}")
            print(f"[cdp] browser alive after failure: {alive()}")
            print("[cdp] -> 该浏览器实例无法承载页面，放弃")
            return 3

    print("[nav] goto", URL)
    cdp.call("Page.navigate", {"url": URL})
    # 等 load 事件，再额外留渲染时间
    cdp.wait_event("Page.loadEventFired", timeout=60)
    for _ in range(30):
        cdp.pump(1.0)
        state = cdp.eval("document.readyState")
        if state == "complete":
            break
    cdp.pump(8.0)

    print("[url]", cdp.eval("location.href"))
    print("[title]", cdp.eval("document.title"))

    rows = cdp.eval("""() => {
        const vis = el => { const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0; };
        const mores = [...document.querySelectorAll('*')].filter(el =>
            el.children.length === 0 && (el.textContent||'').trim() === '更多' && vis(el));
        const out = [];
        let n = 0;
        for (const m of mores) {
            let row = m, found = null;
            for (let i = 0; i < 10 && row; i++) {
                row = row.parentElement; if (!row) break;
                const cls = (row.className||'').toString();
                if (cls.includes('post-item') || cls.includes('article-card-bone')
                    || cls.includes('article-card-wrap')) { found = row; break; }
            }
            if (!found) continue;
            found.setAttribute('data-dsh-row', String(n));
            const t = (found.innerText||'').split('\\n')[0].trim().slice(0,90);
            if (t) out.push({index: n, title: t,
                             isPrivate: (found.innerText||'').includes('仅我可见')});
            n += 1;
        }
        return out;
    }""")
    print(f"[rows] {len(rows or [])}")
    for r in (rows or []):
        print(f"   {r['index']:>2}  private={str(r['isPrivate']):<5} {r['title'][:50]}")

    pages = cdp.eval("""() => {
        const items = [...document.querySelectorAll('li.fake-pagination-item, [class*=pagination-item]')];
        let mx = 1; const seen = [];
        for (const it of items) {
            const n = parseInt((it.textContent||'').trim(), 10);
            if (Number.isFinite(n)) { seen.push(n); if (n > mx) mx = n; }
        }
        return {max: mx, sample: seen.slice(0, 25)};
    }""")
    print("[pagination]", pages)

    if rows:
        # 只读测试：点第一行的「更多」，看菜单是否出现（随后 Escape 关掉）
        pos = cdp.eval("""() => {
            const row = document.querySelector('[data-dsh-row="0"]');
            if (!row) return null;
            const m = [...row.querySelectorAll('*')].find(el =>
                el.children.length === 0 && (el.textContent||'').trim() === '更多');
            if (!m) return null;
            const r = m.getBoundingClientRect();
            return {x: r.left + r.width/2, y: r.top + r.height/2, w: r.width, h: r.height};
        }""")
        print("[more-btn]", pos)
        if pos:
            mouse_click(cdp, pos["x"], pos["y"])
            cdp.pump(2.5)
            menu = cdp.eval("""() => [...document.querySelectorAll('*')]
                .filter(el => el.children.length === 0 &&
                    ['设为仅我可见','删除作品','分享'].includes((el.textContent||'').trim()))
                .map(el => { const r = el.getBoundingClientRect();
                    return {t: el.textContent.trim(), x: r.left + r.width/2,
                            y: r.top + r.height/2, vis: r.width > 0 && r.height > 0}; })""")
            print("[menu-after-real-click]", json.dumps(menu, ensure_ascii=False)[:500])
            # 再验证 JS element.click() 是否也能弹出（用于选路）
            cdp.eval("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
            cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
            cdp.pump(1.0)
            jsres = cdp.eval("""() => {
                const row = document.querySelector('[data-dsh-row="1"]');
                if (!row) return 'no-row';
                const m = [...row.querySelectorAll('*')].find(el =>
                    el.children.length === 0 && (el.textContent||'').trim() === '更多');
                if (!m) return 'no-more';
                m.click();
                return 'clicked';
            }""")
            cdp.pump(2.0)
            menu2 = cdp.eval("""() => [...document.querySelectorAll('*')]
                .filter(el => el.children.length === 0 &&
                    ['设为仅我可见','删除作品','分享'].includes((el.textContent||'').trim()))
                .map(el => el.textContent.trim())""")
            print(f"[js-click] {jsres} -> menu={menu2}")

    try:
        cdp.call("Browser.close")
    except Exception:
        pass
    print("[done] probe finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
