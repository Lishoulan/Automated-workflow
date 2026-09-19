"""探测中文搜索/新闻入口从当前机器是否可用。

用途：GitHub runner（美国）上的 Bing 返回的是**本地化结果**——实测搜中文社会新闻
返回「披萨食谱」「Reddit 板块」，材料抓取因此全部失败。
本脚本逐个探测中国搜索入口，判断哪一条链路在 runner 上可用。

本地（中国）与 runner（美国）分别跑，对比 `OK/FAIL` 即可。
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
from urllib.parse import quote

TIMEOUT = 10.0


def _http_get(url: str, *, host: str | None = None, timeout: float = TIMEOUT) -> tuple[int, str]:
    """极简 HTTPS GET，返回 (状态码, 正文前若干字符)。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    target = parsed.hostname or ""
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    header_host = host or target

    raw = socket.create_connection((target, port), timeout=timeout)
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(raw, server_hostname=target) as tls:
            req = (
                f"GET {path} HTTP/1.1\r\nHost: {header_host}\r\n"
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36\r\n"
                "Accept-Language: zh-CN,zh;q=0.9\r\n"
                "Accept: text/html,application/xhtml+xml\r\nConnection: close\r\n\r\n"
            )
            tls.sendall(req.encode())
            chunks: list[bytes] = []
            total = 0
            while total < 20000:
                data = tls.recv(8192)
                if not data:
                    break
                chunks.append(data)
                total += len(data)
        body = b"".join(chunks).decode("utf-8", "replace")
        status = 0
        first = body.split("\r\n", 1)[0]
        if " " in first:
            try:
                status = int(first.split()[1])
            except (IndexError, ValueError):
                status = 0
        return status, body
    finally:
        try:
            raw.close()
        except Exception:  # noqa: BLE001
            pass


PROBES = [
    ("百度搜索", "https://www.baidu.com/s?wd=" + quote("老人摔倒")),
    ("百度资讯", "https://www.baidu.com/s?rtt=1&tn=news&word=" + quote("老人摔倒")),
    ("搜狗搜索", "https://www.sogou.com/web?query=" + quote("老人摔倒")),
    ("搜狗微信", "https://weixin.sogou.com/weixin?type=2&query=" + quote("老人摔倒")),
    ("必应中国", "https://cn.bing.com/search?q=" + quote("老人摔倒") + "&ensearch=0"),
    ("必应中文站", "https://cn.bing.com/search?q=" + quote("老人摔倒") + "&mkt=zh-CN"),
    ("必应全球zh", "https://www.bing.com/search?q=" + quote("老人摔倒") + "&mkt=zh-CN&setlang=zh-CN&cc=CN"),
    ("必应全球默认", "https://www.bing.com/search?q=" + quote("老人摔倒")),
    ("必应新闻", "https://www.bing.com/news/search?q=" + quote("老人摔倒") + "&mkt=zh-CN"),
    ("谷歌新闻", "https://news.google.com/search?q=" + quote("老人摔倒") + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("谷歌搜索", "https://www.google.com/search?q=" + quote("老人摔倒") + "&hl=zh-CN"),
    ("今日头条搜索", "https://so.toutiao.com/search?keyword=" + quote("老人摔倒")),
    ("新浪搜索", "https://search.sina.com.cn/?q=" + quote("老人摔倒") + "&c=news"),
    ("中新网搜索", "https://sou.chinanews.com.cn/search.do?q=" + quote("老人摔倒")),
    ("澎湃搜索", "https://www.thepaper.cn/searchResult?searchWord=" + quote("老人摔倒")),
    ("网易搜索", "https://www.163.com/search?keyword=" + quote("老人摔倒")),
    ("360搜索", "https://www.so.com/s?q=" + quote("老人摔倒")),
]


def main() -> int:
    print(f"python={sys.version.split()[0]}")
    results: list[tuple[str, bool, str, int]] = []
    for name, url in PROBES:
        try:
            status, body = _http_get(url)
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"[:90], 0))
            continue
        # 用"正文里出现查询词"判断结果是否真的相关，而不只是 HTTP 200
        relevant = "老人" in body and "摔倒" in body
        marker = "OK  " if (status == 200 and relevant) else "WEAK"
        results.append((name, status == 200 and relevant, f"HTTP {status} relevant={relevant} len={len(body)}", status))

    for name, ok, detail, _status in results:
        print(f"{'OK  ' if ok else 'FAIL'} {name:12} {detail}")
    print(f"\n可用 {sum(1 for _, ok, _, _ in results if ok)}/{len(results)}")
    print(json.dumps({name: ok for name, ok, _, _ in results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
