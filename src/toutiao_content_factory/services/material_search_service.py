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
    evidence_type: str = "article"


@dataclass(frozen=True)
class MaterialEvidenceAssessment:
    """Whether collected material can support a fact-grounded article."""

    passed: bool
    total_chars: int
    independent_source_count: int
    full_article_count: int
    factual_anchor_count: int
    reasons: tuple[str, ...] = ()


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


_FACT_ANCHOR_PATTERN = re.compile(
    r"(?:"
    r"\d+(?:\.\d+)?(?:%|％|元|万元|亿元|人|家|项|次|天|年|月|日|时|分|秒|米|公里|英寸|款|台|个)?"
    r"|[一二两三四五六七八九十百千万亿两]+(?:人|家|项|次|天|年|月|日|元|米|款|台|个)"
    r"|据|报道|通报|公告|发布|表示|回应|显示|披露|介绍|宣布|确认"
    r"|发生|完成|推出|上线|开工|签约|获批|增长|下降|达到|超过|位于|来自|涉及|造成|截至"
    r")"
)


def _source_identity(source: MaterialSource) -> str:
    """Return an identity that does not count several pages from one site twice."""
    url = str(source.url or "").strip()
    if url:
        host = urllib.parse.urlparse(url).netloc.lower()
        host = re.sub(r"^(?:www\.|m\.|mobile\.)", "", host)
        if host:
            labels = host.split(".")
            if len(labels) >= 3 and ".".join(labels[-2:]) in {
                "com.cn",
                "net.cn",
                "org.cn",
                "gov.cn",
            }:
                host = ".".join(labels[-3:])
            elif len(labels) >= 2:
                host = ".".join(labels[-2:])
            return f"host:{host}"
    title = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(source.title or "")).lower()
    return f"title:{title[:80]}" if title else ""


def _factual_anchor_count(sources: Iterable[MaterialSource]) -> int:
    anchors: set[str] = set()
    for source in sources:
        for sentence in re.split(r"[。！？!?；;\n]+", str(source.body or "")):
            sentence = re.sub(r"\s+", "", sentence).strip()
            if len(sentence) < 10 or not _FACT_ANCHOR_PATTERN.search(sentence):
                continue
            # Search results often repeat the same sentence with a different title.
            # A normalized prefix prevents those copies from inflating the count.
            normalized = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9%％]+", "", sentence).lower()
            if normalized:
                anchors.add(normalized[:120])
    return len(anchors)


def assess_material_evidence(bundle: MaterialBundle) -> MaterialEvidenceAssessment:
    """Assess source diversity and concrete facts before an article is generated.

    A single substantial article can carry an event. Shorter material needs at
    least two independent sources. Synthetic search-result digests add context,
    but never count as an independent source or a full article.
    """
    usable = list(bundle.usable)
    real_sources = [item for item in usable if isinstance(item, MaterialSource)]

    # Keep compatibility with custom collectors that implement the historical
    # MaterialBundle protocol but do not expose typed MaterialSource objects.
    if usable and not real_sources:
        total_chars = int(getattr(bundle, "total_chars", 0) or 0)
        return MaterialEvidenceAssessment(
            passed=total_chars >= 400,
            total_chars=total_chars,
            independent_source_count=1,
            full_article_count=1,
            factual_anchor_count=0,
            reasons=() if total_chars >= 400 else ("素材少于 400 字",),
        )

    unique_sources: list[MaterialSource] = []
    seen_bodies: set[str] = set()
    for source in real_sources:
        normalized = re.sub(
            r"[^\u4e00-\u9fffA-Za-z0-9%％]+", "", str(source.body or "")
        ).lower()
        if not normalized or normalized in seen_bodies:
            continue
        seen_bodies.add(normalized)
        unique_sources.append(source)

    total_chars = sum(len(str(item.body or "")) for item in unique_sources)
    independent_ids = {
        identity
        for item in unique_sources
        if item.evidence_type != "search_digest"
        if (identity := _source_identity(item))
    }
    full_articles = [
        item for item in unique_sources if item.evidence_type == "article"
    ]
    longest_article = max((len(item.body) for item in full_articles), default=0)
    factual_anchors = _factual_anchor_count(unique_sources)

    reasons: list[str] = []
    if total_chars < 400:
        reasons.append("素材少于 400 字")
    if factual_anchors < 5:
        reasons.append(f"具体事实锚点只有 {factual_anchors} 个，少于 5 个")
    source_gate_passed = longest_article >= 900 or (
        len(independent_ids) >= 2 and total_chars >= 600
    )
    if not source_gate_passed:
        reasons.append(
            "缺少一篇 900 字以上完整正文，且独立来源不足 2 个或总素材少于 600 字"
        )

    return MaterialEvidenceAssessment(
        passed=not reasons,
        total_chars=total_chars,
        independent_source_count=len(independent_ids),
        full_article_count=len(full_articles),
        factual_anchor_count=factual_anchors,
        reasons=tuple(reasons),
    )


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


#: 进程级「后端已死」缓存：本次运行里连续拿不到结果的搜索后端不再重复查询。
#:
#: ⚠️ 2026-09-20：一次运行要为 4 个候选选题各搜一次，而每次都把 3 个后端全查一遍，
#: 每个后端还带 2 次重试 —— 实测 `cn.bing.com`（在美国 IP 上 301）白白多花
#: 1~2 分钟/次。这个缓存把"已经证明没用"的后端从后续查询里摘掉。
_DEAD_BACKENDS: set[str] = set()


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
      1. 查**还没被证明没用**的后端，把结果合并去重；
      2. 按**能不能抓到正文**排序 —— `PREFERRED_FETCH_HOSTS` 里的域
         （实测在云端能抓到正文的新闻站）排前面，已知拿不到的跳转链接排最后；
      3. 这样抓取预算先花在"大概率能拿到正文"的链接上，
         拿不到时还有摘要兜底。

    用**真实浏览器**取结果（HTTP 直接抓搜索引擎不稳定）。
    """
    merged: list[dict] = []
    seen_urls: set[str] = set()
    for backend in SEARCH_BACKENDS:
        name = backend[0]
        if name in _DEAD_BACKENDS:
            continue
        hits = _search_with_backend(backend, query, retries=retries, pause=pause)
        if not hits:
            # 完全没结果 = 这个后端在当前网络下不可用（如 cn.bing.com 在美国 IP 上 301）。
            # 后续查询不再浪费时间去试它 —— 一次运行有 4 个候选要搜。
            _DEAD_BACKENDS.add(name)
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
    判据：优先接受完整查询；否则标题/摘要至少覆盖一半查询二元组，且长查询
    至少命中 4 个二元组。不能因为“父亲”“邻居”这类常见词就放行另一条故事。
    """
    query_text = "".join(re.findall(r"[\u4e00-\u9fff]", str(query or "")))
    if not query_text:
        return hits
    query_bigrams = {query_text[index : index + 2] for index in range(len(query_text) - 1)}
    kept: list[dict] = []
    for hit in hits:
        title = str(hit.get("title", "") or "").strip()
        # A news digest may mention the query as one item among several
        # unrelated headlines.  It is not evidence for the selected story.
        if _looks_like_multi_topic_digest(title):
            continue
        haystack = " ".join(
            str(value or "")
            for value in (title, hit.get("snippet", ""), hit.get("description", ""))
        )
        haystack_text = "".join(re.findall(r"[\u4e00-\u9fff]", haystack))
        if query_text in haystack_text:
            kept.append(hit)
            continue
        if len(query_text) < 4 or not query_bigrams:
            continue
        haystack_bigrams = {
            haystack_text[index : index + 2]
            for index in range(len(haystack_text) - 1)
        }
        overlap = query_bigrams & haystack_bigrams
        minimum_overlap = 2 if len(query_bigrams) <= 5 else 4
        if len(overlap) >= minimum_overlap and len(overlap) / len(query_bigrams) >= 0.5:
            kept.append(hit)
    return kept


def _looks_like_multi_topic_digest(title: str) -> bool:
    """Return whether a result title lists several unrelated headlines."""
    text = str(title or "").strip()
    if not text:
        return False
    separator_count = len(re.findall(r"[;；|｜]", text))
    digest_prefix = bool(re.match(r"^(?:早报|晚报|晨报|午报|新闻速览|今日要闻)[：:]", text))
    return separator_count >= 2 or (digest_prefix and separator_count >= 1)


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
                source.evidence_type = "article"
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
                source.evidence_type = "search_snippet"
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
                    evidence_type="search_digest",
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
