"""检查提示词领域措辞：叙事号不得再看到「科技/产业」的自我定位。

背景：提示词里写死过「你是科技产业方向的资深编辑」「标题要像头条科技分析号」，
导致社会故事账号产出的标题被硬套进科技框架（实测标题出现「科技行业……」）。
本脚本对每个提示词分别在 industry / narrative 两种领域下渲染，报告残留。

用法：python scripts/check_prompt_domain_wording.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services.prompt_repository import PromptRepository  # noqa: E402

PROMPTS_DIR = ROOT / "prompts"
# 只检查会被实际调用的提示词；image_prompt 已停用。
TEMPLATES = [
    "analysis_prompt.md",
    "article_prompt.md",
    "data_prompt.md",
    "data_prompt_v2.md",
    "editorial_council_prompt.md",
    "generation_prompt.md",
    "polish_prompt.md",
    "publish_review_prompt.md",
    "review_prompt.md",
    "title_prompt.md",
    "title_prompt_v2.md",
    "trend_prompt.md",
    "trend_prompt_v2.md",
]
# 叙事号出现这些词就说明定位还是科技号。
NARRATIVE_FORBIDDEN = ["科技产业", "科技行业", "科技分析", "产业分析", "科技新闻", "科技内容"]
# 这些是「示例数据」，允许保留：它们示范结构，不是自我定位。
EXAMPLE_ALLOWLIST = ["宇树科技", "机器人", "产业链"]


def main() -> int:
    repo = PromptRepository(PROMPTS_DIR)
    kwargs = {
        "platform": "今日头条",
        "brand_name": "手蓝人间故事",
        "account_name": "手蓝人间故事",
    }
    failures: list[str] = []

    for name in TEMPLATES:
        if not (PROMPTS_DIR / name).exists():
            failures.append(f"{name}: 文件不存在")
            continue
        narrative = repo.render(name, content_domain="narrative", **kwargs)
        industry = repo.render(name, content_domain="industry", **kwargs)

        hits = [word for word in NARRATIVE_FORBIDDEN if word in narrative]
        # 示例区里的领域词不算自我定位，从命中里剔除仅当该行含示例锚点
        real_hits: list[str] = []
        for word in hits:
            for line in narrative.splitlines():
                if word not in line:
                    continue
                if any(anchor in line for anchor in EXAMPLE_ALLOWLIST):
                    continue
                real_hits.append(word)
                break

        if real_hits:
            failures.append(f"{name}: 叙事号残留 {sorted(set(real_hits))}")
            for line in narrative.splitlines():
                if any(word in line for word in real_hits):
                    failures.append(f"    | {line.strip()[:100]}")
        else:
            print(f"[OK] {name}")

        # industry 域不应被改动
        if "科技产业" not in industry and "科技行业" not in industry:
            marker = name.replace(".md", "")
            # 有些模板本来就不含科技定位，只在原本含有的模板上校验
            if any(
                w in (PROMPTS_DIR / name).read_text(encoding="utf-8")
                for w in ("科技产业", "科技行业")
            ):
                failures.append(f"{name}: industry 域被误伤（科技定位被替换掉了）")

    print()
    if failures:
        print("=== 失败 ===")
        for item in failures:
            print(item)
        return 1
    print("全部提示词在 narrative 域下已无科技自我定位，且 industry 域未被误伤。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
