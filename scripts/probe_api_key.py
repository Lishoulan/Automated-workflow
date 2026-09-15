#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测一个 API key 属于哪个平台（只读，不消耗额度）。

用法：
    python scripts/probe_api_key.py <api_key>

原理：用 Bearer 认证打各平台的 /models 端点。
- 200  → 该平台认这个 key，同时列出可用模型
- 401  → key 无效 / 不属于该平台
- 403  → 可能需要额外凭据（如 AK+SK 配对、或账号权限）

注意：部分平台需要两个凭据（百度千帆 AK/SK、腾讯云 SecretId/SecretKey、
MiniMax key+secret），只给单个 key 必然 401，这类要单独确认。
"""
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PLATFORMS: list[tuple[str, str, str]] = [
    ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "key 格式为 `<32位hex>.<16位hex>`，缺后半段会 401"),
    ("硅基流动", "https://api.siliconflow.cn/v1", ""),
    ("阿里百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", ""),
    ("火山方舟", "https://ark.cn-beijing.volces.com/api/v3", "key 需为特定格式"),
    ("DeepSeek", "https://api.deepseek.com/v1", "通常 sk- 开头"),
    ("Moonshot/Kimi", "https://api.moonshot.cn/v1", "通常 sk- 开头"),
    ("百度千帆", "https://qianfan.baidubce.com/v2", "需要 AK+SK，单 key 不够"),
    ("腾讯混元", "https://api.hunyuan.cloud.tencent.com/v1", "需要 SecretId+SecretKey"),
    ("MiniMax", "https://api.minimax.chat/v1", "需要 key+secret 配对"),
    ("零一万物", "https://api.lingyiwanwu.com/v1", ""),
]


def probe(name: str, base: str, key: str, note: str = "") -> bool:
    url = base.rstrip("/") + "/models"
    try:
        req = Request(url, headers={"Authorization": "Bearer " + key})
        with urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode())
            models = [m.get("id") for m in payload.get("data", [])]
            print(f"  [OK] {name}: 连通 | 模型 {len(models)} 个")
            for mid in models[:8]:
                print(f"         - {mid}")
            return True
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")[:110].replace("\n", " ")
        print(f"  [XX] {name}: HTTP {exc.code} | {body}")
    except URLError as exc:
        print(f"  [XX] {name}: 网络错误 {exc.reason}")
    except Exception as exc:  # pragma: no cover
        print(f"  [XX] {name}: {type(exc).__name__} {str(exc)[:70]}")
    if note:
        print(f"         ^ 提示：{note}")
    return False


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    key = sys.argv[1].strip()
    print(f"探测 key：{key[:6]}...{key[-4:]}（长度 {len(key)}）")
    print(f"前缀 sk- ：{key.startswith('sk-')}")
    print()
    hits = [name for name, base, note in PLATFORMS if probe(name, base, key, note)]
    print()
    if hits:
        print(f"结论：属于 -> {', '.join(hits)}")
        return 0
    print("结论：所测平台均不认这个 key。")
    print("常见原因：① key 复制不完整（智谱需 `xxx.yyy` 两段）")
    print("          ② 属于其它平台 —— 需要你提供 base_url 才能测")
    print("          ③ 需要配套的 secret / AK-SK 才能认证")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
