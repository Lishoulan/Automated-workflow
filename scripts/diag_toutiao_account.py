# -*- coding: utf-8 -*-
"""头条账号真实状态诊断 v2

修复点：
- 头条内容管理页是 iframe 渲染，必须 wait main frame + 等待内容
- 通过点击左侧导航进入各子页面（不靠猜 URL）
- 抽取文章行展现/阅读/点赞/评论
- 截图存盘供人工核对
"""
import asyncio
import json
import os
import re
from datetime import datetime

from playwright.async_api import async_playwright

ROOT = r"F:\Automated-workflow-main"
PROFILE = os.path.join(ROOT, ".runtime", "accounts", "robot-notes", "toutiao_profile")
OUT = os.path.join(ROOT, "diagnostics")
os.makedirs(OUT, exist_ok=True)
TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def save(name, data):
    path = os.path.join(OUT, f"{name}_{TS}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


async def shot(page, tag):
    p = os.path.join(OUT, f"{tag}_{TS}.png")
    try:
        await page.screenshot(path=p, full_page=True)
    except Exception:
        p = None
    return p


async def click_nav(page, text, wait_ms=4000):
    """点击左侧导航文字；若不存在返回 False。"""
    for sel in (
        f"text={text}",
        f"a:has-text('{text}')",
        f"span:has-text('{text}')",
        f"li:has-text('{text}')",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2000):
                await loc.click(timeout=3000)
                await page.wait_for_timeout(wait_ms)
                return True
        except Exception:
            continue
    return False


async def main_frame_text(page):
    try:
        return await page.main_frame.evaluate("() => document.body.innerText")
    except Exception as e:
        return f"<main_frame innerText 失败: {e}>"


async def extract_article_rows(page):
    """在主 frame 抽取头条文章管理行（卡片式）。"""
    js = """
    () => {
      const SEL = ['[class*=\"article-item\"]','[class*=\"content-item\"]','[class*=\"list-item\"]','[class*=\"ArticleCard\"]','article','div[class*=\"card\"]'];
      const seen = new Set();
      const out = [];
      for (const s of SEL) {
        for (const el of document.querySelectorAll(s)) {
          const txt = (el.innerText || '').trim();
          if (txt.length < 20) continue;
          if (!/阅读|展现/.test(txt)) continue;
          // 抓数字
          const m = {
            展现: (txt.match(/展现\\s*([\\d,]+)/) || [])[1] || null,
            阅读: (txt.match(/阅读\\s*([\\d,]+)/) || [])[1] || null,
            点赞: (txt.match(/点赞\\s*([\\d,]+)/) || [])[1] || null,
            评论: (txt.match(/评论\\s*([\\d,]+)/) || [])[1] || null,
          };
          // 标题：第一个含文字且较短的子元素；或第一个 a
          const a = el.querySelector('a[href]');
          let title = (a && (a.getAttribute('title') || (a.innerText||'').trim())) || '';
          if (!title) {
            const tnodes = el.querySelectorAll('h1,h2,h3,h4,[class*=\"title\"]');
            for (const t of tnodes) {
              const tt = (t.innerText||'').trim();
              if (tt.length>4 && tt.length<200) { title=tt; break; }
            }
          }
          if (!title) title = txt.split('\\n')[0].slice(0,80);
          const key = title + '|' + (m.阅读||'') + '|' + (m.展现||'');
          if (seen.has(key)) continue;
          seen.add(key);
          out.push({title, ...m, len: txt.length, snippet: txt.slice(0,160)});
        }
      }
      return out;
    }
    """
    try:
        return await page.main_frame.evaluate(js)
    except Exception as e:
        return [{"error": str(e)}]


async def main():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            channel="msedge",
            headless=False,
            viewport={"width": 1500, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        report = {"probe_at": datetime.now().isoformat(), "profile": PROFILE}

        # 1. 主页 / 仪表盘
        await page.goto("https://mp.toutiao.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        report["home"] = {"url": page.url, "screenshot": await shot(page, "home"),
                          "text_head": (await main_frame_text(page))[:1500]}

        # 2. 作品管理（点击左侧）
        clicked = await click_nav(page, "作品管理", 5000)
        if not clicked:
            await page.goto("https://mp.toutiao.com/profile_v4/manage/content/all",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
        report["content_manage_clicked"] = clicked
        report["content_manage_url"] = page.url
        report["content_manage_shot"] = await shot(page, "content_manage")
        report["content_manage_text_head"] = (await main_frame_text(page))[:2000]
        report["article_rows"] = await extract_article_rows(page)
        print(f"[articles] extracted {len(report['article_rows'])} rows")

        # 3. 收益数据（点击左侧）
        await page.goto("https://mp.toutiao.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        clicked = await click_nav(page, "收益数据", 6000)
        report["income_clicked"] = clicked
        report["income_url"] = page.url
        report["income_shot"] = await shot(page, "income")
        report["income_text"] = await main_frame_text(page)

        # 4. 创作权益
        await page.goto("https://mp.toutiao.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        clicked = await click_nav(page, "创作权益", 6000)
        report["rights_clicked"] = clicked
        report["rights_url"] = page.url
        report["rights_shot"] = await shot(page, "rights")
        report["rights_text"] = await main_frame_text(page)

        # 5. 头条认证
        await page.goto("https://mp.toutiao.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        clicked = await click_nav(page, "头条认证", 6000)
        report["auth_clicked"] = clicked
        report["auth_url"] = page.url
        report["auth_shot"] = await shot(page, "auth")
        report["auth_text"] = await main_frame_text(page)

        # 6. 提现
        await page.goto("https://mp.toutiao.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        clicked = await click_nav(page, "提现", 6000)
        report["withdraw_clicked"] = clicked
        report["withdraw_url"] = page.url
        report["withdraw_shot"] = await shot(page, "withdraw")
        report["withdraw_text"] = await main_frame_text(page)

        out = save("toutiao_diag_v2", report)
        print(f"\n[done] {out}")
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
