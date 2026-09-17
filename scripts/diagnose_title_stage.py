"""标题阶段归因诊断：回答「20 个候选到底在哪一步被杀光」。

Phase 0.5 的云端对照实验发现：换成 glm-4.6 之后标题形态变好，但**候选数仍然只有 4~5
条**（提示词要求 20）。产物里只落盘最终候选，没有分阶段数据，所以无法判断究竟是

    A. 模型本来就只返回了几条
    B. `_dedupe_titles`（语义骨架去重）杀掉了大半
    C. `validate_title_compliance`（长度/禁用词/正文一致性）杀掉了大半

本脚本用**缓存里的真实 `data_report` + 真实正文**重建一次 TitleAgent 调用，逐阶段计数。

- 只读 + **单次** LLM 调用（走 `.env`，即本地当前的 `OPENAI_TITLE_MODEL`）。
- 不改动任何生产代码：全部靠实例级包装（monkeypatch）取数。
- 顺带验证一个结构性不对称：兜底标题调用 `validate_title_compliance(title)` 时
  **不传正文**，因此不过「正文一致性」这道最严的闸。

用法：
    .venv/Scripts/python.exe scripts/diagnose_title_stage.py
    .venv/Scripts/python.exe scripts/diagnose_title_stage.py --cache cache/xxxx.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.toutiao_content_factory.agents.title_agent import TitleAgent  # noqa: E402
from src.toutiao_content_factory.config import load_settings  # noqa: E402
from src.toutiao_content_factory.models import (  # noqa: E402
    CacheContext,
    DataReport,
    GeneratedContent,
    TrendReport,
)
from src.toutiao_content_factory.services.openai_service import (  # noqa: E402
    OpenAIContentService,
)
from src.toutiao_content_factory.services.prompt_repository import (  # noqa: E402
    PromptRepository,
)

ACCOUNT_KEY = "robot_notes"


def _from_dict(cls, payload: dict):
    """只取该 dataclass 认识的字段，避免历史产物里的多余键炸掉构造。"""
    if not isinstance(payload, dict):
        return cls()
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in payload.items() if k in known})


def _compliance_breakdown(agent: TitleAgent, title: str, body_text: str) -> list[str]:
    """逐条复算 `validate_title_compliance` 的各道子闸，指出是被哪一条杀掉的。"""
    from src.toutiao_content_factory.services.article_quality_checker import (
        ArticleQualityChecker,
    )

    reasons: list[str] = []
    max_length = agent._title_max_length()
    if len(title) > max_length:
        reasons.append(f"超长({len(title)}>{max_length})")
    for pattern in agent.DISCOURAGED_TITLE_PATTERNS:
        if pattern in title:
            reasons.append(f"禁用词:{pattern}")
            break
    if any(p.search(title) for p in agent.UNSUPPORTED_FINANCIAL_LINK_PATTERNS):
        reasons.append("无依据的财务因果")
    if ArticleQualityChecker().check_editorial_integrity(title, body_text).get("issues"):
        reasons.append("编辑一致性")
    if body_text and 10 <= len(title) <= max_length and not agent._has_concrete_hook(title, body_text):
        reasons.append("缺正文具象钩子")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="标题阶段归因诊断")
    parser.add_argument("--cache", default="", help="指定缓存文件；默认取最近修改的一个")
    parser.add_argument("--json", default="", help="把结构化结果写到该路径")
    args = parser.parse_args()

    cache_dir = PROJECT_ROOT / "cache"
    if args.cache:
        cache_path = Path(args.cache)
    else:
        # index.json 等台账文件不是运行缓存，只挑「有 data_report + 有正文」的字典型缓存
        usable: list[Path] = []
        for path in cache_dir.glob("*.json"):
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(blob, dict):
                continue
            content = blob.get("content") or {}
            if isinstance(content, dict) and (content.get("article") or content.get("polished_article")):
                usable.append(path)
        if not usable:
            print("cache/ 里没有带正文的运行缓存，用 --cache 指定")
            return 1
        # 优先挑机器人赛道的选题——本账号只发这个垂类，别的赛道结论不可外推
        robotish: list[Path] = []
        for path in usable:
            blob = json.loads(path.read_text(encoding="utf-8"))
            topic = str((blob.get("data_report") or {}).get("topic_title") or "")
            if any(k in topic for k in ("机器人", "人形", "Optimus", "具身")):
                robotish.append(path)
        pool = robotish or usable
        cache_path = max(pool, key=lambda p: p.stat().st_mtime)

    payload_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    print(f"缓存文件: {cache_path.name}")

    settings = load_settings(PROJECT_ROOT, ACCOUNT_KEY)
    logger = logging.getLogger("title-stage-diagnose")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    data_report = _from_dict(DataReport, payload_cache.get("data_report") or {})
    content = _from_dict(GeneratedContent, payload_cache.get("content") or {})
    trend_report = TrendReport.from_dict(payload_cache.get("trend_report") or {})
    style_key = str(payload_cache.get("style_key") or settings.default_style)
    style = settings.style_profiles.get(style_key)
    if style is None:
        print(f"缓存里的 style_key={style_key!r} 在配置中不存在，改用默认风格")
        style = settings.style_profiles[settings.default_style]

    if not content.article and not content.polished_article:
        print("该缓存没有正文，换一个 --cache")
        return 1

    print(f"选题: {data_report.topic_title}")
    print(f"正文: {len(content.polished_article or content.article)} 字")
    import os as _os

    print(f"标题模型: {_os.getenv('OPENAI_TITLE_MODEL', '').strip() or '(未设置，沿用正文模型)'}")

    agent = TitleAgent(
        settings,
        PromptRepository(settings.prompts_dir),
        OpenAIContentService(settings, logger),
        logger,
    )

    # ---- 实例级取数包装（不动生产代码）----
    captured: dict = {}
    dedupe_calls: list[dict] = []
    compliance_calls: list[dict] = []
    fallback_calls: list[list[str]] = []

    original_request_json = agent.request_json
    original_dedupe = agent._dedupe_titles
    original_validate = agent.validate_title_compliance
    original_fallback = agent._fallback_titles

    def spy_request_json(system_prompt: str, user_prompt: str):
        result = original_request_json(system_prompt, user_prompt)
        captured["payload"] = result
        return result

    def spy_dedupe(payload, cache_context):
        out = original_dedupe(payload, cache_context)
        raw = [str(i).strip() for i in payload if str(i).strip()] if isinstance(payload, list) else []
        kept = set(out)
        dedupe_calls.append(
            {
                "in": len(raw),
                "out": len(out),
                "dropped": [t for t in raw if t not in kept],
            }
        )
        return out

    def spy_validate(title: str, body_text: str = ""):
        ok = original_validate(title, body_text)
        compliance_calls.append({"title": title, "body_len": len(body_text), "ok": ok})
        return ok

    def spy_fallback(*a, **kw):
        out = original_fallback(*a, **kw)
        fallback_calls.append(list(out))
        return out

    agent.request_json = spy_request_json  # type: ignore[method-assign]
    agent._dedupe_titles = spy_dedupe  # type: ignore[method-assign]
    agent.validate_title_compliance = spy_validate  # type: ignore[method-assign]
    agent._fallback_titles = spy_fallback  # type: ignore[method-assign]

    result = agent.run(content, trend_report, data_report, style, CacheContext())

    # ---- 报告 ----
    raw_model = (captured.get("payload") or {}).get("titles") or []
    raw_recommended = (captured.get("payload") or {}).get("recommended_titles") or []
    body_reference = agent._title_body_reference(content)

    print("\n=== 分阶段计数 ===")
    print(f"0. 模型原始 titles            : {len(raw_model)}（提示词要求 20）")
    print(f"   模型原始 recommended_titles: {len(raw_recommended)}")
    for i, call in enumerate(dedupe_calls, 1):
        print(f"{i}. 去重 {call['in']} → {call['out']}（杀掉 {call['in'] - call['out']}）")
    if fallback_calls:
        print(f"{len(dedupe_calls) + 1}. 兜底标题产出            : {len(fallback_calls[0])}")

    model_side = [c for c in compliance_calls if c["body_len"] > 0]
    fallback_side = [c for c in compliance_calls if c["body_len"] == 0]
    print(f"\n=== 合规校验（共 {len(compliance_calls)} 次调用）===")
    print(f"  带正文（模型候选）: {len(model_side)} 次，通过 {sum(1 for c in model_side if c['ok'])}")
    print(f"  不带正文（兜底）  : {len(fallback_side)} 次，通过 {sum(1 for c in fallback_side if c['ok'])}")
    print("  ⚠️ 兜底不过「正文具象钩子」与「编辑一致性」两道闸 —— 结构性不对称")

    print("\n=== 模型候选被杀原因 ===")
    for title in raw_model:
        cleaned = agent._sanitize_title(str(title).strip())
        reasons = _compliance_breakdown(agent, cleaned, body_reference)
        status = "PASS" if not reasons else "FAIL " + "/".join(reasons)
        print(f"  [{status}] {cleaned}")

    print("\n=== 最终结果 ===")
    print(f"  titles            : {len(result['titles'])}")
    print(f"  recommended_titles: {result['recommended_titles']}")
    for i, t in enumerate(result["titles"], 1):
        print(f"   {i}. {t}")

    if args.json:
        out = {
            "cache_file": cache_path.name,
            "topic": data_report.topic_title,
            "raw_model_titles": raw_model,
            "dedupe_calls": dedupe_calls,
            "fallback_titles": fallback_calls[0] if fallback_calls else [],
            "compliance_model_side": model_side,
            "compliance_fallback_side": fallback_side,
            "final_titles": result["titles"],
            "recommended": result["recommended_titles"],
        }
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结构化结果已写入: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
