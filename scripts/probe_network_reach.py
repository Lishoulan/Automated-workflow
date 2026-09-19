"""从当前机器探测材料抓取链路的连通性。

用途：判断"云端抓不到正文素材"是不是网络可达性问题。
在 GitHub runner（美国）与本地（中国）分别跑一次，对比结果即可定位。
"""

from __future__ import annotations

import socket
import ssl
import sys
import time
from urllib.parse import urlparse

TARGETS = [
    # 搜索引擎
    "https://www.bing.com/search?q=test",
    "https://www.baidu.com/s?wd=test",
    # 中文新闻站点（材料抓取的典型来源）
    "https://news.sina.com.cn/",
    "https://www.thepaper.cn/",
    "https://www.chinanews.com.cn/",
    "https://www.toutiao.com/",
    "https://baijiahao.baidu.com/",
    "https://www.163.com/",
    "https://www.sohu.com/",
    # 对照：境外站点
    "https://www.google.com/",
]


def probe(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    started = time.monotonic()
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return False, f"TCP 失败: {type(exc).__name__}: {exc}"
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(
                f"HEAD {parsed.path or '/'} HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n".encode()
            )
            data = tls.recv(256).decode("latin-1", "replace")
        elapsed = time.monotonic() - started
        status = data.split("\r\n", 1)[0].strip()
        return True, f"{elapsed:.1f}s {status}"
    except Exception as exc:  # noqa: BLE001
        return False, f"TLS 失败: {type(exc).__name__}: {exc}"
    finally:
        try:
            raw.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    print(f"python={sys.version.split()[0]}")
    ok_count = 0
    for url in TARGETS:
        ok, detail = probe(url)
        ok_count += 1 if ok else 0
        print(f"{'OK  ' if ok else 'FAIL'} {url}  -> {detail}")
    print(f"\n可达 {ok_count}/{len(TARGETS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
