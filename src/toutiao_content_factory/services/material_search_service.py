"""按标题抓取真实报道正文，为「只有标题」的热榜选题补上可写素材。

## 为什么需要它

热榜类选题（百度热搜 / 头条热榜）在采集时**只有标题，没有正文**：实测 5 条选题的
snippet 长度全部为 0，多数连原文链接都没有。让模型用 28 个字的标题写 1100 字文章，
它只能靠行业套话填空和自我复述 —— 实测正文里出现「订单、验收、回款、利润率承压」
这类与社会新闻毫无关系的内容，还会把提示词当成素材事实复述出来。

这个服务补上中间那一环：用标题去搜索 → 拿到候选报道 → 抓出正文 → 作为素材。

## 实测依据（2026-09-19）

查询「9万买的马车祸身亡 主人获赔13.5万」能从百度命中的结果里抓到 4220 字正文。
百度结果给的是 `www.baidu.com/link?url=...` 跳转链接，必须跟随重定向才能拿到真实
文章地址，所以抓取时要 `follow_redirects`。
"""

from __future__ import annotations

import html
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: 完整的浏览器请求头。
#: ⚠️ 只发 User-Agent + Accept-Language 会被百度判定为机器人并跳到验证码页
#: （实测：返回 `wappass.baidu.com/static/captcha/tuxing_v2.html`，页面仅 1438 字节、
#: 零个 h3 标签，于是搜索命中恒为 0）。补全 Accept / Accept-Encoding / Connection /
#: Sec-Fetch-* 之后才拿到正常结果页。
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

#: 进程级 cookie 会话：百度的 BAIDUID 等要靠它带上，否则每次都像新访客
_COOKIE_JAR: dict[str, str] = {}

#: 搜索结果里要跳过的噪声域名（搜索页自身、聚合页、视频站）
SKIP_HOSTS = (
    "baidu.com",
    "sogou.com",
    "so.com",
    "google.com",
    "bing.com",
    "zhihu.com/search",
    "douyin.com",
    "kuaishou.com",
    "bilibili.com",
    "weibo.com",
)

#: 页面里的导航/推荐类噪声行，抽取正文时按行剔除
NOISE_LINE_PATTERNS = (
    r"^(首页|资讯|军事|财经|娱乐|汽车|游戏|文化|健康|公益|更多|注册|登录|下载|APP|广告)$",
    r"^(-->|<!-+)$",
    r"^(责任编辑|编辑|来源|作者|责编)[:：]",
    r"^(上一篇|下一篇|相关推荐|热点推荐|猜你喜欢|热门推荐|最新相关信息)",
    r"^(版权所有|Copyright|免责声明|关于我们|联系我们|网站地图)",
    r"^[\d\s\-/:：年月日]+$",
    # 百度的横向导航条
    r"^(网页|图片|资讯|视频|笔记|地图|贴吧|文库|采购|小说|翻译|学术|词典|百科|知道|健康|直播|图片)\s",
    r"^(搜索工具|百度一下|相关搜索|其他人还在搜|为您推荐)",
    # ⚠️ 2026-09-19：站点导航块。实测「佛学酒业 --> 文史古诗词人物解梦生活商业成长」
    # 这类面包屑/栏目串漏进了正文，让文章里出现与选题毫无关系的词。
    r"(-->|<!-+|&gt;|&lt;){1,}",
    r"^[\u4e00-\u9fff]{2,6}(\s+[\u4e00-\u9fff]{2,6}){3,}$",  # 空格分隔的栏目串
    r"^([\u4e00-\u9fff]{2,4}){4,}$",  # 无分隔连排的栏目串（如「佛学酒业文史古诗词」）
)

#: 页面里出现这些行就说明抓到的是搜索引擎结果页，不是文章
SEARCH_PAGE_MARKERS = (
    "搜索工具",
    "相关搜索",
    "其他人还在搜",
    "百度一下",
    "找到相关结果",
    "百度为您找到",
)


@dataclass
class MaterialSource:
    """一条找到的素材来源。"""

    title: str
    url: str
    body: str = ""
    ok: bool = False
    error: str = ""


@dataclass
class MaterialBundle:
    """一个选题补齐后的素材集合。"""

    query: str
    sources: list[MaterialSource] = field(default_factory=list)

    @property
    def usable(self) -> list[MaterialSource]:
        return [item for item in self.sources if item.ok and item.body]

    @property
    def combined_text(self) -> str:
        """把可用来源拼成一段素材文本（带来源标记，便于 writer 引用来源感表达）。"""
        parts: list[str] = []
        for item in self.usable:
            parts.append(f"【来源：{item.title}】\n{item.body}")
        return "\n\n".join(parts)

    @property
    def total_chars(self) -> int:
        return sum(len(item.body) for item in self.usable)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


#: 命中这些标记说明落到了反爬/验证页，HTTP 抓取拿不到正文
_ANTISPIDER_MARKERS = (
    "antispider",
    "wappass.baidu.com",
    "验证码",
    "请输入验证码",
    "访问过于频繁",
    "sec.163.com",
)


def _looks_like_antispider(page_html: str, final_url: str) -> bool:
    haystack = f"{final_url} {page_html[:3000]}"
    return any(marker in haystack for marker in _ANTISPIDER_MARKERS)


def fetch_text_with_browser(url: str, timeout_ms: int = 45000) -> tuple[str, str]:
    """用真实浏览器抓页面，返回 (页面 HTML, 最终地址)。

    ⚠️ 2026-09-19 实测：`weixin.sogou.com` 的搜索结果给的是 **`/link?url=...` 跳转链接**，
    用 urllib 直接抓会被判为爬虫、落到 `weixin.sogou.com/antispider/`（正文 0 字）。
    真实浏览器带完整 cookie 与 JS 执行环境时能正常跳转到公众号文章页。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(locale="zh-CN", user_agent=USER_AGENT)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            return page.content(), page.url
        finally:
            browser.close()


def fetch_text_smart(url: str, timeout: int = 20) -> tuple[str, str, str]:
    """先走 HTTP，遇到反爬页自动改用真实浏览器。返回 (html, final_url, how)。

    `how` 是 "http" 或 "browser"，便于日志里看清是哪条路径拿到的。
    """
    try:
        page_html, final_url = fetch_text(url, timeout=timeout)
    except Exception:
        page_html, final_url = "", url
    if page_html and not _looks_like_antispider(page_html, final_url):
        return page_html, final_url, "http"
    # HTTP 拿不到（反爬/异常）→ 换浏览器
    try:
        page_html, final_url = fetch_text_with_browser(url)
    except Exception:
        return page_html, final_url, "browser-failed"
    return page_html, final_url, "browser"


def fetch_text(url: str, timeout: int = 20, follow_redirects: bool = True) -> tuple[str, str]:
    """抓取 URL 并解码为文本，返回 (页面文本, 最终地址)。

    - 带完整浏览器请求头与 cookie 会话（否则百度会跳验证码页）
    - 百度结果链接是跳转链接，必须跟随重定向才能拿到真实文章页
    """
    ctx = _ssl_context()
    headers = dict(BROWSER_HEADERS)
    if _COOKIE_JAR:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in _COOKIE_JAR.items())
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPRedirectHandler(),
    )
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        final_url = resp.geturl()
        # 收下 cookie，供后续请求复用
        for header in resp.headers.get_all("Set-Cookie") or []:
            name_value = header.split(";", 1)[0]
            if "=" in name_value:
                name, _, value = name_value.partition("=")
                _COOKIE_JAR[name.strip()] = value.strip()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc), final_url
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore"), final_url


def strip_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|iframe)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\u3000]+", " ", text)


def extract_article_body(page_html: str, min_chars: int = 600) -> str:
    """从页面 HTML 里抽出正文段落。

    做法：按块级标签切段，逐行剔除导航/版权类噪声，保留足够长的中文句子，
    最后拼成正文。刻意保持简单——复杂的正文抽取器在这里不必要，而且更容易
    把推荐流里的无关文章混进来。

    会先判断页面是不是搜索引擎结果页：百度结果里第一条常常是「…的最新相关信息」
    这类聚合页，它的正文其实是热榜列表，混进素材会把别的新闻内容带进文章。
    """
    plain_head = strip_tags(page_html)[:4000]
    if sum(1 for marker in SEARCH_PAGE_MARKERS if marker in plain_head) >= 2:
        return ""
    # 取 <body> 范围，避免 head 里的 meta 干扰
    m = re.search(r"(?is)<body[^>]*>(.*?)</body>", page_html)
    fragment = m.group(1) if m else page_html
    # 段落级切分
    blocks = re.split(r"(?i)</?(?:p|div|br|li|h[1-6]|section|article)[^>]*>", fragment)
    noise_res = [re.compile(p) for p in NOISE_LINE_PATTERNS]
    kept: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        text = strip_tags(block).strip()
        if not text:
            continue
        if len(text) < 15:
            continue
        if any(rx.search(text) for rx in noise_res):
            continue
        # 中文占比过低的多半是导航或代码
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        if cjk < len(text) * 0.4:
            continue
        if text in seen:
            continue
        seen.add(text)
        kept.append(text)
        if sum(len(x) for x in kept) >= 6000:
            break
    body = "\n".join(kept)
    return body if len(body) >= min_chars else ""


def search_web(query: str, limit: int = 6, *, retries: int = 2, pause: float = 2.0) -> list[dict]:
    """搜索候选报道，返回 [{title, url}]。**按多个搜索后端依次尝试**。

    ⚠️ 为什么必须是"多后端"（2026-09-19 实测，两轮踩坑）：

    第一轮：只用 Bing，本地（中国）正常。上线云端后**一条都抓不到**。
    第二轮：从 GitHub runner（美国 IP）实测，Bing 返回的是**本地化结果** ——
        搜「老人在家摔倒后，子女才发现没人知道他的日常情况」返回**披萨食谱**，
        搜「张雪回应打车被认出"忽悠"司机」返回 **Reddit 板块**，
        抓下来全是 403 或登录页，`usable=0 total_chars=0`。
    同一次从 runner 跑的入口探测（`scripts/probe_cn_search.py`）：

        可用：今日头条搜索 so.toutiao.com（HTTP 200，命中相关）
              搜狗微信   weixin.sogou.com（HTTP 200，命中相关）
        不可用：百度（TLS 握手超时，被墙）
                必应中国 cn.bing.com（301 失效）
                新浪/中新网/澎湃/网易站内搜索（均返回无关页面）

    结论：**云端的可用后端与本地完全不同**，所以不能只挑一个。
    按"在 runner 上实测可用"的顺序尝试，全部失败才返回空：

    1. `so.toutiao.com`  —— runner 与本地都可用，且与发布平台同源，最优先
    2. `weixin.sogou.com` —— runner 上可用，公众号文章质量高
    3. `cn.bing.com`      —— 本地主力，runner 上会退化，仅作兜底

    每个后端失败就换下一个；用**真实浏览器**取结果（HTTP 直接抓搜索引擎不稳定）。
    """
    for backend in SEARCH_BACKENDS:
        hits = _search_with_backend(backend, query, retries=retries, pause=pause)
        if hits:
            return hits[:limit]
    return []


#: 搜索后端：(名字, 结果页 URL 模板, 结果项提取 JS)
#: 顺序 = 尝试顺序，按"GitHub runner 上实测可用"排。
SEARCH_BACKENDS: tuple[tuple[str, str, str], ...] = (
    (
        "toutiao",
        "https://so.toutiao.com/search?keyword={q}",
        # 头条搜索结果里正文链接通常带 /article/ 或 /w/ 或 /group/
        """() => {
            const seen = new Set();
            const results = [];
            const anchors = document.querySelectorAll('a[href]');
            for (const a of anchors) {
                const title = (a.innerText || '').trim();
                const href = a.href || '';
                if (title.length < 8) continue;
                if (!/^https?:/.test(href)) continue;
                if (!/(toutiao\\.com\\/(article|w|group|a)|\\/item\\/)/.test(href)) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                results.push({title: title.slice(0, 120), url: href});
            }
            return results;
        }""",
    ),
    (
        "sogou-weixin",
        "https://weixin.sogou.com/weixin?type=2&query={q}",
        # 公众号文章结果在 .news-list li 里，链接是 /link?url=... 跳转
        """() => {
            const seen = new Set();
            const results = [];
            for (const h3 of document.querySelectorAll('.news-list h3, .txt-box h3, h3')) {
                const a = h3.querySelector('a') || (h3.closest('a') ? h3.closest('a') : null);
                if (!a) continue;
                const title = (h3.innerText || a.innerText || '').trim();
                let href = a.href || '';
                if (title.length < 8 || !/^https?:/.test(href)) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                results.push({title: title.slice(0, 120), url: href});
            }
            return results;
        }""",
    ),
    (
        "bing-cn",
        "https://cn.bing.com/search?q={q}",
        """() => {
            const seen = new Set();
            const results = [];
            for (const h2 of document.querySelectorAll('li.b_algo h2, h2')) {
                const a = h2.querySelector('a');
                if (!a) continue;
                const title = (a.innerText || '').trim();
                const href = a.href || '';
                if (title.length < 6 || !/^https?:/.test(href)) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                results.push({title: title.slice(0, 120), url: href});
            }
            return results;
        }""",
    ),
)


def _search_with_backend(
    backend: tuple[str, str, str],
    query: str,
    *,
    retries: int = 2,
    pause: float = 2.0,
) -> list[dict]:
    """用单个后端搜索；失败（含反爬页）返回空列表，由调用方换下一个后端。"""
    name, template, extractor = backend
    from playwright.sync_api import sync_playwright

    url = template.replace("{q}", urllib.parse.quote(query))
    out: list[dict] = []
    for attempt in range(1, max(retries, 1) + 1):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(locale="zh-CN", user_agent=USER_AGENT)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)
                out = page.evaluate(extractor)
                browser.close()
        except Exception:
            out = []
        if out:
            break
        if attempt < retries:
            time.sleep(pause * attempt)
    if out:
        return _filter_relevant_hits(name, query, out)
    return []


def _filter_relevant_hits(backend_name: str, query: str, hits: list[dict]) -> list[dict]:
    """丢掉与查询无关的结果。

    ⚠️ 2026-09-19 实测（GitHub runner）：Bing 会把中文查询本地化成美国结果 ——
    搜「老人在家摔倒后…」返回「47 Homemade Pizza Recipes」，
    搜「张雪回应打车…」返回 Reddit 板块。这些条目抓下来全是 403，
    白白耗掉抓取预算，还会让人误以为"搜索引擎是通的"。
    判据：标题或 URL 里必须出现查询中的中文片段（取最长的 2-4 字词）。
    """
    tokens = [
        token
        for token in re.findall(r"[\u4e00-\u9fff]{2,4}", query)
        if token
    ]
    if not tokens:
        return hits
    kept: list[dict] = []
    for hit in hits:
        haystack = f"{hit.get('title', '')} {hit.get('url', '')}"
        if any(token in haystack for token in tokens):
            kept.append(hit)
    return kept


def collect_material(
    title: str,
    *,
    limit: int = 6,
    max_sources: int = 3,
    min_body_chars: int = 600,
    timeout: int = 20,
) -> MaterialBundle:
    """按标题找报道并抓正文。"""
    bundle = MaterialBundle(query=title)
    for hit in search_web(title, limit=limit):
        if len(bundle.usable) >= max_sources:
            break
        url = hit["url"]
        if any(host in url for host in SKIP_HOSTS):
            # 跳转链接允许（百度 / 搜狗微信的 link 域），但纯搜索/视频站跳过。
            # ⚠️ 2026-09-19：`weixin.sogou.com` 必须在白名单里 ——
            # 它是云端（美国 IP）少数可用的搜索后端，结果给的就是 /link?url= 跳转链接。
            if "baidu.com/link" not in url and "weixin.sogou.com/link" not in url:
                continue
        source = MaterialSource(title=hit["title"], url=url)
        try:
            # `fetch_text_smart`：HTTP 拿到反爬页时自动改用真实浏览器。
            # 搜狗微信的跳转链接必须走浏览器，否则一律落到 antispider。
            page, final_url, how = fetch_text_smart(url, timeout=timeout)
            source.url = final_url or url
            body = extract_article_body(page, min_chars=min_body_chars)
            if body:
                source.body = body
                source.ok = True
            else:
                source.error = f"页面里没抽出足够长的正文（{how}）"
        except urllib.error.HTTPError as exc:
            source.error = f"HTTP {exc.code}"
        except Exception as exc:
            source.error = f"{type(exc).__name__}: {str(exc)[:80]}"
        bundle.sources.append(source)
    return bundle


def collect_material_bulk(
    titles: Iterable[str],
    *,
    max_sources: int = 3,
) -> dict[str, MaterialBundle]:
    return {title: collect_material(title, max_sources=max_sources) for title in titles}
