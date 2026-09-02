from __future__ import annotations

import re


# These are common public-facing company names that do not carry an obvious
# Chinese corporate suffix.  A short CJK string is not accepted by itself:
# doing so turns places, industries, and source labels into companies.
KNOWN_COMPANY_NAMES = frozenset(
    {
        "特斯拉",
        "比亚迪",
        "华为",
        "宁德时代",
        "小米",
        "腾讯",
        "百度",
        "阿里巴巴",
        "阿里",
        "京东",
        "苹果",
        "英伟达",
        "英特尔",
        "微软",
        "谷歌",
        "亚马逊",
        "富士康",
        "优必选",
        "宇树",
        "智元",
        "波士顿动力",
        "小鹏",
        "蔚来",
        "理想",
        "零跑",
        "吉利",
        "长城汽车",
        "赛力斯",
        "大疆",
        "海康威视",
        "科大讯飞",
        "中芯国际",
        "寒武纪",
        "长江存储",
        "地平线",
        "黑芝麻智能",
        "文远知行",
        "小马智行",
        "商汤",
        "字节跳动",
        "美团",
        "联想",
        "西门子",
        "博世",
        "丰田",
        "宝马",
        "奔驰",
        "大众",
        "本田",
        "松下",
        "三星",
        "索尼",
        "施耐德",
        "OpenAI",
        "Anthropic",
        "Tesla",
        "NVIDIA",
        "Apple",
        "Google",
        "Microsoft",
        "Amazon",
        "ABB",
        "KUKA",
    }
)

# These aliases are also ordinary words or verbs in Chinese.  A nearby
# business noun or action is required before extracting them from free text;
# direct validation still accepts the aliases when an upstream field already
# identifies them explicitly.
AMBIGUOUS_KNOWN_COMPANY_NAMES = frozenset(
    {
        "理想",
        "大众",
        "苹果",
        "奔驰",
        "吉利",
        "小米",
    }
)
KNOWN_COMPANY_CONTEXT_MARKERS = (
    "公司",
    "集团",
    "汽车",
    "车型",
    "品牌",
    "产品",
    "业务",
    "财报",
    "营收",
    "收入",
    "订单",
    "交付",
    "出货",
    "销量",
    "工厂",
    "产线",
    "官网",
    "招聘",
    "电池",
    "芯片",
    "手机",
    "机器人",
    "上市",
    "量产",
    "销售",
    "制造",
)
KNOWN_COMPANY_ACTION_MARKERS = (
    "发布",
    "宣布",
    "合作",
    "采购",
    "供应",
    "融资",
    "投资",
    "股价",
    "股东",
    "创始人",
    "下单",
)

GENERIC_COMPANY_EXCLUSIONS = frozenset(
    {
        "人形机器人",
        "机器人",
        "具身智能",
        "具身智能公司",
        "AI机器人",
        "AI硬件",
        "储能",
        "新能源",
        "新能源车",
        "智能装备",
        "产业链",
        "系统集成",
        "热管理",
        "机器视觉",
        "核心零部件",
        "关键零部件",
        "电池材料",
        "正极材料",
        "负极材料",
        "上游材料",
        "核心材料",
        "原材料",
        "材料端",
        "智能汽车",
        "新能源汽车",
        "电动汽车",
        "汽车产业",
        "国内人形机器人",
        "柏林工厂",
        "汽车工厂",
        "真实产线",
        "实验室",
        "公开材料",
        "公开报道",
        "素材信息",
        "德国汽车",
        "据德国汽车",
        "涉及哪些公司",
        "哪些公司",
        "公司主体",
        "项目主体",
        "订单主体",
        "关键企业",
        "相关企业",
        "新能源与机器人",
        "机器人行业",
        "机器人产业",
        "储能行业",
        "新能源行业",
        "新能源产业",
        "工业自动化",
        "机器人公司",
        "人形机器人公司",
        "AI机器人公司",
        "智能机器人",
        "其他智能",
        "某智能",
        "其他智能机器人",
        "其他机器人",
    }
)

COMPANY_STOP_WORDS = frozenset(
    {
        "这家公司",
        "这类公司",
        "相关公司",
        "头部企业",
        "主流新能源",
        "一众主流新能源",
        "国内新能源",
        "海外新能源",
        "整车企业",
        "车企",
        "企业",
        "公司",
        "客户",
        "项目方",
        "供应商",
        "品牌",
        "产业链",
        "行业",
        "市场",
        "赛道",
        "厂商",
    }
)

COMPANY_PREFIX_NOISE = (
    "这家",
    "这类",
    "相关",
    "头部",
    "一众",
    "主流",
    "国内",
    "海外",
    "而",
    "某",
    "该",
    "再",
    "曾经",
    "据",
    "根据",
    "来自",
    "涉及",
    "包括",
    "其中",
    "关于",
    "目前",
    "如果",
    "能否",
    "哪些",
    "什么",
    # Editorial instructions can end with a token that looks like a company
    # suffix, for example "重点看电池".  They are not company entities.
    "重点看",
    "重点关注",
    "优先看",
    "先看",
    "关注",
)

COMPANY_CONTEXT_NOISE = (
    "公开材料",
    "公开报道",
    "来源",
    "原文",
    "摘要",
    "素材",
    "媒体",
    "报道",
    "发布日期",
    "链接",
    "数据",
    "趋势",
    "原因",
    "结论",
    "价格战",
    "订单",
    "交付",
    "风险",
    "工厂",
    "产线",
    "实验室",
    "工位",
    "车间",
    "仓库",
    "园区",
    "电站",
    "电厂",
    "机场",
    "现场",
    "哪些",
    "涉及",
    "是什么",
    "如何",
    "为什么",
    "是否",
    "能否",
    "以及",
    "包括",
    "其中",
    "不是",
    "而是",
    "上半年",
    "下半年",
    "本年度",
    "同比",
    "环比",
    "销量",
    "收入",
    "利润",
    "统计",
    "数据",
)
COMPANY_GENERIC_SUBSTRINGS = (
    "新能源",
    "储能",
    "人形机器人",
    "具身智能",
    "智能机器人",
    "机器人行业",
    "机器人产业",
    "工业自动化",
    "AI机器人",
    "AI硬件",
    "产业链",
    "系统集成",
    "机器视觉",
    "热管理",
    "核心零部件",
    "关键零部件",
    "增长",
    "驱动力",
    "行业",
    "定制",
    "解决方案",
)

# Model extraction sometimes returns a clause or a product classification as
# a company because it happens to end with a company-like suffix (for example
# "为最主要的增长驱动力" or "其他行业定制智能机器人及智能机器人").
COMPANY_SEMANTIC_PREFIX_NOISE = ("为", "其他")
COMPANY_SEMANTIC_JOINER_NOISE = ("及",)

COMPANY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "公司",
    "集团",
    "科技",
    "能源",
    "汽车",
    "股份",
    "电气",
    "智能",
    "机器人",
    "半导体",
    "电子",
    "时代",
    "精工",
    "制造",
    "材料",
    "电池",
    "重工",
    "工业",
    "光伏",
    "储能",
    "动力",
    "机电",
    "控股",
    "装备",
    "技术",
    "银行",
    "证券",
)

COMPANY_SUFFIX_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{2,24}(?:"
    + "|".join(re.escape(item) for item in COMPANY_SUFFIXES)
    + r")"
)
COMPANY_ALLOWED_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·&.()（）\- ]+$")
COMPANY_CANONICAL_TAILS = (
    "公司",
    "集团",
    "科技",
    "能源",
    "汽车",
    "股份",
    "技术",
    "机器人",
    "人形",
    "Optimus",
    "汽车工厂",
    "工厂",
    "产线",
)


def normalize_company_name(value: object) -> str:
    """Return a short public company name when a candidate contains a brand."""

    candidate = re.sub(r"\s+", "", str(value or "").strip("，。；：:、\"'“”‘’()（）"))
    if not candidate:
        return ""

    for alias in sorted(KNOWN_COMPANY_NAMES, key=len, reverse=True):
        if candidate == alias:
            return alias
        if not candidate.startswith(alias):
            continue
        tail = candidate[len(alias) :]
        if tail and any(marker in tail for marker in COMPANY_CANONICAL_TAILS):
            return alias
    return candidate


def is_valid_company_name(value: object) -> bool:
    candidate = normalize_company_name(value)
    if not candidate or len(candidate) < 2 or len(candidate) > 24:
        return False
    if not COMPANY_ALLOWED_RE.fullmatch(candidate):
        return False
    if candidate in KNOWN_COMPANY_NAMES:
        return True
    if candidate in GENERIC_COMPANY_EXCLUSIONS or candidate in COMPANY_STOP_WORDS:
        return False
    if any(marker in candidate for marker in COMPANY_GENERIC_SUBSTRINGS):
        return False
    if any(candidate.startswith(prefix) for prefix in COMPANY_SEMANTIC_PREFIX_NOISE):
        return False
    if any(
        joiner in candidate and any(marker in candidate for marker in ("产品", "服务", "方案", "机器人"))
        for joiner in COMPANY_SEMANTIC_JOINER_NOISE
    ):
        return False
    if any(candidate.startswith(prefix) for prefix in COMPANY_PREFIX_NOISE):
        return False
    if any(token in candidate for token in COMPANY_CONTEXT_NOISE):
        return False
    if re.search(r"[。！？；：:#]", candidate):
        return False
    return bool(COMPANY_SUFFIX_RE.fullmatch(candidate))


def extract_known_company_names(text: object) -> list[str]:
    """Find only explicitly known no-suffix company aliases in arbitrary text."""

    # URLs frequently contain a company slug. They are source metadata, not
    # evidence that the company is mentioned in the article text.
    raw_text = re.sub(r"https?://\S+", " ", str(text or ""), flags=re.IGNORECASE)
    folded = raw_text.casefold()
    matches: list[tuple[int, int, str]] = []
    for alias in KNOWN_COMPANY_NAMES:
        position = folded.find(alias.casefold())
        if position < 0:
            continue
        if alias in AMBIGUOUS_KNOWN_COMPANY_NAMES:
            left = raw_text[max(0, position - 8) : position]
            right = raw_text[position + len(alias) : position + len(alias) + 12]
            has_adjacent_context = any(
                marker in left[-4:] or 0 <= right.find(marker) <= 3
                for marker in KNOWN_COMPANY_CONTEXT_MARKERS
            )
            has_business_action = any(marker in right[:10] for marker in KNOWN_COMPANY_ACTION_MARKERS)
            if not has_adjacent_context and not has_business_action:
                continue
        matches.append((position, -len(alias), alias))
    result: list[str] = []
    for _position, _length, alias in sorted(matches):
        if any(alias != previous and alias.casefold() in previous.casefold() for previous in result):
            continue
        result.append(alias)
    return result
