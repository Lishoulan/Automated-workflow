"""实证：免费模型 glm-4-flash 生成的标题，能否过现有标题闸门。

零成本（glm-4-flash 免费）。用真实选题与真实事实，跑真实提示词。
输出：过闸率 + 被拒原因分布 + 通过的具体标题。

2026-09-18 结论：19 条标题 14 条过闸（74%）。据此推翻了代码里
「glm-4-flash 生成的标题会被全部过滤」的既有假设，云端标题模型
由付费的 glm-4.6 改为 glm-4-flash（commit a44f355）。

用法：.venv/Scripts/python.exe scripts/free_model_title_probe.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker  # noqa: E402

ENV: dict[str, str] = {}
for _line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        ENV[_k.strip()] = _v.strip()

# 真实选题 + 真实事实（取自 9-17/9-18 已发布稿件的题材）
TOPIC = "人形机器人进入交付期：订单、产能与利润的分化"
SUMMARY = (
    "埃斯顿、优必选、宇树等公司陆续披露订单与产能进展，人形机器人从展示阶段进入交付阶段。"
    "关节电机交付量突破5万台，产业链利润开始分层；工信部推动机器人密度目标，但真实产能"
    "与交付能力仍是瓶颈。"
)
KEY_POINTS = [
    "关节电机交付量突破5万台",
    "工信部推动机器人密度目标80%",
    "优必选柳州工厂规划年产万台",
    "行业年出货量预判仅2万台",
    "机器人产业链利润开始分层",
]

PROMPT = f"""你是 TitleAgent，负责生成适合今日头条的科技行业标题。

账号：
手蓝机器人笔记

风格要求：
聚焦科技商业与先进制造，用数据拆热点，用产业链看趋势兑现。

请输出一个 JSON 对象，不要输出 JSON 之外的内容。

字段要求：
- titles: 20 个标题数组
- recommended_titles: 3 个推荐标题数组

标题要求：
- 信息量高
- 有判断、有分化、有结果感
- 可适度使用反问、对比、数字，但不能低质夸张
- 每条不超过 30 字
- 20 条标题的开头句式必须分散，同一句式最多 2 条

文章摘要：
{SUMMARY}

关键数据：
{json.dumps(KEY_POINTS, ensure_ascii=False)}


"""

CHECKER = ArticleQualityChecker()
BODY = SUMMARY + " " + " ".join(KEY_POINTS)


def call(model: str) -> tuple[list[str], str]:
    base = ENV["OPENAI_BASE_URL"].rstrip("/")
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 2000,
            "temperature": 0.9,
        }
    ).encode()
    req = urllib.request.Request(
        base + "/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {ENV['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"] or ""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
    try:
        parsed = json.loads(cleaned)
        titles = parsed.get("titles") or []
    except json.JSONDecodeError:
        titles = [
            ln.strip().lstrip("0123456789.、) ")
            for ln in cleaned.splitlines()
            if ln.strip()
        ]
    return [str(t).strip() for t in titles if str(t).strip()], content


def gate(title: str) -> list[str]:
    try:
        result = CHECKER._check_title(title, BODY)
        issues = result.get("issues", []) or []
    except Exception as exc:  # pragma: no cover
        return [f"ERROR:{type(exc).__name__}"]
    out = []
    for issue in issues:
        if isinstance(issue, dict):
            out.append(str(issue.get("code") or issue.get("rule") or issue)[:40])
        else:
            out.append(str(issue)[:40])
    return out


def run(model: str) -> None:
    print(f"\n{'=' * 78}\n模型：{model}\n{'=' * 78}")
    try:
        titles, _raw = call(model)
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode()[:180]
            except Exception:
                detail = "?"
        print(f"  调用失败：{type(exc).__name__} {exc} {detail}")
        return
    print(f"  生成标题数：{len(titles)}")
    if not titles:
        return
    passed, rejected = [], []
    reasons: Counter[str] = Counter()
    for t in titles:
        issues = gate(t)
        if issues:
            rejected.append((t, issues))
            for code in issues:
                reasons[code.split("（")[0][:28]] += 1
        else:
            passed.append(t)
    rate = len(passed) / len(titles) * 100
    print(f"  过闸：{len(passed)}/{len(titles)}（{rate:.0f}%）")
    print("\n  --- 通过的标题 ---")
    for t in passed:
        print(f"    ✅ {t}")
    print("\n  --- 被拒原因分布 ---")
    for code, n in reasons.most_common():
        print(f"    {n:>3}x {code}")


if __name__ == "__main__":
    for m in ("glm-4-flash", "glm-4.7-flash"):
        run(m)
