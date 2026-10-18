"""Editorial choices and source-bound writing briefs, never factual approval."""
from __future__ import annotations

import re


STORY_TRACKS = {
    "家庭财产": ("赠房", "赠与", "继承", "遗产", "过户", "拆迁", "宅基地", "房产", "分家", "借钱", "借条", "还钱"),
    "养老照护": ("赡养", "养老院", "护工", "照护", "护理费", "陪诊"),
    "劳动权益": ("工资", "欠薪", "劳动合同", "加班", "辞退", "开除", "裁员", "调休", "请假", "工伤", "劳动争议"),
    "消费权益": ("物业费", "停车费", "停车账", "停车费账本", "退款", "退费", "退货", "押金", "预付", "租金", "租房", "保修", "消费者", "消费纠纷"),
}
STORY_QUESTIONS = {
    "家庭财产": "本案处理的是谁的财产、哪一部分，已披露的约定怎样影响处理范围？",
    "养老照护": "本案约定了哪些照护责任或收费，实际履行与约定有哪些可核对的差别？",
    "劳动权益": "本案的具体安排与处理结果是什么，哪些条件决定了争议范围？",
    "消费权益": "费用或承诺对应什么服务，实际交付和已确认的处理结果是什么？",
}


def story_reader_fit(topic) -> dict:
    # Collector tags and unrelated source snippets cannot make a topic relevant.
    text = f"{topic.title} {topic.summary}"
    def relevant_marker(word):
        if word not in text:
            return False
        if word == "继承":
            return bool(re.search(r"遗产|遗嘱|继承权|继承人|继承纠纷|法定继承|房产|房屋|存款|股权|财产|遗赠", text))
        if word == "加班":
            return bool(re.search(r"员工|职工|工人|劳动者|雇员|打工|劳动|用工|加班费|考勤|薪资|薪酬|强制加班|公司(?:安排|要求)", text))
        return True
    tracks = [name for name, markers in STORY_TRACKS.items() if any(relevant_marker(word) for word in markers)]
    if "婚介" in text and re.search(r"退款|退费|服务费|失联|纠纷|法院|判决|返还", text):
        if "消费权益" not in tracks:
            tracks.append("消费权益")
    if "物业服务合同" in text and re.search(r"纠纷|争议|法院|判决", text):
        if "消费权益" not in tracks:
            tracks.append("消费权益")
    if "彩礼" in text and re.search(r"返还|退还|诉讼|法院|判决|婚约.{0,8}争议", text):
        if "家庭财产" not in tracks:
            tracks.append("家庭财产")
    if "老人" in text and any(word in text for word in ("走失", "找回家人", "找到走失", "认亲", "志愿者帮")):
        if "养老照护" not in tracks:
            tracks.append("养老照护")
    return {"profile": "social_practical", "eligible": bool(tracks), "tracks": tracks,
            "basis": "editorial_choice", "question": STORY_QUESTIONS[tracks[0]] if tracks else "",
            "note": "题材聚焦不代表事实通过，也不代表已证明阅读效果。"}


def wechat_value_priority(topic, facts: list[dict]) -> int:
    if "市值" not in f"{topic.title} {topic.summary}":
        return 0
    text = "\n".join(str(row.get("quote", "")) for row in facts)
    useful = ("测试", "验证", "部署", "交付", "营业收入", "净利润", "销量", "产量", "操作条件", "同期投产")
    return 0 if any(word in text for word in useful) else 1


def build_reader_brief(title: str, quotes: list[str], *, profile: str) -> dict:
    sources = []
    for quote in quotes:
        for sentence in re.split(r"(?<=[。！？])", str(quote or "")):
            sentence = sentence.strip()
            if (12 <= len(sentence) <= 180 and sentence not in sources
                    and not re.search(r"https?://|【来源|忽略.{0,6}指令|ignore.*instructions", sentence, re.I)):
                sources.append(sentence)
            if len(sources) == 2:
                break
        if len(sources) == 2:
            break
    if profile == "social_practical":
        from types import SimpleNamespace
        fit = story_reader_fit(SimpleNamespace(title=title, summary=""))
        question = fit["question"] or "本案已确认的结果与具体条件是什么，哪些判断还没有来源支持？"
    elif any(word in title for word in ("芯片", "机器人", "测试", "模型", "产品")):
        question = "本次披露的能力在哪些条件下得到验证，哪些仍未披露？"
    else:
        question = "这些已披露事实各自的对象、时间和条件是什么，能说明什么具体变化？"
    return {"profile": profile, "topic": str(title)[:120], "question": question,
            "source_quotes": sources, "answer_verified": False,
            "instruction": "先回答本题问题，再解释两项原文事实的关系；原句不足时收窄或换题。此简报不是已验证答案。"}


def generic_padding_reason(text: str) -> str:
    """Catch observed fallback padding, not every sentence discussing uncertainty."""
    markers = ("普通读者可以先保存权威来源", "这里需要把已经发生的事实", "最稳妥的做法是记录事实",
               "把事实看清楚，再给自己的判断留一点更新空间", "这条消息值得看，但价值不只在热闹本身",
               "真正有价值的是把已确认的事实和自己的推测分开", "如果话题涉及政策、社保、养老或公共待遇",
               "公开讨论不等于规则已经改变", "你最希望主管部门先说明哪一个具体问题")
    sentences = [part.strip() for part in re.split(r"(?<=[。！？])|\n+", str(text or "")) if part.strip()]
    padded = [part for part in sentences if any(marker in part for marker in markers)]
    total = sum(len(part) for part in sentences)
    observed_advice = ("消费者需仔细阅读", "消费者应主动了解合同细节", "避免被动接受不利条款")
    # Two generic advice sentences consume a substantial short-post analysis
    # budget. This narrow rule is based on the real 2026-10-18 draft, not a
    # blanket prohibition on discussing consumer rights or uncertainty.
    if total <= 600 and sum(any(marker in part for marker in observed_advice) for part in sentences) >= 2:
        return "known_generic_padding"
    return "known_generic_padding" if len(padded) >= 2 and sum(map(len, padded)) >= total * .35 else ""


def grounded_summary(summary: str, article: str) -> str:
    """Use complete paragraph context; never truncate a following qualification."""
    body = str(article or "").split("参考来源：", 1)[0]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()
                  and not part.strip().startswith(("#", "![", "http://", "https://"))]
    if not paragraphs:
        return ""
    first = paragraphs[0]
    if not 12 <= len(first) <= 120:
        return ""
    if generic_padding_reason(first):
        return ""
    # A model paraphrase is not independent evidence for a draft-card claim.
    # The complete first paragraph keeps reported claims and their caveats together.
    return str(summary).strip() if str(summary).strip() == first else first
