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
    """搜索候选报道，返回 [{title, url}]。

    ## 为什么要多后端

    ⚠️ 2026-09-19 实测：**云端的可用后端与本地完全不同**，只挑一个必然翻车。
    第一轮只用 Bing（本地正常），上线云端后一条素材都抓不到 —— 因为
    `cn.bing.com` 在美国 IP 上是 301，而 Bing 默认会把中文查询**本地化成美国结果**
    （搜「老人在家摔倒后…」返回「47 Homemade Pizza Recipes」）。

    ## 为什么要"合并"而不是"依次返回第一个有结果的"

    ⚠️ 2026-09-19 第二次踩坑：改成"依次尝试、第一个有结果就用"之后，
    **搜狗微信**（`weixin.sogou.com`）返回了 21 条结果，于是 Bing 的结果全部被丢弃 ——
    可搜狗微信给的是 `/link?url=...` **跳转链接**，实测**全部落到 `antispider` 反爬页**，
    正文恒为 0 字。21 次抓取预算全浪费在拿不到的链接上。

    现在的做法：
      1. **每个后端都查**，把结果合并去重；
      2. 按**能不能抓到正文**排序 —— `PREFERRED_FETCH_HOSTS` 里的域
         （实测在云端能抓到正文的新闻站）排前面，已知拿不到的跳转链接排最后；
      3. 这样抓取预算先花在"大概率能拿到正文"的链接上，
         拿不到时还有摘要兜底。

    用**真实浏览器**取结果（HTTP 直接抓搜索引擎不稳定）。
    """
    merged: list[dict] = []
    seen_urls: set[str] = set()
    for backend in SEARCH_BACKENDS:
        hits = _search_with_backend(backend, query, retries=retries, pause=pause)
        for hit in hits:
            url = str(hit.get("url", "") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(hit)
        # 已经拿到足够多"高优先级"来源就不必再查后面的后端
        if sum(1 for item in merged if _fetch_priority(str(item.get("url", ""))) >= 2) >= limit:
            break
    merged.sort(key=lambda item: -_fetch_priority(str(item.get("url", ""))))
    # ⚠️ 2026-09-19：**不要把已知拿不到正文的链接交给抓取**。
    # 搜狗微信的 `/link?url=` 实测 100% 落到 antispider（正文恒 0 字），
    # 而它在结果列表里常常占掉一大半。抓取预算只有 3 条，
    # 花在这种链接上等于把额度丢进水里 —— 实测过一次"21 条全是搜狗链接"，
    # 结果 usable=0。
    usable = [item for item in merged if _fetch_priority(str(item.get("url", ""))) > 0]
    if usable:
        return usable[:limit]
    # 全是不可抓链接时仍然返回（让上层拿到标题/摘要，走摘要兜底路径）
    return merged[:limit]


#: 实测在 GitHub runner（美国 IP）上**能抓到正文**的新闻域。
#: 抓取预算有限（每次运行 3 条），所以优先花在它们身上。
#: 数据来自 `.runtime/probe_fetch_hosts.py` 的实测。
PREFERRED_FETCH_HOSTS = (
    "news.china.com",
    "china.com",
    "sohu.com",
    "thepaper.cn",
    "news.qq.com",
    "qq.com",
    "163.com",
    "sina.com.cn",
    "ifeng.com",
    "people.com.cn",
    "xinhuanet.com",
    "chinanews.com",
    "cctv.com",
    "baijiahao.baidu.com",
)


def _fetch_priority(url: str) -> int:
    """抓取优先级：2 = 已知可抓正文；0 = 已知抓不到（跳转/搜索站）；1 = 未知。"""
    text = str(url or "")
    if not text:
        return 0
    # 已知抓不到正文的跳转链接：搜狗微信、百度 link、搜索引擎自身
    if "weixin.sogou.com/link" in text or "baidu.com/link" in text:
        return 0
    if any(host in text for host in SKIP_HOSTS):
        return 0
    if any(host in text for host in PREFERRED_FETCH_HOSTS):
        return 2
    return 1
    return []


#: 从搜索结果页抓取「结果列表」的通用 JS：标题 + 链接 + **描述片段**。
#:
#: ⚠️ 2026-09-19：描述片段（Bing 每条结果下面那两行摘要）是**兜底素材的关键** ——
#: 实测云端（美国 IP）能从 Bing 拿到结果列表，但目标站点经常拒绝抓取正文
#: （`尝试 2 个来源` → 正文 0 字 → 放弃运行）。而描述片段本身通常就含
#: 「时间 + 地点 + 人物 + 经过 + 结果」，足以支撑一篇有事实依据的短稿。
_BING_RESULT_JS = """() => {
    const seen = new Set();
    const results = [];
    for (const li of document.querySelectorAll('li.b_algo')) {
        const a = li.querySelector('h2 a');
        if (!a) continue;
        const title = (a.innerText || '').trim();
        const href = a.href || '';
        if (title.length < 6 || !/^https?:/.test(href)) continue;
        if (seen.has(href)) continue;
        seen.add(href);
        const caption = li.querySelector('.b_caption, .b_lineclamp2, .b_algoSlug, p');
        const snippet = caption ? (caption.innerText || '').trim() : '';
        results.push({title: title.slice(0, 120), url: href, snippet: snippet.slice(0, 400)});
    }
    return results;
}"""

#: 搜狗微信结果：标题 + 链接 + 摘要
_SOGOU_RESULT_JS = """() => {
    const seen = new Set();
    const results = [];
    for (const li of document.querySelectorAll('.news-list li, li')) {
        const h3 = li.querySelector('h3');
        if (!h3) continue;
        const a = h3.querySelector('a') || li.querySelector('a');
        if (!a) continue;
        const title = (h3.innerText || '').trim();
        const href = a.href || '';
        if (title.length < 8 || !/^https?:/.test(href)) continue;
        if (seen.has(href)) continue;
        seen.add(href);
        const p = li.querySelector('.txt-info, p');
        const snippet = p ? (p.innerText || '').trim() : '';
        results.push({title: title.slice(0, 120), url: href, snippet: snippet.slice(0, 400)});
    }
    return results;
}"""

#: 搜索后端：(名字, 结果页 URL 模板, 结果项提取 JS)
#: 顺序 = 尝试顺序，按"GitHub runner 上实测可用"排。
#:
#: ⚠️ 2026-09-19 从 runner（美国 IP）实测各入口的结论（`scripts/probe_cn_search.py`）：
#:
#:   可用：www.bing.com + mkt=zh-CN（HTTP 200，命中相关）
#:         www.bing.com 默认（HTTP 200，命中相关）
#:         so.toutiao.com（HTTP 200 但**页面里没有文章链接**，提取恒为 0 条）
#:         weixin.sogou.com（HTTP 200，但 /link 跳转全部落到 antispider，正文 0 字）
#:   不可用：cn.bing.com（**301，直接失效** —— 这是云端抓不到素材的第一层根因）
#:           百度（TLS 握手超时，被墙）
#:           谷歌新闻（302）、谷歌搜索（返回页面里没有中文结果）
#:           新浪/中新网/澎湃/网易/360 站内搜索（均返回无关页面）
#:
#: 第二层根因：即便搜索有结果，**目标站点在美国 IP 上经常拒绝正文抓取**
#: （实测 `尝试 2 个来源` → 正文 0 字）。所以除了换搜索站点，还要用
#: **搜索结果自带的描述片段**兜底（见 `_BING_RESULT_JS` 与 `collect_material`）。
SEARCH_BACKENDS: tuple[tuple[str, str, str], ...] = (
    (
        "bing-global-zh",
        "https://www.bing.com/search?q={q}&mkt=zh-CN&setlang=zh-CN&cc=CN",
        _BING_RESULT_JS,
    ),
    (
        "bing-cn",
        "https://cn.bing.com/search?q={q}",
        _BING_RESULT_JS,
    ),
    (
        "sogou-weixin",
        "https://weixin.sogou.com/weixin?type=2&query={q}",
        _SOGOU_RESULT_JS,
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


def _search_phrases(title: str) -> list[str]:
    """把选题标题变成几个"搜索引擎能给出结果"的查询词。

    ⚠️ 2026-09-19 实测：直接把整句标题丢给搜索引擎，长句标题（尤其是长青题那种
    「…，为什么最后总是闹到翻脸」的议论句）**一条结果都没有**：

        「老人在家摔倒后，子女才发现没人知道他的日常情况」 → hits=0
        「老人把养老钱借给亲戚，对方拖着不还，想要回来得先做哪几件事」 → hits: 0
        「子女轮流照顾老人，为什么最后总是闹到翻脸」 → hits=0

    而短的事件短语能拿到大量真实报道（「男子高空作业绳子被2楼老人剪断」→ 10 条）。
    所以这里按标点切分，从**最短到最长**依次尝试，取第一个有结果的。
    """
    text = str(title or "").strip()
    if not text:
        return []
    phrases: list[str] = []
    head = re.split(r"[，,。！!？?；;：:]", text)[0].strip()
    if head:
        phrases.append(head)
    # 去掉疑问/议论尾巴（「为什么…」「该先做哪几件事」），留事件主干
    stem = re.sub(
        r"(为什么|怎么|如何|该|先|最后|总是|究竟|到底|值得|算|看|做|办|处理|回事|几步|几件事|哪几件事|哪几件|几步)",
        "",
        head,
    ).strip()
    stem = re.sub(r"[的地得了着过]$", "", stem).strip()
    if stem and stem not in phrases and len(stem) >= 4:
        phrases.append(stem)
    # 再退一步：取主干里的前 8~10 个字
    if stem and len(stem) > 10:
        phrases.append(stem[:10])
    phrases.append(text)
    # 去重且保持顺序；太短的丢掉（「老人」这种搜出来全是无关内容）
    seen: set[str] = set()
    out: list[str] = []
    for item in phrases:
        normalized = item.strip()
        if len(normalized) < 4 or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def collect_material(
    title: str,
    *,
    limit: int = 6,
    max_sources: int = 3,
    min_body_chars: int = 600,
    timeout: int = 20,
) -> MaterialBundle:
    """按标题找报道并抓正文。

    搜索用**多个由标题派生的短语**依次尝试（见 `_search_phrases`）：
    长句标题直接搜不到东西，但它的前半句/主干往往能搜到大量真实报道。
    """
    bundle = MaterialBundle(query=title)
    hits: list[dict] = []
    for phrase in _search_phrases(title):
        hits = search_web(phrase, limit=limit)
        if hits:
            break
    for hit in hits:
        if len(bundle.usable) >= max_sources:
            break
        url = hit["url"]
        if any(host in url for host in SKIP_HOSTS):
            # 跳转链接允许（百度 / 搜狗微信的 link 域），但纯搜索/视频站跳过。
            # ⚠️ 2026-09-19：`weixin.sogou.com` 必须在白名单里 ——
            # 它是云端少数有结果的后端，结果给的就是 /link?url= 跳转链接。
            if "baidu.com/link" not in url and "weixin.sogou.com/link" not in url:
                continue
        source = MaterialSource(title=hit["title"], url=url)
        try:
            # `fetch_text_smart`：HTTP 拿到反爬页时自动改用真实浏览器。
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

        # ── 兜底：正文抓不到时，用搜索结果自带的描述片段 ────────────────────
        #
        # ⚠️ 2026-09-19 云端实测：搜索能拿到结果列表，但目标站点在美国 IP 上
        # 经常拒绝抓取（`尝试 2 个来源` → 正文 0 字 → 整个选题作废 → 当天不发）。
        # Bing 每条结果下面那两行摘要通常已经含「时间 + 地点 + 人物 + 经过 + 结果」，
        # 是**有来源、可核对**的事实，比"什么都发不出去"或"让模型凭空编"都好。
        # 标注 `[搜索结果摘要]`，writer 侧仍受反虚构约束（不得补造细节）。
        if not source.ok:
            snippet = str(hit.get("snippet", "") or "").strip()
            snippet = re.sub(r"\s+", " ", snippet)
            if len(snippet) >= 40:
                source.body = f"{hit['title']}。{snippet}"
                source.ok = True
                source.error = ""
        bundle.sources.append(source)

    # 若逐条抓取拿到的正文太少，把所有结果的摘要合并成一份兜底素材
    if sum(len(item.body) for item in bundle.usable) < min_body_chars:
        merged = _merge_snippet_material(hits)
        if merged:
            bundle.sources.append(
                MaterialSource(
                    title=f"{title}（搜索结果汇总）",
                    url="",
                    body=merged,
                    ok=True,
                )
            )
    return bundle


def _merge_snippet_material(hits: list[dict]) -> str:
    """把搜索结果里的标题与描述合并成一份兜底素材。"""
    lines: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        title = str(hit.get("title", "") or "").strip()
        snippet = re.sub(r"\s+", " ", str(hit.get("snippet", "") or "").strip())
        for item in (title, snippet):
            normalized = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9%]+", "", item)
            if len(normalized) < 12 or normalized in seen:
                continue
            seen.add(normalized)
            lines.append(item)
    merged = "\n".join(lines)
    return merged if len(merged) >= 200 else ""


def collect_material_bulk(
    titles: Iterable[str],
    *,
    max_sources: int = 3,
) -> dict[str, MaterialBundle]:
    return {title: collect_material(title, max_sources=max_sources) for title in titles}
