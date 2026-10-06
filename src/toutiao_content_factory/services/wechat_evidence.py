"""Source-bound evidence and editorial requirements for WeChat auto runs.

Legacy attribution strings intentionally do not produce verified facts. The raw
source bodies stay in the private working directory, never in cloud diagnostics.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

KINDS = {
    "policy_explainer": ("政策解读", "解释已公布措施、作用环节和适用边界", "实施条件、适用范围、执行进展", "哪些适用条件还需要进一步明确？"),
    "product_progress": ("产品进展", "解释已披露能力、限制和验证情况，区分演示与实际部署", "验证条件、能力边界、实际部署进展", "你最希望看到哪项能力的独立验证？"),
    "company_earnings": ("企业业绩", "围绕明确企业及原文财务数据解释变化，不外推未披露订单", "原文披露的财务指标及其统计期", "哪项已披露指标最值得继续跟踪？"),
    "industry_update": ("行业动态", "解释已发生的变化、已有证据与尚未明确的边界", "技术进展、应用条件、后续公开披露", "哪个应用条件最值得继续观察？"),
}
NUMBER = re.compile(r"(?<![A-Za-z\d])\d+(?:\.\d+)?\s*(?:万亿|亿|万|千|百)?\s*(?:千瓦时|千瓦|兆瓦|吉瓦|美元|人民币|亿元|万元|元|%|％|倍|台|套|家|项|吨|公里|小时|分钟|秒|人|片|个|GB|GW|MW|kWh|kW)?", re.I)
PERIOD = re.compile(r"(?:截至(?:\d{4}年)?\d{1,2}月(?:\d{1,2}日|底|末)?|\d{4}年(?:\d{1,2}月(?:至\d{1,2}月)?|[上下]半年|[一二三四1234]季度)?|(?:今年|去年)?(?:[上下]半年|[一二三四1234]季度))")
FINANCIAL = re.compile(r"营业收入|净利润|毛利率|财报|扣非|营收|经营现金流")
BUSINESS_CLAIM = re.compile(r"利润|盈利|毛利|回款|合同验收|订单|客户复购")


def stable_id(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]


def public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        if parsed.path.startswith("/cgi-bin/"):
            return ""
        # Tracking and credentials are unnecessary to establish source identity.
        identifying = {"id", "article_id", "articleid", "docid", "itemid", "mid", "idx", "sn", "__biz"}
        query = urlencode(sorted((key, val) for key, val in parse_qsl(parsed.query) if key.lower() in identifying))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    except ValueError:
        return ""


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property", attrs.get("name", attrs.get("itemprop", ""))).lower()
            self.values[key] = attrs.get("content", "").strip()
        if tag == "time" and attrs.get("datetime"):
            self.values.setdefault("datepublished", attrs["datetime"])


def source_metadata(page_html: str) -> dict:
    """Read explicit publication metadata before body/noise filtering.

    A collection timestamp, copyright year, modified date, or date in article
    prose never substitutes for the page's publication date.
    """
    parser = _MetadataParser()
    parser.feed(page_html)
    values = parser.values
    raw_date = next((values[k] for k in ("article:published_time", "datepublished", "pubdate", "publishdate", "publish_date") if values.get(k)), "")
    published_at = ""
    if raw_date:
        try:
            published_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).isoformat()
        except ValueError:
            match = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", raw_date)
            if match:
                try:
                    published_at = datetime(*map(int, match.groups())).date().isoformat()
                except ValueError:
                    pass
    return {"publisher": values.get("article:publisher", values.get("og:site_name", "")),
            "published_at": published_at, "date_verified": bool(published_at),
            "page_title": values.get("og:title", "")}


def source_records(sources) -> list[dict]:
    records = []
    for source in sources:
        url = public_url(str(source.url or ""))
        # Search snippets and anonymous digests are not verified original prose.
        if not source.ok or source.evidence_type != "article" or not url or not source.body:
            continue
        records.append({"source_id": stable_id("src_", url), "title": source.title,
                        "url": url, "publisher": getattr(source, "publisher", ""),
                        "published_at": getattr(source, "published_at", "") if getattr(source, "date_verified", False) else "",
                        "date_verified": bool(getattr(source, "date_verified", False)),
                        "original_text": source.body})
    return records


def _normalize(text: str) -> str:
    return re.sub(r"[\s，,。；;：:、（）()“”\"'—-]+", "", text).lower().replace("％", "%")


def _numbers(text: str) -> list[dict]:
    result = []
    for match in NUMBER.finditer(text):
        token = _normalize(match.group())
        value = re.match(r"\d+(?:\.\d+)?", token).group()
        unit = token[len(value):]
        # Dates belong to the period field, not a performance measurement.
        if not unit and text[match.end():match.end()+1] in {"年", "月", "日"}:
            continue
        result.append({"value": value, "unit": unit})
    return result


def _subject(sentence: str) -> str:
    text = PERIOD.sub("", sentence)
    text = re.sub(r"^.*?(?:显示|指出|披露|表示|介绍)[，：:]?", "", text)
    head = re.split(r"达到|为|占|同比|增长|下降|超过|约|已|将|\d", text, maxsplit=1)[0]
    return re.sub(r"^[，,：:\s]+|[，,：:\s]+$", "", head)[-45:]


def _measurements(sentence: str) -> list[dict]:
    """Bind each number to its own clause, rather than pooling sentence values."""
    measurements = []
    previous_object = ""
    period = list(dict.fromkeys(PERIOD.findall(sentence)))
    for clause in re.split(r"[，,；;。\n]", sentence):
        subject = _subject(clause) or previous_object
        if subject:
            previous_object = subject
        metric = next((token for token in ("同比增长", "同比下降", "环比增长", "环比下降", "占比", "占", "增长", "下降") if token in clause), "")
        for number in _numbers(clause):
            measurements.append({**number, "statistical_object": subject,
                                 "statistical_period": period, "metric": metric})
    return measurements


def build_facts(records: list[dict]) -> list[dict]:
    facts, seen = [], set()
    for source in records:
        if not public_url(source.get("url", "")) or not source.get("original_text"):
            continue
        for sentence in re.split(r"(?<=[。！？；])|\n+", source["original_text"]):
            quote = sentence.strip()
            if len(quote) < 12 or len(quote) > 800:
                continue
            fact_id = stable_id("fact_", source["source_id"], _normalize(quote))
            if fact_id in seen:
                continue
            seen.add(fact_id)
            facts.append({"fact_id": fact_id, "source_id": source["source_id"], "quote": quote,
                          "numbers": _numbers(quote), "statistical_object": _subject(quote),
                          "measurements": _measurements(quote),
                          "statistical_period": list(dict.fromkeys(PERIOD.findall(quote)))})
    return facts


def classify_kind(topic_type: str, facts: list[dict]) -> str:
    prose = "\n".join(item["quote"] for item in facts)
    if topic_type == "earnings_signal" and any(
        FINANCIAL.search(item["quote"]) and item["numbers"]
        and re.search(r"[\u4e00-\u9fffA-Za-z]{2,}(?:公司|集团|股份)|(?:英伟达|特斯拉|比亚迪|宁德时代|苹果|微软)", item["quote"])
        for item in facts
    ):
        return "company_earnings"
    if topic_type == "policy_market" and re.search(r"通知|办法|政策|意见|措施|规定", prose):
        return "policy_explainer"
    if topic_type in {"product_launch", "project_progress"} and re.search(r"发布|推出|产品|测试|验证|部署|投产", prose):
        return "product_progress"
    return "industry_update"


def requirements(kind: str) -> dict:
    label, brief, watch, question = KINDS.get(kind, KINDS["industry_update"])
    return {"label": label, "suggested_angle": brief, "follow_up_focus": watch,
            "writing_brief": brief + "。所有数字逐项绑定原句、统计对象、单位和统计期；缺失信息保持未知。",
            "closing": question,
            "instruction": f"稿件类型：{label}。{brief}。跟踪：{watch}。结尾问题：{question}。没有企业财务或订单原句时，不生成利润兑现、回款、合同验收等断言。"}


def supporting_facts(sentence: str, facts: list[dict]) -> list[dict]:
    """Conservative context match; a shared numeric value is insufficient.

    Paraphrases still undergo the independent council review. This check only
    supplies traceable candidates, and never establishes a verdict on its own.
    """
    sentence = re.sub(r"^据《[^》]+》报道[，,]?", "", sentence.strip())
    normalized = _normalize(sentence)
    numbers = _numbers(sentence)
    periods = PERIOD.findall(sentence)
    measurements = _measurements(sentence)
    matches = []
    for fact in facts:
        quote = _normalize(fact["quote"])
        if any(number not in fact["numbers"] for number in numbers):
            continue
        if any(_normalize(period) not in quote for period in periods):
            continue
        if numbers and any(_normalize(period) not in normalized for period in fact["statistical_period"]):
            continue
        if normalized and normalized in quote:
            matches.append(fact)
            continue
        bound = fact.get("measurements", _measurements(fact["quote"]))
        if numbers and any(not any(
            item["value"] == candidate["value"] and item["unit"] == candidate["unit"]
            and _normalize(item["statistical_object"])
            and _normalize(item["statistical_object"]) in _normalize(candidate["statistical_object"])
            and (not item["metric"] or item["metric"] == candidate["metric"])
            for item in bound) for candidate in measurements):
            continue
        companies = re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}(?:公司|集团|股份)", sentence)
        if any(company not in fact["quote"] for company in companies):
            continue
        grams = {normalized[i:i+2] for i in range(len(normalized)-1)}
        qgrams = {quote[i:i+2] for i in range(len(quote)-1)}
        if len(grams & qgrams) >= 6 and len(grams & qgrams) / max(1, len(grams)) >= 0.55:
            matches.append(fact)
    return matches


def _claim_text(sentence: str) -> str:
    # Source titles can contain numbers, dates, and business terms. They are
    # attribution metadata, not measurements asserted by this sentence.
    return re.sub(r"^据《[^》]+》报道[，,]?", "", sentence.strip())


def _unsupported_claim(sentence: str, facts: list[dict]) -> bool:
    claim = _claim_text(sentence)
    business = BUSINESS_CLAIM.search(claim)
    if not _numbers(claim) and not business:
        return False
    matches = supporting_facts(claim, facts)
    if not matches:
        return True
    if business:
        # A source's general industry commentary is not company financial or
        # order evidence. Keep the same object-bound match as for other facts.
        company = r"[\u4e00-\u9fffA-Za-z]{2,}(?:公司|集团|股份)|英伟达|特斯拉|比亚迪|宁德时代|苹果|微软"
        financial = any(FINANCIAL.search(fact["quote"]) and re.search(company, fact["quote"])
                        and any(item["unit"] for item in fact["numbers"]) for fact in matches)
        order = any(re.search(company, fact["quote"])
                    and re.search(r"订单|合同", fact["quote"])
                    and re.search(r"签订|签署|获得|收到|交付|验收|披露", fact["quote"]) for fact in matches)
        if not financial and not order:
            return True
    return False


def _mobile_paragraphs(text: str, limit: int) -> list[str]:
    """Change only whitespace at existing punctuation, before full review."""
    chunks, current = [], ""
    for part in re.split(r"(?<=[。！？；])", text):
        if not part:
            continue
        if current and len(current) + len(part) > limit:
            chunks.append(current)
            current = ""
        current += part
    if current:
        chunks.append(current)
    return chunks


def unsupported_claims(article: str, facts: list[dict]) -> list[str]:
    if not facts:
        return []  # Old reports retain their existing independent gates.
    article = article.split("参考来源：", 1)[0]
    return [sentence.strip() for sentence in re.split(r"(?<=[。！？；])|\n+", article)
            if sentence.strip() and _unsupported_claim(sentence, facts)]


def prepare_article(article: str, report) -> str:
    """Remove unsupported measured claims and restore source-bound citations.

    Runs before title generation and complete-article review after every body
    change. We never attach a publisher to a claim without matching evidence.
    """
    if not report.fact_evidence or not report.source_records:
        return article
    sources = {item["source_id"]: item for item in report.source_records}
    body = article.split("参考来源：", 1)[0].strip()
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", body):
        sentences = [part.strip() for part in re.split(r"(?<=[。！？；])", paragraph) if part.strip()]
        kept, source_ids = [], []
        for sentence in sentences:
            sentence = _claim_text(sentence)
            matches = supporting_facts(sentence, report.fact_evidence)
            if report.article_kind == "policy_explainer":
                prose = "\n".join(fact["quote"] for fact in report.fact_evidence)
                if any(token in sentence and token not in prose for token in
                       ("全球", "世界各国", "全球趋势", "成本构成", "投资收益", "收益率")):
                    continue
            if _unsupported_claim(sentence, report.fact_evidence):
                continue
            kept.append(sentence)
            source_ids.extend(fact["source_id"] for fact in matches)
        if not kept:
            continue
        citation_size = max((len(f"据《{sources[sid]['title']}》报道，")
                             for sid in source_ids if sid in sources), default=0)
        for text in _mobile_paragraphs("".join(kept), max(1, 180 - citation_size)):
            chunk_sources = [fact["source_id"] for sentence in re.split(r"(?<=[。！？；])", text)
                             for fact in supporting_facts(sentence, report.fact_evidence)]
            if chunk_sources:
                source = sources.get(chunk_sources[0], {})
                name = source.get("publisher") or source.get("title")
                if name and name not in text:
                    text = f"据《{source['title']}》报道，" + text
            paragraphs.append(text)
    references = []
    for source in report.source_records:
        date = source.get("published_at") or "发布日期未核实"
        publisher = source.get("publisher") or "发布机构未标注"
        references.append(f"《{source['title']}》；{publisher}；{date}；{source['url']}")
    return "\n\n".join(paragraphs) + "\n\n参考来源：\n" + "\n".join(references)


def effective_body_chars(article: str) -> int:
    body = article.split("参考来源：", 1)[0]
    body = re.sub(r"据《[^》]+》报道[，,]?", "", body)
    body = re.sub(r"https?://\S+", "", body)
    return len(re.sub(r"\s+", "", body))


def build_outline(report, retry_context: dict | None = None) -> None:
    """Bind each section to existing facts; never manufacture missing analysis."""
    import hashlib
    import json

    context = retry_context or {}
    report.source_fingerprint = hashlib.sha256(json.dumps(report.fact_evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    matching = [item for item in context.get("failed_inputs", []) if item.get("source_fingerprint") == report.source_fingerprint]
    report.regeneration_requirements = list(dict.fromkeys(reason for item in matching
        for reason in item.get("requirements", [])))[:12] or list(context.get("requirements", []))[:12]
    labels = {
        "policy_explainer": ["发生了什么变化", "措施及作用环节", "适用地区和项目类型", "原文规定的实施条件", "边界及未披露信息", "后续核对变量"],
        "product_progress": ["产品及披露进展", "已披露能力", "能力的作用环节", "验证条件与结果", "限制及未验证范围", "后续核对变量"],
        "company_earnings": ["企业及统计期", "原文财务数据", "业务分类与变化", "数据可比范围", "披露限制", "后续核对变量"],
    }.get(report.article_kind, ["事件及主体", "已披露进展", "相关技术环节", "事实之间的关系", "证据边界", "后续核对变量"])
    facts = list(report.fact_evidence)
    # A relay starts from the issues rather than regenerating the same brief.
    if report.regeneration_requirements and len(facts) > 1:
        facts = facts[1:] + facts[:1]
    report.writing_outline = [{"section": label, "fact_ids": [item["fact_id"] for item in facts[i::len(labels)]],
                               "target_chars": 350} for i, label in enumerate(labels)]
    # Sections with no distinct fact can explain boundaries of an existing fact.
    for section in report.writing_outline:
        if not section["fact_ids"] and facts:
            section["fact_ids"] = [facts[0]["fact_id"]]
    payload = {"facts": report.fact_evidence, "kind": report.article_kind,
               "outline": report.writing_outline, "requirements": report.regeneration_requirements}
    report.draft_fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if report.draft_fingerprint in context.get("failed_fingerprints", []):
        from toutiao_content_factory.exceptions import LowQualityAbortException
        raise LowQualityAbortException("同一素材、大纲和要求已失败，换题。")
    report.word_target = 2100
    report.writing_brief += " 正文目标2000～2200字，最低1750字；按writing_outline逐节展开，每节仅使用绑定证据。参考来源、网址和重复引用不计篇幅。"
    if report.regeneration_requirements:
        report.writing_brief += " 本轮重点：" + "；".join(report.regeneration_requirements)
    report.format_brief = report.writing_brief


def apply_requirements(report, extraction, trend_report=None) -> None:
    report.source_records = extraction.source_records
    report.fact_evidence = extraction.fact_evidence
    report.article_kind = extraction.article_kind or "industry_update"
    brief = requirements(report.article_kind)
    for field in ("suggested_angle", "follow_up_focus", "writing_brief"):
        setattr(report, field, brief[field])
    report.format_brief = brief["writing_brief"]
    report.paragraph_instruction = brief["instruction"] + "每段不超过180字；保持原句的统计对象、单位与统计期完整。"
    report.watch_metrics = brief["follow_up_focus"].split("、")
    if trend_report is not None:
        trend_report.recommended_angle = brief["suggested_angle"]
        for field in ("hot_trends", "audience_hooks", "competitive_edges"):
            setattr(trend_report, field, [item for item in getattr(trend_report, field)
                                         if not unsupported_claims(item, report.fact_evidence)])
    report.source_facts = [fact["quote"] for fact in report.fact_evidence] or report.source_facts
    if report.fact_evidence:
        report.source_references = [f"《{source['title']}》；{source.get('publisher') or '发布机构未标注'}；{source.get('published_at') or '发布日期未核实'}；{source['url']}"
                                    for source in report.source_records]
        prose = "\n".join(fact["quote"] for fact in report.fact_evidence)
        report.companies = [name for name in report.companies if name in prose]
        report.focus_companies = [name for name in report.focus_companies if name in prose]
        for field in ("core_data", "growth_numbers", "key_points", "industry_trends", "evidence_notes"):
            setattr(report, field, [item for item in getattr(report, field)
                                   if not unsupported_claims(item, report.fact_evidence)])
