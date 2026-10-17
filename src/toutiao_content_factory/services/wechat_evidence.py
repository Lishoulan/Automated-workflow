"""Source-bound evidence and editorial requirements for WeChat auto runs.

Legacy attribution strings intentionally do not produce verified facts. The raw
source bodies stay in the private working directory, never in cloud diagnostics.
"""
from __future__ import annotations

import hashlib
import json
import html
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

KINDS = {
    "policy_explainer": ("政策解读", "解释已公布措施、作用环节和适用边界", "实施条件、适用范围、执行进展", "哪些适用条件还需要进一步明确？"),
    "product_progress": ("产品进展", "解释已披露能力、限制和验证情况，区分演示与实际部署", "验证条件、能力边界、实际部署进展", "你最希望看到哪项能力的独立验证？"),
    "company_earnings": ("企业业绩", "围绕明确企业及原文财务数据解释变化，不外推未披露订单", "原文披露的财务指标及其统计期", "哪项已披露指标最值得继续跟踪？"),
    "industry_update": ("行业动态", "解释已发生的变化、已有证据与尚未明确的边界", "技术进展、应用条件、后续公开披露", "哪个应用条件最值得继续观察？"),
}
NUMBER = re.compile(r"(?<![A-Za-z\d])\d+(?:\.\d+)?\s*(?:万亿|亿|万|千|百)?\s*(?:千瓦时|千瓦|兆瓦|吉瓦|美元|人民币|亿元|万元|元|%|％|倍|台|套|家|项|吨|公里|小时|分钟|秒|人|片|个|辆|度|GB|GW|MW|kWh|kW)?", re.I)
PERIOD = re.compile(r"(?:截至(?:\d{4}年)?\d{1,2}月(?:\d{1,2}日|底|末)?|\d{4}年(?:\d{1,2}月(?:至\d{1,2}月)?|[上下]半年|[一二三四1234]季度)?|(?:今年|去年)?(?:[上下]半年|[一二三四1234]季度))")
FINANCIAL = re.compile(r"营业收入|净利润|毛利率|财报|扣非|营收|经营现金流")
BUSINESS_CLAIM = re.compile(r"利润|盈利|毛利|回款|合同验收|订单|客户复购")
QUALIFIED_CLAIM = re.compile(r"认为|预计|估算|预测|可能|有望|据传|爆料|尚未|未确认|预告|宣称|声称|预览")
COMPARATIVE_CLAIM = re.compile(r"优于|领先于|超越|击败|胜过|更强|更安全")


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
        self.ld_json = []
        self._script = False
        self._script_text = []
        self._nickname = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property", attrs.get("name", attrs.get("itemprop", ""))).lower()
            self.values[key] = attrs.get("content", "").strip()
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._script = True
            self._script_text = []
        if attrs.get("id") == "js_name":
            self._nickname = True
        if tag == "time" and attrs.get("datetime"):
            self.values.setdefault("datepublished", attrs["datetime"])

    def handle_data(self, data):
        if self._script:
            self._script_text.append(data)
        if self._nickname and data.strip():
            self.values.setdefault("wechat:nickname", data.strip())

    def handle_endtag(self, tag):
        if tag == "script" and self._script:
            try:
                self.ld_json.append(json.loads("".join(self._script_text)))
            except (ValueError, TypeError):
                pass
            self._script = False
        if tag in {"a", "span"}:
            self._nickname = False


def source_metadata(page_html: str, *, source_url: str = "") -> dict:
    """Read explicit publication metadata before body/noise filtering.

    A collection timestamp, copyright year, modified date, or date in article
    prose never substitutes for the page's publication date.
    """
    parser = _MetadataParser()
    parser.feed(page_html)
    values = parser.values
    structured_dates, structured_publishers = set(), set()
    for payload in parser.ld_json:
        entries = payload if isinstance(payload, list) else payload.get("@graph", [payload]) if isinstance(payload, dict) else []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            types = entry.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list) or not any(isinstance(kind, str) and kind in {"Article", "NewsArticle", "BlogPosting"} for kind in types):
                continue
            if isinstance(entry.get("datePublished"), str):
                structured_dates.add(entry["datePublished"])
            publisher = entry.get("publisher", {})
            name = publisher.get("name", "") if isinstance(publisher, dict) else publisher if isinstance(publisher, str) else ""
            if isinstance(name, str) and name:
                structured_publishers.add(name)
    if len(structured_dates) == 1:
        values.setdefault("datepublished", next(iter(structured_dates)))
    if len(structured_publishers) == 1:
        values.setdefault("article:publisher", next(iter(structured_publishers)))
    if urlsplit(source_url).hostname == "mp.weixin.qq.com" and 'id="js_content"' in page_html:
        stamp = re.search(r"(?:var\s+)?ct\s*=\s*['\"](\d{10})['\"]", page_html)
        if stamp:
            values.setdefault("datepublished", datetime.fromtimestamp(int(stamp[1]), timezone(timedelta(hours=8))).isoformat())
        nickname = values.get("wechat:nickname", "")
        if not nickname:
            match = re.search(r"(?:var\s+)?nickname\s*=\s*['\"]([^'\"<>]{1,100})['\"]", page_html)
            nickname = html.unescape(match[1]) if match else ""
        if nickname:
            values.setdefault("article:publisher", nickname)
    # Explicit article-header fields, never dates found in prose or related links.
    for element_id, field in (("pubtime_baidu", "datepublished"), ("source_baidu", "article:publisher")):
        match = re.search(r"<(?:span|time|div)[^>]+id=['\"]" + element_id + r"['\"][^>]*>(.*?)</(?:span|time|div)>", page_html, re.S | re.I)
        if match:
            value = html.unescape(re.sub(r"<[^>]+>", "", match[1])).strip()
            if value:
                values.setdefault(field, value)
    host = urlsplit(source_url).hostname or ""
    if (host == "www.hncourt.gov.cn" or host.endswith(".hncourt.gov.cn")) and urlsplit(source_url).path == "/public/detail.php":
        title = re.search(r'<p\b[^>]*\bid=["\']hztitle["\'][^>]*>(.*?)</p>', page_html, re.S | re.I)
        date = re.search(r'<span\b[^>]*\bclass=["\'][^"\']*\barticle_time\b[^"\']*["\'][^>]*>\s*发布时间[：:]\s*([^<>]+)</span>', page_html, re.S | re.I)
        if title:
            values.setdefault("og:title", html.unescape(re.sub(r"<[^>]+>", "", title[1])).strip())
            if date:
                values.setdefault("datepublished", html.unescape(date[1]).strip())
            values.setdefault("article:publisher", host)
    if host in {"court.gov.cn", "www.court.gov.cn"}:
        header = re.search(r'<ul\b[^>]*\bclass=[\"\'][^\"\']*\bmessage\b[^\"\']*[\"\'][^>]*>(.*?)</ul>',
                           page_html, re.S | re.I)
        if header:
            for label, field in (("发布时间", "datepublished"), ("来源", "article:publisher")):
                match = re.search(r'<li\b[^>]*>\s*' + label + r'[：:]\s*([^<>]+)</li>',
                                  header[1], re.S | re.I)
                if match:
                    values.setdefault(field, html.unescape(match[1]).strip())
    if host == "m.thepaper.cn":
        header = re.search(r'<div[^>]+class=[\"\'][^\"\']*\bheader\b[^\"\']*[\"\'][^>]*>', page_html, re.I)
        if header:
            region = re.split(r"<article\b", page_html[header.end():], maxsplit=1, flags=re.I)[0][:2000]
            info = re.search(r'<div[^>]+class=[\"\'][^\"\']*\binfo\b[^\"\']*[\"\'][^>]*>(.*?)</div>', region, re.S | re.I)
            if info:
                text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", info[1]))).strip()
                match = re.fullmatch(r"媒体[：:]\s*(.{1,100}?)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)", text)
                if match:
                    values.setdefault("article:publisher", match[1])
                    values.setdefault("datepublished", match[2])
            title = re.search(r'<div[^>]+id=[\"\']title[\"\'][^>]*>(.*?)</div>', region, re.S | re.I)
            if title:
                values.setdefault("og:title", html.unescape(re.sub(r"<[^>]+>", "", title[1])).strip())
    if host == "ofweek.com" or host.endswith(".ofweek.com"):
        header = re.search(r'<div[^>]+class=[\"\'][^\"\']*\bartical-relative\b[^\"\']*[\"\'][^>]*>', page_html, re.I)
        if header:
            region = page_html[header.end():header.end() + 1200]
            date = re.search(r'<div[^>]+class=[\"\'][^\"\']*\btime\b[^\"\']*[\"\'][^>]*>\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*</div>', region, re.I)
            if date:
                values.setdefault("datepublished", date[1])
            publisher = re.search(r'<a[^>]+href=[\"\']https?://mp\.ofweek\.com/[^>]+>(.*?)</a>', region, re.S | re.I)
            if publisher:
                text = html.unescape(re.sub(r"<[^>]+>", "", publisher[1])).strip()
                name = text.splitlines()[0].strip() if text else ""
                if name:
                    values.setdefault("article:publisher", name)
    raw_date = next((values[k] for k in ("article:published_time", "datepublished", "pubdate", "publishdate", "publish_date", "pubtime", "publish-time", "dcterms.issued", "dc.date.issued") if values.get(k)), "")
    published_at = ""
    if raw_date:
        try:
            published_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).isoformat()
        except ValueError:
            match = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", raw_date)
            if match:
                try:
                    parts = [int(value) if value is not None else 0 for value in match.groups()]
                    stamp = datetime(*parts)
                    published_at = stamp.isoformat() if match[4] is not None else stamp.date().isoformat()
                except ValueError:
                    pass
    return {"publisher": values.get("article:publisher", values.get("og:site_name", "")),
            "published_at": published_at, "date_verified": bool(published_at),
            "page_title": values.get("og:title", "")}


def is_event_article(title: str, url: str) -> bool:
    parsed = urlsplit(url)
    return not (re.search(r"专题页|攻略汇总|游戏大全", title)
                or (parsed.hostname == "cg.163.com" and parsed.path.startswith("/static/game/")))


def source_records(sources) -> list[dict]:
    records = []
    for source in sources:
        url = public_url(str(source.url or ""))
        # Search snippets and anonymous digests are not verified original prose.
        if not source.ok or source.evidence_type != "article" or not url or not source.body:
            continue
        if not is_event_article(str(source.title or ""), url):
            continue
        records.append({"source_id": stable_id("src_", url), "title": source.title,
                        "url": url, "publisher": getattr(source, "publisher", ""),
                        "published_at": getattr(source, "published_at", "") if getattr(source, "date_verified", False) else "",
                        "date_verified": bool(getattr(source, "date_verified", False)),
                        "original_text": source.body})
    return records


def scope_source_records(records: list[dict], topic_url: str, *, event_published_at: str = "") -> list[dict]:
    """Keep provenance, but distinguish the selected event from dated background.

    Event/RSS dates can delimit background; they never populate a missing source
    date. A known selected-source date takes precedence over other reports.
    """
    scoped = [dict(source) for source in records]
    primary = next((source for source in scoped if public_url(topic_url)
                    and public_url(source.get("url", "")) == public_url(topic_url)), None)
    if primary is None:
        return scoped  # Unbound/legacy sources are not upgraded to current evidence.

    def stamp(value):
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return date.replace(tzinfo=timezone(timedelta(hours=8))) if date.tzinfo is None else date
        except (TypeError, ValueError):
            return None

    primary_date = primary.get("published_at", "") if primary.get("date_verified") is True else ""
    reference_value = primary_date or event_published_at
    reference = stamp(reference_value)
    for source in scoped:
        value = source.get("published_at", "") if source.get("date_verified") is True else ""
        published = stamp(value)
        role = "primary" if source is primary else "undated"
        if source is not primary and published and reference:
            # Date-only metadata cannot establish an hour-level difference.
            exact_time = "T" in str(value) and "T" in str(reference_value)
            if exact_time:
                outside_window = abs(reference - published) > timedelta(hours=12)
            else:
                beijing = timezone(timedelta(hours=8))
                outside_window = abs(reference.astimezone(beijing).date()
                                     - published.astimezone(beijing).date()) > timedelta(days=1)
            role = "background" if outside_window else "supporting"
        source["source_role"] = role
    return scoped


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
    for clause in re.split(r"[，,、；;。\n]", sentence):
        subject = _subject(clause) or previous_object
        if subject:
            previous_object = subject
        metric = next((token for token in ("同比增长", "同比下降", "环比增长", "环比下降", "占比", "占", "增长", "下降") if token in clause), "")
        for number in _numbers(clause):
            measurements.append({**number, "statistical_object": subject,
                                 "statistical_period": period, "metric": metric})
    return measurements


def build_facts(records: list[dict], *, include_background: bool = True) -> list[dict]:
    facts, seen = [], set()
    for source in records:
        if not include_background and source.get("source_role") == "background":
            continue
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
            if "source_role" in source:
                facts[-1].update(source_role=source["source_role"], source_published_at=source.get("published_at", ""))
    return facts


def classify_kind(topic_type: str, facts: list[dict]) -> str:
    prose = "\n".join(item["quote"] for item in facts)
    if topic_type == "earnings_signal" and any(
        FINANCIAL.search(item["quote"]) and item["numbers"]
        and re.search(r"[\u4e00-\u9fffA-Za-z]{2,}(?:公司|集团|股份)|(?:英伟达|特斯拉|比亚迪|宁德时代|苹果|微软)", item["quote"])
        for item in facts
    ):
        return "company_earnings"
    policy = any(
        re.search(r"通知|管理办法|实施办法|政策|指导意见|实施细则|条例", item["quote"])
        and re.search(r"国务院|政府|能源局|发改委|发展改革|工信部|财政部|委员会|能源机构|主管部门|(?:省|市|自治区)(?:发布|印发|出台|规定|政府)", item["quote"])
        for item in facts)
    if topic_type == "policy_market" and policy:
        return "policy_explainer"
    if (topic_type in {"product_launch", "project_progress", "policy_market"}
            and re.search(r"产品|处理器|芯片|机器人|模型|设备", prose)
            and re.search(r"发布|推出|测试|验证|部署|投产|型号|首发", prose)):
        return "product_progress"
    return "industry_update"


def requirements(kind: str) -> dict:
    label, brief, watch, question = KINDS.get(kind, KINDS["industry_update"])
    if kind == "product_progress":
        brief += "；配置、性能、基准测试或独立验证未披露时明确保持未知，不推导性能提升、市场领导地位或产业链收益。消息源未经官方确认时保留报道归因，不把路线图或预测写成已实现事实"
        brief += "；产品预告仍是预告，比较对象、测试方法或验证结果未披露时明确说未披露，不补造竞品名称或胜负结论；不以旧融资报道解释本次产品能力"
    brief += "；保留原句中的消息来源、发言人及认为、预测、估算、可能等限定，观点和预测不得写成已实施政策或确定事实"
    return {"label": label, "suggested_angle": brief, "follow_up_focus": watch,
            "writing_brief": brief + "。所有数字逐项绑定原句、统计对象、单位和统计期；缺失信息保持未知。",
            "closing": question,
            "instruction": f"稿件类型：{label}。{brief}。跟踪：{watch}。结尾问题：{question}。没有企业财务或订单原句时，不生成利润兑现、回款、合同验收等断言。"}


def _strip_source_citations(text: str, *, anywhere: bool = False) -> str:
    """Read balanced book-title delimiters, including old stacked citations."""
    cursor = 0
    while cursor < len(text):
        start = text.find("据《", cursor) if anywhere else (0 if text.startswith("据《") else -1)
        if start < 0:
            break
        depth, end = 1, None
        for position in range(start + 2, len(text)):
            if text[position] == "《":
                depth += 1
            elif text[position] == "》":
                depth -= 1
                if depth == 0:
                    suffix = re.match(r"报道[，,]?", text[position + 1:])
                    if suffix:
                        end = position + 1 + suffix.end()
                    break
        if end is None:
            if not anywhere:
                break
            cursor = start + 2
            continue
        text = text[:start] + text[end:]
        cursor = start
    return text


def _source_prefix(source: dict) -> str:
    # Preserve the source's actual title in metadata/footer. Inner book names
    # use corner quotes so the outer attribution is unambiguous to readers.
    title = source["title"].replace("《", "「").replace("》", "」")
    return f"据《{title}》报道，"


def supporting_facts(sentence: str, facts: list[dict]) -> list[dict]:
    """Conservative context match; a shared numeric value is insufficient.

    Paraphrases still undergo the independent council review. This check only
    supplies traceable candidates, and never establishes a verdict on its own.
    """
    sentence = _claim_text(sentence)
    normalized = _normalize(sentence)
    numbers = _numbers(sentence)
    periods = PERIOD.findall(sentence)
    measurements = _measurements(sentence)
    comparison_targets = re.findall(r"(?:优于|领先于|超越|击败|胜过)([^，,。；;！？]+)", sentence)
    matches = []
    for fact in facts:
        quote = _normalize(fact["quote"])
        if any(_normalize(target) not in quote for target in comparison_targets):
            continue
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
    return _strip_source_citations(sentence.strip())


def _requires_qualification(claim: str, fact: dict) -> bool:
    qualifier = QUALIFIED_CLAIM.search(fact["quote"])
    if not qualifier:
        return False
    # A later forecast does not change an earlier, already established fact.
    prefix = fact["quote"][:qualifier.start()].rstrip("，,：: ")
    if prefix:
        established = {**fact, "quote": prefix, "numbers": _numbers(prefix),
                       "measurements": _measurements(prefix),
                       "statistical_period": list(dict.fromkeys(PERIOD.findall(prefix)))}
        if supporting_facts(claim, [established]):
            return False
    return True


def _unsupported_claim(sentence: str, facts: list[dict]) -> bool:
    claim = _claim_text(sentence)
    business = BUSINESS_CLAIM.search(claim)
    comparative = False
    for clause in re.split(r"[，,。；;！？]", claim):
        comparison = COMPARATIVE_CLAIM.search(clause)
        if comparison and not re.search(r"不能证明|无法证明|没有证据|是否|能否|尚未证实|需要|应当", clause[:comparison.start()]):
            comparative = True
    unqualified_assertion = (_numbers(claim) or re.search(r"将|必然|一定", claim) or comparative) and not QUALIFIED_CLAIM.search(claim)
    if unqualified_assertion:
        matches = supporting_facts(claim, facts)
        if matches and all(_requires_qualification(claim, fact) for fact in matches):
            return True
    if not _numbers(claim) and not business and not comparative:
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
    article = _strip_source_citations(article.split("参考来源：", 1)[0], anywhere=True)
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
    cited_sources = set()
    seen_sentences = set()
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = _claim_text(paragraph)
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
            normalized = re.sub(r"\s+", "", sentence)
            if len(normalized) >= 12 and normalized in seen_sentences:
                continue
            if len(normalized) >= 12:
                seen_sentences.add(normalized)
            kept.append(sentence)
            source_ids.extend(fact["source_id"] for fact in matches)
        if not kept:
            continue
        citation_size = max((len(_source_prefix(sources[sid]))
                             for sid in source_ids if sid in sources), default=0)
        for text in _mobile_paragraphs("".join(kept), max(1, 180 - citation_size)):
            chunk_sources = [fact["source_id"] for sentence in re.split(r"(?<=[。！？；])", text)
                             for fact in supporting_facts(sentence, report.fact_evidence)]
            if chunk_sources:
                source = sources.get(chunk_sources[0], {})
                name = source.get("publisher") or source.get("title")
                if name and name not in text and source.get("source_id") not in cited_sources:
                    text = _source_prefix(source) + text
                cited_sources.add(source.get("source_id"))
            paragraphs.append(text)
    references = []
    active_sources = {fact["source_id"] for fact in report.fact_evidence}
    for source in report.source_records:
        if source.get("source_role") == "background" and source.get("source_id") not in active_sources:
            continue
        date = source.get("published_at") or "发布日期未核实"
        publisher = source.get("publisher") or "发布机构未标注"
        references.append(f"《{source['title']}》；{publisher}；{date}；{source['url']}")
    return "\n\n".join(paragraphs) + "\n\n参考来源：\n\n" + "\n\n".join(references)


def effective_body_chars(article: str) -> int:
    body = article.split("参考来源：", 1)[0]
    body = _strip_source_citations(body, anywhere=True)
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
    signals = {
        "policy_explainer": [r"发布|通知|政策|措施", r"要求|规定|支持|补贴", r"地区|项目|省|市|适用", r"同期|条件|投产|实施|期限", r"范围|仅|未|不得|边界", r"实施|日期|期限|披露|核对"],
        "product_progress": [r"公司|企业|发布|预告|产品|模型", r"能力|功能|支持|性能|网络安全|网安", r"架构|芯片|工艺|机制|部署|接口", r"测试|验证|基准|结果|条件|披露", r"限制|尚未|未披露|未验证|边界|比较对象", r"测试|验证|发布时间|披露|进展"],
        "company_earnings": [r"公司|企业|季度|半年|年", r"收入|利润|毛利|现金流|财报", r"业务|产品|增长|下降|占比", r"同比|环比|统计|期间|口径", r"未|限制|范围|仅|风险", r"财报|披露|统计期|日期"],
    }.get(report.article_kind, [r"公司|企业|发布|事件", r"完成|发布|进展|上线", r"技术|设备|芯片|模型|工艺", r"条件|涉及|因为|因此|要求", r"未|限制|范围|边界", r"测试|验证|披露|实施|日期"])
    report.writing_outline = []
    for index, (label, signal) in enumerate(zip(labels, signals)):
        matching_facts = [fact for fact in facts if re.search(signal, fact["quote"])]
        bindings = matching_facts[:4] or facts[:1]
        report.writing_outline.append({"section_id": index, "section": label,
            "fact_ids": [fact["fact_id"] for fact in bindings], "target_chars": 350,
            "evidence_scope": "direct" if matching_facts else "boundary_only"})
    payload = {"facts": report.fact_evidence, "kind": report.article_kind,
               "outline": report.writing_outline, "requirements": report.regeneration_requirements}
    report.draft_fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if report.draft_fingerprint in context.get("failed_fingerprints", []):
        from toutiao_content_factory.exceptions import LowQualityAbortException
        raise LowQualityAbortException("同一素材、大纲和要求已失败，换题。")
    report.word_target = 2100
    report.writing_brief += " 正文目标2000～2200字，最低1750字；按writing_outline逐节展开，每节仅使用绑定证据。evidence_scope为boundary_only时只解释证据边界，不编造该节所需信息、不重复其他节结论。参考来源、网址和重复引用不计篇幅。"
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
